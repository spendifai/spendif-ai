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
# and the entrypoint runs through `uv run`. It is however the only remaining
# source of Trivy HIGH findings on this image — setuptools 70.3.0
# (CVE-2025-47273) and the msgpack 1.1.2 that pip carries in its vendored tree
# (GHSA-6v7p-g79w-8964) — neither of which is reachable from the app. Upgrading
# site-packages was not enough: the reported copies live in pip's vendored tree
# and in the bundled ensurepip wheels, so they are removed outright.
RUN python -c "import glob, os, shutil, sysconfig; \
p = sysconfig.get_paths()['purelib']; \
targets = [x for pat in ('pip', 'pip-*', 'setuptools', 'setuptools-*', 'pkg_resources', 'pkg_resources-*', 'wheel', 'wheel-*', '_distutils_hack', 'distutils-precedence.pth') for x in glob.glob(os.path.join(p, pat))]; \
[shutil.rmtree(t, ignore_errors=True) if os.path.isdir(t) else os.remove(t) for t in targets]" && \
    rm -rf /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13 && \
    find /usr/local/lib/python3.13 -name "*.whl" -delete

# Entrypoint — uv run attiva automaticamente la venv e risolve il symlink Python
CMD ["uv", "run", "streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
