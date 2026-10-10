# arena-sim as a container:  docker build -t arena-sim starter-kit && docker run -p 8080:8080 arena-sim
# Optional: mount the public data package and pass --data so cited IDs are checked against it.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-editable --no-install-project --extra sim
COPY arena_kit ./arena_kit
RUN uv sync --frozen --no-dev --no-editable --extra sim && useradd --create-home sim
USER sim
ENV PORT=8080
CMD ["sh", "-c", "exec /app/.venv/bin/arena-sim serve --host 0.0.0.0 --port ${PORT}"]
