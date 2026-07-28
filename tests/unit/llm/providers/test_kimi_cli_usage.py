"""kimi_cli_usage — resolve the session dir from a run's stdout meta line (or
the newest matching session dir), then sum the real 4-bucket
``usage.record``/``usageScope=="turn"`` events in that session's wire.jsonl.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from roboco.llm.providers import kimi_cli_usage as ku

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _usage_record(
    *,
    input_other: int,
    output: int,
    cache_read: int = 0,
    cache_creation: int = 0,
    scope: str = "turn",
) -> str:
    return json.dumps(
        {
            "type": "usage.record",
            "model": "kimi-code/k3",
            "usageScope": scope,
            "usage": {
                "inputOther": input_other,
                "output": output,
                "inputCacheRead": cache_read,
                "inputCacheCreation": cache_creation,
            },
        }
    )


def _resume_hint(session_id: str) -> str:
    return json.dumps(
        {"role": "meta", "type": "session.resume_hint", "session_id": session_id}
    )


# ---------------------------------------------------------------------------
# session_id_from_run_log
# ---------------------------------------------------------------------------


def test_session_id_from_run_log_finds_terminal_meta_line(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [
            json.dumps({"role": "assistant", "content": "working"}),
            _resume_hint("session_abc123"),
        ],
    )
    assert ku.session_id_from_run_log(log) == "session_abc123"


def test_session_id_from_run_log_keeps_the_last_match(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_resume_hint("session_first"), _resume_hint("session_second")])
    assert ku.session_id_from_run_log(log) == "session_second"


def test_session_id_from_run_log_none_when_absent(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [json.dumps({"role": "assistant", "content": "hi"})])
    assert ku.session_id_from_run_log(log) is None
    assert ku.session_id_from_run_log(tmp_path / "nope.jsonl") is None


# ---------------------------------------------------------------------------
# resolve_session_dir — primary (known id) + fallback (newest under cwd basename)
# ---------------------------------------------------------------------------


def test_resolve_session_dir_finds_known_session_id(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    session_dir = home / "sessions" / "wd_myrepo_ab12cd34ef56" / "session_abc123"
    session_dir.mkdir(parents=True)
    resolved = ku.resolve_session_dir(
        session_id="session_abc123",
        workdir="/data/workspaces/myrepo",
        kimi_code_home=home,
    )
    assert resolved == session_dir


def test_resolve_session_dir_falls_back_to_newest(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    wd_dir = home / "sessions" / "wd_myrepo_ab12cd34ef56"
    old_session = wd_dir / "session_old"
    new_session = wd_dir / "session_new"
    old_session.mkdir(parents=True)
    time.sleep(0.01)
    new_session.mkdir(parents=True)
    resolved = ku.resolve_session_dir(
        session_id=None, workdir="/data/workspaces/myrepo", kimi_code_home=home
    )
    assert resolved == new_session


def test_resolve_session_dir_none_when_sessions_root_absent(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    assert (
        ku.resolve_session_dir(
            session_id=None, workdir="/x/myrepo", kimi_code_home=home
        )
        is None
    )


def test_resolve_session_dir_falls_back_when_id_not_found(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    wd_dir = home / "sessions" / "wd_myrepo_ab12cd34ef56"
    only_session = wd_dir / "session_other"
    only_session.mkdir(parents=True)
    resolved = ku.resolve_session_dir(
        session_id="session_missing",
        workdir="/data/workspaces/myrepo",
        kimi_code_home=home,
    )
    assert resolved == only_session


# ---------------------------------------------------------------------------
# aggregate_usage_from_wire
# ---------------------------------------------------------------------------


def test_aggregate_sums_turn_scoped_usage_records(tmp_path: Path) -> None:
    wire = tmp_path / "wire.jsonl"
    _write_jsonl(
        wire,
        [
            _usage_record(input_other=100, output=50, cache_read=10),
            json.dumps({"type": "llm.request", "model": "kimi-code/k3"}),
            _usage_record(input_other=200, output=80, cache_read=20, cache_creation=5),
        ],
    )
    agg = ku.aggregate_usage_from_wire(wire)
    assert agg["inputOther"] == 300  # noqa: PLR2004
    assert agg["output"] == 130  # noqa: PLR2004
    assert agg["inputCacheRead"] == 30  # noqa: PLR2004
    assert agg["inputCacheCreation"] == 5  # noqa: PLR2004
    assert agg["turns"] == 2  # noqa: PLR2004


def test_aggregate_ignores_non_turn_scope_and_bad_lines(tmp_path: Path) -> None:
    wire = tmp_path / "wire.jsonl"
    _write_jsonl(
        wire,
        [
            "not json",
            _usage_record(input_other=5, output=1, scope="session"),
            _usage_record(input_other=10, output=5),
        ],
    )
    agg = ku.aggregate_usage_from_wire(wire)
    assert agg["inputOther"] == 10  # noqa: PLR2004
    assert agg["turns"] == 1


def test_aggregate_zero_for_missing_log(tmp_path: Path) -> None:
    agg = ku.aggregate_usage_from_wire(tmp_path / "nope.jsonl")
    assert agg["turns"] == 0
    assert all(v == 0 for k, v in agg.items() if k != "turns")


# ---------------------------------------------------------------------------
# capture_run_usage / main
# ---------------------------------------------------------------------------


def test_capture_run_usage_writes_usage_json(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    session_dir = home / "sessions" / "wd_myrepo_hash1" / "session_abc"
    (session_dir / "agents" / "main").mkdir(parents=True)
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    _write_jsonl(wire, [_usage_record(input_other=100, output=50, cache_read=10)])

    run_log = tmp_path / "run.jsonl"
    _write_jsonl(run_log, [_resume_hint("session_abc")])

    out = tmp_path / "usage.json"
    tokens = ku.capture_run_usage(
        run_log=run_log,
        workdir="/data/workspaces/myrepo",
        model="kimi-code/k3",
        out_path=out,
        kimi_code_home=home,
    )
    assert tokens == (100, 50, 10, 0)
    data = json.loads(out.read_text())
    assert data["model"] == "kimi-code/k3"
    assert data["tokens_input"] == 100  # noqa: PLR2004
    assert data["tokens_output"] == 50  # noqa: PLR2004
    assert data["tokens_cache_read"] == 10  # noqa: PLR2004
    assert data["turns"] == 1
    assert data["cost_usd"] > 0.0


def test_capture_run_usage_zero_when_no_session_found(tmp_path: Path) -> None:
    home = tmp_path / ".kimi-code"
    run_log = tmp_path / "run.jsonl"
    run_log.write_text("", encoding="utf-8")
    out = tmp_path / "usage.json"
    tokens = ku.capture_run_usage(
        run_log=run_log,
        workdir="/data/workspaces/myrepo",
        model="kimi-code/k3",
        out_path=out,
        kimi_code_home=home,
    )
    assert tokens == (0, 0, 0, 0)
    data = json.loads(out.read_text())
    assert data["tokens_input"] == 0
    assert data["turns"] == 0


def test_main_writes_usage_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".kimi-code"
    session_dir = home / "sessions" / "wd_myrepo_hash1" / "session_abc"
    (session_dir / "agents" / "main").mkdir(parents=True)
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    _write_jsonl(wire, [_usage_record(input_other=200, output=100)])

    run_log = tmp_path / "run.jsonl"
    _write_jsonl(run_log, [_resume_hint("session_abc")])

    out = tmp_path / "usage.json"
    monkeypatch.setattr(ku, "USAGE_OUT_PATH", out)
    monkeypatch.setattr(ku, "KIMI_CODE_HOME", home)
    monkeypatch.setenv("ROBOCO_KIMI_RUN_LOG", str(run_log))
    monkeypatch.setenv("ROBOCO_KIMI_WORKDIR", "/data/workspaces/myrepo")
    monkeypatch.setenv("ROBOCO_AGENT_MODEL", "kimi-code/k3")

    assert ku.main() == 0
    data = json.loads(out.read_text())
    assert data["tokens_input"] == 200  # noqa: PLR2004
    assert data["tokens_output"] == 100  # noqa: PLR2004


def test_main_warns_when_run_log_env_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("ROBOCO_KIMI_RUN_LOG", raising=False)
    with caplog.at_level("WARNING", logger="roboco.llm.providers.kimi_cli_usage"):
        assert ku.main() == 0
    assert any("ROBOCO_KIMI_RUN_LOG" in r.message for r in caplog.records)
