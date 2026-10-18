# Tutorial: the SDLC process in a workgraph

A hands-on walk through workgraph, a knowledge layer for your software work that sits beside Claude Code. It builds a graph of your projects, their lifecycle, products, teams and processes, and gives Claude Code (and you) a persistent memory of it. The walk uses the `acme-shop` example and the software development lifecycle (SDLC) as the process, then a real codebase, then Claude Code itself. About 40 minutes. The output shown was produced by running the commands in order against the shipped examples, and is abridged where marked. Later steps build on earlier ones (step 3 adds a test file, for example, and the counts in steps 4 and 8 include it), so your numbers match only if you follow the order. Search results from the LLM and embedding steps can differ on your machine.

You will:

1. see the SDLC as a process template
2. build the repo graph and trace one requirement through the lifecycle
3. find the gaps in the process and close them
4. connect the repo to a product, a team and a process (the layered graphs)
5. review a graph file as YAML and keep your comments
6. query the whole thing from Python
7. ingest a second, real codebase (`--src`) and add the LLM pass on your Claude subscription
8. search it (keyword and semantic) and review it in the Streamlit app
9. let Claude Code feed the graph automatically, and query it with `/workgraph`
10. backfill existing Claude Code work, and move the graph to another machine safely

Install first with [SETUP.md](SETUP.md) (the structural build needs no LLM and no API key). Then work in a scratch copy of the examples, with a scratch workgraph home, so neither the examples nor your real `~/workgraph` are touched:

```bash
export WORKGRAPH_HOME=/tmp/tutorial-home     # where graph.json, caches and clones go; default is ~/workgraph
cp -r examples /tmp/tutorial && cd /tmp/tutorial
```

## 1. The SDLC is a process template

```bash
workgraph domains
```

```
publishing  Publishing: idea → outline → draft → edit → publish → promote
research    Research: question → literature → hypothesis → experiment → analysis → writeup
sdlc        Software development lifecycle: requirement → design → build → test → release → operate
```

Each line is a **domain pack**, a YAML file in `src/workgraph/domains/`. The `sdlc` pack defines the stages, the legal links between them (`design --satisfies--> requirement`, `build --implements--> design`, `test --verifies--> requirement`), the gap rules ("every requirement needs a design and a test") and the path rules that decide which stage a file belongs to (`**/requirements/**` is a requirement, `**/adr/**` is a design, `**/tests/**` is a test). Open `src/workgraph/domains/sdlc.yaml` and read it once; everything below is driven by it.

SDLC is one process among many. A different process, such as research or publishing, is another pack. Section 4 adds a process that is not a pack at all.

## 2. Build the repo graph and trace a requirement

`examples/acme-shop` is a tiny shop with requirements, decisions, code and a test:

```
acme-shop/
  docs/requirements/REQ-001.md  REQ-002.md  REQ-003.md
  docs/adr/ADR-001.md  ADR-002.md
  docs/ROADMAP.md
  src/cart.py  src/checkout.py
  tests/test_cart.py
```

Build it:

```bash
workgraph build -c acme-shop/workspace.yaml
```

```
23 nodes, 65 edges, 5 communities
[sdlc]
  requirement  {'acme-shop': 4}
  design       {'acme-shop': 2}
  build        {'acme-shop': 2}
  test         {'acme-shop': 1}
  release      {}
  operate      {}
layers: process 7, repo 16
warning: docs/adr/ADR-002.md: broken reference `satisfies: REQ-099`
wrote /tmp/tutorial-home/out/graph.json
```

The summary counts artifacts per lifecycle stage. `release` and `operate` are empty because this project has nothing there yet. Nothing was configured: the stage of each file came from its path.

Links come from three places, all visible in the example files:

| Where | Example | Meaning |
|---|---|---|
| frontmatter in markdown | `satisfies: [REQ-001]` in `ADR-001.md` | the decision satisfies the requirement |
| annotation comment in code | `# implements: ADR-001` at the top of `cart.py`, `# verifies: REQ-001` in `test_cart.py` | code implements a decision, a test verifies a requirement |
| derived from imports | `test_cart.py` imports `cart.py` | an `INFERRED` `verifies` edge, marked as less certain than the explicit ones |

So REQ-001 is traced end to end: requirement (`REQ-001.md`) ← design (`ADR-001.md`) ← code (`cart.py`) ← test (`test_cart.py`). Section 6 shows how to ask the graph for that chain.

The warning is a **broken reference**: `ADR-002.md` says it satisfies `REQ-099`, which does not exist. The graph reports it instead of silently dropping it.

## 3. Find the gaps and close them

```bash
workgraph gaps -c acme-shop/workspace.yaml
```

```
[sdlc] code file has no test (1)
  - checkout.py  (acme-shop:src/checkout.py)

[sdlc] requirement has no design (1)
  - REQ-003: Discount codes  (acme-shop:docs/requirements/REQ-003.md)

[sdlc] requirement has no test (2)
  - REQ-002: Customers can check out  (acme-shop:docs/requirements/REQ-002.md)
  - REQ-003: Discount codes  (acme-shop:docs/requirements/REQ-003.md)

[warnings] (1)
  - docs/adr/ADR-002.md: broken reference `satisfies: REQ-099`

4 gaps, 1 warnings
```

Each gap is a rule from the pack evaluated as a plain graph query: a node in the `requirement` stage with no incoming `verifies` edge. Close two of them by writing a test for checkout:

```bash
cat > acme-shop/tests/test_checkout.py <<'PY'
# verifies: REQ-002
from cart import Cart
from checkout import total


def test_total():
    c = Cart()
    c.add("a", 2)
    assert total(c, {"a": 5.0}) == 10.0
PY
workgraph gaps -c acme-shop/workspace.yaml
```

```
[sdlc] requirement has no design (1)
  - REQ-003: Discount codes  (acme-shop:docs/requirements/REQ-003.md)

[sdlc] requirement has no test (1)
  - REQ-003: Discount codes  (acme-shop:docs/requirements/REQ-003.md)

[warnings] (1)
  - docs/adr/ADR-002.md: broken reference `satisfies: REQ-099`

2 gaps, 1 warnings
```

The explicit `# verifies: REQ-002` covered the requirement, and the import of `checkout.py` covered the code file. The 4 gaps became 2. REQ-003 is the next one to work on: it needs a design note and a test. Add `satisfies: [REQ-003]` to a new decision under `docs/adr/` and watch it go. The broken reference stays until you fix `ADR-002.md`.

This is the process doing its job: the graph tells you what is missing at each stage, instead of someone having to remember it.

## 4. Layers: the repo, the product, the team and the process

So far the graph covers one repo. The `graphs/` directory next to `workspace.yaml` adds more layers, each a JSON file (the canonical form) with a YAML twin for review:

```
graphs/
  org.json      org.yaml        # Acme > Engineering > Commerce > Payments team > Dana
  product.json  product.yaml    # Retail products > Shop > Checkout service
  process.json  process.yaml    # stakeholder interview ... feedback
```

Layer nodes use readable IDs of the form `<layer>:<path>`, for example `org:acme/engineering/commerce/payments`. Read `graphs/product.yaml`. The part that links the layers is five edges:

```yaml
  - source: project:acme-shop                  # the repo graph (a project node)
    relation: belongs_to
    target: product:acme/retail/shop/checkout  # ... belongs to this application
  - source: product:acme/retail/shop
    relation: owned_by
    target: org:acme/engineering/commerce/payments
  - source: product:acme/retail/shop
    relation: follows
    target: process:sdlc                       # the SDLC pack, as a process template
  - source: product:acme/retail/shop
    relation: follows
    target: process:delivery                   # the stakeholder-to-feedback process
```

Nodes are defined once and referenced by ID from other layers, so the Payments team appears in the graph once and not once per repo or product.

Two processes are linked here. `process:sdlc` was not written by hand: every domain pack in the workspace becomes a process template, with its stages `part_of` it. `process:delivery` is a layer file, a process that is not a pack: eight steps (stakeholder interview, requirement spec, solution design, prototyping, build, test, release, feedback), each `part_of` the process and joined by `precedes`. A product can follow several processes.

Check that every reference resolves:

```bash
workgraph check -c workspace.yaml
```

```
3 layer files, 18 nodes, 27 edges; 0 errors, 0 notes
```

`check` validates each file's structure (allowed node kinds per layer, allowed relations between layers, no cycles in `part_of`) and then every cross-graph reference. Break one on purpose to see it: rename `org:acme/engineering/commerce/payments` in `graphs/org.json` (the node and its edges) and run `check` again. The product's `owned_by` edge now fails, and because each node carries a short `uid` that survives a rename, the message says the node was **renamed** and points at the new ID instead of just saying "broken". Undo the edit before continuing.

Now build everything:

```bash
workgraph build -c workspace.yaml
```

```
60 nodes, 143 edges, 8 communities
...
layers: org 6, process 23, product 3, repo 28
```

References are written briefly in the files, but at run time they are all resolved into **one** in-memory graph. Every node has a `layer`, so a single query can go from a function to the team that owns the product it belongs to.

A reference can also point at a whole sub-graph with a `/**` suffix: an edge from `product:acme/retail/**` means one edge from the node and each of its descendants.

## 5. Review as YAML, keep your comments

The JSON files are for machines (validation, tools). The YAML twins are for people. Say a reviewer wants to annotate the Shop product:

```bash
cd graphs
# open product.yaml and add a comment line above the Shop node, for example:
#   # flagship product, reviewed 2026-10 by Dana
#   - id: product:acme/retail/shop
workgraph convert product.yaml       # YAML -> JSON; the comment is saved in the database, $WORKGRAPH_HOME/workgraph.sqlite3
```

```
wrote product.json (1 comments saved)
```

JSON has no comments, so they are kept in a small SQLite store, anchored to the node's ID rather than to a line number. Prove it survives: delete the YAML and regenerate it from the JSON.

```bash
rm product.yaml
workgraph convert product.json       # JSON -> YAML; comments are restored from the store
sed -n 8,12p product.yaml
```

```
wrote product.yaml (1 comments restored)
  # flagship product, reviewed 2026-10 by Dana
  - id: product:acme/retail/shop
    uid: b6976f29
    kind: product
    label: Shop
```

If you edit a YAML file and forget to convert it, `check` and `build` tell you ("edited since the last convert"), because only the JSON is loaded. If a commented node is later deleted, its comment is not dropped: it moves to the end of the file as `# [orphaned from <id>] ...`.

## 6. Ask the graph questions

`build` writes `$WORKGRAPH_HOME/out/graph.json`, a NetworkX node-link file. Load it and walk the layers:

```python
import json
import networkx as nx

G = nx.node_link_graph(json.load(open("/tmp/tutorial-home/out/graph.json")), edges="edges")

def out(u, rel): return [v for _, v, d in G.out_edges(u, data=True) if d["relation"] == rel]
def into(v, rel): return [u for u, _, d in G.in_edges(v, data=True) if d["relation"] == rel]

# 1. the SDLC chain for REQ-001
req = next(n for n, d in G.nodes(data=True) if d.get("label", "").startswith("REQ-001"))
print(into(req, "satisfies"))   # ['acme-shop:doc_adr_001']   the design decision
print(into(req, "verifies"))    # ['acme-shop:tests_test_cart']   the test

# 2. from the repo up to the product, the team and the processes
product = out("project:acme-shop", "belongs_to")[0]   # 'product:acme/retail/shop/checkout'
print(out("product:acme/retail/shop", "owned_by"))    # ['org:acme/engineering/commerce/payments']
print(out("product:acme/retail/shop", "follows"))     # ['process:sdlc', 'process:delivery']
```

The first query is what the README means by *graph-grounded context*: the requirement, its design decision and its test come back as a connected set, without searching text. The second crosses three layers (repo, product, org) and two processes in a few lines. An AI assistant or coding agent can be handed exactly this sub-graph as its context before a task.

## 7. Ingest a second codebase: conceptbook-app

So far everything came from a workspace file and a toy project. workgraph can also ingest any folder or git URL directly with `--src`, with no workspace file. Here it is on a real codebase, `conceptbook-app` (a React and Python application). Replace the path with any repo you have.

```bash
workgraph build --src ~/projects/digital-duck/conceptbook-app
```

```
  AST extraction: 113/113 uncached files (100%) [8 workers]
1337 nodes, 5746 edges, 91 communities
[sdlc]
  requirement  {'conceptbook-app': 3}
  design       {'conceptbook-app': 15}
  build        {'conceptbook-app': 110}
  test         {'conceptbook-app': 4}
  release      {'conceptbook-app': 3}
  operate      {}
layers: process 7, repo 1330
wrote /tmp/tutorial-home/out/graph.json
```

That took about 40 seconds on the first run and used no LLM. The path rules in the `sdlc` pack sorted the files into stages (documents under `docs/adr` become design, `tests/` become test, and so on), tree-sitter extracted the code, and the project's `.gitignore` kept `node_modules` and similar out. The same works for a URL, which is shallow-cloned into `$WORKGRAPH_HOME/src` first:

```bash
workgraph gaps --src https://github.com/digital-duck/work-graph
```

The gap report for conceptbook-app is a useful reality check:

```bash
workgraph gaps --src ~/projects/digital-duck/conceptbook-app
```

```
[sdlc] code file has no test (73)
  - About.js  (conceptbook-app:src/pages/About.js)
  - BulkGenerateBar.js  (conceptbook-app:src/components/BulkGenerateBar.js)
  ...
73 gaps, 0 warnings
```

Real projects rarely annotate requirements, so the only rule that fires is the code-versus-test one. The graph found 4 test files and 73 code files without a test linked to them.

### Add the LLM pass with your Claude subscription

The planning documents (`DESIGN.md`, `docs/README-roadmap.md`, `docs/README-todo.md`, a `readme-plan.md`) hold the project's real requirements, decisions and open tasks in prose. The `cc_cli` adapter reads them through the Claude Code CLI **on your subscription**: it removes `ANTHROPIC_API_KEY` and similar variables from the environment of the call, so it never bills the API. Check it first:

```bash
workgraph llm --src ~/projects/digital-duck/conceptbook-app --adapter cc_cli
```

```
conceptbook-app/doc-items (llm_items) -> cc_cli   billing: subscription (Claude Code login; API-key env vars stripped)
    [ok] `claude` on PATH: /home/you/.local/bin/claude
    [ok] subscription login: claude.ai / pro
    [ok] billing env vars stripped for the call: ANTHROPIC_BASE_URL, CLAUDECODE, CLAUDE_CODE_ENTRYPOINT (set in this shell, removed for the call)
```

Then run it. It sends four documents, one call each, so expect a few minutes:

```bash
workgraph open --src ~/projects/digital-duck/conceptbook-app --semantic --adapter cc_cli
```

```
conceptbook-app:DESIGN.md (11 open)
  - [task/build] Move sessions to httpOnly cookie instead of bearer token for security
  - [requirement/design] Personalization: store API keys encrypted at rest
  - [decision/design] Open decision: choose hosting provider
  ...
conceptbook-app:docs/README-roadmap.md (4 open)
  - [requirement/requirement] Build reviewable moderator revision workflow for graph.yaml corrections/additions
  - [decision/design] Defer community review/promotion of Learn Anything domains
  ...
conceptbook-app:docs/README-todo.md (9 open)
  - [task/build] Manually review 12 files with malformed LaTeX delimiter typos
  - [task/release] Write TMLR paper on ConceptBook
  ...
conceptbook-app:docs/DEV/manage/readme-plan.md (2 open)
  - [task/design] Future: add group management (create/rename/delete) to Manage UI
  ...
```

26 open items from 4 documents (abridged above). Each reads as `[kind/stage] text`: the model decided what kind of item it is and which lifecycle stage it belongs to, within the pack's stage vocabulary. They are `INFERRED` nodes linked back to their source document, and they do not trigger gap rules until you confirm them. This first run took about 5 minutes. Results are cached in `$WORKGRAPH_HOME/cache/`, so running it again reuses them, and if your subscription limit is reached the pass stops with a warning instead of failing the build.

The result is a graph that mixes what the code *is* (symbols, imports, tests) with what the team *intends* (open decisions, deferred work), in the same lifecycle stages.

## 8. Search it and review it in a browser

With the graph built (section 4 built all three layers), query it from the command line:

```bash
workgraph status
```

```
graph: /tmp/tutorial-home/out/graph.json (built 2026-10-18 11:37)
60 nodes, 143 edges
layers:   org 6, process 23, product 3, repo 28
projects: acme-shop 10, rq-demo 6
gaps:     5  (experiment has no analysis 2, question has no hypothesis 1, requirement has no design 1, requirement has no test 1)
```

**Keyword search** matches names, paths, summaries and the first lines of each file. A match in a node's name outranks one in its body, partial words work, and quoted words must appear together:

```bash
workgraph search "checkout test"
```

```
  11.93  [repo/file] test_checkout.py   (acme-shop:tests/test_checkout.py)
   3.15  [repo/decision] ADR-002: Checkout totals computed server-side   (acme-shop:docs/adr/ADR-002.md)
   3.15  [product/application] Checkout service   (:graphs/product.json)
   3.15  [repo/file] checkout.py   (acme-shop:src/checkout.py)
   2.82  [process/stage] Test   (:domains/sdlc.yaml)
```

Note that one query returned nodes from three layers: the test file you wrote in step 3, a decision, a product application, a code file and a process stage. (Your numbers will match these only if you followed the steps in order.)

**Semantic search** ranks by meaning, so you can describe what you want instead of guessing its name:

```bash
workgraph search "customers paying for an order" --semantic
```

```
(embeddings via ollama (nomic-embed-text))
   0.60  [repo/requirement] REQ-002: Customers can check out   (acme-shop:docs/requirements/REQ-002.md)
   0.57  [repo/requirement] REQ-001: Customers can add items to a cart   (acme-shop:docs/requirements/REQ-001.md)
   0.57  [product/application] Checkout service   (:graphs/product.json)
   0.55  [repo/decision] ADR-002: Checkout totals computed server-side   (acme-shop:docs/adr/ADR-002.md)
   0.53  [repo/requirement] REQ-003: Discount codes   (acme-shop:docs/requirements/REQ-003.md)
   ...
```

The first line says how the ranking was made. Embeddings come from a local Ollama model (`ollama pull nomic-embed-text`) or from sentence-transformers (`pip install 'workgraph[semantic]'`). With neither, workgraph falls back to TF-IDF and tells you the ranking is lexical, not meaning. The scores are similarities, so even weak matches appear; read the top few, not the whole list. Vectors are cached in `$WORKGRAPH_HOME/cache/embeddings`, so only the first search pays for indexing.

Look at one node and its links:

```bash
workgraph show REQ-002
```

```
acme-shop:doc_req_002
  label: REQ-002: Customers can check out
  kind: requirement
  layer: repo
  project: acme-shop
  stages: ['requirement']
  source_file: docs/requirements/REQ-002.md
  -> belongs_to: acme-shop  [project:acme-shop]
  -> in_stage: Requirement  [stage:sdlc:requirement]
  <- satisfies: ADR-002: Checkout totals computed server-side  [acme-shop:doc_adr_002]
  <- verifies: test_checkout.py  [acme-shop:tests_test_checkout]
```

The same things are available in a browser, for reviewing rather than scripting:

```bash
python -m pip install 'workgraph[ui]'     # once, in a clean environment (see SETUP.md)
workgraph ui
```

The app opens at `http://localhost:8501` with four tabs: **Overview** (counts per layer, project and kind), **Search** (keyword or semantic, with the backend choice and a note on which one answered), **Browse** (every node, filterable by name) and **Gaps** (each rule's findings). The sidebar filters by layer, project, kind and stage across all tabs. Click any row and a panel opens below with the node's attributes, its links in and out, and a small diagram of its neighbourhood. After running `workgraph build` again, press *Reload*.

## 9. Make Claude Code feed the graph

You don't have to run `build` by hand for every project. Two Claude Code hooks make workgraph the memory of your sessions:

```
/plugin marketplace add digital-duck/work-graph
/plugin install workgraph@workgraph
```

That installs the plugin in `plugin/`: the two hooks, and the command `/workgraph:workgraph` (Claude Code puts the plugin name in front of plugin commands). If you prefer the plain `/workgraph`, use `workgraph claude-setup --write` instead, which merges the hooks into `~/.claude/settings.json` (keeping a `.bak` copy) and adds `~/.claude/commands/workgraph.md`. Use one route or the other, not both. `workgraph claude-setup` without `--write` just prints what it would do.

After that, when you start `claude` in a project:

- the project is added to `~/workgraph/workspace.yaml` (the manual equivalent is `workgraph track <path>`)
- the session is logged, and a short summary of what the graph knows is added to Claude's context: artifacts per stage, gaps, open work items, the product and owner the project belongs to, and what earlier sessions changed
- when the session ends, the changed files are recorded and the graph is rebuilt in the background if anything changed

Inside a session, ask the graph directly:

```
/workgraph status
/workgraph search checkout
/workgraph gaps
/workgraph which requirements have no tests?
```

The command runs `workgraph` through Bash (it is limited to `workgraph` commands), answers from what the graph returns and names the nodes it used. It never starts the LLM passes, because those spend your Claude subscription.

The hooks use the structural build only (no LLM), never block a session, and skip your home folder. You can try the handler without installing anything by piping the hook input to it:

```bash
echo '{"session_id":"demo","cwd":"/tmp/tutorial/acme-shop"}' | workgraph hook session-start
```

## 10. Bring in existing Claude Code work, and move to another machine

Everything above started from an empty graph. Two more commands cover real life.

**Backfill.** Claude Code already knows the projects you worked in. Look first (this is a dry run and changes nothing):

```bash
workgraph import-claude
```

```
55 Claude Code project folders in /home/you/.claude/projects

  track          /home/you/projects/digital-duck/cb-zinets   [9 memory, 2 sessions]
  track          /home/you/projects/digital-duck/cve-coder   [1 memory, 5 sessions]
  skip: folder no longer exists   (-home-you-projects-digital-duck-graph-ir)   [0 memory, 1 sessions]
  ...
dry run: nothing changed. `--apply` would track 42 folders and import session metadata.
```

`workgraph import-claude --apply --with-titles` then adds those folders to your workspace and loads one journal row per session (when, branch, files edited, and Claude's own one-line title). Message text is never stored. On the author's machine that was 42 folders and 65 sessions. Run `workgraph build`, and each project's Claude Code memory files appear as `memory` nodes that `workgraph search` finds.

**A second machine.** The built graph can always be rebuilt from your projects. What cannot be rebuilt is what you wrote into the graph, so that is all that travels, as text, and never the database:

```bash
workgraph export -o workgraph.tar.gz       # on machine A; carry it on a USB stick
workgraph import workgraph.tar.gz          # on machine B

workgraph sync init git@github.com:you/my-workgraph.git    # both machines; the repository must be PRIVATE
workgraph sync push                        # after you change something
workgraph sync pull --clone-missing        # on the other machine
```

After the import, projects have no folder on machine B yet; `workgraph track <folder>` binds one and `--clone-missing` clones them. The org layer (it names people), the session journal and the LLM cache are left out unless you add `--with org,sessions,llm-cache`. See the User Guide section "Moving to another machine" for the safety rules and what happens when both machines edit the same file.

## 11. What you have seen, and where next

| Step | Framework idea |
|---|---|
| 1 | a process is data (a domain pack), not code |
| 2 | artifacts are tagged with lifecycle stages and linked by explicit, annotated and derived edges |
| 3 | gap rules are graph queries over those stages and links |
| 4 | several graphs reference each other by ID and resolve into one in-memory graph |
| 5 | JSON is canonical, YAML is for human review, comments survive the round trip |
| 6 | the resolved graph answers cross-layer questions |
| 7 | any folder or git URL can be ingested with `--src`, and an LLM pass adds the intent hidden in prose |
| 8 | the same graph can be searched by name or by meaning, and reviewed in a browser |
| 9 | Claude Code sessions are tracked and summarized automatically, so the graph acts as memory |
| 10 | the graph can absorb past Claude Code work and travel between machines as private text, never as a database |

Next steps:

- Point a workspace at your own repo: copy `examples/acme-shop/workspace.yaml`, set `path:`, and run `build` and `gaps`.
- Add the LLM pass for planning prose: `workgraph open --semantic --adapter cc_cli -c acme-shop/workspace.yaml` extracts work items from `ROADMAP.md` (needs a logged-in `claude` CLI, see [SETUP.md](SETUP.md)).
- Write your own process as a layer file or a domain pack: see [USER-GUIDE.md](USER-GUIDE.md).
- Read the design behind the layers, IDs and the comment store: [../DEV/README-design.md](../DEV/README-design.md).
