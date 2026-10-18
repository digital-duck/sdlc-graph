"""LLM adapter: the one place that decides which backend serves a prompt.

Backends: our own `cc_cli` plus graphify's (`graphify.llm.BACKENDS`: claude-cli, bedrock, claude, gemini, ...).
Typical setup: `cc_cli` at home (Claude Code *subscription*, never the API), `bedrock` at work (AWS account).

Billing, because the names are easy to confuse:
    cc_cli      subscription. Strips ANTHROPIC_API_KEY & co. from the environment (see cc_cli.py).
    claude-cli  graphify's built-in. Passes the environment through: with ANTHROPIC_API_KEY set it bills the API.
    claude      Anthropic API, per token (ANTHROPIC_API_KEY).
    bedrock     your AWS account.

Resolution order, first hit wins:
    1. explicit argument            complete(..., backend="bedrock")
    2. WORKGRAPH_BACKEND env var   export WORKGRAPH_BACKEND=bedrock      <- switch machines without editing YAML
    3. project `backend:` in workspace.yaml
    4. processor `backend:` in the domain pack
    5. DEFAULT_BACKEND (cc_cli)

Bedrock is configured through the standard AWS environment plus graphify's model override:
    AWS_PROFILE / AWS_REGION            credentials and region
    GRAPHIFY_BEDROCK_MODEL              model or inference-profile id (graphify's built-in default is old; set this)
"""
from __future__ import annotations

import os
import shutil

DEFAULT_BACKEND = "cc_cli"
OWN_BACKENDS = {"cc_cli"}
BILLING = {
    "cc_cli": "subscription (Claude Code login; API-key env vars stripped)",
    "claude-cli": "whatever `claude` picks up: API per-token if ANTHROPIC_API_KEY is set, else subscription",
    "claude": "Anthropic API, per token",
    "bedrock": "AWS account",
}
ENV_BACKEND = "WORKGRAPH_BACKEND"


def resolve_backend(*candidates: str | None) -> str:
    """Pick the backend: explicit candidates in priority order, with the env var outranking config-file values.

    Call as resolve_backend(explicit, project_backend, processor_backend)."""
    explicit, *configured = candidates
    chosen = explicit or os.environ.get(ENV_BACKEND) or next((c for c in configured if c), None) or DEFAULT_BACKEND
    from graphify.llm import BACKENDS
    if chosen not in BACKENDS and chosen not in OWN_BACKENDS:
        raise ValueError(f"unknown LLM backend {chosen!r}; available: {sorted(set(BACKENDS) | OWN_BACKENDS)}")
    return chosen


def complete(prompt: str, *, backend: str, max_tokens: int = 4096) -> str:
    """Send a plain-text prompt to the backend and return the reply text."""
    if backend == "cc_cli":
        from . import cc_cli
        return cc_cli.complete(prompt)
    from graphify import llm as g   # looked up at call time so tests can patch graphify.llm._call_llm
    return g._call_llm(prompt, backend=backend, max_tokens=max_tokens)


def check_backend(backend: str) -> list[tuple[str, bool, str]]:
    """Local, offline readiness checks for a backend: [(check, ok, detail)]. Makes no network calls."""
    from graphify.llm import BACKENDS, _get_backend_api_key
    out: list[tuple[str, bool, str]] = []
    if backend == "cc_cli":
        from . import cc_cli
        path = shutil.which("claude")
        out.append(("`claude` on PATH", bool(path), path or "install Claude Code and run `claude` once to log in"))
        if path:
            ok, detail = _claude_login()
            out.append(("subscription login", ok, detail))
        set_vars = cc_cli.stripped_vars_present()
        out.append(("billing env vars stripped for the call", True,
                    ", ".join(set_vars) + " (set in this shell, removed for the call)" if set_vars else "none set"))
    elif backend == "claude-cli":
        out.append(("ANTHROPIC_API_KEY unset", not os.environ.get("ANTHROPIC_API_KEY"),
                    "set: this backend will bill the API; use cc_cli for subscription billing"
                    if os.environ.get("ANTHROPIC_API_KEY") else "ok"))
        path = shutil.which("claude")
        out.append(("`claude` on PATH", bool(path), path or "install Claude Code"))
    elif backend == "bedrock":
        try:
            import boto3  # noqa: F401
            out.append(("boto3 installed", True, ""))
        except ImportError:
            out.append(("boto3 installed", False, "pip install boto3  (or: python -m pip install -e '.[bedrock]')"))
        has_creds = any(os.environ.get(k) for k in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_WEB_IDENTITY_TOKEN_FILE"))
        out.append(("AWS credentials in env", has_creds, "set AWS_PROFILE (or key env vars / SSO login)"))
        out.append(("AWS region", bool(os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")),
                    os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "defaults to us-east-1"))
        out.append(("GRAPHIFY_BEDROCK_MODEL set", bool(os.environ.get("GRAPHIFY_BEDROCK_MODEL")),
                    os.environ.get("GRAPHIFY_BEDROCK_MODEL") or f"using graphify default {BACKENDS['bedrock']['default_model']}"))
    elif backend in BACKENDS:
        out.append(("API key", bool(_get_backend_api_key(backend)) or backend == "ollama", "see graphify.llm.BACKENDS"))
    return out


def _claude_login() -> tuple[bool, str]:
    """Offline login check via `claude auth status` (reads local config, no model call)."""
    import json
    import subprocess
    try:
        r = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, timeout=30,
                           env=__import__("workgraph.cc_cli", fromlist=["x"]).subscription_env())
        info = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return False, "could not read `claude auth status`; run `claude` once to log in"
    ok = bool(info.get("loggedIn")) and info.get("authMethod") == "claude.ai"
    return ok, f"{info.get('authMethod')} / {info.get('subscriptionType') or 'no subscription'}" if ok else \
        f"not a subscription login (authMethod={info.get('authMethod')!r}); run `claude` and /login"
