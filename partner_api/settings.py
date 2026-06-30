"""Configuración del servicio partner_api — lee su propio set de env vars.

No importa el `config.py` de la mesa a propósito: este proceso es aparte
y se mantiene mínimo. Las variables van en el `.env` del Droplet.

Env vars:
  POSTGRES_URI           Connection string Supabase/Postgres (la maneja
                         partner_api.pg). La fuente de datos es SQL `partner.*`
                         (decomiso Mongo: ACAPortfolio eliminada).
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

PARTNER_JWT_SECRET: str = os.getenv("PARTNER_JWT_SECRET", "").strip()
PARTNER_TOKEN_TTL_MIN: int = int(os.getenv("PARTNER_TOKEN_TTL_MIN", "60"))
