"""code_read_file (module read_file): return one file of the private codebase, or a part of it.

Its definition is read_file.json. It reads the same files code_search searches:
everything under fixtures/repo except config/. A path is relative to
fixtures/repo, as search results print it.

It never asks the file system whether a path exists. On a case-insensitive disk,
which is macOS's default, opening src/report.ts would silently open src/Report.ts
and report success (Assignment 3, Part B, task 3). Instead it compares the
requested path, case included, with the repository's own file list, and a path
that is not on it fails with the near misses that are.
"""

from __future__ import annotations

from pathlib import Path

MAX_CHARS = 20_000                          # either format, after the line cap
LINES = {"concise": 150, "detailed": 400}
MAX_PATH_CHARS = 300
NEAR_MISSES = 5


def files(repo_root) -> list[str]:
    """Every readable file, as a path relative to the repository root: the list a path must be on."""
    from tools.search_repo import EXCLUDED_DIRS
    root = Path(repo_root)
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and not (set(p.relative_to(root).parts) & EXCLUDED_DIRS))


def _distance(a: str, b: str, cap: int = 3) -> int:
    """Levenshtein distance, stopped early once it exceeds `cap`."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


def _squash(name: str) -> str:
    """A file name with case, '_' and '-' ignored: export_button.tsx and ExportButton.tsx agree."""
    return name.lower().replace("_", "").replace("-", "")


def near_misses(rel: str, known: list[str]) -> list[str]:
    """Existing paths a model most likely meant: the same path in another case, one or two
    characters away, or the same file name in another directory. Closest first."""
    scored = []
    for path in known:
        if path.lower() == rel.lower():
            score = 0
        elif (d := _distance(rel, path, 2)) <= 2:
            score = d
        elif _squash(path.rsplit("/", 1)[-1]) == _squash(rel.rsplit("/", 1)[-1]):
            score = 3                              # the same file name elsewhere, case and separators aside
        else:
            continue
        scored.append((score, path))
    return [p for _, p in sorted(scored)][:NEAR_MISSES]


def check(args: dict) -> list[str]:
    """The content rules the schema cannot carry, for the registry's boundary check."""
    out = []
    path = str(args.get("path") or "")
    if not path.strip():
        out.append("path is empty: give a path relative to the repository root, as a code_search hit prints it")
    elif len(path) > MAX_PATH_CHARS:
        out.append(f"path is {len(path)} characters; the cap is {MAX_PATH_CHARS}")
    elif path.startswith("/") or ".." in path.replace("\\", "/").split("/"):
        out.append(f"path {path[:80]!r} leaves the repository: give a path relative to its root, such as "
                   "'src/api/errors.ts', with no leading / and no ..")
    elif any(c in path for c in "*?[{"):
        out.append(f"path {path[:80]!r} is a glob: code_read_file reads one exact path; find it with code_search first")
    start = args.get("start_line")
    if isinstance(start, int) and start < 1:
        out.append(f"start_line is {start}; lines count from 1, or use null to read from the top")
    return out


def run(ctx, path, start_line, response_format):
    from harness.formats import normalize_path
    from tools import ToolError
    from tools.search_repo import EXCLUDED_DIRS

    rel = normalize_path(str(path))
    if rel in (None, "none"):
        raise ToolError("path is empty: give a path relative to the repository root, as a code_search hit prints it.")
    root = Path(ctx.repo_root).resolve()
    if rel.startswith("/") or ".." in rel.split("/"):
        raise ToolError(f"{path!r} is outside the repository: give a path relative to its root, such as "
                        "'src/api/errors.ts'. This path will not succeed however often it is retried.")
    if set(rel.split("/")) & EXCLUDED_DIRS:
        raise ToolError(f"{rel} is under a directory RepoMind may never read ({', '.join(sorted(EXCLUDED_DIRS))}): "
                        "it holds configuration and secrets. Read a file under src/, docs/ or test/ instead. "
                        "This will not succeed with any arguments for that path.")
    known = files(root)
    if rel not in known:
        close = near_misses(rel, known)
        hint = (f" Did you mean: {', '.join(close)}?" if close
                else " No file has a similar name: search with code_search and copy a hit's path.")
        raise ToolError(f"File not found: {rel} is not a file in the repository (paths are case-sensitive and "
                        f"must match exactly).{hint} Retrying {rel} will not succeed.")
    try:
        text = (root / rel).read_text()
    except UnicodeDecodeError:
        raise ToolError(f"{rel} is not a text file, so it cannot be read. Choose a source or documentation file.")
    lines = text.splitlines()
    first = max(1, int(start_line or 1))
    if lines and first > len(lines):
        raise ToolError(f"start_line is {first}, but {rel} has {len(lines)} lines: use null, or a line from 1 to "
                        f"{len(lines)}.")
    cap = LINES.get(response_format, LINES["concise"])
    chunk = lines[first - 1:first - 1 + cap]
    numbered = response_format == "detailed"
    body = "\n".join(f"{first + i:>5}| {line}" if numbered else line for i, line in enumerate(chunk))
    cut_chars = len(body) > MAX_CHARS
    if cut_chars:
        body = body[:MAX_CHARS]
        chunk = body.splitlines()[:-1] or chunk[:1]   # whole lines only, so next_start_line is exact
        body = "\n".join(chunk)
    end = first + len(chunk) - 1 if chunk else 0
    truncated = bool(lines) and end < len(lines)
    import json
    return json.dumps({"path": rel, "start_line": first if lines else 0, "end_line": end,
                       "total_lines": len(lines), "truncated": truncated,
                       "next_start_line": end + 1 if truncated else None, "content": body})
