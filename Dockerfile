FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and config
COPY src/ /app/src/
COPY openenv.yaml /app/

# Set the entrypoint to run the baseline inference
CMD ["python", "src/inference.py"]
