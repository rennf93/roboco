# =============================================================================
# Decisions sidecar (the built-in Laya tier of the Decisions service) - CPU-only
# =============================================================================
# Runs Laya (Convai Innovations' typed-decision models, Apache-2.0: library
# github.com/NandhaKishorM/laya, weights convaiinnovations/laya, the
# "typed-decisions" checkpoint) through the library's OFFICIAL onnxruntime
# path (laya.onnx_agent.ONNXAgent; onnxruntime for inference) and mirrors the
# OpenRouter Decisions wire shape at POST /api/alpha/decisions so
# roboco/services/decisions/client.py speaks ONE implementation against both
# tiers (docs/internal/decisions-spec.md sections 8 Stage 0.5 and 10).
#
# WHY THE BUILDER STAGE (verified against upstream v0.3.20 and live,
# 2026-09-26): the typed-decisions HF subfolder ships model.safetensors +
# rl_agent_config.json + encoder/ + tokenizer/ but NO ONNX file, and
# ONNXAgent refuses to load without one ("run export_onnx.py first"). The
# builder therefore exports laya.onnx (+ its external .data file) from the
# torch checkpoint with upstream's own export script at the SAME pin as the
# library, then drops the torch weights: the runtime stage serves through
# onnxruntime and never runs torch inference (torch stays installed only
# because laya's own package imports it at load time).
#
# The export is fp32 (upstream's script has no quantization flag at this
# pin): laya.onnx ~3.5MB of graph + laya.onnx.data ~1.7GB of external
# weights, ~1-2GB RAM at inference - the spec's documented envelope. When
# upstream ships int8 export, flip it HERE and nothing else changes.
# =============================================================================

FROM python:3.13-slim-bookworm AS builder

# LAYA_HF_REVISION pins the Hugging Face revision of the convaiinnovations/laya
# checkpoint baked into the image below. PRODUCTION BUILDS MUST PIN THE FULL
# COMMIT HASH: the default "main" tracks the moving branch, so two builds of
# this Dockerfile can bake different weights. Build with e.g.
#   docker build -f docker/decisions.Dockerfile --build-arg LAYA_HF_REVISION=<hash> .
ARG LAYA_HF_REVISION=main

# LAYA_LIB_TAG pins the laya LIBRARY revision installed in BOTH stages (the
# weights pin above covers only the checkpoint) and the export script pulled
# from the same tag, so loader, exporter, and runtime code can never drift
# apart. Defaults to the v0.3.20 release tag (the version the Hummin gate's
# calibration baseline was measured on; runtime fixes only, checkpoints
# unchanged). PRODUCTION BUILDS SHOULD STILL PIN THE FULL COMMIT HASH: a tag
# can be re-pointed upstream. server.py's _load_agent docstring depends on
# ONNXAgent's signature at the pinned revision, so bump the pins together and
# re-verify that docstring on every bump. Build with e.g.
#   docker build -f docker/decisions.Dockerfile --build-arg LAYA_LIB_TAG=<ref> .
ARG LAYA_LIB_TAG=v0.3.20

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# Builder deps: torch from the CPU index FIRST (so the resolver keeps the
# CPU wheel instead of pulling the CUDA build to satisfy laya's core dep),
# then laya with the [onnx] extra (onnx + onnxruntime) and onnxscript
# (required by torch>=2.13's ONNX exporter, verified live: without it
# export fails with ModuleNotFoundError). huggingface_hub pulls the
# checkpoint at build time.
RUN uv pip install --system --no-cache \
        torch \
        --index-url https://download.pytorch.org/whl/cpu
RUN uv pip install --system --no-cache \
        "laya[onnx] @ git+https://github.com/NandhaKishorM/laya.git@${LAYA_LIB_TAG}" \
        onnxscript \
        huggingface_hub

# Bake the typed-decisions checkpoint at build, pinned to LAYA_HF_REVISION
# (see the ARG above). Only the typed-decisions subfolder is downloaded.
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

# Export the ONNX graph + external weights from the torch checkpoint with
# upstream's export script at the SAME tag as the installed library, then
# drop the torch weights (nothing in the runtime reads them).
RUN curl -fsSL \
        "https://raw.githubusercontent.com/NandhaKishorM/laya/${LAYA_LIB_TAG}/scripts/export_onnx.py" \
        -o /tmp/export_onnx.py \
    && python /tmp/export_onnx.py \
        --model /models/typed-decisions \
        --output /models/typed-decisions/laya.onnx \
    && rm /models/typed-decisions/model.safetensors

# ----------------------------------------------------------------------------
# Runtime: onnxruntime inference only. torch is still installed (laya's own
# package imports it at load time - laya.agent._fix_tokenizer_config runs on
# every ONNXAgent construction), but never used for inference.
# ----------------------------------------------------------------------------
FROM python:3.13-slim-bookworm

ARG LAYA_LIB_TAG=v0.3.20

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

RUN uv pip install --system --no-cache \
        torch \
        --index-url https://download.pytorch.org/whl/cpu
RUN uv pip install --system --no-cache \
        "laya[onnx] @ git+https://github.com/NandhaKishorM/laya.git@${LAYA_LIB_TAG}" \
        fastapi \
        uvicorn \
        huggingface_hub

COPY --from=builder /models /models

# Checkpoint context math (verified against upstream + live, 2026-09): the
# typed-decisions checkpoint is a 1024-token ModernBERT with
# head_max_len=256, leaving roughly 768 tokens for state + question text -
# the client's 1800-char total budget (~450-600 tokens) fits under it. Do
# NOT repoint LAYA_SUBFOLDER at the repo-root english checkpoint without
# halving that budget: it is a 512-token model (head 192), and overlong
# reads silently truncate the rubric tail - the exact failure mode the
# Hummin gate hit before it measured checkpoints. Calibration note: the
# checkpoint ships fitted temperatures (choice/score/noul vector + per-
# option-count buckets), and the server refuses to serve without them; the
# choice:11+ bucket value is below upstream's clamp floor, so 12+-option
# choices carry clamped-uncalibrated confidences - one more reason the
# tool-spotlight pilot caps its option band at 20 and gates at 0.7. AMP
# note: upstream documents AMP dtype as a probability-drift source (bf16
# moves probabilities up to 0.073 vs fp32); if a calibration offset shows
# up on this CPU sidecar, investigate LAYA_CPU_AMP before refitting.
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
