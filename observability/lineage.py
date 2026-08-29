from __future__ import annotations

import json
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_graph(path: str | Path) -> dict[str, list[str]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload["dataset_lineage"] if "dataset_lineage" in payload else payload


def _transitive_downstream(graph: dict[str, list[str]], start: str) -> list[str]:
    """Shared BFS traversal used by both dataset- and column-level lineage."""
    seen = {start}
    q: deque[str] = deque([start])
    out: list[str] = []
    while q:
        node = q.popleft()
        for child in graph.get(node, []):
            if child not in seen:
                seen.add(child)
                out.append(child)
                q.append(child)
    return out


def get_downstream_assets(graph: dict[str, list[str]], start: str) -> list[str]:
    """Return transitive downstream assets in BFS order, excluding start."""
    return _transitive_downstream(graph, start)


def get_column_downstream(column_graph: dict[str, list[str]], start_column: str) -> list[str]:
    """Return transitive downstream columns in BFS order, excluding start.

    `column_graph` keys/values are `table.column` strings (see
    `data/baseline/lineage_graph.json` -> `column_lineage`). Traversal logic is
    identical to dataset-level lineage; the only difference is the graph's node
    identifiers carry a table-qualified column name instead of just a table name.
    """
    return _transitive_downstream(column_graph, start_column)


def extract_dbt_dataset_graph(manifest_path: str | Path) -> dict[str, list[str]]:
    """Minimal dbt manifest parser.

    It maps each dbt node unique_id to the nodes that depend on it. Students may
    enrich names, exposures, owners, columns, or OpenLineage facets.
    """
    path = Path(manifest_path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    graph: dict[str, list[str]] = {}
    child_map = manifest.get("child_map", {})
    for parent, children in child_map.items():
        graph[parent] = list(children)
    return graph


def extract_dbt_column_graph(manifest_path: str | Path) -> dict[str, list[str]]:
    """Best-effort column-level graph derived from a dbt manifest's `depends_on`.

    dbt's manifest.json does not ship column-level lineage out of the box (that
    requires either dbt's experimental column-level lineage or parsing each
    model's compiled SQL). As a pragmatic starter-lab approximation, this treats
    every column of a node as depending on every column of its declared upstream
    parents -- coarser than true column lineage, but still useful for a
    conservative blast-radius estimate ("might be affected" rather than a false
    "definitely not affected").
    """
    path = Path(manifest_path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    nodes = manifest.get("nodes", {})
    graph: dict[str, list[str]] = {}
    for unique_id, node in nodes.items():
        columns = list(node.get("columns", {}).keys())
        if not columns:
            continue
        for parent_id in node.get("depends_on", {}).get("nodes", []):
            parent = nodes.get(parent_id)
            if not parent:
                continue
            parent_columns = list(parent.get("columns", {}).keys())
            if not parent_columns:
                continue
            for parent_col in parent_columns:
                parent_key = f"{parent_id}.{parent_col}"
                graph.setdefault(parent_key, [])
                for col in columns:
                    child_key = f"{unique_id}.{col}"
                    if child_key not in graph[parent_key]:
                        graph[parent_key].append(child_key)
    return graph


def emit_openlineage_event(
    *,
    job_name: str,
    inputs: list[str],
    outputs: list[str],
    event_type: str = "COMPLETE",
    namespace: str = "data-reliability-lab",
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build a minimal OpenLineage RunEvent (https://openlineage.io/) as a dict.

    This does not require an OpenLineage backend/Marquez -- it produces a
    spec-shaped event any OpenLineage-compatible collector can ingest, which is
    enough to demonstrate the dataset-lineage facts flowing out of a pipeline run
    without adding a network dependency to the lab.
    """
    now = datetime.now(timezone.utc).isoformat()
    return {
        "eventType": event_type,
        "eventTime": now,
        "run": {"runId": run_id or str(uuid.uuid4())},
        "job": {"namespace": namespace, "name": job_name},
        "inputs": [{"namespace": namespace, "name": name} for name in inputs],
        "outputs": [{"namespace": namespace, "name": name} for name in outputs],
        "producer": "data-reliability-lab/observability.lineage",
    }
