"""Apply the live rules file against the Santander transactions and report
what each rule matches.

The rules file is a YAML list (see ``santander/staging_rules.py``). The dry
run prints per-rule hit lists, flags zero-match rules, lists descriptions
caught by multiple rules (and which one would win), and finishes with an
unmatched-merchants summary so you can see the gaps.

Nothing is written to the database.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import psycopg

from .config import Config
from .santander.merchant import prepare_for_match
from .santander.staging_rules import Rule, parse_rules


@dataclass
class _Row:
    description: str
    rows: int
    spend: Decimal


def _load(path_str: str) -> list[Rule]:
    path = Path(path_str)
    if not path.is_file():
        print(f"error: {path_str} not found", file=sys.stderr)
        sys.exit(1)
    try:
        rules = parse_rules(path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    if not rules:
        print(f"error: no rules parsed from {path_str}", file=sys.stderr)
        sys.exit(1)
    return rules


def _fetch_rows(cfg: Config) -> list[_Row]:
    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT description, COUNT(*), COALESCE(SUM(amount), 0)
            FROM transactions
            WHERE account_id = 'santander'
            GROUP BY description
            ORDER BY COUNT(*) DESC
            """,
        )
        # Apply the same prep clean_merchant uses, so dry-run matches what
        # the importer/retag pipeline will see in production.
        return [
            _Row(description=prepare_for_match(d), rows=n, spend=s)
            for d, n, s in cur.fetchall()
        ]


def dry_run(cfg: Config, rules_path: str) -> None:
    rules = _load(rules_path)
    rows = _fetch_rows(cfg)
    if not rows:
        print("No Santander transactions found.", file=sys.stderr)
        return

    hits: dict[int, list[tuple[_Row, bool]]] = defaultdict(list)
    row_conflicts: list[list[int]] = []
    unmatched: list[_Row] = []

    for row in rows:
        matchers = [i for i, rule in enumerate(rules) if rule.compiled.search(row.description)]
        row_conflicts.append(matchers)
        if not matchers:
            unmatched.append(row)
            continue
        winner = matchers[0]
        for idx in matchers:
            hits[idx].append((row, idx == winner))

    _print_per_rule(rules, hits)
    _print_conflicts(rules, rows, row_conflicts)
    _print_unmatched(unmatched)
    _print_summary(rules, rows, hits, unmatched, row_conflicts)


def _print_per_rule(rules: list[Rule], hits: dict[int, list[tuple[_Row, bool]]]) -> None:
    print("# Per-rule matches\n")
    for idx, rule in enumerate(rules):
        rule_hits = hits.get(idx, [])
        n_rows = sum(r.rows for r, _ in rule_hits)
        won_rows = sum(r.rows for r, won in rule_hits if won)
        total_spend = sum((abs(r.spend) for r, _ in rule_hits), Decimal("0"))
        won_spend = sum((abs(r.spend) for r, won in rule_hits if won), Decimal("0"))

        flag = "" if rule_hits else "  ⚠ NO MATCHES"
        shadowed = "" if won_rows == n_rows else f"  ({n_rows - won_rows} shadowed by earlier rule)"
        print(
            f"## Rule {idx + 1}: {rule.canonical!r} ({rule.category})  "
            f"— /{rule.regex}/{flag}{shadowed}"
        )
        print(f"   Wins: {won_rows} rows, £{won_spend:.2f}   "
              f"Total touched: {n_rows} rows, £{total_spend:.2f}")
        for row, won in sorted(rule_hits, key=lambda t: t[0].rows, reverse=True):
            mark = " " if won else "·"
            print(f"   {mark} {row.description[:65]:<65}  ({row.rows} rows, £{abs(row.spend):.2f})")
        print()


def _print_conflicts(
    rules: list[Rule], rows: list[_Row], row_conflicts: list[list[int]]
) -> None:
    multi = [(rows[i], idxs) for i, idxs in enumerate(row_conflicts) if len(idxs) > 1]
    print("\n# Conflicts — descriptions matched by >1 rule (first wins)\n")
    if not multi:
        print("  (none)\n")
        return
    multi.sort(key=lambda t: t[0].rows, reverse=True)
    for row, idxs in multi:
        winner = rules[idxs[0]]
        losers = [rules[i].canonical for i in idxs[1:]]
        print(f"  {row.description[:60]:<60}  ({row.rows} rows)")
        print(f"     winner: {winner.canonical!r} ({winner.category})")
        print(f"     also matched: {', '.join(repr(x) for x in losers)}")
    print()


_NORMALISE_PATTERNS = (
    re.compile(r",?\s*ON\s+\d{2}-\d{2}-\d{4}\s*$", re.IGNORECASE),
    re.compile(r"\s*\(VIA [^)]+\)\s*", re.IGNORECASE),
    re.compile(r",\s*[\d.]+\s*[A-Z]{3},\s*RATE\s+[\d./A-Z]+\s*$", re.IGNORECASE),
    re.compile(r"^CARD PAYMENT TO\s+", re.IGNORECASE),
    re.compile(r"^FASTER PAYMENTS? (RECEIPT|PAYMENT) REF\.?\s*", re.IGNORECASE),
)


def _normalise(description: str) -> str:
    s = description
    for pat in _NORMALISE_PATTERNS:
        s = pat.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip(" ,")


def _print_unmatched(unmatched: list[_Row]) -> None:
    print("\n# Top unmatched descriptions (boilerplate stripped, re-aggregated)\n")
    if not unmatched:
        print("  (none — every description was matched)\n")
        return
    merged: dict[str, _Row] = {}
    for row in unmatched:
        key = _normalise(row.description)
        existing = merged.get(key)
        if existing is None:
            merged[key] = _Row(description=key, rows=row.rows, spend=row.spend)
        else:
            existing.rows += row.rows
            existing.spend += row.spend
    for row in sorted(merged.values(), key=lambda r: (r.rows, abs(r.spend)), reverse=True):
        print(f"  {row.description[:70]:<70}  ({row.rows} rows, £{abs(row.spend):.2f})")


def _print_summary(
    rules: list[Rule],
    rows: list[_Row],
    hits: dict[int, list[tuple[_Row, bool]]],
    unmatched: list[_Row],
    row_conflicts: list[list[int]],
) -> None:
    total_rows = sum(r.rows for r in rows)
    unmatched_rows = sum(r.rows for r in unmatched)
    matched_rows = total_rows - unmatched_rows
    zero_match_rules = [i for i in range(len(rules)) if not hits.get(i)]
    multi_match_descs = sum(1 for idxs in row_conflicts if len(idxs) > 1)

    print("\n# Summary")
    print(f"  rules:                       {len(rules)}")
    print(f"  rules with zero matches:     {len(zero_match_rules)}")
    print(f"  total Santander rows:        {total_rows}")
    print(f"  matched (at least one rule): {matched_rows} ({matched_rows / total_rows:.1%})")
    print(f"  unmatched:                   {unmatched_rows} ({unmatched_rows / total_rows:.1%})")
    print(f"  descriptions w/ conflicts:   {multi_match_descs}")
    if zero_match_rules:
        names = [rules[i].canonical for i in zero_match_rules]
        print("\n  Zero-match rules:", ", ".join(repr(n) for n in names))
