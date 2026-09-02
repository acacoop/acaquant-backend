"""Detectores de NEGOCIO: lo que el back office concilia a mano. Doc: `AGENT.md` §0.dk.

Regla de la casa para este módulo: **el agente lee los mismos services que
dibujan la pantalla** (`api/services/bancos`), nunca reimplementa un predicado.
Si la tab dice «no concilia» y el agente dice otra cosa, uno de los dos miente,
y no hay forma de saber cuál. REGLA #9.
"""
from __future__ import annotations

import logging

from agente import reloj
from agente.tipos import Hallazgo, SinDatos

logger = logging.getLogger(__name__)


def _etiqueta(fila: dict) -> str:
    """Cómo se nombra una cuenta en un hallazgo: la etiqueta del back office
    si la cargaron; si no, banco + número + moneda."""
    if fila.get("cuenta"):
        return str(fila["cuenta"])
    if fila.get("etiqueta"):
        return str(fila["etiqueta"])
    return f"{fila.get('banco_nombre') or ''} {fila.get('numero') or ''} {fila.get('moneda') or ''}".strip()


def _fmt(x) -> str:
    return "?" if x is None else f"{x:,.2f}"


# ═══ banco_no_cierra ═══════════════════════════════════════════════════════
def banco_no_cierra(u: dict) -> list[Hallazgo]:
    """Los bancos, con la conciliación puesta en el agente y no en una tab.

    Cinco reglas, todas sobre lo que el back office ya calcula al abrir la
    pantalla, más UNA cosa que la pantalla no puede ver por diseño:

      · `extracto_no_cierra`: apertura + créditos − débitos ≠ cierre en el
        extracto que mandó el banco (`bancos.extracto_dia.cierra = false`).
      · `saldo_discrepante`: el banco informa dos cierres distintos para el
        mismo día (extracto vs. saldo informado). Es el badge ≠ del consolidado.
      · `banco_vs_mayor`: el tablero del día hábil anterior no concilia para
        esa cuenta (`concilia = false`, el mismo predicado que la tab).
      · `descuadre_arrastrado`: la misma cuenta lleva `arrastre_dias` hábiles
        seguidos sin conciliar. **Esto la tab no lo muestra**: cada día se juzga
        aislado a propósito (`bancos.tablero`, docstring). El agente mira los
        días uno al lado del otro y lo canta como una sola cosa.
      · `pendiente_viejo`: arreglos confirmados para Contabilidad que llevan
        más de `pendiente_dias` hábiles sin marcarse resueltos.

    Sin arreglo, a propósito: conciliar es decidir de qué lado falta plata, y
    eso lo hace una persona. Lo que el agente aporta es que nadie tenga que
    abrir la tab para enterarse.
    """
    from agente import fuentes

    pend_dias = int(u.get("pendiente_dias", 5))
    arrastre = int(u.get("arrastre_dias", 3))
    out: list[Hallazgo] = []
    ciegos: list[str] = []

    # ── el extracto de cada día ──
    ext = fuentes.bancos_extractos()
    if ext is None:
        ciegos.append("bancos.extracto_dia")
    else:
        no_cierra: dict[str, list[dict]] = {}
        for f in ext:
            if f.get("cierra") is False:
                no_cierra.setdefault(_etiqueta(f), []).append(f)
            sc, sd = f.get("saldo_cierre"), f.get("saldo_dia")
            if sc is not None and sd is not None and abs(sc - sd) >= 0.01:
                out.append(Hallazgo(
                    sujeto=_etiqueta(f), regla="saldo_discrepante", severidad="media",
                    problema=(f"«{_etiqueta(f)}»: el banco informa dos cierres distintos para "
                              f"el {f['fecha']}: extracto {_fmt(sc)} · saldo informado "
                              f"{_fmt(sd)} (≠ {_fmt(sc - sd)}) · {reloj.hhmm()}"),
                    detalle=f"extracto {f.get('numero_extracto') or '?'}",
                    que_hacer=("Manda el saldo informado (decisión del back office, "
                               "2026-09-01). Antes de sellar el cierre, revisar el extracto "
                               "de ese día en BACK OFFICE → Interbanking: si el extracto "
                               "está incompleto, `jobs.interbanking_sync --dias 2`."),
                    evidencia={"fecha": str(f["fecha"]), "extracto": sc, "informado": sd,
                               "diferencia": round(sc - sd, 2)}))
        for cuenta, filas in no_cierra.items():
            fechas = [str(x["fecha"]) for x in filas]
            out.append(Hallazgo(
                sujeto=cuenta, regla="extracto_no_cierra", severidad="alta",
                problema=(f"«{cuenta}»: el extracto del banco no cierra el "
                          f"{', '.join(fechas)}: apertura + créditos − débitos difiere del "
                          f"cierre en {_fmt(filas[0].get('diferencia'))} · {reloj.hhmm()}"),
                detalle=" · ".join(f"{x['fecha']}: dif {_fmt(x.get('diferencia'))}"
                                   for x in filas),
                que_hacer=("Abrir BACK OFFICE → Interbanking en esa cuenta y ese día. Si "
                           "faltan movimientos, `jobs.interbanking_sync --dias 2` los trae; "
                           "si el banco informó mal, reclamar a Interbanking con el número "
                           "de extracto."),
                evidencia={"items": fechas,
                           "diferencias": {str(x["fecha"]): x.get("diferencia") for x in filas},
                           "extractos": [x.get("numero_extracto") for x in filas]}))

    # ── banco contra mayor, día por día ──
    tabs = fuentes.bancos_tableros(arrastre)
    if tabs is None:
        ciegos.append("bancos.tablero")
    elif tabs:
        fechas = sorted(tabs, reverse=True)
        hoy = fechas[0]
        for fila in tabs[hoy]:
            if fila.get("concilia") is not False:
                continue
            cid = fila["id"]
            seguidos = 0
            for f in fechas:
                mismo = next((x for x in tabs[f] if x["id"] == cid), None)
                if mismo and mismo.get("concilia") is False:
                    seguidos += 1
                else:
                    break
            cuenta = _etiqueta(fila)
            dif, sin_g = fila.get("diferencia"), fila.get("dif_sin_gastos")
            if seguidos >= arrastre:
                out.append(Hallazgo(
                    sujeto=cuenta, regla="descuadre_arrastrado", severidad="alta",
                    problema=(f"«{cuenta}» lleva {seguidos} días hábiles seguidos sin "
                              f"conciliar contra el mayor (último: {hoy}, diferencia "
                              f"{_fmt(dif)}). La tab juzga cada día aislado y no lo muestra "
                              f"· {reloj.hhmm()}"),
                    detalle=f"sin gastos: {_fmt(sin_g)}",
                    que_hacer=("No es un día: es un movimiento que nunca se cargó al mayor "
                               "y se arrastra. Conciliar desde el primer día que no cierra "
                               f"({fechas[seguidos - 1]}) en BACK OFFICE → Interbanking → "
                               "CONCILIACIÓN y confirmar el pendiente para Contabilidad."),
                    evidencia={"dias_seguidos": seguidos, "desde": str(fechas[seguidos - 1]),
                               "hasta": str(hoy), "diferencia": dif, "dif_sin_gastos": sin_g}))
            else:
                out.append(Hallazgo(
                    sujeto=cuenta, regla="banco_vs_mayor", severidad="media",
                    problema=(f"«{cuenta}» no concilia el {hoy}: el banco cierra "
                              f"{_fmt(fila.get('cierre_banco'))} y el mayor da "
                              f"{_fmt(fila.get('saldo_final'))} (diferencia {_fmt(dif)}) "
                              f"· {reloj.hhmm()}"),
                    detalle=f"sin gastos: {_fmt(sin_g)} · movimientos del mayor: "
                            f"{fila.get('movimientos_mayor')}",
                    que_hacer=("Abrir la conciliación de ese día en BACK OFFICE → Interbanking "
                               "→ CONCILIACIÓN y usar el buscador de candidatos; lo que no "
                               "calce se confirma como pendiente para Contabilidad. Si el "
                               "mayor todavía se está cargando (mirar la hora de la corrida), "
                               "esperar la próxima."),
                    evidencia={"fecha": str(hoy), "cierre_banco": fila.get("cierre_banco"),
                               "saldo_final_mayor": fila.get("saldo_final"),
                               "diferencia": dif, "dif_sin_gastos": sin_g,
                               "gastos": fila.get("gastos")}))

    # ── lo confirmado que nadie cerró ──
    pend = fuentes.bancos_pendientes()
    if pend is None:
        ciegos.append("bancos.conciliacion_pendientes")
    else:
        viejos: dict[str, list[dict]] = {}
        for p in pend:
            if not p.get("resuelto") and int(p.get("dias_habiles") or 0) >= pend_dias:
                viejos.setdefault(_etiqueta(p), []).append(p)
        for cuenta, ps in viejos.items():
            out.append(Hallazgo(
                sujeto=cuenta, regla="pendiente_viejo", severidad="media",
                problema=(f"«{cuenta}»: {len(ps)} arreglo(s) confirmados para Contabilidad "
                          f"llevan más de {pend_dias} días hábiles sin marcarse resueltos "
                          f"· {reloj.hhmm()}"),
                detalle=" · ".join(f"{p.get('fecha')} {p.get('accion')} "
                                   f"{str(p.get('descripcion') or '')[:40]} {_fmt(p.get('importe'))}"
                                   for p in ps[:10]),
                que_hacer=("Si Contabilidad ya lo corrigió, marcarlo resuelto en BACK OFFICE → "
                           "Interbanking → MOVIMIENTOS A CONCILIAR; si no, reclamarlo con "
                           "esta lista. Mientras esté abierto, la conciliación lo vuelve a "
                           "encontrar cada día."),
                evidencia={"items": [str(p.get("id")) for p in ps],
                           "dias_maximo": max(int(p.get("dias_habiles") or 0) for p in ps)}))

    if len(ciegos) == 3:
        raise SinDatos("no pude leer extractos, tablero ni pendientes: no sé cómo están los bancos")
    if ciegos:
        logger.warning("banco_no_cierra: no pude leer %s — esas reglas no cierran nada",
                       ", ".join(ciegos))
    return out
