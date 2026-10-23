"""Domain packs and workspaces.

A domain pack (domains/<name>.yaml) is the only thing that changes between use cases:
vocabulary (stages, relations, gap rules, path classifiers) plus processing (which
artifact types go through which processor and LLM backend). The graph engine is shared.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

from . import paths

DOMAINS_DIR = Path(__file__).parent / "domains"
# Node kinds that are structure, not knowledge: excluded from gap checks and summaries.
STRUCTURAL_KINDS = {"stage", "project", "rationale", "symbol", "external"}


def make_id(s: str) -> str:
    """Same shape as graphify's node ids: lowercase, [a-z0-9_] runs."""
    return re.sub(r"[^\w]+", "_", s.casefold()).strip("_")


def glob_match(path: str, globs: list[str]) -> bool:
    """Case-insensitive glob match (README.md vs readme.md); a leading `**/` also matches the root."""
    path = path.casefold()
    return any(fnmatchcase(path, g.casefold()) or fnmatchcase("/" + path, g.casefold()) for g in globs)


def custom_domain_files() -> dict[str, Path]:
    """Your own packs: <home>/custom/domains/<name>.yaml (or .yml), by name."""
    d = paths.custom_domains_dir()
    return {p.stem: p for p in sorted(d.glob("*.y*ml")) if p.suffix in (".yaml", ".yml")} if d.is_dir() else {}


def available_domains() -> list[str]:
    return sorted({p.stem for p in DOMAINS_DIR.glob("*.yaml")} | set(custom_domain_files()))


@dataclass
class Stage:
    id: str
    label: str
    next: list[str] = field(default_factory=list)


@dataclass
class Processor:
    name: str
    use: str
    match: list[str] = field(default_factory=list)
    backend: str | None = None
    kinds: list[str] = field(default_factory=list)   # item kinds for the llm_items processor


@dataclass
class Domain:
    name: str
    label: str
    stages: dict[str, Stage]
    flow_relations: list[dict]
    expectations: list[dict]
    classifiers: list[dict]
    processors: list[Processor]
    ignore: list[str] = field(default_factory=list)   # default noise patterns for this domain

    @classmethod
    def load(cls, name_or_path: str | Path) -> "Domain":
        p = Path(name_or_path)
        if p.suffix not in (".yaml", ".yml"):
            p = custom_domain_files().get(str(name_or_path)) or DOMAINS_DIR / f"{name_or_path}.yaml"   # yours win
            if not p.exists():
                raise ValueError(f"unknown domain {name_or_path!r}; available: {available_domains()}")
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        stages = {s["id"]: Stage(s["id"], s.get("label", s["id"]), s.get("next", [])) for s in raw["stages"]}
        d = cls(
            name=raw["domain"], label=raw.get("label", raw["domain"]), stages=stages,
            flow_relations=raw.get("flow_relations", []), expectations=raw.get("expectations", []),
            classifiers=raw.get("classifiers", []), ignore=raw.get("ignore", []),
            processors=[Processor(x["name"], x["use"], x.get("match", []), x.get("backend"), x.get("kinds", []))
                        for x in raw.get("processors", [])],
        )
        d._check()
        return d

    def _check(self) -> None:
        def need(stage: str, where: str) -> None:
            if stage not in self.stages:
                raise ValueError(f"[{self.name}] {where} references unknown stage {stage!r}")
        for s in self.stages.values():
            for n in s.next:
                need(n, f"stage {s.id}.next")
        for r in self.flow_relations:
            need(r["from"], "flow_relations"); need(r["to"], "flow_relations")
        for e in self.expectations:
            need(e["stage"], "expectations")
        for c in self.classifiers:
            need(c["stage"], "classifiers")

    @property
    def relation_names(self) -> set[str]:
        return {r["relation"] for r in self.flow_relations}

    def stage_node(self, stage: str) -> str:
        return f"stage:{self.name}:{stage}"

    def classify(self, rel_path: str) -> list[str]:
        """Stage tags for a project-relative path: every matching rule adds one, else the default."""
        tags, default = [], None
        for rule in self.classifiers:
            if rule.get("default"):
                default = rule["stage"]
            elif glob_match(rel_path, rule["globs"]):
                tags.append(rule["stage"])
        return list(dict.fromkeys(tags or ([default] if default else [])))

    def validate_stages(self, tags: list[str]) -> list[str]:
        """Enforce the constrained tag vocabulary; returns de-duplicated tags in order."""
        unknown = [t for t in tags if t not in self.stages]
        if unknown:
            raise ValueError(f"unknown stage tag(s) {unknown} for domain {self.name!r}; allowed: {sorted(self.stages)}")
        return list(dict.fromkeys(tags))

    def process_extraction(self) -> dict:
        """The process layer (stages + `precedes` flow) as a graphify extraction dict."""
        nodes, edges = [], []
        for s in self.stages.values():
            nodes.append({"id": self.stage_node(s.id), "label": s.label, "file_type": "concept",
                          "source_file": f"domains/{self.name}.yaml", "kind": "stage",
                          "domain": self.name, "stages": [s.id]})
            for n in s.next:
                edges.append({"source": self.stage_node(s.id), "target": self.stage_node(n), "relation": "precedes",
                              "confidence": "EXTRACTED", "source_file": f"domains/{self.name}.yaml"})
        return {"nodes": nodes, "edges": edges, "hyperedges": []}


def _resolve_pack(name_or_path: str | Path) -> Path:
    p = Path(name_or_path)
    if p.suffix in (".yaml", ".yml"):
        return p
    return custom_domain_files().get(str(name_or_path)) or DOMAINS_DIR / f"{name_or_path}.yaml"


def validate_pack(name_or_path: str | Path) -> list[str]:
    """Every problem in a domain pack, not just the first (`Domain.load` stops at the first).
    Empty list = the pack loads. Accepts a file path or a pack name (your own packs win)."""
    from .processors import REGISTRY       # imported here: processors imports this module
    p = _resolve_pack(name_or_path)
    if not p.is_file():
        return [f"pack not found: {p}"]
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return [f"invalid YAML: {e}"]
    if not isinstance(raw, dict):
        return ["the top level must be a mapping"]
    errs: list[str] = []

    def items(key: str, required: bool = False) -> list:
        v = raw.get(key)
        if v is None:
            if required:
                errs.append(f"`{key}` is required")
            return []
        if not isinstance(v, list):
            errs.append(f"`{key}` must be a list")
            return []
        return v

    if not isinstance(raw.get("domain"), str) or not raw.get("domain"):
        errs.append("`domain` is required and must be a name")
    if "label" in raw and not isinstance(raw["label"], str):
        errs.append("`label` must be a string")

    stage_ids: list[str] = []
    for i, st in enumerate(items("stages", required=True)):
        if not isinstance(st, dict) or not isinstance(st.get("id"), str):
            errs.append(f"stages[{i}]: needs a string `id`")
        elif st["id"] in stage_ids:
            errs.append(f"stages[{i}]: duplicate stage id {st['id']!r}")
        else:
            stage_ids.append(st["id"])
    if "stages" in raw and isinstance(raw["stages"], list) and not raw["stages"]:
        errs.append("`stages` must not be empty")

    def known(stage, where: str) -> None:
        if stage not in stage_ids:
            errs.append(f"{where} references unknown stage {stage!r}; stages are {stage_ids}")

    for i, st in enumerate(items("stages")):
        if isinstance(st, dict):
            nxt = st.get("next", [])
            if not isinstance(nxt, list):
                errs.append(f"stages[{i}] ({st.get('id')}): `next` must be a list of stage ids")
            else:
                for n in nxt:
                    known(n, f"stage {st.get('id')}.next")

    relations: set[str] = set()
    for i, r in enumerate(items("flow_relations")):
        if not isinstance(r, dict) or not all(k in r for k in ("from", "to", "relation")):
            errs.append(f"flow_relations[{i}]: needs `from`, `to` and `relation`")
            continue
        known(r["from"], f"flow_relations[{i}].from")
        known(r["to"], f"flow_relations[{i}].to")
        relations.add(r["relation"])

    for i, e in enumerate(items("expectations")):
        if not isinstance(e, dict) or not all(k in e for k in ("stage", "needs", "message")):
            errs.append(f"expectations[{i}]: needs `stage`, `needs` and `message`")
            continue
        known(e["stage"], f"expectations[{i}].stage")
        if relations and e["needs"] not in relations:
            errs.append(f"expectations[{i}]: `needs` {e['needs']!r} is not a flow relation {sorted(relations)}")
        if e.get("direction", "in") not in ("in", "out"):
            errs.append(f"expectations[{i}]: `direction` must be `in` or `out`")

    defaults = 0
    for i, c in enumerate(items("classifiers")):
        if not isinstance(c, dict) or "stage" not in c:
            errs.append(f"classifiers[{i}]: needs `stage`")
            continue
        known(c["stage"], f"classifiers[{i}].stage")
        if c.get("default"):
            defaults += 1
        elif not isinstance(c.get("globs"), list) or not c["globs"]:
            errs.append(f"classifiers[{i}]: needs a non-empty `globs` list, or `default: true`")
    if defaults > 1:
        errs.append("more than one classifier is marked `default`")

    for i, pr in enumerate(items("processors")):
        if not isinstance(pr, dict) or not all(isinstance(pr.get(k), str) for k in ("name", "use")):
            errs.append(f"processors[{i}]: needs string `name` and `use`")
            continue
        if pr["use"] not in REGISTRY:
            errs.append(f"processors[{i}] ({pr['name']}): unknown `use` {pr['use']!r}; allowed: {sorted(REGISTRY)}")
        for key in ("match", "kinds"):
            if key in pr and not isinstance(pr[key], list):
                errs.append(f"processors[{i}] ({pr['name']}): `{key}` must be a list")

    if "ignore" in raw and not (isinstance(raw["ignore"], list) and all(isinstance(g, str) for g in raw["ignore"])):
        errs.append("`ignore` must be a list of glob strings")
    return errs


@dataclass
class Project:
    name: str
    path: Path
    domain: Domain
    backend: str | None = None   # overrides the LLM backend named in the domain's processors
    ignore: list[str] = field(default_factory=list)   # extra gitignore-style patterns (generated output etc.)


@dataclass
class Workspace:
    projects: list[Project]
    graphs_dir: Path | None = None   # layer files (product, org, process), see docs/DEV/README-design.md
    unresolved: list[str] = field(default_factory=list)   # projects with no folder on this machine (synced, not yet cloned/tracked)

    @property
    def domains(self) -> dict[str, Domain]:
        return {p.domain.name: p.domain for p in self.projects}

    @classmethod
    def load(cls, path: str | Path) -> "Workspace":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        default = raw.get("domain", "sdlc")
        cache: dict[str, Domain] = {}
        projects, unresolved = [], []
        for p in raw["projects"]:
            dn = p.get("domain", default)
            cache.setdefault(dn, Domain.load(dn))
            if p.get("path"):
                folder = (path.parent / p["path"]).resolve()
            else:                      # a project that arrived through sync: usable once cloned or tracked here
                from . import paths
                folder = paths.src_dir() / p["name"]
                if not folder.is_dir():
                    unresolved.append(p["name"])
                    continue
            projects.append(Project(p["name"], folder, cache[dn],
                                    p.get("backend", raw.get("backend")), p.get("ignore", [])))
        from . import paths
        if raw.get("graphs"):
            graphs = path.parent / raw["graphs"]
        elif path.parent.resolve() == paths.home().resolve():     # the default workspace: layer files live in custom/
            graphs = paths.graphs_dir()
        else:
            graphs = path.parent / "graphs"
        return cls(projects, graphs.resolve() if graphs.is_dir() else None, unresolved)
