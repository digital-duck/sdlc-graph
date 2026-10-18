"""Where workgraph keeps its data. One home folder (default ~/workgraph, override with WORKGRAPH_HOME):

    workspace.yaml   the default workspace: the projects workgraph knows about
    graphs/          layer files (org, product, process; JSON + YAML)
    workgraph.sqlite3  comments, uid history, Claude Code session journal. Lives here, never inside graphs/ or sync/
    sync/            the git working tree for `workgraph sync` (exported text only, no database)
    out/             the built graph.json
    cache/           cached LLM results
    src/             clones of repositories given as URLs (`--src https://...`)
"""
from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("WORKGRAPH_HOME") or "~/workgraph").expanduser()


def default_workspace() -> Path:
    return home() / "workspace.yaml"


def graphs_dir() -> Path:
    return home() / "graphs"


def out_dir() -> Path:
    return home() / "out"


def cache_dir() -> Path:
    return Path(os.environ.get("WORKGRAPH_CACHE") or home() / "cache").expanduser()


def src_dir() -> Path:
    return home() / "src"


def store_path() -> Path:
    """The database. Kept at the top of the home folder, outside every folder that is synced or committed."""
    return home() / "workgraph.sqlite3"


def legacy_store_paths() -> list[Path]:
    return [graphs_dir() / "store.sqlite"]


def sync_dir() -> Path:
    return home() / "sync"
