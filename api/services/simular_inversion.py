"""Simular Inversión — ¿qué cobro si pongo $X en este bono a este precio?

Doc: `docs/RENTA_FIJA.md` §0 (paso 21). Service PURO (sin FastAPI). Alimenta el
modal SIMULAR INVERSIÓN de la vista /renta-fija.

**El contrato que ordena este archivo: la simulación corre el MISMO motor que
produce la TEA de la tabla.** `engines.curvas.calcular_campos` recibe el precio
como argumento (no lo busca), así que simular es literalmente llamarlo con el
precio que tipeó el usuario — el patrón ya probado por `agente/alta._simular_tasa`
y `jobs/backfill_tasas`. Si acá hubiera otra fórmula, el día que difieran nadie
se entera: la tabla diría 12% y el modal 14% y los dos parecerían un dato.

Por lo mismo, el CRONOGRAMA sale de `bono_detalle.cronograma` (despacha por
`rama_calculo`; sumar `amortizacion + interes` a mano daría CERO para soberanos
y CER — sus campos se llaman distinto) y las conversiones TEM/TNA de
`quant.tasas` (la misma convención que muestra la tabla).

**Escala y monedas.** El importe se asume en la moneda en que COTIZA la pata
elegida (comprás AL30 con pesos, AL30D con dólares): `vn = importe × 100 /
precio`, y cada flujo escala por `vn / 100`. Los flujos se pagan en SU moneda
(`moneda_flujo`), que puede no ser la del precio — un AL30 se paga en ARS y
cobra USD. Cuando difieren, la ganancia directa se calcula pasando el importe a
la moneda del flujo vía MEP live (si no hay MEP, viaja el warning y no se
inventa un tipo de cambio).

**CER: acá SÍ se ajusta, con bandera.** La ficha (`bono_detalle`) muestra los
flujos contractuales a valores de emisión porque su pregunta es "¿qué promete el
papel?". La de este modal es "¿cuánta plata me entra?", y a valores de emisión
la respuesta sería mentira por el total de la inflación desde la emisión. Cada
flujo usa su CER de liquidación (T−10 hábiles) si ya está publicado; para los
futuros se proyecta con el ÚLTIMO CER constante y viaja `cer_proyectado=True`
— el mismo criterio (y el mismo warning) que usaba Comparar Inversión.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from api.cache import cached
from api.services._sql import _f
from core import curvas_sql, market_snapshot

logger = logging.getLogger(__name__)


@cached(ttl=60)
def _deps_calculo() -> dict:
    """CER + días hábiles + MEP + A3500, una sola carga cada 60s.

    El modal pega en cada cambio de precio (con debounce del lado del front);
    recargar la serie CER de SQL en cada tecleo sería pagar 1200 filas por
    keystroke para un dato que cambia una vez por día.
    """
    from engines.curvas import cargar_a3500_actual, cargar_cer, cargar_dias_habiles
    mep = None
    try:
        from api.services.macro import get_ultimo_mep
        # get_ultimo_mep devuelve un DICT {mep, ccl, ...}, no un float.
        mep = ((get_ultimo_mep() or {}).get("mep")) or None
    except Exception:
        logger.warning("simular_inversion: sin MEP live", exc_info=True)
    return {
        "cer": cargar_cer(dias=1200),
        "habiles": cargar_dias_habiles(),
        "mep": float(mep) if mep else None,
        "a3500": cargar_a3500_actual(),
    }


def _moneda_del_precio(rama: str, simbolo: str | None, moneda_flujo: str | None) -> str:
    """En qué moneda está el PRECIO que tipea el usuario. Espejo de cómo el motor
    trata el precio en cada rama (`calcular_campos`):

    - soberanos / ON hard-dollar → la decide el SUFIJO del símbolo (D/C → USD,
      sin sufijo → ARS), la misma regla que `precio_soberano_a_usd`. NO se mira
      `moneda_eje`: AL30 tiene eje USD pero su pata en pesos cotiza en ARS.
    - dolar_linked y el resto → ARS (el motor los trata como precio en pesos).
    """
    if rama == "soberanos" or (rama == "on" and (moneda_flujo or "").upper() == "USD"):
        partes = (simbolo or "").split(" - ")
        s = partes[2] if len(partes) >= 3 else (simbolo or "")
        return "USD" if s and s[-1].upper() in ("D", "C") else "ARS"
    return "ARS"


def _precio_referencia(simbolo: str | None) -> float | None:
    """Last del snapshot live para la pata (el default del input de precio)."""
    if not simbolo:
        return None
    try:
        m = (market_snapshot.cols_map([simbolo], ["last_price"]) or {}).get(simbolo) or {}
        px = _f(m.get("last_price"))
        return px if px and px > 0 else None
    except Exception:
        return None


def _factor_cer(doc: dict, fecha_iso: str, deps: dict) -> tuple[float | None, bool]:
    """`(factor, proyectado)` para UN flujo CER: CER(liquidación) / CER(emisión).

    Liquidación = T−10 hábiles del pago (el mismo n=10 del motor). Si esa fecha
    todavía no tiene CER publicado, se usa el último conocido y se marca
    proyectado: CER constante, sin proyección de inflación.
    """
    from engines.curvas import get_cer_liquidacion
    cer_emision = _f(doc.get("cer_emision"))
    if not cer_emision or not deps["cer"]:
        return None, False
    cer_liq = get_cer_liquidacion(deps["cer"], deps["habiles"], fecha_iso, n=10)
    if cer_liq:
        return float(cer_liq) / cer_emision, False
    ultimo = deps["cer"][max(deps["cer"].keys())]
    return float(ultimo) / cer_emision, True


def simular(ticker: str, importe: float, precio: float | None = None) -> dict:
    """La simulación completa de UNA compra: métricas al precio dado + cronograma
    escalado al importe.

    `ticker` es el CORTO (`AL30`, PK de `mercado.curvas`). `precio` en la moneda
    en que cotiza la pata; si viene None se usa el last del snapshot. Devuelve
    `{"error": ...}` en vez de tirar, igual que `bono_detalle.get_bono`.
    """
    from engines.curvas import calcular_campos, curva_depende_de, rama_calculo
    from quant.tasas import rendimiento_al_plazo, tem_desde_tea, tna_desde_tea

    tk = (ticker or "").strip().upper()
    if not tk:
        return {"error": "ticker vacío"}
    if not importe or importe <= 0:
        return {"error": "importe inválido"}

    doc = curvas_sql.find_one(tk)
    if not doc:
        return {"error": f"{tk} no está en el master de curvas"}

    # El cronograma contractual sale de la MISMA función que usa el modal FICHA
    # (bono_detalle.cronograma, que despacha por rama) — así los dos modales no
    # pueden contradecirse. Es pura sobre el doc: acá no hay segunda consulta.
    from api.services.bono_detalle import cronograma
    _, flujos_100 = cronograma(doc)

    simbolo = doc.get("ticker")  # símbolo de mercado (blob: clave vieja)
    precio_ref = _precio_referencia(simbolo)
    precio_sim = _f(precio) if precio and precio > 0 else precio_ref
    if not precio_sim:
        return {"error": f"{tk} no tiene precio live y no se indicó uno",
                "precio_referencia": None}

    deps = _deps_calculo()
    rama = rama_calculo(doc)
    warnings: list[str] = []

    # ── Métricas al precio simulado: EL MOTOR, con el precio inyectado ──
    mep = deps["mep"] if curva_depende_de(rama, "mep") else None
    a3500 = deps["a3500"] if curva_depende_de(rama, "a3500") else None
    if curva_depende_de(rama, "mep") and not mep:
        warnings.append("mep_faltante")
    if curva_depende_de(rama, "a3500") and not a3500:
        warnings.append("a3500_faltante")
    try:
        campos = calcular_campos(
            {"price": float(precio_sim), "timestamp": datetime.now(UTC)},
            doc, deps["cer"], deps["habiles"], mep=mep, tc_a3500=a3500,
        ) or {}
    except Exception as e:
        logger.warning("simular_inversion: motor falló para %s: %s", tk, e)
        campos = {}
        warnings.append("motor_no_calculo")

    tea = _f(campos.get("TEA"))
    metrics = {
        "TEA": tea,
        # TEM/TNA con la MISMA convención de quant.tasas para todas las ramas
        # (calcular_campos solo trae TEM en algunas).
        "TEM": _f(campos.get("TEM")) if campos.get("TEM") is not None else tem_desde_tea(tea),
        "TNA": tna_desde_tea(tea),
        "duration": _f(campos.get("duration")),
        "mod_duration": _f(campos.get("mod_duration")),
        "convexity": _f(campos.get("convexity")),
        "paridad": _f(campos.get("paridad")),
    }

    # ── Cronograma escalado: solo flujos futuros, CER ajustado, × vn/100 ──
    vn_nominal = float(importe) * 100.0 / float(precio_sim)
    factor_vn = vn_nominal / 100.0
    cer_proyectado = False
    flujos_out: list[dict] = []
    for f in flujos_100:
        if not f.get("futuro"):
            continue  # la compra es HOY: lo ya pagado no se cobra
        amort, interes, monto = f["amortizacion"], f["interes"], f["monto"]
        if rama == "cer":
            fc, proy = _factor_cer(doc, f["fecha"], deps)
            if fc is None:
                warnings.append("cer_sin_serie")
                continue
            amort, interes, monto = amort * fc, interes * fc, monto * fc
            cer_proyectado = cer_proyectado or proy
        flujos_out.append({
            "fecha": f["fecha"],
            "amortizacion": round(amort * factor_vn, 2),
            "interes": round(interes * factor_vn, 2),
            "monto": round(monto * factor_vn, 2),
            "monto_por_100": round(monto, 6),
        })

    total_a_cobrar = round(sum(f["monto"] for f in flujos_out), 2)

    # ── Ganancia directa: importe y cobros pueden estar en monedas distintas ──
    from engines.curvas import moneda_flujo_esperada
    moneda_flujo = ((doc.get("moneda_flujo") or "").upper()
                    or moneda_flujo_esperada(doc) or None)
    if rama in ("cer", "tasa_fija"):
        moneda_flujo = "ARS"  # CER: ajustado acá, ya en ARS corrientes
    elif rama == "soberanos":
        moneda_flujo = "USD"  # los flujos del master ya están en USD
    moneda_precio = _moneda_del_precio(rama, simbolo, moneda_flujo)
    importe_en_moneda_flujo: float | None = float(importe)
    if moneda_precio and moneda_flujo and moneda_precio != moneda_flujo:
        if deps["mep"]:
            if moneda_precio == "ARS" and moneda_flujo == "USD":
                importe_en_moneda_flujo = float(importe) / deps["mep"]
            elif moneda_precio == "USD" and moneda_flujo == "ARS":
                importe_en_moneda_flujo = float(importe) * deps["mep"]
            else:
                # Par de monedas que no sabemos convertir (ej. flujo "DL"):
                # mejor sin ganancia que con una inventada.
                importe_en_moneda_flujo = None
        else:
            importe_en_moneda_flujo = None
            if "mep_faltante" not in warnings:
                warnings.append("mep_faltante")
    ganancia = rendimiento = None
    if importe_en_moneda_flujo and flujos_out:
        ganancia = round(total_a_cobrar - importe_en_moneda_flujo, 2)
        rendimiento = round(total_a_cobrar / importe_en_moneda_flujo - 1, 6)

    dias_al_vto = None
    vto = str(doc.get("fecha_vencimiento") or "")[:10] or None
    if vto:
        try:
            dias_al_vto = (date.fromisoformat(vto) - date.today()).days
        except ValueError:
            pass
    if cer_proyectado:
        warnings.append("cer_proyectado_constante")

    return {
        "ticker": tk,
        "instrumento": simbolo,
        "rama": rama,
        # La ficha sale del doc del master — los MISMOS campos que muestra el
        # modal FICHA (bono_detalle.get_bono), sin la segunda consulta a la vista.
        "ficha": {
            "emisor":       doc.get("emisor"),
            "emisor_tipo":  doc.get("emisor_tipo"),
            "tipo":         doc.get("tipo"),
            "curva":        doc.get("curva"),
            "moneda":       doc.get("moneda_eje"),
            "moneda_flujo": doc.get("moneda_flujo"),
            "ajuste":       doc.get("ajuste"),
            "ajuste_alt":   doc.get("ajuste_alt"),
            "ley":          doc.get("ley"),
            "fecha_emision":     str(doc.get("fecha_emision") or "")[:10] or None,
            "fecha_vencimiento": vto,
            "valor_nominal":     _f(doc.get("valor_nominal")),
            "cupon_anual":       _f(doc.get("cupon_anual")),
            "cer_emision":       _f(doc.get("cer_emision")),
            "flujo_vencimiento": _f(doc.get("flujo_vencimiento")),
        },
        "moneda_precio": moneda_precio,
        "moneda_flujo": moneda_flujo,
        "precio_referencia": precio_ref,
        "simulacion": {
            "precio": float(precio_sim),
            "importe": float(importe),
            "vn_nominal": round(vn_nominal, 2),
            "metrics": metrics,
            "rendimiento_al_vto": rendimiento_al_plazo(tea=tea, dias=dias_al_vto),
            "dias_al_vto": dias_al_vto,
            "total_a_cobrar": total_a_cobrar,
            "importe_en_moneda_flujo": (round(importe_en_moneda_flujo, 2)
                                        if importe_en_moneda_flujo else None),
            "ganancia": ganancia,
            "rendimiento_directo": rendimiento,
            "flujos": flujos_out,
            "n_pagos": len(flujos_out),
            "cer_proyectado": cer_proyectado,
        },
        "warnings": sorted(set(warnings)),
    }
