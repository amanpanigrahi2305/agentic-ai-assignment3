"""code_search (module search_repo): literal, case-insensitive text search over the private codebase.

Its definition is search_repo.json. It reads a prebuilt index,
fixtures/search_index/index.json, of every text file under fixtures/repo except
config/, and the index records when it was built. With source "live", it reads the
files themselves instead. Results come back as JSON, hits in path order, with
count (every match), shown, truncated, index_built_at and index_age: what the
starter's last line reported, as fields.

**Malformed**, for Assignment 3 (Part A, task 1), is any of three things:
  1. a query that is empty or only whitespace;
  2. a query longer than MAX_QUERY_CHARS;
  3. a filter that is not a valid glob (glob_problem, below).
The starter answered a malformed query exactly as it answered one that matched
nothing, "No matches.". Now the two differ in the raw response: a search that
matches nothing is a success, JSON with count 0 and query_valid true; a malformed
one raises ToolError, which the registry returns as is_error with a repair fact.
check() holds the same rules for the registry's boundary check, before dispatch.
"""

from __future__ import annotations

import fnmatch
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

EXCLUDED_DIRS = {"config", "node_modules", ".git"}
HARD_CAP = 20
MAX_QUERY_CHARS = 200
INDEX = "fixtures/search_index/index.json"
GLOB_CHARS = re.compile(r"^[A-Za-z0-9_.\-/*?\[\]{},]+$")


def glob_problem(pattern: str) -> str | None:
    """Why `pattern` is not a valid path glob, or None when it is one."""
    if pattern.startswith("/"):
        return "a glob is relative to the repository root; it cannot start with /"
    if ".." in pattern.split("/"):
        return "a glob cannot leave the repository (..)"
    if not GLOB_CHARS.match(pattern):
        return "a glob uses letters, digits, / . _ - and the wildcards * ? [ ] { }; no spaces, colons or quotes"
    depth = 0
    for ch in pattern:
        depth += {"[": 1, "]": -1}.get(ch, 0)
        if depth not in (0, 1):
            return "a glob's [ and ] must pair up"
    if depth:
        return "a glob's [ and ] must pair up"
    if pattern.count("{") != pattern.count("}"):
        return "a glob's { and } must pair up"
    return None


def problem(query, filter=None) -> str | None:
    """Why this search is malformed, or None when it is well-formed."""
    text = str(query or "")
    if not text.strip():
        return ("query is empty or only whitespace: give 1 to 200 characters of literal text to find, "
                "such as an identifier or an error message")
    if len(text) > MAX_QUERY_CHARS:
        return (f"query is {len(text):,} characters long; the cap is {MAX_QUERY_CHARS}: search for one identifier "
                "or one short phrase from it, not a whole passage")
    if filter not in (None, ""):
        why = glob_problem(str(filter))
        if why:
            return f"path_glob {str(filter)[:80]!r} is not a valid glob: {why}. Use null to search everything"
    return None


def check(args: dict) -> list[str]:
    """The content rules the schema cannot carry (lengths, ranges, globs), for the boundary."""
    out = [why for why in (problem(args.get("query")), problem("x", args.get("path_glob")) if args.get("path_glob")
                           else None) if why]       # each field on its own, so every one at fault is named
    n = args.get("max_results")
    if isinstance(n, int) and not 1 <= n <= HARD_CAP:
        out.append(f"max_results is {n}; it must be 1 to {HARD_CAP} (10 for a first search)")
    return out


def _expand(pattern: str) -> list[str]:
    """{a,b} alternatives, which fnmatch does not know."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    return [p for option in match.group(1).split(",")
            for p in _expand(pattern[:match.start()] + option + pattern[match.end():])]


def _matches(path: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    return any(fnmatch.fnmatchcase(path, p) or fnmatch.fnmatchcase(path, p.replace("**/", ""))
               for p in _expand(pattern))


def load_index(root: Path) -> dict:
    return json.loads((Path(root) / INDEX).read_text())


def _disk(repo_root: Path) -> dict[str, list[str]]:
    files = {}
    for path in sorted(Path(repo_root).rglob("*")):
        rel = path.relative_to(repo_root)
        if path.is_file() and not (set(rel.parts) & EXCLUDED_DIRS):
            try:
                files[rel.as_posix()] = path.read_text().splitlines()
            except UnicodeDecodeError:
                continue
    return files


def age(built_at: str, now: float | None = None) -> str:
    built = datetime.strptime(built_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    days = int(((now or time.time()) - built.timestamp()) // 86400)
    return "today" if days < 1 else f"{days} day{'s' if days != 1 else ''} ago"


CONCISE_CHARS, DETAILED_CHARS, CONTEXT_CHARS = 100, 240, 160


def run(ctx, query, path_glob, max_results, source, response_format):
    from tools import ToolError

    why = problem(query, path_glob)
    if why is not None:
        raise ToolError(f"Malformed search: {why}. This query will fail however often it is retried; "
                        "fix it and call again.")
    repo_root = Path(ctx.repo_root)
    if source == "live":
        files, built, age_text = _disk(repo_root), None, None
    else:
        index = load_index(repo_root.parent.parent)
        files, built, age_text = index["files"], index["built_at"], age(index["built_at"])
    needle = str(query).strip().lower()
    limit = max(1, min(int(max_results), HARD_CAP))
    detailed = response_format == "detailed"
    matches = [(rel, number, lines) for rel, lines in sorted(files.items()) if _matches(rel, path_glob)
               for number, line in enumerate(lines, 1) if needle in line.lower()]
    hits = []
    for rel, number, lines in matches[:limit]:
        hit = {"path": rel, "line": number,
               "text": lines[number - 1].strip()[:DETAILED_CHARS if detailed else CONCISE_CHARS]}
        if detailed:
            hit["before"] = lines[number - 2].strip()[:CONTEXT_CHARS] if number > 1 else None
            hit["after"] = lines[number].strip()[:CONTEXT_CHARS] if number < len(lines) else None
        hits.append(hit)
    where = f"index built {built}, {age_text}" if built else "read from disk"
    if not matches:
        note = (f"{where}; no line matches {needle!r}"
                + (f" in files matching {path_glob!r}" if path_glob else "")
                + ": the query is valid; try a shorter or different term")
    else:
        note = f"{where}; {len(matches)} matches, {len(hits)} shown"
        if len(matches) > len(hits):
            note += "; truncated: narrow the query or set path_glob to see the rest"
    return json.dumps({"query": str(query), "query_valid": True, "count": len(matches), "shown": len(hits),
                       "truncated": len(matches) > len(hits), "hits": hits,
                       "source": "live" if source == "live" else "index", "index_built_at": built,
                       "index_age": age_text, "path_glob": path_glob, "note": note})
