# syntax=docker/dockerfile:1
#
# One-command promptry dashboard. The built dashboard UI ships inside the
# package (promptry/dashboard/static), so this image needs no Node build step.
#
#   docker build -t promptry .
#   docker run -p 8420:8420 -v promptry-data:/data promptry
#   # open http://localhost:8420
#
FROM python:3.12-slim

# Extras baked into the image. "llm" (default) enables live model calls, the
# LLM judge (G-Eval / RAG metrics / assert_llm), and the playground. Set to ""
# for a lean core image (registry + evals + cost + capture still work), or
# "full" for everything (adds the semantic/embedding stack).
ARG EXTRAS=llm

WORKDIR /app
COPY pyproject.toml README.md ./
COPY promptry ./promptry

RUN if [ -n "$EXTRAS" ]; then \
        pip install --no-cache-dir ".[$EXTRAS]"; \
    else \
        pip install --no-cache-dir .; \
    fi

# All state (SQLite DB, cached prices, session secret) lives on a volume so it
# survives restarts. Simple, single-file, no external database.
ENV PROMPTRY_DB=/data/promptry.db
VOLUME ["/data"]

EXPOSE 8420

# Serve the ASGI app directly (bind 0.0.0.0 so the mapped port is reachable).
CMD ["uvicorn", "promptry.dashboard.server:app", "--host", "0.0.0.0", "--port", "8420"]
