"""Forge provider detection + registration-time validation — pure, no DB/IO."""

from __future__ import annotations

from roboco.foundation.policy.forge import (
    KNOWN_PROVIDERS,
    detect_provider,
    validate_project_forge,
)

# ---------------------------------------------------------------------------
# detect_provider — host extraction across https/ssh/.git/subgroup shapes
# ---------------------------------------------------------------------------


def test_detect_https_github() -> None:
    assert detect_provider("https://github.com/owner/repo.git") == "github"


def test_detect_https_github_no_dot_git_suffix() -> None:
    assert detect_provider("https://github.com/owner/repo") == "github"


def test_detect_https_github_with_token_userinfo() -> None:
    url = "https://x-access-token:ghp_abc123@github.com/owner/repo.git"
    assert detect_provider(url) == "github"


def test_detect_ssh_scp_syntax_github() -> None:
    assert detect_provider("git@github.com:owner/repo.git") == "github"


def test_detect_ssh_url_scheme_github() -> None:
    assert detect_provider("ssh://git@github.com/owner/repo.git") == "github"


def test_detect_https_gitlab_com() -> None:
    assert detect_provider("https://gitlab.com/group/project.git") == "gitlab"


def test_detect_ssh_scp_syntax_gitlab() -> None:
    assert detect_provider("git@gitlab.com:group/project.git") == "gitlab"


def test_detect_gitlab_subgroup_path_still_detects_host() -> None:
    # Subgroup paths (3+ segments) don't change host resolution — detect_provider
    # only looks at the host, never the path shape.
    url = "https://gitlab.com/group/subgroup/project.git"
    assert detect_provider(url) == "gitlab"


def test_detect_self_hosted_gitlab_host_is_unresolvable() -> None:
    # A self-hosted host can't be told apart from GHE/Gitea by host alone.
    assert detect_provider("https://gitlab.example.com/group/project.git") is None


def test_detect_self_hosted_https_unknown_host() -> None:
    assert detect_provider("https://git.internal.example/owner/repo.git") is None


def test_detect_bitbucket_host_unresolvable() -> None:
    assert detect_provider("https://bitbucket.org/owner/repo.git") is None


def test_detect_empty_string() -> None:
    assert detect_provider("") is None


def test_detect_unparseable_garbage() -> None:
    assert detect_provider("not a url at all") is None


# ---------------------------------------------------------------------------
# validate_project_forge — the registration-time gate
# ---------------------------------------------------------------------------


def test_empty_git_url_is_ok() -> None:
    assert validate_project_forge(None, None) is None
    assert validate_project_forge("", None) is None


def test_detected_github_no_explicit_provider_is_ok() -> None:
    assert validate_project_forge("https://github.com/owner/repo.git", None) is None


def test_detected_github_ssh_scp_no_explicit_provider_is_ok() -> None:
    assert validate_project_forge("git@github.com:owner/repo.git", None) is None


def test_explicit_github_provider_is_ok_regardless_of_host() -> None:
    # The GitHub Enterprise escape hatch — a non-github.com host is accepted
    # once the operator explicitly names the provider.
    url = "https://ghe.internal.example/owner/repo.git"
    assert validate_project_forge(url, "github") is None


def test_explicit_gitlab_provider_rejected_as_not_yet_supported() -> None:
    error = validate_project_forge("https://gitlab.com/group/project.git", "gitlab")
    assert error is not None
    assert "not yet" in error.lower()
    assert "gitlab" in error.lower()


def test_explicit_gitea_provider_rejected_as_not_yet_supported() -> None:
    error = validate_project_forge("https://gitea.example.com/owner/repo.git", "gitea")
    assert error is not None
    assert "not yet" in error.lower()
    assert "gitea" in error.lower()


def test_unknown_host_no_explicit_provider_rejected() -> None:
    error = validate_project_forge("https://git.internal.example/owner/repo.git", None)
    assert error is not None
    assert "github" in error.lower()


def test_detected_gitlab_no_explicit_provider_rejected() -> None:
    error = validate_project_forge("https://gitlab.com/group/project.git", None)
    assert error is not None
    assert "github" in error.lower()


def test_unknown_provider_string_rejected_naming_known_providers() -> None:
    error = validate_project_forge("https://github.com/owner/repo.git", "bitbucket")
    assert error is not None
    for provider in KNOWN_PROVIDERS:
        assert provider in error


def test_known_providers_tuple_is_the_documented_set() -> None:
    assert KNOWN_PROVIDERS == ("github", "gitlab", "gitea")
