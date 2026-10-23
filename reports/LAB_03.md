# Assignment 3 · A tool surface that tells the truth, and writes that happen once

## Part A: The Five Tool Definitions

### Strict Schemas and Parameter Types

I used `python tools/audit_schemas.py` to check the five tool definitions. The audit counted **5 strict tools, 0 optional parameters, and 2 nullable (union-typed) parameters**: `code_search.path_glob` and `code_read_file.start_line`. Both nullable parameters accept either a value or `null`.

The definitions also meet the portability limits used in the audit: no more than 20 strict tools, no optional parameters, no more than 16 union types, one level of nesting, no validation keywords, and `additionalProperties: false` on every object.

### Strict Mode and Model Compatibility

I used the Mistral `ministral-14b-2512` model, the same model I used in Assignments 1 and 2, through Mistral's OpenAI-compatible API. Mistral supports a `strict` field in function definitions, so I set `"strict": true` for all five tools.

The third call in `make ping` was accepted with all five definitions marked as strict. The result is recorded in `runs/_ping.jsonl`, which confirms that all five definitions were accepted in one request. Based on this result, I set `compiles` to `true`.

Mistral does not document the same grammar limits as Anthropic and Azure. To keep the definitions portable across providers, I used the limits from the schema audit as additional constraints.

### Tool Consolidation and Naming

I decided not to merge any of the five tools because they do not always run together in a fixed order. A search is sometimes followed by a file read, but not always. The two write operations also need to remain separate so each can be tracked and verified independently.

Instead of combining tools, I consolidated functionality within the existing definitions. For example, `source: index | live` replaces the separate `deep` flag, while `start_line` allows the file-reading tool to continue reading a long file without requiring another tool.

I used consistent names for each service:

- `tracker_read_issue` and `tracker_post_comment` for the issue tracker.
- `code_search` and `code_read_file` for the codebase.
- `pulls_open_draft` for pull requests.

Each tool returns JSON containing meaningful identifiers, such as `issue-4192`, `comment-12`, and `pr-7`. The registry sends only `name`, `description`, `input_schema`, and `strict`. The two examples are added to the end of each description through the `render` function in `tools/__init__.py`.

### Improving `search_repo`

I renamed the original `search_repo` tool to `code_search` and made its description and parameters more specific. The original description simply said, "Search the code." The updated version explains what the tool searches, how matching works, when to use it, what the results look like, and how to interpret empty or malformed queries.

| Area | Original version | Updated version |
|---|---|---|
| Name | `search_repo` | `code_search`, using the codebase namespace |
| Description | "Search the code." | Explains searchable directories, matching behavior, usage, result structure, errors, and limits |
| Query | `query`: string | `query`: 1–200 characters of literal text, without operators |
| Path filter | Optional `filter` string | Nullable `path_glob` with examples and root-relative glob rules |
| Result limit | Optional `max_results`, default 10 | Required `max_results` from 1 to 20, with guidance on token cost |
| Search depth | Optional `deep` boolean | `source` enum with `index` and `live` options, including their costs and staleness |
| Response format | Not available | `response_format` enum with `concise` and `detailed` options |
| Optional parameters | 3 out of 4 parameters | No optional parameters; nullable values are used when absence has meaning |
| `additionalProperties` | Not set | Set to `false` |
| Result structure | Text lines in `path:line: text` format, or "No matches." | Structured JSON with query validity, counts, hits, truncation, source, index information, path filter, and notes |
| Examples | None | Typical and advanced examples |
| Strict mode | Not specified | Enabled |

The updated tool searches `src/`, `docs/`, `test/`, and the repository root, but excludes `config/`. It matches literal, case-insensitive substrings in line text rather than searching filenames or interpreting regular expressions.

The `path_glob` parameter accepts a glob relative to the repository root. Setting it to `null` searches the entire searchable codebase, while a glob that matches no files returns a count of zero. The `max_results` parameter is required and accepts values from 1 to 20, while `count` always reports the total number of matches.

I also replaced the `deep` flag with the `source` enum. This makes the difference between indexed and live searches more explicit, including their respective costs and the possibility of stale index results. The `response_format` option provides a concise format by default, while detailed results include lines up to 240 characters and the surrounding lines for additional context.

The result is now structured JSON containing `query`, `query_valid`, `count`, `shown`, `truncated`, `hits`, `source`, `index_built_at`, `index_age`, `path_glob`, and `note`. A valid query with no matches returns `count: 0` and `query_valid: true`. A malformed query is reported as an error instead of being treated as a normal search miss.

Finally, I added two examples to the description. The first demonstrates a typical search for `writeHeader` across the codebase using the index and concise output. The second demonstrates a more advanced search using a quoted error message, a documentation-only path filter such as `{README.md,docs/**}`, live search, and detailed output.

### Validation Boundary

The validation boundary is implemented in `tools/__init__.py`, in `ToolRegistry.validate`. It runs before every tool dispatch and checks both the schema and the tool-specific content rules.

First, enum values are matched case-insensitively, so a value such as `"INDEX"` is normalized to `"index"`. The boundary then validates the arguments against the JSON schema in `harness/schema.py`. After that, each tool's `check()` function applies additional rules that strict mode cannot express, including string lengths, numeric ranges, path globs, the comment's `label:` and `file:` lines, and the pull request's branch.

After a tool executes, its result is checked against the `result_schema` declared in its definition file before it is returned to the model. If a call fails validation, it is rejected before execution and cannot write anything. The resulting error identifies every field that failed validation.

The validation tests confirmed that all 15 malformed calls were rejected, giving a result of 15 out of 15.

### Development Run

I ran the development configuration using `python run.py --way planned --set dev`. The run completed for both issues, and every tool call used a new name. The planner also followed the required order: `pulls_open_draft` was the second-to-last step, followed by `tracker_post_comment` as the final step.

## Part B: Errors

### Error Categories by Tool

I classified errors into three categories: transient, permanent, and ambiguous. Transient errors can be retried because they may be caused by temporary issues. Permanent errors require the model to correct the request rather than retry it unchanged. Ambiguous errors are different because the operation might have succeeded even though the tool returned a timeout.

| Tool | Transient (retried in code) | Permanent (returned to the model with a repair fact) | Ambiguous |
|---|---|---|---|
| `tracker_read_issue` | Reading the issue's file is interrupted (`InterruptedError`, `BlockingIOError`). The code makes up to three attempts. | The issue does not exist, the issue number is not a positive integer, or an argument is unknown or missing. | None, because reading an issue does not change anything. |
| `code_search` | An interruption while accessing the index or reading a file. The code makes up to three attempts. | The query is empty or contains only whitespace, exceeds 200 characters, `path_glob` is invalid (including a comma-separated or space-separated list instead of one glob), `max_results` is outside the range of 1–20, an enum value is not recognized, or an argument still holds the plan's `$fill` placeholder. | None. |
| `code_read_file` | A file read is interrupted or the disk is temporarily busy. The code makes up to three attempts. | The path does not match the repository's file list, is under `config/`, leaves the repository, is a glob, points to a non-text file, or has an out-of-range `start_line`. Paths are case-sensitive, and near matches are suggested when possible. | None. |
| `tracker_post_comment` | A temporary failure of a read the write depends on: the check before each attempt, or the read-back after a timeout (SQLite `OperationalError`, `ConnectionError`). The code makes up to three attempts at that read; if it keeps failing, the harness cannot tell whether the write happened and escalates. The write itself has no transient case, because the tracker reports every failed write as the same `TrackerTimeout`. | The issue does not exist, the body is empty or exceeds 4,000 characters, the required `label:` or `file:` lines are invalid, or a hook blocks the write. | A `TrackerTimeout` after dispatch. The system reads the comment back by its key and then proceeds, retries with the same key, or escalates. If readback also fails with `ReadbackUnavailable`, the system escalates. |
| `pulls_open_draft` | The same as for comments: a failing check or read-back gets up to three attempts in code, then the run escalates. | The issue does not exist, the branch does not follow `repomind/issue-<number>`, the title is empty, exceeds 120 characters, or contains multiple lines, the body is empty or exceeds 4,000 characters, or a hook blocks the write. | A timeout after dispatch. The system reads the pull request back by branch and then proceeds, retries with the same key, or escalates. If readback also fails, the system escalates. |

The important distinction is that the tracker raises the same `TrackerTimeout` whether or not a write has actually committed. Because of this, the registry treats every write timeout as ambiguous rather than transient. It always checks whether the operation succeeded before deciding whether to retry. I explain this recovery process in Part C.

### Protocol Errors: End the Run

Protocol errors occur when the model calls an unknown tool or provides arguments that are not a JSON object. The full error is recorded in `runs/<run_id>.harness.log` and in the call record with the outcome `protocol`. The registry then raises `ProtocolError`, and the harness ends the run with `tool_error` without sending another request to the model.

I chose to end the run because, in planned mode, the plan validator's check 1 already checks whether a tool exists before executing the plan. If an unknown tool reaches the registry anyway, it means the model and the harness disagree about the available interface. This is an issue that needs to be investigated by a person, rather than something the model can fix by changing its arguments.

Returning a generic error to the model would not help because no argument change could make an unknown tool valid. It could instead waste additional turns trying different arguments. Ending the run makes the failure visible in the trace and harness log while preventing execution from drifting into an unintended write operation.

### Execution Errors and Repair Messages

Execution errors are returned with `is_error: true`, and their messages are limited to 700 characters. I put the most useful repair information first, explaining what was wrong and what needs to change. The message then indicates whether retrying is likely to help.

For example, a permanent error tells the model that retrying the same request will not succeed. If the read retries are exhausted, the message recommends trying again after 30 seconds with the same arguments. For an escalated write, the message explicitly says not to retry the call.

This approach gives the model a clear next step instead of returning a vague error that could lead to repeated failed attempts.

### Case-Sensitive File Paths

For `code_read_file`, I match the requested path against the repository's file list rather than relying on the file system. The comparison is case-sensitive, so requesting `src/report.ts` will never open `src/Report.ts`, even on a case-insensitive disk.

When a path is invalid, the tool can suggest near matches. These include the same path with different capitalization, paths that differ by one or two characters, and files with the same name in another directory. For the last case, the comparison ignores differences in capitalization, underscores, and hyphens.

For example, requesting `src/ui/export_button.tsx` can suggest `src/components/ExportButton.tsx`. These suggestions help the model correct a path without silently opening a different file.

The exact error returned when requesting `src/report.ts` is:

```text
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

## Part C: Writes That May Already Have Happened

### Idempotency Key

I implemented the idempotency key in `tools/__init__.py` using the `derive_key` function. The key is generated by hashing the run ID, plan step, and arguments with SHA-256, then taking the first 32 characters of the result.

The exact formula is:

```python
sha256(
    json.dumps(
        {"run_id": run_id, "plan_step": step_id, "args": args},
        sort_keys=True,
        separators=(",", ":")
    )
)[:32]
```

The arguments used to generate the key are the same arguments that passed validation on the first attempt. When a retry is needed, the registry resends those arguments unchanged, so the model does not take part in the retry process. The key is also not included in the tool definition. Instead, the registry passes `idempotency_key` directly to each write tool's `run()` function.

For testing, setting `KEY_MODE=uuid` replaces the derived key with `uuid4().hex` on every attempt.

### Check Before Writing and Verify After a Timeout

Before every write attempt, the registry checks whether the operation has already happened during the current run. For a pull request, it looks for an open PR associated with the branch `repomind/issue-<n>` using `tracker.prs(run_id, branch=...)`. For a comment, it checks for an existing comment with the same key using `tracker.comment_by_key`.

If a `TrackerTimeout` occurs after dispatch, the registry reads back the result and follows one of three paths:

- Found: The operation already succeeded, so the registry continues using the existing record. The result has a status of `already_posted` for a comment or `already_open` for a pull request.
- Not found: The registry retries the operation using the same idempotency key.
- Readback unavailable: If verification fails with `ReadbackUnavailable`, the registry escalates instead of risking a duplicate write.

The registry makes at most three attempts. If the operation is still unconfirmed after the third attempt, it escalates. An escalated result includes `escalate: true`, and the planned execution mode ends the run with the status `escalated`.

Setting `VERIFY=off` disables the readback step and allows blind retries. However, the registry still checks for an existing operation before every attempt and enforces the three-attempt limit.

### Enforcing the Write Order

I enforced the write order in `ways/planned.py`, specifically in the `order_problems` function. Every valid plan must contain exactly one `tracker_post_comment` step as the final step and exactly one `pulls_open_draft` step immediately before it.

If a plan does not follow this order, it is rejected and sent back for replanning. I also included the required ordering in both the planner and reviser prompts so the model is reminded of the constraint when generating or correcting a plan.

### Skipping the Pull Request When the File Is `none`

The executor handles this case when it reaches the pull request step. Before performing either write, it drafts the comment and runs a second validation pass through `draft_comment`.

If the second pass holds the draft, the run ends with an escalated status before either write occurs. If the draft's `file:` field is `none`, the executor records a `skipped` event, marks the pull request step as `skipped`, and does not call `pulls_open_draft`. The comment step then posts the same draft.

If the draft names a file, the model fills in the pull request title and body using the draft as context. The code determines the issue number and branch, and ensures that the first line of the pull request body is `File: <path>`.

I tested the no-file behavior using `harness.scripted:DraftNamesNoFile`. The test produced one comment, no pull request, and a `skipped` event. I also verified this behavior in a real development run, `planned-4192-s01-34a615`, where the draft named `none` and no pull request was opened.

### Example: A Write That Timed Out After Committing

The following example comes from `runs/planned-4191-s03-e0c9c7.calls.jsonl`. It shows a pull request that committed successfully but timed out before the tracker returned a response. The next record confirms that the pull request existed when the registry read it back. The request body has been shortened for readability.

```json
{"run_id":"planned-4191-s03-e0c9c7","t":12.515,"tool":"pulls_open_draft","role":"open_pr","args":{"number":4191,"branch":"repomind/issue-4191","title":"Update README to replace 'npm run serve' with the correct build-and-start command","body":"File: README.md\n\nThe \"Running locally\" section instructs users to run `npm run serve` …"},"plan_step":4,"key":"01486bba53b32cda28b9cea991e4837e","attempt":1,"outcome":"timeout","is_error":true,"result":"the tracker did not answer in time; read back: it had committed","latency_s":0.01,"cost_usd":0.0}
{"run_id":"planned-4191-s03-e0c9c7","t":12.516,"tool":"pulls_open_draft","role":"open_pr","args":{"…":"as above"},"plan_step":4,"key":"01486bba53b32cda28b9cea991e4837e","attempt":1,"outcome":"ok","is_error":false,"result":"confirmed by reading back after the timeout: {\"pr\":\"pr-11\",\"issue\":\"issue-4191\",\"branch\":\"repomind/issue-4191\",\"state\":\"open\",\"draft\":true,\"status\":\"already_open\"}","latency_s":0.012,"cost_usd":0.0}
```

The two records use the same plan step, key, and attempt number. Although the first record reports a timeout, the readback confirms that the pull request was already open. The registry therefore treats the operation as successful instead of creating another pull request.

### Preventing Secrets in Write Operations

I added a `no_secrets_in_writes` hook that runs before every call. Because RepoMind reads private code and can write to public-facing services, the hook checks whether the text being submitted looks like a credential.

A hook denies a call by returning a reason, and a hook that raises an error denies it too. This is a fail-closed approach: when the system cannot establish that a write is safe, it does not allow the operation to proceed. A denied call never executes and is recorded with the outcome `denied`.

### Development Tests

I tested the retry and verification behavior separately from the main evaluation using `FLAKY_WRITES=1` with `--set dev --runs 3`. All six runs finished, and each produced exactly one comment and a pull request only when the comment named a file.

Several runs exercised the timeout-recovery logic:

- Run `planned-4191-s03-e0c9c7`: Both the pull request and the comment committed successfully before timing out. The registry confirmed each operation by reading it back.
- Run `planned-4191-s01-e44c07`: The first comment attempt timed out before committing. The registry read it back, confirmed it was absent, and retried with the same key. The retry then committed but timed out again, and the registry confirmed the comment through readback.

I also tested the more difficult case by enabling `READBACK_FAIL=1`. Four of the six runs experienced a write timeout, and all four ended with `escalated` rather than creating duplicate writes. In the three runs where the pull request was the write that timed out, no comment was posted because the run escalated before reaching the final comment step.

These tests show why a timeout cannot automatically be treated as a failed write. Checking the result before retrying helps prevent duplicates, while escalation provides a safer outcome when the system cannot determine whether an operation succeeded.

## Part D: Measurement and Evaluation

The starter version is commit `b05a857`, GitHub's initial commit from the template. I retrieved the original tool definitions from it with `git show b05a857:tools/<module>.json`. I committed and pushed my predictions on their own in `f557dc7`, before `make reset` and before any evaluation run.

**The measurement was run twice, and only the second counts.** The first measurement, 24 runs on the code in `aaac224`, exposed a bug in my executor's repair step. When the boundary rejected a call, the executor asked the model again for the `$fill` arguments only. The bad value was usually one the planner or reviser had fixed in the plan, so the plan's value won over the model's correction. In all 9 repair cycles the repaired call was the same invalid call again.

I fixed this in commit `477f7fd`, together with three smaller gaps:
- the boundary now rejects an argument that still holds the `$fill` placeholder;
- it rejects a comma-separated list given as a glob;
- the checks before and after a write retry transient read failures in code.

The planner and reviser prompts now also say to fix only the issue number and the branch.

I then ran `make reset`, which archived the first tracker database to `tracker/archive/` and moved the first 24 runs to `runs/discarded/`, and ran both arms again. Both arms of the measurement below ran on the code in `477f7fd`, with no change between them. My predictions in `f557dc7` were pushed before every evaluation run.

### 1. The Cost of the Tool Definitions

I measured the token cost using `eval/price_surface.py --count` with the `ministral-14b-2512` model. For each measurement, I compared the prompt-token count from the same request with and without the content being measured. The difference in `prompt_tokens` was saved in `eval/surface_counts.json`. The fixes in `477f7fd` did not change any definition or the planner's tool list, so these counts hold for both measurements.

For the starter, I counted the definitions exactly as its registry sent them, through the `tools.SENT` fields and without examples, along with the starter's `_tool_list`. For my implementation, I counted the definitions as my registry sends them, including the examples rendered into the descriptions.

| Prompt tokens | Starter | My implementation | Increase |
|---|---:|---:|---:|
| All five tool definitions together | 456 | 3,460 | 7.6× |
| `read_issue` alone | 68 | 375 | |
| `search` alone | 131 | 1,038 | |
| `read_file` alone | 64 | 661 | |
| `comment` alone | 86 | 612 | |
| `open_pr` alone | 119 | 786 | |
| Planner tool list, used in plan and revise messages | 115 | 1,187 | 10.3× |

Each tool measured individually includes Mistral's tool-use preamble. Adding the individual measurements gives 468 tokens for the starter and 3,472 for my implementation, about 12 tokens more than measuring all five together. The difference is the preamble counted five times instead of once.

#### Average Token Cost per Run

I then priced the 12 runs of my harness arm. Each run used an average of **8.00 model calls**. For each `model_call` line, I counted the definitions of the tools that call carried, and for a plan or revise call, the tool list as well. The `submit_plan` tool and the critique's verdict tool are the same in both versions, so I left them out. I used the uncached input rate of $0.20 per million tokens from `pricing.yaml`.

| Call type | Calls per run | Starter tokens per call | My tokens per call | Starter tokens per run | My tokens per run |
|---|---:|---:|---:|---:|---:|
| Fill `code_search` | 3.08 | 131 | 1,038 | 403.9 | 3,200.5 |
| Revise, including tool list | 1.25 | 115 | 1,187 | 143.8 | 1,483.8 |
| Plan, including tool list | 1.00 | 115 | 1,187 | 115.0 | 1,187.0 |
| Fill `tracker_post_comment` (draft) | 1.00 | 86 | 612 | 86.0 | 612.0 |
| Fill `code_read_file` | 0.92 | 64 | 661 | 58.7 | 605.9 |
| Fill `pulls_open_draft` | 0.67 | 119 | 786 | 79.3 | 524.0 |
| Fill `tracker_read_issue` (a repair) | 0.08 | 68 | 375 | 5.7 | 31.2 |
| **Total** | **8.00** | | | **892.3 tokens = $0.000178** | **7,644.4 tokens = $0.001529** |
| If every call carried all five of my definitions | 8.00 | | 3,460 | | **27,680.0 tokens = $0.005536** |

For example, the search row is 3.08 × 1,038 = 3,200.5 tokens per run. The cost of my implementation is:

`7,644.4 × $0.20 / 1,000,000 = $0.001529 per run`

Search fill calls average 3.08 per run, against 1.42 in the first measurement. The reason is that a rejected search is now repaired by a second fill call, and the reviser ran 1.25 times per run.

#### What the Additional Tokens Provide

My rewritten definitions cost about 8.6 times as much per run as the starter's: 6,752 more tokens, about $0.0014 per run.

Those tokens give the fill model information the starter left it to guess:
- what the search matches (literal, case-insensitive text in lines, not file names), and that the codebase is TypeScript;
- what an empty result means, and the caps;
- that file paths are case-sensitive;
- the branch format and the `label:` and `file:` lines;
- two worked examples per tool.

The evidence is that the fill model produced **none** of the 15 values the boundary rejected (section 3). It wrote 0 invalid globs, 0 invented fields and 0 placeholders; the placeholders in comment drafts were the planner's template, not a fill (section 3). This is an inference from where the bad values came from, not a controlled comparison: I did not run the starter's definitions as a baseline.

The extra tokens did not reach the model that needed them most, though. All 15 bad values came from the planner or reviser, which sees only the tool list. `_tool_list` in `agent/plan.py` gives each argument's name and type, but not its description. So the planner never read "there are no .py or .js files" or "no spaces", and it wrote `**/*.py **/*.js **/*.java **/*.go`. The definitions are only as useful as the calls that carry them.

#### Savings from Sending Only the Relevant Tool

The planned way sends each fill call only its step's tool. If every call carried all five of my definitions, as in the Assignment 1 agent loop, a run would read 27,680 tokens of definitions instead of 7,644. That saves about 20,036 tokens and $0.0040 per run, **72%**.

A plan or revise call carries a 1,187-token text list instead of 3,460 tokens of schemas. Caching could cut the price of sending the definitions again, but not the room they take in the model's context during an eight-call run.

### 2. Write Reliability: Results from the Tracker

I collected the results from `tracker/tracker.db`, by `run_id`, with `python eval/count_writes.py --write`, which also filled the `writes` section of `reports/LAB_03.json`. There were 12 finished runs in each arm.

| Measurement | My harness | Control |
|---|---|---|
| Triage comments per run, where the expected count is 1 | Exactly 1 in every run | Maximum of 3; 6 of 12 runs had more than 1, producing 10 extra comments |
| Open PRs from `repomind/issue-<n>`, where the expected count is 1 if the file is not `none`, otherwise 0 | Correct in all 12 runs | Correct in all 12 runs |
| Writes that timed out but had actually committed (`attempts`, `timeout_after_commit`) | 9 | 15 |
| Runs that ended `escalated` | 0 of 12 | 1 of 12 |
| Successful triage according to the grounded check | 4 of 12 | 1 of 12 |

All 24 writes that committed before timing out have a call-record line with the same key and the outcome `timeout`. This is checked by `eval/count_writes.py` and by the checker's Auditable trace.

The two arms differed only in `KEY_MODE` and `VERIFY`.
- **My harness** read back all 9 committed timeouts, found the existing writes, and duplicated none.
- **The control** retried every timeout blindly with a fresh key. Before each comment attempt it searched for a key the tracker had never seen, so it could not find its earlier comment and posted again. That produced 10 extra comments. Four runs received three comments each: 4201 sweep 3, 4202 sweep 4, 4203 sweep 4 and 4204 sweep 4.
- **Pull requests** stayed correct in every control run, because the check before `open_pr` searches by branch. The branch identifies the pull request whatever key is sent.
- **The control's one escalation** (4202 sweep 4) came after its comment was still unconfirmed after three blind attempts, by which time the tracker already held three copies. Escalating after the damage does not undo it.

**The triage gap comes from the writes, not the model.** Three control runs, 4201 sweeps 3 and 4 and 4205 sweep 4, posted the correct label and file. They fail the grounded check only because they posted it two or three times, and the check requires exactly one comment. Without the duplicates, the control would have passed 4 of 12, the same as my harness. Triage itself is still weak in both arms; the remaining misses are wrong files, discussed below.

### 3. The Four Patterns of Tool Misuse

#### Right Tool, Wrong Arguments

The boundary narrowed this pattern, but it is still the most common one.

It rejected **15 calls** before they ran, 11 in my arm and 4 in the control. Every one of the 15 bad values came from the planner or reviser, not the fill model:
- **Lists given as a glob:** space-separated `**/*.py **/*.js **/*.java **/*.go`, one separated by newlines (4204 sweep 1), and a comma list `**/*.py,**/*.js,…` (4201 sweep 4). The comma list was the one the first measurement let through as a valid query that matched nothing.
- **Invented fields:** `focus` on `tracker_read_issue` (4205 sweep 1) and `exclude_glob` on `code_search` (4205 sweep 2).
- **A query with the `$fill` placeholder** left in it: `language:python AND not documentation AND … $fill` (4205 sweep 4).

After the fix, **all 15 repaired calls ran successfully**, against 0 of 9 in the first measurement. The comment draft caught a related slip in 11 of 12 of my runs. The planner wrote the comment's body as a template, `"label: $fill\nfile: $fill\n$fill"`, instead of the single `$fill` the executor treats as an open argument. So the executor did not ask the model to fill it, and the check before either write found the placeholder and had the model write the body once. This rose from 1 run in 12 in the first measurement to 11 in 12, most likely because the new prompt told the planner to write `$fill` for every argument it does not know.

The prompt fix did not prevent any of this. The planner and reviser were told to write `$fill` for every argument except the issue number and branch, and the revisions still wrote globs. Only the code-level repair corrected them.

Some wrong arguments were well-formed, so the boundary could not catch them:
- `**/*.py` ran as a valid glob in a TypeScript repository and matched nothing (4204 sweep 1 in my arm, 4204 sweep 3 in the control).
- The search text `csv.ts merges rows` was a file name, which the description says the search never matches (both 4206 runs in my arm).
- `code_read_file` was given a directory, `src/export/` or `src/notifications/`, four times.

#### Wrong Tool, Valid Call

This pattern was removed by design for the fill model. Each fill call offered exactly one tool, its step's: all 121 execute `model_call` lines across the 24 runs carry one tool in `tools`. There were 0 `off_plan` events, so the model never called a tool other than the one it was offered. The namespaced names also keep the two codebase tools apart from the tracker's.

It is not removed for the planner and reviser, which choose each step's tool. In `planned-4205-s01-28aeb7`, the reviser added a `tracker_read_issue` step to "check if the issue is related to documentation or translations", expecting true or false. No tool does that, and the step's invented `focus` field was rejected at the boundary. So this pattern moved from the call to the plan, where the plan's four checks catch a tool that does not exist, but not a real tool used for the wrong purpose.

#### Correct Call, Misread Result

The search result now distinguishes a valid empty result (`count: 0`, `query_valid: true`) from a malformed query, which is an error. Of the 46 successful searches, 23 returned no matches, and no run treated an empty result as an answer.

The executor's checkpoint does not read `count` itself. It tests whether any returned path is a TypeScript file under `src/` (the `SOURCE` pattern in `ways/planned.py`, from Assignment 2). That check failed and triggered a revision 30 times: 18 after an empty search and 12 after hits that were only documentation or translation files. 18 of the 30 revisions were followed by another search.

The model still misread results that were valid and well-formed:
- **Issue 4206:** the issue's own title names `src/export/csv.ts`. In all four runs the model read it and blamed it, though the bug is elsewhere. In three runs it searched for the file's name first; in 4206 sweep 3 it searched only for "merge", found nothing, and read `csv.ts` anyway.
- **Issue 4203:** the hit for "Malformed row" is the translation file `src/i18n/en.json`. All four runs named the translation, not the code that raises the message.

Every call in these runs was valid, and nothing at the boundary flagged them. Only the answer key did.

#### No Tool Call at All

This pattern is still standing.

All 121 fill calls returned a tool call this time, but one direct case remains. In control run `planned-4206-s04-f1bc1a`, the reviser explained in prose why the search had failed ("The issue is that the `code_search` step (step 2) failed …") and ended its turn without calling `submit_plan`. The harness recorded this as a schema failure ("the plan has 0 tracker_post_comment steps"), not as a missing call, so even the direct case is filed under the wrong name. In the first measurement, a fill for `code_read_file` in `planned-4204-s02-f7139a` (now in `runs/discarded/`) answered "I cannot proceed to `code_read_file` …" instead, and the executor's second try made the call.

The indirect form was common, and it is the one that matters. Take `planned-4204-s01-7563a2` in my arm:
- searches for "summary email" and "email" returned nothing;
- a later "email" search found hits;
- the read step was then given the directory `src/notifications/`, which failed;
- the model never made the read that would have found the date and time-zone code, and posted `file: none`.

The same thing happened in four runs across the arms. Each failed to read a directory, made no second read, and posted `file: none`. Three of them ended `completed`, with well-formed comments and the right number of PRs. The fourth, 4202 sweep 4 in the control, escalated, but only over its comment write, not over the missing read. Nothing in the trace, the call record or the tracker looks like a failure, because the call that mattered was never made.

That is why this pattern stands. A call that never happens leaves no line to inspect. The only way I knew was the grounded check against `fixtures/answers.yaml`, which failed 8 of my 12 runs. Without an answer key, a run that gives up early looks the same as one that finished. Detecting it needs a set of tasks with known answers, which Assignment 7 builds. It is the pattern slide 53 names as the largest single share of failed tasks (36%, MCP-Atlas).

### 4. Predictions That Did Not Fully Match the Results

I recorded my predictions in `reports/PREDICTIONS_03.md`, committed and pushed alone as `f557dc7` before any evaluation run. The commit's time is 17:34:43 CDT. The "3:36" written in the file's first line is a typo for the time I wrote it, and the commit time is the one that counts.

| Prediction | Actual result | Outcome |
|---|---|---|
| My harness would produce zero duplicate comments | Zero | Correct |
| My harness would produce zero duplicate pull requests | Zero | Correct |
| The control would produce about nine extra comments across about six runs | 10 extra comments across 6 runs | Correct |
| The control would produce zero duplicate pull requests | Zero | Correct |
| `code_search` would be the most misused tool, by wrong arguments | 14 of the 15 boundary rejections, and 23 empty results out of 46, against 5 `code_read_file` errors and 1 `tracker_read_issue` rejection | Correct |

#### Where the 54% and the Nine Come From

Under `FLAKY_WRITES`, each write attempt commits and then times out with probability 0.5, times out before committing with probability 0.1, and goes through with probability 0.4 (`harness/tracker.py:54`). The control keeps retrying while attempts time out, up to three attempts, and every attempt that commits adds a comment.

A run gets two or more comments in two ways:
- its first attempt commits and times out (0.5), and a later attempt commits (1 − 0.1 × 0.1 = 0.99): 0.495;
- its first attempt times out before committing (0.1), the second commits and times out (0.5), and the third commits (0.9): 0.045.

That gives 0.495 + 0.045 = **0.54 per run**, or 6.5 runs out of 12. The expected number of comments per run is 0.9 × (1 + 0.6 + 0.36) = 1.764, or 0.764 extra, so about 9.2 extra comments over 12 runs. The measured 10 extra across 6 runs matches both.

The first, discarded measurement gave 10 extra across 8 runs. With a standard deviation of about 1.7 runs at 12 runs, 6 and 8 are both within the expected range.

#### What I Did Not Predict: the Escalation

I did not predict that the control would escalate at all. A control that never reads back can never confirm a write. A comment whose three blind attempts all time out, each with a 60% chance, ends the run `escalated`, even though the tracker may already hold three copies. That is what happened in 4202 sweep 4.

My harness escalated 0 times, because it read back each timed-out write and found it. In the first measurement the control escalated 4 times, so the count varies, but the cause is the same.

## AI assistance

I used Claude Code to help implement the code and develop the evaluation scripts in `eval/` for Part D. After drafting the report, I also used Claude Code to check grammar, verify the data, and improve the report's organization. I wrote `reports/PREDICTIONS_03.md` myself before running the evaluation. Finally, I tested the implementation using `make test`, `make ping`, and `make check-03`, which passed all checks, including all 82 automated points. I also cross-checked the reported numbers against the tracker's database to make sure they were accurate.
