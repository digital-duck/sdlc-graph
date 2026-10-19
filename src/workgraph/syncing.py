"""Moving a workgraph between machines: a text "sync tree" of everything that cannot be rebuilt.

What is shared: the portable workspace, layer files, the comment / uid / session records of the database as JSONL,
and optionally the LLM cache. What is never shared: the database itself, built graphs, AST and embedding caches,
project clones. The same tree is committed to a private git repository (`sync`) or packed into a tar.gz (`export`).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

import yaml
from ruamel.yaml import YAML

from . import paths
from .store import Store, file_key
from .tracking import STARTER, init_home

FORMAT = 1
PARTS = ("workspace", "graphs", "comments", "org", "sessions", "llm-cache")
DEFAULT_PARTS = ("workspace", "graphs", "comments")      # org (people), sessions (file lists) and the LLM cache are opt-in
MANAGED = ("manifest.json", "workspace.yaml", "graphs", "store", "cache")
MAX_FILE = 5 * 1024 * 1024
MAX_ARCHIVE = 200 * 1024 * 1024
BAD_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".sqlite-wal", ".sqlite-shm", ".sqlite-journal", ".db-wal"}
SECRETS = {
    "Anthropic API key": r"sk-ant-[A-Za-z0-9_\-]{20,}",
    "AWS access key": r"AKIA[0-9A-Z]{16}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}",
    "Slack token": r"xox[baprs]-[A-Za-z0-9\-]{10,}",
    "Google API key": r"AIza[0-9A-Za-z_\-]{35}",
    "private key": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
}
GITIGNORE = """# Allowlist: only the files workgraph writes are tracked. Everything else, databases included, is ignored.
*
!.gitignore
!manifest.json
!workspace.yaml
!graphs/
!graphs/*.json
!graphs/*.yaml
!store/
!store/*.jsonl
!cache/
!cache/llm/
!cache/llm/*.json
"""


class SyncError(RuntimeError):
    pass


def parse_parts(extra: str | None) -> set[str]:
    parts = set(DEFAULT_PARTS)
    for p in (x.strip() for x in (extra or "").split(",") if x.strip()):
        if p not in PARTS:
            raise SyncError(f"unknown part {p!r}; choose from {', '.join(PARTS)}")
        parts.add(p)
    return parts


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# --- guards: nothing private leaves by accident

def guard_tree(root: Path) -> list[str]:
    """Problems that must stop a push or an export: databases, big files, secrets."""
    problems = []
    for f in sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts):
        rel = f.relative_to(root).as_posix()
        if f.suffix.lower() in BAD_SUFFIXES:
            problems.append(f"{rel}: database file")
            continue
        head = f.open("rb").read(16)
        if head.startswith(b"SQLite format 3"):
            problems.append(f"{rel}: SQLite database")
            continue
        if f.stat().st_size > MAX_FILE:
            problems.append(f"{rel}: larger than {MAX_FILE // 1024 // 1024} MB")
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for name, pat in SECRETS.items():
            if re.search(pat, text):
                problems.append(f"{rel}: looks like a {name}")
    return problems


# --- the tree

def _portable_workspace() -> dict:
    ws_file = paths.default_workspace()
    raw = yaml.safe_load(ws_file.read_text(encoding="utf-8")) if ws_file.exists() else {}
    out = {"domain": (raw or {}).get("domain", "sdlc"), "projects": []}
    for p in (raw or {}).get("projects") or []:
        entry = {k: v for k, v in p.items() if k != "path"}      # folders differ per machine
        if "repo" not in entry and p.get("path"):
            from .tracking import _git_remote
            folder = (ws_file.parent / str(p["path"])).resolve()
            if (repo := _git_remote(folder)):
                entry["repo"] = repo
        out["projects"].append(entry)
    return out


def _layer_of_graph_file(f: Path) -> str | None:
    j = f.with_suffix(".json")
    try:
        if j.exists():
            return json.loads(j.read_text(encoding="utf-8")).get("layer")
        m = re.search(r"^layer:\s*(\S+)", f.read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else None
    except (OSError, ValueError):
        return None


def export_tree(dest: Path, parts: set[str], store: Store) -> dict:
    """Write the sync tree into `dest`, replacing what a previous export put there (and nothing else)."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in MANAGED:
        t = dest / name
        shutil.rmtree(t) if t.is_dir() else t.unlink(missing_ok=True)
    counts: dict[str, int] = {}
    excluded = set() if "org" in parts else {"org"}
    if "workspace" in parts:
        (dest / "workspace.yaml").write_text(
            "# Portable workspace: names and repositories, no local folders. `workgraph sync pull --clone-missing`\n"
            "# or `workgraph track <folder>` binds each project on a new machine.\n"
            + yaml.safe_dump(_portable_workspace(), sort_keys=False), encoding="utf-8")
        counts["projects"] = len(_portable_workspace()["projects"])
    if "graphs" in parts and paths.graphs_dir().is_dir():
        n = 0
        for f in sorted(paths.graphs_dir().iterdir()):
            if f.suffix in (".json", ".yaml") and _layer_of_graph_file(f) not in excluded:
                (dest / "graphs").mkdir(exist_ok=True)
                shutil.copy2(f, dest / "graphs" / f.name)
                n += 1
        counts["graph files"] = n
    if "comments" in parts:
        _jsonl(dest / "store" / "comments.jsonl", store.dump_comments(excluded))
        _jsonl(dest / "store" / "node_ids.jsonl", store.dump_node_ids(excluded))
        counts["comments"] = len(_read_jsonl(dest / "store" / "comments.jsonl"))
    if "sessions" in parts:
        _jsonl(dest / "store" / "sessions.jsonl", store.dump_sessions())
        counts["sessions"] = len(_read_jsonl(dest / "store" / "sessions.jsonl"))
    if "llm-cache" in parts and paths.cache_dir().is_dir():
        files = sorted(paths.cache_dir().glob("llm_items-*.json"))
        for f in files:
            (dest / "cache" / "llm").mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest / "cache" / "llm" / f.name)
        counts["llm cache files"] = len(files)
    (dest / "manifest.json").write_text(json.dumps(
        {"format": FORMAT, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "parts": sorted(parts), "counts": counts},
        indent=2) + "\n", encoding="utf-8")
    problems = guard_tree(dest)
    if problems:
        raise SyncError("refusing to export, nothing leaves this machine:\n  " + "\n  ".join(problems))
    for f in (dest / "graphs").glob("*") if (dest / "graphs").is_dir() else []:
        store.set_synced("graphs/" + f.name, _sha(f))
    return counts


def _merge_workspace(incoming: dict) -> list[str]:
    """Union by project name into the live workspace. Existing entries (with their local paths) are kept."""
    init_home()
    ws_file = paths.default_workspace()
    y = YAML()
    y.width = 4096
    doc = y.load(ws_file.read_text(encoding="utf-8")) or {}
    if doc.get("projects") is None:
        doc["projects"] = []
    have = {p["name"]: p for p in doc["projects"]}
    added = []
    for p in incoming.get("projects") or []:
        if p["name"] in have:
            if p.get("repo") and not have[p["name"]].get("repo"):
                have[p["name"]]["repo"] = p["repo"]
            continue
        m = y.map()
        m.update(p)
        m.fa.set_flow_style()
        doc["projects"].append(m)
        added.append(p["name"])
    with ws_file.open("w", encoding="utf-8") as f:
        y.dump(doc, f)
    return added


def import_tree(src: Path, store: Store) -> dict:
    """Apply a sync tree to the live home. Never destroys local work: a layer file changed on both sides keeps the
    local version and puts the incoming one beside it as `<name>.incoming.<ext>`."""
    mf = src / "manifest.json"
    if not mf.exists():
        raise SyncError(f"{src} is not a workgraph sync tree (no manifest.json)")
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT:
        raise SyncError(f"sync format {manifest.get('format')} is not supported by this workgraph (expects {FORMAT})")
    init_home()
    rep: dict = {"updated": [], "added": [], "conflicts": [], "projects added": [], "unresolved": []}
    if (src / "workspace.yaml").exists():
        rep["projects added"] = _merge_workspace(yaml.safe_load((src / "workspace.yaml").read_text(encoding="utf-8")) or {})
    gdir = paths.graphs_dir()
    for f in sorted((src / "graphs").glob("*")) if (src / "graphs").is_dir() else []:
        key, theirs = "graphs/" + f.name, _sha(f)
        live = gdir / f.name
        base = store.synced_hash(key)
        if not live.exists():
            gdir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, live)
            rep["added"].append(f.name)
        elif _sha(live) == theirs:
            pass
        elif base is not None and _sha(live) == base:           # unchanged here since the last sync: take theirs
            shutil.copy2(f, live)
            rep["updated"].append(f.name)
        elif base is not None and theirs == base:               # unchanged there: keep ours
            continue
        else:                                                   # changed on both sides, or never synced before
            side = live.with_name(f"{live.stem}.incoming{live.suffix}")
            shutil.copy2(f, side)
            rep["conflicts"].append(f"{f.name} (incoming copy: {side.name})")
            continue
        store.set_synced(key, theirs)
    rep["comments added"] = store.merge_comments(_read_jsonl(src / "store" / "comments.jsonl"))
    rep["uid records added"] = store.merge_node_ids(_read_jsonl(src / "store" / "node_ids.jsonl"))
    rep["sessions added"] = store.merge_sessions(_read_jsonl(src / "store" / "sessions.jsonl"))
    n = 0
    for f in sorted((src / "cache" / "llm").glob("llm_items-*.json")) if (src / "cache" / "llm").is_dir() else []:
        t = paths.cache_dir() / f.name
        if not t.exists():
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, t)
            n += 1
    rep["llm cache files added"] = n
    from .domain import Workspace
    rep["unresolved"] = Workspace.load(paths.default_workspace()).unresolved
    return rep


# --- tar.gz (initial setup, for example from a USB stick)

def export_archive(out: Path, parts: set[str], store: Store) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "workgraph-sync"
        counts = export_tree(tree, parts, store)
        out.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(out, "w:gz") as tar:
            tar.add(tree, arcname="workgraph-sync")
    return counts


def _safe_extract(archive: Path, dest: Path) -> Path:
    """Unpack only plain files and folders under one top-level folder: no links, devices, absolute or `..` paths."""
    total = 0
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for m in members:
            p = Path(m.name)
            if not (m.isfile() or m.isdir()) or p.is_absolute() or ".." in p.parts:
                raise SyncError(f"unsafe entry in archive: {m.name}")
            total += m.size
        if total > MAX_ARCHIVE:
            raise SyncError("archive is too large to be a workgraph dump")
        for m in members:
            target = (dest / m.name).resolve()
            if dest.resolve() not in target.parents and target != dest.resolve():
                raise SyncError(f"unsafe entry in archive: {m.name}")
        tar.extractall(dest, members=members)
    top = [p for p in dest.iterdir()]
    if len(top) != 1 or not (top[0] / "manifest.json").exists():
        raise SyncError("not a workgraph dump (expected one folder with manifest.json)")
    return top[0]


def import_archive(archive: Path, store: Store) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        tree = _safe_extract(archive, Path(tmp))
        problems = guard_tree(tree)
        if problems:
            raise SyncError("refusing to import:\n  " + "\n  ".join(problems))
        return import_tree(tree, store)


# --- git

def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise SyncError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r


def check_private(url: str, confirmed: bool) -> str:
    """Refuse a public repository. GitHub is asked through `gh`; anything unknown needs --confirm-private."""
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url)
    if m and shutil.which("gh"):
        r = subprocess.run(["gh", "api", f"repos/{m.group(1)}/{m.group(2)}", "--jq", ".private"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip() == "true":
            return "private (checked with gh)"
        if r.returncode == 0 and r.stdout.strip() == "false":
            raise SyncError(f"{url} is a PUBLIC repository. Your graph can contain people, plans and notes; use a private one.")
    if not confirmed:
        raise SyncError(f"cannot verify that {url} is private. Make sure it is, then repeat with --confirm-private.")
    return "private (confirmed by you)"


def load_config() -> dict:
    f = paths.home() / "sync.yaml"
    if not f.exists():
        raise SyncError("sync is not set up: run `workgraph sync init <private-git-url>` first")
    return yaml.safe_load(f.read_text(encoding="utf-8"))


def sync_init(url: str, parts: set[str], confirm_private: bool) -> str:
    status = check_private(url, confirm_private)
    init_home()
    tree = paths.sync_dir()
    if (tree / ".git").exists():
        raise SyncError(f"{tree} is already a sync repository")
    tree.parent.mkdir(parents=True, exist_ok=True)
    if tree.exists() and any(tree.iterdir()):
        raise SyncError(f"{tree} exists and is not empty")
    r = subprocess.run(["git", "clone", "--quiet", url, str(tree)], capture_output=True, text=True)
    if r.returncode:
        raise SyncError(f"cannot clone {url}: {(r.stderr or r.stdout).strip()}")
    (tree / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (paths.home() / "sync.yaml").write_text(yaml.safe_dump({"remote": url, "parts": sorted(parts)}), encoding="utf-8")
    return status


def sync_push(store: Store, message: str | None = None) -> dict:
    cfg = load_config()
    tree = paths.sync_dir()
    counts = export_tree(tree, set(cfg["parts"]), store)
    _git(["add", "-A"], tree)
    if not _git(["status", "--porcelain"], tree).stdout.strip():
        return {"pushed": False, "counts": counts}
    _git(["commit", "-q", "-m", message or f"workgraph sync {time.strftime('%Y-%m-%d %H:%M')}"], tree)
    _git(["push", "-q", "-u", "origin", "HEAD"], tree)
    return {"pushed": True, "counts": counts}


def sync_pull(store: Store, clone_missing: bool = False) -> dict:
    load_config()
    tree = paths.sync_dir()
    if _git(["remote", "-v"], tree).stdout.strip() and _git(["ls-remote", "--heads", "origin"], tree, check=False).stdout.strip():
        _git(["pull", "--ff-only", "-q"], tree)
    rep = import_tree(tree, store)
    if clone_missing:
        rep["cloned"] = []
        ws = yaml.safe_load(paths.default_workspace().read_text(encoding="utf-8")) or {}
        for p in ws.get("projects") or []:
            if not p.get("path") and p["name"] in rep["unresolved"] and p.get("repo"):
                from .sources import fetch
                fetch(p["repo"])
                rep["cloned"].append(p["name"])
        from .domain import Workspace
        rep["unresolved"] = Workspace.load(paths.default_workspace()).unresolved
    return rep


def sync_status() -> dict:
    cfg = load_config()
    tree = paths.sync_dir()
    changed = [ln for ln in _git(["status", "--porcelain"], tree).stdout.splitlines() if ln.strip()]
    ahead = behind = None
    r = _git(["fetch", "-q", "origin"], tree, check=False)
    if r.returncode == 0:
        c = _git(["rev-list", "--left-right", "--count", "HEAD...@{u}"], tree, check=False)
        if c.returncode == 0:
            ahead, behind = (int(x) for x in c.stdout.split())
    return {"remote": cfg["remote"], "parts": cfg["parts"], "uncommitted": changed, "ahead": ahead, "behind": behind}
