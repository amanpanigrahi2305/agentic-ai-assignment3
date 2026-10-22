# ARCHITECTURE.md · RepoMind on the seven layers

For each layer: what does this job, and where is it enforced. Cite code as a file
path and line number joined by a colon, inside backticks; a checker opens every
reference (`checks/FORMATS.md`, section 8). A layer held only by a sentence in a
prompt (any file under `prompts/`) is **EMPTY**, and so is a layer nothing does
yet. Status is one of: present, partial (say what is missing), or **EMPTY**. Each
assignment brings this file up to date.

What each layer is for (Lecture 1, slides 42 to 44):

- **Model**: reasoning, choosing tools, generating text.
- **Harness**: runs the loop, builds each prompt, trims old history.
- **Tools**: where the system acts on the world.
- **Memory & State**: what survives a step, a session, a restart.
- **Control & Policy**: what the system may do, and when it must ask.
- **Observability**: a step-by-step record of what it did.
- **Evaluation**: measuring quality, before and after release.

The table below is RepoMind as Assignment 3 leaves it.

| Layer | What does this job | Where it is enforced | Status |
|---|---|---|---|
| Model | The configured model, called only through the metered client | `harness/client.py:120` | present |
| Harness | The loop, with three bounds checked in code; a plan validated before it runs, and an executor that runs only the plan's steps | `agent/loop.py:38`, `ways/planned.py:126`, `ways/planned.py:254` | present |
| Tools | Five namespaced tools behind one registry. Every call passes the boundary check (enums, schema, content rules) before it runs; every write carries a harness-derived idempotency key, is checked before each attempt, and is read back after a timeout (proceed, retry with the same key, or escalate); a fail-closed hook runs first, and every attempt is in the call record | `tools/__init__.py:222`, `tools/__init__.py:134`, `tools/__init__.py:303`, `tools/__init__.py:257` | present |
| Memory & State | A run's progress file, outside the model's context | `ways/notes.py:67` | partial: nothing survives a run |
| Control & Policy | The second pass holds a draft that still fails; a write nobody can confirm ends the run escalated, for a person; a pre-call hook can deny any call | `ways/planned.py:382`, `tools/__init__.py:367`, `tools/__init__.py:257` | partial: one hook, no approval step before a write |
| Observability | One trace line per model call and per tool call, the plan as data before it runs, and a call record of every tool attempt written at the registry, independent of the agent | `agent/loop.py:50`, `ways/planned.py:87`, `tools/__init__.py:392` | partial: traces and records, no alerts |
| Evaluation | The grounded check reads the tracker, never the agent | `eval/check_triage.py:7` | partial: offline only |

**Empty layers: 0 of 7**, and four are partial.
