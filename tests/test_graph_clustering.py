"""Tests for the type-cluster graph fold (graph_cluster.cluster_graph_by_type).

The Graph tab must represent the WHOLE case (case 35 = 4917 entities /
27,103 relationships) without dumping thousands of nodes into the browser.
The fold collapses entities into ONE node per type (sized by entity count)
and relationships into inter-type weighted bundles (including self-loops),
so ANY case renders as ~6-10 nodes + ~15-30 edges.

Federal rigor: the fold must be DETERMINISTIC (same input → byte-identical
output) and TOTALS-PRESERVING (no entity or relationship silently vanishes).
"""
from graph_cluster import cluster_graph_by_type


# ── Fixtures matching the live Cases API graph shape ────────────────────────────


def _node(eid, etype, mentions=1, label=None):
    return {"data": {"id": str(eid), "type": etype,
                     "label": label or f"{etype}#{eid}",
                     "mention_count": mentions}}


def _edge(src, tgt, rel="associated_with"):
    return {"data": {"id": f"e{src}-{tgt}-{rel}", "source": str(src),
                     "target": str(tgt), "label": rel, "weight": 0.5}}


# ── Empty / degenerate ──────────────────────────────────────────────────────────


def test_empty_graph_yields_empty_clusters():
    clusters, bundles, meta = cluster_graph_by_type([], [])
    assert clusters == []
    assert bundles == []
    assert meta["total_entities"] == 0
    assert meta["total_relationships"] == 0
    assert meta["type_count"] == 0


def test_nodes_without_edges():
    nodes = [_node(1, "phone"), _node(2, "phone"), _node(3, "email")]
    clusters, bundles, meta = cluster_graph_by_type(nodes, [])
    by_type = {c["type"]: c for c in clusters}
    assert by_type["phone"]["count"] == 2
    assert by_type["email"]["count"] == 1
    assert bundles == []
    assert meta["total_entities"] == 3
    assert meta["total_relationships"] == 0
    assert meta["type_count"] == 2


# ── Cluster sizing + labelling ──────────────────────────────────────────────────


def test_cluster_label_and_count():
    nodes = [_node(i, "phone") for i in range(1905)] + \
            [_node(2000 + i, "account") for i in range(1791)]
    clusters, _, meta = cluster_graph_by_type(nodes, [])
    by_type = {c["type"]: c for c in clusters}
    assert by_type["phone"]["count"] == 1905
    assert by_type["account"]["count"] == 1791
    assert "1,905" in by_type["phone"]["label"] or "1905" in by_type["phone"]["label"]
    # phone-cluster (1905) must be visually larger than account-cluster (1791)
    assert by_type["phone"]["size"] >= by_type["account"]["size"]
    # every cluster carries a stable id derived from its type
    assert by_type["phone"]["id"] == "cluster:phone"


# ── Bundle edges (inter-type + self-loops) ──────────────────────────────────────


def test_inter_type_bundle_weight_is_edge_count():
    nodes = [_node(1, "person"), _node(2, "account"),
             _node(3, "account"), _node(4, "phone")]
    edges = [_edge(1, 2), _edge(1, 3),               # person↔account x2
             _edge(2, 3),                             # account↔account (self-loop) x1
             _edge(1, 4)]                             # person↔phone x1
    _, bundles, meta = cluster_graph_by_type(nodes, edges)
    by_pair = {tuple(sorted([b["source"], b["target"]])): b for b in bundles}
    pa = by_pair[tuple(sorted(["cluster:person", "cluster:account"]))]
    assert pa["weight"] == 2
    aa = by_pair[("cluster:account", "cluster:account")]   # self-loop
    assert aa["weight"] == 1
    assert aa["source"] == aa["target"] == "cluster:account"
    pp = by_pair[tuple(sorted(["cluster:person", "cluster:phone"]))]
    assert pp["weight"] == 1
    # three distinct bundles
    assert len(bundles) == 3


def test_bundle_id_is_stable_and_unordered():
    nodes = [_node(1, "email"), _node(2, "phone")]
    # source/target order swapped on the two edges — must fold to ONE bundle
    edges = [_edge(1, 2), _edge(2, 1)]
    _, bundles, _ = cluster_graph_by_type(nodes, edges)
    assert len(bundles) == 1
    assert bundles[0]["weight"] == 2


# ── Totals preservation (the federal invariant) ─────────────────────────────────


def test_totals_preserved_sum_of_cluster_counts_equals_node_count():
    # Mirror case 35's real type distribution.
    dist = {"phone": 1905, "account": 1791, "email": 900, "person": 183,
            "address": 85, "company": 52, "device": 1}
    nodes = []
    nid = 0
    for etype, n in dist.items():
        for _ in range(n):
            nid += 1
            nodes.append(_node(nid, etype))
    clusters, _, meta = cluster_graph_by_type(nodes, [])
    assert sum(c["count"] for c in clusters) == len(nodes) == 4917
    assert meta["total_entities"] == 4917
    assert meta["type_count"] == 7


def test_totals_preserved_sum_of_bundle_weights_equals_edge_count():
    # 2 phones, 2 accounts; build a known edge multiset.
    nodes = [_node(1, "phone"), _node(2, "phone"),
             _node(3, "account"), _node(4, "account")]
    edges = ([_edge(1, 2)] * 3 +          # phone↔phone x3
             [_edge(3, 4)] * 5 +          # account↔account x5
             [_edge(1, 3)] * 4)           # phone↔account x4
    _, bundles, meta = cluster_graph_by_type(nodes, edges)
    assert sum(b["weight"] for b in bundles) == len(edges) == 12
    assert meta["total_relationships"] == 12


def test_edge_with_unknown_endpoint_is_accounted_not_silently_dropped():
    # Edge references entity 99 which has no node — must be counted in meta,
    # never silently lost (totals integrity).
    nodes = [_node(1, "phone"), _node(2, "phone")]
    edges = [_edge(1, 2), _edge(1, 99)]
    _, bundles, meta = cluster_graph_by_type(nodes, edges)
    known = sum(b["weight"] for b in bundles)
    assert known == 1
    assert meta["edges_with_unknown_endpoint"] == 1
    # nothing vanishes: known + unknown == total edges in
    assert known + meta["edges_with_unknown_endpoint"] == len(edges)
    assert meta["total_relationships"] == len(edges)


# ── Determinism ─────────────────────────────────────────────────────────────────


def test_deterministic_output_order():
    nodes = [_node(1, "email"), _node(2, "phone"), _node(3, "phone"),
             _node(4, "account"), _node(5, "account"), _node(6, "account")]
    edges = [_edge(2, 4), _edge(3, 5), _edge(1, 2)]
    a = cluster_graph_by_type(nodes, edges)
    # Re-run with the SAME input — output must be byte-identical.
    b = cluster_graph_by_type(nodes, edges)
    assert a == b
    clusters_a, _, _ = a
    # Cluster order: by count DESC then type ASC (account 3, phone 2, email 1).
    assert [c["type"] for c in clusters_a] == ["account", "phone", "email"]


def test_accepts_flat_node_and_edge_dicts():
    # The fold must accept already-unwrapped {id,type} / {source,target} too.
    nodes = [{"id": "1", "type": "phone"}, {"id": "2", "type": "email"}]
    edges = [{"id": "e", "source": "1", "target": "2"}]
    clusters, bundles, meta = cluster_graph_by_type(nodes, edges)
    assert meta["total_entities"] == 2
    assert len(bundles) == 1
    assert bundles[0]["weight"] == 1


def test_missing_type_folds_into_unknown_bucket():
    nodes = [{"data": {"id": "1"}}, _node(2, "phone")]
    clusters, _, meta = cluster_graph_by_type(nodes, [])
    by_type = {c["type"]: c for c in clusters}
    assert "unknown" in by_type
    assert by_type["unknown"]["count"] == 1
    assert meta["total_entities"] == 2
