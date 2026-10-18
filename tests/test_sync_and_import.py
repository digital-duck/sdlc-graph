import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

from workgraph import claude_import as ci
from workgraph import paths, syncing
from workgraph.cli import main
from workgraph.convert import json_to_yaml
from workgraph.domain import Workspace
from workgraph.store import Store
from workgraph.tracking import init_home, track_project

EX = Path(__file__).parent.parent / "examples"


def _make_home(home: Path, monkeypatch, with_org=True):
    """A populated home: layer files, a tracked project, a YAML comment, a session."""
    monkeypatch.setenv("WORKGRAPH_HOME", str(home))
    init_home()
    for f in (EX / "graphs").glob("*"):
        if f.suffix in (".json", ".yaml") and (with_org or not f.name.startswith("org")):
            shutil.copy2(f, paths.graphs_dir() / f.name)
    track_project(EX / "acme-shop")
    with Store(paths.store_path()) as st:
        y = paths.graphs_dir() / "product.yaml"
        json_to_yaml(paths.graphs_dir() / "product.json", y, st)
        y.write_text(y.read_text().replace("  - id: product:acme/retail/shop\n", "  # flagship\n  - id: product:acme/retail/shop\n", 1))
        from workgraph.convert import yaml_to_json
        yaml_to_json(y, paths.graphs_dir() / "product.json", st)
        st.session_import("s1", "acme-shop", "/x", "main", 1.0, 2.0, ["src/cart.py"], "a title")
        st.remember_ids("org", [{"uid": "aaaaaaaa", "id": "org:acme"}])


def test_guard_blocks_databases_and_secrets(tmp_path):
    (tmp_path / "a.sqlite3").write_bytes(b"SQLite format 3\x00" + b"0" * 100)
    (tmp_path / "b.txt").write_text("key = sk-ant-api03-" + "x" * 40)
    (tmp_path / "ok.json").write_text("{}")
    problems = syncing.guard_tree(tmp_path)
    assert any("database" in p or "SQLite" in p for p in problems) and any("Anthropic" in p for p in problems)
    assert not any("ok.json" in p for p in problems)


def test_database_lives_outside_synced_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKGRAPH_HOME", str(tmp_path / "h"))
    assert paths.store_path() == tmp_path / "h" / "workgraph.sqlite3"
    assert paths.sync_dir() not in paths.store_path().parents and paths.graphs_dir() not in paths.store_path().parents
    assert syncing.GITIGNORE.startswith("# Allowlist") and "\n*\n" in syncing.GITIGNORE
    old = paths.graphs_dir() / "store.sqlite"                       # a database from before the rename moves over
    old.parent.mkdir(parents=True)
    with Store(old):
        pass
    with Store(paths.store_path()):
        pass
    assert paths.store_path().exists() and not old.exists()


def test_archive_round_trip_between_machines_excludes_org_by_default(tmp_path, monkeypatch):
    _make_home(tmp_path / "a", monkeypatch)
    arc = tmp_path / "wg.tar.gz"
    with Store(paths.store_path()) as st:
        syncing.export_archive(arc, syncing.parse_parts(None), st)
    with tarfile.open(arc) as t:
        names = t.getnames()
    assert any(n.endswith("graphs/product.json") for n in names) and not any("org." in n or "sqlite" in n for n in names)
    assert not any("sessions" in n for n in names)                  # opt-in
    # machine B
    monkeypatch.setenv("WORKGRAPH_HOME", str(tmp_path / "b"))
    with Store(paths.store_path()) as st:
        rep = syncing.import_archive(arc, st)
        assert "product.json" in rep["added"] and rep["comments added"] >= 1
    assert (paths.graphs_dir() / "product.yaml").read_text().count("# flagship") == 1
    ws = yaml.safe_load(paths.default_workspace().read_text())
    assert ws["projects"][0]["name"] == "acme-shop" and "path" not in ws["projects"][0]    # no machine path travels
    assert Workspace.load(paths.default_workspace()).unresolved == ["acme-shop"]
    track_project(EX / "acme-shop")                                  # bind the project to a local folder
    assert Workspace.load(paths.default_workspace()).unresolved == []


def test_opt_in_parts(tmp_path, monkeypatch):
    _make_home(tmp_path / "a", monkeypatch)
    arc = tmp_path / "wg.tar.gz"
    with Store(paths.store_path()) as st:
        syncing.export_archive(arc, syncing.parse_parts("org,sessions"), st)
    with tarfile.open(arc) as t:
        names = t.getnames()
    assert any(n.endswith("org.json") for n in names) and any(n.endswith("sessions.jsonl") for n in names)
    with pytest.raises(syncing.SyncError):
        syncing.parse_parts("everything")


def test_import_never_overwrites_local_work(tmp_path, monkeypatch):
    _make_home(tmp_path / "a", monkeypatch)
    tree = tmp_path / "tree"
    with Store(paths.store_path()) as st:
        syncing.export_tree(tree, syncing.parse_parts(None), st)
        # unchanged locally, changed remotely -> take theirs
        doc = json.loads((tree / "graphs" / "process.json").read_text())
        doc["nodes"][0]["label"] = "Renamed remotely"
        (tree / "graphs" / "process.json").write_text(json.dumps(doc, indent=2))
        rep = syncing.import_tree(tree, st)
        assert "process.json" in rep["updated"]
        assert "Renamed remotely" in (paths.graphs_dir() / "process.json").read_text()
        # changed on both sides -> keep local, park the incoming copy
        local = json.loads((paths.graphs_dir() / "process.json").read_text())
        local["nodes"][0]["label"] = "Edited locally"
        (paths.graphs_dir() / "process.json").write_text(json.dumps(local, indent=2))
        doc["nodes"][0]["label"] = "Edited remotely again"
        (tree / "graphs" / "process.json").write_text(json.dumps(doc, indent=2))
        rep = syncing.import_tree(tree, st)
    assert rep["conflicts"] and "Edited locally" in (paths.graphs_dir() / "process.json").read_text()
    assert "Edited remotely again" in (paths.graphs_dir() / "process.incoming.json").read_text()


def test_unsafe_archive_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKGRAPH_HOME", str(tmp_path / "h"))
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as t:
        data = b"x"
        info = tarfile.TarInfo("../escape.txt")
        info.size = 1
        t.addfile(info, io.BytesIO(data))
    with Store(paths.store_path()) as st, pytest.raises(syncing.SyncError):
        syncing.import_archive(bad, st)
    assert not (tmp_path / "escape.txt").exists()


def _git_env(monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}.items():
        monkeypatch.setenv(k, v)


def test_git_sync_between_two_machines(tmp_path, monkeypatch):
    _git_env(monkeypatch)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    _make_home(tmp_path / "a", monkeypatch)
    with pytest.raises(syncing.SyncError):                            # cannot prove it is private
        syncing.sync_init(str(remote), syncing.parse_parts(None), False)
    syncing.sync_init(str(remote), syncing.parse_parts(None), True)
    with Store(paths.store_path()) as st:
        assert syncing.sync_push(st)["pushed"] is True
        assert syncing.sync_push(st)["pushed"] is False               # nothing changed
    tracked = subprocess.run(["git", "ls-files"], cwd=paths.sync_dir(), capture_output=True, text=True).stdout
    assert "graphs/product.json" in tracked and "sqlite" not in tracked and "org." not in tracked
    # machine B
    monkeypatch.setenv("WORKGRAPH_HOME", str(tmp_path / "b"))
    syncing.sync_init(str(remote), syncing.parse_parts(None), True)
    with Store(paths.store_path()) as st:
        rep = syncing.sync_pull(st)
    assert "product.json" in rep["added"] and rep["unresolved"] == ["acme-shop"]
    # an edit on A reaches B
    monkeypatch.setenv("WORKGRAPH_HOME", str(tmp_path / "a"))
    doc = json.loads((paths.graphs_dir() / "process.json").read_text())
    doc["nodes"][0]["label"] = "From A"
    (paths.graphs_dir() / "process.json").write_text(json.dumps(doc, indent=2))
    with Store(paths.store_path()) as st:
        assert syncing.sync_push(st)["pushed"]
    monkeypatch.setenv("WORKGRAPH_HOME", str(tmp_path / "b"))
    with Store(paths.store_path()) as st:
        rep = syncing.sync_pull(st)
    assert "process.json" in rep["updated"] and "From A" in (paths.graphs_dir() / "process.json").read_text()


def test_push_is_refused_when_a_secret_is_present(tmp_path, monkeypatch):
    _git_env(monkeypatch)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    _make_home(tmp_path / "a", monkeypatch)
    syncing.sync_init(str(remote), syncing.parse_parts(None), True)
    p = paths.graphs_dir() / "product.json"
    doc = json.loads(p.read_text())
    doc["nodes"][0]["label"] = "token ghp_" + "a" * 36
    p.write_text(json.dumps(doc))
    with Store(paths.store_path()) as st, pytest.raises(syncing.SyncError, match="GitHub token"):
        syncing.sync_push(st)


# --- Claude Code import

def _claude_home(tmp_path, monkeypatch, project: Path):
    cd = tmp_path / "claude"
    monkeypatch.setenv("WORKGRAPH_CLAUDE_DIR", str(cd))
    d = cd / "projects" / ci.slug(project)
    (d / "memory").mkdir(parents=True)
    (d / "memory" / "MEMORY.md").write_text("- [x](x.md)\n")
    (d / "memory" / "prefs.md").write_text("---\nname: prefs\ndescription: user likes short answers\nmetadata:\n  type: feedback\n---\nBody text.\n")
    recs = [
        {"type": "user", "sessionId": "sess1", "timestamp": "2026-10-01T10:00:00.000Z", "cwd": str(project), "gitBranch": "main",
         "message": {"role": "user", "content": "SECRET PROMPT TEXT"}},
        {"type": "assistant", "sessionId": "sess1", "timestamp": "2026-10-01T10:05:00.000Z", "cwd": str(project), "gitBranch": "dev",
         "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": str(project / "src" / "cart.py")}}]}},
        {"type": "ai-title", "sessionId": "sess1", "aiTitle": "Fix the cart"},
    ]
    (d / "sess1.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    return cd


def test_memory_files_become_untagged_nodes(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    shutil.copytree(EX / "acme-shop", proj)
    _claude_home(tmp_path, monkeypatch, proj)
    from workgraph.pipeline import build_workspace
    track_project(proj)
    M, _, _ = build_workspace(Workspace.load(paths.default_workspace()))
    mem = [d for _, d in M.nodes(data=True) if d.get("kind") == "memory"]
    assert len(mem) == 1 and mem[0]["label"] == "memory: prefs" and mem[0]["memory_type"] == "feedback"
    assert mem[0]["stages"] == [] and "short answers" in mem[0]["summary"]


def test_import_claude_plan_and_session_metadata_only(tmp_path, monkeypatch, capsys):
    proj = tmp_path / "proj"
    shutil.copytree(EX / "acme-shop", proj)
    _claude_home(tmp_path, monkeypatch, proj)
    monkeypatch.setattr(ci, "TEMP_PREFIXES", ())                     # pytest's tmp_path lives under /tmp
    (Path(os.environ["WORKGRAPH_CLAUDE_DIR"]) / "projects" / "-no-such-folder-here").mkdir()
    main(["import-claude"])
    out = capsys.readouterr().out
    assert "dry run" in out and "folder no longer exists" in out and "track" in out
    assert not paths.default_workspace().exists() or "proj" not in paths.default_workspace().read_text()
    main(["import-claude", "--apply", "--with-titles"])
    assert "proj" in paths.default_workspace().read_text()
    with Store(paths.store_path()) as st:
        s = st.recent_sessions("proj")[0]
        rows = st.db.execute("SELECT * FROM sessions").fetchall()
    assert s["title"] == "Fix the cart" and s["branch"] == "dev" and s["changed"] == ["src/cart.py"]
    assert "SECRET PROMPT TEXT" not in json.dumps(rows)              # message text is never stored
    main(["import-claude", "--apply"])                               # idempotent
    with Store(paths.store_path()) as st:
        assert st.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_slug_decoding_finds_folders_with_dots_and_dashes(tmp_path):
    d = tmp_path / "digital-duck" / "SPL.py"
    d.mkdir(parents=True)
    assert ci.decode_slug(ci.slug(d)) == d
