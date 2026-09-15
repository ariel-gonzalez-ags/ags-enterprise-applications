# syntax=docker/dockerfile:1

# ---- Stage 1: build the static site ----
# Pinned digest keeps builds reproducible (node 22-alpine, resolved 2026-09).
FROM node:26-alpine@sha256:ef24c5053d50fdc3e4e56eb4e7ddb7861874ab0fdc797046ba897581deb8e868 AS build
WORKDIR /app

# Copy only dependency manifests first for better layer caching.
# package-lock.json exists → reproducible installs via npm ci.
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-fund --no-audit

COPY web/ ./
RUN npm run build

# Smoke-test the built output inside the image build: broken output = failed build.
RUN node tests/checks.mjs

# ---- Stage 2: serve with nginx (unprivileged, port 8080) ----
FROM nginxinc/nginx-unprivileged:1.31-alpine@sha256:19c132c9ab02d3b783f478743dafc7a7f42e27aa7d2bdcbec1bb1128ca8f2a07
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8081/healthz || exit 1

USER nginx
CMD ["nginx", "-g", "daemon off;"]
