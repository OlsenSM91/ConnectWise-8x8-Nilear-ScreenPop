FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY server.py .
COPY database.py .
COPY connectwise_api.py .
COPY .env .

# Copy logo files
COPY logo-1024px-darkbg-logo.png .
COPY logo-1024px-lightbg-logo.png .

# Create data directory for SQLite database
RUN mkdir -p /app/data

# Expose port
EXPOSE 1337

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:1337/health')"

# Start the server
CMD ["python", "server.py"]
