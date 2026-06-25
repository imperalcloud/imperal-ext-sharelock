"""Tests for the clustered Graph panel (overview + drill-in) and the
cluster-aware get_intelligence_graph chat tool.

These exercise the WHOLE-case behaviour: the overview must render type
clusters (not a 200-node sample) with a non-animated layout and a drill-in
on_node_click; the drill-in must render the entities of one type capped, with
a back button; the chat tool must report the per-type breakdown with true
totals.
"""
import asyncio

import panels_graph
import handlers_drilldown
import queries


# Case-35-shaped fixture: 4,917 entities across 7 types, single rel_type.
_DIST = {"phone": 1905, "account": 1791, "email": 900, "person": 183,
         "address": 85, "company": 52, "device": 1}


def _full_graph_payload():
    nodes, nid = [], 0
    for etype, n in _DIST.items():
        for _ in range(n):
            nid += 1
            nodes.append({"data": {"id": str(nid), "type": etype,
                                   "label": f"{etype}#{nid}", "mention_count": 3}})
    # A handful of edges spanning a few type pairs (exact weights not asserted
    # here — the fold is unit-tested separately).
    edges = [{"data": {"id": f"e{i}", "source": "1", "target": str(2 + i),
                       "label": "associated_with", "weight": 0.5}}
             for i in range(50)]
    return {"nodes": nodes, "edges": edges,
            "stats": {"total_entities": len(nodes), "total_edges": len(edges)}}


def _walk(node):
    """Yield every UINode in a tree (props.children / props lists)."""
    yield node
    props = getattr(node, "props", {}) or {}
    for v in props.values():
        if isinstance(v, list):
            for x in v:
                if hasattr(x, "props"):
                    yield from _walk(x)
        elif hasattr(v, "props"):
            yield from _walk(v)


def _find(tree, type_name):
    return [n for n in _walk(tree) if getattr(n, "type", None) == type_name]


# ── Overview ─────────────────────────────────────────────────────────────────────


def test_overview_renders_type_clusters_not_raw_nodes(monkeypatch):
    async def fake_graph(case_id, max_nodes=200, min_mentions=1,
                         entity_type=None, agency_id=None):
        # overview must request the full case (high max_nodes), not a sample
        assert max_nodes >= 4917
        assert entity_type is None
        return _full_graph_payload()
    monkeypatch.setattr(queries, "get_graph", fake_graph)

    tree = asyncio.run(panels_graph.build_graph_panel(35, agency_id="default"))
    graphs = _find(tree, "Graph")
    assert len(graphs) == 1
    g = graphs[0]
    # 7 type clusters, NOT 4917 nodes.
    assert len(g.props["nodes"]) == 7
    assert all(n["id"].startswith("cluster:") for n in g.props["nodes"])
    # biggest cluster is phone(1905)
    labels = [n["label"] for n in g.props["nodes"]]
    assert any("Phone" in l and "1,905" in l for l in labels)


def test_overview_is_non_animated_and_clickable(monkeypatch):
    async def fake_graph(case_id, max_nodes=200, min_mentions=1,
                         entity_type=None, agency_id=None):
        return _full_graph_payload()
    monkeypatch.setattr(queries, "get_graph", fake_graph)

    tree = asyncio.run(panels_graph.build_graph_panel(35, agency_id="default"))
    g = _find(tree, "Graph")[0]
    # never animate the overview (would still be cheap at 7 nodes, but the
    # contract is deterministic + non-animated)
    assert g.props.get("animate") is False
    assert g.props.get("layout") == "concentric"
    # clicking a cluster drills in via __panel__dashboard on the graph tab.
    # to_dict() is the runtime shape DGraph spreads + injects node_id into.
    click = g.props.get("on_node_click")
    assert click is not None
    d = click.to_dict()
    assert d["function"] == "__panel__dashboard"
    assert d["params"].get("tab") == "graph"
    assert d["params"].get("case_id") == "35"


def test_overview_stats_reflect_true_totals(monkeypatch):
    async def fake_graph(case_id, max_nodes=200, min_mentions=1,
                         entity_type=None, agency_id=None):
        return _full_graph_payload()
    monkeypatch.setattr(queries, "get_graph", fake_graph)

    tree = asyncio.run(panels_graph.build_graph_panel(35, agency_id="default"))
    stat_vals = [getattr(s, "props", {}).get("value") for s in _find(tree, "Stat")]
    assert "4,917" in stat_vals          # all entities, not "top 200"
    assert "7" in stat_vals              # entity-type count


def test_overview_empty_graph_shows_info(monkeypatch):
    async def fake_graph(case_id, max_nodes=200, min_mentions=1,
                         entity_type=None, agency_id=None):
        return {"nodes": [], "edges": [], "stats": {}}
    monkeypatch.setattr(queries, "get_graph", fake_graph)
    tree = asyncio.run(panels_graph.build_graph_panel(35, agency_id="default"))
    assert _find(tree, "Graph") == []
    assert _find(tree, "Alert")


# ── Drill-in ─────────────────────────────────────────────────────────────────────


def test_drill_in_renders_capped_entities_with_back_button(monkeypatch):
    async def fake_graph(case_id, max_nodes=200, min_mentions=1,
                         entity_type=None, agency_id=None):
        # drill-in must scope to the focus type + cap nodes
        assert entity_type == "phone"
        assert max_nodes <= 200
        nodes = [{"data": {"id": str(i), "type": "phone",
                           "label": f"phone#{i}", "mention_count": 200 - i}}
                 for i in range(150)]
        return {"nodes": nodes, "edges": [],
                "stats": {"total_entities_considered": 1905}}
    monkeypatch.setattr(queries, "get_graph", fake_graph)

    tree = asyncio.run(panels_graph.build_graph_panel(
        35, agency_id="default", graph_focus="phone"))
    g = _find(tree, "Graph")[0]
    assert len(g.props["nodes"]) == 150       # capped
    assert g.props["layout"] == "cose-bilkent"   # animated is fine for a small set
    # a back-to-overview button exists, returning to the cluster view
    buttons = _find(tree, "Button")
    assert any("Back to overview" in (getattr(b, "props", {}).get("label") or "")
               for b in buttons)


def test_focus_decoder():
    assert panels_graph._focus_from_node_id("cluster:phone") == "phone"
    assert panels_graph._focus_from_node_id("cluster:") is None
    assert panels_graph._focus_from_node_id("") is None
    assert panels_graph._focus_from_node_id(None) is None
    assert panels_graph._focus_from_node_id("123") is None   # a raw entity id


# ── Chat tool is cluster-aware ────────────────────────────────────────────────────


def _unlock(monkeypatch):
    import auth_gate

    class U:
        unlocked = True
        role = "investigator"
        imperal_id = "u1"
        agency_id = "default"

    async def fake(ctx, force_fresh=False):
        return U()
    monkeypatch.setattr(auth_gate, "_fetch_unlock", fake)


class _Ctx:
    pass


def test_get_intelligence_graph_reports_type_breakdown(monkeypatch):
    _unlock(monkeypatch)

    async def fake_graph(case_id, max_nodes=200, min_mentions=1,
                         entity_type=None, agency_id=None):
        assert max_nodes >= 4917     # true-totals fetch
        return _full_graph_payload()
    monkeypatch.setattr(queries, "get_graph", fake_graph)

    res = asyncio.run(handlers_drilldown.fn_get_intelligence_graph(
        _Ctx(), handlers_drilldown.CaseIdParams(case_id=35)))
    assert res.status == "success"
    assert res.data["node_count"] == 4917
    assert res.data["type_count"] == 7
    # top entities still capped (never a full dump)
    assert len(res.data["top_entities"]) <= 50
    # breakdown carries the per-type counts, biggest first
    tb = res.data["type_breakdown"]
    assert tb[0] == {"type": "phone", "count": 1905}
    assert "phone 1,905" in res.summary
