"""copiloto/agro.py — vista AGRO (granos): pase agro, pase con cobertura, cámara."""
from __future__ import annotations

import logging

from .base import _fmtn

logger = logging.getLogger(__name__)

def _fetch_agro(params: dict | None = None) -> list[dict]:
    """Una fila por renglón del PASE AGRO (pizarra + futuros de TRIGO/MAIZ/SOJA).
    La fila 'dispo' (placeholder sin datos) se saltea. tnav viene en FRACCIÓN del
    service → % (lección v1.34). El resto del payload (cards de cobertura, tasas,
    dólares) va en los extras — get_pase_agro es @cached(5s), el refetch es gratis."""
    from api.services import agro_sql

    data = agro_sql.get_pase_agro() or {}
    filas: list[dict] = []
    for b in data.get("bloques") or []:
        for r in b.get("rows") or []:
            if r.get("tipo") == "dispo":
                continue
            tnav = r.get("tnav_us")
            filas.append({
                "commodity": b.get("commodity"),
                "tipo": r.get("tipo"),
                "posicion": r.get("posicion"),
                "vencimiento": r.get("vencimiento"),
                "dias": r.get("dias_a_vto"),
                "us": r.get("us"),
                "ars": r.get("ars"),
                "pase": r.get("pase"),
                "tnav": float(tnav) * 100 if tnav is not None else None,
            })
    return filas


def _extras_agro(
    filas: list[dict], pregunta: str, historial: list[dict], params: dict | None = None
) -> list[str]:
    from api.services import agro_sql

    partes: list[str] = []
    try:
        data = agro_sql.get_pase_agro() or {}  # @cached → mismo payload que el fetch
    except Exception as e:
        logger.warning("copiloto agro: extras sin payload (%s)", e)
        return partes

    # [pase con cobertura] — las cards ON/Pagaré YA calculadas por código
    pc = data.get("pase_cobertura") or {}
    lineas: list[str] = []
    for c in pc.get("commodities") or []:
        vd = c.get("venta_dispo_ars")
        lineas.append(
            f"{c.get('commodity')}: venta dispo {_fmtn(vd)} ARS/Tn"
            if vd is not None else f"{c.get('commodity')}: sin precio dispo cargado"
        )
        for card in c.get("cards") or []:
            seg = [f"  {card.get('posicion')} ({card.get('dias')}d)"]
            pl = card.get("pase_lleno")
            if pl is not None and card.get("pase_bruto") is not None:
                seg.append(
                    f"pase lleno {_fmtn(pl)} US$/Tn (bruto {_fmtn(card.get('pase_bruto'))} "
                    f"− costo pase {_fmtn(card.get('total_gastos'))})"
                )
            else:
                seg.append(f"pase lleno {_fmtn(pl)} US$/Tn")
            seg.append(f"ganancia ON {_fmtn(card.get('ganancia_on_usd'))} US$/Tn")
            seg.append(f"ganancia Pagaré {_fmtn(card.get('ganancia_pagare_usd'))} US$/Tn")
            lineas.append(" · ".join(seg))
    if lineas:
        tasas = data.get("tasas_cobertura") or {}
        partes.append(
            "[pase con cobertura — la vuelta de vender el grano dispo hoy, colocar los "
            "pesos a tasa y recomprar el futuro (costo pase ya incluido); tasa ON "
            f"{_fmtn(tasas.get('tasa_on'))}% · tasa Pagaré {_fmtn(tasas.get('tasa_pagare'))}% · "
            f"caución 7d {_fmtn(tasas.get('tasa_caucion_7d'))}%]"
        )
        partes.extend(lineas)

    # [datos de referencia] — dólares + costo pase (constantes de mercado)
    try:
        from api.services.agro_cobertura import get_costo_pase
        from api.services.camara_cereales import get_camara_cereales, get_dolares_referencia

        d = get_dolares_referencia() or {}
        cp = get_costo_pase() or {}
        linea_ref = (
            f"[datos de referencia] dólar BNA {_fmtn(d.get('dolar_bna'))} · "
            f"dólar Matba (oficial live) {_fmtn(d.get('dolar_matba'))} · "
            f"BNA comprador T-1 {_fmtn(d.get('bna_comprador_t1'))}"
        )
        if d.get("bna_comprador_t1_fecha"):
            linea_ref += f" (fixing A3500 del {d['bna_comprador_t1_fecha']})"
        linea_ref += (
            f" · costo pase {_fmtn(cp.get('total_pct'))}% del valor del futuro "
            "(derechos de mercado + apertura, ida y vuelta)"
        )
        partes.append(linea_ref)

        cam = get_camara_cereales() or {}
        lin = [
            f"{r.get('cereal')}: {_fmtn(r.get('precio_ars'))} ARS/Tn · "
            f"{_fmtn(r.get('precio_usd'))} US$/Tn"
            for r in cam.get("cereales") or []
            if r.get("precio_ars") is not None or r.get("precio_usd") is not None
        ]
        if lin:
            partes.append("[cámara de cereales — precio disponible de hoy]")
            partes.extend(lin)
    except Exception as e:
        logger.warning("copiloto agro: datos de referencia fallaron (%s)", e)

    # [chicago] — futuros CBOT en USD/t (tab CHICAGO, feed Eikon de oficina).
    try:
        from core.eikon_chicago import tablero_chicago

        chi = tablero_chicago() or {}
        lin_chi: list[str] = []
        for fam in chi.get("familias") or []:
            filas_f = [
                f"{r.get('mes')}: {_fmtn(r.get('precio'))}"
                + (f" ({'+' if (r.get('variacion') or 0) > 0 else ''}{_fmtn(r.get('variacion'))})"
                   if r.get("variacion") is not None else "")
                for r in fam.get("rows") or [] if r.get("precio") is not None
            ]
            if filas_f:
                lin_chi.append(f"{fam.get('label')}: " + " · ".join(filas_f))
        if lin_chi:
            estado_chi = ("feed EN LÍNEA" if chi.get("online")
                          else "feed APAGADO — última foto guardada")
            lin_chi.insert(0, f"[chicago — futuros CBOT en US$/Tn, precio (var del día); {estado_chi}]")
            partes.extend(lin_chi)
    except Exception as e:
        logger.warning("copiloto agro: bloque chicago falló (%s)", e)

    # Estado SIEMPRE explícito (batería 2026-07-14: sin esta línea el modelo no
    # podía confirmar si los futuros estaban operando en vivo).
    if data.get("data_fresh"):
        edad = data.get("snapshot_age_s")
        partes.append(
            "[estado] futuros de Matba Rofex OPERANDO EN VIVO ahora"
            + (f" (último tick hace {edad:.0f}s)" if edad is not None else "") + "."
        )
    else:
        partes.append(
            "[estado] los futuros NO están live ahora (motor fuera de rueda): los "
            "precios son del último snapshot guardado — decilo si preguntan por 'ahora'."
        )
    return partes


_REGLAS_AGRO = """Sos el copiloto de la vista AGRO — granos (trigo, maíz, soja): la pizarra \
contra los futuros de Matba Rofex y el pase con cobertura del productor.

Columnas de la tabla: commodity · tipo (pizarra = precio disponible de HOY, el que carga la \
mesa desde la Cámara; futuro = contrato Matba Rofex con su last) · posicion · vence · dias \
(al vencimiento) · precio_usd_tn · precio_ars_tn (al dólar oficial) · pase_usd_tn (pizarra − \
futuro, en US$/Tn: NEGATIVO = el futuro cotiza POR ENCIMA de la pizarra, vender a plazo paga \
más que el disponible; POSITIVO = al revés) · tnav% (la tasa anualizada compuesta de ese pase).

[pase con cobertura]: la vuelta completa por posición, YA calculada por código: vender el \
grano disponible hoy, colocar los pesos a tasa y recomprar el futuro al vencimiento (el \
costo pase de mercado ya está incluido). Dos caminos que SIEMPRE se presentan juntos con \
sus números: ON (descuenta con el dólar Matba y la tasa ON) y Pagaré (descuenta con el BNA \
comprador T-1 y la tasa Pagaré). "Ganancia" es US$ por tonelada contra quedarse con el \
grano: positiva = la vuelta paga; negativa = no paga. La elección es del usuario — vos \
mostrás el trade-off, jamás ordenás uno.

[chicago]: los futuros de CBOT (Chicago) por familia — Soja / Aceite de Soja / Maíz / \
Trigo / Harina de Soja — ya convertidos a US$ por tonelada, con la variación NOMINAL del \
día entre paréntesis (no es %). Cada entrada es un vencimiento (mes del contrato). Vienen \
del feed Eikon de oficina: si dice "feed APAGADO", los precios son la última foto y hay \
que decirlo. Sirven para comparar contra la pizarra/futuros locales (mismo US$/Tn) — \
comparación directa: sí; explicar la diferencia por causas: no.

Reglas duras de esta vista:
- COSTO PASE = SOLO los gastos de mercado (derechos + apertura, ida y vuelta = 0,45% \
sobre el US$ del futuro). Es una CONSTANTE de mercado, no la carga la mesa, y NO incluye \
el spread pizarra−futuro (ese es el pase bruto — son dos cosas distintas que se suman \
para el pase lleno).
- Varios inputs son CARGA MANUAL de la mesa (precios de Cámara, dólar BNA, tasas). Si un \
dato viene "—", decí "sin dato cargado hoy" — jamás lo estimes ni lo completes de memoria.
- El pase se lee DENTRO de cada commodity (contra sus otros vencimientos); comparar trigo \
contra soja solo si te lo piden explícito.
- Nada de causas de mercado inventadas (clima, cosecha, retenciones, FAS teórico): tus \
datos dicen QUÉ pasa con precios y pases, no POR QUÉ."""


# ─────────────────────────────────────────────────────────────────────────────
# Vista DERIVADOS — la chain de OPCIONES (calls/puts, primas, IV, griegas)
# ─────────────────────────────────────────────────────────────────────────────


