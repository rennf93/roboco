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
front/active-upstreams.conf (host-written atomically by scripts/deploy-nas.sh,
mounted read-only via the front/ DIRECTORY) holds three `set` directives
($active_panel / $active_orchestrator / $active_dispatcher) consumed by
variable proxy_pass targets. With the pinned Docker resolver, names resolve
at REQUEST time: nginx can never die on "host not found in upstream" when a
named color is not running (the blue-deploy outage), and a flip is a
graceful hot reload. The pinned invariants below follow the file.
"""

from __future__ import annotations

from pathlib import Path

_NGINX_CONF = Path(__file__).parents[2] / "deploy" / "nginx.conf"
_DEPLOY_SCRIPT = Path(__file__).parents[2] / "scripts" / "deploy-nas.sh"
_DISPATCHER_LOCATIONS = (
    "location /api/orchestrator/",
    "location /api/secretary/live/",
    "location /api/prompter/live/",
)
_INCLUDE = "/etc/nginx/front/active-upstreams.conf"


def _read_conf() -> str:
    return _NGINX_CONF.read_text()


def test_active_upstreams_come_from_the_front_directory_include() -> None:
    """No inline `upstream` blocks at all: the active color travels as `set`
    directives from the front/ directory include, resolved at request time
    via Docker's embedded DNS resolver. A file bind or an http-level include
    would regress either the inode trap or the `set` context."""
    conf = _read_conf()
    assert "\nupstream " not in conf, (
        "no upstream blocks: active color comes from the set-directive include"
    )
    assert f"include {_INCLUDE};" in conf
    # `set` is server-context only: the include must sit inside the server
    # block (after `server {`, which is the file's only server).
    assert conf.index("server {") < conf.index(f"include {_INCLUDE};")
    assert "resolver 127.0.0.11" in conf


def test_deploy_script_writes_variable_include_atomically() -> None:
    """The flip file the script writes must define the three variables the
    template consumes, via an atomic tmp+mv swap (the directory mount only
    propagates inode swaps, not in-place writes to a file-bound path)."""
    script = _DEPLOY_SCRIPT.read_text()
    for var in ("active_panel", "active_orchestrator", "active_dispatcher"):
        assert f"set \\${var} " in script, f"missing set directive for {var}"
    assert "mv -f" in script
    # The flip reloads (graceful) instead of restarting nginx: a restart
    # drops in-flight requests and is the outage shape that motivated the
    # variable design.
    assert "nginx -s reload" in script
    assert "restart nginx" not in script


def test_fleet_owning_locations_proxy_to_dispatcher() -> None:
    conf = _read_conf()
    for location in _DISPATCHER_LOCATIONS:
        start = conf.index(location)
        # The location's own block, up to its closing brace.
        block_end = conf.index("}", start)
        block = conf[start:block_end]
        assert "proxy_pass http://$active_dispatcher;" in block, (
            f"{location} must proxy to the dispatcher variable from the "
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


def test_generic_api_websocket_and_panel_locations_pin_their_variables() -> None:
    conf = _read_conf()
    for location, var in (
        ("location /api/ {", "proxy_pass http://$active_orchestrator;"),
        ("location /ws/ {", "proxy_pass http://$active_orchestrator;"),
        ("location / {", "proxy_pass http://$active_panel;"),
    ):
        start = conf.index(location)
        block_end = conf.index("}", start)
        block = conf[start:block_end]
        assert var in block
