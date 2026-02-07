FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends git ca-certificates nodejs npm \
  && npm install -g @anthropic-ai/claude-code \
  && rm -rf /var/lib/apt/lists/*

COPY agents/requirements.txt /app/agents/requirements.txt
RUN pip install --no-cache-dir -r /app/agents/requirements.txt

COPY shared /app/shared
COPY agents /app/agents

ENV PYTHONUNBUFFERED=1
ENV WORKTREES_ROOT=/worktrees
ENV BARE_REPO_ROOT=/repo

CMD ["python", "-m", "agents.entrypoint"]
