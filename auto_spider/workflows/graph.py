from __future__ import annotations

from langgraph.graph import END, START, StateGraph


def build_graph(execute=None, checkpointer=None):
    """Actual conditional workflow, with persisted interrupts for human review."""
    builder = StateGraph(dict)
    nodes = (
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
        "close",
    )
    for name in nodes:

        def node(state, name=name):
            return execute(name, state) if execute else {**state, "node": name}

        builder.add_node(name, node)
    builder.add_edge(START, "normalize_input")
    builder.add_conditional_edges(
        "normalize_input", lambda s: "build_spec" if s.get("kind") == "repair" else "inspect_site"
    )
    builder.add_edge("inspect_site", "build_spec")
    builder.add_conditional_edges(
        "build_spec",
        lambda s: (
            "build_report"
            if s.get("skip_generation")
            else "diagnose_failure"
            if s.get("kind") == "repair"
            else "generate_code"
        ),
    )
    builder.add_edge("generate_code", "run_validation")
    builder.add_edge("run_validation", "build_report")
    builder.add_edge("run_regression", "build_report")
    builder.add_edge("build_report", "report_gate")
    builder.add_conditional_edges("report_gate", lambda s: s["route"])
    builder.add_edge("build_failure_bundle", "diagnose_failure")
    builder.add_conditional_edges("diagnose_failure", lambda s: s["route"])
    builder.add_edge("patch_code", "run_regression")
    builder.add_edge("commit_candidate", "await_manual_run")
    builder.add_edge("await_manual_run", "record_review")
    builder.add_edge("record_review", END)
    builder.add_edge("close", END)
    return builder.compile(checkpointer=checkpointer)
