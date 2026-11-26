FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy logger library and examples
COPY elk_logger.py .
COPY examples/ examples/

# Create log output directory
RUN mkdir -p /app/logs

ENV PYTHONPATH=/app


# Default command (overridden per service in docker-compose.yml)
CMD ["python", "examples/product_a_auth.py"]
