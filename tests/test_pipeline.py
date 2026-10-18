from pathlib import Path

from work_graph.analyze import find_gaps
from work_graph.domain import Workspace
from work_graph.pipeline import build_workspace, stage_summary

EX = Path(__file__).parent.parent / "examples"


def test_multi_domain_workspace_gaps():
    ws = Workspace.load(EX / "workspace.yaml")
    M, _, warnings = build_workspace(ws)
    assert set(stage_summary(M)) == {"sdlc", "research"}
    got = {(g["domain"], g["message"], g["label"].split(":")[0]) for g in find_gaps(M, ws)}
    assert ("sdlc", "requirement has no design", "REQ-003") in got
    assert ("sdlc", "requirement has no test", "REQ-002") in got
    assert ("sdlc", "code file has no test", "checkout.py") in got
    assert ("research", "question has no hypothesis", "Q2") in got
    # covered items are not flagged
    assert not any(g[2] in ("REQ-001", "cart.py", "Q1", "H1") for g in got)
    assert any("REQ-099" in w for w in warnings)


def test_derived_verifies_edge_is_inferred():
    M, _, _ = build_workspace(Workspace.load(EX / "acme-shop" / "workspace.yaml"))
    derived = [d for *_, d in M.edges(data=True) if d.get("relation") == "verifies" and d["confidence"] == "INFERRED"]
    assert derived  # test_cart.py imports cart.py


def _ws(tmp_path, projects):
    cfg = tmp_path / "workspace.yaml"
    lines = ["domain: sdlc", "projects:"] + [f"  - {{name: {n}, path: {n}}}" for n in projects]
    cfg.write_text("\n".join(lines) + "\n")
    return Workspace.load(cfg)


def test_cross_project_clone_links(tmp_path):
    body = "def helper():\n    return 'shared logic that is long enough to count as a real file'\n"
    for name, extra in (("a", ""), ("b", ""), ("c", "# local tweak\n")):
        (tmp_path / name / "src").mkdir(parents=True)
        (tmp_path / name / "src" / "util.py").write_text(extra + body)
    M, _, _ = build_workspace(_ws(tmp_path, ["a", "b", "c"]))
    rels = {d["relation"] for *_, d in M.edges(data=True)}
    assert {"identical_to", "variant_of"} <= rels
    assert M.graph["clones"][0]["path"] == "src/util.py"
    assert M.graph["clones"][0]["projects"] == ["a", "b", "c"]


def test_llm_items_domain_prompt_cache_and_gap_exemption(tmp_path, monkeypatch):
    import json
    calls = []

    def fake(prompt, *, backend, max_tokens):
        calls.append(prompt)
        return "Sure!\n" + json.dumps({"items": [
            {"title": "Support SSO", "kind": "requirement", "stages": ["requirement", "bogus"], "status": "open"},
            {"title": "Ignore me", "kind": "nonsense", "stages": ["build"]},
        ]})

    from work_graph import cc_cli
    monkeypatch.setattr(cc_cli, "complete", lambda prompt, **kw: fake(prompt, backend="cc_cli", max_tokens=0))
    monkeypatch.setenv("WORK_GRAPH_CACHE", str(tmp_path / "cache"))
    (tmp_path / "p" / "docs").mkdir(parents=True)
    (tmp_path / "p" / "docs" / "ROADMAP.md").write_text("# Roadmap\n- [ ] Support SSO\n")
    ws = _ws(tmp_path, ["p"])
    M, _, warnings = build_workspace(ws, semantic=True)
    items = [(n, d) for n, d in M.nodes(data=True) if d.get("origin") == "llm"]
    assert len(items) == 1                               # invalid kind dropped
    n, d = items[0]
    assert d["stages"] == ["requirement"] and d["status"] == "open"   # unknown stage tag rejected
    assert any(e["relation"] == "derived_from" and e["confidence"] == "INFERRED"
               for *_, e in M.out_edges(n, data=True))
    assert "allowed stages" in calls[0].lower() and "requirement, design, build" in calls[0]
    assert not [g for g in find_gaps(M, ws) if g["node"] == n]        # LLM items don't trigger gap rules
    build_workspace(ws, semantic=True)
    assert len(calls) == 1                               # second run served from cache


def test_gap_filters_skip_trivial_files(tmp_path):
    (tmp_path / "p" / "src").mkdir(parents=True)
    (tmp_path / "p" / "src" / "__init__.py").write_text("")
    (tmp_path / "p" / "src" / "core.py").write_text("def run():\n    return 1\n")
    ws = _ws(tmp_path, ["p"])
    M, _, _ = build_workspace(ws)
    flagged = {g["label"] for g in find_gaps(M, ws)}
    assert "core.py" in flagged and "__init__.py" not in flagged


def test_backend_resolution_order(monkeypatch):
    import pytest
    from work_graph.llm import resolve_backend

    monkeypatch.delenv("WORK_GRAPH_BACKEND", raising=False)
    assert resolve_backend(None, None, None) == "cc_cli"                      # default
    assert resolve_backend(None, None, "gemini") == "gemini"                  # processor value
    assert resolve_backend(None, "ollama", "gemini") == "ollama"              # project beats processor
    monkeypatch.setenv("WORK_GRAPH_BACKEND", "bedrock")
    assert resolve_backend(None, "ollama", "gemini") == "bedrock"             # env beats config files
    assert resolve_backend("claude-cli", "ollama", "gemini") == "claude-cli"  # explicit beats env
    with pytest.raises(ValueError, match="unknown LLM backend"):
        resolve_backend("nope")
