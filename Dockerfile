# Shared image for the 3 reviewers + api — only CMD differs per deployment
# (see docker-compose.yml / the Helm chart). Dashboard has its own image,
# see Dockerfile.dashboard.

FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ agents/
COPY monitoring/ monitoring/
COPY pipeline.py serve_one.py run_servers.py main.py api.py webhook.py ./

RUN mkdir -p logs

# No default CMD — each deployment sets its own command explicitly.
