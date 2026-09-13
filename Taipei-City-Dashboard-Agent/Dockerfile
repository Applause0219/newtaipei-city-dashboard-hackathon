FROM python:3.12-slim

LABEL project="taipei-city-dashboard" \
      component="youth-agent" \
      hackathon="new-taipei-ai-hackathon-2025"

WORKDIR /app

# Install build deps, copy project, install, then clean up in one layer
COPY pyproject.toml ./
COPY src/ ./src/
COPY scripts/ ./scripts/

RUN pip install --no-cache-dir . \
    && rm -rf /root/.cache/pip

EXPOSE 8090

CMD ["uvicorn", "youth_agent.api:app", "--host", "0.0.0.0", "--port", "8090"]
