# The review page (M7). Start it with:
#
#     docker compose up -d app
#     open http://127.0.0.1:8000/
#
# The code only: data/, .env and profile.toml are mounted by compose.yaml and
# kept out of the image by .dockerignore.
FROM python:3.13-slim

# Pinned for the same reason the SearXNG image is: a tool changing under us
# should show up in a diff.
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY prompts ./prompts
RUN uv sync --frozen --no-dev

# Inside the container the page binds every interface; compose publishes it
# on the host's loopback only, so it is still not reachable from outside.
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "company-reach", "review", "--host", "0.0.0.0"]
