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

The table below shows the state of RepoMind after completing Assignment 3.

| Layer | What does this job | Where it is enforced | Status |
|---|---|---|---|
| Model | The configured model, called only through the metered client | `harness/client.py:120` | present |
| Harness | The loop, with three bounds checked in code; a plan validated before it runs, and an executor that runs only the plan's steps | `agent/loop.py:38`, `ways/planned.py:126`, `ways/planned.py:261` | present |
| Tools | Five namespaced tools managed through a single registry. Each call is validated against the schema, enums, and content rules before execution. Every write uses a harness-generated idempotency key, checks for an existing result before each attempt, and verifies the outcome after a timeout—proceeding, retrying with the same key, or escalating if the result remains uncertain. A fail-closed hook runs before each call, and every attempt is recorded. | `tools/__init__.py:222`, `tools/__init__.py:134`, `tools/__init__.py:319`, `tools/__init__.py:273` | present |
| Memory & State | A run's progress file, outside the model's context | `ways/notes.py:67` | partial: nothing survives a run |
| Control & Policy | The second pass holds back a draft that still fails, so it is never posted. If a write cannot be confirmed, the run stops and escalates the issue for human review. A pre-call hook can also block any tool call. | `ways/planned.py:389`, `tools/__init__.py:397`, `tools/__init__.py:273` | partial: one pre-call hook is implemented, but there is no approval step before a write |
| Observability | Each model call and tool call produces a trace line. The plan is stored as data before execution, and the registry records every tool attempt independently of the agent. | `agent/loop.py:50`, `ways/planned.py:87`, `tools/__init__.py:422` | partial: tracing and call records are implemented, but alerts are not |
| Evaluation | The grounded check reads the tracker, never the agent | `eval/check_triage.py:7` | partial: offline only |

**Empty layers: 0 of 7**, and four are partial.
