"""Configuración del servicio partner_api — lee su propio set de env vars.

No importa el `config.py` de la mesa a propósito: este proceso es aparte
y se mantiene mínimo. Las variables van en el `.env` del Droplet.

Env vars:
  PARTNER_MONGO_URI      Connection string Mongo del usuario READ-ONLY
                         scopeado a la base `ACAPortfolio`. Distinto del
                         MONGO_URI de la mesa (que tiene acceso full).
  PARTNER_JWT_SECRET     Secreto para firmar los JWT de los proveedores.
                         String largo y random. Sin esto el servicio no
                         arranca.
  PARTNER_TOKEN_TTL_MIN  Minutos de vida del token (default 60).
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

PARTNER_MONGO_URI: str = os.getenv("PARTNER_MONGO_URI", "").strip()
PARTNER_JWT_SECRET: str = os.getenv("PARTNER_JWT_SECRET", "").strip()
PARTNER_TOKEN_TTL_MIN: int = int(os.getenv("PARTNER_TOKEN_TTL_MIN", "60"))

DB_NAME = "ACAPortfolio"
