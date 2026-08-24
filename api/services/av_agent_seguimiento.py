"""api/services/av_agent_seguimiento.py — ¿LO QUE SE ARREGLÓ, SIGUIÓ ARREGLADO?

Doc madre: **`docs/AV_AGENT.md`** §0.ac.

Pedido del user (2026-08-19):

    «Necesito que este agente entienda cuándo hizo algo bien, no solamente
     porque yo le puse "acertó", sino porque queda registrado y al otro día o
     durante unos días puede detectar que los cambios que se marcaron como
     hechos realmente tuvieron consistencia. Es como que yo diga que modelé bien
     un bono: mañana cuando abre el mercado lo veo en la tabla y digo sí, lo hice
     bien, porque si no vería un error.»

LA DIFERENCIA ENTRE «LO APLIQUÉ» Y «FUNCIONÓ»
==============================================

Hasta hoy el agente verificaba **releyendo la base en el mismo segundo**: que la
escritura entró. Eso no dice nada sobre si el arreglo era el correcto — *un
símbolo mal puesto se escribe igual de bien que uno bien puesto*.

La única prueba que vale es el tiempo. Si el problema no vuelve en los días
siguientes, el arreglo era el bueno. Y si vuelve, el diagnóstico estaba mal — que
es información igual de valiosa y hasta hoy se perdía entera.

POR QUÉ ESTO VALE MÁS QUE UN CLICK
===================================

Un ✔ humano es una opinión (buena, pero opinión). Que el problema **no haya
vuelto en cinco días** no es la opinión de nadie: volvió o no volvió. Por eso el
voto que sale de acá entra al eval set como `verificado` y **sí cuenta para la
compuerta**, al revés que el `derivado` de una aprobación.

Y no se puede maquillar: el agente no controla si el problema reaparece. Es lo
más parecido a una recompensa verificable que este sistema puede tener.

⚠️ **«Todavía no volvió» NO es «aguantó».** Hasta que pasa la ventana no se
afirma nada. Sin esa espera estaríamos premiando un arreglo de hace una hora, que
es justo lo que no queremos medir.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Cuántos días se mira un arreglo antes de darlo por bueno. Cinco son una semana
# de mercado: un problema de datos que iba a volver, vuelve adentro de eso.
DIAS_DE_PRUEBA = 5

MIRANDO, AGUANTO, VOLVIO = "mirando", "aguanto", "volvio"


def anotar(*, clave: str, sujeto: str, regla: str, tipo: str = "",
           dominio: str = "bono", por: str = "", que_se_hizo: str = "",
           dias: int = DIAS_DE_PRUEBA) -> bool:
    """«Esto se dio por arreglado» → hay que volver a mirarlo.

    Idempotente por `clave`: re-arreglar lo mismo REINICIA la ventana (es un
    arreglo nuevo y merece su propia prueba) y limpia el veredicto anterior.

    **Nunca levanta**: anotar el seguimiento no puede tumbar la acción que
    acababa de arreglar algo de verdad.
    """
    clave = (clave or "").strip()
    if not clave or not (sujeto or "").strip() or not (regla or "").strip():
        return False
    hasta = datetime.now(UTC) + timedelta(days=max(1, dias))
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agente.av_agent_seguimiento "
                "(clave, tipo, sujeto, regla, dominio, arreglado_por, "
                " que_se_hizo, mirar_hasta) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (clave) DO UPDATE SET "
                "  arreglado_at = now(), arreglado_por = EXCLUDED.arreglado_por, "
                "  que_se_hizo = EXCLUDED.que_se_hizo, "
                "  mirar_hasta = EXCLUDED.mirar_hasta, revisiones = 0, "
                "  volvio_at = NULL, veredicto = 'mirando', votado = false",
                (clave, tipo or None, sujeto.strip().upper(), regla.strip(),
                 dominio, por or None, (que_se_hizo or "")[:400], hasta))
        return True
    except Exception as e:
        logger.warning("seguimiento: no pude anotar %s (%s)", clave, e)
        return False


def revisar(claves_abiertas: set[str] | None) -> dict:
    """Pasada diaria: de lo que se dio por arreglado, ¿qué volvió y qué aguantó?

    `claves_abiertas` son las identidades de los problemas que HOY siguen
    abiertos. **`None` = no se pudo saber** y entonces no se toca nada: dar todo
    por bueno porque no pudimos mirar sería la peor forma de premiar (§0.v).
    """
    if claves_abiertas is None:
        return {"ok": False, "error": "no se pudo leer qué sigue abierto — no se "
                                      "cambia ningún veredicto"}
    volvieron: list[dict] = []
    aguantaron: list[dict] = []
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT clave, sujeto, regla, dominio, arreglado_at, mirar_hasta "
                "FROM agente.av_agent_seguimiento WHERE veredicto = %s",
                (MIRANDO,))
            filas = cur.fetchall()
            ahora = datetime.now(UTC)
            for clave, sujeto, regla, dominio, arreglado, hasta in filas:
                if clave in claves_abiertas:
                    cur.execute(
                        "UPDATE agente.av_agent_seguimiento "
                        "SET veredicto = %s, volvio_at = now(), "
                        "    revisiones = revisiones + 1, ultima_revision_at = now() "
                        "WHERE clave = %s", (VOLVIO, clave))
                    volvieron.append({"clave": clave, "sujeto": sujeto,
                                      "regla": regla, "dominio": dominio,
                                      "arreglado_at": arreglado})
                elif ahora >= hasta:
                    cur.execute(
                        "UPDATE agente.av_agent_seguimiento "
                        "SET veredicto = %s, revisiones = revisiones + 1, "
                        "    ultima_revision_at = now() WHERE clave = %s",
                        (AGUANTO, clave))
                    aguantaron.append({"clave": clave, "sujeto": sujeto,
                                       "regla": regla, "dominio": dominio,
                                       "arreglado_at": arreglado})
                else:
                    # Sigue en prueba. **No se afirma nada todavía**: «no volvió
                    # aún» no es «aguantó».
                    cur.execute(
                        "UPDATE agente.av_agent_seguimiento "
                        "SET revisiones = revisiones + 1, ultima_revision_at = now() "
                        "WHERE clave = %s", (clave,))
            conn.commit()
    except Exception as e:
        logger.exception("seguimiento: la revisión falló")
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}

    # ⚠️ `votos` sale SIEMPRE en 0 desde 2026-08-24 — ver `_votar`. Se sigue
    # devolviendo (el job lo imprime) para que el 0 se VEA: sacar el campo haría
    # que «no vota» se lea igual que «no pasó nada».
    votos = _votar(volvieron, aguantaron)
    return {"ok": True, "mirados": len(filas), "volvieron": volvieron,
            "aguantaron": aguantaron, "votos": votos}


def _votar(volvieron: list[dict], aguantaron: list[dict]) -> int:
    """**YA NO VOTA AL EVAL SET** (2026-08-24). Devuelve siempre 0, a propósito.

    ⚠️⚠️ **El motivo: este mecanismo solo podía decir «aguantó».** El veredicto
    sale de `clave in claves_abiertas`, y las dos mitades arman la clave con
    formatos DISTINTOS:

        lo que se anota   `av_agent_acciones` →  accion:objetivo:regla
                                                 `mercado.apuntar_pata:AO29:pata_equivocada`
        contra qué compara `jobs/seguimiento`  →  tipo:sujeto:regla
                                                 `precio_moneda:AO29:pata_equivocada`

    Los ids de acción están namespaceados (`mercado.*`, `assets.*`, `sistema.*`)
    y **ninguno coincide jamás con un tipo de hallazgo**: la intersección es
    vacía por construcción. Entonces `revisar()` nunca puede marcar `volvio`, y
    todo lo que pasa la ventana se sella `aguanto` → un ✔ `verificado` al eval
    set, que la compuerta de autonomía cuenta **igual que un click humano**.

    Y desde §0.de esto quedó siendo el ÚNICO emisor de `verificado` (a
    `cerrar_hitos` se le sacó el voto por la razón espejo: sus dos veredictos
    tampoco significaban lo que decían). O sea: la única fuente que le queda a la
    compuerta es una que **estructuralmente no puede emitir un negativo**.

    Misma decisión que allá, y por la misma regla: **no inventar una señal es
    mejor que fabricar una.** El seguimiento sigue corriendo —`estado()` alimenta
    la pantalla y el veredicto sirve para mirarlo— pero no emite juicio.

    Vuelve a votar cuando la clave sea UNA sola (`clave_de_problema`) en las dos
    puntas. Eso es la Fase 2 del plan: apagar el camino viejo o unificarlo.
    """
    return 0


def estado(limite: int = 60) -> dict:
    """Lo que hay en seguimiento, para la pantalla. Minimalista y completo:
    cuántos aguantaron, cuántos volvieron, y los que están en prueba."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT veredicto, count(*) FROM agente.av_agent_seguimiento "
                "GROUP BY veredicto")
            conteo = {a: int(b) for a, b in cur.fetchall()}
            cur.execute(
                "SELECT clave, sujeto, regla, veredicto, arreglado_at, "
                "       arreglado_por, mirar_hasta, volvio_at, que_se_hizo "
                "FROM agente.av_agent_seguimiento "
                # Lo que VOLVIÓ primero: es lo único accionable de esta lista.
                "ORDER BY (veredicto = 'volvio') DESC, arreglado_at DESC LIMIT %s",
                (limite,))
            cols = ["clave", "sujeto", "regla", "veredicto", "arreglado_at",
                    "arreglado_por", "mirar_hasta", "volvio_at", "que_se_hizo"]
            filas = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "items": []}
    for f in filas:
        for k in ("arreglado_at", "mirar_hasta", "volvio_at"):
            f[k] = f[k].isoformat() if f[k] else None
    return {"ok": True, "aguanto": conteo.get(AGUANTO, 0),
            "volvio": conteo.get(VOLVIO, 0), "mirando": conteo.get(MIRANDO, 0),
            "dias_de_prueba": DIAS_DE_PRUEBA, "items": filas}
