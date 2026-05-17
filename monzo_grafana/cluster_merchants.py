"""Cluster Santander merchant variants into families and cross-reference each
family against Monzo's canonical merchant list.

Emits a Markdown report ranked by cluster spend so a human can walk top-down
and decide:

- a single canonical name (usually a Monzo merchant)
- a single category
- a regex covering every Santander variant

…then append the suggested block to ``data/santander_rules.yaml`` and re-run
``import-santander`` + ``retag`` to apply.
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

import psycopg

from .config import Config

# Categories considered "real classification" on the Monzo side.
EXCLUDED_MONZO_CATEGORIES = frozenset({"unknown", "general", "internal", "savings", "split"})

# Words that match too freely to be useful as cluster keys.
STOPWORDS = frozenset(
    {
        # determiners / connectors
        "the", "and", "for", "from", "with", "via", "your", "this", "that",
        "into", "onto", "their",
        # company suffixes
        "ltd", "limited", "plc", "inc", "company", "group", "holdings",
        "intl", "international", "corp", "corporation",
        # geo
        "london", "bath", "bristol", "england", "scotland", "wales",
        "british", "south", "north", "east", "west",
        # bank boilerplate
        "card", "payment", "purchase", "online", "credit", "debit",
        "transfer", "tran", "trans", "fpid", "fpib", "fpio", "ref",
        "reference", "money", "cash", "fees", "bill", "bills", "rent",
        "mandate", "deposit", "withdrawal", "cheque", "interest",
        "faster", "receipt", "giro", "disbursements", "paid",
        "amount", "outstanding", "outstandin", "savings", "saving",
        # payment processors (not real merchants)
        "sumup", "zettle", "square",
        # generic business categories
        "store", "stores", "shop", "shops", "retail", "outlet", "branch",
        "cafe", "bars", "kitchen", "house", "grill", "diner", "restaurant",
        "burger", "burgers", "coffee", "club", "hotel", "lounge", "garden",
        "market", "markets", "tavern", "food", "foods", "deli",
        # tags
        "gbp", "eur", "usd",
    }
)  # fmt: skip

# Bidirectional aliases that bridge known Santander↔Monzo abbreviations.
# Keep this small and explicit; not a fuzzy heuristic.
_ALIAS_PAIRS: list[tuple[str, str]] = [
    ("amzn", "amazon"),
    ("sbry", "sainsbury"),
    ("mkts", "market"),
    ("mkt", "market"),
    ("smkt", "market"),
    ("tfl", "transport"),
    ("waitros", "waitrose"),
]
_ALIASES: dict[str, str] = {}
for _a, _b in _ALIAS_PAIRS:
    _ALIASES[_a] = _b
    _ALIASES[_b] = _b


# Bank-transfer boilerplate that wraps personal-transfer descriptions. These
# rows aren't real merchants and tokens from the surrounding text (person
# names, reference words) bridge otherwise-unrelated merchants in the
# union-find. Strip them before tokenising so each transfer collapses to
# nothing and lands in its own singleton cluster.
_TRANSFER_PATTERNS = [
    re.compile(r"^bill payment( via faster payment)? (to|from)\b.*", re.IGNORECASE),
    re.compile(r"^bank giro credit\b.*", re.IGNORECASE),
    re.compile(r"^transfer (to|from)\b.*", re.IGNORECASE),
    re.compile(r"^cash (paid|withdrawal)\b.*", re.IGNORECASE),
    re.compile(r"^cheque\b.*", re.IGNORECASE),
    re.compile(r"^interest paid\b.*", re.IGNORECASE),
    re.compile(r"^unipayment\b.*", re.IGNORECASE),
    re.compile(r"^uni payment\b.*", re.IGNORECASE),
    re.compile(r"^merry christmas\b.*", re.IGNORECASE),
    re.compile(r"^happy birthday\b.*", re.IGNORECASE),
    re.compile(
        r"^(rent|train|travel|flight|money owed|savings outstandin)\b.*from\b.*",
        re.IGNORECASE,
    ),
]


def _normalise(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _is_transfer(s: str) -> bool:
    return any(p.match(s) for p in _TRANSFER_PATTERNS)


def _tokens(s: str) -> set[str]:
    # Personal transfers carry no merchant signal — return an empty set so
    # they end up in their own singleton cluster.
    if _is_transfer(s):
        return set()
    raw = {t for t in _normalise(s).split() if len(t) >= 4 and not t.isdigit()}
    canonical = {_ALIASES.get(t, t) for t in raw}
    return {t for t in canonical if t not in STOPWORDS}


@dataclass
class _SantanderMerchant:
    name: str
    rows: int
    spend: Decimal  # negative = outgoing; we sum abs() for ranking


@dataclass
class _Cluster:
    members: list[_SantanderMerchant] = field(default_factory=list)
    tokens: set[str] = field(default_factory=set)

    @property
    def rows(self) -> int:
        return sum(m.rows for m in self.members)

    @property
    def abs_spend(self) -> Decimal:
        return sum((abs(m.spend) for m in self.members), Decimal("0"))


def _fetch_santander(cfg: Config) -> list[_SantanderMerchant]:
    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT merchant, COUNT(*), COALESCE(SUM(amount), 0)
            FROM transactions
            WHERE account_id = 'santander'
            GROUP BY merchant
            """
        )
        return [_SantanderMerchant(name=m, rows=n, spend=s) for m, n, s in cur.fetchall()]


def _fetch_monzo(cfg: Config) -> tuple[dict[str, int], dict[str, str]]:
    """Return (counts per Monzo merchant, dominant category per Monzo merchant).

    Only counts merchants that have a *real* category (not in
    ``EXCLUDED_MONZO_CATEGORIES``) — Monzo's `general`/`unknown` carry no
    signal we want to propagate.
    """
    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT merchant, category FROM transactions "
            "WHERE account_id = 'monzo' AND category <> ALL(%s)",
            (list(EXCLUDED_MONZO_CATEGORIES),),
        )
        by_merchant: dict[str, Counter[str]] = defaultdict(Counter)
        for merchant, category in cur.fetchall():
            by_merchant[merchant][category] += 1
    counts = {m: sum(c.values()) for m, c in by_merchant.items()}
    dominant = {m: c.most_common(1)[0][0] for m, c in by_merchant.items()}
    return counts, dominant


def _cluster_santander(
    merchants: list[_SantanderMerchant],
    monzo_counts: dict[str, int],
) -> list[_Cluster]:
    """Cluster Santander merchants by their best-matching Monzo merchant.

    For each Santander merchant we look at its significant tokens and find
    the Monzo merchant whose token set overlaps most. That Monzo merchant
    becomes the cluster key. Monzo activity (transaction count) breaks ties
    between equal-overlap candidates.

    Santander merchants with no Monzo overlap fall back to a Santander-only
    cluster keyed on their lowest-frequency token (the most discriminating
    one — usually the brand name). Merchants with no significant tokens at
    all (transfers, one-off references) land in singleton clusters.
    """
    monzo_tokens: dict[str, set[str]] = {m: _tokens(m) for m in monzo_counts}

    sant_token_freq: Counter[str] = Counter()
    sant_tokens: dict[str, set[str]] = {}
    for m in merchants:
        ts = _tokens(m.name)
        sant_tokens[m.name] = ts
        sant_token_freq.update(ts)

    clusters: dict[str, _Cluster] = defaultdict(_Cluster)
    for m in merchants:
        ts = sant_tokens[m.name]
        if not ts:
            key = f"singleton:{m.name}"
        else:
            best_monzo: str | None = None
            best_score: tuple[int, int] = (0, 0)
            for monzo_m, monzo_ts in monzo_tokens.items():
                overlap = len(ts & monzo_ts)
                if overlap == 0:
                    continue
                score = (overlap, monzo_counts.get(monzo_m, 0))
                if score > best_score:
                    best_score = score
                    best_monzo = monzo_m
            if best_monzo:
                key = f"monzo:{best_monzo}"
            else:
                # Pick the most-bridging token (highest Santander freq) — that
                # tends to be the brand identifier shared across variants.
                # Tiebreak by length then alpha for determinism.
                primary = max(ts, key=lambda t: (sant_token_freq[t], len(t), t))
                key = f"sant:{primary}"
        clusters[key].members.append(m)
        clusters[key].tokens |= ts
    return sorted(clusters.values(), key=lambda c: c.abs_spend, reverse=True)


def _monzo_candidates(
    cluster: _Cluster,
    monzo_counts: dict[str, int],
    monzo_dominant: dict[str, str],
    limit: int = 3,
) -> list[tuple[str, int, str]]:
    """Top Monzo merchants whose tokens overlap the cluster's tokens.

    Returns (monzo_merchant, transaction_count, dominant_category) tuples.
    """
    if not cluster.tokens:
        return []
    candidates: list[tuple[str, int, str]] = []
    for m, n in monzo_counts.items():
        if _tokens(m) & cluster.tokens:
            candidates.append((m, n, monzo_dominant[m]))
    candidates.sort(key=lambda t: t[1], reverse=True)
    return candidates[:limit]


def _suggest(cluster: _Cluster, candidates: list[tuple[str, int, str]]) -> tuple[str, str, str]:
    """Heuristic starting point: canonical name, category, regex.

    The human edits these before pasting. Pure heuristic — keep it dumb.
    """
    if candidates:
        canonical = candidates[0][0]
        weighted: Counter[str] = Counter()
        for _, n, cat in candidates:
            weighted[cat] += n
        category = weighted.most_common(1)[0][0]
    else:
        # No Monzo match — fall back to the highest-spend Santander variant.
        canonical = cluster.members[0].name.title() if cluster.members else "???"
        category = "unknown"

    if cluster.tokens:
        # Pick the token that appears in the most Santander variants in this
        # cluster — that's the most discriminating regex anchor.
        token_freq: Counter[str] = Counter()
        for m in cluster.members:
            for t in _tokens(m.name):
                token_freq[t] += 1
        anchor = token_freq.most_common(1)[0][0]
        regex = rf"(?i)\b{re.escape(anchor)}\b"
    else:
        regex = "(?i)" + re.escape(cluster.members[0].name) if cluster.members else "(?i).*"
    return canonical, category, regex


def _ambiguous(candidates: list[tuple[str, int, str]]) -> bool:
    return len({cat for _, _, cat in candidates}) > 1


def _render(
    clusters: list[_Cluster],
    monzo_counts: dict[str, int],
    monzo_dominant: dict[str, str],
    min_rows: int,
    min_spend: Decimal,
) -> str:
    out: list[str] = []
    out.append("# Santander merchant clusters\n")
    out.append(
        f"_{len(clusters)} clusters total; filtering to rows≥{min_rows}, spend≥£{min_spend}_\n"
    )

    shown = 0
    for i, cluster in enumerate(clusters, start=1):
        if cluster.rows < min_rows or cluster.abs_spend < min_spend:
            continue
        shown += 1
        candidates = _monzo_candidates(cluster, monzo_counts, monzo_dominant)
        canonical, category, regex = _suggest(cluster, candidates)
        amb = " — AMBIGUOUS (multiple categories)" if _ambiguous(candidates) else ""

        out.append(f"## Cluster {i} — {cluster.rows} rows, £{cluster.abs_spend:.2f} spend{amb}")
        out.append("Santander variants:")
        for member in sorted(cluster.members, key=lambda x: x.rows, reverse=True):
            out.append(f"  - {member.name:<45} ({member.rows} rows, £{abs(member.spend):.2f})")
        out.append("Candidate Monzo names:")
        if candidates:
            for m, n, cat in candidates:
                out.append(f"  - {m:<45} ({n} rows, {cat})")
        else:
            out.append("  - (none — no shared tokens with any categorised Monzo merchant)")
        out.append("Suggested:")
        out.append(f"  canonical: {canonical}")
        out.append(f"  category:  {category}")
        out.append(f"  regex:     {regex}")
        out.append("")

    out.insert(2, f"_{shown} clusters shown below._\n")
    return "\n".join(out)


def cluster_merchants(
    cfg: Config, min_rows: int, min_spend: float, output: str | None
) -> None:
    santander = _fetch_santander(cfg)
    if not santander:
        print("No Santander transactions found.", file=sys.stderr)
        return
    monzo_counts, monzo_dominant = _fetch_monzo(cfg)

    clusters = _cluster_santander(santander, monzo_counts)
    report = _render(clusters, monzo_counts, monzo_dominant, min_rows, Decimal(str(min_spend)))

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Wrote {output}", file=sys.stderr)
    else:
        print(report)
