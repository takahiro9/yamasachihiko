# ローカルで全体（閲覧画面・MCP・パイプラインの CLI）を動かすイメージ。Python（uv）と Node（wrangler）を 1 つに入れる。
# 重い依存（埋め込みの torch など）はビルド引数 UV_EXTRAS で足す（compose の worker だけが入れる）。
FROM node:24-bookworm-slim AS node

FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /usr/local/bin/uv
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH \
    WRANGLER_SEND_METRICS=false CI=true
WORKDIR /app

# Python の依存（コードより先に入れてキャッシュを効かせる）
ARG UV_EXTRAS="--extra extract --extra mcp"
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project ${UV_EXTRAS}

# Worker の依存（linux 用の workerd を入れるので、ホストの node_modules は持ち込まない）
COPY cloud/worker/package.json cloud/worker/package-lock.json ./cloud/worker/
RUN cd cloud/worker && npm ci --no-audit --no-fund

COPY src ./src
COPY migrations ./migrations
COPY contracts ./contracts
COPY cloud ./cloud
COPY .claude/skills ./.claude/skills
RUN uv sync --frozen --no-dev ${UV_EXTRAS}
