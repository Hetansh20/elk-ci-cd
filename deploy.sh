#!/bin/bash

set -euo pipefail

# ==========================
# Configuration
# ==========================
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="docker-compose.yml"

echo "=========================================="
echo "ELK Deployment Started : $(date)"
echo "Project Directory : $PROJECT_DIR"
echo "=========================================="

# Go to project directory
cd "$PROJECT_DIR"

# Verify compose file exists
if [ ! -f "$COMPOSE_FILE" ]; then
    echo "ERROR: $COMPOSE_FILE not found in $PROJECT_DIR"
    ls -la
    exit 1
fi

# Validate compose file
echo "Validating docker compose..."
docker compose -f "$COMPOSE_FILE" config > /dev/null

# Pull latest images
echo "Pulling latest images..."
docker compose -f "$COMPOSE_FILE" pull

## Remove previous containers for this Deployment

echo "[INFO] Cleaning previous containers from compose file..."
sudo -E docker compose -f "$COMPOSE_FILE" down --remove-orphans 

# Wait for containers to stop
echo "Waiting for containers to stop..."
sleep 60

# Deploy containers
echo "Deploying containers..."
sudo -E docker compose -f "$COMPOSE_FILE" up -d --remove-orphans

# Wait for startup
echo "Waiting for services..."
sleep 60

# Check status
echo "Checking container status..."

FAILED=$(docker ps -a \
    --filter "status=exited" \
    --format "{{.Names}}" \
    | grep -v "^elk_setup$" || true)

if [ -n "$FAILED" ]; then
    echo "Deployment Failed!"
    echo "Failed Containers:"
    echo "$FAILED"

    echo "Container Logs:"
    docker compose -f "$COMPOSE_FILE" logs

    exit 1
fi

echo "Running Containers:"
docker compose -f "$COMPOSE_FILE" ps

# Cleanup
echo "Cleaning old images..."
docker image prune -f

echo "=========================================="
echo "Deployment Successful : $(date)"
echo "=========================================="