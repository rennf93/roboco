"""``docker run -e`` values leave argv for the docker CLI's environment."""

from __future__ import annotations

import pytest
from roboco.utils.docker_env import docker_cli_env, docker_options_without_env_values


def test_every_env_flag_form_becomes_a_bare_name() -> None:
    argv, values = docker_options_without_env_values(
        [
            "docker",
            "run",
            "-e",
            "ROBOCO_AGENT_TOKEN=tok-secret",
            "--env",
            "NEBIUS_API_KEY=neb-secret",
            "--env=ZAI_API_KEY=zai-secret",
            "-v",
            "/host:/c",
        ]
    )
    assert argv == [
        "docker",
        "run",
        "-e",
        "ROBOCO_AGENT_TOKEN",
        "-e",
        "NEBIUS_API_KEY",
        "-e",
        "ZAI_API_KEY",
        "-v",
        "/host:/c",
    ]
    assert values == {
        "ROBOCO_AGENT_TOKEN": "tok-secret",
        "NEBIUS_API_KEY": "neb-secret",
        "ZAI_API_KEY": "zai-secret",
    }
    assert not any("secret" in token for token in argv)


def test_values_keep_equals_signs_newlines_and_empty_strings() -> None:
    _, values = docker_options_without_env_values(
        ["-e", "PROMPT=line one\nline=two", "-e", "EMPTY="]
    )
    assert values == {"PROMPT": "line one\nline=two", "EMPTY": ""}


def test_a_bare_name_is_left_to_pass_through() -> None:
    argv, values = docker_options_without_env_values(["-e", "HOME"])
    assert argv == ["-e", "HOME"]
    assert values == {}


def test_the_last_assignment_of_a_name_wins_as_in_docker() -> None:
    _, values = docker_options_without_env_values(["-e", "A=1", "-e", "A=2"])
    assert values == {"A": "2"}


@pytest.mark.parametrize(
    "options",
    [["-e"], ["-e", "1BAD=x"], ["--env=no spaces=x"]],
)
def test_malformed_env_options_are_rejected(options: list[str]) -> None:
    with pytest.raises(ValueError):
        docker_options_without_env_values(options)


def test_cli_env_layers_values_over_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.setenv("ROBOCO_AGENT_TOKEN", "stale")
    env = docker_cli_env({"ROBOCO_AGENT_TOKEN": "fresh"})
    assert env["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert env["ROBOCO_AGENT_TOKEN"] == "fresh"
