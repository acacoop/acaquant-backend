"""asistente/permitido.py — QUÉ CUENTAS PUEDE MIRAR EL ASISTENTE.

── SU ROL EN EL CICLO: es la puerta, y está cerrada por default ──

El asistente NO ve la base entera. Ve las cuentas que estén declaradas en el
`.env` del Droplet, y ninguna más.

    ASISTENTE_CUENTAS=12345,67890,11111

⚠️ **SI NO HAY NINGUNA DECLARADA, NO MUESTRA NADA.** No muestra todo: no
muestra nada, y lo dice. Es la diferencia entre un permiso y un filtro. Un
filtro que se olvida deja pasar todo; un permiso que falta corta.

## Por qué en el `.env` y no en el código

Tres razones, y la tercera es la que importa:

  · el `.env` no está en git, así que los números de cuenta no quedan en la
    historia del repo para siempre;
  · cambiarlo no es un deploy — se edita y listo;
  · y sobre todo: **para cambiarlo hay que entrar al Droplet**, que es el mismo
    nivel de acceso que hace falta para correr el asistente. No hay forma de
    ampliar el permiso desde afuera.

## Por qué levanta una excepción en vez de devolver una lista vacía

Porque una lista vacía se puede ignorar sin querer. Si una herramienta futura
se olvida de filtrar, con una lista vacía la consulta sale igual y devuelve
TODO — un error que no falla. Con la excepción, esa herramienta se rompe fuerte
la primera vez que alguien la corre, que es cuando conviene enterarse.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_RAIZ, ".env"))

CLAVE_ENV = "ASISTENTE_CUENTAS"

# El fragmento de SQL que va en el WHERE de TODA consulta que toque datos de
# cuentas. Está acá y no escrito a mano en cada herramienta para que haya un
# solo lugar donde mirar qué filtra — y un test lo exige.
FILTRO_SQL = "t.id_cuenta = ANY(%(cuentas_permitidas)s)"


class SinPermiso(Exception):
    """No hay ninguna cuenta habilitada. La consulta no debe salir."""


def cuentas() -> list[str]:
    """Los `id_cuenta` habilitados. Lista vacía si no hay ninguno."""
    return [c.strip() for c in os.getenv(CLAVE_ENV, "").split(",") if c.strip()]


def parametros() -> dict:
    """Los parámetros que necesita `FILTRO_SQL`. Levanta `SinPermiso` si no hay
    ninguna cuenta habilitada — la consulta NO tiene que salir."""
    permitidas = cuentas()
    if not permitidas:
        raise SinPermiso(
            f"no hay ninguna cuenta habilitada para el asistente. "
            f"Se declaran en el `.env` del Droplet: {CLAVE_ENV}=id1,id2,id3")
    return {"cuentas_permitidas": permitidas}


def como_error() -> dict:
    """El mensaje que se le devuelve al modelo cuando no hay permiso. Va como
    dato y no como excepción: el modelo tiene que poder decir «no tengo acceso»
    en vez de quedarse callado o inventar."""
    return {
        "error": "el asistente no tiene ninguna cuenta habilitada para consultar",
        "que_hacer": (
            "Decile al usuario que hay que declarar las cuentas en el `.env` del "
            f"servidor, en {CLAVE_ENV}. NO estimes ni inventes ningún número."),
    }
