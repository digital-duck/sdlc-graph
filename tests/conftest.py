import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never touch the real ~/workgraph from tests."""
    monkeypatch.setenv("WORKGRAPH_HOME", str(tmp_path / "wg-home"))
    monkeypatch.delenv("WORKGRAPH_CACHE", raising=False)
