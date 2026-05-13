# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim

ARG CODEX_NPM_VERSION=0.130.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    jq \
    procps \
    python3 \
    python3-pip \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Official CLI installs.
RUN npm install -g "@openai/codex@${CODEX_NPM_VERSION}"

# Bridge runtime deps.
COPY requirements-bridge.txt /tmp/requirements-bridge.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements-bridge.txt &&     rm /tmp/requirements-bridge.txt

# Runtime files.
COPY bin/codeseeq-entrypoint /usr/local/bin/codeseeq-entrypoint
COPY bin/codeseeq-bridge.py /usr/local/bin/codeseeq-bridge.py
COPY bin/codeseeq-healthcheck /usr/local/bin/codeseeq-healthcheck
COPY bin/codeseeq-print-config /usr/local/bin/codeseeq-print-config
COPY config/model-catalog.json /etc/codeseeq/model-catalog.json
COPY config/codex-model-catalog.json /etc/codeseeq/codex-model-catalog.json

RUN chmod +x \
    /usr/local/bin/codeseeq-entrypoint \
    /usr/local/bin/codeseeq-bridge.py \
    /usr/local/bin/codeseeq-healthcheck \
    /usr/local/bin/codeseeq-print-config

RUN groupadd -g 10001 codeseeq && \
    useradd -m -u 10001 -g 10001 -s /bin/bash codeseeq && \
    mkdir -p /workspace /home/codeseeq/.codeseeq /home/codeseeq/.config/codeseeq /run/codeseeq /var/log/codeseeq && \
    chown -R codeseeq:codeseeq /workspace /home/codeseeq /run/codeseeq /var/log/codeseeq /etc/codeseeq

WORKDIR /workspace

ENV CODESEEQ_IN_CONTAINER=1 \
    CODESEEQ_CODEX_HOME=/home/codeseeq/.codeseeq \
    CODESEEQ_CONFIG_HOME=/home/codeseeq/.config/codeseeq \
    CODESEEQ_WORKDIR=/workspace \
    CODESEEQ_OPENRESPONSES_HOST=127.0.0.1 \
    CODESEEQ_OPENRESPONSES_PORT=8080 \
    CODESEEQ_OPENRESPONSES_URL=http://127.0.0.1:8080/v1 \
    CODESEEQ_OPENRESPONSES_CMD=/usr/local/bin/codeseeq-bridge.py

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD /usr/local/bin/codeseeq-healthcheck || exit 1

USER codeseeq

ENTRYPOINT ["tini", "--", "/usr/local/bin/codeseeq-entrypoint"]
CMD ["codex"]
