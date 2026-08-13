"""API rdzenia — REST (FastAPI) + serwowana konsola WWW (Etap 5).

Publiczne API:
    create_app — buduje aplikację FastAPI z konfiguracji (router/audyt wstrzykiwalne).
"""

from __future__ import annotations

from husarz.api.app import create_app

__all__ = ["create_app"]
