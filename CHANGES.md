# CHANGES.md

Three lines per assignment: what it added, what it removed, what it constrained.
Removals are logged like additions.

## Assignment 1
- **Added:** a bounded tool loop; four ways to triage an issue (a direct call, a workflow, an agent, and a hybrid of the two); a grounded success check that reads the tracker; a results script
- **Removed:** nothing
- **Constrained:** every agent run to three bounds checked in code (steps, tokens, wall clock); success to what the tracker holds for the run's ID

## Assignment 2
- **Added:** a plan written as data before the issue is read; a validator with four checks; an executor that runs only the plan's steps; one checkpoint that may revise the plan; a second pass before the comment; detectors and a diff over traces; a progress file and a compaction
- **Removed:** the model's choice of the next tool, in the planned way
- **Constrained:** every tool call to a step of a validated plan; revisions to steps not yet done; the comment to drafts that pass the second pass

## Assignment 3

- **Added:** Five namespaced tool definitions with parameters that describe their behavior, along with two examples for each tool, capped JSON results, and strict mode. Added a boundary validator before every tool call and a result-shape check afterward. Repair-fact errors, transient retries in code, and protocol errors are handled through the harness log. Also added `pulls_open_draft` as a planning step before commenting, harness-generated idempotency keys, a check before every write, a verification step with three possible outcomes, a fail-closed pre-call hook, and a call record with one line per attempt.

- **Removed:** Free-text tool descriptions and free-form filters (`filter`, `deep`), replacing them with a glob, enums, and nullable fields. Removed the file-system existence check from `read_file` and stopped using `"No matches."` as the response to malformed queries.

- **Constrained:** Every write can be safely retried using the same idempotency key, with a maximum of three attempts. If the result still cannot be confirmed, the operation is escalated. The plan must end with exactly one PR step followed by exactly one comment step, and every argument must pass boundary validation before a tool runs.
