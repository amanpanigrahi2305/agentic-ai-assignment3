# Assignment 3 · A tool surface that tells the truth, and writes that happen once

Fill in each section. Every number here must match `reports/LAB_03.json`, and
every count of writes must come from the tracker's database, not from your traces.

## Part A · The five definitions

- **Strict tools, optional parameters, nullable or union-typed parameters** (from
  `python tools/audit_schemas.py`): **5** strict tools, **0** optional parameters,
  **2** nullable parameters (`code_search.path_glob` and `code_read_file.start_line`,
  each `["…", "null"]`). Within both vendors' limits: at most 20 strict tools, 0
  optional parameters across the five, at most 16 union types, nesting 1 level, no
  validation keywords, `additionalProperties: false` on every object.
- **Strict mode.** My model is Mistral `ministral-14b-2512`, as in Assignments 1 and 2,
  through Mistral's OpenAI-compatible API. Mistral's function calling takes a
  `strict` field on a function. Every definition sets `"strict": true`, and
  `make ping`'s third call was **accepted** with all five marked strict ("your 5
  definitions were accepted in one request (5 marked strict)", recorded in
  `runs/_ping.jsonl`), so `compiles` is `true`. Mistral does not document the
  grammar limits Anthropic and Azure do, so the audit's limits are what keep the
  same five definitions portable to a vendor that compiles them.
- **Consolidation, in one line:** No two of the five are always called together in
  the same order: a search is followed by a read only sometimes, and the two writes
  must stay separate so that each can be keyed and verified on its own. So I merged
  none, and consolidated *inside* tools instead. `source: index | live` replaces the
  free-floating `deep` flag, and `start_line` lets one read tool continue a long file
  rather than adding a second tool.

Names use one prefix per service: `tracker_read_issue`, `tracker_post_comment`
(the issue tracker), `code_search`, `code_read_file` (the codebase), and
`pulls_open_draft` (the pull requests). Every result is JSON with semantic
identifiers (`issue-4192`, `comment-12`, `pr-7`). The registry sends `name`,
`description`, `input_schema` and `strict` only, with the two examples rendered into
the end of the description (`tools/__init__.py`, `render`).

`search_repo`, before and after:

| | Before (the starter) | After |
|---|---|---|
| Name | `search_repo` | `code_search` (namespaced: the codebase) |
| Description | "Search the code." | What it searches (src/, docs/, test/, root; never config/), how it matches (literal, case-insensitive substring over line text, not file names, not regex), when to use it, the full result shape, what an empty result means, what is malformed, and the cap |
| Query | `query` (string): "Text to look for" | `query` (string): 1 to 200 characters of literal text, what kind of term finds the most, no operators |
| Filter | `filter` (string): "Optional filter expression" | `path_glob` (string or null): a glob relative to the root, with examples; the codebase is TypeScript; null searches everything; a glob matching no file gives count 0 |
| Limit | `max_results` (integer, optional): "Default 10" | `max_results` (integer, required): 1 to 20, the token cost of each hit, when to choose 10 or 20; `count` always reports every match |
| Depth | `deep` (boolean, optional) | `source`: enum `index \| live`, the cost and staleness of each, and when `live` is worth it |
| Format | none | `response_format`: enum `concise \| detailed`, concise the cheap default; detailed adds 240-character lines and the line before and after |
| Optional parameters | 3 of 4 | 0 of 5 (required, nullable where absence means something) |
| `additionalProperties` | not set | `false` |
| Result | `path:line: text` lines, or "No matches." for both an empty result and a malformed query | JSON `{query, query_valid, count, shown, truncated, hits[{path, line, text}], source, index_built_at, index_age, path_glob, note}`. A miss is `count: 0, query_valid: true`; a malformed query is `is_error` |
| Examples | none | typical (`writeHeader`, whole codebase, index, concise), then hard (a quoted error message, docs only via `{README.md,docs/**}`, live, detailed) |
| Strict | absent | `true` |

**The boundary** (`tools/__init__.py`, `ToolRegistry.validate`) runs before every
dispatch. It matches enum values case-insensitively (`"INDEX"` becomes `"index"`),
checks the JSON schema (`harness/schema.py`), then runs the tool's own content rules
(`check()` in each tool module): lengths, ranges, globs, the comment's `label:` and
`file:` lines, and the PR's branch. Those are the rules strict mode cannot carry.
Every result is checked against the `result_schema` its definition file declares
before the model sees it. A rejected call never runs and writes nothing, and its
error names every field at fault (the checker's 15 malformed calls: 15 of 15).

The dev run (`python run.py --way planned --set dev`) finished on both issues, and
every tool call used a new name. The planner put `pulls_open_draft` second to last
and `tracker_post_comment` last.

## Part B · Errors

| Tool | Transient (retried in code) | Permanent (to the model, with a repair fact) | Ambiguous |
|---|---|---|---|
| `tracker_read_issue` | issue store busy or interrupted (sqlite `OperationalError`, `ConnectionError`, `InterruptedError`), up to 3 attempts | no such issue; number not a positive integer; unknown or missing argument | none: a read changes nothing |
| `code_search` | index or file read interrupted, up to 3 attempts | empty or whitespace-only query; query over 200 characters; `path_glob` not a valid glob; `max_results` outside 1 to 20; an enum value it does not know | none |
| `code_read_file` | a file read interrupted or the disk busy, up to 3 attempts | path not on the repository's file list (case-sensitive, near misses named); under `config/`; leaves the repository or is a glob; not a text file; `start_line` out of range | none |
| `tracker_post_comment` | a timeout before dispatch, or the check before the write unable to connect for a moment | no such issue; body empty, over 4,000 characters, or without a valid `label:` / `file:`; a hook denies it | a timeout after dispatch (`TrackerTimeout`): read back by key, then proceed, retry with the same key, or escalate; a read-back that fails too (`ReadbackUnavailable`): escalate |
| `pulls_open_draft` | as for the comment | no such issue; branch not `repomind/issue-<number>`; title empty, over 120 characters or several lines; body empty or over 4,000 characters; a hook denies it | a timeout after dispatch: read back by branch, then proceed, retry with the same key, or escalate; a read-back that fails too: escalate |

The tracker raises the same `TrackerTimeout` whether or not the write committed. So
the registry treats every write timeout as ambiguous, never as transient, and
always reads it back before deciding (Part C).

- **Protocol errors: `end_run`.** An unknown tool name, or arguments that are not an
  object, is logged in full to `runs/<run_id>.harness.log` and to the call record
  (outcome `protocol`). The registry then raises `ProtocolError`, and the loop ends
  the run `tool_error` without sending the model another request. Why: in the planned
  way the validator's check 1 already refuses any plan step whose tool does not
  exist, so a protocol error at the registry means the harness and the model
  disagree about the interface. That is a bug for a person, not something the model
  can repair. A generic result would hand the model an error no argument can fix,
  and it would spend turns rewording arguments. Ending the run costs one run, which
  is visible in the trace and the harness log, and cannot drift into a write.
- **Execution errors** come back `is_error: true`, capped at 700 characters. The
  repair fact comes first (what was wrong, what would be right), then whether a retry
  can work: "Retrying … will not succeed" for a permanent failure, "Retry after 30
  seconds with the same arguments" after the read retries are spent, and "do not
  retry this call" for an escalated write.
- **Paths are matched against the file list, never the file system.**
  `code_read_file` compares the requested path, case included, with the list of the
  repository's files, so `src/report.ts` can never open `src/Report.ts` on a
  case-insensitive disk. The near misses are the same path in another case, one or
  two characters away, or the same file name in another directory with case, `_` and
  `-` ignored (so `src/ui/export_button.tsx` suggests
  `src/components/ExportButton.tsx`).
- **The exact text of the `read_file` near-miss error for `src/report.ts`**:

  ```
  File not found: src/report.ts is not a file in the repository (paths are case-sensitive and must match exactly). Did you mean: src/Report.ts, src/report.tsx, src/reports.ts? Retrying src/report.ts will not succeed.
  ```

Tested without tokens, by calling the registry from Python:
- `repo_delete_branch` raised `ProtocolError`, and the harness log received
  `PROTOCOL ERROR: unknown tool 'repo_delete_branch'; this registry has [...]. plan_step None; arguments {"branch": "main"}`.
- A search with a whitespace query, glob `src/[unclosed` and `max_results` 50 was
  rejected with one line for each of the three fields.
- `{"number": "issue-4192"}` was rejected with
  `number: string, where the schema wants integer (expected integer: The issue's number as a bare integer, 4192, …)`.
- The call record holds `protocol`, `rejected`, `rejected`, `error`, `ok`.

## Part C · Writes that may already have happened

- **The key formula** (`tools/__init__.py`, `derive_key`):
  `sha256(json.dumps({"run_id": run_id, "plan_step": step_id, "args": args}, sort_keys=True, separators=(",", ":")))[:32]`.
  The arguments are the ones the boundary validated on the first attempt, and every
  retry resends them unchanged from inside the registry, so the model never takes
  part in a retry. The key is never in a definition: each write tool's `run()` takes
  `idempotency_key` from the registry. `KEY_MODE=uuid` replaces it with
  `uuid4().hex` on every attempt.
- **Check before, then verify (the three exits).** Before every attempt, the
  registry reads whether the effect already exists in this run: an open PR from
  `repomind/issue-<n>` (`tracker.prs(run_id, branch=…)`), or a comment with this
  attempt's key (`tracker.comment_by_key`). After a `TrackerTimeout` it reads the
  same thing back:
  - **found**: proceed, with the existing row (`status: already_posted` or `already_open`);
  - **not found**: retry with the same key;
  - **`ReadbackUnavailable`**: escalate.

  It makes at most 3 attempts, and one still unconfirmed after the third escalates.
  An escalated result carries `escalate: true`, and the planned way ends the run
  `escalated`. `VERIFY=off` skips the read-back and retries blind, but keeps the
  check before every attempt and the 3-attempt bound.
- **The plan rule** (`ways/planned.py`, `order_problems`): exactly one
  `tracker_post_comment` step, the last, and exactly one `pulls_open_draft` step,
  right before it. A plan that breaks it is rejected and re-planned. The planner and
  reviser prompts state the order.
- **How a run whose file is `none` skips the PR: a check in the executor.** When the
  executor reaches the PR step it first drafts the comment and runs the second pass
  on it (`draft_comment`). A draft the second pass holds ends the run escalated
  before either write. If the draft's `file:` is `none`, the executor writes a
  `skipped` event, marks the PR step `skipped`, and never calls `pulls_open_draft`.
  The comment step then posts that same draft. Otherwise the model fills the PR's
  title and body with the draft in front of it. Code fixes the number and the branch,
  and makes the body's first line `File: <path>`. Tested free with
  `harness.scripted:DraftNamesNoFile`: one comment, no PR, a `skipped` event. A real
  dev run whose draft named `none` (issue 4192, sweep 1) also opened no PR.
- **One call-record line for a write.** A PR that committed and then timed out, with
  the next line for the same attempt confirming it by reading it back
  (`runs/planned-4191-s03-e0c9c7.calls.jsonl`; the body is shortened here):

```json
{"run_id": "planned-4191-s03-e0c9c7", "t": 12.515, "tool": "pulls_open_draft", "role": "open_pr", "args": {"number": 4191, "branch": "repomind/issue-4191", "title": "Update README to replace 'npm run serve' with the correct build-and-start command", "body": "File: README.md\n\nThe \"Running locally\" section instructs users to run `npm run serve` …"}, "plan_step": 4, "key": "01486bba53b32cda28b9cea991e4837e", "attempt": 1, "outcome": "timeout", "is_error": true, "result": "the tracker did not answer in time; read back: it had committed", "latency_s": 0.01, "cost_usd": 0.0}
{"run_id": "planned-4191-s03-e0c9c7", "t": 12.516, "tool": "pulls_open_draft", "role": "open_pr", "args": {"…": "as above"}, "plan_step": 4, "key": "01486bba53b32cda28b9cea991e4837e", "attempt": 1, "outcome": "ok", "is_error": false, "result": "confirmed by reading back after the timeout: {\"pr\": \"pr-11\", \"issue\": \"issue-4191\", \"branch\": \"repomind/issue-4191\", \"state\": \"open\", \"draft\": true, \"status\": \"already_open\"}", "latency_s": 0.012, "cost_usd": 0.0}
```

- **The hook.** `no_secrets_in_writes` runs before every call and denies a write
  whose text looks like a credential (RepoMind reads private code and writes in
  public). Any hook that raises denies the call (fail closed). A denied call never
  runs and leaves a call-record line with outcome `denied`.
- **Development tests (not the measurement).**
  - `FLAKY_WRITES=1 … --set dev --runs 3`: all 6 runs finished, each with exactly one
    comment, and a PR exactly when its comment named a file.
  - Writes that timed out in those runs:
    - `4191-s03`: the PR and then the comment each committed and timed out, and each
      was confirmed by reading it back.
    - `4191-s01`: the comment timed out before committing, was read back as absent
      and retried with the same key, then committed and timed out, and was confirmed.
  - Adding `READBACK_FAIL=1`: 4 of the 6 runs had a write time out, and all 4 ended
    `escalated` with no duplicate. In the 3 where the PR was the write that timed
    out, no comment was posted.

## Part D · Measure

1. The surface price, with its arithmetic: the five definitions together, each alone, and the
   planner's tool list, the starter's and yours; then a mean harness-arm run, each call
   charged for what it carried, and the same calls with all five on every one:

2. The table, from the tracker:

| Measure | Your harness | Control |
|---|---|---|
| Triage comments per run (should be 1) | | |
| Open PRs from `repomind/issue-<n>` per run (1 when the file is not `none`, else 0) | | |
| Writes that timed out and had in fact committed | | |
| Runs that ended `escalated` | | |
| Triage success (your grounded check from Assignment 1) | | |

3. The four shapes of tool misuse: which your surface narrowed, with evidence for
   each; which is still standing; did you see it in your runs, and how would you
   know if you had not?

4. Every prediction in `reports/PREDICTIONS_03.md` that missed, and why:

