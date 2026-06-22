"""Runner: per-file dispatch, waiver filtering, fail-loud on grammar failure."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from roboco.conventions.grammars import GrammarUnavailable
from roboco.conventions.runner import ValidatorCouldNotRun, run
from roboco.foundation.policy.conventions.models import (
    ConventionsStandard,
    Module,
    Waiver,
)

if TYPE_CHECKING:
    from pathlib import Path

_MODEL_PY = b"from pydantic import BaseModel\nclass M(BaseModel):\n    x: int\n"


def _write(root: Path, rel: str, content: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_runner_flags_python_model_in_router(tmp_path: Path) -> None:
    _write(tmp_path, "app/routers/users.py", _MODEL_PY)
    std = ConventionsStandard(
        modules=[Module(path="app/routers", purpose="r", forbidden=["model"])]
    )
    findings = run(tmp_path, ["app/routers/users.py"], std)
    assert [f.rule for f in findings] == ["no_models_in_routers"]


def test_runner_drops_waived_finding(tmp_path: Path) -> None:
    _write(tmp_path, "app/routers/legacy.py", _MODEL_PY)
    std = ConventionsStandard(
        modules=[Module(path="app/routers", purpose="r", forbidden=["model"])],
        waivers=[
            Waiver(
                path="app/routers/legacy.py", rule="no_models_in_routers", reason="x"
            )
        ],
    )
    assert run(tmp_path, ["app/routers/legacy.py"], std) == []


def test_runner_flags_ts_component_in_wrong_module(tmp_path: Path) -> None:
    _write(tmp_path, "src/pages/Home.tsx", b"export const Home = () => <div/>;\n")
    std = ConventionsStandard(
        modules=[Module(path="src/pages", purpose="pages", forbidden=["component"])]
    )
    findings = run(tmp_path, ["src/pages/Home.tsx"], std)
    assert any(f.kind == "component" for f in findings)


def test_runner_skips_unsupported_extension(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", b"# hi\n")
    assert run(tmp_path, ["README.md"], ConventionsStandard()) == []


def test_runner_skips_missing_file(tmp_path: Path) -> None:
    assert run(tmp_path, ["gone.py"], ConventionsStandard()) == []


def test_runner_is_fail_loud_on_grammar_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "x.py", b"x = 1\n")

    def boom(_source: bytes) -> list:
        raise GrammarUnavailable("python")

    monkeypatch.setattr("roboco.conventions.classify_python.classify_definitions", boom)
    with pytest.raises(ValidatorCouldNotRun):
        run(tmp_path, ["x.py"], ConventionsStandard())
