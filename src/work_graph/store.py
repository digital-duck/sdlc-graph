"""SQLite store next to the graph files: file registry, YAML comments, and uid history for rename detection."""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, layer TEXT, json_sha256 TEXT, yaml_sha256 TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS comments (file TEXT, item_key TEXT, key TEXT, position TEXT, text TEXT, seq INTEGER);
CREATE INDEX IF NOT EXISTS comments_file ON comments(file);
CREATE TABLE IF NOT EXISTS node_ids (uid TEXT, id TEXT, layer TEXT, last_seen REAL, PRIMARY KEY (uid, id));
"""


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(DDL)

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
                        (base.name, layer, sha256(base.with_suffix(".json")), sha256(base.with_suffix(".yaml")), time.time()))
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
