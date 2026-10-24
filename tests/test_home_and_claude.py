import json
import subprocess
from pathlib import Path

import pytest
import yaml

from workgraph import claude_code, paths, sources
from workgraph.cli import main
from workgraph.domain import Workspace
from workgraph.tracking import init_home, track_project

EX = Path(__file__).parent.parent / "examples"


def test_home_defaults_and_override(tmp_path, monkeypatch):
    assert paths.home() == tmp_path / "wg-home"
    monkeypatch.delenv("WORKGRAPH_HOME")
    assert paths.home() == Path("~/workgraph").expanduser()


def test_init_and_track_are_idempotent_and_keep_comments():
    h = init_home()
    assert paths.graphs_dir().is_dir() and (h / "workspace.yaml").exists()
    assert paths.graphs_dir() == h / "custom" / "graphs" and (h / "custom" / "README.md").exists()
    assert paths.store_path().exists()                 # the database exists from the start
    assert track_project(EX / "acme-shop") is True
    assert track_project(EX / "acme-shop") is False
    assert track_project(EX / "rq-demo", domain="research") is True
    text = paths.default_workspace().read_text()
    assert text.startswith("# workgraph default workspace")        # comments survive the edit
    projects = yaml.safe_load(text)["projects"]
    assert [p["name"] for p in projects] == ["acme-shop", "rq-demo"] and projects[1]["domain"] == "research"


def test_build_uses_default_workspace_and_writes_to_home(capsys):
    track_project(EX / "acme-shop")
    main(["build"])
    assert (paths.out_dir() / "graph.json").exists()
    assert "26 nodes" in capsys.readouterr().out


def test_src_local_path_and_url_helpers(capsys):
    main(["gaps", "--src", str(EX / "acme-shop")])
    assert "requirement has no design" in capsys.readouterr().out      # REQ-003 has no design (checkout.py is tested, so no code-file gap)
    assert sources.is_url("https://github.com/a/b.git") and sources.is_url("git@github.com:a/b.git")
    assert not sources.is_url("/some/path") and not sources.is_url("file:///x")
    assert sources.project_name("git@github.com:digital-duck/work-graph.git") == "work-graph"
    with pytest.raises(SystemExit):
        main(["gaps", "--src", str(EX / "acme-shop"), "-c", str(EX / "workspace.yaml")])
    with pytest.raises(SystemExit):
        main(["gaps", "--src", "/does/not/exist"])


def test_src_url_clones_into_home(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        dest = Path(cmd[-1])
        (dest / ".git").mkdir(parents=True)
        (dest / "a.py").write_text("def f():\n    return 1\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(sources.subprocess, "run", fake_run)
    ws = sources.workspace_from_sources(["https://example.com/team/demo.git"])
    assert calls[0][:4] == ["git", "clone", "--depth", "1"]
    assert ws.projects[0].name == "demo" and ws.projects[0].path == paths.src_dir() / "demo"


def _repo(tmp_path):
    r = tmp_path / "myproj"
    r.mkdir()
    (r / "a.py").write_text("def f():\n    return 1\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(r), *cmd], check=True)
    return r


def test_session_hooks_track_log_and_summarize(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_code, "rebuild_in_background", lambda: calls.append("rebuild"))
    calls = []
    r = _repo(tmp_path)
    out = claude_code.handle("session-start", json.dumps({"session_id": "s1", "cwd": str(r)}))
    assert "myproj" in out and calls == ["rebuild"]                    # first session: no graph yet
    assert "myproj" in paths.default_workspace().read_text()
    (r / "a.py").write_text("def f():\n    return 2\n")
    claude_code.handle("session-end", json.dumps({"session_id": "s1", "cwd": str(r)}))
    assert calls == ["rebuild", "rebuild"]                              # files changed -> rebuild
    main(["build"])
    out = claude_code.handle("session-start", json.dumps({"session_id": "s2", "cwd": str(r)}))
    assert "memory for project myproj" in out and "Earlier session" in out and "a.py" in out


def test_hooks_ignore_home_folder_and_never_raise(tmp_path):
    assert claude_code.handle("session-start", json.dumps({"cwd": str(paths.home())})) == ""
    assert claude_code.handle("session-start", "not json") == ""
    assert claude_code.handle("nonsense", "{}") == ""


def test_install_hooks_merges_without_clobbering(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "x", "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}))
    assert claude_code.install_hooks(settings) == ["SessionStart", "SessionEnd"]
    data = json.loads(settings.read_text())
    assert data["model"] == "x" and len(data["hooks"]["SessionStart"]) == 2
    assert claude_code.install_hooks(settings) == []                    # second run adds nothing
    assert settings.with_suffix(".json.bak").exists()


def test_plain_command_install_and_plugin_copy_in_sync(tmp_path):
    cmds = tmp_path / "commands"
    assert claude_code.install_command(cmds) is True
    assert claude_code.install_command(cmds) is False                  # never overwrites
    plugin_copy = Path(__file__).parent.parent / "plugin" / "commands" / "workgraph.md"
    assert (cmds / "workgraph.md").read_text() == plugin_copy.read_text()


def test_background_rebuild_pins_the_default_workspace(monkeypatch):
    seen = {}

    class P:
        pid = 1

    monkeypatch.setattr(claude_code.subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd) or P())
    claude_code.rebuild_in_background()
    assert seen["cmd"][-2:] == ["-c", str(paths.default_workspace())]


def test_build_creates_the_whole_home_including_the_database():
    main(["build", "--src", str(EX / "acme-shop")])
    assert paths.store_path().exists() and paths.default_workspace().exists() and (paths.out_dir() / "graph.json").exists()


def test_relative_config_falls_back_to_the_home_workspace(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)                                  # a folder with no workspace.yaml
    with pytest.raises(SystemExit) as e:
        main(["build", "-c", "nothere.yaml"])
    assert "nothere.yaml" in str(e.value) and "looked in the current folder" in str(e.value)
    with pytest.raises(SystemExit) as e:                         # the home's starter workspace has no projects
        main(["build", "-c", "workspace.yaml"])
    assert "lists no projects yet" in str(e.value)
    track_project(EX / "acme-shop")
    main(["build", "-c", "workspace.yaml"])                      # found in the home folder
    assert "26 nodes" in capsys.readouterr().out


def test_legacy_graphs_folder_moves_into_custom(tmp_path):
    old = paths.legacy_graphs_dir()
    old.mkdir(parents=True)
    (old / "product.json").write_text("{}")
    init_home()
    assert not old.exists() and (paths.graphs_dir() / "product.json").exists()
    assert yaml.safe_load(paths.default_workspace().read_text())["projects"] == []
    assert Workspace.load(paths.default_workspace()).graphs_dir == paths.graphs_dir().resolve()   # the default workspace finds custom/graphs


def test_custom_domain_pack_is_found_by_name_and_wins_over_builtin(tmp_path):
    from workgraph.domain import Domain, available_domains
    init_home()
    src = (Path(__file__).parent.parent / "src/workgraph/domains/research.yaml").read_text(encoding="utf-8")
    (paths.custom_domains_dir() / "mine.yaml").write_text(src.replace("domain: research", "domain: mine"), encoding="utf-8")
    assert "mine" in available_domains()
    assert Domain.load("mine").name == "mine"
    (paths.custom_domains_dir() / "sdlc.yaml").write_text(src, encoding="utf-8")
    assert Domain.load("sdlc").name == "research"
