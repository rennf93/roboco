# =============================================================================
# Decisions sidecar (the self-hosted tier of the Decisions service)
# =============================================================================
# Runs TWO llama.cpp decision servers behind the FastAPI proxy in
# docker/decisions/server.py, mirroring the OpenRouter Decisions wire shape
# at POST /api/alpha/decisions so roboco/services/decisions/client.py speaks
# ONE implementation against the self-hosted tiers and the OpenRouter
# fallback tier (docs/internal/decisions-spec.md sections 8 Stage 0.5 and 10).
# Decision-model support is llama.cpp PR #29818 (/v1/systemone, first in
# b11361), but the clef ARCHITECTURE is PR #29831, first in b11371: b11361
# dies with "unknown model architecture: 'clef'". The pin below is b11371,
# which is exactly #29831's merge commit; ggml-org/Clef-Flash-GGUF names
# #29831 as its requirement.
#
#   - clef tier (default): ggml-org/Clef-Flash-GGUF, Cloudflare's 9B decision
#     model (Apache-2.0, post-trained from Qwen3.5-9B), Q4_K_M GGUF ~6.7GB.
#     CEO ruling 2026-11-01 (trail #55): Clef-Flash replaces the 27B Clef,
#     which read ~5 tokens/s on the NAS CPU and returned 0 verdicts in 948
#     calls on 2026-10-28;
#   - laya (cheap option): ggml-org/Laya-GGUF, a 421M ModernBERT decision
#     model (Apache-2.0), Q8_0 GGUF ~449MB, ctx 4096.
#
# llama.cpp is built with the Vulkan backend (and the CPU backend, always):
# with the host's /dev/dri passed in (docker-compose.igpu.yaml), Clef-Flash
# runs on the NAS's Intel Iris Xe. Measured on the NAS 2026-11-01, Q4_K_M:
# Iris Xe 90 tok/s at 512 tokens, 81 at 2k, 73 at 4k; CPU 15-16 tok/s at 12
# threads. On real pilot payloads (45, 169-3371 tokens) Q4_K_M agreed with
# Q8_0 on 89 of 93 answers and ran p50 3.6 s, p95 22.9 s on the Iris Xe.
# Without a GPU device the Vulkan backend finds none and llama.cpp runs on
# the CPU (the entrypoint sets the thread count; the default was 2.4x slower).
#
# PRODUCTION BUILDS MUST KEEP THE FULL HASH PINS: a moving "main" would bake
# different weights per build. No torch, no onnxruntime: llama.cpp owns
# inference and the model-native calibration temperatures ride the GGUF.
#
# FETCH_CLEF_WEIGHTS=false exists for CI only: the image-build job validates
# the multi-stage build and smokes the laya tier without pulling the clef
# weights onto a shared runner (it runs the container with CLEF_ENABLED=false).
# Production builds keep the default (true) and bake both weights.
#
# Builder and runtime share Debian trixie: a binary compiled against one
# glibc does not load on an older one, and trixie is the first Debian with
# the glslc shader compiler and a Mesa new enough for llama.cpp's Vulkan
# backend on the Iris Xe (Mesa 25).
# =============================================================================

FROM python:3.12-slim-trixie AS builder

# LLAMA_CPP_REF pins the llama.cpp source the serving binary is built from.
# PRODUCTION BUILDS SHOULD PIN THE FULL TAG: a tag can be re-pointed
# upstream, but b-tags are immutable release markers.
ARG LLAMA_CPP_REF=b11371

# apt pins follow agent-base.Dockerfile's scheme: the upstream series trixie
# ships for its whole life, never a Debian revision (every point or security
# update replaces it), and ca-certificates by suite, never by version.
RUN apt-get update && apt-get install -y --no-install-recommends \
        'build-essential=12.12*' \
        ca-certificates/trixie-security \
        'cmake=3.31.*' \
        'git=1:2.47.*' \
        'glslc=2025.2*' \
        'libvulkan-dev=1.4.309*' \
        'spirv-headers=1.6.1*' \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN git clone --depth 1 --branch "${LLAMA_CPP_REF}" https://github.com/ggml-org/llama.cpp .

# GGML_NATIVE=ON optimizes the CPU backend for the build host's instruction
# set (the NAS image never leaves that host); GGML_VULKAN=ON adds the GPU
# backend. Static llama libs (BUILD_SHARED_LIBS=OFF), no tests or examples,
# no embedded web UI (the entrypoint runs llama-server with --no-webui).
RUN cmake -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_NATIVE=ON \
        -DGGML_VULKAN=ON \
        -DBUILD_SHARED_LIBS=OFF \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=OFF \
    && cmake --build build --target llama-server -j"$(nproc)"

# ----------------------------------------------------------------------------
# Weight fetch: both GGUFs from the HF resolve URLs, pinned to full commit
# hashes. Kept in its own stage so a builder/toolchain change never re-dls
# the weights and a weight re-pin never recompiles llama.cpp.
# ----------------------------------------------------------------------------
FROM debian:trixie-slim AS weights

# CLEF_HF_REVISION pins the ggml-org/Clef-Flash-GGUF revision (2026-11-01).
ARG CLEF_HF_REVISION=a5a18a6e13bb36573a5f11385a1e7b9a0c868e9e
ARG CLEF_GGUF_QUANT=Q4_K_M
# LAYA_HF_REVISION pins the ggml-org/Laya-GGUF revision; Q8_0 (~449MB) is
# the smallest F16/Q8 file in that repo (BF16 is ~844MB).
ARG LAYA_HF_REVISION=22265007700297ba9e128297e82540cf28c5d7d4
ARG FETCH_CLEF_WEIGHTS=true

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates/trixie-security \
        'curl=8.14.*' \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    install -d -m 0755 /models; \
    clef_gguf="/models/Clef-Flash-${CLEF_GGUF_QUANT}.gguf"; \
    if [ "${FETCH_CLEF_WEIGHTS}" = "true" ]; then \
      curl -fsSL --http1.1 --retry 3 \
        -o "$clef_gguf" \
        "https://huggingface.co/ggml-org/Clef-Flash-GGUF/resolve/${CLEF_HF_REVISION}/Clef-Flash-${CLEF_GGUF_QUANT}.gguf"; \
      [ -s "$clef_gguf" ]; \
    fi; \
    curl -fsSL --http1.1 --retry 3 \
      -o /models/Laya-Q8_0.gguf \
      "https://huggingface.co/ggml-org/Laya-GGUF/resolve/${LAYA_HF_REVISION}/Laya-Q8_0.gguf"; \
    [ -s /models/Laya-Q8_0.gguf ]

# ----------------------------------------------------------------------------
# Runtime: python + the proxy, the llama-server binary, the GGUFs, and the
# Vulkan loader + Mesa drivers the GPU backend loads at run time.
# ----------------------------------------------------------------------------
FROM python:3.12-slim-trixie

# llama-server's CPU backend uses OpenMP (GGML_OPENMP=ON): libgomp1 at
# runtime, matching llama.cpp's own runtime image. libvulkan1 +
# mesa-vulkan-drivers give it the Iris Xe when /dev/dri is passed in.
RUN apt-get update && apt-get install -y --no-install-recommends \
        'libgomp1=14.2.*' \
        'libvulkan1=1.4.309*' \
        'mesa-vulkan-drivers=25.0.*' \
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

# The clef input cap (CLEF_MAX_INPUT_TOKENS, default 8192) is not set here:
# compose passes it from ROBOCO_DECISIONS_CLEF_MAX_INPUT_TOKENS, and the
# entrypoint and server.py default it. A *TOKEN* name in ENV trips the
# scanner's sensitive-data rule (DL3064), though it is a token count.
ENV CLEF_MODEL_ID=ggml-org/Clef-Flash-GGUF:Q4_K_M \
    LAYA_MODEL_ID=ggml-org/Laya-GGUF \
    CLEF_GGUF_PATH=/models/Clef-Flash-${CLEF_GGUF_QUANT}.gguf \
    LAYA_GGUF_PATH=/models/Laya-Q8_0.gguf \
    CLEF_UPSTREAM_URL=http://127.0.0.1:8110 \
    LAYA_UPSTREAM_URL=http://127.0.0.1:8111 \
    UPSTREAM_TIMEOUT_S=180.0 \
    LAYA_CTX_SIZE=4096 \
    DECISIONS_HOST=0.0.0.0 \
    DECISIONS_PORT=8100 \
    PYTHONUNBUFFERED=1 \
    MESA_SHADER_CACHE_DIR=/tmp/mesa-shader-cache

WORKDIR /app
# --chmod: the files keep the build folder's own modes otherwise, and the NAS
# deploy copy is owner-only (0600), unreadable to this image's non-root user.
COPY --chmod=0644 docker/decisions/server.py /app/server.py
COPY --chmod=0755 docker/decisions/entrypoint.sh /app/entrypoint.sh
# Mesa caches compiled Vulkan shaders under the user's home, which this user
# does not have (/nonexistent): MESA_SHADER_CACHE_DIR above points it at /tmp.
# Nothing here needs root: the proxy and both llama-servers bind ports above
# 1024 and only read the weights, the binary and the proxy source, which stay
# root-owned so the served process cannot alter them. A dedicated system user
# with a fixed, numeric uid:gid. The GPU device node is reached through the
# host's render group, which compose adds (group_add) only where /dev/dri is
# passed in.
RUN chmod 0755 /usr/local/bin/llama-server \
    && groupadd --system --gid 10001 decisions \
    && useradd --system --uid 10001 --gid 10001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin decisions
USER 10001:10001

# Same probe contract compose pins: python stdlib urllib against /health,
# 200 = healthy. The image's start-period is longer than compose's 60s
# because a standalone run also loads the clef GGUF from disk before
# /health can answer 200.
HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=180s \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=3).status == 200 else 1)"]

EXPOSE 8100

CMD ["/app/entrypoint.sh"]
