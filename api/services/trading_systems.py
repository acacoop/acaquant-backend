"""Lógica PURA del panel de Trading intradía de CEDEARs (vista /trading).

Sin I/O: recibe los crudos que ya devuelven los services de mercado
(`scanner_sql.get_cedears_scanner / get_pivot_points / get_quant_stats` +
`get_cedears_intraday`) y devuelve, por ticker:

  1. `derivar_campos(...)` → los ~10 campos derivados (pct_vs_vwap, pos_rango,
     gap_adr_pct, distancia a pivotes, zscore, spread, vwap_crosses, estado_OR,
     dia_volatil, ...).
  2. `evaluar_sistemas(campos)` → el semáforo de los 5 sistemas operativos
     (S1..S5) como INACTIVO / VIGILAR / ACTIVO + lado + nota.

Todo el cálculo es determinístico y testeable (tests/unit/test_trading_systems).
Los UMBRALES viven acá, en un solo lugar, para tunearlos con histórico real
después (REGLA: defaults primero, calibración por papel después).

Sin flujo agresor, sin osciladores. Solo precio, VWAP, niveles, gap vs ADR y
volumen — las únicas señales que el operador da por confiables.

NO persiste nada y NO necesita RVOL: S2 arranca con el fallback `dia_volatil`
(rango del día vs vol 30d anualizada). El RVOL fino es Fase 2 (tabla SQL +
job) — ver [[project_vista_trading]].
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

# ── Umbrales (defaults de la spec; calibrar por papel con histórico) ──────────
UMBRAL = {
    "s1_adr_dir_pct": 1.0,      # |adr_vs_1d_pct| > esto → dirección overnight clara
    "s1_ventana_min": 90,       # S1 solo evalúa en los primeros N min de rueda
    "or_window_min": 15,        # Opening Range = primeras N velas del día
    "toca_tol": 0.002,          # tolerancia para "toca" un pivote (0.2%)
    "s2_pos_alto": 90.0,        # posición en rango para extremo alto (short)
    "s2_pos_bajo": 10.0,        # posición en rango para extremo bajo (long)
    "s2_z": 2.0,                # |zscore| del último retorno para "estirado"
    "s2_retroceso_pct": 0.5,    # retroceso desde el extremo del día que gatilla
    "s3_crosses": 4,            # cruces de VWAP para "día lateral"
    "s3_spread_max": 0.30,      # spread_pct máximo para que el scalp cierre
    "s3_pos_compra": 20.0,      # piso del rango → ACTIVO compra
    "s3_pos_venta": 80.0,       # techo del rango → ACTIVO venta
    "s4_vigilar": 0.5,          # |gap_adr_pct| para VIGILAR dislocación
    "s4_activo": 0.8,           # |gap_adr_pct| para ACTIVO (catch-up / fade)
    "s5_crosses_max": 2,        # cruces de VWAP para "día tendencial"
    "dia_volatil_mult": 1.5,    # rango_dia_pct > esto × vol_diaria → día volátil
    "rvol_volatil": 1.5,        # RVOL > esto → día con volumen anormal (Fase 2)
    "climax_mult": 3.0,         # vela de clímax = vol > esto × media 10 velas
    "trading_days": 252,
}

_INACTIVO = "INACTIVO"
_VIGILAR = "VIGILAR"
_ACTIVO = "ACTIVO"


# ── helpers ───────────────────────────────────────────────────────────────────

def _f(x: Any) -> float | None:
    """Cast laxo a float; None/no-numérico → None."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _parse_iso(s: Any) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sistema(estado: str, lado: str | None = None, nota: str = "") -> dict[str, Any]:
    return {"estado": estado, "lado": lado, "nota": nota}


def _hhmm(t: Any) -> str | None:
    """Minuto-del-día UTC 'HH:MM' del timestamp de una vela ('...THH:MM:00Z')."""
    dt = _parse_iso(t)
    return dt.strftime("%H:%M") if dt else None


def _baseline_at(baseline: dict[str, float], minuto: str) -> float | None:
    """Volumen acumulado promedio del baseline en el minuto <= `minuto` más cercano."""
    best_k: str | None = None
    best_v: float | None = None
    for k, v in baseline.items():
        if k <= minuto and (best_k is None or k > best_k):
            best_k, best_v = k, v
    return best_v


def _rvol(candles: list[dict], baseline: dict[str, float] | None) -> float | None:
    """RVOL = volumen acumulado de hoy hasta el último minuto / promedio 20d a ese minuto."""
    if not baseline or not candles:
        return None
    cum = 0.0
    for c in candles:
        v = _f(c.get("vol"))
        if v:
            cum += v
    minuto = _hhmm(candles[-1].get("t"))
    if minuto is None:
        return None
    base = _baseline_at(baseline, minuto)
    if not base or base <= 0:
        return None
    return cum / base


# ── 1. campos derivados ────────────────────────────────────────────────────────

def _campos_velas(candles: list[dict], last: float | None) -> dict[str, Any]:
    """Deriva de las velas/minuto: serie VWAP + cruces, Opening Range y clímax.

    `candles` = list[{t, o, h, l, c, vol}] asc por minuto (como devuelve
    scanner_sql.get_cedears_intraday). Si está vacío → todo None.
    """
    out: dict[str, Any] = {
        "vwap_crosses": None,
        "estado_OR": None,
        "climax": None,
        "minutos_rueda": None,
        "n_velas": len(candles),
    }
    if not candles:
        return out

    # minutos transcurridos de rueda (proxy de cuán avanzada está la sesión)
    t0 = _parse_iso(candles[0].get("t"))
    tN = _parse_iso(candles[-1].get("t"))
    if t0 and tN:
        out["minutos_rueda"] = (tN - t0).total_seconds() / 60.0

    # serie VWAP reconstruida (typical-price ponderado por volumen, acumulado)
    cum_tv = 0.0
    cum_v = 0.0
    cruces = 0
    signo_prev: int | None = None
    for c in candles:
        h, l, cl, vol = _f(c.get("h")), _f(c.get("l")), _f(c.get("c")), _f(c.get("vol"))
        if cl is None:
            continue
        if h is None or l is None:
            h = l = cl
        typ = (h + l + cl) / 3.0
        if vol and vol > 0:
            cum_tv += typ * vol
            cum_v += vol
        vwap_i = (cum_tv / cum_v) if cum_v > 0 else cl
        signo = 1 if cl > vwap_i else (-1 if cl < vwap_i else 0)
        if signo != 0:
            if signo_prev is not None and signo != signo_prev:
                cruces += 1
            signo_prev = signo
    out["vwap_crosses"] = cruces

    # Opening Range: primeras `or_window_min` velas
    or_high = or_low = None
    if t0:
        win = UMBRAL["or_window_min"]
        for c in candles:
            t = _parse_iso(c.get("t"))
            if t is None or (t - t0).total_seconds() / 60.0 > win:
                break
            h, l = _f(c.get("h")), _f(c.get("l"))
            if h is not None:
                or_high = h if or_high is None else max(or_high, h)
            if l is not None:
                or_low = l if or_low is None else min(or_low, l)
    if last is not None and or_high is not None and or_low is not None:
        if last > or_high:
            out["estado_OR"] = "BREAK_UP"
        elif last < or_low:
            out["estado_OR"] = "BREAK_DOWN"
        else:
            out["estado_OR"] = "INSIDE"

    # clímax de volumen: última vela vs media de las 10 previas
    vols = [v for v in (_f(c.get("vol")) for c in candles) if v is not None]
    if len(vols) >= 2:
        prev = vols[-11:-1] if len(vols) > 10 else vols[:-1]
        if prev:
            media = sum(prev) / len(prev)
            out["climax"] = bool(media > 0 and vols[-1] > UMBRAL["climax_mult"] * media)

    return out


def _pivotes(pivots: dict | None, last: float | None) -> dict[str, Any]:
    """Distancia al pivote (R/S) inmediato y flags de toque, del frame DIARIO."""
    out: dict[str, Any] = {
        "dist_R_pct": None, "dist_S_pct": None,
        "toca_R2_R3": None, "toca_S2_S3": None,
        "pivots_diario": None,
    }
    if not pivots or last is None or last <= 0:
        return out
    frame = (pivots.get("frames") or {}).get("diario")
    if not frame:
        return out
    lv = frame.get("levels") or {}
    pp = _f(lv.get("pp"))
    r = [_f(lv.get(k)) for k in ("pp", "r1", "r2", "r3")]
    s = [_f(lv.get(k)) for k in ("pp", "s1", "s2", "s3")]
    r_arriba = [x for x in r if x is not None and x > last]
    s_abajo = [x for x in s if x is not None and x < last]
    if r_arriba:
        out["dist_R_pct"] = (min(r_arriba) - last) / last * 100
    if s_abajo:
        out["dist_S_pct"] = (last - max(s_abajo)) / last * 100
    r2, s2 = _f(lv.get("r2")), _f(lv.get("s2"))
    tol = UMBRAL["toca_tol"]
    if r2 is not None:
        out["toca_R2_R3"] = bool(last >= r2 * (1 - tol))
    if s2 is not None:
        out["toca_S2_S3"] = bool(last <= s2 * (1 + tol))
    out["pivots_diario"] = {"pp": pp, "r2": r2, "s2": s2}
    return out


def derivar_campos(
    scanner_row: dict,
    pivots: dict | None,
    stats: dict | None,
    candles: list[dict] | None,
    baseline: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Combina los crudos en el set de campos derivados por ticker.

    `baseline` (Fase 2, opcional) = {minuto 'HH:MM' UTC: vol_acum_promedio_20d} de
    `mercado.cedears_volume_history` → habilita el RVOL. Sin él, `dia_volatil` cae
    al fallback por rango (vol del día vs vol 30d).
    """
    sr = scanner_row or {}
    last = _f(sr.get("last"))
    vwap = _f(sr.get("vwap"))
    high = _f(sr.get("high"))
    low = _f(sr.get("low"))
    close_prev = _f(sr.get("close"))
    vs_1d_usd = _f(sr.get("vs_1d_usd_pct"))
    adr_vs_1d = _f(sr.get("adr_vs_1d_pct"))
    adr_ret_mtd = _f(sr.get("adr_ret_mtd_pct"))

    campos: dict[str, Any] = {
        "ticker": sr.get("ticker_corto"),
        "last": last,
        "vwap": vwap,
        "spread_pct": _f(sr.get("spread_pct")),
        "intraday_pct": _f(sr.get("intraday_pct")),
        "vs_1d_pct": _f(sr.get("vs_1d_pct")),
        "vs_1d_usd_pct": vs_1d_usd,
        "adr_vs_1d_pct": adr_vs_1d,
        "adr_ret_mtd_pct": adr_ret_mtd,
        "updated_at": sr.get("updated_at"),
    }

    # pct vs VWAP (espina de S1/S3/S5)
    campos["pct_vs_vwap"] = (
        (last - vwap) / vwap * 100 if (last is not None and vwap and vwap > 0) else None
    )

    # posición en el rango del día 0-100 (S2/S3)
    if last is not None and high is not None and low is not None:
        campos["pos_rango"] = 50.0 if high == low else (last - low) / (high - low) * 100
    else:
        campos["pos_rango"] = None

    # gap CEDEAR vs ADR (S4) — ambos vs cierre previo y en USD → comparables
    campos["gap_adr_pct"] = (
        adr_vs_1d - vs_1d_usd if (adr_vs_1d is not None and vs_1d_usd is not None) else None
    )

    # rango del día en % (para día volátil)
    campos["rango_dia_pct"] = (
        (high - low) / close_prev * 100
        if (high is not None and low is not None and close_prev and close_prev > 0)
        else None
    )

    # retroceso desde el extremo del día (S2)
    campos["retroceso_high_pct"] = (
        (high - last) / high * 100 if (high and last is not None and high > 0) else None
    )
    campos["retroceso_low_pct"] = (
        (last - low) / low * 100 if (low and last is not None and low > 0) else None
    )

    # zscore + vol diaria (S2)
    z = (stats or {}).get("zscore") or {}
    campos["zscore"] = _f(z.get("d30"))
    vol = (stats or {}).get("vol") or {}
    vol30 = _f(vol.get("d30"))
    campos["vol30d"] = vol30
    vol_diaria_pct = (
        vol30 / math.sqrt(UMBRAL["trading_days"]) * 100 if vol30 is not None else None
    )
    campos["vol_diaria_pct"] = vol_diaria_pct
    volatil_rango = bool(
        campos["rango_dia_pct"] is not None
        and vol_diaria_pct
        and campos["rango_dia_pct"] > UMBRAL["dia_volatil_mult"] * vol_diaria_pct
    )

    campos.update(_pivotes(pivots, last))
    campos.update(_campos_velas(candles or [], last))

    # RVOL (Fase 2): volumen acumulado de hoy vs promedio 20d al mismo minuto.
    rvol = _rvol(candles or [], baseline)
    campos["rvol"] = rvol
    # Día volátil = volumen anormal (RVOL) O el fallback por rango.
    campos["dia_volatil"] = bool(
        (rvol is not None and rvol > UMBRAL["rvol_volatil"]) or volatil_rango
    )
    return campos


# ── 2. semáforo de los 5 sistemas ──────────────────────────────────────────────

def _s1_apertura(c: dict) -> dict:
    """Continuación de apertura (direccional). Solo en los primeros 90' de rueda."""
    mins = c.get("minutos_rueda")
    if mins is None or mins > UMBRAL["s1_ventana_min"]:
        return _sistema(_INACTIVO, nota="fuera de la ventana de apertura")
    adr = c.get("adr_vs_1d_pct")
    pvw = c.get("pct_vs_vwap")
    orr = c.get("estado_OR")
    if adr is None or abs(adr) <= UMBRAL["s1_adr_dir_pct"]:
        return _sistema(_INACTIVO, nota="ADR sin dirección overnight clara")
    lado = "long" if adr > 0 else "short"
    if orr == "INSIDE":
        return _sistema(_VIGILAR, lado, "ADR con dirección; precio dentro del opening range")
    activo = (
        (adr > 0 and orr == "BREAK_UP" and pvw is not None and pvw > 0)
        or (adr < 0 and orr == "BREAK_DOWN" and pvw is not None and pvw < 0)
    )
    if activo:
        return _sistema(_ACTIVO, lado, "rompió el opening range a favor del ADR y del VWAP")
    return _sistema(_INACTIVO, nota="no confirma ruptura a favor")


def _s2_fade(c: dict) -> dict:
    """Fade de toma de ganancias (reversión). Requiere día volátil."""
    if not c.get("dia_volatil"):
        return _sistema(_INACTIVO, nota="el día no es volátil (sin fade)")
    pos = c.get("pos_rango")
    z = c.get("zscore")
    if pos is None or z is None:
        return _sistema(_INACTIVO, nota="faltan datos (pos. rango / zscore)")
    # extremo alto → candidato SHORT
    if pos > UMBRAL["s2_pos_alto"] and c.get("toca_R2_R3") and z > UMBRAL["s2_z"]:
        retro = c.get("retroceso_high_pct")
        if retro is not None and retro >= UMBRAL["s2_retroceso_pct"]:
            nota = "estirado arriba y empezó a retroceder"
            if c.get("climax"):
                nota += " (clímax de volumen)"
            return _sistema(_ACTIVO, "short", nota)
        return _sistema(_VIGILAR, "short", "estirado arriba; falta que retroceda")
    # extremo bajo → candidato LONG
    if pos < UMBRAL["s2_pos_bajo"] and c.get("toca_S2_S3") and z < -UMBRAL["s2_z"]:
        retro = c.get("retroceso_low_pct")
        if retro is not None and retro >= UMBRAL["s2_retroceso_pct"]:
            nota = "sobrevendido abajo y empezó a rebotar"
            if c.get("climax"):
                nota += " (clímax de volumen)"
            return _sistema(_ACTIVO, "long", nota)
        return _sistema(_VIGILAR, "long", "sobrevendido abajo; falta que rebote")
    return _sistema(_INACTIVO, nota="no está en un extremo estirado")


def _s3_scalp(c: dict) -> dict:
    """Scalp de vueltas en rango (días laterales)."""
    spread = c.get("spread_pct")
    if spread is not None and spread >= UMBRAL["s3_spread_max"]:
        return _sistema(_INACTIVO, nota="spread ancho: el scalp no cierra")
    crosses = c.get("vwap_crosses")
    pos = c.get("pos_rango")
    if crosses is None or crosses < UMBRAL["s3_crosses"]:
        return _sistema(_INACTIVO, nota="el día no es lateral (pocos cruces de VWAP)")
    if pos is not None and pos <= UMBRAL["s3_pos_compra"]:
        return _sistema(_ACTIVO, "long", "día lateral, precio en el piso del rango")
    if pos is not None and pos >= UMBRAL["s3_pos_venta"]:
        return _sistema(_ACTIVO, "short", "día lateral, precio en el techo del rango")
    return _sistema(_VIGILAR, None, "día lateral; esperar piso/techo del rango")


def _s4_dislocacion(c: dict) -> dict:
    """Dislocación CEDEAR vs ADR (relative value). Aritmética pura del gap."""
    gap = c.get("gap_adr_pct")
    if gap is None:
        return _sistema(_INACTIVO, nota="sin ADR para comparar")
    if gap > UMBRAL["s4_activo"]:
        return _sistema(_ACTIVO, "long", f"CEDEAR rezagado vs ADR ({gap:+.2f}%): catch-up")
    if gap < -UMBRAL["s4_activo"]:
        return _sistema(_ACTIVO, "short", f"CEDEAR sobre-reaccionó vs ADR ({gap:+.2f}%): fade")
    if abs(gap) > UMBRAL["s4_vigilar"]:
        lado = "long" if gap > 0 else "short"
        return _sistema(_VIGILAR, lado, f"dislocación incipiente ({gap:+.2f}%)")
    return _sistema(_INACTIVO, nota="CEDEAR y ADR alineados")


def _s5_holdeo(c: dict) -> dict:
    """Holdeo de momentum (gestión de tenencia). Sostener mientras la estructura aguante."""
    pvw = c.get("pct_vs_vwap")
    crosses = c.get("vwap_crosses")
    fondo = c.get("adr_ret_mtd_pct")
    if pvw is None:
        return _sistema(_INACTIVO, nota="sin VWAP")
    if pvw < 0:
        return _sistema(_VIGILAR, "long", "ALERTA: perdió el VWAP — evaluar salida")
    tendencial = crosses is not None and crosses <= UMBRAL["s5_crosses_max"]
    fondo_ok = fondo is not None and fondo > 0
    if tendencial and fondo_ok:
        return _sistema(_ACTIVO, "long", "arriba del VWAP, tendencial y con fondo de mes a favor")
    return _sistema(_VIGILAR, "long", "arriba del VWAP pero falta confirmación de tendencia/fondo")


def evaluar_sistemas(campos: dict) -> dict[str, dict]:
    """Resuelve el semáforo de los 5 sistemas a partir de los campos derivados."""
    return {
        "S1": _s1_apertura(campos),
        "S2": _s2_fade(campos),
        "S3": _s3_scalp(campos),
        "S4": _s4_dislocacion(campos),
        "S5": _s5_holdeo(campos),
    }
