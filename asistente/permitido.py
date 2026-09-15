"""Qué cuentas puede mirar el asistente: las de `ASISTENTE_CUENTAS` en el
`.env`, y ninguna más. Sin ninguna declarada no muestra nada (fail-closed).
`FILTRO_SQL` va en el WHERE de toda consulta que toque datos de cuentas."""
from __future__ import annotations

import os

from dotenv import load_dotenv

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_RAIZ, ".env"))

CLAVE_ENV = "ASISTENTE_CUENTAS"
FILTRO_SQL = "t.id_cuenta = ANY(%(cuentas_permitidas)s)"


class SinPermiso(Exception):
    """No hay ninguna cuenta habilitada. La consulta no debe salir."""


def cuentas() -> list[str]:
    """Los `id_cuenta` habilitados. Lista vacía si no hay ninguno."""
    return [c.strip() for c in os.getenv(CLAVE_ENV, "").split(",") if c.strip()]


def parametros() -> dict:
    """Los parámetros de `FILTRO_SQL`. Levanta `SinPermiso` si no hay cuentas."""
    permitidas = cuentas()
    if not permitidas:
        raise SinPermiso(
            f"no hay ninguna cuenta habilitada para el asistente. "
            f"Se declaran en el `.env` del Droplet: {CLAVE_ENV}=id1,id2,id3")
    return {"cuentas_permitidas": permitidas}


def como_error() -> dict:
    """El error que lee el modelo cuando no hay permiso."""
    return {
        "error": "el asistente no tiene ninguna cuenta habilitada para consultar",
        "que_hacer": (
            "Decile al usuario que hay que declarar las cuentas en el `.env` del "
            f"servidor, en {CLAVE_ENV}. NO estimes ni inventes ningún número."),
    }
