"""RepoMind's tools, and the registry that runs them.

Each tool is two files with one name: tools/<module>.json, its definition, the only
thing the model sees of it; and tools/<module>.py, the code that runs it. Rename a
tool inside its .json file and keep both file names. tools/roles.yaml says which
tool plays each of RepoMind's five roles (checks/FORMATS.md, section 11.2).

The registry's interface, which the runner, your ways and the checker use
(checks/FORMATS.md, section 11.3):

    registry = ToolRegistry(ctx)          # ctx is a RunContext, below
    registry.schemas                      # what the vendor gets: name, description, input_schema, strict
    registry.names()                      # the tools' names, in MODULES order
    registry.definition(name)             # the whole definition file, examples included
    registry.hooks.append(hook)           # hook(name, tool_input) runs before every call
    registry.call(name, tool_input, plan_step=None) -> ToolResult

A registry is bound to one run: every write is stamped with that run's ID. Build
one per run with `default_tools(ctx)`.

What happens to one call, in order (Assignment 3, Parts A to C):

  1. Protocol check. An unknown tool, or arguments that are not an object, is a
     protocol error: "for you", not the model. It is written in full to
     runs/<run_id>.harness.log and the call record, and the call raises
     ProtocolError, so the agent's loop ends the run tool_error (end_run policy).
  2. Boundary check (Part A, task 6), before dispatch: enum values matched
     case-insensitively, then the JSON schema, then the tool's own content rules
     (lengths, ranges, globs: the keywords strict mode rejects). A rejected call
     never runs; its is_error result names every field at fault and what would be
     right.
  3. Hooks (Part C, task 5): each hook(name, args) may deny the call by returning
     anything but None. A hook that raises denies it too: fail closed.
  4. Dispatch. A read retries transient failures below the model, at most
     READ_ATTEMPTS times; a tool execution error goes back to the model with its
     repair fact first. A write goes through the write gate (below).
  5. The result is checked against the shape its definition declares
     (result_schema) before the model sees it.

The write gate (Part C, tasks 2 to 4 and 6). Every attempt of a write carries an
idempotency key the harness derives, never the model:

    sha256(canonical JSON of {"run_id", "plan_step", "args"})[:32]

with keys sorted and no whitespace, over the arguments the harness validated the
first time, which every retry resends unchanged. Before every attempt it checks
whether the effect already exists in this run (an open PR from the branch; a
comment with this key). After a timeout it reads the write back: found, it
proceeds with that result; not found, it retries with the same key; unreadable,
or still unconfirmed after WRITE_ATTEMPTS attempts, it escalates (the result's
`escalate` is true, and the planned way ends the run escalated). KEY_MODE=uuid
sends a fresh random key on every attempt instead, and VERIFY=off skips the read
back and retries blind: Part D's naive control.

Every attempt of every call leaves one line in the call record (ctx.calls).
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent

# The five modules, in the order the registry offers them. Keep these file names.
MODULES = ("read_issue", "search_repo", "read_file", "post_comment", "open_pr")

# The fields of a definition the vendor gets. Everything else in the file (the
# examples, the result shape) stays here.
SENT = ("name", "description", "input_schema", "strict")

READ_ATTEMPTS = 3          # a read's transient failures, retried below the model
WRITE_ATTEMPTS = 3         # a write's attempts, in both of Part D's arms
BACKOFF_S = 0.05           # between read attempts: the local tracker recovers in milliseconds
ERROR_CHARS = 700          # an error is capped; its repair fact comes first, so a cut one still helps
GENERIC_PROTOCOL = "That call could not be processed."   # never shown under end_run; kept short if it ever is

# Transient: a busy database, a dropped connection. Retried in code, never by the model.
TRANSIENT = (sqlite3.OperationalError, ConnectionError, BlockingIOError, InterruptedError)


class ToolError(Exception):
    """A tool failed in a way the model can be told about. Becomes an is_error result."""


class ProtocolError(RuntimeError):
    """A call no argument can fix: an unknown tool, or arguments that are not an object.
    The registry logs it in full to the harness log and raises this; the loop ends the run."""


@dataclass
class ToolResult:
    content: str
    is_error: bool
    latency_s: float
    outcome: str = "ok"          # the call record's outcome for the attempt the model is told about
    escalate: bool = False       # a write that could not be confirmed: a person must look (the third exit)


@dataclass
class RunContext:
    run_id: str
    tracker: object                    # harness.tracker.Tracker
    repo_root: object                  # pathlib.Path to fixtures/repo
    calls: object = None               # harness.calls.CallRecord: runs/<run_id>.calls.jsonl
    harness_log: object = None         # pathlib.Path: runs/<run_id>.harness.log, for protocol errors
    switches: dict = field(default_factory=dict)   # harness.switches.current(), as the runner read them


def load_definition(module: str) -> dict:
    return json.loads((TOOLS_DIR / f"{module}.json").read_text())


def render(definition: dict) -> dict:
    """The definition as the vendor gets it: SENT fields only, its examples written into
    the end of the description, which every vendor reads."""
    sent = {k: copy.deepcopy(definition[k]) for k in SENT if k in definition}
    examples = definition.get("examples") or []
    if examples:
        lines = [f"Example {i} ({'typical' if i == 1 else 'hard'}): {json.dumps(e.get('input'), separators=(', ', ': '))}"
                 + (f" ({e['note']})" if e.get("note") else "") for i, e in enumerate(examples, 1)]
        sent["description"] = sent.get("description", "") + "\n\n" + "\n".join(lines)
    return sent


def derive_key(run_id: str, plan_step, args: dict) -> str:
    """The idempotency key of one intended write: the same run, step and arguments give the
    same key on every retry. The model never sees it."""
    canonical = json.dumps({"run_id": run_id, "plan_step": plan_step, "args": args},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def cap(text: str, limit: int = ERROR_CHARS) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


# -- the default hook ---------------------------------------------------------------------------------

SECRET = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY|\b(?:sk|pk)_(?:live|test)_\w{8,}|\b(?:API_KEY|DB_URL|PASSWORD|"
                    r"SECRET)\s*=\s*\S+", re.IGNORECASE)


def no_secrets_in_writes(name: str, tool_input: dict):
    """Deny a write whose text looks like a credential: RepoMind reads private code and
    writes in public (Lecture 1's lethal trifecta). Reads are always allowed."""
    from harness.roles import WRITES, role_of
    if role_of(name) in WRITES and SECRET.search(json.dumps(tool_input)):
        return "the text looks like it contains a credential, and this tool writes where the public can read"
    return None


# -- the registry --------------------------------------------------------------------------------------

class ToolRegistry:
    def __init__(self, ctx: RunContext, modules=MODULES):
        self.ctx = ctx
        self.hooks: list = [no_secrets_in_writes]
        self._tools: dict[str, tuple[dict, object]] = {}
        for module in modules:
            definition = load_definition(module)
            self._tools[definition["name"]] = (definition, importlib.import_module(f"tools.{module}"))

    @property
    def schemas(self) -> list[dict]:
        """The definitions as the vendor gets them, in MODULES order, ready for
        ModelClient.create(tools=...)."""
        return [render(d) for d, _ in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def definition(self, name: str) -> dict:
        return self._tools[name][0]

    # -- one call -----------------------------------------------------------------------------------
    def call(self, name: str, tool_input: dict, plan_step=None) -> ToolResult:
        """Run one tool. Every failure the model can act on comes back as an is_error result;
        a protocol error raises ProtocolError (logged first), and the loop ends the run.

        `plan_step` is the id of the plan step the call runs for, from ctx.call_tool,
        or None when the model chose the call (the agent's loop)."""
        start = time.monotonic()
        entry = self._tools.get(name)
        if entry is None or not isinstance(tool_input, dict):
            why = (f"unknown tool {name!r}; this registry has {self.names()}" if entry is None
                   else f"arguments for {name} are a {type(tool_input).__name__}, not a JSON object")
            self._protocol(name, tool_input, plan_step, why, start)
            raise ProtocolError(f"protocol error, logged to the harness log: {why}")
        definition, module = entry

        args, problems = self.validate(name, tool_input)
        if problems:
            message = cap(f"Rejected before {name} ran, so nothing happened: " + "; ".join(problems)
                          + ". Fix these arguments and call again.")
            self._record(name, tool_input, plan_step, None, 1, "rejected", True, message, start)
            return ToolResult(message, True, time.monotonic() - start, "rejected")

        reason = self._denied(name, args)
        if reason is not None:
            message = cap(f"Denied by policy before {name} ran, so nothing happened: {reason}. "
                          "This call will not be allowed with these arguments.")
            self._record(name, args, plan_step, None, 1, "denied", True, message, start)
            return ToolResult(message, True, time.monotonic() - start, "denied")

        from harness.roles import WRITES, role_of
        role = role_of(name)
        if role in WRITES:
            return self._write(name, role, definition, module, args, plan_step)
        return self._read(name, definition, module, args, plan_step)

    # -- the boundary -------------------------------------------------------------------------------
    def validate(self, name: str, tool_input: dict) -> tuple[dict, list[str]]:
        """(the arguments, normalised; every problem, one line each naming its field)."""
        from harness.schema import errors
        definition, module = self._tools[name]
        schema = definition.get("input_schema") or {}
        props = schema.get("properties") or {}
        args = copy.deepcopy(tool_input)
        for key, spec in props.items():          # an enum's capitalisation is not guaranteed
            value = args.get(key)
            if isinstance(value, str) and isinstance(spec, dict) and spec.get("enum"):
                match = {str(e).lower(): e for e in spec["enum"]}.get(value.strip().lower())
                if match is not None:
                    args[key] = match
        problems = [self._explain(line, props) for line in errors(args, schema)]
        if not problems and hasattr(module, "check"):
            problems = list(module.check(args))
        return args, problems

    @staticmethod
    def _explain(line: str, props: dict) -> str:
        """A schema error, with what would be right."""
        field_name = line.split(":", 1)[0].split(".", 1)[0].split("[", 1)[0]
        spec = props.get(field_name)
        if spec is None and "not in the schema" in line:
            return f"{line}: remove it; this tool takes exactly {', '.join(props)}"
        if not isinstance(spec, dict):
            return line
        if spec.get("enum"):
            want = "one of " + ", ".join(map(str, spec["enum"]))
        else:
            kinds = spec.get("type") if isinstance(spec.get("type"), list) else [spec.get("type")]
            want = " or ".join(str(k) for k in kinds if k)
        example = str(spec.get("description", "")).split(". ")[0][:120]
        return f"{line} (expected {want}: {example})"

    def _denied(self, name: str, args: dict) -> str | None:
        """The first hook's reason to deny, or None. A hook that raises denies: fail closed."""
        for hook in list(self.hooks):
            try:
                verdict = hook(name, copy.deepcopy(args))
            except Exception as exc:  # noqa: BLE001 - any failure of a policy check is a denial
                return f"a pre-call hook ({getattr(hook, '__name__', 'hook')}) raised {type(exc).__name__}, " \
                       "and a hook that cannot decide denies"
            if verdict is not None:
                return str(verdict) if isinstance(verdict, str) and verdict else \
                    f"a pre-call hook ({getattr(hook, '__name__', 'hook')}) denied it"
        return None

    # -- reads --------------------------------------------------------------------------------------
    def _read(self, name, definition, module, args, plan_step) -> ToolResult:
        start = time.monotonic()
        for attempt in range(1, READ_ATTEMPTS + 1):
            t0 = time.monotonic()
            try:
                content = module.run(self.ctx, **args)
            except ToolError as exc:
                message = cap(str(exc))
                self._record(name, args, plan_step, None, attempt, "error", True, message, t0)
                return ToolResult(message, True, time.monotonic() - start, "error")
            except TRANSIENT as exc:
                self._record(name, args, plan_step, None, attempt, "error", True,
                             f"transient, retried in code: {type(exc).__name__}: {exc}", t0)
                time.sleep(BACKOFF_S * attempt)
                continue
            except (TypeError, ValueError) as exc:     # a fault in the tool's code, past the boundary
                self._log(f"{name}: the tool raised {type(exc).__name__}: {exc}; args {json.dumps(args)}")
                message = (f"{name} failed inside its own code ({type(exc).__name__}); this is a harness fault, "
                           "not your arguments, and retrying will not help.")
                self._record(name, args, plan_step, None, attempt, "error", True, message, t0)
                return ToolResult(message, True, time.monotonic() - start, "error")
            bad = self._bad_shape(name, definition, content)
            if bad:
                self._record(name, args, plan_step, None, attempt, "error", True, bad, t0)
                return ToolResult(bad, True, time.monotonic() - start, "error")
            self._record(name, args, plan_step, None, attempt, "ok", False, content, t0)
            return ToolResult(content, False, time.monotonic() - start, "ok")
        message = (f"{name} could not reach its data after {READ_ATTEMPTS} attempts (a transient failure, not your "
                   "arguments). Retry after 30 seconds with the same arguments.")
        return ToolResult(message, True, time.monotonic() - start, "error")

    # -- writes: the key, the check before, the three exits -------------------------------------------
    def _write(self, name, role, definition, module, args, plan_step) -> ToolResult:
        from harness.tracker import ReadbackUnavailable, TrackerTimeout
        start = time.monotonic()
        on = {"key_mode": "derived", "verify": "on", **(self.ctx.switches or {})}
        derived = derive_key(self.ctx.run_id, plan_step, args)
        for attempt in range(1, WRITE_ATTEMPTS + 1):
            key = uuid.uuid4().hex if on["key_mode"] == "uuid" else derived
            t0 = time.monotonic()
            try:                                   # check before you execute: one read
                existing = self._existing(role, args, key)
            except ReadbackUnavailable as exc:
                return self._escalate(name, start, f"the check before attempt {attempt} could not read the "
                                                   f"tracker ({exc})")
            if existing is not None:
                content = module.result({**existing, "found": True})
                self._record(name, args, plan_step, key, attempt, "ok", False,
                             f"found by the check before attempt {attempt}, not written again: {content}", t0)
                return ToolResult(content, False, time.monotonic() - start, "ok")
            try:
                content = module.run(self.ctx, **args, idempotency_key=key)
            except TrackerTimeout as exc:
                if on["verify"] == "off":
                    self._record(name, args, plan_step, key, attempt, "timeout", True,
                                 f"{exc}; VERIFY=off: retrying blind", t0)
                    continue
                try:                               # verify: did the effect happen?
                    found = self._existing(role, args, key)
                except ReadbackUnavailable as unreadable:
                    self._record(name, args, plan_step, key, attempt, "timeout", True,
                                 f"{exc}; read back failed ({unreadable}): cannot tell, escalating", t0)
                    return self._escalate(name, start, f"attempt {attempt} timed out and could not be read back")
                if found is not None:
                    self._record(name, args, plan_step, key, attempt, "timeout", True,
                                 f"{exc}; read back: it had committed", t0)
                    content = module.result({**found, "found": True})
                    self._record(name, args, plan_step, key, attempt, "ok", False,
                                 f"confirmed by reading back after the timeout: {content}", t0)
                    return ToolResult(content, False, time.monotonic() - start, "ok")
                self._record(name, args, plan_step, key, attempt, "timeout", True,
                             f"{exc}; read back: it had not happened, retrying with the same key", t0)
                continue
            except ToolError as exc:
                message = cap(str(exc))
                self._record(name, args, plan_step, key, attempt, "error", True, message, t0)
                return ToolResult(message, True, time.monotonic() - start, "error")
            except ValueError as exc:
                message = cap(f"The tracker refused the write: {exc}. Fix the arguments and call again.")
                self._record(name, args, plan_step, key, attempt, "error", True, message, t0)
                return ToolResult(message, True, time.monotonic() - start, "error")
            bad = self._bad_shape(name, definition, content)
            self._record(name, args, plan_step, key, attempt, "ok", False, bad or content, t0)
            return ToolResult(content, False, time.monotonic() - start, "ok")
        return self._escalate(name, start, f"still unconfirmed after {WRITE_ATTEMPTS} attempts")

    def _existing(self, role: str, args: dict, key: str):
        """This run's row for this write, if it is already there: an open PR from the same
        branch, or a comment with this key. Raises ReadbackUnavailable when it cannot tell."""
        from harness.roles import arg
        tracker = self.ctx.tracker
        if role == "open_pr":
            rows = tracker.prs(self.ctx.run_id, branch=args[arg("open_pr", "branch")])
            return rows[0] if rows else None
        return tracker.comment_by_key(self.ctx.run_id, key)

    def _escalate(self, name: str, start: float, why: str) -> ToolResult:
        self._log(f"{name}: escalated: {why}")
        return ToolResult(f"The outcome of {name} could not be confirmed ({why}). The run stops here and a person "
                          "checks the tracker; do not retry this call.", True, time.monotonic() - start,
                          "timeout", escalate=True)

    # -- results, the record, the log -------------------------------------------------------------------
    def _bad_shape(self, name: str, definition: dict, content) -> str | None:
        """Why a result breaks the shape its definition promises, or None. Logged in full."""
        from harness.schema import errors
        shape = definition.get("result_schema")
        if not shape:
            return None
        try:
            problems = errors(json.loads(content), shape)
        except (TypeError, ValueError) as exc:
            problems = [f"not JSON ({exc})"]
        if not problems:
            return None
        self._log(f"{name}: result breaks its declared shape: {problems[:5]}; result {str(content)[:500]}")
        return f"{name} returned a result in the wrong shape; this is a harness fault, not your arguments."

    def _record(self, name, args, plan_step, key, attempt, outcome, is_error, result, t0) -> None:
        if self.ctx.calls is None:
            return
        self.ctx.calls.write(tool=name, args=args if isinstance(args, dict) else {"_raw": repr(args)},
                             plan_step=plan_step, key=key, attempt=attempt, outcome=outcome, is_error=is_error,
                             result=str(result), latency_s=time.monotonic() - t0)

    def _protocol(self, name, tool_input, plan_step, why, start) -> None:
        self._log(f"PROTOCOL ERROR: {why}. plan_step {plan_step!r}; arguments {json.dumps(tool_input, default=str)}")
        self._record(str(name), tool_input, plan_step, None, 1, "protocol", True, why, start)

    def _log(self, text: str) -> None:
        if self.ctx.harness_log is None:
            return
        path = Path(self.ctx.harness_log)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} run {self.ctx.run_id}: {text}\n")


def default_tools(ctx: RunContext) -> ToolRegistry:
    return ToolRegistry(ctx)
