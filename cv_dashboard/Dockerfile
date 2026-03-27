# We use Python 3.11 for the dashboard as it is heavily tested with Reflex
FROM python:3.11-slim

WORKDIR /app

# Reflex requires curl and unzip to install its underlying JS engine
RUN apt-get update && apt-get install -y curl unzip && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rxconfig.py .
COPY . .

# Force Reflex to bind to all network interfaces so we can reach it from our browser
ENV HOST=0.0.0.0

# Expose the frontend (3000) and backend API (8000) ports
EXPOSE 3000 8000

CMD ["reflex", "run"]
