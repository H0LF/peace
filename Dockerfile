FROM node:24-slim AS dashboard
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

FROM python:3.13-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
WORKDIR /app
# uid 1000 is the usual first Linux user, who owns the Compose secret files (mode 600).
RUN useradd --uid 1000 --create-home peace && mkdir /data && chown peace /data  # a new volume inherits this owner

# API, dashboard and worker.
FROM base AS app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
COPY --from=dashboard /dashboard/dist /app/dashboard/dist
RUN uv sync --frozen --no-dev
# The 02:00 schedule and Lisbon timetable need tz data in the image: fail the build if missing.
RUN python -c "from zoneinfo import ZoneInfo; ZoneInfo('Europe/Lisbon')"
USER peace
EXPOSE 8420
CMD ["sh", "-c", "alembic upgrade head && exec peace-api"]

# Speech (Whisper + Supertonic on the CPU); models download into the hf volume on first use.
FROM base AS voice
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra voice --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra voice
RUN mkdir -p /home/peace/.cache/huggingface && chown -R peace /home/peace/.cache  # the models volume inherits this owner
USER peace
ENV PEACE_VOICE_HOST=0.0.0.0
EXPOSE 3900
CMD ["peace-voice"]
