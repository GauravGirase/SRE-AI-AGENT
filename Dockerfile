FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim
WORKDIR /app

# Environment variables
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1

# Install dependencies
COPY pyproject.toml pyproject.toml
RUN uv pip install -r pyproject.toml && \
    uv pip install aws-opentelemetry-distro==0.12.2

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

# AgentCore runtime port
EXPOSE 8000

# Copy application code
COPY . .

CMD ["opentelemetry-instrument", "python", "-m", "src.main"]