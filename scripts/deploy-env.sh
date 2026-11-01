# shellcheck shell=bash
# Host-specific .env values for scripts/deploy-nas.sh, which sources this file
# from the stack dir. Every helper reads and appends ./.env in the caller's
# working directory and never replaces a value that is already declared.

# Declared in the environment this runs with, or as a line in .env, even when
# empty: an operator's explicit empty value is a decision, not a gap.
env_key_declared() { # env_key_declared <VAR>
  [ -n "${!1+x}" ] && return 0
  grep -qE "^$1=" .env 2>/dev/null
}

# The non-empty value set in the environment or .env, or nothing.
env_value() { # env_value <VAR>
  local value="${!1:-}"
  [ -n "$value" ] || value="$(grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
  printf '%s' "$value"
}

append_env_value() { # append_env_value <VAR> <VALUE>
  # Start on a fresh line even if .env does not end with a newline.
  [ ! -s .env ] || [ -z "$(tail -c1 .env)" ] || echo >> .env
  echo "$1=$2" >> .env
}

# Redis runs with requirepass, and compose refuses to start without
# ROBOCO_REDIS_PASSWORD (`:?`). The first deploy after that change generates
# one into .env; it never replaces a value that is already set (in .env or
# in the environment this script runs with).
ensure_env_secret() { # ensure_env_secret <VAR>
  local generated
  [ -n "$(env_value "$1")" ] && return 0
  generated="$(python3 -c 'import secrets; print(secrets.token_hex(32))')" \
    || { echo "[deploy] could not generate $1 (python3 failed); add it to .env by hand" >&2; exit 1; }
  append_env_value "$1" "$generated"
  echo "[deploy] generated $1 into .env (it did not have one)"
}

# The IPv4 gateway docker assigned <network>, or nothing when the network does
# not exist yet or has no IPv4 gateway.
docker_network_ipv4_gateway() { # docker_network_ipv4_gateway <network>
  local gateway
  for gateway in $(docker network inspect "$1" \
      --format '{{range .IPAM.Config}}{{.Gateway}} {{end}}' 2>/dev/null || true); do
    if python3 -c 'import ipaddress, sys; ipaddress.IPv4Address(sys.argv[1])' \
        "$gateway" 2>/dev/null; then
      echo "$gateway"
      return 0
    fi
  done
}

# Host-proxied tailnet traffic (Tailscale Serve on this host -> localhost:3000
# -> docker's port proxy) reaches nginx from the gateway of the network nginx
# sits on, so that address is the one recorded X-Forwarded-For hop the guard's
# resolver must peel to attribute a request to its real tailnet client
# (ROBOCO_GUARD_TRUSTED_CHAIN_PEERS, roboco/security.py
# _build_trusted_hop_networks). It belongs to this docker host, so it is read
# from docker here rather than committed. Before the network exists (a first
# deploy) nothing is written and the next deploy derives it.
ensure_env_chain_peer() { # ensure_env_chain_peer <network>
  local gateway
  env_key_declared ROBOCO_GUARD_TRUSTED_CHAIN_PEERS && return 0
  gateway="$(docker_network_ipv4_gateway "$1")"
  if [ -z "$gateway" ]; then
    echo "[deploy] $1 has no IPv4 gateway yet; ROBOCO_GUARD_TRUSTED_CHAIN_PEERS is derived on the next deploy"
    return 0
  fi
  append_env_value ROBOCO_GUARD_TRUSTED_CHAIN_PEERS "$gateway"
  echo "[deploy] set ROBOCO_GUARD_TRUSTED_CHAIN_PEERS=$gateway in .env (the $1 gateway host-proxied traffic arrives from)"
}
