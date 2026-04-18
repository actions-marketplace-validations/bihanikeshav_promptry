"""Pretend-we're-an-LLM-agent tour of the mock DB via MCP-shaped tool calls.

This script imports the same tool handlers the MCP server exposes and
invokes them as an agent would. If this works, `promptry mcp` will
behave identically when driven by a real Claude / Cursor client.

Usage: python -m mockdb.agent_insights
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from promptry import mcp_server as m

console = Console(force_terminal=True, legacy_windows=False)


def _h(n: int, title: str):
    console.rule(f"[bold]agent query {n}.[/bold] {title}", style="magenta")


def tour():
    _h(1, "How many prompt versions of chipmunk-support-system exist?")
    out = m.prompt_list(name="chipmunk-support-system")
    console.print(out)

    _h(2, "Show me the latest prompt content.")
    out = m.prompt_show(name="chipmunk-support-system")
    console.print(out[:1500])

    _h(3, "Diff v1 vs v10 — what changed?")
    out = m.prompt_diff(name="chipmunk-support-system", v1=1, v2=10)
    console.print(out[:2500])

    _h(4, "Is any suite drifting in the last 14 days?")
    for suite in ("billing-no-prices", "grounded-features", "no-pwn", "response-length", "off-topic-refusal"):
        try:
            out = m.check_drift(suite_name=suite, module="mockdb.suites", window=14)
        except Exception as e:
            out = f"(error: {e})"
        console.print(f"  [bold]{suite}[/bold]")
        console.print(out[:600])
        console.print("")

    _h(5, "Show the cost report — token spend over last 28 days.")
    out = m.cost_report(days=28)
    console.print(out[:2000])

    _h(6, "What safety templates do we have? (first 8)")
    out = m.list_templates()
    console.print(out[:1500])

    _h(7, "Compare models on billing-no-prices: llama3.2-1b vs qwen2.5-0.5b baseline.")
    try:
        out = m.compare_models(
            suite_name="billing-no-prices",
            candidate="llama3.2-1b",
            baseline="qwen2.5-0.5b",
        )
        console.print(out[:2000])
    except Exception as e:
        console.print(f"[red]compare_models failed:[/red] {e}")

    _h("END", "agent tour complete")
    console.print("[dim]Every call above is what a Claude/Cursor client would see over MCP.[/dim]")


if __name__ == "__main__":
    tour()
