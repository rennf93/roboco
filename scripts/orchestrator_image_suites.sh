#!/usr/bin/env bash
# Run, inside a built orchestrator image and as root, the suites that only
# mean something there: the git-user helper's live contract, the hostile-clone
# suite (the real privilege drop, ROBOCO_WORKSPACE_GIT_DROP=required as the
# image sets it), and the live throwaway workspace-container and Entire hook
# suites (sibling containers over the host's Docker, as in production). On a
# host they skip. The "Orchestrator image suites" CI workflow runs exactly
# this, so a local run is the same run.
#
#   scripts/orchestrator_image_suites.sh IMAGE WORKDIR
#
# IMAGE: a built orchestrator image (docker/orchestrator.Dockerfile).
# WORKDIR: a host directory for pytest's temp files, mounted at the same path
#   inside: the suites bind-mount clones into sibling containers, and the
#   Docker daemon resolves bind sources on the host.
# DOCKER: the docker command, e.g. DOCKER="sudo -n docker" (default: docker).
# SUITES_CONTAINER: the test container's name (default:
#   roboco-orchestrator-image-suites).
#
# The image ships without the dev extras: they are installed into the
# container's copy of /app/.venv from the image's own uv.lock. tests/,
# .entire/ (the Entire suite seeds a project from it) and docker/git-support/
# (the hostile-clone suite copies it) are mounted read-only from this checkout.
# Every test here only skips when it is not in the image, so a run that skips
# any test fails: a green run must be one that exercised the drop.
set -euo pipefail

image="${1:?usage: $0 IMAGE WORKDIR}"
workdir="${2:?usage: $0 IMAGE WORKDIR}"
read -r -a docker <<< "${DOCKER:-docker}"
repo="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$workdir"
workdir="$(cd "$workdir" && pwd)"

"${docker[@]}" run --rm \
    --name "${SUITES_CONTAINER:-roboco-orchestrator-image-suites}" \
    --user 0:0 \
    --no-healthcheck \
    --entrypoint /bin/sh \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$workdir:$workdir" \
    -v "$repo/tests:/app/tests:ro" \
    -v "$repo/.entire:/app/.entire:ro" \
    -v "$repo/docker/git-support:/app/docker/git-support:ro" \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -e ROBOCO_WORKSPACE_CONTAINER_LIVE=1 \
    -e ROBOCO_ENTIRE_LIVE=1 \
    -e ROBOCO_WORKSPACE_CONTAINER_IMAGE="$image" \
    -e SUITES_WORKDIR="$workdir" \
    "$image" -c '
        set -e
        uv sync --frozen --extra dev --quiet
        python -m pytest -p no:cacheprovider --no-cov -rs \
            --basetemp "$SUITES_WORKDIR/pytest" \
            --junitxml "$SUITES_WORKDIR/junit.xml" \
            tests/integration/test_git_user_helper_live.py \
            tests/integration/test_hostile_workspace_clone.py \
            tests/integration/test_workspace_container_live.py \
            tests/integration/test_entire_hooks_live.py
        python -c "
import sys, xml.etree.ElementTree as ET
suites = ET.parse(sys.argv[1]).getroot().iter(\"testsuite\")
skipped = sum(int(suite.get(\"skipped\", 0)) for suite in suites)
sys.exit(f\"{skipped} test(s) skipped inside the image\" if skipped else 0)
" "$SUITES_WORKDIR/junit.xml"
    '
