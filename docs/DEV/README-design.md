# Design: layered graphs, references, dual formats

Design for the multi-graph prototype described in the Vision section of the [README](../../README.md). It extends the existing repo and process layers with product and org layers, graph references, JSON/YAML dual storage and a comment store. The wider context is in `zinets/README-graph-vision.md`.

Status: this document was written before the build. Where the build differs, the code wins and this file should be updated.

## 1. Goals and non-goals

Goals:

1. Several graphs (layers) that reference each other instead of copying nodes.
2. References written briefly in the spec, always resolved in memory at run time.
3. JSON as the canonical machine format with validation and referential integrity. YAML as the human format for review and audit, with comments that are never lost.
4. Lossless conversion in both directions.

Non-goals for the prototype: a multi-user store, access control for the org layer, federation across separate stores, editing graphs through the LLM, and replacing the repo layer's graphify-derived IDs.

## 2. Layers

| Layer | Node kinds | Hierarchy (`part_of`, child to parent) | Source |
|---|---|---|---|
| `repo` | project, file, symbol, document, requirement, ... | built today by the pipeline | scanned from projects |
| `product` | `family`, `product`, `application` | family > product > application | layer file |
| `org` | `company`, `division`, `department`, `group`, `team`, `person` | company > ... > person | layer file |
| `process` | `process`, `step` | process > step, with `precedes` between steps | layer file, plus every domain pack |

Domain packs are process templates. For each pack in the workspace the loader adds a node `process:<domain>` and a `part_of` edge from each of its `stage:<domain>:<id>` nodes, so `follows: process:sdlc` resolves without any layer file. A process that is not a pack (for example stakeholder interview through feedback) can be written as a `process` layer file with `step` nodes and `precedes` edges.

Cross-layer relations:

| Relation | From | To | Meaning |
|---|---|---|---|
| `part_of` | any | same layer | containment, child to parent |
| `precedes` | process step | process step | ordering inside a process |
| `belongs_to` | repo project | product (any level) | the project builds that product |
| `owned_by` | product | org | which team or unit owns it |
| `follows` | product | process | which delivery process it uses |

Template versus instance: a process node is the template. A product's progress through it (status, owners, artifacts) will be a separate instance later. The prototype only stores templates and the `follows` link.

## 3. Node IDs

Decision: **readable path-style IDs**, `<layer>:<path>`, for example `org:acme/engineering/payments`, `product:acme/shop/checkout`, `process:sdlc`. References always use these IDs. There is no `aliases` field.

Renames are the weak point of path-style IDs, so each layer node also carries a short **`uid`** (8 hex characters, generated once, never edited and never used in references). It exists only so the tools can tell a rename from a deletion:

- The store remembers every `(uid, id)` pair it has seen.
- When a reference fails to resolve, the checker looks up the missing ID in that history. If the same `uid` now exists under a different ID, the error says `renamed to <new id>` and names the references to update. Otherwise it is reported as a plain broken reference.

So the ID stays readable and reviewable, and the uid turns "broken reference" into "renamed, here is the fix". The checker only reports renames. It never rewrites references on its own.

Repo-layer IDs are unchanged (graphify-derived, prefixed with the project name). The repo layer is addressable from other layers as `project:<name>`, which is already the project node ID.

An ID's prefix names its layer: `org:`, `product:`, `process:`, and `stage:` and `project:` for the process and repo nodes that already exist. Any other prefix is treated as a repo node.

## 4. Layer files

One file per layer, kept together in a single directory next to `workspace.yaml` (`graphs:` key, default `graphs/`):

```
examples/graphs/
  org.json        org.yaml
  product.json    product.yaml
  store.sqlite    # file registry + comments + uid history
```

JSON form (canonical):

```json
{
  "schema": "work-graph/1",
  "layer": "product",
  "nodes": [
    {"id": "product:acme/shop", "uid": "3fa9c2d1", "kind": "product", "label": "Shop"}
  ],
  "edges": [
    {"source": "product:acme/shop", "target": "org:acme/engineering/payments", "relation": "owned_by"}
  ]
}
```

The YAML form has the same content and key names. Edges default to `confidence: EXTRACTED`. Extra keys on nodes and edges are allowed and preserved.

### Validation (`schema.py`)

Applied to the JSON form, always before merging:

- top level has `schema`, `layer`, `nodes`, `edges`, and `layer` is a known layer
- every node has a string `id` whose prefix equals the file's layer, a `kind` allowed for that layer, and no duplicate IDs (within the file or across files)
- every edge has `source`, `target`, `relation`, and the relation and its endpoint layers match the table in section 2
- `part_of` edges form a forest (no cycles)
- `uid`, when present, is unique

The validator is hand-written, with no JSON Schema dependency. A real JSON Schema document can be generated from the same constants later if other tools need one.

### References and sub-graphs (`refs.py`)

Edge endpoints may name a node in any loaded layer. A **sub-graph reference** is an ID ending in `/**`, meaning that node plus all its descendants, found by following `part_of` edges downward. `org:acme/engineering/**` as an edge source expands to one edge per member.

Resolution happens at load time and produces one in-memory graph:

1. Load and validate every layer file.
2. Collect all nodes into one ID table, together with the repo graph and the pack-derived process nodes.
3. Expand sub-graph references.
4. Check every endpoint exists. Failures are errors, with the rename hint from section 3 when the store has the history.
5. Add nodes with a `layer` attribute and edges with their `relation` to the NetworkX graph.

Nothing downstream sees unresolved references, and the resolved graph is what `build` writes to `graph.json`.

## 5. Dual formats and conversion (`convert.py`)

JSON is canonical and is what the loader reads. YAML exists for review. `work-graph convert <file>` converts by extension: `.yaml` to `.json`, `.json` to `.yaml`. The YAML writer is `ruamel.yaml`, with a fixed layout (block style, `id` first, a fixed key order, sequences indented under their key) so that the comment store can find items reliably.

Rules:

- The conversion is lossless for graph content. Converting there and back gives the same JSON.
- A YAML file newer than its JSON is reported by `build`/`check` as "run convert", because the loader reads JSON only.
- Hand-written flow style (`{a: 1}`) in YAML is accepted when reading. It is normalized on the next write.

## 6. Comment store (`store.py`)

YAML comments have no JSON equivalent. The SQLite store (`<graphs dir>/store.sqlite`) keeps them:

| Table | Columns | Purpose |
|---|---|---|
| `files` | path, layer, json_sha256, yaml_sha256, updated_at | registry of tracked graph files and their pairs |
| `comments` | file, item_key, key, position, text | the comments |
| `node_ids` | uid, id, layer, last_seen | uid history for rename detection |

Comment anchors do not use line numbers, so they survive reordering and reformatting:

- `item_key`: the node ID, the edge as `source|relation|target`, or `@section:<name>` / `@file` for comments outside any item
- `key`: the key on the commented line, or none for a comment on the item as a whole
- `position`: `before` (full-line comment above) or `trailing` (comment at the end of a line)

Flow:

- **YAML to JSON.** Harvest the comments from the YAML text into the store, replacing that file's previous set, then write the JSON.
- **JSON to YAML.** If the YAML file exists, harvest it first, so comments typed since the last conversion are never overwritten. Write the new YAML, then re-attach the stored comments by anchor.
- **Orphans.** A comment whose item no longer exists in the JSON is not dropped. It is written at the end of the YAML as `# [orphaned from <item_key>] <text>`, and the conversion reports the count. The next harvest reads those lines back as file-level comments, so nothing is lost.

Limitations of the prototype: comments are matched at item level plus one key. Comments inside hand-written flow-style items attach to the item as a whole. Reordering a node is safe. Renaming a node's `id` makes its comments orphans until someone moves them.

## 7. Commands

| Command | Does |
|---|---|
| `work-graph build -c workspace.yaml` | existing build, now also loads and merges the layer files (errors become warnings in the output) |
| `work-graph check -c workspace.yaml` | validate layer files and references, report renames, stale YAML, missing uids. `--assign-uids` writes uids into JSON files that lack them |
| `work-graph convert <file> [-o OUT] [--db PATH]` | JSON to YAML or YAML to JSON with the comment store |

## 8. Module map

```
src/work_graph/
  schema.py    # layers, kinds, relation rules, validate_layer()
  refs.py      # load layer files, expand /** references, merge into the build graph, check integrity
  convert.py   # JSON <-> YAML, comment harvest and re-attach
  store.py     # SQLite: registry, comments, uid history
```

`pipeline.build_workspace` calls `refs.merge_layers` after the repo graph is built and clustered. Clustering and gap analysis are unchanged: layer nodes are added after clustering, and gap analysis skips nodes that do not belong to a domain pack.

## 9. Tests

- schema: valid file passes, each rule has a failing case
- refs: `/**` expansion, broken reference, rename hint, pack-derived `process:` nodes, cross-layer merge into the example workspace
- convert: JSON to YAML to JSON round trip is identical
- store: comments survive YAML to JSON to YAML, an added comment is not lost on re-conversion, an orphan is kept and reported

## 10. Open questions

- Whether `uid` should be assigned automatically on first `convert` instead of through `check --assign-uids`.
- Per-layer packs: the layer vocabulary is Python constants for now. Moving it to YAML packs would match the domain-pack approach.
- Process instances (status of a product moving through a template).
- Relationship to the `primitives / concepts / applications` three-layer `graph.yaml` of the ZiNets ecosystem.
