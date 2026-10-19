"""SQLite store next to the graph files: file registry, YAML comments, and uid history for rename detection."""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

from . import paths

DDL = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, layer TEXT, json_sha256 TEXT, yaml_sha256 TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS comments (file TEXT, item_key TEXT, key TEXT, position TEXT, text TEXT, seq INTEGER);
CREATE INDEX IF NOT EXISTS comments_file ON comments(file);
CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, project TEXT, cwd TEXT, branch TEXT, head TEXT,
  started REAL, ended REAL, changed TEXT, title TEXT);
CREATE TABLE IF NOT EXISTS synced (path TEXT PRIMARY KEY, sha256 TEXT);
CREATE TABLE IF NOT EXISTS node_ids (uid TEXT, id TEXT, layer TEXT, last_seen REAL, PRIMARY KEY (uid, id));
"""


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def file_key(base: Path) -> str:
    """How a graph file is named in the store: its bare name for files in the home `custom/graphs/` folder (so the key is the
    same on every machine), else the full path without extension (a workspace's own graphs folder, e.g. examples/)."""
    base = base.with_suffix("") if base.suffix else base
    if base.parent.resolve() == paths.graphs_dir().resolve():
        return base.name
    return str((base.parent.resolve() / base.name))


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path == paths.store_path() and not self.path.exists():
            for old in paths.legacy_store_paths():      # pre-rename name and place
                if old.exists():
                    old.rename(self.path)
                    break
        self.db = sqlite3.connect(self.path)
        self.db.executescript(DDL)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(sessions)")}
        if "title" not in cols:                       # databases created before session titles existed
            self.db.execute("ALTER TABLE sessions ADD COLUMN title TEXT")

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- registry
    def register(self, base: Path, layer: str | None) -> None:
        """Record a graph file pair (`base` without extension) and its current content hashes."""
        self.db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)",
                        (file_key(base), layer, sha256(base.with_suffix(".json")), sha256(base.with_suffix(".yaml")), time.time()))
        self.db.commit()

    def registered(self, name: str) -> tuple[str | None, str | None] | None:
        """(json_sha256, yaml_sha256) recorded at the last convert of `name` (no extension), or None."""
        r = self.db.execute("SELECT json_sha256, yaml_sha256 FROM files WHERE path=?", (name,)).fetchone()
        return (r[0], r[1]) if r else None

    # --- comments
    def replace_comments(self, file: str, comments: list[dict]) -> None:
        self.db.execute("DELETE FROM comments WHERE file=?", (file,))
        self.db.executemany("INSERT INTO comments VALUES (?,?,?,?,?,?)",
                            [(file, c["item_key"], c["key"], c["position"], c["text"], i) for i, c in enumerate(comments)])
        self.db.commit()

    def comments(self, file: str) -> list[dict]:
        rows = self.db.execute("SELECT item_key, key, position, text FROM comments WHERE file=? ORDER BY seq", (file,))
        return [dict(item_key=r[0], key=r[1], position=r[2], text=r[3]) for r in rows]

    # --- uid history
    def remember_ids(self, layer: str, nodes: list[dict]) -> None:
        now = time.time()
        self.db.executemany("INSERT OR REPLACE INTO node_ids VALUES (?,?,?,?)",
                            [(n["uid"], n["id"], layer, now) for n in nodes if n.get("uid")])
        self.db.commit()

    def uids_for(self, node_id: str) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT uid FROM node_ids WHERE id=?", (node_id,))]

    # --- export / merge for sync (text records; the database itself is never shared)
    def layer_of_file(self, key: str) -> str | None:
        r = self.db.execute("SELECT layer FROM files WHERE path=?", (key,)).fetchone()
        return r[0] if r else None

    def dump_comments(self, layers_excluded: set[str]) -> list[dict]:
        out = []
        for (f,) in self.db.execute("SELECT DISTINCT file FROM comments ORDER BY file").fetchall():
            if "/" in f or self.layer_of_file(f) in layers_excluded:   # keys with a path belong to a local graphs folder
                continue
            out += [{"file": f, **c} for c in self.comments(f)]
        return out

    def merge_comments(self, records: list[dict]) -> int:
        added = 0
        have = {(r[0], r[1], r[2], r[3], r[4]) for r in self.db.execute("SELECT file, item_key, key, position, text FROM comments")}
        nxt: dict[str, int] = {}
        for r in records:
            k = (r["file"], r["item_key"], r["key"], r["position"], r["text"])
            if k in have:
                continue
            seq = nxt.setdefault(r["file"], (self.db.execute("SELECT COALESCE(MAX(seq), -1) FROM comments WHERE file=?",
                                                              (r["file"],)).fetchone()[0]) + 1)
            self.db.execute("INSERT INTO comments VALUES (?,?,?,?,?,?)", (*k[:4], k[4], seq))
            nxt[r["file"]] = seq + 1
            have.add(k)
            added += 1
        self.db.commit()
        return added

    def dump_node_ids(self, layers_excluded: set[str]) -> list[dict]:
        rows = self.db.execute("SELECT uid, id, layer FROM node_ids ORDER BY layer, id, uid")
        return [{"uid": u, "id": i, "layer": l} for u, i, l in rows if l not in layers_excluded]

    def merge_node_ids(self, records: list[dict]) -> int:
        before = self.db.execute("SELECT COUNT(*) FROM node_ids").fetchone()[0]
        self.db.executemany("INSERT OR IGNORE INTO node_ids VALUES (?,?,?,?)",
                            [(r["uid"], r["id"], r["layer"], time.time()) for r in records])
        self.db.commit()
        return self.db.execute("SELECT COUNT(*) FROM node_ids").fetchone()[0] - before

    def dump_sessions(self) -> list[dict]:
        rows = self.db.execute("SELECT session_id, project, branch, started, ended, changed, title FROM sessions "
                               "WHERE ended IS NOT NULL ORDER BY started, session_id")
        return [dict(session_id=r[0], project=r[1], branch=r[2], started=r[3], ended=r[4],
                     changed=[c for c in (r[5] or "").split("\n") if c], title=r[6] or "") for r in rows]

    def merge_sessions(self, records: list[dict]) -> int:
        have = {r[0] for r in self.db.execute("SELECT session_id FROM sessions")}
        added = 0
        for r in records:
            if r["session_id"] not in have:
                self.session_import(r["session_id"], r["project"], "", r.get("branch", ""), r["started"], r["ended"],
                                    r.get("changed", []), r.get("title", ""))
                added += 1
        return added

    def synced_hash(self, path: str) -> str | None:
        r = self.db.execute("SELECT sha256 FROM synced WHERE path=?", (path,)).fetchone()
        return r[0] if r else None

    def set_synced(self, path: str, digest: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO synced VALUES (?,?)", (path, digest))
        self.db.commit()

    # --- Claude Code sessions (the work journal)
    def session_start(self, session_id: str, project: str, cwd: str, branch: str, head: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO sessions (session_id, project, cwd, branch, head, started) VALUES (?,?,?,?,?,?)",
                        (session_id, project, cwd, branch, head, time.time()))
        self.db.commit()

    def session_end(self, session_id: str, project: str, cwd: str, branch: str, head: str, changed: list[str]) -> None:
        self.db.execute("INSERT OR IGNORE INTO sessions (session_id, project, cwd, branch, head, started) VALUES (?,?,?,?,?,?)",
                        (session_id, project, cwd, branch, head, time.time()))
        self.db.execute("UPDATE sessions SET ended=?, branch=?, head=?, changed=? WHERE session_id=?",
                        (time.time(), branch, head, "\n".join(changed), session_id))
        self.db.commit()

    def session_import(self, session_id: str, project: str, cwd: str, branch: str, started: float, ended: float,
                       changed: list[str], title: str = "") -> None:
        """A session read from a Claude Code transcript. Idempotent: the same id replaces its earlier row."""
        self.db.execute("INSERT OR REPLACE INTO sessions (session_id, project, cwd, branch, head, started, ended, changed, title) "
                        "VALUES (?,?,?,?,?,?,?,?,?)", (session_id, project, cwd, branch, "", started, ended, "\n".join(changed), title))
        self.db.commit()

    def session_head(self, session_id: str) -> str | None:
        r = self.db.execute("SELECT head FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        return r[0] if r else None

    def recent_sessions(self, project: str, limit: int = 3) -> list[dict]:
        rows = self.db.execute("SELECT started, ended, branch, changed, title FROM sessions WHERE project=? AND ended IS NOT NULL "
                               "ORDER BY started DESC LIMIT ?", (project, limit))
        return [dict(started=r[0], ended=r[1], branch=r[2], changed=[c for c in (r[3] or "").split("\n") if c], title=r[4] or "")
                for r in rows]
