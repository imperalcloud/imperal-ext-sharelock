"""Sharelock v2 — type-cluster graph fold (pure, deterministic).

The Intelligence Graph tab must represent the WHOLE case — case 35 holds
4,917 entities / 27,103 relationships — without shipping thousands of nodes
to the browser (animated Cytoscape physics would hang the tab) and without
hiding any of the data behind a "top 200" sample.

The fold collapses the full graph into:

* ONE cluster node per entity ``type`` (phone / account / email / person /
  address / company / device …), sized by entity count and labelled
  ``"Phone (1,905)"`` — so the user sees 100 % of the entities folded into
  ~6-10 readable nodes.
* ONE weighted bundle edge per unordered type-pair (self-loops included):
  e.g. ``phone↔phone 6,615``, ``account↔phone 4,232`` — folding all 27,103
  relationships into ~15-30 readable edges.

Federal rigor
-------------
* **Deterministic** — same input → byte-identical output (clusters sorted by
  count DESC then type ASC; bundles sorted by the unordered cluster-id pair).
* **Totals-preserving** — ``sum(cluster.count) == len(nodes)`` and
  ``sum(bundle.weight) + edges_with_unknown_endpoint == len(edges)``. Nothing
  is silently dropped: edges that reference an entity with no node are counted
  in ``meta["edges_with_unknown_endpoint"]`` rather than discarded.

This module is PURE (no I/O, no SDK, no network) so it is trivially testable
and reusable by both the panel (panels_graph) and the chat summary
(handlers_drilldown.get_intelligence_graph).
"""
from __future__ import annotations

from typing import Any

_UNKNOWN = "unknown"


def _unwrap(el: Any) -> dict:
    """Accept Cases API ``{"data": {...}}`` or already-flat ``{...}`` dicts."""
    if isinstance(el, dict):
        inner = el.get("data")
        if isinstance(inner, dict):
            return inner
        return el
    return {}


def _node_type(data: dict) -> str:
    t = data.get("type")
    if isinstance(t, str) and t.strip():
        return t.strip()
    return _UNKNOWN


def cluster_graph_by_type(
    nodes: list, edges: list
) -> tuple[list[dict], list[dict], dict]:
    """Fold a full entity/relationship graph into type clusters + bundles.

    Args:
        nodes: Cytoscape nodes (``{"data": {"id", "type", ...}}`` or flat).
        edges: Cytoscape edges (``{"data": {"source", "target", ...}}`` or flat).

    Returns:
        ``(cluster_nodes, bundle_edges, meta)`` where

        * ``cluster_nodes`` — one per type: ``{"id": "cluster:<type>",
          "label": "<Type> (<count>)", "type", "count", "size",
          "mention_count"}`` sorted by count DESC, type ASC.
        * ``bundle_edges`` — one per unordered type-pair (self-loops kept):
          ``{"id": "bundle:<a>__<b>", "source", "target", "weight": <count>,
          "label": "<count>"}`` sorted by the cluster-id pair.
        * ``meta`` — true totals: ``{"total_entities", "total_relationships",
          "type_count", "types": [(type, count), ...],
          "edges_with_unknown_endpoint"}``.
    """
    nodes = nodes if isinstance(nodes, list) else []
    edges = edges if isinstance(edges, list) else []

    # 1) Fold nodes → per-type counts + an entity-id → type index for edges.
    type_count: dict[str, int] = {}
    id_to_type: dict[str, str] = {}
    for n in nodes:
        data = _unwrap(n)
        etype = _node_type(data)
        type_count[etype] = type_count.get(etype, 0) + 1
        eid = data.get("id")
        if eid is not None:
            id_to_type[str(eid)] = etype

    # 2) Fold edges → per unordered type-pair counts (self-loops kept once).
    pair_weight: dict[tuple[str, str], int] = {}
    edges_unknown = 0
    for e in edges:
        data = _unwrap(e)
        src = data.get("source")
        tgt = data.get("target")
        st = id_to_type.get(str(src)) if src is not None else None
        tt = id_to_type.get(str(tgt)) if tgt is not None else None
        if st is None or tt is None:
            edges_unknown += 1
            continue
        pair = (st, tt) if st <= tt else (tt, st)
        pair_weight[pair] = pair_weight.get(pair, 0) + 1

    # 3) Build deterministic cluster nodes (count DESC, then type ASC).
    ordered_types = sorted(type_count.items(), key=lambda kv: (-kv[1], kv[0]))
    max_count = max((c for _, c in ordered_types), default=1) or 1
    cluster_nodes: list[dict] = []
    for etype, count in ordered_types:
        # Visual size 20..70 scaled by share of the largest cluster, so the
        # biggest type is clearly dominant yet the smallest stays clickable.
        size = 20.0 + 50.0 * (count / max_count)
        cluster_nodes.append({
            "id": f"cluster:{etype}",
            "label": f"{etype.capitalize()} ({count:,})",
            "type": etype,
            "count": count,
            "size": round(size, 2),
            # mention_count drives the concentric layout ring + node sizing in
            # the renderer; use the cluster count so bigger types sit central.
            "mention_count": count,
        })

    # 4) Build deterministic bundle edges (sorted by cluster-id pair).
    bundle_edges: list[dict] = []
    for (a, b) in sorted(pair_weight.keys()):
        weight = pair_weight[(a, b)]
        bundle_edges.append({
            "id": f"bundle:{a}__{b}",
            "source": f"cluster:{a}",
            "target": f"cluster:{b}",
            "weight": weight,
            "label": f"{weight:,}",
        })

    meta = {
        "total_entities": len(nodes),
        "total_relationships": len(edges),
        "type_count": len(type_count),
        "types": ordered_types,            # [(type, count), ...] count DESC
        "edges_with_unknown_endpoint": edges_unknown,
    }
    return cluster_nodes, bundle_edges, meta
