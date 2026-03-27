# Use the official Python 3.14 slim image
FROM python:3.14-slim

# Prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1

# Set the working directory inside the container
WORKDIR /app

# 1. Copy ONLY requirements first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 2. Copy ALL remaining files from the video_processor folder into /app
COPY . .

# Run the orchestrator
CMD ["python", "main.py"]