"""Constantes y helpers compartidos entre los sub-módulos de manager/."""
from __future__ import annotations

import os
from zoneinfo import ZoneInfo

_AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

# Usado por jobs/run para el cwd del subprocess
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
