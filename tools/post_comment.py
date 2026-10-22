"""tracker_post_comment (module post_comment): write a comment on an issue. Public:
whoever filed the issue can read it.

Its definition is post_comment.json. `idempotency_key` is not in the definition:
the model never sees or sets it. The registry derives it and passes it (Assignment
3, Part C, task 2), and the tracker accepts each key once.
"""

import json

from harness.formats import LABELS, parse_triage_comment

MAX_BODY_CHARS = 4_000


def check(args: dict) -> list[str]:
    out = []
    n, body = args.get("number"), args.get("body")
    if isinstance(n, int) and n < 1:
        out.append(f"number is {n}; an issue number is a positive integer, such as 4192")
    if isinstance(body, str):
        if not body.strip():
            out.append("body is empty: its first two lines must be 'label: <bug | feature | question | docs>' "
                       "and 'file: <path, or none>'")
        elif len(body) > MAX_BODY_CHARS:
            out.append(f"body is {len(body):,} characters; the cap is {MAX_BODY_CHARS:,}: shorten the note "
                       "after the first two lines")
        else:
            label, file = parse_triage_comment(body)
            if label not in LABELS:
                out.append(f"body's label line is {label!r}: line 1 must be 'label: ' and one of "
                           f"{', '.join(LABELS)}")
            if file is None:
                out.append("body has no file line: line 2 must be 'file: ' and a path relative to the "
                           "repository root, or 'none'")
    return out


def result(row: dict) -> str:
    """What the model is told about a comment row, new or found again."""
    return json.dumps({"comment": f"comment-{row['id']}", "issue": f"issue-{row['issue']}",
                       "status": "already_posted" if row.get("replayed") or row.get("found") else "posted"})


def run(ctx, number, body, idempotency_key=None):
    from harness.tracker import IssueNotFound
    from tools import ToolError

    if not str(body).strip():
        raise ToolError("body is empty: its first two lines must be 'label: ...' and 'file: ...'.")
    try:
        comment = ctx.tracker.post_comment(ctx.run_id, int(number), str(body), key=idempotency_key)
    except IssueNotFound:
        raise ToolError(f"Issue {number} does not exist in the tracker, so nothing was posted. Use the number of "
                        "the issue this triage read; retrying this number will not succeed.")
    return result(comment)
