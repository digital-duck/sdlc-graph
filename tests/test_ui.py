from pathlib import Path

import pytest

pytest.importorskip("streamlit")
try:                                    # a conda base with NumPy 2 and an old pyarrow breaks Streamlit's tables
    import pandas  # noqa: F401
    import pyarrow  # noqa: F401
except Exception:
    pytest.skip("pandas/pyarrow unusable in this environment", allow_module_level=True)

from streamlit.testing.v1 import AppTest

from workgraph import paths, query
from workgraph.cli import main

APP = Path(__file__).parent.parent / "src" / "workgraph" / "ui" / "streamlit" / "app.py"
EX = Path(__file__).parent.parent / "examples"


def test_app_overview_keyword_and_semantic_search(monkeypatch):
    main(["build", "-c", str(EX / "workspace.yaml")])
    monkeypatch.setattr(query, "ollama_ready", lambda: (False, "ollama is not running"))
    monkeypatch.setattr(query, "sbert_ready", lambda: (False, "no sbert"))
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert dict((m.label, m.value) for m in at.metric)["Nodes"] == "57"
    at.text_input[0].set_value("checkout").run()
    assert not at.exception and any("results" in c.value for c in at.caption)
    at.radio[0].set_value("Semantic").run()
    assert not at.exception and any("TF-IDF" in c.value for c in at.caption)
    assert any("edges." in c.value and "workgraph viz --engine" in c.value for c in at.caption)     # the Map tab drew a graph


def test_app_without_a_graph_shows_a_message():
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception and any("workgraph build" in e.value for e in at.error)


def test_layers_tab_shows_parent_and_child_canvases(monkeypatch):
    main(["build", "-c", str(EX / "workspace.yaml")])
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    box = {s.label: s for s in at.selectbox}
    assert {"Parent layer", "Child layer", "Node in the parent layer"} <= set(box)
    assert box["Parent layer"].value == "product" and box["Child layer"].value == "repo"     # the layer just above the finest
    assert any(m.value.startswith("**Parent: `product`**") for m in at.markdown)
    assert any(m.value.startswith("**Child: `repo` under ") and "loaded `product` and `repo` only" in m.value
               for m in at.markdown)
    box["Parent layer"].select("org").run()
    assert not at.exception and any(m.value.startswith("**Parent: `org`**") for m in at.markdown)
    assert any(m.value.startswith("**Child: `product` under ") for m in at.markdown)


def test_layers_tab_offers_to_create_missing_partitions(monkeypatch):
    main(["build", "-c", str(EX / "workspace.yaml")])
    import shutil
    shutil.rmtree(paths.out_dir() / "layers")
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception and any(b.label == "Create layer partitions from this graph" for b in at.button)
    next(b for b in at.button if b.label.startswith("Create layer")).click().run()
    assert not at.exception and (paths.out_dir() / "layers" / "layers.json").exists()
