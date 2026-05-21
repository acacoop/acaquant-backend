"""Comparar Inversión — service que compara 2 bonos de Trading.Curvas lado a lado.

Fase 1: universo restringido a curvas con flujos modelados en
`_calendario_flujos` (cer, tasa_fija, soberanos). `tamar` y `dolar_linked`
quedan fuera del selector hasta que existan en el calendario.

Fase 2 (pendiente): sumar `Trading.BondsMaster` con adapter de shape +
TIR/duration on-the-fly. Ver memoria `project-comparar-inversion-wip`.

Reusa:
- `listar_curva()` → metadata + métricas live de `MarketSnapshot.metrics`.
- `_calendario_flujos()` → cash por cada 100 VN, ya en escala del precio
  (CER ajustado por CER_liq/cer_emision; soberanos en USD; tasa fija
  absolutos).
- `get_ultimo_mep()` → conversión cross-moneda ARS↔USD.
"""
from __future__ import annotations

import logging
from datetime import date

from api.cache import cached
from api.db import get_db_trading
from api.services.renta_fija import listar_curva

logger = logging.getLogger(__name__)

# Curvas con _calendario_flujos soportado. Mapeo a moneda del flujo del bono.
_CURVAS_SOPORTADAS: dict[str, str] = {
    "cer": "ARS",
    "tasa_fija": "ARS",
    "soberanos": "USD",
}


def _moneda_de(curva: str) -> str:
    return _CURVAS_SOPORTADAS.get(curva, "ARS")


@cached(ttl=30)
def listar_bonos_seleccionables() -> list[dict]:
    """Universo del selector. Una entrada por bono.

    `id` con prefijo de fuente para Fase 2 (`curvas:` vs futuro `bm:`).
    Tickers que no tienen precio live se devuelven igual (el cliente los
    muestra deshabilitados); las métricas viven en `metricas` y pueden
    ser None.
    """
    out: list[dict] = []
    for curva in _CURVAS_SOPORTADAS:
        # Para curva='cer' listar_curva ya filtra los fijados; quedan los
        # nativos CER. Los fijados se incluyen al pedir curva='tasa_fija'
        # con badge cer_fijado=true → del lado del cliente se ven en el
        # mismo dropdown con label "(CER fijado)".
        for b in listar_curva(curva=curva):
            ticker_corto = b.get("ticker_corto") or b.get("ticker")
            out.append({
                "id": f"curvas:{ticker_corto}",
                "ticker": b.get("ticker"),
                "ticker_corto": ticker_corto,
                "label": ticker_corto,
                "curva": curva,
                "tipo": b.get("tipo"),
                "moneda": _moneda_de(curva),
                "vencimiento": b.get("fecha_vencimiento"),
                "meses_al_vto": b.get("meses_al_vto"),
                "cer_fijado": b.get("cer_fijado", False),
            })
    # Ordenamos por moneda y luego vencimiento para que el dropdown agrupe
    # naturalmente ARS arriba, USD abajo, cronológico.
    out.sort(key=lambda x: (x["moneda"], x.get("vencimiento") or "9999"))
    return out


def _parse_id(bono_id: str) -> tuple[str, str]:
    """`curvas:TX26` → (`curvas`, `TX26`). Lanza ValueError si malformado."""
    if ":" not in bono_id:
        raise ValueError(f"id inválido: {bono_id}")
    source, ticker_corto = bono_id.split(":", 1)
    if source != "curvas":
        # Fase 2: source='bm' caerá acá hasta que se integre BondsMaster.
        raise ValueError(f"source no soportado: {source}")
    return source, ticker_corto


def _find_bono(ticker_corto: str) -> dict | None:
    """Busca el bono en las curvas soportadas. Único hit por ticker_corto."""
    for curva in _CURVAS_SOPORTADAS:
        for b in listar_curva(curva=curva):
            if b.get("ticker_corto") == ticker_corto:
                return {**b, "_curva": curva}
    return None


def _flujos_de(curva: str, ticker_corto: str) -> tuple[list[dict], bool]:
    """Devuelve los flujos FUTUROS del bono (fecha ≥ hoy) en VN 100,
    normalizados a la misma escala que el precio. Los cupones ya pagados se
    descartan: la comparación es de una inversión hecha hoy. Para CER proyecta
    con `cer_actual / cer_emision` constante (último CER publicado), así el
    cliente ve todos los pagos pendientes y no solo los con CER de liquidación.

    Retorna `(flujos, cer_proyectado)`. `cer_proyectado=True` señala que
    los flujos CER usan factor constante (no proyección de inflación).
    Cada flujo trae el desglose `cupon` (renta) y `amort` (devolución de
    capital) por separado, además del `monto` total. El cliente lo usa para
    graficar solo la renta — si no, el bullet de amortización al vto (≈100)
    aplasta los cupones (≈0.5) y los hace invisibles.
    """
    from engines.curvas import cargar_cer, fecha_flujo

    db = get_db_trading()
    doc = db["Curvas"].find_one(
        {"curva": curva, "ticker_corto": ticker_corto},
        {"_id": 0, "flujos": 1, "cer_emision": 1},
    )
    if not doc:
        return [], False
    flujos_doc = doc.get("flujos") or []

    cer_factor: float | None = None
    cer_proyectado = False
    if curva == "cer":
        cer_emision = doc.get("cer_emision")
        if cer_emision:
            cer_dict = cargar_cer(db.client, dias=15)
            if cer_dict:
                ultimo = cer_dict[max(cer_dict.keys())]
                cer_factor = float(ultimo) / float(cer_emision)
                cer_proyectado = True

    # Inversión que se hace HOY: solo cuentan los flujos futuros. Los cupones
    # ya pagados no se cobran al comprar hoy, así que se descartan (el eje del
    # gráfico arranca en el presente, n_flujos refleja lo que falta cobrar).
    hoy = date.today()

    out: list[dict] = []
    for f in flujos_doc:
        fd = fecha_flujo(f)
        if not fd:
            continue
        if fd < hoy:
            continue
        if curva == "cer" and cer_factor is None:
            continue
        cupon, amort = _desglose_flujo(curva, f)
        if curva == "cer":
            cupon *= cer_factor
            amort *= cer_factor
        monto = cupon + amort
        if monto and monto > 0:
            out.append({
                "fecha": fd.isoformat(),
                "cupon": round(cupon, 6),
                "amort": round(amort, 6),
                "monto": round(monto, 6),
            })
    out.sort(key=lambda x: x["fecha"])
    return out, cer_proyectado


def _desglose_flujo(curva: str, f: dict) -> tuple[float, float]:
    """Devuelve `(cupon, amort)` por cada 100 VN para un flujo.

    - tasa_fija: `interes` / `amortizacion`. Zero coupon (Lecap) trae solo
      `monto` → todo capital al vto (cupon=0).
    - soberanos: `cupon_sobre_residual` ya viene en monto USD por 100 VN
      (ver monto_flujo_soberano); amortización = `amortizacion_pct`.
    - cer: % sobre VN; el factor CER lo aplica el caller.
    """
    if curva == "tasa_fija":
        if "monto" in f and "interes" not in f and "amortizacion" not in f:
            return 0.0, float(f["monto"])
        return float(f.get("interes", 0)), float(f.get("amortizacion", 0))
    if curva == "soberanos":
        cupon = float(f.get("cupon_sobre_residual", 0)) / 100 * 100
        amort = float(f.get("amortizacion_pct", 0)) / 100 * 100
        return cupon, amort
    if curva == "cer":
        amort = float(f.get("amortizacion_pct", 0)) / 100 * 100
        if "cupon_sobre_residual" in f:
            cupon = (
                float(f.get("cupon_sobre_residual", 0))
                * float(f.get("residual_previo_pct", 0)) / 100 * 100
            )
        else:
            cupon = float(f.get("cupon_anual", 0)) * 100
        return cupon, amort
    return 0.0, 0.0


def _convertir_mep(monto: float, desde: str, hasta: str, mep: float | None) -> float | None:
    """ARS→USD: monto/mep. USD→ARS: monto*mep. Same currency: pass-through."""
    if desde == hasta:
        return monto
    if not mep or mep <= 0:
        return None
    if desde == "ARS" and hasta == "USD":
        return monto / mep
    if desde == "USD" and hasta == "ARS":
        return monto * mep
    return None


def _bono_payload(
    bono: dict,
    flujos: list[dict],
    monto_input: float,
    moneda_input: str,
    mep: float | None,
) -> tuple[dict, list[str]]:
    """Construye el payload de un lado de la comparación.

    Devuelve `(payload, warnings_locales)`. Warnings:
    - `sin_precio_live` si no hay last_price para escalar VN.
    - `mep_faltante` si necesita convertir y no hay MEP.
    """
    warnings: list[str] = []
    curva = bono["_curva"]
    moneda_bono = _moneda_de(curva)
    precio = bono.get("ultimo_precio")

    monto_en_moneda_bono: float | None = monto_input
    if moneda_input != moneda_bono:
        monto_en_moneda_bono = _convertir_mep(
            monto_input, desde=moneda_input, hasta=moneda_bono, mep=mep,
        )
        if monto_en_moneda_bono is None:
            warnings.append("mep_faltante")

    vn_nominal: float | None = None
    flujos_escalados: list[dict] = []
    if precio and precio > 0 and monto_en_moneda_bono:
        vn_nominal = monto_en_moneda_bono * 100.0 / precio
        factor = vn_nominal / 100.0
        for f in flujos:
            flujos_escalados.append({
                "fecha": f["fecha"],
                "monto": round((f.get("monto") or 0) * factor, 2),
                "cupon": round((f.get("cupon") or 0) * factor, 2),
                "amort": round((f.get("amort") or 0) * factor, 2),
                "monto_por_100": f.get("monto"),
            })
    else:
        warnings.append("sin_precio_live")
        # Sin precio live no podemos escalar — devolvemos los flujos en VN 100
        # para que el cliente al menos los grafique relativos.
        flujos_escalados = [
            {
                "fecha": f["fecha"],
                "monto": f.get("monto"),
                "cupon": f.get("cupon"),
                "amort": f.get("amort"),
                "monto_por_100": f.get("monto"),
            }
            for f in flujos
        ]

    payload = {
        "id": f"curvas:{bono.get('ticker_corto')}",
        "ticker": bono.get("ticker"),
        "ticker_corto": bono.get("ticker_corto"),
        "label": bono.get("ticker_corto"),
        "curva": curva,
        "tipo": bono.get("tipo"),
        "moneda": moneda_bono,
        "vencimiento": bono.get("fecha_vencimiento"),
        "meses_al_vto": bono.get("meses_al_vto"),
        "cer_fijado": bono.get("cer_fijado", False),
        "is_zero_coupon": bono.get("is_zero_coupon"),
        "cer_emision": bono.get("cer_emision"),
        "metricas": {
            "ultimo_precio": precio,
            "tea": bono.get("tea"),
            "tem": bono.get("tem"),
            "paridad": bono.get("paridad"),
            "duration": bono.get("duration"),
            "mod_duration": bono.get("mod_duration"),
            "convexity": bono.get("convexity"),
            "tc_breakeven": bono.get("tc_breakeven"),
        },
        "monto_input": monto_input,
        "monto_efectivo": monto_en_moneda_bono,
        "vn_nominal": round(vn_nominal, 2) if vn_nominal else None,
        "flujos": flujos_escalados,
        "n_flujos": len(flujos_escalados),
    }
    return payload, warnings


@cached(ttl=10)
def comparar(a_id: str, b_id: str, monto: float, moneda_input: str = "ARS") -> dict:
    """Compara 2 bonos por id y escala los flujos al monto invertido.

    `moneda_input` define la moneda del input del usuario. Cada bono se paga
    en su propia moneda; si difiere de `moneda_input`, se convierte vía MEP
    live (mismo helper que usa `_tc_breakeven`).

    Warnings posibles (a nivel global, dedupe automático):
    - `cross_moneda` — A y B tienen monedas distintas.
    - `sin_precio_live` — alguno no tiene last_price → flujos sin escalar.
    - `mep_faltante` — no se pudo convertir el monto a moneda del bono.
    - `flujos_cer_sin_proyectar` — alguno es CER variable; el gráfico
      muestra solo flujos con CER de liquidación ya publicado.
    """
    moneda_input = (moneda_input or "ARS").upper()
    if moneda_input not in ("ARS", "USD"):
        moneda_input = "ARS"

    _, ticker_a = _parse_id(a_id)
    _, ticker_b = _parse_id(b_id)

    bono_a = _find_bono(ticker_a)
    bono_b = _find_bono(ticker_b)
    if not bono_a or not bono_b:
        faltantes = [tk for tk, b in ((ticker_a, bono_a), (ticker_b, bono_b)) if not b]
        return {"error": "bono_no_encontrado", "tickers": faltantes}

    flujos_a, cer_proy_a = _flujos_de(bono_a["_curva"], ticker_a)
    flujos_b, cer_proy_b = _flujos_de(bono_b["_curva"], ticker_b)

    # MEP solo se carga si lo necesitamos (alguna moneda difiere de la input).
    mep: float | None = None
    moneda_a = _moneda_de(bono_a["_curva"])
    moneda_b = _moneda_de(bono_b["_curva"])
    if moneda_input != moneda_a or moneda_input != moneda_b:
        from api.services.macro import get_ultimo_mep  # lazy: evita ciclo
        mep_doc = get_ultimo_mep()
        mep_raw = mep_doc.get("mep") if mep_doc else None
        if mep_raw and mep_raw > 0:
            mep = float(mep_raw)

    payload_a, w_a = _bono_payload(bono_a, flujos_a, monto, moneda_input, mep)
    payload_b, w_b = _bono_payload(bono_b, flujos_b, monto, moneda_input, mep)

    warnings = set(w_a) | set(w_b)
    if moneda_a != moneda_b:
        warnings.add("cross_moneda")
    # CER: si proyectamos con CER constante avisamos al cliente.
    if cer_proy_a or cer_proy_b:
        warnings.add("cer_proyectado_constante")

    return {
        "a": payload_a,
        "b": payload_b,
        "meta": {
            "monto_input": monto,
            "moneda_input": moneda_input,
            "mep_aplicado": mep,
            "warnings": sorted(warnings),
        },
    }
