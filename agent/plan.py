"""Assignment 2's working solution, Parts A and C: the planner and the reviser.
It ships in Assignment 3's starter in place of your own Assignment 2 code.

The plan comes back as data through one strict tool, submit_plan, whose input
schema is agent/plan_schema.json. The planner sees the task, the issue number and
the tool list; the reviser sees the plan and structured step results. Neither
sees a word of the issue.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "agent" / "plan_schema.json").read_text())
SUBMIT_PLAN = {
    "name": "submit_plan",
    "description": "Submit the whole plan as data. Call it exactly once.",
    "input_schema": SCHEMA,
    "strict": True,
}
PLANNER_PROMPT = "prompts/planner.md"
REVISER_PROMPT = "prompts/reviser.md"
STRUCTURED = ("id", "tool", "status", "count", "paths", "error_code")   # all a reviser may see of a step result


def _prompt(rel: str) -> str:
    return (ROOT / rel).read_text()


def _tool_list(tool_schemas: list[dict]) -> str:
    """The tools as the planner reads them: name, what it does, and every argument with its
    type or its values. The examples the registry renders into a description are left out:
    the planner fixes which tool a step calls, and the step's fill call sees the examples."""
    lines = []
    for tool in tool_schemas:
        props = (tool.get("input_schema") or {}).get("properties") or {}
        args = ", ".join(f"{name} ({'|'.join(spec['enum']) if spec.get('enum') else _type(spec)})"
                         for name, spec in props.items())
        about = str(tool.get("description", "")).split("\n\nExample", 1)[0]
        lines.append(f"- {tool['name']}: {about} Arguments: {args or 'none'}.")
    return "\n".join(lines)


def _type(spec: dict) -> str:
    kind = spec.get("type", "any")
    return " or ".join(kind) if isinstance(kind, list) else str(kind)


def _submitted(response) -> list[dict]:
    for call in response.tool_calls:
        if call.name == SUBMIT_PLAN["name"]:
            steps = call.input.get("steps")
            return steps if isinstance(steps, list) else []
    return []


def make_plan(client, issue_number: int, tool_schemas: list[dict], trace=None, *, failures=None) -> dict:
    """Version 1, or a re-plan after a rejection. Never the issue: only its number."""
    user = (f"Plan the triage of issue #{issue_number}. You will not see the issue; the plan's first "
            f"step reads it.\n\nThe tools the plan may use:\n{_tool_list(tool_schemas)}")
    if failures:
        user += ("\n\nYour last plan was rejected. Its failures, as (step, check): "
                 + json.dumps(failures) + "\nCheck 1: unknown tool. Check 2: depends on a step that is not "
                 "earlier. Check 3: no tool or no expected_result. Check 4: too few or too many steps. "
                 "Check \"schema\": the plan did not fit submit_plan's schema; every field of every step is required.")
    response = client.create(system=_prompt(PLANNER_PROMPT), messages=[{"role": "user", "content": user}],
                             tools=[SUBMIT_PLAN])
    if trace is not None:
        trace.model_call(0, response, purpose="plan")
    return {"meta": {"model": response.model, "prompt": PLANNER_PROMPT,
                     "tools": [t["name"] for t in tool_schemas]},
            "steps": _submitted(response)}


def revise_plan(client, plan: dict, step_results: list[dict], trace=None) -> dict:
    """A repair, not a replacement. Sees structured step results only, never `args`."""
    structured = [{k: r.get(k) for k in STRUCTURED} for r in step_results]
    user = ("A checkpoint's check failed. Repair the plan: you may change a step that failed, to retry it, "
            "and any step not yet run; keep every step marked done exactly as it is.\n\n"
            f"The plan:\n{json.dumps(plan.get('steps', []), indent=1)}\n\n"
            f"Results of the steps that have run:\n{json.dumps(structured, indent=1)}")
    response = client.create(system=_prompt(REVISER_PROMPT), messages=[{"role": "user", "content": user}],
                             tools=[SUBMIT_PLAN])
    if trace is not None:
        trace.model_call(0, response, purpose="revise")
    proposed = _submitted(response)

    # A step that is done comes back as it was, whatever the model wrote; a failed one may change.
    done = [str(r.get("id")) for r in step_results if r.get("status") == "done"]
    old = {str(s.get("id")): s for s in plan.get("steps", [])}
    kept = [copy.deepcopy(old[i]) for i in done if i in old]
    rest = [s for s in proposed if str(s.get("id")) not in done]
    meta = dict(plan.get("meta") or {})
    meta.update({"model": response.model, "prompt": REVISER_PROMPT})
    return {"meta": meta, "steps": kept + rest}
