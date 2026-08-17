# The public edge: the built React app as static files, TLS, and the reverse
# proxy that puts the API on the same origin as the app.
#
# No `# syntax=` directive — see the note in backend.Dockerfile.
#
# Build from the repository root, not from deploy/:
#   docker build -f deploy/web.Dockerfile .

FROM node:22-slim AS build

WORKDIR /src

# npm ci, not npm install — it installs exactly what package-lock.json pins, so
# the image cannot quietly pick up a newer minor of a dependency than the one
# the gate ran against.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# `npm run build` is tsc -b, the form check, vite build, then the landing
# prerender (scripts/prerender.mjs bakes the public page and its crawl files
# into dist) — the same steps scripts/verify.sh runs, so a type error fails
# the image build rather than shipping.
#
# SITE_ORIGIN is the one build-time setting the prerender reads: the absolute
# origin written into the canonical tag, sitemap and robots.txt. The default
# is the hosted deployment's URL; a self-host behind its own domain should
# pass --build-arg SITE_ORIGIN=https://its.domain here.
ARG SITE_ORIGIN
RUN SITE_ORIGIN="$SITE_ORIGIN" npm run build


FROM caddy:2-alpine

# Caddy serves these; nothing at runtime writes to them.
COPY --from=build /src/dist /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile

EXPOSE 80 443
