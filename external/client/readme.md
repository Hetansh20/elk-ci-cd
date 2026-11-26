

# external app logs integration



docker build -t product-a-api-dev_external -f ./client/Dockerfile ./client
docker run -d \
  --name product_a_api_dev_external \
  --restart unless-stopped \
  -e PRODUCT=product-external \
  -e SERVICE=api-service \
  -e ENVIRONMENT=dev \
  -e LOG_LEVEL=DEBUG \
  -e LOGSTASH_HOST=172.20.0.3 \
  -e LOGSTASH_PORT=5000 \
  -v $(pwd)/logs/dev/product-a/api-service:/app/logs \
  product-a-api-dev_external \
  python examples/product_a_api.py