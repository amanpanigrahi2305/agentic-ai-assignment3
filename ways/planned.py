"""Assignment 2's working solution, Parts B, C and D: plan, validate, execute inside the
boundary, revise at one checkpoint, and a second pass before posting.
It ships in Assignment 3's starter in place of your own Assignment 2 code, changed in
two ways only: it finds each tool by its role in tools/roles.yaml (harness.roles), so
a tool you rename still runs, and it reads a search result as text or as JSON
(harness.formats.result_paths). The plan rule is still Assignment 2's, the comment
last; a PR step runs like any other step, unskipped, until Part C.

The boundary: the executor runs the plan's steps in order and nothing else. Where a
step's arguments are "$fill", one model call fills them, offered that step's tool
alone; a call to any other tool is refused, traced as off_plan, and never runs.
Two tries without an on-plan call halt the run as escalated.

The planner's output is checked against agent/plan_schema.json (harness.schema)
before the four checks, and so is the rule that its last step is the comment: a
vendor can accept "strict" and not enforce it, and a plan with no comment step
passes all four checks and posts nothing. A plan that breaks either is rejected
like one that fails a check.

The checkpoint, a check in code: after the first search step, and after any search
step a revision added or changed, until MAX_REVISIONS is spent, the search returned
at least one source file: a .ts or .tsx file under src/, not only documentation
(README.md, CHANGELOG.md, docs/) or translations (src/i18n/*.json). A revision never
changes a step that is done; it may change a failed step, which then runs again.

Part G's ways/notes.py extends this file without changing it: it subclasses
Executor and overrides its hooks, record() (after every step), context() (what
every executor call sees) and the class attribute critique, and asks its questions
once run() has returned, however the run ended.

The second pass, before the comment, in ctx.critique's mode: external runs
agent.critique.verify, code; self asks the model with agent.critique.self_critique.
A failing draft with a round left is rewritten by one call; a draft still failing
in the last round is held, and the run escalates. One comment, after the last round.

Assignment 3, Part C. The plan rule: exactly one comment step, the last, with
exactly one PR step right before it (schema_check). A PR can be closed and a
comment cannot, so the comment comes last. The PR needs the triage's file, so when
the executor reaches the PR step it first drafts the comment and runs the second
pass on it (draft()); a draft the second pass holds ends the run before either
write. The skip is a check in the executor: a draft whose file is none opens no
PR, and the PR step is recorded "skipped". Then the comment step posts that same
draft. A write the registry cannot confirm (its result's `escalate`) ends the run
escalated, and a protocol error ends it tool_error, before anything else runs.
"""

from __future__ import annotations

import copy
import json
import re

from agent.critique import self_critique, verify
from agent.diff import step_args
from agent.plan import SCHEMA, make_plan, revise_plan
from agent.validator import validate
from harness.formats import FILL, format_triage_comment, parse_triage_comment, result_paths
from harness.roles import ROLES, arg, tool_for
from harness.schema import errors as schema_errors
from tools import ProtocolError

MAX_REPLANS = 2       # three planner calls in all; a fourth on the same request is the same coin, so a person looks
MAX_REVISIONS = 2     # a repair of the first search, and one more if the repair also finds nothing
MAX_ROUNDS = 2        # check, rewrite once, check again: slide 43's limit, the same in both modes
MAX_FILL_TRIES = 2    # a step the model will not fill on plan, twice, halts the run
CHECKPOINT = "after_first_search"
SOURCE = re.compile(r"^src/(?!i18n/).+\.tsx?$")   # code, not documentation or translations
CONTEXT_CHARS = 6000  # of each earlier result, in a fill call


def role(name: str | None) -> str | None:
    """The role a step's tool plays (tools/roles.yaml), or None."""
    return next((r for r in ROLES if tool_for(r) == name), None)


def allows_null(spec: dict) -> bool:
    """Whether a property's schema takes null: "type": [..., "null"], or a null branch."""
    kind = spec.get("type")
    return ("null" in kind if isinstance(kind, list) else kind == "null") or any(
        isinstance(b, dict) and allows_null(b) for b in spec.get("anyOf") or spec.get("oneOf") or [])


def run(ctx) -> None:
    plan = plan_until_valid(ctx)
    if plan is None:
        return
    ctx.trace.plan(1, plan)
    Executor(ctx, plan).run()


def schema_check(ctx, plan: dict, version: int) -> list[dict]:
    """The plan as the planner's tool returned it, against agent/plan_schema.json. A
    failure is written as a schema line, and comes back as failures for a re-plan."""
    problems = schema_errors({k: v for k, v in plan.items() if k != "meta"}, SCHEMA)
    problems += order_problems(plan.get("steps") or [])
    if problems:
        ctx.trace.event("schema", version=version, errors=problems[:20])
    return [{"step": None, "check": "schema", "error": e} for e in problems[:5]]


def order_problems(steps: list) -> list[str]:
    """The plan rule: exactly one comment step, the last, and exactly one PR step right before it."""
    roles_ = [role(s.get("tool")) if isinstance(s, dict) else None for s in steps]
    comment, pr = tool_for("comment"), tool_for("open_pr")
    out = []
    if roles_.count("comment") != 1:
        out.append(f"the plan has {roles_.count('comment')} {comment} steps; it needs exactly one, the last")
    elif roles_[-1] != "comment":
        out.append(f"the plan's last step is not {comment}: the comment comes last")
    if roles_.count("open_pr") != 1:
        out.append(f"the plan has {roles_.count('open_pr')} {pr} steps; it needs exactly one, right before {comment}")
    elif len(roles_) < 2 or roles_[-2] != "open_pr":
        out.append(f"the {pr} step is not right before the last step, {comment}: open the PR, then post the comment")
    return out


def plan_until_valid(ctx) -> dict | None:
    """The reject exit: re-plan up to MAX_REPLANS times, then escalate. Never execute a rejected plan."""
    failures = None
    for attempt in range(1, MAX_REPLANS + 2):
        plan = make_plan(ctx.client, ctx.issue_number, ctx.tools.schemas, trace=ctx.trace, failures=failures)
        broken = schema_check(ctx, plan, 1)
        if broken:                                # rejected before the four checks: it never runs
            failures = broken
            continue
        verdict = validate(plan)
        ctx.trace.event("validation", version=1, attempt=attempt, valid=verdict["valid"],
                        failures=verdict["failures"])
        if verdict["valid"]:
            return plan
        failures = verdict["failures"]
    ctx.trace.end("escalated", detail=f"no valid plan in {MAX_REPLANS + 1} attempts; last failures {failures}")
    return None


class Executor:
    critique = True   # the notes way turns the second pass off

    def __init__(self, ctx, plan: dict):
        self.ctx = ctx
        self.plan = copy.deepcopy(plan)
        self.version = 1
        self.results: list[dict] = []    # step_results (checks/FORMATS.md, section 10.3), in the order steps ran
        self.outputs: dict[str, str] = {}  # each step's tool output, for later calls; never shown to the reviser
        self.calls = 0                   # execute and critique calls so far: the step number of the next lines
        self.revisions = 0
        self.revised: set[str] = set()   # ids of steps a revision added or changed: the checkpoint follows them
        self.ran: set[str] = set()       # ids of steps that ran and are not to run again
        self.draft: dict | None = None   # the comment's arguments, drafted and passed before either write

    # -- the plan, in order ------------------------------------------------------------
    def next_step(self) -> dict | None:
        return next((s for s in self.plan.get("steps") or [] if str(s.get("id")) not in self.ran), None)

    def run(self) -> None:
        while True:
            step = self.next_step()
            if step is None:
                return                            # the plan is finished; the runner writes completed
            if role(step.get("tool")) == "open_pr":
                if not self.open_pr(step):
                    return
                continue
            if role(step.get("tool")) == "comment":
                if not self.comment(step):
                    return
                continue
            first = not any(role(r["tool"]) == "search" for r in self.results)
            result = self.run_step(step)
            if result is None:
                return                            # halted, and ended escalated
            if role(step.get("tool")) == "search" and (first or str(step.get("id")) in self.revised):
                self.checkpoint(CHECKPOINT, any(SOURCE.match(p) for p in result.get("paths") or []))

    # -- one step ------------------------------------------------------------------------
    def run_step(self, step: dict) -> dict | None:
        tool, args = step.get("tool"), step_args(step)
        if not tool:
            return self.record(step, args, None, "no_tool")
        to_fill = self.open_keys(step, args)
        args = self.filled(step, args)
        if args is None:
            return None
        result = self.call(step, args, to_fill)
        if result is None:
            return None
        return self.record(step, args, result)

    def call(self, step: dict, args: dict, to_fill: list[str], note: str = ""):
        """Run one step's tool through the registry. A call the boundary rejected, on a step
        with arguments the model filled, is filled once more with the rejection's repair fact.
        None when the run has ended: a protocol error (tool_error) or a write no one can
        confirm (escalated)."""
        try:
            result = self.ctx.call_tool(self.calls, step["tool"], args, plan_step=step.get("id"))
            if result.outcome == "rejected" and role(step["tool"]) != "comment":
                # Re-fill every field the boundary refused, as well as the "$fill" ones: a value the
                # plan fixed can be the bad one, and it must not win over the model's repair.
                schema = next((t for t in self.ctx.tools.schemas if t["name"] == step["tool"]), None) or {}
                props = (schema.get("input_schema") or {}).get("properties") or {}
                bad = self.ctx.tools.faulty(step["tool"], args)
                keys = list(dict.fromkeys(to_fill + [k for k in bad if k in props]))
                self.ctx.trace.event("repair", step=step.get("id"), tool=step["tool"], fields=keys,
                                     error=result.content[:300])
                fixed = {k: v for k, v in args.items() if k not in keys and k in props}
                again = self.fill(step, {**fixed, **{k: FILL for k in keys}}, keys,
                                  note=(note + "\n\n" if note else "") + "Your last call was rejected: "
                                  + result.content + " Call the tool again with every argument fixed.")
                if again is not None:
                    args.clear()
                    args.update(self.pr_args(again) if role(step["tool"]) == "open_pr" else again)
                    result = self.ctx.call_tool(self.calls, step["tool"], args, plan_step=step.get("id"))
        except ProtocolError as exc:
            self.record(step, args, None, "protocol")
            self.ctx.trace.end("tool_error", detail=f"step {step.get('id')}: {exc}")
            return None
        if result.escalate:
            self.record(step, args, result, "unconfirmed")
            self.ctx.trace.end("escalated", detail=f"step {step.get('id')}, {step['tool']}: the write could not be "
                                                   "confirmed, so a person checks the tracker")
            return None
        return result

    def open_keys(self, step: dict, args: dict) -> list[str]:
        """The arguments a fill call must supply: those the plan marked "$fill", and any the
        tool's schema requires that the plan left out."""
        schema = next((t for t in self.ctx.tools.schemas if t["name"] == step.get("tool")), None) or {}
        required = (schema.get("input_schema") or {}).get("required") or []
        return [k for k, v in args.items() if v == FILL] + [k for k in required if k not in args]

    def filled(self, step: dict, args: dict) -> dict | None:
        to_fill = self.open_keys(step, args)
        if not to_fill:
            return args
        args = {**args, **{k: FILL for k in to_fill}}
        filled = self.fill(step, args, to_fill)
        if filled is None:
            self.record(step, args, None, "off_plan")
            self.ctx.trace.end("escalated", detail=f"step {step.get('id')}: no on-plan call in {MAX_FILL_TRIES} tries")
        return filled

    def fill(self, step: dict, args: dict, keys: list[str], *, system: str = "executor.md",
             purpose: str = "execute", note: str = "") -> dict | None:
        """One model call fills a step's "$fill" arguments, offered the step's tool alone. It
        keeps every argument the model gave that the schema knows, the ones the plan never
        mentioned included, and a null where the schema allows one (Part A's required and
        nullable fields); the plan's own values win."""
        schema = next((t for t in self.ctx.tools.schemas if t["name"] == step["tool"]), None)
        if schema is None:
            return None
        fixed = {k: v for k, v in args.items() if v != FILL}
        props = (schema.get("input_schema") or {}).get("properties") or {}
        nullable = {k for k, s in props.items() if isinstance(s, dict) and allows_null(s)}
        for _ in range(MAX_FILL_TRIES):
            self.calls += 1
            response = self.ctx.client.create(system=self.ctx.prompt(system), tools=[schema],
                                              messages=[{"role": "user", "content": self.fill_request(step, fixed, keys, note)}])
            self.ctx.trace.model_call(self.calls, response, purpose=purpose)
            for call in response.tool_calls:
                if call.name != step["tool"]:
                    self.refuse(step, call)       # the boundary: never run, always traced
                    continue
                filled = {**{k: v for k, v in call.input.items() if k in props}, **fixed}   # the plan's values win
                if any(k not in filled or filled[k] in ("", FILL) or (filled[k] is None and k not in nullable)
                       for k in keys):
                    continue
                if role(step["tool"]) == "comment":
                    body = arg("comment", "body")
                    label, file = parse_triage_comment(str(filled[body]))
                    filled[body] = format_triage_comment(label or "bug", file)
                if role(step["tool"]) == "open_pr":
                    filled = self.pr_args(filled)
                return filled
        return None

    def refuse(self, step: dict, call) -> None:
        self.ctx.trace.event("off_plan", step=step.get("id"), tool=call.name, input=call.input, version=self.version)

    def context(self) -> list[str]:
        """What the run has gathered so far, one block per step, for a fill or a critique."""
        return [f"## Step {r['id']}, {r['tool']}, {r['status']}\n{self.outputs.get(str(r['id']), '')[:CONTEXT_CHARS]}"
                for r in self.results]

    def fill_request(self, step: dict, fixed: dict, keys: list[str], note: str = "") -> str:
        parts = [f"Triage issue #{self.ctx.issue_number}. You are running step {step.get('id')} of a fixed "
                 f"plan: {step.get('action')}. Call {step['tool']} once. Fill in: {', '.join(keys)}. "
                 f"The plan fixes: {json.dumps(fixed)}."]
        if role(step["tool"]) == "comment":
            parts.append("The comment's first two lines must be:\nlabel: <bug | feature | question | docs>\n"
                         "file: <path relative to the repository root, or none>")
        if role(step["tool"]) == "open_pr" and self.draft is not None:
            _, file = parse_triage_comment(str(self.draft[arg("comment", "body")]))
            parts.append(f"The triage comment is drafted, and will be posted after this step:\n\n"
                         f"{self.draft[arg('comment', 'body')]}\n\nOpen the draft pull request for {file}: "
                         f"branch {self.branch()}; a title of one line, at most 120 characters, saying what the "
                         f"change does; a body whose first line is 'File: {file}', then two to five sentences "
                         "proposing the change in prose.")
        if note:
            parts.append(note)
        return "\n\n".join(parts + self.context())

    def record(self, step: dict, args: dict, result, error: str | None = None) -> dict:
        tool = step.get("tool")
        text = result.content if result is not None else ""
        count, paths = None, []
        if result is not None and role(tool) in ("search", "read_file"):
            paths = result_paths(tool, args, text, result.is_error)
            count = len(paths) if role(tool) == "search" and not result.is_error else None
        if error is None and result is not None and result.is_error:
            error = ("not_found" if "no such file" in text or "File not found" in text
                     else "rejected" if getattr(result, "outcome", "") == "rejected" else "tool_error")
        if error is None and role(tool) == "search" and not count:
            error = "no_matches"
        entry = {"id": step.get("id"), "tool": tool, "status": "failed" if error else "done", "count": count,
                 "paths": paths, "error_code": error, "args": args}
        self.results.append(entry)
        self.ran.add(str(step.get("id")))
        self.outputs[str(step.get("id"))] = text
        for s in self.plan.get("steps") or []:
            if str(s.get("id")) == str(step.get("id")):
                s["status"] = entry["status"]
        return entry

    # -- revision, at the checkpoint whose check failed --------------------------------------
    def checkpoint(self, name: str, ok: bool) -> bool:
        """True when a new version is now active."""
        if ok or self.revisions >= MAX_REVISIONS:
            return False
        self.revisions += 1
        proposed = revise_plan(self.ctx.client, self.plan, self.results, trace=self.ctx.trace)
        broken = schema_check(self.ctx, proposed, self.version + 1)
        verdict = {"valid": False, "failures": broken} if broken else validate(proposed)
        self.ctx.trace.event("revision", checkpoint=name, version=self.version + 1, valid=verdict["valid"],
                             failures=verdict["failures"])
        if not verdict["valid"]:
            return False                          # rejected: it never runs, and it counted against the bound
        before = {str(s.get("id")): {k: v for k, v in s.items() if k != "status"} for s in self.plan.get("steps") or []}
        changed = {str(s.get("id")) for s in proposed.get("steps") or []
                   if before.get(str(s.get("id"))) != {k: v for k, v in s.items() if k != "status"}}
        self.revised |= changed
        failed = {str(r["id"]) for r in self.results if r["status"] == "failed"}
        done = {str(r["id"]) for r in self.results if r["status"] == "done"}
        self.ran -= (changed & failed) - done      # a failed step the revision changed runs again
        self.version += 1
        self.plan = proposed
        self.ctx.trace.plan(self.version, proposed)
        return True

    # -- the draft, the PR, and the comment, in that order -----------------------------------------
    def comment_step(self) -> dict | None:
        return next((s for s in self.plan.get("steps") or [] if role(s.get("tool")) == "comment"), None)

    def branch(self) -> str:
        return f"repomind/issue-{self.ctx.issue_number}"

    def pr_args(self, args: dict) -> dict:
        """The PR's arguments with what code decides fixed: this run's issue, its branch, and a
        body that names the drafted file first."""
        args = dict(args)
        args[arg("open_pr", "number")] = self.ctx.issue_number
        args[arg("open_pr", "branch")] = self.branch()
        if self.draft is not None:
            _, file = parse_triage_comment(str(self.draft[arg("comment", "body")]))
            body = str(args.get(arg("open_pr", "body")) or "")
            if file and file != "none" and file not in body.split("\n", 1)[0]:
                args[arg("open_pr", "body")] = f"File: {file}\n\n{body}".strip()
        return args

    def draft_comment(self) -> dict | None:
        """The comment, drafted and through the second pass, before either write. None when
        the run has ended: the draft could not be filled, or the second pass held it."""
        if self.draft is not None:
            return self.draft
        step = self.comment_step()
        if step is None:
            return None
        body = arg("comment", "body")
        draft = self.filled(step, {**step_args(step), arg("comment", "number"): self.ctx.issue_number})
        if draft is None:
            return None
        problems = self.ctx.tools.validate(step["tool"], draft)[1]
        if problems:                               # the boundary would refuse it: repair it now, before any write
            self.ctx.trace.event("repair", step=step.get("id"), tool=step["tool"], error="; ".join(problems)[:300])
            repaired = self.fill(step, {**draft, body: FILL}, [body],
                                 note="The draft was invalid: " + "; ".join(problems) + ". Write it again.")
            draft = repaired if repaired is not None else draft
        final = self.second_pass(step, draft) if self.critique else draft
        if final is None:
            self.record(step, draft, None, "held")
            self.ctx.trace.end("escalated", detail=f"the draft still failed the second pass after {MAX_ROUNDS} "
                                                   "rounds; nothing was written")
            return None
        self.draft = final
        return final

    def open_pr(self, step: dict) -> bool:
        """Draft the comment, then open the PR it names, or skip it when it names none.
        False when the run has ended."""
        draft = self.draft_comment()
        if draft is None:
            return False
        _, file = parse_triage_comment(str(draft[arg("comment", "body")]))
        if file in (None, "none"):
            self.skip(step, "the drafted comment names file: none, so no pull request is opened")
            return True
        args = {**step_args(step), arg("open_pr", "number"): self.ctx.issue_number,
                arg("open_pr", "branch"): self.branch()}    # code decides these; the model fills the rest
        to_fill = self.open_keys(step, args)
        args = self.filled(step, args)
        if args is None:
            return False
        args = self.pr_args(args)                  # a plan that fixed the body still names the file first
        result = self.call(step, args, to_fill)
        if result is None:
            return False
        entry = self.record(step, args, result)
        if entry["status"] == "failed":            # a PR that failed for good: post nothing a reader would see
            self.ctx.trace.end("escalated", detail=f"step {step.get('id')}: the pull request could not be opened "
                                                   f"({result.content[:160]}); no comment was posted")
            return False
        return True

    def skip(self, step: dict, why: str) -> None:
        self.ctx.trace.event("skipped", step=step.get("id"), tool=step.get("tool"), reason=why)
        entry = {"id": step.get("id"), "tool": step.get("tool"), "status": "skipped", "count": None, "paths": [],
                 "error_code": None, "args": step_args(step)}
        self.results.append(entry)
        self.ran.add(str(step.get("id")))
        self.outputs[str(step.get("id"))] = why
        for s in self.plan.get("steps") or []:
            if str(s.get("id")) == str(step.get("id")):
                s["status"] = "skipped"

    def comment(self, step: dict) -> bool:
        """Post the draft the PR step was built from, once. False when the run has ended."""
        draft = self.draft_comment()
        if draft is None:
            return False
        result = self.call(step, draft, [])
        if result is None:
            return False
        self.record(step, draft, result)
        return True

    def evidence(self) -> dict:
        issue = next((self.outputs.get(str(r["id"]), "") for r in self.results if role(r["tool"]) == "read_issue"), "")
        return {"issue": issue, "results": self.context()}

    def second_pass(self, step: dict, args: dict) -> dict | None:
        mode = self.ctx.critique
        if mode not in ("self", "external"):
            return args
        body = arg("comment", "body")
        for round_ in range(1, MAX_ROUNDS + 1):
            draft = args[body]
            if mode == "external":
                verdict = verify(draft, self.ctx)
            else:
                self.calls += 1
                v = self_critique(self.ctx.client, draft, self.evidence(), self.ctx.trace, step=self.calls)
                verdict = {"passed": v["decision"] == "accept", "reasons": [v["reason"]] if v.get("reason") else []}
            reason = "; ".join(verdict["reasons"]) or None
            changed = False
            if not verdict["passed"] and round_ < MAX_ROUNDS:
                rewritten = self.rewrite(step, args, reason or "the critic asked for a revision")
                if rewritten is not None:
                    changed = rewritten[body] != draft
                    args = rewritten
            self.ctx.trace.event("critique", round=round_, mode=mode, result="pass" if verdict["passed"] else "fail",
                                 reason=reason, changed=changed, draft=draft)
            if verdict["passed"]:
                return args
        return None                               # still failing in the last round: held

    def rewrite(self, step: dict, args: dict, reason: str) -> dict | None:
        """One model call rewrites the draft with the reason, offered the comment tool alone."""
        body = arg("comment", "body")
        note = f"Rewrite the draft. The draft:\n\n{args[body]}\n\nWhy it must change: {reason}"
        return self.fill(step, {**args, body: FILL}, [body], system="rewrite.md", purpose="critique", note=note)
