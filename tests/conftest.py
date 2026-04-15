"""Configuración compartida de pytest.

Agrega la raíz del repo al sys.path para que los tests puedan importar
`quant`, `jobs`, `dashboard`, etc. sin instalar el proyecto como paquete.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
