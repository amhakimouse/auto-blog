#!/bin/bash
set -e

# Sync dependencies if requirements.txt has changed or on first run
# This is fast if everything is already installed
echo "Checking/Installing dependencies..."
pip install --no-cache-dir -r requirements.txt

# Execute the main command (from docker-compose or Dockerfile)
exec "$@"
