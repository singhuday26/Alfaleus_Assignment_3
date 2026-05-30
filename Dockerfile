# ==============================================================================
# Builder Stage: Compile and build dependencies
# ==============================================================================
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system dependencies required for compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt-dev \
    zlib1g-dev \
    libjpeg-dev \
    libssl-dev \
    libffi-dev \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment to isolate python dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ==============================================================================
# Runner Stage: Lean execution environment
# ==============================================================================
FROM python:3.11-slim AS runner

WORKDIR /app

# Install lightweight runtime libraries and curl for service health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libxml2 \
    libxslt1.1 \
    zlib1g \
    libjpeg62-turbo \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy project source files
COPY . .

# Expose FastAPI and Streamlit standard ports
EXPOSE 8000
EXPOSE 8501

# Default runtime command (can be overridden in docker-compose.yml)
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
