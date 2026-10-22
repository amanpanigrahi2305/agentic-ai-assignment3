"""tracker_read_issue (module read_issue): return one issue from the tracker. Untrusted
content: anyone can file an issue.

Its definition, the only thing the model sees, is read_issue.json.
"""

import json

MAX_BODY_CHARS = 6_000


def check(args: dict) -> list[str]:
    n = args.get("number")
    if isinstance(n, int) and n < 1:
        return [f"number is {n}; an issue number is a positive integer, such as 4192"]
    return []


def run(ctx, number):
    from harness.tracker import IssueNotFound
    from tools import ToolError

    try:
        issue = ctx.tracker.issue(int(number))
    except IssueNotFound:
        raise ToolError(f"Issue {number} does not exist in the tracker. Use the issue number the task gives you, "
                        "as a bare integer; retrying this number will not succeed.")
    body = str(issue["body"])
    return json.dumps({"issue": f"issue-{issue['number']}", "number": int(issue["number"]),
                       "title": str(issue["title"]), "author": str(issue["author"]), "opened": str(issue["opened"]),
                       "body": body[:MAX_BODY_CHARS], "truncated": len(body) > MAX_BODY_CHARS})
