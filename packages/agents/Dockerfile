FROM node:22-slim

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends git ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Copy workspace root for npm workspaces resolution
COPY package.json package-lock.json ./
COPY packages/shared/package.json packages/shared/
COPY packages/agents/package.json packages/agents/

RUN npm ci --workspace=@agent-fleet/agents --workspace=@agent-fleet/shared

COPY packages/shared/ packages/shared/
COPY packages/agents/ packages/agents/

RUN npm run build -w @agent-fleet/shared \
 && npm run build -w @agent-fleet/agents

ENV NODE_ENV=production
ENV WORKTREES_ROOT=/worktrees
ENV BARE_REPO_ROOT=/repo

CMD ["node", "packages/agents/dist/entrypoint.js"]
