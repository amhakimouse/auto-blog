#!/bin/sh
set -e

# Sync dependencies if package.json has changed
# This uses the anonymous volume for node_modules
echo "Checking/Installing frontend dependencies..."
npm install --no-audit --no-fund --silent

# Execute the main command
exec "$@"
