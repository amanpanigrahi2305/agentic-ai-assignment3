"""Assignment 3, Part D, task 1: what the tool surface costs, per call and per run.

    python eval/price_surface.py --count     # measure the token counts (a few small calls to your model)
    python eval/price_surface.py             # price the harness arm's runs from the saved counts

Counting is by difference, with the model client built as harness/ping.py builds
it: the same one-message call with and without what is counted, the difference
in prompt_tokens (cached tokens included). It counts, for the starter's
definitions and for yours: the five together, each alone, and the planner's tool
list (agent.plan._tool_list) as text in the message. Yours go as your registry
sends them (registry.schemas, examples rendered into the description); the
starter's as its registry sent them: the fields of tools.SENT each file had in
the repository's first commit, with no examples, and its tool list built by the
starter's own _tool_list. A definition counted alone includes the vendor's
preamble for tool use. The counts are saved to eval/surface_counts.json.

Pricing reads every model_call line of the harness arm's runs (eval/arms.py),
which names the tools that call carried (`tools`), and charges each call, at the
model's uncached input rate in pricing.yaml, for the definitions it carried (one
of ours alone, by role; all five together when it carried all five) and, for a
plan or revise call, the tool list too. submit_plan and the second pass's verdict
tool are the same before and after, so they are left out. It prices the same
calls three ways: with the starter's counts, with yours, and as if every call,
the planner's and reviser's included, had carried all five of yours and nothing
else.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.plan import _tool_list  # noqa: E402
from harness.backends import make_backend  # noqa: E402
from harness.client import ModelClient  # noqa: E402
from harness.ping import current_schemas  # noqa: E402
from harness.pricing import PriceSheet  # noqa: E402
from harness.roles import ROLES, role_of, tool_for  # noqa: E402
from harness.settings import load_settings  # noqa: E402
from tools import MODULES, SENT  # noqa: E402

FIRST_COMMIT = "b05a857"                        # the starter, as GitHub's template created it
COUNTS = ROOT / "eval" / "surface_counts.json"
SYSTEM = "You are a connectivity test."
USER = "Do not call any tool. Answer with the word ok."
LIST_HEAD = "\n\nThe tools the plan may use:\n"   # how agent.plan.make_plan puts the list in its message
STARTER_ROLE = {"read_issue": "read_issue", "search_repo": "search", "read_file": "read_file",
                "post_comment": "comment", "open_pr": "open_pr"}


def starter_schemas() -> list[dict]:
    """The starter's five definitions from the first commit, as its registry sent them."""
    out = []
    for module in MODULES:
        text = subprocess.run(["git", "show", f"{FIRST_COMMIT}:tools/{module}.json"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout
        definition = json.loads(text)
        out.append({k: definition[k] for k in SENT if k in definition})
    return out


def starter_tool_list(tool_schemas: list[dict]) -> str:
    """agent.plan._tool_list as the starter wrote it (first commit), unchanged."""
    lines = []
    for tool in tool_schemas:
        props = (tool.get("input_schema") or {}).get("properties") or {}
        args = ", ".join(f"{name} ({spec.get('type', 'any')})" for name, spec in props.items())
        lines.append(f"- {tool['name']}: {tool.get('description', '')} Arguments: {args or 'none'}.")
    return "\n".join(lines)


def prompt_tokens(client, *, tools=None, user=USER) -> int:
    response = client.create(system=SYSTEM, messages=[{"role": "user", "content": user}], tools=tools)
    return response.usage.prompt_tokens


def count() -> dict:
    settings = load_settings(ROOT / "settings.yaml")
    client = ModelClient(make_backend(settings.provider, settings), PriceSheet(ROOT / "pricing.yaml"))
    base = prompt_tokens(client)
    out = {"model": settings.model, "baseline_prompt_tokens": base}
    for which, schemas, listing, roles_of in (
            ("before", starter_schemas(), starter_tool_list, lambda s: STARTER_ROLE[s["name"]]),
            ("after", current_schemas(), _tool_list, lambda s: role_of(s["name"]))):
        together = prompt_tokens(client, tools=schemas) - base
        alone = {roles_of(s): prompt_tokens(client, tools=[s]) - base for s in schemas}
        text = listing(schemas)
        list_tokens = prompt_tokens(client, user=USER + LIST_HEAD + text) - base
        out[which] = {"names": [s["name"] for s in schemas], "together": together, "alone": alone,
                      "list": list_tokens, "list_chars": len(text)}
        print(f"{which:>6}: five together {together}, alone {alone}, planner's list {list_tokens} "
              f"({len(text)} characters)")
    COUNTS.write_text(json.dumps(out, indent=2) + "\n")
    print(f"saved to {COUNTS.relative_to(ROOT)}; {client.totals()['prompt_tokens']} prompt tokens spent")
    return out


def price(counts: dict) -> dict:
    from eval.arms import load_arms
    runs = load_arms()["harness"]
    if not runs:
        raise SystemExit("no finished harness-arm runs yet: run Part D, task 3 first")
    settings = load_settings(ROOT / "settings.yaml")
    rate = PriceSheet(ROOT / "pricing.yaml").find(settings.model).input / 1_000_000   # USD per uncached token
    ours = {tool_for(r): r for r in ROLES}
    totals = {"before": 0, "after": 0, "all_five": 0}
    calls = 0
    for run in runs:
        for record in run["records"]:
            if record.get("type") != "model_call":
                continue
            calls += 1
            carried = [ours[n] for n in record.get("tools") or [] if n in ours]
            planning = record.get("purpose") in ("plan", "revise")
            for which in ("before", "after"):
                c = counts[which]
                tokens = c["together"] if len(set(carried)) == len(ROLES) else sum(c["alone"][r] for r in carried)
                totals[which] += tokens + (c["list"] if planning else 0)
            totals["all_five"] += counts["after"]["together"]
    n = len(runs)
    out = {"runs": n, "calls_per_run": round(calls / n, 2),
           "tokens_per_run": {k: round(v / n, 1) for k, v in totals.items()},
           "cost_per_run": {k: round(v / n * rate, 7) for k, v in totals.items()},
           "input_usd_per_million": rate * 1_000_000}
    print(json.dumps(out, indent=2))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--count", action="store_true", help="measure the token counts with your model first")
    a = p.parse_args(argv)
    counts = count() if a.count or not COUNTS.exists() else json.loads(COUNTS.read_text())
    if not a.count:
        price(counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
