# ==========================================
# STAGE 1: Builder
# ==========================================
FROM python:3.11-slim AS builder

# Fail promptly when the pinned CPU wheel is unavailable; never silently compile.
RUN pip download --no-cache-dir --only-binary=:all: --no-deps --timeout 20 --retries 0 \
    --dest /wheels --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
    llama-cpp-python==0.3.10

# Create and activate virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy python dependencies and model download script
COPY requirements.txt .
COPY download_model.py .

# Install dependencies
# Install dependencies using pre-compiled universal CPU wheels to prevent hardware mismatch
RUN pip install --no-cache-dir --only-binary=llama-cpp-python --find-links=/wheels \
    --report=/opt/venv/install-report.json -r requirements.txt
RUN python -m spacy download en_core_web_md || python -m spacy download en_core_web_sm

# Pre-download the LLM weights into the image to achieve 0ms runtime network latency
RUN python download_model.py


# ==========================================
# STAGE 2: Runner
# ==========================================
FROM python:3.11-slim AS runner

# Set strict environment variables
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Copy the compiled virtual environment (Leaves GCC/build-essential behind)
COPY --from=builder /opt/venv /opt/venv

# Copy the pre-downloaded GGUF model
COPY --from=builder /model /model

# Create the required AMD Hackathon I/O directories
RUN mkdir -p /input /output

# Install libgomp1 required for llama-cpp-python universal CPU wheels
RUN apt-get update && apt-get install -y libgomp1 && rm -rf /var/lib/apt/lists/*

# Set the working directory and copy the RelayOS AI OS source code
WORKDIR /app
COPY . /app

# Execute the orchestrator
CMD ["python", "main.py"]
