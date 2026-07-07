"""grok_cli_usage — capture token usage from a Grok CLI session store."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from roboco.llm.providers import grok_cli_usage as gu

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_updates(path: Path, totals: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, t in enumerate(totals):
        # Real grok shape: the cumulative totalTokens rides on the INNER
        # params.update._meta, not params._meta (which only holds event ids).
        lines.append(
            json.dumps(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "s1",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "_meta": {"totalTokens": t, "chunkId": i},
                        },
                        "_meta": {"eventId": f"s1-{i}"},
                    },
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_total_tokens_is_the_running_max(tmp_path: Path) -> None:
    upd = tmp_path / "updates.jsonl"
    _write_updates(upd, [3863, 18133, 18220, 18253, 18220])
    assert gu.total_tokens_from_updates(upd) == 18253  # noqa: PLR2004


def test_total_tokens_zero_for_missing_or_empty(tmp_path: Path) -> None:
    assert gu.total_tokens_from_updates(tmp_path / "nope.jsonl") == 0
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n  \n", encoding="utf-8")
    assert gu.total_tokens_from_updates(empty) == 0


def test_total_tokens_tolerates_bad_lines(tmp_path: Path) -> None:
    upd = tmp_path / "updates.jsonl"
    upd.write_text(
        'not json\n{"params":{"_meta":{"totalTokens":42}}}\n{"x":1}\n',
        encoding="utf-8",
    )
    assert gu.total_tokens_from_updates(upd) == 42  # noqa: PLR2004


def test_find_updates_path_encodes_cwd(tmp_path: Path) -> None:
    home = tmp_path / ".grok"
    cwd = "/data/workspaces/roboco/backend/be-dev-1"
    sid = "019edd59-bc7b-7920"
    target = (
        home / "sessions" / "%2Fdata%2Fworkspaces%2Froboco%2Fbackend%2Fbe-dev-1" / sid
    )
    _write_updates(target / "updates.jsonl", [10])
    found = gu.find_updates_path(home, cwd, sid)
    assert found is not None
    assert found == target / "updates.jsonl"


def test_find_updates_path_none_when_absent(tmp_path: Path) -> None:
    home = tmp_path / ".grok"
    assert gu.find_updates_path(home, "/x", "sid") is None
    assert gu.find_updates_path(home, "", "sid") is None  # missing cwd
    assert gu.find_updates_path(home, "/x", "") is None  # missing session id


def test_usage_and_cost_prices_total_at_output_rate() -> None:
    # grok-build output rate is $2.00/1M → 1M tokens = $2.00.
    tokens, cost = gu.usage_and_cost("grok-build", 1_000_000)
    assert tokens == 1_000_000  # noqa: PLR2004
    assert abs(cost - 2.00) < 1e-6  # noqa: PLR2004


def test_main_writes_usage_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".grok"
    cwd = "/ws/be-dev-1"
    sid = "sid-1"
    target = home / "sessions" / "%2Fws%2Fbe-dev-1" / sid
    _write_updates(target / "updates.jsonl", [1234])
    out = tmp_path / "usage.json"
    monkeypatch.setattr(gu, "USAGE_OUT_PATH", out)
    monkeypatch.setenv("GROK_HOME", str(home))
    monkeypatch.setenv("ROBOCO_GROK_RUN_CWD", cwd)
    monkeypatch.delenv("ROBOCO_GROK_RUN_LOG", raising=False)
    monkeypatch.setenv("ROBOCO_AGENT_SESSION_ID", sid)
    monkeypatch.setenv("ROBOCO_AGENT_MODEL", "grok-build")
    assert gu.main() == 0
    data = json.loads(out.read_text())
    assert data["total_tokens"] == 1234  # noqa: PLR2004
    assert data["model"] == "grok-build"
    assert data["cost_usd"] > 0.0


def test_capture_session_usage_writes_running_total(tmp_path: Path) -> None:
    home = tmp_path / ".grok"
    cwd = "/ws/intake-1"
    sid = "sid-x"
    target = home / "sessions" / "%2Fws%2Fintake-1" / sid
    _write_updates(target / "updates.jsonl", [100, 900, 500])
    out = tmp_path / "usage.json"
    tokens = gu.capture_session_usage(
        cwd=cwd, session_id=sid, model="grok-build", out_path=out, grok_home=home
    )
    assert tokens == 900  # noqa: PLR2004 — the running max is the chat total
    data = json.loads(out.read_text())
    assert data["total_tokens"] == 900  # noqa: PLR2004
    assert data["cost_usd"] > 0.0


def test_capture_session_usage_zero_when_session_absent(tmp_path: Path) -> None:
    out = tmp_path / "usage.json"
    tokens = gu.capture_session_usage(
        cwd="/ws/x",
        session_id="missing",
        model="grok-build",
        out_path=out,
        grok_home=tmp_path / ".grok",
    )
    assert tokens == 0
    # A zero session still writes a usage file (a real zero-cost run).
    assert json.loads(out.read_text())["total_tokens"] == 0


def test_session_id_from_run_log_reads_the_real_id(tmp_path: Path) -> None:
    log = tmp_path / "run.json"
    log.write_text(
        json.dumps({"text": "ok", "sessionId": "019edd9d-real", "stopReason": "End"}),
        encoding="utf-8",
    )
    assert gu.session_id_from_run_log(log) == "019edd9d-real"


def test_session_id_from_run_log_reads_streaming_ndjson(tmp_path: Path) -> None:
    # streaming-json: the id rides on the terminal `end` event line.
    log = tmp_path / "run.ndjson"
    log.write_text(
        '{"type":"thought","data":"hm"}\n'
        '{"type":"text","data":"ok"}\n'
        '{"type":"end","stopReason":"EndTurn","sessionId":"019stream-real"}\n',
        encoding="utf-8",
    )
    assert gu.session_id_from_run_log(log) == "019stream-real"


def test_session_id_from_run_log_none_for_bad_log(tmp_path: Path) -> None:
    assert gu.session_id_from_run_log(tmp_path / "absent.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert gu.session_id_from_run_log(bad) is None
    idless = tmp_path / "idless.json"
    idless.write_text(json.dumps({"text": "ok"}), encoding="utf-8")
    assert gu.session_id_from_run_log(idless) is None


def test_main_prefers_run_log_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # grok ignores a requested id, so the real id comes from the run log — it must
    # win over the ROBOCO_AGENT_SESSION_ID fallback (which points at no store).
    home = tmp_path / ".grok"
    cwd = "/ws/be-dev-1"
    real_sid = "real-sid"
    _write_updates(
        home / "sessions" / "%2Fws%2Fbe-dev-1" / real_sid / "updates.jsonl", [777]
    )
    run_log = tmp_path / "run.json"
    run_log.write_text(json.dumps({"sessionId": real_sid}), encoding="utf-8")
    out = tmp_path / "usage.json"
    monkeypatch.setattr(gu, "USAGE_OUT_PATH", out)
    monkeypatch.setenv("GROK_HOME", str(home))
    monkeypatch.setenv("ROBOCO_GROK_RUN_CWD", cwd)
    monkeypatch.setenv("ROBOCO_GROK_RUN_LOG", str(run_log))
    monkeypatch.setenv("ROBOCO_AGENT_SESSION_ID", "ignored-fallback")
    monkeypatch.setenv("ROBOCO_AGENT_MODEL", "grok-build")
    assert gu.main() == 0
    assert json.loads(out.read_text())["total_tokens"] == 777  # noqa: PLR2004


def test_main_warns_when_run_log_yields_no_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # RUN_LOG points at a malformed file; the env fallback id points at no
    # store, so usage is 0 — but the parse miss must be warned, not silent.
    bad_log = tmp_path / "bad.json"
    bad_log.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(gu, "USAGE_OUT_PATH", tmp_path / "usage.json")
    monkeypatch.setenv("GROK_HOME", str(tmp_path / ".grok"))
    monkeypatch.setenv("ROBOCO_GROK_RUN_CWD", "/ws/be-dev-1")
    monkeypatch.setenv("ROBOCO_GROK_RUN_LOG", str(bad_log))
    monkeypatch.setenv("ROBOCO_AGENT_SESSION_ID", "fallback-sid")
    monkeypatch.setenv("ROBOCO_AGENT_MODEL", "grok-build")
    with caplog.at_level("WARNING", logger="roboco.llm.providers.grok_cli_usage"):
        assert gu.main() == 0
    assert any("run log" in r.message.lower() for r in caplog.records)
