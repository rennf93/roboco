"""deploy/nginx.conf routing table: fleet-owning routes -> dispatcher.

The dispatcher (ROBOCO_ROLE=dispatcher) is the only process with a live
AgentOrchestrator fleet (spawn/waiting-agent in-memory state); the
orchestrator (ROBOCO_ROLE=api) runs the same routes against an always-empty
copy of that state (see roboco/runtime/orchestrator.py's role gate). These
tests parse the real nginx.conf as text (no nginx binary needed) and pin the
routing table so a future edit can't silently regress it back onto
`orchestrator`.

2026-09-17 (blue-green): the conf moved from docker/nginx.conf to
deploy/nginx.conf and became a template that INCLUDES the color switch —
front/active-upstreams.conf (host-written by scripts/deploy-nas.sh, mounted
read-only) carries the color-suffixed `upstream` blocks (roboco_dispatcher /
roboco_orchestrator / roboco_panel) for whichever color is active. The
pinned invariants below follow the file.
"""

from __future__ import annotations

from pathlib import Path

_NGINX_CONF = Path(__file__).parents[2] / "deploy" / "nginx.conf"
_DISPATCHER_LOCATIONS = (
    "location /api/orchestrator/",
    "location /api/secretary/live/",
    "location /api/prompter/live/",
)


def _read_conf() -> str:
    return _NGINX_CONF.read_text()


def test_dispatcher_upstream_comes_from_the_active_color_include() -> None:
    """No inline `upstream roboco_dispatcher` block in the template: the
    upstream is color-suffixed and lives in front/active-upstreams.conf,
    which the deploy script writes ONLY for a color whose containers are
    already up and healthy — so startup name resolution is guaranteed by
    ordering, and a full host reboot converges via nginx's restart loop.
    Docker's embedded DNS resolver stays pinned for any per-request
    resolution the template still does."""
    conf = _read_conf()
    assert "upstream roboco_dispatcher {" not in conf
    assert "upstream roboco_orchestrator {" not in conf
    assert "include /etc/nginx/active-upstreams.conf;" in conf
    assert "resolver 127.0.0.11" in conf


def test_fleet_owning_locations_proxy_to_dispatcher() -> None:
    conf = _read_conf()
    for location in _DISPATCHER_LOCATIONS:
        start = conf.index(location)
        # The location's own block, up to its closing brace.
        block_end = conf.index("}", start)
        block = conf[start:block_end]
        assert "proxy_pass http://roboco_dispatcher;" in block, (
            f"{location} must proxy to the dispatcher upstream from the "
            "active-color include"
        )


def test_dispatcher_locations_precede_generic_api_location() -> None:
    """Documents the routing table order (nginx's own longest-prefix match
    would pick these anyway, but the file order must not drift from it)."""
    conf = _read_conf()
    generic_api_index = conf.index("location /api/ {")
    for location in _DISPATCHER_LOCATIONS:
        assert conf.index(location) < generic_api_index, (
            f"{location} must precede the generic /api/ location"
        )


def test_generic_api_and_websocket_locations_still_proxy_to_orchestrator() -> None:
    conf = _read_conf()
    for location in ("location /api/ {", "location /ws/ {"):
        start = conf.index(location)
        block_end = conf.index("}", start)
        block = conf[start:block_end]
        assert "proxy_pass http://roboco_orchestrator;" in block
