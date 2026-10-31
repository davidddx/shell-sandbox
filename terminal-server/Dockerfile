
# ---- Build stage: node-pty is a native module and needs a compiler ----
FROM node:20-bookworm-slim AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 make g++ \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
 
# ---- Runtime stage ----
FROM node:20-bookworm-slim
# Static Docker CLI only (the daemon is the host's, via the mounted socket)
COPY --from=docker:cli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /app
COPY --from=build /app/node_modules ./node_modules
COPY package*.json server.js ./
COPY public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
 
