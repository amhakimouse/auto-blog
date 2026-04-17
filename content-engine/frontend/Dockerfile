# ─────────────────────────────────────────────────────────────────────────────
# Frontend Dockerfile — React + TypeScript + Vite (dev server)
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine

WORKDIR /app

# Install dependencies first (cached layer if package.json unchanged)
COPY package*.json ./
RUN npm ci --silent

# Copy source (overridden by volume mount in dev)
COPY . .

# Copy entrypoint script
COPY dev-entrypoint.sh /usr/local/bin/dev-entrypoint.sh
RUN chmod +x /usr/local/bin/dev-entrypoint.sh

EXPOSE 5173

# Vite must bind to 0.0.0.0 to be reachable from outside the container
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
