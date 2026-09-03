"""`lab/langgraph/base.py` — LAS CONEXIONES. Un solo módulo las conoce.

DOS IDENTIDADES, Y EL NOMBRE DE CADA UNA ES UNA AFIRMACIÓN VERDADERA
===================================================================

    lector_lab    lee producción (tres tablas) y el diario del lab.
                  **La base NO lo deja escribir.** En ningún lado.
    escritor_lab  escribe SOLO en `lab.investigaciones`. Producción le sigue
                  siendo de solo lectura porque no tiene permiso, no porque el
                  código se porte bien.

Que sean dos y no una es la diferencia entre «confío en que no va a escribir en
producción» y «no puede». Con un solo rol con permiso de escritura habría que
leer los GRANT para saber qué puede tocar; con dos, el nombre lo dice.

⚠️ **NINGUNA CAE A `POSTGRES_URI`.** Esa es la del sistema y puede todo. Un
fallback silencioso convierte la garantía en una intención: el día que alguien
borre una variable, el lab entraría con permiso de escritura sobre producción y
**nadie se enteraría**, porque desde afuera se ve exactamente igual. Es la misma
ley que el ruteo fail-closed de `core/llm.py`.
"""
from __future__ import annotations

import os
import pathlib

from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).resolve().parents[2] / ".env")

LECTOR, ESCRITOR = "POSTGRES_URI_LECTOR", "POSTGRES_URI_ESCRITOR"


def _uri(var: str) -> str:
    uri = os.getenv(var, "").strip()
    if not uri:
        raise RuntimeError(
            f"falta {var} en el .env. El lab NO usa la conexión del sistema a "
            f"propósito: esa puede escribir en producción. Armala con "
            f"`python -m lab.langgraph.armar_lector`.")
    return uri


def _conectar(var: str):
    import psycopg
    return psycopg.connect(_uri(var), connect_timeout=10)


def leer(sql: str, params: tuple = ()) -> tuple[list, list] | str:
    """Devuelve (columnas, filas), o un TEXTO si algo falló.

    Devolver texto en vez de levantar es a propósito: un error que corta el
    grafo deja al ayudante mudo. Un texto que dice «no pude leer la base» lo
    deja seguir razonando y, sobre todo, **lo deja decírtelo**.

    Abre y cierra la conexión en cada consulta. Es más lento que un pool, y a
    cambio no queda una conexión del lab colgada contra producción entre
    pregunta y pregunta.
    """
    try:
        with _conectar(LECTOR) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [d[0] for d in cur.description], cur.fetchall()
    except Exception as e:
        return f"no pude leer la base ({type(e).__name__}: {e})"


def escribir(sql: str, params: tuple = ()):
    """Inserta en el diario del lab. **Acá SÍ se levanta si falla.**

    Es al revés que `leer` y a propósito: si una lectura falla, la
    investigación puede seguir con menos información. Si la escritura falla, lo
    que hay que saber es que **no quedó registrada** — tragarse ese error deja
    creer que se guardó algo que no está.
    """
    with _conectar(ESCRITOR) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone() if cur.description else None
