FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir '.[api,database,robot,tracing,llm]'

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn robo_claw_maestro.main:app --host 0.0.0.0 --port 8000"]
