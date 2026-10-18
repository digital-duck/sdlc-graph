---
description: Query the workgraph knowledge graph - status, search, gaps, show, build, track, ui
argument-hint: [status | search <query> | gaps | show <node> | build | track | ui | a question]
allowed-tools: Bash(workgraph:*)
---

You are using **workgraph**, a knowledge graph of this user's projects, their lifecycle stages, products, teams and processes. It is the user's memory for their work: use it before guessing.

What the graph knows about the current project right now:

!`workgraph context .`

The user ran `/workgraph` with: `$ARGUMENTS`

Do what they asked, using the `workgraph` CLI through Bash:

| Arguments | Run |
|---|---|
| (empty) or `status` | `workgraph status`, then summarize the context above and what to look at next |
| `search <text>` | `workgraph search "<text>"`; add `--semantic` when the text is a question or a description rather than names |
| `gaps` | `workgraph gaps --src .` (artifacts missing links their domain pack expects, plus broken references) |
| `show <node>` | `workgraph show "<node>"` (a node id, or a label such as `REQ-002`) |
| `build` | `workgraph build` (rebuilds the graph of every tracked project; no LLM) |
| `track` | `workgraph track .` (add this folder to the tracked projects) |
| `ui` | tell the user to run `workgraph ui` in a terminal (it starts a web app); do not start it yourself |
| anything else | treat it as a question: run `workgraph search "<key words>" --semantic`, then `workgraph show` on the best hits, and answer from what the graph returns |

Rules:
- Answer from the graph's output and say which nodes you used (their labels and ids). If the graph has nothing relevant, say so instead of guessing.
- If `workgraph` is not found, tell the user to install it (`python -m pip install workgraph`) and stop.
- If the project is not tracked or there is no graph yet, offer `workgraph track .` and `workgraph build`.
- Do not run `--semantic` LLM passes (`workgraph open --semantic`, `workgraph build --semantic`): they spend the user's Claude subscription. Suggest them, and let the user run them.
