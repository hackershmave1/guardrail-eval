# guardrail-eval in one container: the harness, the AgenticGoat labs, their listeners and the
# guardrail slot all talk over localhost inside it, so attack side effects stay in the container
# (REQ-ENV-03) and only model traffic leaves. Build with the submodule checked out:
#   git submodule update --init && docker compose build
# The model endpoint comes from .env, mounted at run time (REQ-ENV-06); it is never baked in.

FROM node:22-bookworm-slim AS node

FROM python:3.12-slim-bookworm

# git: run.json records the commit; curl/procps: run_evals.sh health checks and listener cleanup;
# jq: scripts/proof.jq.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl jq procps ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Non-root user. Match UID/GID to the host user so the bind-mounted results/ stays theirs.
ARG UID=1000
ARG GID=1000
RUN groupadd -g "$GID" eval && useradd -m -u "$UID" -g "$GID" -s /bin/bash eval

# promptfoo, pinned (promptfoo/VERSION): one version grades every run that is compared.
COPY promptfoo/VERSION /tmp/promptfoo-version
RUN npm install -g --no-fund --no-audit "promptfoo@$(cat /tmp/promptfoo-version)" \
 && npm cache clean --force

WORKDIR /app
RUN chown eval:eval /app
USER eval

# Python deps in /app/venv, where the scripts expect them. CPU-only torch keeps the image GBs
# smaller; sentence-transformers (Lab 04 embeddings) needs no GPU.
COPY --chown=eval:eval requirements.txt ./
RUN python -m venv venv \
 && venv/bin/pip install --no-cache-dir -q torch --index-url https://download.pytorch.org/whl/cpu \
 && venv/bin/pip install --no-cache-dir -q -r requirements.txt

# Lab 04's embedding model, fetched at build time so a run needs no Hugging Face access.
ENV HF_HOME=/home/eval/.cache/huggingface
RUN venv/bin/python -c "from sentence_transformers import SentenceTransformer as S; S('all-MiniLM-L6-v2')"
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

COPY --chown=eval:eval . .
RUN test -f agentic-goat/labs/01-mcp-tool-poisoning/package.json \
    || { echo "agentic-goat/ is empty: run 'git submodule update --init' before building" >&2; exit 1; } \
 && cd agentic-goat/labs/01-mcp-tool-poisoning && npm install --silent --no-fund --no-audit

ENV PATH=/app/venv/bin:$PATH PROMPTFOO_DISABLE_TELEMETRY=1
EXPOSE 8888
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["help"]
