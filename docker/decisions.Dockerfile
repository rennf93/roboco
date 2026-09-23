# =============================================================================
# Decisions sidecar (the built-in Laya tier of the Decisions service) - CPU-only
# =============================================================================
# Runs Laya (Convai Innovations' typed-decision models, Apache-2.0: library
# github.com/NandhaKishorM/laya, weights convaiinnovations/laya, the
# "typed-decisions" checkpoint) through the library's OFFICIAL onnxruntime
# path (laya.onnx_agent.ONNXAgent; onnxruntime, no torch at runtime) and
# mirrors the OpenRouter Decisions wire shape at POST /api/alpha/decisions so
# roboco/services/decisions/client.py speaks ONE implementation against both
# tiers (docs/internal/decisions-spec.md sections 8 Stage 0.5 and 10).
#
# The laya install comes from the upstream GitHub repo (the PyPI extra story
# for the ONNXAgent path is still settling upstream; the git install with the
# [onnx] extra is the form the project documents for source installs). git is
# needed for that install and stays in the image (same posture as the
# orchestrator image, which also ships git).
#
# int8 quantization: at this pin upstream does not expose a documented
# env/flag for int8-quantized ONNX export (export tooling lives in
# upstream's export_onnx.py, outside the package API). The checkpoint is
# baked exactly as published; when upstream ships int8 weights or a stable
# flag, flip it HERE and nothing else changes. Revisit at the next pin bump.
# =============================================================================

FROM python:3.13-slim-bookworm

# LAYA_HF_REVISION pins the Hugging Face revision of the convaiinnovations/laya
# checkpoint baked into the image below. PRODUCTION BUILDS MUST PIN THE FULL
# COMMIT HASH: the default "main" tracks the moving branch, so two builds of
# this Dockerfile can bake different weights. Build with e.g.
#   docker build -f docker/decisions.Dockerfile --build-arg LAYA_HF_REVISION=<hash> .
ARG LAYA_HF_REVISION=main

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv, same source as the orchestrator image. The laya repo has no lockfile to
# pin against, so uv here is just the fast installer; versions resolve fresh
# at build time and are pinned by pinning the LAYA_HF_REVISION above.
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# Library + runtime deps. onnxruntime is listed explicitly so the ONNX path
# never depends on the extra resolving upstream; fastapi + uvicorn serve the
# adapter; huggingface_hub pulls the checkpoint at build time.
RUN uv pip install --system --no-cache \
        "laya[onnx] @ git+https://github.com/NandhaKishorM/laya.git" \
        onnxruntime \
        fastapi \
        uvicorn \
        huggingface_hub

# Bake the typed-decisions checkpoint at build, pinned to LAYA_HF_REVISION
# (see the ARG above). Only the typed-decisions subfolder is downloaded; the
# ONNX weights (laya.onnx) are expected to ship inside that subfolder - if a
# future revision stops carrying them, run upstream's export_onnx.py in a
# layer here at the same pin.
RUN python - <<'EOF'
import os
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="convaiinnovations/laya",
    revision=os.environ["LAYA_HF_REVISION"],
    local_dir="/models",
    allow_patterns=["typed-decisions/**"],
)
EOF

ENV LAYA_MODEL_ID=convaiinnovations/laya \
    LAYA_SUBFOLDER=typed-decisions \
    LAYA_MODELS_DIR=/models \
    LAYA_HOST=0.0.0.0 \
    LAYA_PORT=8100 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY docker/decisions/server.py /app/server.py

EXPOSE 8100

CMD ["python", "server.py"]
