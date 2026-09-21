FROM node:22-bookworm-slim AS node-runtime
FROM python:3.12-slim
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock requirements.txt ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml README.md ./
COPY podcast_bot ./podcast_bot
COPY dictionary ./dictionary
# CC-CEDICT (CC BY-SA 4.0) is converted offline from the pinned snapshot; the
# licence travels with the generated database. See dictionary/README.md.
RUN python -m podcast_bot.reader.cedict dictionary/cedict_ts.u8.gz \
        podcast_bot/reader/cedict.sqlite3 \
    && cp dictionary/LICENSE-CC-CEDICT.txt podcast_bot/reader/LICENSE-CC-CEDICT.txt \
    && rm -rf dictionary
RUN pip install --no-cache-dir --no-deps . \
    && useradd --uid 1000 --create-home bot \
    && mkdir -p /app/data && chown bot:bot /app/data
USER bot
HEALTHCHECK --interval=5s --timeout=3s --start-period=60s --retries=3 \
    CMD python -m podcast_bot.health
CMD ["python", "-m", "podcast_bot"]
