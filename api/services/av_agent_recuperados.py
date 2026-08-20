"""api/services/av_agent_recuperados.py — CUANDO ALGO VUELVE, TAMBIÉN SE AVISA.

Doc madre: **`docs/AV_AGENT.md`** §0.ah.

Pedido del user (2026-08-20): *«quiero que el agente sepa avisar cuando un motor
que estaba caído vuelve a funcionar»*.

**Hoy se arregla y desaparece en silencio.** Los hallazgos de rueda se REEMPLAZAN
en cada corrida (§0.u): si el motor vuelve, su fila simplemente no se escribe. El
que estaba esperando no se entera nunca, y termina entrando a la pantalla cada
diez minutos a ver si sigue el problema. Peor todavía cuando el aviso salió por
mensaje al back office: quedan con la mala noticia y sin la buena.

CÓMO SE SABE QUE VOLVIÓ, SIN GUARDAR NADA NUEVO
===============================================

La corrida anterior **todavía está en la tabla** cuando arranca la nueva —
`reemplazar_hallazgos` borra e inserta en la misma transacción, así que hasta ese
momento lo viejo sigue ahí. Se lee antes de pisar y se resta:

    lo que estaba mal antes  −  lo que está mal ahora  =  lo que se arregló

Sin tabla nueva, sin estado que mantener y sin poder desincronizarse.

QUÉ SE ANUNCIA Y QUÉ NO
=======================

Solo las piezas del SISTEMA: motores, jobs, proveedores, tablas. **Un bono que
consiguió punta no es una noticia** — pasa cien veces por día y anunciarlo
llenaría la pantalla de confeti hasta que nadie mire ninguna.

Y **vence rápido** (30 min): una buena noticia envejece más rápido que una mala.
«Volvió hace tres horas» no le sirve a nadie y ocupa el lugar de lo que sí pasa
ahora.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Qué vale la pena anunciar cuando vuelve. Son los tipos por los que alguien
# estuvo esperando: si se avisó que se cayó, se avisa que volvió.
SE_ANUNCIA = ("proveedor_caido", "motor_caido", "motor_ruidoso", "tabla_quieta",
              "salud")

# Una buena noticia envejece rápido. A los 30 minutos ya no informa: estorba.
VENCE_EN_S = 30 * 60


def clave(h: dict) -> str:
    """La identidad de un problema entre corridas.

    `tipo:ticker` y **no la regla**: si un motor pasa de «no produce» a «error»
    sigue siendo el mismo motor roto, y contarlo como uno que se arregló y otro
    nuevo sería mentir dos veces.
    """
    return f"{(h.get('tipo') or '').strip()}:{(h.get('ticker') or '').strip()}"


def detectar_recuperados(nuevos: list[dict], *, alcance: str = "live") -> list[dict]:
    """Los que estaban mal en la corrida anterior y ahora no.

    Se llama ANTES de pisar los hallazgos viejos. **Nunca levanta**: si esto
    falla, la corrida tiene que guardar igual lo que encontró.
    """
    try:
        antes = _lo_de_antes(alcance)
    except Exception as e:
        logger.warning("av_agent_recuperados: no pude leer lo anterior (%s)", e)
        return []

    ahora = {clave(h) for h in (nuevos or [])}
    out = []
    for k, viejo in antes.items():
        if k in ahora or viejo["tipo"] not in SE_ANUNCIA:
            continue
        h = _hallazgo(viejo)
        # ⚠️ **SI LA MALA NOTICIA SALIÓ POR MENSAJE, LA BUENA TAMBIÉN.** Cuando
        # se cayó un proveedor se le avisó al back office (§0.ad); dejarlos con
        # la mala y sin la buena es peor que no haber avisado: siguen operando a
        # mano y pensando que falta la mitad del día.
        if viejo["tipo"] == "proveedor_caido":
            h["evidencia"]["aviso"] = _avisar_vuelta(viejo)
        out.append(h)
    return out


def _avisar_vuelta(viejo: dict) -> dict:
    """El mismo canal y la misma gente que recibieron la caída."""
    try:
        from datetime import date

        from api.services import av_agent_mensajes as msg
        from api.services.av_agent_proveedores import _a_quien
        a = _a_quien()
        if not a:
            return {"enviados": 0, "error": "nadie a quien avisar"}
        nombre = (viejo.get("ticker") or "?").upper()
        tema = f"proveedor_volvio:{viejo.get('ticker')}:{date.today().isoformat()}"
        r = msg.enviar_muchos(
            [{"para": e, "tema": tema,
              "asunto": f"{nombre} VOLVIÓ · ya funciona",
              "detalle": "Los datos del día vuelven solos en la próxima carga.",
              "donde": "BACK OFFICE · TESORERÍA"} for e in a],
            por="av_agent_recuperados")
        return {"enviados": r.get("enviados", 0), "a": a}
    except Exception as e:                                  # nunca hacia arriba
        logger.warning("av_agent_recuperados: no pude avisar la vuelta (%s)", e)
        return {"enviados": 0, "error": str(e)[:200]}


def _lo_de_antes(alcance: str) -> dict[str, dict]:
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT tipo, ticker, motivo, severidad, corrida_at "
            "FROM mercado.av_agent_hallazgos WHERE alcance = %s", (alcance,))
        filas = cur.fetchall()
    return {f"{r[0]}:{r[1]}": {"tipo": r[0], "ticker": r[1], "motivo": r[2],
                               "severidad": r[3], "desde": r[4]}
            for r in filas}


def _hallazgo(viejo: dict) -> dict:
    """El aviso de que volvió. Corto, como todos (§0.ag)."""
    nombre = (viejo.get("ticker") or "?").upper()
    return {
        "tipo": "recuperado", "ticker": viejo.get("ticker"), "regla": "volvio",
        # BAJA siempre: es una buena noticia. Que aparezca arriba de un problema
        # real sería exactamente al revés de lo que la pantalla tiene que hacer.
        "severidad": "baja",
        "motivo": f"{nombre} VOLVIÓ · ya funciona",
        "evidencia": {
            "texto": (f"Estaba: {viejo.get('motivo') or 'con problemas'}\\n"
                      f"Ahora responde. Este aviso se va solo en 30 min."),
            "era_tipo": viejo.get("tipo"),
            "era_severidad": viejo.get("severidad"),
            "era_motivo": viejo.get("motivo")}}
