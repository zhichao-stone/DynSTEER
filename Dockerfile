FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    DYNSTEER_BOOTSTRAP_VENV=/opt/bootstrap-venv \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /workspace/DynSTEER

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Build a reusable base environment. docker-compose mounts /opt/venv as a
# named volume; start.sh seeds that volume from /opt/bootstrap-venv once.
COPY pyproject.toml uv.lock README.md /workspace/DynSTEER/
RUN UV_PROJECT_ENVIRONMENT=/opt/bootstrap-venv uv sync --frozen --no-dev --no-install-project \
    && mkdir -p /opt/venv \
    && cp -a /opt/bootstrap-venv/. /opt/venv/

COPY . /workspace/DynSTEER
RUN chmod +x /workspace/DynSTEER/scripts/*.sh

ENTRYPOINT ["bash", "/workspace/DynSTEER/scripts/start.sh"]
