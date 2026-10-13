FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered real-time logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies including dumb-init to reap zombie Chromium processes
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    dumb-init \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser and its system dependencies for Playwright
RUN playwright install --with-deps chromium

# Copy the rest of the application code
COPY . .

# Ensure working directories exist
RUN mkdir -p uploads outputs wa_session

# Port configuration (Railway automatically provides PORT environment variable)
ENV PORT=5000
EXPOSE 5000

# Use dumb-init as init process (PID 1) to forward signals and reap zombie Chromium renderer processes
ENTRYPOINT ["dumb-init", "--"]

# Launch application
CMD ["python", "app.py"]
