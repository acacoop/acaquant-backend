"""`lab/langgraph/datos.py` — LAS LLAVES A LA BASE. Solo lectura.

Hasta acá el ayudante leía ARCHIVOS. Esto es lo primero que toca Postgres, así
que la seguridad se diseña ahora y no después.

TRES GUARDAS, Y LA PRIMERA ES LA QUE IMPORTA
============================================

  1. **NO existe una herramienta "corré este SQL".** El modelo pasa un ticker,
     no una consulta. Las consultas están escritas acá, a mano, y son las
     únicas que puede ejecutar. Una tool de SQL libre no es una herramienta:
     es una consola remota — la misma razón por la que `agente/rehacer.py`
     tiene una lista corta de jobs y no un `subprocess` abierto.
  2. **Todo es SELECT, y los valores van como parámetros** (`%s`), nunca
     pegados al texto de la consulta.
  3. **Todo tiene techo.** Nada devuelve más de un puñado de filas: lo que
     vuelve entra al contexto del modelo y se paga por token.

⚠️ **LOS NOMBRES ESTÁN CRUZADOS Y NO ES UN ERROR DE TIPEO.** En `mercado.curvas`
la PK es `ticker` (`AL30`) e `instrumento` es el SÍMBOLO DE MERCADO
(`MERV - XMEV - AL30 - 24hs`). Y en `mercado.market_snapshot` la columna se
llama `ticker` pero guarda **el símbolo**. O sea:

    curvas.instrumento  ==  market_snapshot.ticker     ← el símbolo
    curvas.ticker                                       ← el nombre corto

Por eso el ayudante necesita DOS pasos para saber si un bono tiene precio:
primero la ficha (para sacar el símbolo), después el precio (con ese símbolo).
Eso no es una molestia del ejercicio: **es el encadenado que hace falta de
verdad**, y es exactamente donde un humano se equivoca.
"""
from __future__ import annotations

import os
import pathlib

from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv(pathlib.Path(__file__).resolve().parents[2] / ".env")


def _uri() -> str:
    """La conexión del LECTOR, y **sin plan B**.

    ⚠️ Si falta `POSTGRES_URI_LECTOR`, esto FALLA. No cae a `POSTGRES_URI` —
    que es el superusuario— por más tentador que sea: un fallback silencioso
    convierte la garantía en una intención. El día que alguien borre la
    variable, el lab volvería a entrar con permiso de escritura y **nadie se
    enteraría**, porque desde afuera se ve exactamente igual.

    Es la misma ley que el ruteo fail-closed de `core/llm.py`: si el proveedor
    que corresponde no está, la llamada no se hace — no se cae a otro.
    """
    uri = os.getenv("POSTGRES_URI_LECTOR", "").strip()
    if not uri:
        raise RuntimeError(
            "falta POSTGRES_URI_LECTOR en el .env. El lab NO usa la conexión "
            "de siempre a propósito: esa tiene permiso de escritura. "
            "Armala con `python -m lab.langgraph.armar_lector`.")
    return uri


def _consultar(sql: str, params: tuple) -> tuple[list, list] | str:
    """Ejecuta y devuelve (columnas, filas), o un TEXTO si algo falló.

    Devolver texto en vez de levantar es a propósito: un error que corta el
    grafo deja al ayudante mudo. Un texto que dice «no pude leer la base» lo
    deja seguir razonando, y sobre todo **lo deja decírtelo**.

    Abre y cierra la conexión en cada consulta. Es más lento que un pool, y
    está bien: son unas pocas consultas por pregunta, y a cambio no hay una
    conexión del lab colgada contra producción entre pregunta y pregunta.
    """
    try:
        import psycopg
        with psycopg.connect(_uri(), connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return [d[0] for d in cur.description], cur.fetchall()
    except Exception as e:
        return f"no pude leer la base ({type(e).__name__}: {e})"


def _tabla(r, vacio: str) -> str:
    if isinstance(r, str):
        return r
    cols, filas = r
    if not filas:
        return vacio
    return "\n".join(" · ".join(f"{c}={v}" for c, v in zip(cols, f) if v is not None)
                     for f in filas)


@tool
def ficha_del_bono(ticker: str) -> str:
    """Devuelve la ficha de un bono del master (`mercado.curvas`): su símbolo de
    mercado, la curva, la moneda, el vencimiento y el emisor. El `ticker` es el
    nombre corto, como 'AL30' o 'TX26'. Usar SIEMPRE PRIMERO cuando la pregunta
    sea sobre un bono concreto: de acá sale el símbolo que necesitan las otras
    herramientas."""
    r = _consultar(
        "SELECT ticker, instrumento AS simbolo, curva, tipo, moneda_eje, ajuste, "
        "       fecha_vencimiento, emisor, emisor_tipo "
        "  FROM mercado.curvas WHERE upper(ticker) = upper(%s)",
        (ticker.strip(),))
    return _tabla(r, f"«{ticker}» no está en el master (`mercado.curvas`). "
                     f"Puede ser que no exista, o que esté cargado con otro nombre.")


@tool
def precio_del_simbolo(simbolo: str) -> str:
    """Devuelve el último precio que el motor tiene de un símbolo de mercado, y
    CUÁNDO lo actualizó. El `simbolo` es el largo, tipo
    'MERV - XMEV - AL30 - 24hs' — sale de `ficha_del_bono`.

    ⚠️ Que no haya fila NO prueba que el bono no cotice: esta tabla sólo guarda
    lo que el motor pidió. Si no aparece, lo más probable es que nadie lo esté
    escuchando."""
    r = _consultar(
        "SELECT ticker AS simbolo, last_price, tea, paridad, updated_at, "
        "       round(extract(epoch FROM now() - updated_at)/60) AS hace_minutos "
        "  FROM mercado.market_snapshot WHERE ticker = %s",
        (simbolo.strip(),))
    return _tabla(r, f"nadie está escuchando «{simbolo}»: no hay fila en "
                     f"`market_snapshot`. El motor sólo escribe lo que suscribe.")


@tool
def hallazgos_del_sujeto(sujeto: str) -> str:
    """Devuelve lo que el AV AGENT ya detectó sobre algo (un ticker, una tabla,
    un job): qué problema, de qué habilidad, en qué estado y desde cuándo. Usar
    para no repetir un diagnóstico que el agente ya hizo."""
    r = _consultar(
        "SELECT habilidad, regla, severidad, estado, problema, "
        "       to_char(detectado_at,'YYYY-MM-DD HH24:MI') AS desde, veces "
        "  FROM agente.hallazgos WHERE upper(sujeto) = upper(%s) "
        " ORDER BY detectado_at DESC LIMIT 10",
        (sujeto.strip(),))
    return _tabla(r, f"el agente no tiene ningún hallazgo sobre «{sujeto}». "
                     f"Ojo: eso puede querer decir que está todo bien, o que "
                     f"ninguna habilidad lo mira.")


HERRAMIENTAS_SQL = [ficha_del_bono, precio_del_simbolo, hallazgos_del_sujeto]
