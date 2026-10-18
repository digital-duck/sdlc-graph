"""`cc_cli` backend: Claude Code CLI on your *subscription*, never the paid API.

Ported (synchronous) from SPL.py `spl/adapters/claude_cli.py`. It is deliberately separate from graphify's
built-in `claude-cli` backend, which passes the whole environment through: if ANTHROPIC_API_KEY is set,
`claude -p` then bills the API per token. Here those variables are stripped so the CLI uses its stored login.

  - prompt goes through stdin (no OS argument-length limit)
  - `--tools ""` unless tools are requested (skips tool-schema loading, saves ~50 s startup per call)
  - transient launch failures (CLI auto-updater swapping the binary) are retried; timeouts are not
  - session/rate/quota limits raise BackendLimitError so callers can stop instead of hammering the CLI
Cost is 0 (flat subscription billing). Not ported: the SPL multimodal path, which needs ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import os
import subprocess
import time

DEFAULT_MODEL = "claude-sonnet-5"      # same default as the SPL adapter; override with WORKGRAPH_CC_MODEL
DEFAULT_TIMEOUT = 1200                 # seconds, same as SPL; override with WORKGRAPH_CC_TIMEOUT
LAUNCH_ATTEMPTS = 3                    # 1 try + 2 retries for OSError (not for timeouts)

# Variables that would route `claude` to a paid API / another provider / a nested-session guard.
STRIP_VARS = {
    "ANTHROPIC_API_KEY",        # paid API: must NOT be used here
    "ANTHROPIC_BASE_URL",       # API base URL: not needed for CLI login auth
    "CLAUDECODE",               # nested-session guard
    "CLAUDE_CODE_ENTRYPOINT",   # nested-session guard
    # Added beyond SPL: other ways to make Claude Code bill someone other than the subscription.
    "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
}


class BackendLimitError(RuntimeError):
    """Session / rate / quota limit reached: stop calling this backend for now."""


def subscription_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    return {k: v for k, v in (os.environ if environ is None else environ).items() if k not in STRIP_VARS}


def stripped_vars_present() -> list[str]:
    """Which billing-relevant variables are set in this shell (and will be removed for the call)."""
    return sorted(k for k in STRIP_VARS if os.environ.get(k))


def complete(prompt: str, *, model: str | None = None, timeout: int | None = None,
             allowed_tools: list[str] | None = None, cli_path: str = "claude") -> str:
    model = model or os.environ.get("WORKGRAPH_CC_MODEL") or DEFAULT_MODEL
    timeout = timeout or int(os.environ.get("WORKGRAPH_CC_TIMEOUT", DEFAULT_TIMEOUT))
    cmd = [cli_path, "--print", "--no-session-persistence", "--model", model]
    cmd += ["--allowedTools", ",".join(allowed_tools)] if allowed_tools else ["--tools", ""]
    env = subscription_env()

    last: OSError | None = None
    proc = None
    for attempt in range(1, LAUNCH_ATTEMPTS + 1):
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout,
                                  env=env, check=False)
            last = None
            break
        except subprocess.TimeoutExpired:   # checked first: a real hang is not a launch hiccup
            raise RuntimeError(f"Claude CLI timed out after {timeout}s") from None
        except OSError as e:
            last = e
            if attempt < LAUNCH_ATTEMPTS:
                time.sleep(1.5 * attempt)
    if last is not None:
        if isinstance(last, FileNotFoundError):
            raise RuntimeError(f"Claude CLI not found at '{cli_path}' (after {LAUNCH_ATTEMPTS} attempts). "
                               "Install Claude Code: https://docs.anthropic.com/en/docs/claude-code")
        raise RuntimeError(f"Claude CLI failed to launch after {LAUNCH_ATTEMPTS} attempts: {last}")
    assert proc is not None

    out, err = proc.stdout.strip(), proc.stderr.strip()
    if proc.returncode != 0:
        detail = err or out or "(no output)"      # the CLI often reports errors on stdout
        if any(m in detail.lower() for m in ("session limit", "rate limit", "quota")):
            raise BackendLimitError(f"Claude CLI limit reached: {detail}")
        raise RuntimeError(f"Claude CLI error (exit {proc.returncode}): {detail}")
    if not out:
        raise RuntimeError("Claude CLI returned an empty response" + (f" (stderr: {err[:300]})" if err else ""))
    return out
