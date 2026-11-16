# yyc-mcp's image (Deployment yyc-mcp and CronJob mcp-cleanup, deploy/apps/yyc-mcp.yaml), built by
# .github/workflows/mcp-image.yml with context news-stream/: the server bundles the site's data modules
# (web-app/src/server, web-app/src/lib) into dist/, so the image carries two files and Node, no node_modules.
FROM node:22.23.4-bookworm-slim AS build
WORKDIR /src/mcp-edge
COPY mcp-edge/package.json mcp-edge/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY mcp-edge/ ./
COPY web-app/src/server ../web-app/src/server
COPY web-app/src/lib ../web-app/src/lib
RUN npx tsc --noEmit && node build.mjs && npm test

FROM node:22.23.4-bookworm-slim
WORKDIR /app
COPY --from=build /src/mcp-edge/dist ./dist
ARG GIT_SHA=unknown
ENV NODE_ENV=production PORT=8080 GIT_SHA=$GIT_SHA
USER node
EXPOSE 8080
CMD ["node", "--enable-source-maps", "dist/server.js"]
