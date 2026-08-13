# ============================================================================
# Dockerfile — obraz rdzenia Husarza (REST API + launcher `husarz up`).
# ----------------------------------------------------------------------------
# Wieloetapowy build: (1) budujemy paczkę do venv, (2) chudy obraz runtime.
# Zasady: non-root, brak kompilatorów w obrazie runtime, brak sekretów/wag/modeli
# (patrz .dockerignore). Egress kontrolowany na warstwie sieci (compose/k8s).
# ============================================================================

# --- Etap 1: build -----------------------------------------------------------
FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Najpierw metadane paczki — lepsze cache warstw, gdy zmienia się tylko kod.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# Instalacja do izolowanego venv (przeniesiony do obrazu runtime).
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

# --- Etap 2: runtime ---------------------------------------------------------
FROM python:3.13-slim AS runtime

# Non-root: stały UID/GID zgodny z sandbox.run_as_user ("1000:1000").
RUN groupadd --gid 1000 husarz \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin husarz

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HUSARZ_CONFIG_DIR=/app/config

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
# Domyślna konfiguracja i prompty (nadpisywalne montażem woluminu w prod/airgap).
COPY config ./config
COPY prompts ./prompts

# Katalogi runtime (audyt, dane, workspace) należą do użytkownika non-root.
RUN mkdir -p /app/audit /app/data /app/artifacts /app/workspace \
    && chown -R husarz:husarz /app

USER husarz

EXPOSE 8000

# Sonda liveness — endpoint /api/health jest celowo bez uwierzytelniania.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

# Domyślnie profil dev na loopbacku kontenera. Compose/k8s nadpisują host/profil.
# Uwaga: nasłuch na 0.0.0.0 wymaga tokenu API (security.auth.api_token_ref) albo
# jawnego --allow-insecure — patrz deploy/.
ENTRYPOINT ["husarz"]
CMD ["up", "--host", "127.0.0.1", "--port", "8000"]
