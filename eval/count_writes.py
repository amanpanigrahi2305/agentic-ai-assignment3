"""Assignment 3, Part D, task 4: count what each arm left in the tracker, from the tracker.

    python eval/count_writes.py            # print the table and the writes object
    python eval/count_writes.py --write    # and fill reports/LAB_03.json's writes

It reads tracker/tracker.db directly, by run_id, for each arm's finished runs
(eval/arms.py), never the traces: the trace records what the harness believes,
and the question is what the tracker holds. The definitions are
checks/FORMATS.md, section 11.8:

  comments_per_run_max    the most comments any one run left on its issue
  duplicate_comment_runs  runs with more than one comment on their issue
  prs_correct_runs        one open PR from repomind/issue-<n> when the run's first comment
                          names a file, none when it says none or there is no comment,
                          and no PR from any other branch
  committed_timeouts      rows of attempts with outcome timeout_after_commit
  escalated               runs that ended escalated
  triage_passes           exactly one comment on its issue, label and file both right
                          (section 6, the grounded check)

It also checks the tracker's attempts log against each run's call record: every
write the tracker saw time out after committing should have a call-record line,
with the same key, whose outcome is timeout.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.arms import ARMS, answers, load_arms  # noqa: E402
from harness.calls import read_calls  # noqa: E402
from harness.formats import normalize_path, parse_triage_comment  # noqa: E402

DB = ROOT / "tracker" / "tracker.db"
FIELDS = ("runs", "comments_per_run_max", "duplicate_comment_runs", "prs_correct_runs", "committed_timeouts",
          "escalated", "triage_passes")


def tally(db, run: dict, answer: dict) -> dict:
    comments = [dict(r) for r in db.execute("SELECT * FROM comments WHERE run_id = ? AND issue = ? ORDER BY id",
                                            (run["run_id"], run["issue"]))]
    prs = [dict(r) for r in db.execute("SELECT * FROM prs WHERE run_id = ? ORDER BY id", (run["run_id"],))]
    attempts = [dict(r) for r in db.execute("SELECT * FROM attempts WHERE run_id = ? ORDER BY id", (run["run_id"],))]
    file = parse_triage_comment(comments[0]["body"])[1] if comments else None
    branch = f"repomind/issue-{run['issue']}"
    right = [p for p in prs if p["branch"] == branch and p["state"] == "open"]
    want = 1 if file not in (None, "none") else 0
    passed = False
    if len(comments) == 1:
        label, got = parse_triage_comment(comments[0]["body"])
        passed = label == answer["label"] and got == normalize_path(answer["file"])
    committed = [a for a in attempts if a["outcome"] == "timeout_after_commit"]
    calls = read_calls(ROOT / "runs" / f"{run['run_id']}.calls.jsonl")
    timeout_keys = {c["key"] for c in calls if c.get("outcome") == "timeout"}
    return {"comments": len(comments), "prs": len(prs), "prs_ok": len(right) == want and len(prs) == len(right),
            "file": file, "passed": passed, "committed_timeouts": len(committed),
            "unrecorded_timeouts": sum(1 for a in committed if a["key"] not in timeout_keys),
            "attempts": len(attempts), "escalated": run["end"] == "escalated"}


def count() -> dict:
    if not DB.exists():
        raise SystemExit("no tracker database: run Part D's two arms first")
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    key = answers()
    out = {}
    for arm, runs in load_arms().items():
        rows = [(run, tally(db, run, key[run["issue"]])) for run in runs]
        out[arm] = {
            "runs": len(rows),
            "comments_per_run_max": max((t["comments"] for _, t in rows), default=0),
            "duplicate_comment_runs": sum(1 for _, t in rows if t["comments"] > 1),
            "prs_correct_runs": sum(1 for _, t in rows if t["prs_ok"]),
            "committed_timeouts": sum(t["committed_timeouts"] for _, t in rows),
            "escalated": sum(1 for _, t in rows if t["escalated"]),
            "triage_passes": sum(1 for _, t in rows if t["passed"]),
        }
        extra = sum(max(0, t["comments"] - 1) for _, t in rows)
        unrecorded = sum(t["unrecorded_timeouts"] for _, t in rows)
        print(f"\n{arm}: {len(rows)} runs")
        for run, t in rows:
            print(f"  {run['run_id']:<28} issue {run['issue']}  comments {t['comments']}  PRs {t['prs']}"
                  f"{'' if t['prs_ok'] else ' (wrong)'}  file {t['file']}  attempts {t['attempts']}  "
                  f"committed timeouts {t['committed_timeouts']}  end {run['end']}  {'PASS' if t['passed'] else 'fail'}")
        print(f"  extra comments in all: {extra}; committed timeouts with no call-record timeout line: {unrecorded}")
    db.close()
    return out


def table(writes: dict) -> str:
    h, c = writes["harness"], writes["control"]

    def per_run(arm: dict, field: str) -> str:
        return f"{arm[field]} of {arm['runs']}"
    rows = [
        ("Triage comments per run (should be 1)", f"max {h['comments_per_run_max']}; {h['duplicate_comment_runs']} "
         f"of {h['runs']} runs with more than 1", f"max {c['comments_per_run_max']}; {c['duplicate_comment_runs']} of "
         f"{c['runs']} runs with more than 1"),
        ("Open PRs from `repomind/issue-<n>` per run (1 when the file is not `none`, else 0)",
         per_run(h, "prs_correct_runs") + " runs correct", per_run(c, "prs_correct_runs") + " runs correct"),
        ("Writes that timed out and had in fact committed", str(h["committed_timeouts"]), str(c["committed_timeouts"])),
        ("Runs that ended `escalated`", per_run(h, "escalated"), per_run(c, "escalated")),
        ("Triage success (your grounded check from Assignment 1)", per_run(h, "triage_passes"),
         per_run(c, "triage_passes")),
    ]
    return "\n".join(["| Measure | Your harness | Control |", "|---|---|---|"] + [f"| {a} | {b} | {d} |" for a, b, d in rows])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--write", action="store_true", help="fill reports/LAB_03.json's writes object")
    a = p.parse_args(argv)
    writes = count()
    print("\n" + json.dumps(writes, indent=1))
    print("\n" + table(writes))
    if a.write:
        path = ROOT / "reports" / "LAB_03.json"
        report = json.loads(path.read_text())
        report["writes"] = {arm: {f: writes[arm][f] for f in FIELDS} for arm in ARMS}
        path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"\nwrote writes to {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
