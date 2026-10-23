# Design: layered graphs, references, dual formats

Design for the multi-graph prototype described in the Vision section of the [README](../../README.md). It extends the existing repo and process layers with product and org layers, graph references, JSON/YAML dual storage and a comment store. The wider context is in `zinets/README-graph-vision.md`.

This document was first written before the build. Where the build differs, the code wins and this file should be updated.

## Status

Legend: **built** = implemented and covered by tests or a live run; **partial** = works with a stated limit; **not built** = planned. Verification says how far it was checked, so "built" does not hide an untried path. 52 automated tests pass in a clean venv. In the conda base environment 50 pass and the UI test module is skipped, because that NumPy and pyarrow pair is broken.

| Area | Feature | Status | Verification and limits |
|---|---|---|---|
| Core | Repo graph from projects (tree-sitter code, markdown frontmatter, annotations, derived `verifies`) | built | tests; run on `acme-shop` and a 113-file real app |
| Core | Domain packs `sdlc`, `research`, `publishing`; gap rules; broken-reference reports | built | tests |
| Core | Cross-project `identical_to` / `variant_of` links (`clones`) | built | test |
| Core | Reload of a built graph for queries (`graph.json`) | built | `query.load_graph`; rebuilding the build pipeline from a saved graph is not needed |
| LLM | `cc_cli` backend (Claude Code subscription, API variables stripped); `llm_items` extraction with caching | built | unit tests; live run: 4 documents, 26 items, about 5 minutes |
| LLM | Other backends (`bedrock`, `claude`, `gemini`, ...) through graphify | partial | wiring and readiness checks only; not exercised here |
| Layers | Layer files: schema, validation, referential integrity (`check`) | built | tests |
| Layers | Product, org and process layers; cross-layer relations; pack-derived `process:<domain>` templates | built | tests; examples in `examples/graphs/` |
| Layers | Sub-graph references (`/**`) | built | test |
| Layers | Layer graph: layers are nodes, layer dependencies are edges, coarseness is relative (section 15) | built | `layers.py`; tests; `workgraph utils layers` |
| Layers | Per-layer partitions in `out/layers/` and scoped loading, so a view loads only the layers it needs | built | tests check that partitions add up to `graph.json` and that a view loads only the partitions it needs. Search and the older tabs still load the whole `graph.json` |
| UI | Layers tab: parent canvas and child canvas (pick a node above, see what the finer layer holds under it below) | built | headless app tests; **not looked at in a browser** |
| Packs | `utils validate-pack` lists every problem in a domain pack | built | tests; all shipped packs pass |
| Layers | `uid` rename hints; `check --assign-uids` | built | test; hints depend on the store having seen the old id |
| Layers | Process instances (a product's status within a process template) | not built | only templates and the `follows` link exist |
| Layers | Per-layer vocabulary as YAML packs | not built | kinds and relations are Python constants in `schema.py` |
| Formats | JSON <-> YAML converters (`convert`) | built | round-trip test |
| Formats | Comment store: anchored comments restored on JSON -> YAML; orphans kept | built | tests; comments inside hand-written flow-style items attach to the whole item; renaming a node id orphans its comments |
| Home | Home folder `~/workgraph` (`WORKGRAPH_HOME`), `init`, `track`, default workspace; `custom/` for user-authored files (`custom/graphs/`), with an explanatory README and automatic move of the old `graphs/` | built | tests |
| Home | `--src <folder or git URL>` ad-hoc builds, shallow clones into `<home>/src` | built | tests (clone mocked); live clone of this repo over https |
| Home | graphify AST cache moved into `<home>/cache/ast` | built | output compared byte-for-byte before and after |
| UX | Build progress on stderr (per project, per slow step, per LLM document) and a closing timing table; `--quiet` / `WORKGRAPH_QUIET` | built | tests. First use found that file scanning, not parsing, dominated a 113-file project, which led to the next row |
| Perf | File scan reuses graphify's per-folder ignore cache | built | conceptbook-app: scan 34.7s to 6.0s, identical file list (11,260 files) and identical graph |
| Search | `status`, `show`, `search` (keyword), `context` | built | tests |
| Search | Semantic search: Ollama embeddings (cached) | built | tests with a fake server; live with `nomic-embed-text` |
| Search | Semantic search: sentence-transformers | built | live in the `graf` env (MiniLM, CPU by default because the pip-chosen PyTorch lacked kernels for a GTX 1080 Ti); not covered by automated tests, since the package is optional |
| Search | TF-IDF fallback, labelled as lexical | built | tests |
| UI | Streamlit app (`workgraph ui`): overview, search, browse, gaps, node panel | built | headless app tests and a server health check; not looked at in a browser by the author |
| Claude Code | Hooks `session-start` / `session-end`: track project, journal, context summary, background rebuild | built | tests with simulated payloads; headless Claude sessions |
| Claude Code | Plugin (`plugin/`) and the `/workgraph` command | built, **held back** | `claude plugin validate`; command run in real headless sessions via `--plugin-dir` and as a project command. Deliberately not published: no marketplace file in the repo (the draft is `docs/DEV/marketplace.draft.json`) until the owner finishes testing |
| Claude Code | `claude-setup --write` (hooks and the plain `/workgraph` command into `~/.claude`) | built | tests on temporary files; never run against the real `~/.claude` |
| Claude Code | Memory files as `memory` nodes (processor `claude_memory`) | built | test; live: a project's 9 memory files found by `search` |
| Claude Code | `import-claude`: track the projects Claude Code worked in; session metadata (and optional titles) into the journal | built | tests; live on this machine in a scratch home: 42 folders, 65 sessions. Dry run by default; message text is never stored |
| Privacy | Database renamed `workgraph.sqlite3` and kept at the top of the home folder, away from every synced or committed folder; `*.sqlite*`/`*.db` git-ignored | built | tests; old `graphs/store.sqlite` moves over automatically |
| Privacy | Guards on export and push: database files, files over 5 MB and secret patterns stop the operation; org layer, sessions and LLM cache are opt-in | built | tests |
| Sync | `dump` / `load` tar.gz (USB, first setup) with safe extraction | built | tests; live between two scratch homes |
| Sync | `sync init/push/pull/status` through a private git repository, 3-way file merge that never overwrites local work, JSONL for comments, uid history and sessions | built | tests with a local bare repo; live between two scratch homes. Not run against GitHub: the privacy check through `gh` is untested |
| Sync | Portable `workspace.yaml` (repo URLs, no local folders); `track` binds a folder; `sync pull --clone-missing` | built | tests |
| Server | A shared server for teams or enterprises (accounts, access control, concurrent writers) | not built | out of scope: workgraph is a personal assistant; a server design comes if it grows into a team product |
| Query | `ask`: graph-grounded LLM answers with cited nodes | not built | `/workgraph <question>` approximates it by letting Claude drive `search` and `show` |
| Query | MCP server so assistants call search/show as tools | not built | the `mcp` extra is declared but unused |
| Query | Impact analysis; traceability report; whole-graph interactive visualization | not built | only a one-hop neighbourhood diagram exists |
| Ingest | Git history (commits, issues); watch mode; session records as graph nodes | not built | sessions are kept in a journal table, not in the graph |
| Review | LLM-proposed edges with accept/reject | not built | LLM items stay `INFERRED` and are excluded from gap rules |
| Fit | Mapping to the ZiNets `primitives / concepts / applications` `graph.yaml` | not built | open question 14 |

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

One file per layer, kept together in a single directory: `<home>/custom/graphs/` for the default workspace, or a `graphs/` folder next to a workspace file (the `graphs:` key overrides). The `custom/` folder is the place for everything the user authors, and has room for future things such as user-written domain packs:

```
examples/graphs/
  org.json        org.yaml
  product.json    product.yaml
```

JSON form (canonical):

```json
{
  "schema": "workgraph/1",
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

JSON is canonical and is what the loader reads. YAML exists for review. `workgraph utils convert <file>` converts by extension: `.yaml` to `.json`, `.json` to `.yaml`. The YAML writer is `ruamel.yaml`, with a fixed layout (block style, `id` first, a fixed key order, sequences indented under their key) so that the comment store can find items reliably.

Rules:

- The conversion is lossless for graph content. Converting there and back gives the same JSON.
- A YAML file newer than its JSON is reported by `build`/`check` as "run convert", because the loader reads JSON only.
- Hand-written flow style (`{a: 1}`) in YAML is accepted when reading. It is normalized on the next write.

## 6. Comment store (`store.py`)

YAML comments have no JSON equivalent. The SQLite database (`<home>/workgraph.sqlite3`, at the top of the home folder and never inside `custom/` or `sync/`) keeps them:

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

Fourteen top-level commands, listed alphabetically in `--help`; the helpers sit under `utils` so the list stays short.

| Command | Does |
|---|---|
| `build`, `gaps`, `open` | the repo graph from a workspace file, the default workspace, or `--src` folders and URLs; `build` also merges the layer files |
| `init`, `track PATH` | create the home folder; add a project to the default workspace |
| `status`, `search`, `show` | query the last built graph (`search --semantic` for embeddings) |
| `ui` | Streamlit app |
| `import-claude` | backfill from Claude Code |
| `dump`, `load` | pack the authored data into a tar.gz / merge one (formerly `export` / `import`) |
| `sync init|push|pull|status` | share through a private git repository |
| `utils check` | validate layer files and references, report renames, stale YAML, missing uids (`--assign-uids` writes uids) |
| `utils convert FILE` | JSON <-> YAML with the comment store |
| `utils claude-setup [--write]` | Claude Code hooks and the plain `/workgraph` command |
| `utils clones`, `utils context`, `utils doctor`, `utils domains`, `utils show-llm` | shared files, project context, environment check, list packs, backend readiness (formerly `llm`) |
| `hook EVENT` | hidden top-level: the Claude Code hook handler. It stays out of the help list and keeps its name because installed hooks call it |

## 8. Module map

```
src/workgraph/
  schema.py        # layers, kinds, relation rules, validate_layer()
  refs.py          # load layer files, expand /** references, merge into the build graph, check integrity
  convert.py       # JSON <-> YAML, comment harvest and re-attach
  store.py         # SQLite: registry, comments, uid history, session journal, sync hashes
  syncing.py       # sync tree: export/import tar.gz, git sync, privacy guards
  claude_import.py # Claude Code memory processor and import-claude
  paths.py         # home folder layout
  sources.py       # --src folders and URLs
  tracking.py      # init, track
  claude_code.py   # hooks, context summary, settings and command installation
  query.py         # load, keyword and semantic search, neighbourhoods, gaps
  ui/streamlit/    # Streamlit app (one folder per UI framework under ui/, so others can be added)
  data/            # packaged copy of the /workgraph command (kept identical to plugin/commands/workgraph.md)
plugin/            # Claude Code plugin (held back; draft marketplace file in docs/DEV/)
```

`pipeline.build_workspace` calls `refs.merge_layers` after the repo graph is built and clustered. Clustering and gap analysis are unchanged: layer nodes are added after clustering, and gap analysis skips nodes that do not belong to a domain pack.

## 9. Tests (the original plan; the suite has since grown to 41 tests)

- schema: valid file passes, each rule has a failing case
- refs: `/**` expansion, broken reference, rename hint, pack-derived `process:` nodes, cross-layer merge into the example workspace
- convert: JSON to YAML to JSON round trip is identical
- store: comments survive YAML to JSON to YAML, an added comment is not lost on re-conversion, an orphan is kept and reported

## 10. Home folder, sources and Claude Code (added after the first build)

- **Home folder.** Because the tool is pip-installed, data does not live in the repo: `$WORKGRAPH_HOME` (default `~/workgraph`) holds `workspace.yaml`, `graphs/` (layer files and `store.sqlite`), `out/graph.json`, `cache/` and `src/`. A workspace file's own `graphs/` folder (as in `examples/`) takes precedence over the home one. graphify's AST cache is redirected into `cache/ast/<project>` so neither projects nor the working directory are written to.
- **`--src <URL or path>`.** Builds an in-memory workspace (one project per source, default domain `sdlc`). URLs (`https://`, `ssh://`, `git://`, `git@host:path`) are shallow-cloned into `src/<name>` and fast-forwarded on later runs. Nothing from the clone is executed.
- **Claude Code.** `SessionStart` and `SessionEnd` hooks call `workgraph hook <event>`. Start tracks the project (git root of the session's cwd, never the home folder), logs the session in the `sessions` table and prints a summary for the session context. End records branch and changed files and starts a detached background build when something changed. Handlers swallow all errors. Installing the hooks into `~/.claude/settings.json` happens only through `claude-setup --write`.
- **Not done.** An MCP server that lets Claude query the graph on demand (the `mcp` extra is declared but unused), writing session records into the graph as nodes instead of a journal table, and per-project domain choice when auto-tracking (new projects default to `sdlc`).

## 11. Search, UI and plugin

- **Search** (`query.py`). One document per non-structural node: label, kind, stages, project, path, summary and the first 1,500 characters of its file. Keyword search is a small weighted scorer (label > path/id > summary > body, idf, prefix matches, quoted phrases). Semantic search is `auto` = ollama embeddings, else sentence-transformers, else TF-IDF; the fallback is reported as lexical, never presented as meaning. Embeddings are cached by content hash in `<home>/cache/embeddings`.
- **UI.** Streamlit app launched by `workgraph ui`: Overview, Search, Browse, Gaps, and a node panel (attributes, links, Graphviz neighbourhood). It reads `graph.json` only.
- **Plugin.** `plugin/` carries the two hooks and the `/workgraph` command; A marketplace file would let `/plugin marketplace add digital-duck/work-graph` find it, so it is **withheld** (draft in `docs/DEV/marketplace.draft.json`) until testing is done. Plugin commands are namespaced by Claude Code, so the plain `/workgraph` is installed as a user command by `claude-setup --write`. Use one route or the other.
- **Not done.** An MCP server (Claude calling `search`/`show` as tools instead of through Bash), an interactive whole-graph visualization, and saving search results as reviewed links.

## 12. Ingesting existing Claude Code memory and history (built)

Only work done after the hooks are installed is tracked, so the work already on the machine needed a backfill. Claude Code keeps it under `~/.claude/projects/<slug>/`, where `<slug>` is the project path with every non-alphanumeric character turned into `-` (on the author's machine: 55 folders, up to 29 memory files each, 75 transcripts). Everything is read-only on `~/.claude`.

| Source | Becomes | How |
|---|---|---|
| `memory/*.md` (not `MEMORY.md`, the index) | one `memory` node per file, with `memory_type` (`user`, `feedback`, `project`, `reference`) and the file's `description` as its summary | the `claude_memory` processor, in every domain pack. It runs on every build for each tracked project, so new memories arrive with no import step. Memory nodes are untagged (not a lifecycle stage), so gap rules ignore them, and their text is searchable like any document |
| `<session>.jsonl` | one row in the session journal: id, project, start and end, branch, files touched by Edit/Write calls, and (with `--with-titles`) Claude's own one-line title | `workgraph import-claude --apply`. **Message text is never stored.** Re-running replaces rows by session id |
| project `CLAUDE.md` | already a design document inside the project | nothing new |

`workgraph import-claude` is a dry run unless `--apply` is given, because applying edits the default workspace. It resolves each slug to a folder (the `cwd` recorded in a transcript, else matching the dash-joined names against the filesystem), then decides per folder: track, already tracked, or skip (folder gone, home or `/tmp`, or nested inside another project unless it is its own git repository or `--include-nested` is given). Because slugs lose information, a folder that cannot be found is listed rather than guessed.

Cautions: transcript and memory formats belong to Claude Code and can change, so parsing is defensive and a failed file is skipped. Memory text is private. It enters the graph (and `search`), but it is **not** part of the sync tree: memory is read from each machine's own `~/.claude`.

## 13. Moving a workgraph between machines (built)

Shared storage is a **private GitHub repository** only; Google Drive, iCloud and similar sync folders are not used, because a database copied mid-write can corrupt and conflicts become duplicate files. A tar.gz carries the *first* setup (for example by USB), and git carries the increments. Sharing a graph with a team or an enterprise would need a server and is out of scope for this personal assistant.

The useful split is **what can be rebuilt** and **what cannot**:

| Content | Rebuildable? | Synced? |
|---|---|---|
| Repo graph (`out/`), AST cache, embeddings, `src/` clones | yes, one `build` | never |
| Layer files (`custom/graphs/`) | no, authored | yes (`graphs` part, stored as `graphs/` in the sync tree). The **org** layer lists people and is **opt-in** (`--with org`) |
| YAML comments, uid history | no | yes, as `store/comments.jsonl` and `store/node_ids.jsonl` (`comments` part) |
| Session journal | no | opt-in (`--with sessions`): it lists changed file names |
| LLM extraction cache | only by spending subscription time again | opt-in (`--with llm-cache`) |
| `workspace.yaml` | partly | yes, **portable**: names, `repo` URLs, domains; no local folders |
| The database `workgraph.sqlite3` | n/a | **never**. It is not inside any synced folder |

**The sync tree.** `export_tree` writes text only: `manifest.json`, `workspace.yaml`, `graphs/`, `store/*.jsonl` and `cache/llm/`. Both routes use it. `export -o FILE.tar.gz` packs it; `sync push` writes it into `<home>/sync/` (a git working tree) and commits. `import FILE` and `sync pull` apply it with `import_tree`.

**Privacy by construction, not by care.**
- The database cannot be committed: it is outside `<home>/sync/`, the repo's `.gitignore` is an allowlist (`*` then explicit `!` entries), and `*.sqlite*`, `*.db` are ignored in this repository too.
- Every export and push passes `guard_tree`: database files, files over 5 MB and secret patterns (Anthropic, AWS, GitHub, Slack and Google keys, private keys) stop the operation.
- `sync init` refuses a repository it cannot show to be private: GitHub is checked with `gh api repos/OWNER/REPO --jq .private` (a public repository is refused); anything else needs `--confirm-private`.
- Credentials embedded in a remote URL are stripped before it is written into `workspace.yaml`.
- Archive import accepts only plain files and folders, rejects absolute or `..` paths and links, caps the size, and scans the content with the same guard.

**Applying a tree never destroys local work.** For each layer file the database remembers the hash as of the last sync. Unchanged here, changed there: take theirs. Unchanged there: keep ours. Changed on both sides (or never synced before and different): keep ours and write theirs as `<name>.incoming.<ext>` for you to merge by hand, and report it. Comments, uid records and sessions merge as unions. A comment deleted on one machine therefore comes back from the other.

**A project without a folder.** After an import, a project from the workspace may have no folder on this machine. It is listed as unresolved and skipped by builds. `workgraph track <folder>` binds it (matching by name or repo URL), and `sync pull --clone-missing` clones it into `<home>/src`.

**Workflow.**
```
machine A:  workgraph dump -o workgraph.tar.gz          # USB stick, once
machine B:  workgraph load workgraph.tar.gz
both:       workgraph sync init git@github.com:you/my-workgraph.git     # a PRIVATE repository
then:       workgraph sync push   /   workgraph sync pull [--clone-missing]   /   workgraph sync status
```

Limits: `sync pull` uses a fast-forward-only git pull, so if the repository's history has diverged you resolve it with git in `<home>/sync/`. The LLM cache is keyed by backend and prompt, so a machine with a different backend re-extracts once. The `gh` privacy check has not been run against GitHub itself.

## 14. Open questions

- Whether `uid` should be assigned automatically on first `convert` instead of through `check --assign-uids`.
- Per-layer packs: the layer vocabulary is Python constants for now. Moving it to YAML packs would match the domain-pack approach.
- Process instances (status of a product moving through a template).
- Whether comments should sync as deletions as well as additions (today a deleted comment returns from the other machine).
- Whether a team or enterprise mode is wanted at all, which would mean a server with accounts and access control rather than files.
- Relationship to the `primitives / concepts / applications` three-layer `graph.yaml` of the ZiNets ecosystem.

## 15. The layered graph: layers as nodes (built)

Today the layers (repo, product, org, process) are a fixed list of names in `schema.py`, and everything lives in one in-memory graph (`graph.json`). That works for the examples and will not for a workspace with tens of thousands of files and symbols, where the UI would draw one hairball. The next step turns the layers themselves into data.

### Idea

Two graphs, as in a network protocol stack, where each layer talks to its neighbours through a defined interface and nobody hard-codes "the top" or "the bottom":

- The **layer graph** is small and always in memory. Its **nodes are layers**; its **edges are layer dependencies** (which layer's nodes may point at which layer's nodes, and by what relation).
- The **instance graph** is the knowledge graph as built today. Every node already carries a `layer` attribute naming a node of the layer graph.

Terminology: this is a *layered* graph. We do not say macro/micro or levels 0 to N, because those numbers are fixed by the author, while here only the **relative** order matters: a layer is *coarser* than the layers that point at it, and *finer* than the layers it points at.

### The layer graph

Derived from the vocabulary in `schema.py` (single source of truth), not written twice:

| Layer-graph element | Derived from | Example |
|---|---|---|
| node (a layer) | `LAYER_KINDS` plus the implicit `repo` layer | `repo`, `product`, `org`, `process` |
| edge (dependency) | each cross-layer entry of `RELATION_RULES` | `repo` -`belongs_to`-> `product`; `product` -`owned_by`-> `org`; `product` -`follows`-> `process` |
| edge attribute `relation` | the relation name | `belongs_to` |
| node attribute `hierarchical` | the layer allows `part_of` (product, org, process) | true/false |
| node attribute `rank` (coarseness) | longest path from a layer nothing points at; finest layer is 0 | repo 0, product 1, org 2, process 2 |

`rank` is computed, never stored in a file. Adding a layer or a relation between two existing layers changes the ranks automatically. Equal ranks mean the layers are siblings, not ordered against each other (org and process).

### Partitioned build output

`build` keeps writing `graph.json` (search, the CLI and the existing tabs depend on it) and also writes a partitioned copy:

```
out/layers/
  layers.json      the layer graph, with node and edge counts per layer
  <layer>.json     nodes of that layer and the edges whose two ends are both in it
  cross.json       edges whose ends are in different layers
```

`layers.load_layer(name)` reads one partition. `layers.load_scope(layers)` merges the named partitions plus the `cross.json` edges between them. A view that needs a node's surroundings first asks the layer graph which layers can be adjacent, then loads only those. The repo layer is the big one, so a question about teams and products never touches it.

### Views

The visualization follows the layer graph instead of drawing one hairball. The Layers tab of `workgraph ui` has:

1. **The layer graph** (collapsed by default): layers with their node counts, coarse at the top, dependencies labelled with their relations and edge counts. Cross-layer edges that no declared dependency explains are listed under it.
2. **A parent canvas**: pick a parent layer (default: the layer just above the finest) and a child layer among the finer layers that point at it. The parent layer is drawn as a graph; a node picker selects one node, shown with a red ring.
3. **A child canvas below it**: what the child layer holds under that node, via `layers.below`: the child-layer nodes linked to the node or to anything inside it, plus what those contain (their own `part_of` descendants; for a project, every node of that project). For a product that is its repositories with their files, documents and relations. A checkbox adds symbols and code, which are hidden by default.
4. **Neighbours by coarseness** (expander): the node's links grouped as coarser, finer, same layer or beside.

Only the partitions a step needs are loaded: `below` reads the node's layer and the child layer, nothing else. The picture itself is the same bundled Cytoscape.js page the Map tab uses (`viz.cytoscape_html`, now with an optional ring for marked nodes). Limit: a click inside the canvas inspects a node but cannot change the selection in Streamlit, so the node is chosen with the picker.

`BUILD_RELATIONS` in `schema.py` names the relations the build adds across layers (`in_stage`, `participates_in`: repo to process). They are not allowed in layer files but are part of the layer graph, which is why repo shows a dependency on process as well as on product.

CLI: `workgraph utils layers` prints the layer graph with ranks and the counts of the last build. `workgraph viz --layer NAME` (existing) draws one layer.

### What this does not change

- `graph.json` stays the canonical built output, so nothing that reads it breaks.
- Layer files, ids, uids and conversion are unchanged.
- `part_of` still means containment inside one layer; a layer dependency is not `part_of`.

### Honest limits

- The build partitions after writing `graph.json`, and search and the older tabs still load the whole `graph.json`. The memory saving is therefore for the Layers tab only until search moves to the partitions.
- Opening a repo node loads the whole repo partition. Sharding the repo layer by project is the next step if that gets large.
- Not looked at in a real browser yet: the tests run the app headlessly.
- The repo layer has its own coarse-to-fine structure (project, file, symbol). It stays one layer for now; splitting it by kind into sub-layers is the obvious next use of the same mechanism.
- Per-layer vocabulary is still Python constants (open question in section 14).

### Tests

- layer graph derived from the vocabulary has the expected nodes, edges and ranks, and a new layer shifts ranks
- partitions written by a build contain each node exactly once, `cross.json` contains exactly the cross-layer edges, and merging all partitions gives back `graph.json`
- `load_scope` of two layers equals the induced subgraph of the full graph
- the Layers tab renders for the example workspace
