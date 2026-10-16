import subprocess

import pytest

from sdlc_graph import cc_cli


class _Done:
    def __init__(self, rc=0, out="hello", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_strips_billing_vars_and_uses_stdin(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return _Done()

    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDECODE", "AWS_PROFILE"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cc_cli.complete("the prompt", model="m1") == "hello"
    assert seen["input"] == "the prompt"                                     # via stdin, not argv
    assert "the prompt" not in seen["cmd"]
    assert seen["cmd"][-2:] == ["--tools", ""] and seen["cmd"][seen["cmd"].index("--model") + 1] == "m1"
    assert not (cc_cli.STRIP_VARS & set(seen["env"]))                       # subscription only
    assert seen["env"]["AWS_PROFILE"] == "x"                                  # unrelated vars pass through


def test_allowed_tools_replaces_tools_off(monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: seen.update(cmd=cmd) or _Done())
    cc_cli.complete("p", allowed_tools=["WebSearch", "Read"])
    assert "--allowedTools" in seen["cmd"] and "WebSearch,Read" in seen["cmd"] and "--tools" not in seen["cmd"]


def test_limit_error_is_distinct_and_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(1) or _Done(1, "Session limit reached"))
    with pytest.raises(cc_cli.BackendLimitError):
        cc_cli.complete("p")
    assert len(calls) == 1


def test_launch_errors_retry_but_timeouts_do_not(monkeypatch):
    monkeypatch.setattr(cc_cli.time, "sleep", lambda s: None)
    n = {"c": 0}

    def flaky(*a, **k):
        n["c"] += 1
        if n["c"] < 3:
            raise FileNotFoundError("claude")
        return _Done()

    monkeypatch.setattr(subprocess, "run", flaky)
    assert cc_cli.complete("p") == "hello" and n["c"] == 3                   # rode out the updater race

    def hang(*a, **k):
        n["c"] += 1
        raise subprocess.TimeoutExpired("claude", 1)

    n["c"] = 0
    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(RuntimeError, match="timed out"):
        cc_cli.complete("p", timeout=1)
    assert n["c"] == 1
