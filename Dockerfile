FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml README.md ./
COPY podcast_bot ./podcast_bot
RUN pip install --no-cache-dir --no-deps . \
    && useradd --uid 1000 --create-home bot \
    && mkdir -p /app/data && chown bot:bot /app/data
USER bot
CMD ["python", "-m", "podcast_bot"]
