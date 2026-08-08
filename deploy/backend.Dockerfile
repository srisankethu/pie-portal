# The FastAPI backend, plus the pie-parser engine it imports in-process and the
# catalogue that engine searches.
#
# No `# syntax=` directive on purpose. Nothing here needs a newer Dockerfile
# frontend than the daemon's built-in one, and pinning one would make every
# build fetch an extra image first — which fails on exactly the restricted
# network a self-hosted deployment is most likely to be on.
#
# Build from the repository root, not from deploy/:
#   docker build -f deploy/backend.Dockerfile .

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies before source, so editing a router does not re-resolve the whole
# tree. psycopg ships a binary wheel, so no compiler is needed in this image.
COPY backend/requirements.txt backend/requirements.txt
RUN python -m pip install --no-cache-dir -r backend/requirements.txt

# app/config.py resolves REPO_ROOT as three parents up from itself, so the
# repository's shape has to survive into the image: with the app at
# /app/backend/app/config.py, REPO_ROOT is /app, and PIE_PARSER_ROOT and
# PIE_CATALOG land where the defaults expect them. Flattening this to
# /app/app would silently move both.
COPY backend/ backend/
COPY scripts/ scripts/
COPY deploy/release.sh deploy/release.sh

# pie-parser is a private submodule. When it has been checked out in the build
# context it is copied here and the catalogue below is decoded from its corpus;
# when it has not, this copies an empty directory and the build still succeeds.
# The `.` on the destination is what makes the empty case work — COPY of an
# empty directory has no files to specify, and Docker treats that as an error
# unless the destination already exists.
RUN mkdir -p /app/pie-parser /app/backend/data
COPY pie-parser/ /app/pie-parser/

# Decode the catalogue now rather than on the first request. AUTO_BUILD_CATALOG
# is off in production (docs/operations.md: "Set 0 in constrained deploys and
# build out of band") — this is the out-of-band build.
#
# Non-fatal on purpose. Without pie-parser the image still runs the whole
# Commercial Decision Platform; only Quote Builder resolution degrades, and it
# degrades the way it is designed to — every line shows PIE OFFLINE and the
# quote still works. A deploy that cannot reach a private submodule should lose
# one surface, not fail to start.
RUN python scripts/build_catalog.py \
 || echo "pie-parser corpus unavailable — Quote Builder lines will show PIE OFFLINE"

# Run unprivileged. Nothing here writes to the image at runtime: the database is
# Postgres and the catalogue was built above.
RUN useradd --system --create-home --uid 10001 portal \
 && chmod +x deploy/release.sh \
 && chown -R portal:portal /app
USER portal

# uvicorn is started from backend/, which is what puts `app` on the import path
# — the same working directory the Makefile and docs/operations.md use.
WORKDIR /app/backend

EXPOSE 8000

# Two workers by default. Each one warms its own copy of the pie-parser
# catalogue (~13 MB decoded), so this trades memory for concurrency: raise it on
# a larger box, or set PIE_WARM=0 to start lean and pay the warm on first use.
ENV UVICORN_WORKERS=2
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}"]
