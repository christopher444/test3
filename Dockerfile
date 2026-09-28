FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 appuser

COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser scripts ./scripts
RUN mkdir -p /app/runtime && chown -R appuser:appuser /app/runtime

USER 10001
ENTRYPOINT ["python", "-m", "app.cli"]
