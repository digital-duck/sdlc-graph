"""Claude Code integration: workgraph as the memory of your Claude Code sessions.

Two hooks (installed with `workgraph claude-setup`):
  SessionStart  track the project you opened (adds it to the default workspace), log the session, and print a short
                summary of what the graph already knows (stages, gaps, open items, product, owner, recent sessions).
                Claude Code adds that output to the session's context.
  SessionEnd    record what changed (branch, modified files) and rebuild the graph in the background if it did.

Hooks must never get in the way of a session: every handler swallows its errors and exits quietly.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import paths
from .tracking import init_home, track_project

HOOK_EVENTS = {"SessionStart": "session-start", "SessionEnd": "session-end"}


def _git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def project_root(cwd: str) -> Path | None:
    """The folder to track for a session started in `cwd`: its git root, else `cwd` itself. Never the home folder."""
    root = Path(_git(cwd, "rev-parse", "--show-toplevel") or cwd).resolve()
    if root == Path.home().resolve() or root == paths.home().resolve() or paths.home().resolve() in root.parents:
        return None
    return root if root.is_dir() else None


def _project_name(root: Path) -> str | None:
    """The name this root is tracked under in the default workspace, if any."""
    import yaml
    ws = paths.default_workspace()
    if not ws.exists():
        return None
    for p in (yaml.safe_load(ws.read_text(encoding="utf-8")) or {}).get("projects") or []:
        if (ws.parent / str(p["path"])).resolve() == root:
            return p["name"]
    return None


def _store():
    from .store import Store
    init_home()
    return Store(paths.store_path())


def build_running() -> bool:
    lock = paths.out_dir() / ".build.lock"
    try:
        os.kill(int(lock.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def rebuild_in_background() -> None:
    """Start `workgraph build` detached, unless one is already running."""
    if build_running():
        return
    out = paths.out_dir()
    out.mkdir(parents=True, exist_ok=True)
    log = (out / "build.log").open("a")
    p = subprocess.Popen([sys.executable, "-m", "workgraph", "build", "-c", str(paths.default_workspace())], stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    (out / ".build.lock").write_text(str(p.pid))


def context_summary(name: str) -> str:
    """What the last build knows about project `name`, as a few lines for the session context."""
    from .analyze import find_gaps
    from .domain import Workspace
    import networkx as nx
    graph = paths.out_dir() / "graph.json"
    if not graph.exists():
        return f"[workgraph] tracking project {name}; no graph built yet (a build has been started in the background)."
    M = nx.node_link_graph(json.loads(graph.read_text(encoding="utf-8")), edges="edges")
    ws = Workspace.load(paths.default_workspace())
    built = time.strftime("%Y-%m-%d %H:%M", time.localtime(graph.stat().st_mtime))
    lines = [f"[workgraph] memory for project {name} (graph built {built}, {graph})"]
    nodes = [(n, d) for n, d in M.nodes(data=True) if d.get("project") == name]
    if not nodes:
        return lines[0] + "\nThis project is not in the last build yet; it will be after this session."
    stages: dict[str, int] = {}
    for _, d in nodes:
        if d.get("kind") not in ("stage", "project", "symbol", "external", "rationale"):
            for s in d.get("stages", []):
                stages[s] = stages.get(s, 0) + 1
    lines.append("Artifacts by stage: " + ", ".join(f"{k} {v}" for k, v in stages.items()))
    pid = f"project:{name}"
    if pid in M:
        rel = lambda u, r: [v for _, v, d in M.out_edges(u, data=True) if d.get("relation") == r]  # noqa: E731
        label = lambda i: M.nodes[i].get("label", i)  # noqa: E731
        for prod in rel(pid, "belongs_to"):
            if M.nodes[prod].get("layer") == "product":
                owners = [label(o) for o in rel(prod, "owned_by")]
                procs = [label(p) for p in rel(prod, "follows")]
                lines.append(f"Product: {label(prod)}" + (f"; owner: {', '.join(owners)}" if owners else "")
                             + (f"; process: {', '.join(procs)}" if procs else ""))
    try:
        gaps = [g for g in find_gaps(M, ws) if g["project"] == name]
    except Exception:
        gaps = []
    if gaps:
        lines.append(f"Gaps ({len(gaps)}):")
        lines += [f"  - {g['label']}: {g['message']}" for g in gaps[:8]]
        if len(gaps) > 8:
            lines.append(f"  - ... {len(gaps) - 8} more (run `workgraph gaps`)")
    items = [d for _, d in nodes if d.get("origin") == "llm" and d.get("status") == "open"]
    if items:
        lines.append(f"Open work items ({len(items)}):")
        lines += [f"  - [{d.get('kind')}] {d.get('label')}" for d in items[:8]]
    try:
        with _store() as store:
            for s in store.recent_sessions(name):
                when = time.strftime("%Y-%m-%d", time.localtime(s["started"]))
                title = f' "{s["title"]}"' if s.get("title") else ""
                files = s["changed"]
                more = "..." if len(files) > 4 else ""
                detail = f" ({', '.join(files[:4])}{more})" if files else ""
                lines.append(f"Earlier session {when}{title} on {s['branch'] or '?'}: {len(files)} files changed{detail}")
    except Exception:
        pass
    return "\n".join(lines)


def session_start(payload: dict) -> str:
    root = project_root(payload.get("cwd") or os.getcwd())
    if not root:
        return ""
    track_project(root)
    name = _project_name(root) or root.name
    with _store() as store:
        store.session_start(payload.get("session_id", ""), name, str(root), _git(str(root), "branch", "--show-current"),
                            _git(str(root), "rev-parse", "HEAD"))
    if not (paths.out_dir() / "graph.json").exists():
        rebuild_in_background()
    return context_summary(name)


def session_end(payload: dict) -> str:
    root = project_root(payload.get("cwd") or os.getcwd())
    if not root:
        return ""
    name = _project_name(root) or root.name
    sid = payload.get("session_id", "")
    head = _git(str(root), "rev-parse", "HEAD")
    changed = [ln.split(None, 1)[1] for ln in _git(str(root), "status", "--porcelain").splitlines() if ln.strip()][:50]
    with _store() as store:
        started_head = store.session_head(sid)
        store.session_end(sid, name, str(root), _git(str(root), "branch", "--show-current"), head, changed)
    if changed or (started_head and started_head != head):
        rebuild_in_background()
    return ""


def handle(event: str, stdin_text: str) -> str:
    """Entry point for `workgraph hook <event>`. Returns text for stdout; never raises."""
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
        fn = {"session-start": session_start, "session-end": session_end}.get(event)
        return fn(payload) if fn else ""
    except Exception:
        return ""


def hook_settings(command: str = "workgraph") -> dict:
    exe = shutil.which(command) or command
    return {"hooks": {ev: [{"hooks": [{"type": "command", "command": f"{exe} hook {name}"}]}]
                      for ev, name in HOOK_EVENTS.items()}}


def command_text() -> str:
    from importlib import resources
    return (resources.files("workgraph") / "data" / "workgraph-command.md").read_text(encoding="utf-8")


def install_command(commands_dir: Path) -> bool:
    """Write the plain `/workgraph` slash command (a user-level command). Never overwrites an existing file."""
    target = commands_dir / "workgraph.md"
    if target.exists():
        return False
    commands_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(command_text(), encoding="utf-8")
    return True


def install_hooks(settings_path: Path) -> list[str]:
    """Merge the workgraph hooks into a Claude Code settings.json. Returns the events that were added."""
    data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    wanted = hook_settings()["hooks"]
    added = []
    for ev, entries in wanted.items():
        existing = data.setdefault("hooks", {}).setdefault(ev, [])
        cmd = entries[0]["hooks"][0]["command"]
        if any(h.get("command", "").endswith(cmd.split(" ", 1)[1]) and "workgraph" in h.get("command", "")
               for e in existing for h in e.get("hooks", [])):
            continue
        existing += entries
        added.append(ev)
    if added:
        if settings_path.exists():
            shutil.copy2(settings_path, settings_path.with_suffix(".json.bak"))
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return added
