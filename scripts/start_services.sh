#!/bin/bash
# Start infrastructure services for TradeTool

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "Starting TradeTool infrastructure services..."
echo "============================================="

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker first."
    exit 1
fi

# Start services
docker compose up -d

echo ""
echo "Waiting for services to be healthy..."

# Wait for QuestDB
echo -n "QuestDB: "
for i in {1..30}; do
    if curl -s http://localhost:9000/health > /dev/null 2>&1; then
        echo "ready"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "timeout"
        exit 1
    fi
    sleep 1
done

# Wait for Redis
echo -n "Redis: "
for i in {1..30}; do
    if docker exec tradetool-redis redis-cli ping > /dev/null 2>&1; then
        echo "ready"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "timeout"
        exit 1
    fi
    sleep 1
done

echo ""
echo "============================================="
echo "Services are ready!"
echo ""
echo "QuestDB Web Console: http://localhost:9000"
echo "QuestDB ILP:         localhost:9009"
echo "Redis:               localhost:6379"
echo ""
echo "To view logs:  docker compose logs -f"
echo "To stop:       docker compose down"
echo "============================================="
