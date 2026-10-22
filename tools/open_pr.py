"""pulls_open_draft (module open_pr): open a draft pull request on the mock remote.

Its definition is open_pr.json. The mock remote needs no commits: a PR is a branch,
a title and a body, stored in the tracker's database. `idempotency_key` is not in
the definition: the registry passes it, as for tracker_post_comment.
"""

import json

MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 4_000


def branch_for(number) -> str:
    return f"repomind/issue-{number}"


def check(args: dict) -> list[str]:
    out = []
    n, branch, title, body = (args.get(k) for k in ("number", "branch", "title", "body"))
    if isinstance(n, int) and n < 1:
        out.append(f"number is {n}; an issue number is a positive integer, such as 4192")
    if isinstance(n, int) and isinstance(branch, str) and branch != branch_for(n):
        out.append(f"branch is {branch[:60]!r}; for issue {n} it must be exactly {branch_for(n)!r}")
    if isinstance(title, str):
        if not title.strip():
            out.append("title is empty: give one line saying what the change does")
        elif len(title) > MAX_TITLE_CHARS or "\n" in title:
            out.append(f"title is {len(title)} characters{' over several lines' if chr(10) in title else ''}; "
                       f"it must be one line of at most {MAX_TITLE_CHARS}")
    if isinstance(body, str):
        if not body.strip():
            out.append("body is empty: its first line is 'File: <path>', then the proposed change in prose")
        elif len(body) > MAX_BODY_CHARS:
            out.append(f"body is {len(body):,} characters; the cap is {MAX_BODY_CHARS:,}")
    return out


def result(row: dict) -> str:
    """What the model is told about a PR row, new or found again."""
    return json.dumps({"pr": f"pr-{row['id']}", "issue": f"issue-{row['issue']}", "branch": row["branch"],
                       "state": row.get("state", "open"), "draft": bool(row.get("draft", 1)),
                       "status": "already_open" if row.get("replayed") or row.get("found") else "opened"})


def run(ctx, number, branch, title, body, idempotency_key=None):
    from harness.tracker import IssueNotFound
    from tools import ToolError

    try:
        pr = ctx.tracker.open_pr(ctx.run_id, int(number), str(branch), str(title), str(body), key=idempotency_key)
    except IssueNotFound:
        raise ToolError(f"Issue {number} does not exist in the tracker, so no pull request was opened. Use the "
                        "number of the issue this triage read; retrying this number will not succeed.")
    return result(pr)
