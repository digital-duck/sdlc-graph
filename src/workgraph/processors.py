"""Processing layer: artifact type -> processor. Each processor returns a graphify extraction dict
(plus an optional `refs` list of unresolved cross-references the pipeline resolves later)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import warnings
from pathlib import Path
from typing import Callable

import yaml

from graphify.detect import _is_ignored, _is_noise_dir, _load_graphifyignore, _parse_gitignore_line
from graphify.extract import collect_files, extract

from . import paths
from .progress import current
from .domain import Processor, Project, glob_match, make_id
from .cc_cli import BackendLimitError
from .claude_import import p_claude_memory
from .llm import complete, resolve_backend


def _inside(root: Path, f: Path) -> bool:
    """True if f (after following symlinks) lives under root; symlinks to other repos are skipped."""
    try:
        f.resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def _ignore_patterns(project: Project) -> list[tuple[Path, str]]:
    """.graphifyignore (graphify's own) + .gitignore + the project's `ignore:` list, gitignore semantics."""
    pats = list(_load_graphifyignore(project.path))
    gi = project.path / ".gitignore"
    if gi.exists():
        pats += [(project.path, ln) for raw in gi.read_text(errors="replace").splitlines()
                 if (ln := _parse_gitignore_line(raw))]
    return pats + [(project.path, g) for g in project.domain.ignore + project.ignore]


def walk_project(project: Project) -> list[Path]:
    pats, root, out = _ignore_patterns(project), project.path, []
    cache: dict = {}     # graphify memoises each folder's verdict here; without it every file re-checks all its parents
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith(".") and not _is_noise_dir(d) and d != "graphify-out"
                  and not _is_ignored(Path(dp) / d, root, pats, _cache=cache)]
        for f in fns:
            fp = Path(dp) / f
            if not f.startswith(".") and _inside(root, fp) and not _is_ignored(fp, root, pats, _cache=cache):
                out.append(fp)
    return sorted(out)


def _rel(project: Project, f: Path) -> str:
    try:
        return f.relative_to(project.path).as_posix()
    except ValueError:
        return f.resolve().relative_to(project.path).as_posix()


def _ids(value) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else re.split(r"[,\s]+", str(value))
    return [str(i).strip() for i in items if str(i).strip()]


def _empty() -> dict:
    return {"nodes": [], "edges": [], "hyperedges": [], "refs": [], "warnings": []}


_DOC_SUFFIXES = {".md", ".markdown", ".txt", ".rst"}  # owned by the document processors, not tree-sitter


def p_ast(project: Project, files: list[Path], proc: Processor) -> dict:
    """graphify tree-sitter extraction. Uses graphify's own file discovery (ignore rules, languages)."""
    allowed = {f.resolve() for f in files}   # already filtered by ignore rules and `match`
    found = [f for f in collect_files(project.path)
             if f.suffix.lower() not in _DOC_SUFFIXES and f.resolve() in allowed]
    if not found:
        return _empty()
    base = Path(os.path.commonpath([f.parent for f in found])).resolve()
    # root pins the path anchor (graphify otherwise derives it from cache_root); cache_root keeps graphify's
    # cache out of the project and the current directory.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="stat index switched")   # we use one cache root per project on purpose
        out = extract(found, cache_root=paths.cache_dir() / "ast" / project.name, root=base)
    out.setdefault("refs", [])
    # graphify reports paths relative to the files' common directory; make them project-relative.
    shift = base.relative_to(project.path)
    for item in out["nodes"] + out["edges"]:
        sf = item.get("source_file")
        if sf and not Path(sf).is_absolute():
            item["source_file"] = (shift / sf).as_posix()
    return out


def p_frontmatter(project: Project, files: list[Path], proc: Processor) -> dict:
    """Markdown -> one node per file. Frontmatter supplies id/title/kind/stages and relation edges."""
    out, relations = _empty(), project.domain.relation_names
    for f in files:
        rel = _rel(project, f)
        text = f.read_text(encoding="utf-8", errors="replace")
        fm: dict = {}
        if text.startswith("---"):
            parts = text.split("\n---", 1)
            if len(parts) == 2:
                try:
                    loaded = yaml.safe_load(parts[0][3:])
                    fm = loaded if isinstance(loaded, dict) else {}
                except yaml.YAMLError:
                    fm = {}
        title = fm.get("title") or next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), f.stem)
        doc_id = str(fm["id"]) if fm.get("id") else None
        node = {"id": "doc_" + make_id(doc_id or rel), "label": f"{doc_id}: {title}" if doc_id else title,
                "file_type": "document", "source_file": rel, "kind": fm.get("kind", "document"),
                "stages": _ids(fm.get("stages")), "doc_id": doc_id}
        out["nodes"].append(node)
        for rname in relations:
            for target in _ids(fm.get(rname)):
                out["refs"].append({"source": ("id", node["id"]), "relation": rname, "target": target, "file": rel})
    return out


_ANNOT = re.compile(r"^\s*(?:#|//|--|/?\*+)\s*@?([A-Za-z_]+)\s*:\s*(.+?)\s*$")


def p_annotations(project: Project, files: list[Path], proc: Processor) -> dict:
    """`# verifies: REQ-001` style comments in any text file -> explicit edges from that file."""
    out, relations = _empty(), project.domain.relation_names
    for f in files:
        rel = _rel(project, f)
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = _ANNOT.match(line)
            if m and m.group(1) in relations:
                for target in _ids(m.group(2)):
                    out["refs"].append({"source": ("path", rel), "relation": m.group(1), "target": target, "file": rel})
    return out


def p_llm(project: Project, files: list[Path], proc: Processor) -> dict:
    """graphify semantic extraction through the chosen backend (Claude, Gemini, Ollama, ...)."""
    from graphify.llm import extract_files_direct
    if not files:
        return _empty()
    backend = resolve_backend(None, project.backend, proc.backend)
    if backend == "cc_cli":   # graphify's extractor, but the call goes through the subscription-safe adapter
        from graphify import llm as g
        out = _empty()
        for i in range(0, len(files), 5):
            prompt = g._EXTRACTION_SYSTEM + "\n\n" + g._read_files(files[i:i + 5], project.path)
            try:
                part = g._parse_llm_json(complete(prompt, backend=backend))
            except BackendLimitError as e:
                out["warnings"].append(f"{e}; skipping remaining files")
                break
            except RuntimeError as e:
                out["warnings"].append(f"LLM extraction failed ({e}); batch skipped")
                continue
            for k in ("nodes", "edges", "hyperedges"):
                out[k].extend(part.get(k, []))
        return out
    out = extract_files_direct(files, backend=backend, root=project.path)
    out.setdefault("refs", [])
    return out


_ITEMS_PROMPT = """You read ONE document from a {domain} project and extract its discrete work items.

Allowed item kinds: {kinds}
Allowed stages (lifecycle tags, use only these ids): {stages}
Stage meaning, in flow order: {flow}

Return ONLY a JSON object, no prose, no markdown fences:
{{"items": [{{"title": "<=100 chars", "kind": "<one allowed kind>", "stages": ["<allowed stage ids>"],
  "status": "done|open|unknown", "summary": "<=200 chars"}}]}}

Rules:
- Only items the document states explicitly: a requirement or goal, a decision taken, a task or to-do.
- Skip background, rationale, headings and anything you would have to invent. Prefer fewer, accurate items.
- status "done" only if the document marks it done (strikethrough, [x], "shipped", "done").
- At most {max_items} items.

Document path: {path}
<document>
{text}
</document>
"""
_MAX_DOC_CHARS, _MAX_ITEMS = 24_000, 40


def _cache_dir() -> Path:
    d = paths.cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _json_object(raw: str) -> dict:
    """Pull the first {...} object out of an LLM reply (tolerates fences and chatter)."""
    i, j = raw.find("{"), raw.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        out = json.loads(raw[i:j + 1])
        return out if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        return {}


def p_llm_items(project: Project, files: list[Path], proc: Processor) -> dict:
    """Domain-prompted extraction: prose docs -> typed work items (INFERRED, origin=llm).

    Uses graphify's backend dispatcher (`_call_llm`) but our own prompt, built from the domain pack's
    vocabulary. Results are cached by prompt hash in <home>/cache, so reruns cost nothing."""
    d, out = project.domain, _empty()
    backend = resolve_backend(None, project.backend, proc.backend)
    kinds = proc.kinds or ["requirement", "decision", "task"]
    flow = "; ".join(f"{s.id}->{','.join(s.next)}" for s in d.stages.values())
    for n, f in enumerate(files, 1):
        rel = _rel(project, f)
        text = f.read_text(encoding="utf-8", errors="replace")[:_MAX_DOC_CHARS]
        prompt = _ITEMS_PROMPT.format(domain=d.name, kinds=", ".join(kinds), stages=", ".join(d.stages), flow=flow,
                                      max_items=_MAX_ITEMS, path=rel, text=text)
        cache = _cache_dir() / f"llm_items-{hashlib.sha256((backend + prompt).encode()).hexdigest()[:24]}.json"
        if cache.exists():
            items = json.loads(cache.read_text(encoding="utf-8"))
        else:
            current().note(f"LLM {n}/{len(files)}: {rel}")
            try:
                items = _json_object(complete(prompt, backend=backend, max_tokens=4096)).get("items", [])
            except BackendLimitError as e:
                out["warnings"].append(f"{rel}: {e}; skipping remaining LLM documents (rerun later, results are cached)")
                break
            except RuntimeError as e:
                out["warnings"].append(f"{rel}: LLM extraction failed ({e}); skipped")
                continue
            cache.write_text(json.dumps(items), encoding="utf-8")
        for i, it in enumerate(items[:_MAX_ITEMS]):
            if not isinstance(it, dict) or not it.get("title") or it.get("kind") not in kinds:
                continue
            nid = f"item_{make_id(rel)}_{i}"
            out["nodes"].append({
                "id": nid, "label": str(it["title"])[:100], "file_type": "concept", "source_file": rel,
                "kind": it["kind"], "stages": [x for x in _ids(it.get("stages")) if x in d.stages],
                "status": it.get("status", "unknown"), "summary": str(it.get("summary", ""))[:200], "origin": "llm"})
            out["refs"].append({"source": ("id", nid), "relation": "derived_from", "target": rel, "file": rel,
                                "confidence": "INFERRED"})
    return out


REGISTRY: dict[str, Callable[[Project, list[Path], Processor], dict]] = {
    "ast": p_ast, "frontmatter": p_frontmatter, "annotations": p_annotations, "llm": p_llm,
    "llm_items": p_llm_items, "claude_memory": p_claude_memory,
}


STEP_LABELS = {"ast": "code (tree-sitter)", "frontmatter": "documents", "annotations": "annotations",
               "llm": "LLM extraction", "llm_items": "LLM extraction", "claude_memory": "Claude Code memory"}


def run_processors(project: Project, *, semantic: bool = False, steps: list | None = None) -> dict:
    """Run every processor of the project's domain and merge the results. `steps` collects
    (label, seconds, files) per step, for progress and timing."""
    t = time.perf_counter()
    all_files = walk_project(project)
    if steps is not None:
        steps.append(("scan files", time.perf_counter() - t, len(all_files)))
    merged = _empty()
    for proc in project.domain.processors:
        if proc.use in ("llm", "llm_items") and not semantic:
            continue
        if proc.use not in REGISTRY:
            raise ValueError(f"[{project.domain.name}] processor {proc.name!r} uses unknown processor {proc.use!r}")
        files = [f for f in all_files if not proc.match or glob_match(_rel(project, f), proc.match)]
        if proc.use in ("llm", "llm_items") and files:
            current().note(f"LLM extraction: {len(files)} document{'s' if len(files) != 1 else ''}, one call each (cached ones are instant) ...")
        t = time.perf_counter()
        part = REGISTRY[proc.use](project, files, proc)
        if steps is not None:
            steps.append((STEP_LABELS.get(proc.use, proc.use), time.perf_counter() - t, len(files)))
        for k in ("nodes", "edges", "hyperedges", "refs", "warnings"):
            merged[k].extend(part.get(k, []))
    return merged
