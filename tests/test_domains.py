import pytest

from sdlc_graph.domain import Domain, available_domains


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
