FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --create-home --uid 10001 agent
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "gunicorn>=22,<24"
COPY agent_app ./agent_app
COPY static ./static
COPY run.py .
USER agent
ENV AGENT_DATA_DIR=/tmp/agent-data AGENT_ENABLE_RUN_PYTHON=0 AGENT_EPHEMERAL_STORAGE=1
# Render (and most hosts) put a reverse proxy in front of the app. Without this every visitor would look like the same
# client address, so the login lockout and rate limits would punish everyone, including you, for one attacker's guesses.
ENV AGENT_TRUST_PROXY=1
# One worker only: tasks run in background threads inside this process.
CMD ["sh", "-c", "exec gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:${PORT:-10000} agent_app.wsgi:app"]
