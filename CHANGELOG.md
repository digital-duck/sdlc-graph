# Changelog

## 0.2.0

- `workgraph viz` draws through [zinet](https://pypi.org/project/zinet/) (>=0.2.0): the same engines as GraphLang (`cytoscape`, `graphviz`, `mermaid`, `ascii`, and `pyvis` / `graphify`), hover highlighting on the interactive page; the Map tab uses the same page. `viz.py` is now a style plus selection rules.
- `convert.py` uses zinet's comment-preserving YAML (`zinet.roundtrip`) with workgraph's `nodes` / `edges` layout.
- New dependency: `zinet>=0.2.0`. The extra `viz` is now `zinet[pyvis]`.

## 0.1.0

First release.
