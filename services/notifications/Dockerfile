# syntax=docker/dockerfile:1
FROM node:26-slim
ENV NODE_ENV=production
WORKDIR /app
# Install only runtime libraries (no TypeScript, no Vitest), exactly as in package-lock.json.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY src ./src
# 1000 = the "node" user that comes with this image
USER 1000
EXPOSE 8002
CMD ["node", "src/main.ts"]
