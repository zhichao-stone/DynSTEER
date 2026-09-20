FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/workspace/DynSTEER/.venv \
    DYNSTEER_BOOTSTRAP_VENV=/opt/bootstrap-venv \
    UV_LINK_MODE=copy \
    PATH="/workspace/DynSTEER/.venv/bin:${PATH}"

WORKDIR /workspace/DynSTEER

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Build a reusable bootstrap environment. start.sh seeds the project-local
# .venv from /opt/bootstrap-venv once when the mounted workspace has no venv.
COPY pyproject.toml uv.lock README.md /workspace/DynSTEER/
RUN UV_PROJECT_ENVIRONMENT=/opt/bootstrap-venv uv sync --frozen --no-dev --no-install-project

COPY . /workspace/DynSTEER
RUN chmod +x /workspace/DynSTEER/scripts/*.sh

ENTRYPOINT ["bash", "/workspace/DynSTEER/scripts/start.sh"]
