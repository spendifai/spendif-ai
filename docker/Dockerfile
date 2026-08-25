# ── Spendify — Dockerfile ────────────────────────────────────────────────────
# Single-stage build con uv: evita problemi di symlink nei multi-stage copy.
# Build:  docker build -t spendify .
# Run:    docker run -p 8501:8501 --env-file .env spendify

# Pinned to 3.13: llama-cpp-python wheels are not yet available for Python 3.14
# (source build fails at link stage for llama-llava-cli / llama-gemma3-cli).
# Unpin when llama-cpp-python publishes 3.14 wheels or fixes the CMake build.
FROM python:3.13-slim

# Pull the latest Debian security patches before installing anything else.
# The base `python:3.13-slim` image is only rebuilt every few weeks, so its
# system packages routinely lag behind upstream CVE fixes (Trivy was flagging
# HIGH severity findings on libcap2 / libsystemd0 / libldap-common despite
# Debian having published the patches already). Running `apt-get upgrade`
# at build time picks them up without waiting for the python:slim image to
# refresh.
RUN apt-get update -qq && \
    apt-get -y -qq upgrade && \
    apt-get install -y -qq --no-install-recommends gcc g++ cmake && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# The system Python in `python:3.13-slim` ships setuptools 70.3.0, which carries
# CVE-2025-47273 (path traversal in PackageIndex). It is not part of our lockfile
# — uv installs its own copy inside /app/.venv — but Trivy scans the whole image
# and gates the build on it. Upgrade the interpreter-level copy in place.
RUN python -m pip install --no-cache-dir --upgrade "setuptools>=78.1.1"

# Installa uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Non scaricare Python separato: usa quello di sistema già presente nell'image
ENV UV_PYTHON_DOWNLOADS=0
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Copia solo i file di dipendenze per sfruttare la cache Docker
COPY pyproject.toml uv.lock ./

# Installa dipendenze nella venv (senza dev/test).
# `--no-cache`: without it uv leaves its download/build cache in /root/.cache/uv,
# which bloats the image and carries package copies that never end up in the
# venv (build-time deps of source-built wheels such as llama-cpp-python). Trivy
# scans those copies too and was flagging msgpack 1.1.2 (GHSA-6v7p-g79w-8964)
# from the cache even though msgpack is absent from uv.lock.
RUN uv sync --frozen --no-dev --no-install-project --no-cache && \
    rm -rf /root/.cache/uv

# Copia il codice sorgente
COPY . .

# Aggiungi la venv al PATH
ENV PATH="/app/.venv/bin:$PATH"

# Directory dove il DB e i log verranno montati come volume
VOLUME ["/app/data", "/app/logs"]

# Porta Streamlit
EXPOSE 8501

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

# Entrypoint — uv run attiva automaticamente la venv e risolve il symlink Python
CMD ["uv", "run", "streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
