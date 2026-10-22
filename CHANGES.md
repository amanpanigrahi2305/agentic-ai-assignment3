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
- **Added:** five namespaced tool definitions with behaviour-describing parameters, two examples each, capped JSON results and strict mode; a boundary validator before every call and a result-shape check after it; repair-fact errors, transient retries in code, and protocol errors routed to the harness log; pulls_open_draft as a plan step before the comment; harness-derived idempotency keys, a check before every write, a verify step with three exits, and a fail-closed pre-call hook; the call record, one line per attempt
- **Removed:** free-text tool descriptions; free-form filters (`filter`, `deep`) in favour of a glob, enums and nullable fields; the file-system existence check in read_file; "No matches." as the answer to a malformed query
- **Constrained:** every write to be safe to retry (same key on every attempt, at most three attempts, escalated when it cannot be confirmed); the plan to end with exactly one PR step and then exactly one comment step; every argument to pass the boundary before a tool runs
