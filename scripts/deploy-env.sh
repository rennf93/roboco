# shellcheck shell=bash
# Helpers for scripts/deploy-nas.sh, which sources this file from the stack
# dir: host-specific .env values (each reads and appends ./.env in the caller's
# working directory and never replaces a value that is already declared) and
# the order locally built images are built in.

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

# The locally built roboco-* images a Dockerfile builds FROM (any stage, tag
# or digest dropped), one per line: the images the deploy must have fresh
# before it builds this one.
roboco_parent_images_of() { # roboco_parent_images_of <dockerfile>
  awk '$1 == "FROM" { for (i = 2; i <= NF; i++) if ($i !~ /^--/) { print $i; break } }' "$1" \
    | sed 's/[:@].*//' | grep '^roboco-' || true
}

# compose's image order is not a build order: a child built before its
# roboco-* parent bakes the stale parent, or after an image wipe fails on the
# missing one with docker.io "pull access denied" (2026-11-02 blue deploy:
# agent-grok-secretary before agent-grok). Build a stale parent first. img and
# name are local, so image_fresh and record_fresh see the parent's, and the
# caller's loop variables are untouched. Uses deploy-nas.sh's image_fresh,
# record_fresh and HASH_LABEL.
build_stale_parents_of() { # build_stale_parents_of <dockerfile>
  local parent img name pdf
  for parent in $(roboco_parent_images_of "$1"); do
    img="$parent"
    name="${parent#roboco-}"
    pdf="docker/$name.Dockerfile"
    [ -f "$pdf" ] || continue
    image_fresh "$img" "$name" && continue
    build_stale_parents_of "$pdf"
    echo "[deploy] building $img (parent of ${1#docker/}) ..."
    if docker build -q -t "$img:latest" $HASH_LABEL -f "$pdf" . ||
       docker build -q -t "$img:latest" $HASH_LABEL -f "$pdf" .; then
      record_fresh
    else
      echo "[deploy] WARNING: build failed for $img" >&2
    fi
  done
}
