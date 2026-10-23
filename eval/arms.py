"""Assignment 3, Part D: the finished evaluation runs of each arm, sorted by their switches.

The same rule as checks/check_03.py: a planned-way run of this assignment on an
evaluation issue, from a real model, that reached run_end and was not stopped by
the vendor's quota or a rate limit, belongs to the arm whose four switches it
recorded in its run_start line.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from harness import LAB, ROOT
from harness import switches as switch_board
from harness.formats import UNFINISHED
from harness.runner import is_free, is_trace
from harness.trace import read_trace

ARMS = {"harness": {"flaky_writes": True, "readback_fail": False, "key_mode": "derived", "verify": "on"},
        "control": {"flaky_writes": True, "readback_fail": False, "key_mode": "uuid", "verify": "off"}}


def answers(root: Path = ROOT) -> dict[int, dict]:
    return {int(k): v for k, v in yaml.safe_load((root / "fixtures" / "answers.yaml").read_text())["eval"].items()}


def load_arms(root: Path = ROOT) -> dict[str, list[dict]]:
    """{arm: [{run_id, issue, end, records}]}, for each arm's finished runs."""
    eval_issues = set(answers(root))
    arms: dict[str, list[dict]] = {a: [] for a in ARMS}
    for path in sorted((root / "runs").glob("*.jsonl")):
        if not is_trace(path):
            continue
        records = read_trace(path)
        start = records[0] if records and records[0].get("type") == "run_start" else None
        if not start or start.get("lab") != LAB or start.get("way") != "planned" or is_free(start.get("backend") or ""):
            continue
        if start.get("issue") not in eval_issues or records[-1].get("type") != "run_end":
            continue
        ends = [r.get("terminal_reason") for r in records if r.get("type") == "end"]
        if set(ends) & set(UNFINISHED):
            continue
        recorded = {**switch_board.DEFAULTS, **(start.get("switches") or {})}
        for arm, wanted in ARMS.items():
            if recorded == wanted:
                arms[arm].append({"run_id": start["run_id"], "issue": start["issue"],
                                  "end": ends[-1] if ends else None, "records": records})
    return arms
