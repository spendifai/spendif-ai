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

# Drop the packaging toolchain of the SYSTEM interpreter. Nothing needs it at
# runtime: uv resolved and installed everything into /app/.venv during the build,
# the entrypoint runs through `uv run` and the healthcheck uses the venv Python.
# It is however the only remaining source of Trivy HIGH findings on this image:
# pip's vendored tree pins msgpack 1.1.2 (GHSA-6v7p-g79w-8964) and the
# setuptools 70.3.0 (CVE-2025-47273) it vendors as pkg_resources. Neither is
# reachable from the app, and neither can be upgraded away — the copies live
# inside pip itself and in the bundled ensurepip wheels, so pip goes.
#
# The path is spelled out here on purpose. The previous version of this step
# resolved it with `python -c "sysconfig.get_paths()['purelib']"`, but the
# `ENV PATH` above already puts /app/.venv/bin first: `python` was the VENV
# interpreter, purelib was the venv site-packages, and the system pip was never
# touched. The step exited 0 while doing nothing, and Trivy kept failing on an
# image everyone believed had been cleaned.
#
# The trailing `test` turns that failure mode from silent into fatal: if the
# layout ever changes again, the build stops here instead of shipping an image
# the scanner will reject.
RUN PY_SITE=/usr/local/lib/python3.13/site-packages && \
    rm -rf "$PY_SITE"/pip "$PY_SITE"/pip-* \
           "$PY_SITE"/setuptools "$PY_SITE"/setuptools-* \
           "$PY_SITE"/pkg_resources "$PY_SITE"/pkg_resources-* \
           "$PY_SITE"/wheel "$PY_SITE"/wheel-* \
           "$PY_SITE"/_distutils_hack "$PY_SITE"/distutils-precedence.pth \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13 && \
    find /usr/local/lib/python3.13 -name "*.whl" -delete && \
    test ! -e "$PY_SITE/pip" && \
    test ! -e "$PY_SITE/setuptools" && \
    echo "system packaging toolchain removed from $PY_SITE"

# Entrypoint — uv run attiva automaticamente la venv e risolve il symlink Python
CMD ["uv", "run", "streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
