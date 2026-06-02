FROM python:3.10-slim

# Set the working directory
WORKDIR /app

# Fix Python path so it can find the 'src' folder
ENV PYTHONPATH="/app"

# 1. Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. Copy the logic (the 'src' folder)
COPY src/ /app/src/

# 3. IMPORTANT: Copy these files to the ROOT of the container
COPY openenv.yaml /app/
COPY inference.py /app/

# 4. Expose BOTH ports (7860 for your UI, 8000 for the validator)
EXPOSE 7860
EXPOSE 8000

# 5. Run the inference script which contains the FastAPI /reset route
# This script will now start the server and be ready for the validator
CMD ["python", "inference.py"]
