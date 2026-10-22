FROM python:3.13.15-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1
COPY --from=ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Create non-root user for security (Debian already ships a 'sync' user)
RUN groupadd --gid 1000 vaultsync && \
    useradd --uid 1000 --gid vaultsync --shell /bin/bash --create-home vaultsync

# README.md is required by the build backend (pyproject readme = ...)
COPY --chown=vaultsync:vaultsync pyproject.toml uv.lock README.md ./
COPY --chown=vaultsync:vaultsync src ./src

# uv sync runs as root (build-time); fix venv ownership afterward.
# Only /app/.venv needs to be writable by vaultsync at runtime.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable && \
    chown -R vaultsync:vaultsync /app/.venv

USER vaultsync

# Cloud Scheduler passes "--task orgs|users|all" as container args.
ENTRYPOINT ["python", "-m", "password_vault_sync"]
CMD ["--task", "all"]
