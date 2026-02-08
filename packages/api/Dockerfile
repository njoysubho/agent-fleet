FROM node:22-slim

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Copy workspace root for npm workspaces resolution
COPY package.json package-lock.json ./
COPY packages/shared/package.json packages/shared/
COPY packages/api/package.json packages/api/

RUN npm ci --workspace=@agent-fleet/api --workspace=@agent-fleet/shared

COPY packages/shared/ packages/shared/
COPY packages/api/ packages/api/

RUN npm run build -w @agent-fleet/shared \
 && npm run build -w @agent-fleet/api

ENV NODE_ENV=production
EXPOSE 8000

CMD ["node", "packages/api/dist/main.js"]
