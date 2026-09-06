"""docker/nginx.conf routing table: fleet-owning routes -> dispatcher.

The dispatcher (ROBOCO_ROLE=dispatcher) is the only process with a live
AgentOrchestrator fleet (spawn/waiting-agent in-memory state); the
orchestrator (ROBOCO_ROLE=api) runs the same routes against an always-empty
copy of that state (see roboco/runtime/orchestrator.py's role gate). These
tests parse the real nginx.conf as text (no nginx binary needed) and pin the
routing table so a future edit can't silently regress it back onto
`orchestrator`.
"""

from __future__ import annotations

from pathlib import Path

_NGINX_CONF = Path(__file__).parents[2] / "docker" / "nginx.conf"
_DISPATCHER_LOCATIONS = (
    "location /api/orchestrator/",
    "location /api/secretary/live/",
    "location /api/prompter/live/",
)


def _read_conf() -> str:
    return _NGINX_CONF.read_text()


def test_dispatcher_is_resolved_per_request_not_at_startup() -> None:
    """No `upstream dispatcher` block: nginx resolves upstream names once at
    config load and refuses to start when one is missing, which took the
    panel down whenever the dispatcher container was not up yet. The name
    goes through Docker's embedded DNS per request instead."""
    conf = _read_conf()
    assert "upstream dispatcher {" not in conf
    assert "resolver 127.0.0.11" in conf
    assert "set $dispatcher http://roboco-dispatcher:8000;" in conf


def test_fleet_owning_locations_proxy_to_dispatcher() -> None:
    conf = _read_conf()
    for location in _DISPATCHER_LOCATIONS:
        start = conf.index(location)
        # The location's own block, up to its closing brace.
        block_end = conf.index("}", start)
        block = conf[start:block_end]
        assert "proxy_pass $dispatcher;" in block, (
            f"{location} must proxy to the dispatcher via the runtime-resolved variable"
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
        assert "proxy_pass http://orchestrator;" in block
