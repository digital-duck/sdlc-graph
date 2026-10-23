import pytest

from workgraph.domain import Domain, available_domains


def test_all_packs_load_and_are_consistent():
    assert {"sdlc", "research", "publishing"} <= set(available_domains())
    for name in available_domains():
        d = Domain.load(name)
        assert d.stages and d.flow_relations and d.processors


def test_classify_multi_tag_and_default():
    d = Domain.load("sdlc")
    assert d.classify("tests/test_x.py") == ["test"]
    assert d.classify("src/app.py") == ["build"]
    assert d.classify("docs/design/specs/api.md") == ["requirement", "design"]
    assert Domain.load("research").classify("scripts/run.py") == ["experiment"]  # code is an experiment there


def test_stage_tags_are_constrained():
    d = Domain.load("sdlc")
    assert d.validate_stages(["build", "test", "build"]) == ["build", "test"]
    with pytest.raises(ValueError, match="unknown stage"):
        d.validate_stages(["hypothesis"])  # a research stage is not an sdlc stage


def test_classify_real_world_conventions():
    d = Domain.load("sdlc")
    assert d.classify("docs/DEV/readme-roadmap.md") == ["requirement", "design"]   # multi-tag
    assert d.classify("DESIGN.md") == ["design"]
    assert d.classify("CLAUDE.md") == ["design"]
    assert d.classify("docs/GUIDE/Tutorial.md") == ["release"]
    assert d.classify("src/App.js") == ["build"]
    assert d.classify("scripts/test_gen.sh") == ["test"]


def test_validate_pack_accepts_every_shipped_pack():
    from workgraph.domain import validate_pack
    for name in available_domains():
        assert validate_pack(name) == [], name


def test_validate_pack_reports_every_problem(tmp_path):
    from workgraph.domain import validate_pack
    bad = tmp_path / "bad.yaml"
    bad.write_text("""
domain: demo
stages:
  - {id: a, next: [b, zzz]}
  - {id: b}
  - {id: b}
flow_relations:
  - {from: a, to: nope, relation: feeds}
expectations:
  - {stage: a, needs: missing, message: m}
classifiers:
  - {stage: a}
  - {stage: q, default: true}
processors:
  - {name: x, use: telepathy}
""", encoding="utf-8")
    errs = "\n".join(validate_pack(bad))
    for needle in ("unknown stage 'zzz'", "duplicate stage id 'b'", "unknown stage 'nope'",
                   "`needs` 'missing'", "non-empty `globs`", "unknown stage 'q'", "unknown `use` 'telepathy'"):
        assert needle in errs, needle
    assert validate_pack(tmp_path / "absent.yaml")[0].startswith("pack not found")
    broken = tmp_path / "broken.yaml"
    broken.write_text("domain: [", encoding="utf-8")
    assert validate_pack(broken)[0].startswith("invalid YAML")


def test_validate_pack_cli_exit_code(tmp_path, capsys):
    from workgraph import cli
    ok = ["sdlc"]
    bad = tmp_path / "p.yaml"
    bad.write_text("stages: []\n", encoding="utf-8")
    cli.cmd_validate_pack(type("A", (), {"pack": ok})())
    assert "[ok] sdlc" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.cmd_validate_pack(type("A", (), {"pack": [str(bad)]})())
    assert "[!!]" in capsys.readouterr().out
