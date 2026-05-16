"""Unit tests for the cluster-merchants helper."""

from __future__ import annotations

from decimal import Decimal

from monzo_grafana.cluster_merchants import (
    _cluster_santander,
    _monzo_candidates,
    _SantanderMerchant,
    _suggest,
    _tokens,
)


def _m(name: str, rows: int = 1, spend: float = -1.0) -> _SantanderMerchant:
    return _SantanderMerchant(name=name, rows=rows, spend=Decimal(str(spend)))


def test_tokens_filters_stopwords_and_short() -> None:
    assert _tokens("TESCO STORES 4521 BATH") == {"tesco"}  # bath geo, stores generic
    assert _tokens("CARD PAYMENT TO TESCO") == {"tesco"}


def test_tokens_applies_aliases() -> None:
    assert "amazon" in _tokens("AMZN MKTP UK")
    assert "amazon" in _tokens("AMAZON.CO.UK")
    assert "sainsbury" in _tokens("SBRY S")  # sbry → sainsbury via alias


def test_cluster_groups_tesco_variants_under_monzo_anchor() -> None:
    merchants = [
        _m("TESCO STORES 4521"),
        _m("TESCO STORES 5103"),
        _m("TESCO METRO BATH"),
        _m("TESCO"),
        _m("RANDOM OTHER MERCHANT"),
    ]
    monzo_counts = {"Tesco": 50, "Sainsbury's": 30}
    clusters = _cluster_santander(merchants, monzo_counts)
    tesco_cluster = next(
        c for c in clusters if "TESCO" in c.members[0].name and len(c.members) > 1
    )
    assert len(tesco_cluster.members) == 4
    singletons = [c for c in clusters if c.members[0].name == "RANDOM OTHER MERCHANT"]
    assert len(singletons) == 1


def test_cluster_bridges_amzn_amazon_via_alias() -> None:
    merchants = [_m("AMZN MKTP UK*ABC"), _m("AMAZON.CO.UK")]
    monzo_counts = {"Amazon": 20}
    clusters = _cluster_santander(merchants, monzo_counts)
    # Both AMZN and AMAZON should share the alias-canonical token and cluster
    # under the same Monzo anchor.
    assert len(clusters) == 1
    assert len(clusters[0].members) == 2


def test_monzo_candidates_overlap() -> None:
    merchants = [_m("TESCO STORES 4521")]
    monzo_counts = {"Tesco": 50, "Sainsbury's": 30}
    monzo_dominant = {"Tesco": "groceries", "Sainsbury's": "groceries"}
    clusters = _cluster_santander(merchants, monzo_counts)
    cands = _monzo_candidates(clusters[0], monzo_counts, monzo_dominant)
    assert [c[0] for c in cands] == ["Tesco"]


def test_suggest_picks_top_monzo_candidate() -> None:
    monzo_counts = {"Tesco": 50}
    cluster = _cluster_santander(
        [_m("TESCO STORES 4521", rows=5, spend=-100)], monzo_counts
    )[0]
    canonical, category, regex = _suggest(
        cluster,
        [("Tesco", 50, "groceries"), ("Tesco Mobile", 5, "bills")],
    )
    assert canonical == "Tesco"
    assert category == "groceries"
    assert "tesco" in regex


def test_unmatched_merchants_cluster_by_primary_token() -> None:
    # No Monzo match — should cluster by lowest-freq token. "lidl" appears in
    # 3 merchants here, "cambridge" in 2; "lidl" still wins per merchant
    # because picking the most-discriminating-FOR-THIS-MERCHANT means
    # comparing tokens of that merchant only (lidl is lower-freq overall).
    merchants = [
        _m("LIDL GB"),
        _m("LIDL GB LONDON"),
        _m("LIDL GB CAMBRIDGE"),
        _m("TZATZIKI CAMBRIDGE"),
    ]
    clusters = _cluster_santander(merchants, monzo_counts={})
    by_size = sorted(len(c.members) for c in clusters)
    # Three LIDL merchants in one cluster, TZATZIKI in its own.
    assert by_size == [1, 3]
