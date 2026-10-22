# Predictions, Assignment 3 Part D

Written October 22, 2026, at 3:36 CDT, before running any evaluations with `FLAKY_WRITES`.

## My Harness

- **Duplicate comments: 0.** If a comment commits but the request times out, the harness reads it back using its derived key, so it won't post the same comment again.
- **Duplicate PRs: 0.** Before each attempt, the harness checks for an existing PR on the `repomind/issue-<n>` branch. After a timeout, it reads back by branch, and a retry resends the same key, which the tracker refuses to write twice.

## Control

- **Duplicate comments: approximately 9 extra comments across about 6 runs.** Since each attempt uses a fresh random key, the control cannot find an existing comment, so the 6 initial attempts that commit and then time out are retried, producing about 6 × 0.9 = 5.4 extra comments, while third attempts add another 4.2 × 0.9 ≈ 3.8, for approximately 9 extra comments in total.
- **Duplicate PRs: 0.** The control checks for an existing PR on the branch before each attempt, so a PR created before a timeout should be found on the next check and won't be created again.

## Most Misused Tool

- **`code_search` — using the right tool with the wrong arguments.** During development, 9 of its 36 calls were rejected because of invalid glob syntax, and 21 of the 27 calls that ran returned no results, suggesting that the model often struggles to construct valid search patterns or queries that match relevant code.