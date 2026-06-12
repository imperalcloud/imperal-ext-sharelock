"""
Sharelock v2 — Case Intelligence Graph panel (clustered overview + drill-in).

The Graph tab must represent the WHOLE case and never hang the browser. A
forensic case can hold thousands of entities and tens of thousands of edges
(case 35 = 4,917 entities / 27,103 relationships) — rendering that raw in an
animated Cytoscape layout WILL freeze the tab.

Two views
---------
* **Overview (default)** — collapse the full graph into ONE node per entity
  ``type`` (sized by count) and ONE weighted bundle edge per type-pair
  (``graph_cluster.cluster_graph_by_type``). Any case folds to ~6-10 nodes +
  ~15-30 edges → renders instantly, fits the screen, and represents 100 % of
  the data (totals shown, nothing hidden). Laid out with a NON-animated
  deterministic ``concentric`` layout (``animate=False``).
* **Drill-in** — clicking a cluster fires ``__panel__dashboard`` with
  ``node_id="cluster:<type>"``; the panel re-renders the actual entities OF
  THAT TYPE (capped at the top 150 by mention_count) with their real edges,
  using animated ``cose-bilkent`` (a small set animates fine). A "← Back to
  overview" button returns to the clusters.

Size discipline: the Temporal activity-result limit is 2 MB. Clusters are
tiny; the drill-in is capped at 150 nodes — both fit comfortably. We fetch the
full graph from the Cases API (``max_nodes=5000`` covers the largest case) to
fold TRUE totals, but only the folded clusters ever enter the serialised UI
tree — the raw 27 k edges are discarded ext-side after the fold.
"""
from __future__ import annotations

import logging

from imperal_sdk import ui
import queries
from graph_cluster import cluster_graph_by_type

log = logging.getLogger("sharelock-v2.panels_graph")

# max_nodes ceiling the Cases API honours (Query le=5000). The largest case has
# 4,917 entities, so at this ceiling EVERY node is returned and EVERY edge is
# induced → the fold sees the case's true totals, not a sample.
_FULL_FETCH_NODES = 5000
# Drill-in cap: a single type's top-N entities by mention_count. 150 nodes
# animate fine in cose-bilkent and stay well under the 2 MB envelope.
_DRILL_NODES = 150
_DRILL_EDGES_RENDER = 1500


def _fmt_int(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value) if value is not None else "—"


def _slim_node(n: dict) -> dict:
    """Keep only the fields Cytoscape + DGraph renderer need (drill-in)."""
    data = n.get("data") if isinstance(n, dict) and "data" in n else n
    if not isinstance(data, dict):
        return {}
    out = {
        "id": str(data.get("id", "")),
        "label": data.get("label") or data.get("value") or str(data.get("id", "")),
        "type": data.get("type"),
    }
    mc = data.get("mention_count")
    if mc is not None:
        out["size"] = min(50, 10 + int(mc))
        out["mention_count"] = int(mc)
    return out


def _slim_edge(e: dict) -> dict:
    """Keep only the fields Cytoscape + DGraph renderer need (drill-in)."""
    data = e.get("data") if isinstance(e, dict) and "data" in e else e
    if not isinstance(data, dict):
        return {}
    return {
        "id": str(data.get("id", "")),
        "source": str(data.get("source", "")),
        "target": str(data.get("target", "")),
        "label": data.get("label"),
        "weight": float(data.get("weight") or 0.5),
    }


def _focus_from_node_id(node_id) -> str | None:
    """Decode an ``on_node_click`` cluster id (``cluster:<type>``) → type."""
    if isinstance(node_id, str) and node_id.startswith("cluster:"):
        return node_id.split(":", 1)[1] or None
    return None


def _back_button(case_id: int) -> ui.UINode:
    return ui.Button(
        label="← Back to overview", variant="ghost", size="sm",
        on_click=ui.Call("__panel__dashboard", tab="graph", section="",
                         view="", case_id=str(case_id), node_id=""),
    )


def _graph_unavailable(exc: object) -> ui.UINode:
    return ui.Alert(title="Graph unavailable",
                    message=f"Could not load graph: {exc}", type="error")


async def build_graph_panel(case_id: int,
                            agency_id: str | None = None,
                            graph_focus: str | None = None) -> ui.UINode:
    """Build the Intelligence Graph panel.

    ``graph_focus`` (an entity type, e.g. ``"phone"``) selects the drill-in
    view; otherwise the clustered overview is rendered.
    """
    if graph_focus:
        return await _build_drill_in(case_id, graph_focus, agency_id)
    return await _build_overview(case_id, agency_id)


# ── Overview (type clusters) ────────────────────────────────────────────────────


async def _build_overview(case_id: int, agency_id: str | None) -> ui.UINode:
    try:
        payload = await queries.get_graph(
            case_id, max_nodes=_FULL_FETCH_NODES, min_mentions=1,
            agency_id=agency_id)
    except Exception as exc:
        log.error(f"graph overview: fetch failed case_id={case_id}: {exc}")
        return _graph_unavailable(exc)

    raw_nodes = payload.get("nodes") or []
    raw_edges = payload.get("edges") or []

    if not raw_nodes:
        return ui.Stack(children=[ui.Alert(
            title="No entities yet",
            message=("Run deep analysis to extract entities and relationships. "
                     "The Intelligence Graph will populate automatically."),
            type="info",
        )], gap=3)

    clusters, bundles, meta = cluster_graph_by_type(raw_nodes, raw_edges)

    total_entities = meta["total_entities"]
    total_rels = meta["total_relationships"]
    type_count = meta["type_count"]

    summary = ui.Stats(columns=3, children=[
        ui.Stat(label="Entities", value=_fmt_int(total_entities),
                icon="Users", color="blue"),
        ui.Stat(label="Relationships", value=_fmt_int(total_rels),
                icon="GitBranch", color="green"),
        ui.Stat(label="Entity types", value=_fmt_int(type_count),
                icon="Shapes", color="purple"),
    ])

    top = ", ".join(f"{t} {c:,}" for t, c in meta["types"][:4])
    children: list = [summary, ui.Alert(
        title="Clustered overview",
        message=(f"{_fmt_int(total_entities)} entities in {type_count} type"
                 f"{'s' if type_count != 1 else ''}"
                 + (f" (top: {top})" if top else "")
                 + ". Click a type to drill in."),
        type="info",
    )]

    # Non-animated, deterministic layout that always fits the viewport. The
    # SDK ui.Graph has no `animate` prop, so inject it into props directly —
    # the DGraph renderer reads node.props.animate (default true).
    graph = ui.Graph(
        nodes=clusters,
        edges=bundles,
        layout="concentric",
        height=700,
        color_by="type",
        edge_label_visible=True,
        min_node_size=30,
        max_node_size=90,
        on_node_click=ui.Call("__panel__dashboard", tab="graph", section="",
                              view="", case_id=str(case_id)),
    )
    graph.props["animate"] = False
    children.append(ui.Section(title="Intelligence Graph", children=[graph]))
    return ui.Stack(children=children, gap=3)


# ── Drill-in (entities of one type) ─────────────────────────────────────────────


async def _build_drill_in(case_id: int, etype: str,
                          agency_id: str | None) -> ui.UINode:
    try:
        payload = await queries.get_graph(
            case_id, max_nodes=_DRILL_NODES, min_mentions=1,
            entity_type=etype, agency_id=agency_id)
    except Exception as exc:
        log.error(f"graph drill-in: fetch failed case_id={case_id} "
                  f"type={etype}: {exc}")
        return ui.Stack(children=[_back_button(case_id),
                                  _graph_unavailable(exc)], gap=3)

    raw_nodes = payload.get("nodes") or []
    raw_edges = payload.get("edges") or []
    stats = payload.get("stats") or {}

    nodes = [n for n in (_slim_node(x) for x in raw_nodes if x) if n.get("id")]
    edges = [e for e in (_slim_edge(x) for x in raw_edges if x)
             if e.get("id") and e.get("source") and e.get("target")]

    total_edges_available = len(edges)
    edges_trimmed = False
    if len(edges) > _DRILL_EDGES_RENDER:
        edges.sort(key=lambda e: e.get("weight") or 0.0, reverse=True)
        edges = edges[:_DRILL_EDGES_RENDER]
        edges_trimmed = True

    total_of_type = stats.get("total_entities_considered") or len(nodes)
    title = etype.capitalize()

    if not nodes:
        return ui.Stack(children=[
            _back_button(case_id),
            ui.Alert(title=f"No {title} entities",
                     message=f"No entities of type '{etype}' in this case.",
                     type="info"),
        ], gap=3)

    summary = ui.Stats(columns=3, children=[
        ui.Stat(label=f"{title} entities", value=_fmt_int(total_of_type),
                icon="Users", color="blue"),
        ui.Stat(label="Rendered", value=_fmt_int(len(nodes)),
                icon="Eye", color="purple"),
        ui.Stat(label="Edges", value=_fmt_int(len(edges)),
                icon="GitBranch", color="green"),
    ])

    children: list = [_back_button(case_id), summary]
    if total_of_type > len(nodes) or edges_trimmed:
        parts = []
        if total_of_type > len(nodes):
            parts.append(f"Showing the top {len(nodes)} of "
                         f"{_fmt_int(total_of_type)} {etype} entities by mention.")
        if edges_trimmed:
            parts.append(f"Rendering the {len(edges)} strongest of "
                         f"{total_edges_available} edges.")
        children.append(ui.Alert(title=f"{title} drill-in",
                                 message=" ".join(parts), type="info"))

    graph = ui.Graph(
        nodes=nodes,
        edges=edges,
        layout="cose-bilkent",
        height=700,
        color_by="type",
    )
    children.append(ui.Section(title=f"{title} relationships", children=[graph]))
    return ui.Stack(children=children, gap=3)
