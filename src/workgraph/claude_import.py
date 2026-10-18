"""Existing Claude Code work -> workgraph.

Two parts, both read-only on ~/.claude:
  * the `claude_memory` processor: a project's Claude Code memory files become `memory` nodes on every build
  * `import_claude`: track the projects Claude Code has worked in and load session metadata into the journal
    (ids, times, branch, files touched, Claude's own session title). Message text is never stored.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from .domain import Processor, Project, make_id

NESTED_OK_MARKER = ".git"
TEMP_PREFIXES = ("/tmp/", "/var/tmp/")        # scratch work, not projects
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def claude_dir() -> Path:
    return Path(os.environ.get("WORKGRAPH_CLAUDE_DIR") or "~/.claude").expanduser()


def slug(path: str | Path) -> str:
    """How Claude Code names a project's folder under ~/.claude/projects: every non-alphanumeric becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def memory_dir(project_path: Path) -> Path:
    return claude_dir() / "projects" / slug(project_path) / "memory"


def parse_memory(text: str) -> dict:
    fm: dict = {}
    if text.startswith("---"):
        parts = text.split("\n---", 1)
        if len(parts) == 2:
            try:
                loaded = yaml.safe_load(parts[0][3:])
                fm = loaded if isinstance(loaded, dict) else {}
            except yaml.YAMLError:
                fm = {}
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    return {"name": fm.get("name"), "description": fm.get("description"), "type": meta.get("type") or fm.get("type")}


def p_claude_memory(project: Project, files: list[Path], proc: Processor) -> dict:
    """A project's Claude Code memory files -> one `memory` node each (untagged: memory is not a lifecycle stage)."""
    out = {"nodes": [], "edges": [], "hyperedges": [], "refs": [], "warnings": []}
    md = memory_dir(project.path)
    if not md.is_dir():
        return out
    for f in sorted(md.glob("*.md")):
        if f.name == "MEMORY.md":          # the index; its entries are the files themselves
            continue
        try:
            fm = parse_memory(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        name = str(fm["name"] or f.stem)
        out["nodes"].append({"id": "memory_" + make_id(f.stem), "label": f"memory: {name}", "file_type": "document",
                             "source_file": str(f), "kind": "memory", "memory_type": fm["type"] or "",
                             "summary": str(fm["description"] or ""), "stages": []})
    return out


# --- finding the projects Claude Code has worked in

def decode_slug(name: str) -> Path | None:
    """Recover a folder from its slug by matching dash-joined entry names against the filesystem."""
    tokens = [t for t in name.split("-") if t != ""]

    def walk(base: Path, rest: list[str]) -> Path | None:
        if not rest:
            return base
        try:
            entries = [e.name for e in os.scandir(base) if e.is_dir(follow_symlinks=False)]
        except OSError:
            return None
        for k in range(len(rest), 0, -1):       # prefer the longest name: folders such as `digital-duck`
            want = "-".join(rest[:k])
            for e in entries:
                if slug(e).strip("-") == want:
                    found = walk(base / e, rest[k:])
                    if found:
                        return found
        return None

    return walk(Path("/"), tokens)


def _first_cwd(files: list[Path]) -> str | None:
    for f in files:
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i > 400:
                        break
                    try:
                        cwd = json.loads(line).get("cwd")
                    except (ValueError, AttributeError):
                        continue
                    if cwd:
                        return cwd
        except OSError:
            continue
    return None


@dataclass
class Found:
    slug: str
    path: Path | None
    memory_files: int
    sessions: list[Path] = field(default_factory=list)
    action: str = ""


def scan(projects_dir: Path | None = None) -> list[Found]:
    root = projects_dir or claude_dir() / "projects"
    found = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        sessions = sorted(d.glob("*.jsonl"))
        mem = len([m for m in (d / "memory").glob("*.md") if m.name != "MEMORY.md"]) if (d / "memory").is_dir() else 0
        path = None
        cwd = _first_cwd(sessions)
        if cwd and slug(cwd) == d.name and Path(cwd).is_dir():
            path = Path(cwd)
        else:
            guess = decode_slug(d.name)
            if guess and slug(guess) == d.name:
                path = guess
        found.append(Found(d.name, path, mem, sessions))
    return found


def plan(found: list[Found], tracked: set[Path], include_nested: bool = False) -> list[Found]:
    """Decide what to do with each folder: track, already tracked, skip (missing, temporary, nested)."""
    paths = {f.path for f in found if f.path}
    home = Path.home().resolve()
    for f in found:
        p = f.path
        if p is None:
            f.action = "skip: folder no longer exists"
        elif p.resolve() in tracked:
            f.action = "already tracked"
        elif p.resolve() == home or str(p).startswith(TEMP_PREFIXES):
            f.action = "skip: home or temporary folder"
        elif not include_nested and any(o != p and o in p.parents for o in paths) and not (p / NESTED_OK_MARKER).exists():
            f.action = "skip: inside another project (use --include-nested)"
        else:
            f.action = "track"
    return found


# --- session metadata

def _ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def read_session(path: Path, project_root: Path, with_titles: bool = False) -> dict | None:
    """Metadata of one transcript: id, times, branch, files touched, title. Message text is not kept."""
    first = last = None
    branch = title = ""
    touched: dict[str, None] = {}
    sid = path.stem
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                t = _ts(r.get("timestamp"))
                if t:
                    first = t if first is None else min(first, t)
                    last = t if last is None else max(last, t)
                if r.get("gitBranch"):
                    branch = r["gitBranch"]
                if r.get("type") == "ai-title" and r.get("aiTitle"):
                    title = str(r["aiTitle"])[:120]
                msg = r.get("message")
                content = msg.get("content") if isinstance(msg, dict) else None
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in EDIT_TOOLS:
                        fp = (block.get("input") or {}).get("file_path") or (block.get("input") or {}).get("notebook_path")
                        if fp:
                            try:
                                touched[str(Path(fp).resolve().relative_to(project_root.resolve()))] = None
                            except ValueError:
                                touched[str(fp)] = None
    except OSError:
        return None
    if first is None:
        return None
    return {"session_id": sid, "started": first, "ended": last or first, "branch": branch,
            "changed": list(touched)[:50], "title": title if with_titles else ""}


def import_claude(store, found: list[Found], *, with_titles: bool = False, track=None) -> dict:
    """Apply the plan: track folders, import session metadata for every folder that has a known path."""
    tracked = imported = 0
    for f in found:
        if f.action == "track" and track and f.path:
            tracked += 1 if track(f.path) else 0
        if f.path and not f.action.startswith("skip: folder"):
            for s in f.sessions:
                meta = read_session(s, f.path, with_titles)
                if meta:
                    store.session_import(meta["session_id"], f.path.name, str(f.path), meta["branch"], meta["started"],
                                         meta["ended"], meta["changed"], meta["title"])
                    imported += 1
    return {"tracked": tracked, "sessions": imported}
