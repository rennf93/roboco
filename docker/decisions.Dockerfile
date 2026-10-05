# =============================================================================
# Decisions sidecar (the self-hosted tier of the Decisions service) - CPU-only
# =============================================================================
# Runs TWO llama.cpp decision servers behind the FastAPI proxy in
# docker/decisions/server.py, mirroring the OpenRouter Decisions wire shape
# at POST /api/alpha/decisions so roboco/services/decisions/client.py speaks
# ONE implementation against the self-hosted tiers and the OpenRouter
# fallback tier (docs/internal/decisions-spec.md sections 8 Stage 0.5 and 10).
# Decision-model support is llama.cpp PR #29818; the FIRST tag carrying the
# /v1/systemone endpoint is b11361 (b11352 predates the merge), so the pin
# below is b11361.
#
#   - clef (default sweet spot): ggml-org/Clef-GGUF, a 27B decision model
#     (Apache-2.0), Q4_K_M GGUF ~19.2GB, ctx 16384;
#   - laya (cheap option): ggml-org/Laya-GGUF, a 421M ModernBERT decision
#     model (Apache-2.0), Q8_0 GGUF ~449MB, ctx 4096.
#
# The builder stage compiles llama-server CPU-only from the pinned tag; the
# weight-fetch stage curls both GGUFs from the Hugging Face resolve URLs
# pinned to full commit hashes. PRODUCTION BUILDS MUST KEEP THE FULL HASH
# PINS: a moving "main" would bake different weights per build. No torch, no
# onnxruntime, no laya pip package, no convaiinnovations/laya weights: the
# ONNX era is gone, llama.cpp owns inference, and the model-native
# calibration temperatures ride the GGUF metadata (no rl_agent_config.json
# gate anywhere anymore).
#
# FETCH_CLEF_WEIGHTS=false exists for CI only: the image-build job validates
# the multi-stage build and smokes the laya tier without pulling 19GB of
# clef onto a shared runner (it runs the container with CLEF_ENABLED=false).
# Production builds keep the default (true) and bake both weights.
# =============================================================================

FROM ubuntu:24.04 AS builder

# LLAMA_CPP_REF pins the llama.cpp source the serving binary is built from.
# PRODUCTION BUILDS SHOULD PIN THE FULL TAG: a tag can be re-pointed
# upstream, but b-tags are immutable release markers. The default is the
# first release tag carrying /v1/systemone (see header).
ARG LLAMA_CPP_REF=b11361

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN git clone --depth 1 --branch "${LLAMA_CPP_REF}" https://github.com/ggml-org/llama.cpp .

# CPU-only build per docs/build.md at the pin: GGML_NATIVE=ON optimizes for
# the build host's instruction set (the NAS image never leaves that host),
# no CUDA/Vulkan/BLAS, static llama libs (BUILD_SHARED_LIBS=OFF), no tests
# or examples, and no embedded web UI (LLAMA_BUILD_UI defaults OFF and the
# entrypoint runs llama-server with --no-webui).
RUN cmake -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_NATIVE=ON \
        -DBUILD_SHARED_LIBS=OFF \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=OFF \
    && cmake --build build --target llama-server -j"$(nproc)"

# ----------------------------------------------------------------------------
# Weight fetch: both GGUFs from the HF resolve URLs, pinned to full commit
# hashes. Kept in its own stage so a builder/toolchain change never re-dlds
# 19GB and a weight re-pin never recompiles llama.cpp.
# ----------------------------------------------------------------------------
FROM debian:bookworm-slim AS weights

# CLEF_HF_REVISION pins the ggml-org/Clef-GGUF revision (2026-10-02).
ARG CLEF_HF_REVISION=5f70656b6670c65eb85ad07a11efe211b5f211bd
ARG CLEF_GGUF_QUANT=Q4_K_M
# LAYA_HF_REVISION pins the ggml-org/Laya-GGUF revision; Q8_0 (~449MB) is
# the smallest F16/Q8 file in that repo (BF16 is ~844MB).
ARG LAYA_HF_REVISION=22265007700297ba9e128297e82540cf28c5d7d4
ARG FETCH_CLEF_WEIGHTS=true

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    install -d -m 0755 /models; \
    clef_gguf="/models/Clef-${CLEF_GGUF_QUANT}.gguf"; \
    if [ "${FETCH_CLEF_WEIGHTS}" = "true" ]; then \
      curl -fsSL --retry 3 \
        -o "$clef_gguf" \
        "https://huggingface.co/ggml-org/Clef-GGUF/resolve/${CLEF_HF_REVISION}/Clef-${CLEF_GGUF_QUANT}.gguf"; \
      [ -s "$clef_gguf" ]; \
    fi; \
    curl -fsSL --retry 3 \
      -o /models/Laya-Q8_0.gguf \
      "https://huggingface.co/ggml-org/Laya-GGUF/resolve/${LAYA_HF_REVISION}/Laya-Q8_0.gguf"; \
    [ -s /models/Laya-Q8_0.gguf ]

# ----------------------------------------------------------------------------
# Runtime: python + the proxy, the llama-server binary, the GGUFs. NO torch,
# NO onnxruntime, NO laya pip package, NO huggingface weights of
# convaiinnovations/laya.
# ----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm

# llama-server's CPU backend uses OpenMP (GGML_OPENMP=ON): libgomp1 at
# runtime, matching llama.cpp's own runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# Same install pattern as the pre-swap Dockerfile (uv pip --system, unpinned).
# httpx is the proxy's async upstream transport; fastapi + uvicorn serve the
# wire.
RUN uv pip install --system --no-cache \
        fastapi \
        httpx \
        uvicorn

COPY --from=builder /src/build/bin/llama-server /usr/local/bin/llama-server
COPY --from=weights /models /models

ARG CLEF_GGUF_QUANT=Q4_K_M

ENV CLEF_MODEL_ID=ggml-org/Clef-GGUF:Q4_K_M \
    LAYA_MODEL_ID=ggml-org/Laya-GGUF \
    CLEF_GGUF_PATH=/models/Clef-${CLEF_GGUF_QUANT}.gguf \
    LAYA_GGUF_PATH=/models/Laya-Q8_0.gguf \
    CLEF_UPSTREAM_URL=http://127.0.0.1:8110 \
    LAYA_UPSTREAM_URL=http://127.0.0.1:8111 \
    UPSTREAM_TIMEOUT_S=120.0 \
    CLEF_CTX_SIZE=16384 \
    LAYA_CTX_SIZE=4096 \
    LLAMA_THREADS=0 \
    DECISIONS_HOST=0.0.0.0 \
    DECISIONS_PORT=8100 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY docker/decisions/server.py /app/server.py
COPY docker/decisions/entrypoint.sh /app/entrypoint.sh
RUN chmod 0755 /app/entrypoint.sh /usr/local/bin/llama-server

# Same probe contract compose pins: python stdlib urllib against /health,
# 200 = healthy. The image's start-period is longer than compose's 60s
# because a standalone run also loads the 19GB clef GGUF from disk before
# /health can answer 200.
HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=180s \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=3).status == 200 else 1)"]

EXPOSE 8100

CMD ["/app/entrypoint.sh"]
