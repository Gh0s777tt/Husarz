# ============================================================================
# husarz-sandbox.Dockerfile — obraz sandboxa narzędzi (husarz-sandbox:latest).
# ----------------------------------------------------------------------------
# Kontener uruchamiany przez DockerSandboxExecutor z twardą izolacją (patrz
# husarz.tools.sandbox.build_docker_argv): --network none, --cap-drop ALL,
# --read-only, non-root, limity CPU/RAM/PID. Obraz jest CELOWO minimalny —
# zawiera tylko interpreter i podstawowe narzędzia z allowlisty komend.
#
# Sieć jest odbierana na starcie kontenera (--network none), więc obraz NIE
# potrzebuje i NIE powinien zawierać niczego, co wymaga egressu w runtime.
# Budowa (jednorazowo, w środowisku z dostępem do rejestru bazowego):
#   docker build -f docker/husarz-sandbox.Dockerfile -t husarz-sandbox:latest .
# ============================================================================

FROM python:3.13-slim

# Narzędzia zgodne z typowymi command_allowlist (python, pytest, git).
# git bywa potrzebny narzędziu 'git'/'run_tests'; instalujemy minimalnie.
RUN apt-get update \
    && apt-get install --no-install-recommends -y git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest

# Non-root, UID/GID zgodne z security.sandbox.run_as_user ("1000:1000").
RUN groupadd --gid 1000 sandbox \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin sandbox

# Workspace montowany w runtime (rw), rootfs pozostaje read-only (--read-only).
WORKDIR /workspace
USER sandbox

# Brak ENTRYPOINT z akcją domyślną — komenda jest zawsze przekazywana przez
# executora z allowlisty. Powłoka logowania nie jest wymagana.
CMD ["python", "--version"]
