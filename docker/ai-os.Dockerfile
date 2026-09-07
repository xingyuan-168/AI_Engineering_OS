FROM docker.io/library/python:3.12.14-bookworm@sha256:852282e520cc1754221fb2e061ab35b13b596e8112a731d60e2a8b471c973b7a

WORKDIR /workspace
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/codex-os-venv

RUN python -m pip install --no-cache-dir uv==0.12.3
COPY . .
RUN uv sync --frozen

USER 65532:65532
ENV PATH="/opt/codex-os-venv/bin:${PATH}"
