"""`--src <URL or local path>`: build a graph from a codebase without writing a workspace file first."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import paths
from .domain import Domain, Project, Workspace

URL_RE = re.compile(r"^(https://|ssh://|git://|git@[\w.\-]+:)")


def is_url(src: str) -> bool:
    return bool(URL_RE.match(src))


def project_name(src: str) -> str:
    base = src.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return re.sub(r"\.git$", "", base) or "project"


def fetch(url: str) -> Path:
    """Shallow-clone a repository into <home>/src/<name> (or fast-forward an existing clone). Nothing is executed."""
    dest = paths.src_dir() / project_name(url)
    if (dest / ".git").is_dir():
        r = subprocess.run(["git", "-C", str(dest), "pull", "--ff-only", "--quiet"], capture_output=True, text=True)
        if r.returncode:
            print(f"warning: could not update {dest} ({r.stderr.strip() or 'git pull failed'}); using the existing clone")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "clone", "--depth", "1", "--quiet", url, str(dest)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"git clone {url} failed: {r.stderr.strip()}")
    return dest


def workspace_from_sources(sources: list[str], domain: str = "sdlc") -> Workspace:
    """An in-memory workspace with one project per source (local directory or git URL)."""
    dom = Domain.load(domain)
    projects, seen = [], set()
    for src in sources:
        path = fetch(src) if is_url(src) else Path(src).expanduser().resolve()
        if not path.is_dir():
            raise FileNotFoundError(f"--src {src}: not a directory")
        name = project_name(str(path) if not is_url(src) else src)
        if name in seen:
            raise ValueError(f"--src {src}: two sources are both called {name!r}")
        seen.add(name)
        projects.append(Project(name, path, dom))
    g = paths.graphs_dir()
    return Workspace(projects, g if g.is_dir() else None)
