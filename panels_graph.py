"""
Sharelock v2 — Case Intelligence Graph panel.

Renders the entities/relationships subgraph from `/cases/{id}/graph`
using the SDK ``ui.Graph`` component (Cytoscape-backed on the Panel
side). Cases API returns Cytoscape-format nodes/edges; ``ui.Graph``
unwraps them server-side so the Panel just needs to register the
cytoscape-js renderer.

Federal rigor: graph payload is deterministic for the same case state
(sort by entity_id/relationship_id in graph_service), so screenshots
embed cleanly in DOJ-style reports.

Size discipline: Temporal activity-result payload limit is 2 MB. A
full 500-node / 13k-edge forensic graph exceeds this. We cap fetch to
a smaller top-N and slim each element to only the fields Cytoscape
actually needs — everything else stays on the Cases API for drill-down.
"""
from __future__ import annotations

import logging

from imperal_sdk import ui
import queries

log = logging.getLogger("sharelock-v2.panels_graph")

# Hard caps so the serialised UI tree stays well below Temporal's 2 MB
# activity result limit (observed: 500 nodes + 13 k edges = ~2.7 MB).
# 200 top-mention nodes + their induced edges comfortably fits in <500 kB.
_MAX_NODES_FETCH = 200
_MIN_MENTIONS = 2            # drop one-off mentions from the overview
_MAX_EDGES_RENDER = 1500     # belt-and-braces: trim edges by strength if the
                             # induced subgraph is still dense


def _fmt_int(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value) if value is not None else "—"


def _slim_node(n: dict) -> dict:
    """Keep only the fields Cytoscape + DGraph renderer need."""
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
        out["size"] = min(50, 10 + int(mc))  # visual size 10..50
        out["mention_count"] = int(mc)
    return out


def _slim_edge(e: dict) -> dict:
    """Keep only the fields Cytoscape + DGraph renderer need."""
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


async def build_graph_panel(case_id: int) -> ui.UINode:
    """Build the Intelligence Graph panel for a case."""
    try:
        payload = await queries.get_graph(
            case_id,
            max_nodes=_MAX_NODES_FETCH,
            min_mentions=_MIN_MENTIONS,
        )
    except Exception as exc:
        log.error(f"graph: failed to fetch case_id={case_id}: {exc}")
        return ui.Alert(
            title="Graph unavailable",
            message=f"Could not load graph: {exc}",
            type="error",
        )

    raw_nodes = payload.get("nodes") or []
    raw_edges = payload.get("edges") or []
    stats = payload.get("stats") or {}

    if not raw_nodes:
        return ui.Stack(children=[
            ui.Alert(
                title="No entities yet",
                message=(
                    "Run deep analysis to extract entities and relationships. "
                    "The Intelligence Graph will populate automatically."
                ),
                type="info",
            ),
        ], gap=3)

    # Slim every element so each node/edge costs ~60–120 bytes in the
    # serialised tree instead of the ~250–400 bytes the raw Cases API
    # payload carries (timestamps, confidence, evidence_count, etc.).
    nodes = [_slim_node(n) for n in raw_nodes if n]
    nodes = [n for n in nodes if n.get("id")]
    edges = [_slim_edge(e) for e in raw_edges if e]
    edges = [e for e in edges if e.get("id") and e.get("source") and e.get("target")]

    # If the induced subgraph still has a huge edge count, keep the
    # strongest connections by weight so the viz stays legible.
    edges_trimmed = False
    total_edges_available = len(edges)
    if len(edges) > _MAX_EDGES_RENDER:
        edges.sort(key=lambda e: e.get("weight") or 0.0, reverse=True)
        edges = edges[:_MAX_EDGES_RENDER]
        edges_trimmed = True

    total_entities_considered = stats.get("total_entities_considered") or len(raw_nodes)
    total_edges_case = stats.get("total_edges") or total_edges_available

    summary = ui.Stats(columns=4, children=[
        ui.Stat(
            label="Entities",
            value=_fmt_int(stats.get("total_entities", len(nodes))),
            icon="Users",
            color="blue",
        ),
        ui.Stat(
            label="Relationships",
            value=_fmt_int(total_edges_case),
            icon="GitBranch",
            color="green",
        ),
        ui.Stat(
            label="Orphans",
            value=_fmt_int(stats.get("orphan_count", 0)),
            icon="CircleDashed",
            color="gray",
        ),
        ui.Stat(
            label="Rendered",
            value=f"{_fmt_int(len(nodes))} / {_fmt_int(len(edges))}",
            icon="Eye",
            color="purple",
        ),
    ])

    children: list = [summary]

    if total_entities_considered > len(nodes) or edges_trimmed:
        msg_parts = []
        if total_entities_considered > len(nodes):
            msg_parts.append(
                f"Showing the top {len(nodes)} of {total_entities_considered} "
                f"entities (filtered to ≥{_MIN_MENTIONS} mentions)."
            )
        if edges_trimmed:
            msg_parts.append(
                f"Rendering the {len(edges)} strongest of {total_edges_available} "
                "relationships in the induced subgraph."
            )
        children.append(ui.Alert(
            title="Overview view",
            message=" ".join(msg_parts),
            type="info",
        ))

    graph = ui.Graph(
        nodes=nodes,
        edges=edges,
        layout="cose-bilkent",
        height=700,
        color_by="type",
    )
    children.append(ui.Section(title="Intelligence Graph", children=[graph]))

    return ui.Stack(children=children, gap=3)
