"""Tracked projects: the default workspace file in the workgraph home folder."""
from __future__ import annotations

import re
from pathlib import Path

from ruamel.yaml import YAML

from . import paths

STARTER = """# workgraph default workspace: the projects it knows about.
# Add projects by hand, with `workgraph track <path>`, or automatically through the Claude Code hooks
# (`workgraph claude-setup`). See docs/GUIDE/USER-GUIDE.md.
domain: sdlc
projects: []
"""


def init_home() -> Path:
    h = paths.home()
    for d in (h, paths.graphs_dir(), paths.out_dir(), paths.cache_dir()):
        d.mkdir(parents=True, exist_ok=True)
    ws = paths.default_workspace()
    if not ws.exists():
        ws.write_text(STARTER, encoding="utf-8")
    return h


def _git_remote(path: Path) -> str | None:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5)
        url = r.stdout.strip() if r.returncode == 0 else ""
        return re.sub(r"^(https?://)[^/@]+@", r"\1", url) or None   # never keep credentials embedded in a URL
    except (OSError, subprocess.SubprocessError):
        return None


def track_project(path: str | Path, name: str | None = None, domain: str | None = None) -> bool:
    """Add a project to the default workspace. Returns False if that path is already tracked.
    Edits the YAML in place, so comments in the file are kept."""
    init_home()
    path = Path(path).expanduser().resolve()
    ws = paths.default_workspace()
    y = YAML()
    y.width = 4096
    doc = y.load(ws.read_text(encoding="utf-8")) or {}
    projects = doc.get("projects")
    if projects is None:
        doc["projects"] = projects = []
    for p in projects:
        if p.get("path") and (ws.parent / str(p["path"])).resolve() == path:
            return False
    base = name or path.name
    repo = _git_remote(path)
    for p in projects:      # a project that arrived through sync and has no folder here yet: bind it to this one
        if not p.get("path") and (p["name"] == base or (repo and p.get("repo") == repo)):
            p["path"] = str(path)
            with ws.open("w", encoding="utf-8") as f:
                y.dump(doc, f)
            return True
    taken = {p["name"] for p in projects}
    unique, i = base, 2
    while unique in taken:
        unique, i = f"{base}-{i}", i + 1
    entry = {"name": unique, "path": str(path)}
    if repo:
        entry["repo"] = repo      # lets another machine clone this project (workgraph sync pull --clone-missing)
    if domain:
        entry["domain"] = domain
    entry_map = y.map()
    entry_map.update(entry)
    entry_map.fa.set_flow_style()
    projects.append(entry_map)
    with ws.open("w", encoding="utf-8") as f:
        y.dump(doc, f)
    return True
