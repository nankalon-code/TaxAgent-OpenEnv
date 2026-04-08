FROM python:3.10-slim

# Set the working directory
WORKDIR /app

# FIX 1: This tells Python where to look for the 'src' folder
ENV PYTHONPATH="/app"

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your code into the container
COPY src/ /app/src/
COPY openenv.yaml /app/

# Open the port for the web UI
EXPOSE 7860

# FIX 2: Run the interactive web interface, NOT the terminal script
CMD ["python", "src/frontend.py"]
