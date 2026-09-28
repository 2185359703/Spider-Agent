from __future__ import annotations

from typing import Any, TypedDict

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - exercised when runner dependency is absent
    END = START = StateGraph = None


class WorkflowGraphState(TypedDict, total=False):
    task_id: str
    run_id: str
    node: str
    observation: dict[str, Any]
    spec: dict[str, Any]
    changed_files: list[str]
    validation: dict[str, Any]
    report_id: str
    next_action: str
    failure_bundle_id: str


def build_graph():
    """Build the deterministic graph used by the worker once LangGraph is installed.

    Database writes remain in WorkflowRunner; these nodes only carry typed state.
    That separation makes replay and unit testing safe.
    """

    if StateGraph is None:
        return None

    builder = StateGraph(WorkflowGraphState)

    def mark(name: str):
        def node(state: WorkflowGraphState) -> WorkflowGraphState:
            return {**state, "node": name}

        return node

    nodes = [
        "normalize_input",
        "inspect_site",
        "build_spec",
        "generate_code",
        "run_validation",
        "build_report",
        "report_gate",
        "commit_candidate",
        "await_manual_run",
        "record_review",
        "build_failure_bundle",
        "diagnose_failure",
        "patch_code",
        "run_regression",
        "submit_fix",
    ]
    for name in nodes:
        builder.add_node(name, mark(name))
    builder.add_edge(START, nodes[0])
    for current, following in zip(nodes, nodes[1:], strict=False):
        builder.add_edge(current, following)
    builder.add_edge(nodes[-1], END)
    return builder.compile()
