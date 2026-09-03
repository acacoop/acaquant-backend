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

from langchain_core.tools import tool

from lab.langgraph.base import leer as _consultar


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


@tool
def reincidencias(sujeto: str = "") -> str:
    """Devuelve las REINCIDENCIAS: cosas que el agente dio por arregladas y
    volvieron. Trae qué arreglo se había aplicado, cuándo se cerró, cuándo
    volvió y **cuántos días aguantó**. Sin `sujeto` trae las últimas; con
    `sujeto` (un ticker, un job) trae las de eso.

    Es la tabla que DEBERÍA estar vacía: cada fila es un arreglo que no sirvió,
    y averiguar por qué no sirvió es distinto de volver a aplicarlo."""
    filtro = " WHERE upper(sujeto) = upper(%s)" if sujeto.strip() else ""
    params = (sujeto.strip(),) if sujeto.strip() else ()
    r = _consultar(
        "SELECT sujeto, habilidad, regla, arreglo_aplicado, "
        "       to_char(resuelto_at,'YYYY-MM-DD HH24:MI') AS se_cerro, "
        "       to_char(volvio_at,  'YYYY-MM-DD HH24:MI') AS volvio, "
        "       round(dias_aguanto, 1) AS dias_aguanto, "
        "       hallazgo_previo_id, hallazgo_id "
        f"  FROM agente.reincidencias{filtro} "
        " ORDER BY volvio_at DESC LIMIT 15", params)
    return _tabla(r, "no hay reincidencias registradas"
                     + (f" para «{sujeto}»" if sujeto.strip() else ""))


@tool
def acciones_sobre(sujeto: str) -> str:
    """EL LIBRO: qué escribió el agente sobre algo, **de qué valor a qué valor**,
    en qué tabla, quién lo pidió y si salió bien. Usar para saber qué se hizo
    realmente, no qué se pensaba hacer — un arreglo que salió «ok» y no cambió
    nada se ve acá y en ningún otro lado."""
    r = _consultar(
        "SELECT to_char(at,'YYYY-MM-DD HH24:MI') AS cuando, arreglo, regla, "
        "       donde, campo, antes, despues, ok, error, por "
        "  FROM agente.acciones WHERE upper(sujeto) = upper(%s) "
        " ORDER BY at DESC LIMIT 15",
        (sujeto.strip(),))
    return _tabla(r, f"el agente nunca escribió nada sobre «{sujeto}»")


@tool
def corridas_del_job(tipo: str) -> str:
    """Las últimas corridas de un job: cuándo arrancó, cuándo terminó, si salió
    ok / partial / error. El `tipo` es el nombre del job, como 'cleanup_curvas'
    o 'dolar_mep'. Usar para ubicar QUÉ corrió cerca del momento en que algo se
    rompió — muchas veces la causa es otro proceso nuestro, no un bug."""
    r = _consultar(
        "SELECT tipo, to_char(started_at,'YYYY-MM-DD HH24:MI') AS arranco, "
        "       to_char(finished_at,'YYYY-MM-DD HH24:MI') AS termino, status "
        "  FROM manager.job_runs WHERE tipo ILIKE %s "
        " ORDER BY started_at DESC LIMIT 12",
        (f"%{tipo.strip()}%",))
    return _tabla(r, f"no hay corridas registradas de «{tipo}». Ojo: puede que "
                     f"el job se llame distinto, o que nunca haya corrido.")


HERRAMIENTAS_SQL = [ficha_del_bono, precio_del_simbolo, hallazgos_del_sujeto,
                    reincidencias, acciones_sobre, corridas_del_job]
