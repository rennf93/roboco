# DevOps Agent
# Infra authoring toolchain: the agent writes Dockerfiles/compose/k8s/
# terraform and must be able to validate its own output (compose config,
# kubeconform-free kubectl dry-run, helm template, terraform validate,
# hadolint/yamllint/shellcheck).

FROM roboco-agent-base

# Pinned tool versions. Every download is fetched per-TARGETARCH (amd64 in
# CI, arm64 on the NAS); each URL was verified to ship BOTH architectures
# (2026-09-20).
ARG DOCKER_CLI_VERSION=28.4.0
ARG DOCKER_COMPOSE_VERSION=2.39.4
ARG KUBECTL_VERSION=v1.33.4
ARG HELM_VERSION=v3.18.6
ARG TERRAFORM_VERSION=1.13.3
ARG HADOLINT_VERSION=v2.12.0
ARG YAMLLINT_VERSION=1.37.1

# TARGETARCH is a predefined global arg; it must be redeclared in-stage to be
# visible to the RUN below (silently empty otherwise, which 404s every URL).
ARG TARGETARCH

USER root

# The docker CLI baked here is for `docker compose config` validation of
# authored compose files ONLY. No docker daemon ships in this image and no
# docker socket is ever mounted (agent containers cannot reach the host
# daemon by design, see roboco_default/roboco_data network split in
# AGENTS.md). Do not "fix" that by adding a socket mount or dockerd.
# Toolchain layout: a single RUN layer, binaries installed to /usr/local/bin
# (compose plugin to /usr/local/lib/docker/cli-plugins), checksums verified
# for kubectl/helm/terraform (the upstreams publish them; docker CLI, compose
# plugin and hadolint are pinned by version only), apt lists and /tmp cleaned
# in the same layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
        unzip shellcheck \
    && case "$TARGETARCH" in \
        amd64) DOCKER_ARCH=x86_64; K8S_ARCH=amd64; HADOLINT_ARCH=x86_64 ;; \
        arm64) DOCKER_ARCH=aarch64; K8S_ARCH=arm64; HADOLINT_ARCH=arm64 ;; \
    esac \
    # hadolint names its assets x86_64/amd64 but arm64 (NOT aarch64), hence
    # the dedicated HADOLINT_ARCH above.
    # Docker CLI (static binary, daemon-less client).
    && curl -fsSL "https://download.docker.com/linux/static/stable/${DOCKER_ARCH}/docker-${DOCKER_CLI_VERSION}.tgz" -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /tmp docker/docker \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    # Docker compose plugin (found by the CLI in /usr/local/lib/docker/cli-plugins).
    && mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL "https://github.com/docker/compose/releases/download/v${DOCKER_COMPOSE_VERSION}/docker-compose-linux-${DOCKER_ARCH}" -o /usr/local/lib/docker/cli-plugins/docker-compose \
    # kubectl (pinned, checksum validated).
    && curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${K8S_ARCH}/kubectl" -o /tmp/kubectl \
    && curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${K8S_ARCH}/kubectl.sha256" -o /tmp/kubectl.sha256 \
    && echo "$(cat /tmp/kubectl.sha256)  /tmp/kubectl" | sha256sum -c - \
    && mv /tmp/kubectl /usr/local/bin/kubectl \
    # helm (pinned, checksum validated; downloaded under its canonical name
    # because the .sha256sum file references that exact filename).
    && curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-${K8S_ARCH}.tar.gz" -o "/tmp/helm-${HELM_VERSION}-linux-${K8S_ARCH}.tar.gz" \
    && curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-${K8S_ARCH}.tar.gz.sha256sum" -o /tmp/helm.sha256sum \
    && cd /tmp && sha256sum -c helm.sha256sum && cd / \
    && tar -xzf "/tmp/helm-${HELM_VERSION}-linux-${K8S_ARCH}.tar.gz" -C /tmp "linux-${K8S_ARCH}/helm" \
    && mv "/tmp/linux-${K8S_ARCH}/helm" /usr/local/bin/helm \
    # terraform (pinned, checksum validated against the release SHA256SUMS;
    # downloaded under its canonical name for the same reason as helm).
    && curl -fsSL "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${K8S_ARCH}.zip" -o "/tmp/terraform_${TERRAFORM_VERSION}_linux_${K8S_ARCH}.zip" \
    && curl -fsSL "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_SHA256SUMS" -o /tmp/terraform.SHA256SUMS \
    && cd /tmp && sha256sum -c --ignore-missing terraform.SHA256SUMS && cd / \
    && unzip -q "/tmp/terraform_${TERRAFORM_VERSION}_linux_${K8S_ARCH}.zip" -d /tmp \
    && mv /tmp/terraform /usr/local/bin/terraform \
    # hadolint (pinned; no official checksum file published upstream).
    && curl -fsSL "https://github.com/hadolint/hadolint/releases/download/${HADOLINT_VERSION}/hadolint-linux-${HADOLINT_ARCH}" -o /usr/local/bin/hadolint \
    # yamllint goes into the image's venv, the way the base/role images
    # install Python tooling (uv pip against /app/.venv, not bare pip).
    && uv pip install --python /app/.venv/bin/python "yamllint==${YAMLLINT_VERSION}" \
    && chmod 0755 /usr/local/bin/docker /usr/local/bin/kubectl /usr/local/bin/helm \
        /usr/local/bin/terraform /usr/local/bin/hadolint \
        /usr/local/lib/docker/cli-plugins/docker-compose \
    && rm -rf /var/lib/apt/lists/* /tmp/*

USER agent

LABEL role="devops"
LABEL description="DevOps agent - infra authoring (Docker, compose, K8s, Terraform) and linting"
