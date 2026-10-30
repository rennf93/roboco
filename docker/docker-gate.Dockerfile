# =============================================================================
# RoboCo's Docker gate (trail #34): the orchestrator reaches Docker only here.
# =============================================================================
# docker/docker-gate/gate.py listens on an owner-only Unix socket in the
# docker-gate volume and forwards to the host's /var/run/docker.sock only the
# Engine API calls RoboCo makes, and only with what RoboCo asks for. No node,
# git or docker CLI: Python and h11.
#
# USER is 65534 so the image never runs as root by default. The compose files
# run it as 0:0 (with every capability dropped but CHOWN, for the socket's
# owner, and DAC_READ_SEARCH, to walk owner-only bind roots for symlinks)
# because the host socket is root:docker 0660, and the docker group's gid
# differs per host; the compose comment says so where it happens.
# =============================================================================

FROM python:3.12-slim-bookworm

# h11 is the gate's only dependency, at the version uv.lock pins.
RUN pip install --no-cache-dir 'h11==0.16.0'

COPY docker/docker-gate/gate.py /app/gate.py

USER 65534:65534

# Healthy means the listener is the owner-only socket it should be and Docker
# answers on the upstream socket (gate.py explains why it does not connect
# through its own listener).
HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=10s \
    CMD ["python", "/app/gate.py", "--health"]

ENTRYPOINT ["python", "/app/gate.py"]
