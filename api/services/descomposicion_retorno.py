"""descomposicion_retorno.py — Atribución carry / rolldown / cambio_tasa.

Soporta dos curvas:

- **tasa_fija** (Lecap + Boncap, zero coupon, pesos): el precio sucio
  cotiza directo en pesos, no hay ajuste de indexación. Tasa de
  trabajo = TEM (mensual), capitalización base 30 días.

- **cer** (Lecers + Boncers cupón): el precio sucio cotiza ajustado
  por VN × CER. Para descomponer carry/rolldown limpios se trabaja
  con **paridad** (precio sucio neutralizado por el accrual del CER)
  y **TEA real**. El componente de indexación se calcula aparte
  (cer_accrual entre fechas, o REM forward) y se compone al final:

      R_ARS_total = (1 + R_paridad) × (1 + cer_accrual) − 1

  donde `R_paridad = carry + rolldown + cambio_tasa` se atribuye con
  el mismo método de revaluación exacta que tasa_fija. La curva de
  interpolación CER usa SOLO Lecers (zero coupon: cupon_anual = 0)
  para evitar que los TEAs comprimidos por cupones de TX26/TX28
  ensucien el rolldown de referencia.

Forma exacta común a las dos curvas (sustituí TEM↔TEA, freq 30↔365,
precio↔paridad según curva):

  R_total              = valor_fin / valor_ini - 1
  Carry                = (1 + tasa_ini)^(días/freq) - 1
  Flujo_final          = valor_ini × (1 + tasa_ini)^(vto_dias_ini/freq)
  Precio_si_curva_ini  = Flujo_final / (1 + tasa_curva_ini(vto_dias_fin))^(vto_dias_fin/freq)
  Rolldown             = (Precio_si_curva_ini / valor_ini - 1) - Carry
  Cambio_tasa          = R_total - Carry - Rolldown

Dos puntos de entrada (los dos aceptan curva ∈ {tasa_fija, cer}):
  - descomposicion_realizada(desde, hasta, metodo, curva) → ex-post.
  - rolldown_esperado(horizonte_dias, metodo, curva)      → ex-ante.

Para `curva="cer"`, el ex-post lee la serie del CER de Trading.CER
para calcular el accrual del período y el ex-ante usa la mediana del
REM (api/services/rem.py) para la proyección forward del IPC.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import numpy as np

from api.cache import cached
from api.db import get_db_trading
from api.services.analitica import snapshot_curva_historico
from api.services.renta_fija import listar_curva

logger = logging.getLogger(__name__)

_TIPOS_LECAP = ("lecap", "boncap")
_METODOS = ("lineal", "cuadratica")
_CURVAS_SOPORTADAS = ("tasa_fija", "cer")

# Config por curva: cómo se mapean los campos genéricos del cálculo
# (valor, tasa, frecuencia) a los datos reales del snapshot.
_CONFIG_CURVA: dict[str, dict[str, Any]] = {
    "tasa_fija": {
        "tasa_field": "tem",
        "freq_dias": 30,
        "valor_field": "ultimo_precio",   # precio sucio (cotiza en pesos)
    },
    "cer": {
        "tasa_field": "tea",              # TEA real (CER trabaja en términos reales)
        "freq_dias": 365,
        "valor_field": "paridad",         # precio neutralizado del accrual CER
    },
}


# ─────────────────────────────────────────────────────────────────────
# Helpers comunes
# ─────────────────────────────────────────────────────────────────────


def _interpolar(
    puntos: list[tuple[float, float]], target: float, metodo: str,
) -> float | None:
    """Interpola tasa en función de vto_dias.

    Lineal: entre los 2 vecinos más cercanos. Si target queda afuera, extrapola
    con los 2 puntos del extremo correspondiente.
    Cuadrática: regresión a + b·x + c·x² sobre TODOS los puntos. Requiere ≥3.
    """
    if len(puntos) < 2:
        return None
    pts = sorted(puntos, key=lambda p: p[0])
    if metodo == "lineal":
        if target <= pts[0][0]:
            (x1, y1), (x2, y2) = pts[0], pts[1]
        elif target >= pts[-1][0]:
            (x1, y1), (x2, y2) = pts[-2], pts[-1]
        else:
            (x1, y1), (x2, y2) = pts[0], pts[1]
            for i in range(len(pts) - 1):
                if pts[i][0] <= target <= pts[i + 1][0]:
                    (x1, y1), (x2, y2) = pts[i], pts[i + 1]
                    break
        if x2 == x1:
            return y1
        return y1 + (y2 - y1) * (target - x1) / (x2 - x1)
    if metodo == "cuadratica":
        if len(pts) < 3:
            return None
        xs = np.array([p[0] for p in pts], dtype=float)
        ys = np.array([p[1] for p in pts], dtype=float)
        coefs = np.polyfit(xs, ys, deg=2)
        return float(np.polyval(coefs, target))
    return None


def _vto_dias(fecha_vto: str | None, fecha_referencia: date) -> int | None:
    if not fecha_vto:
        return None
    try:
        vto = date.fromisoformat(str(fecha_vto)[:10])
    except ValueError:
        return None
    return (vto - fecha_referencia).days


def _es_universo_interpolacion(r: dict, curva: str) -> bool:
    """¿Este bono entra en la curva de referencia para interpolar?

    - tasa_fija: solo Lecap y Boncap (zero coupon, pesos). Resto no aporta.
    - cer: solo Lecers (cupon_anual = 0 → is_zero_coupon=True). Boncers
      cupón tipo TX26/TX28 se excluyen porque sus TEAs comprimidas por
      cupones desplazan la curva.
    """
    if curva == "tasa_fija":
        return r.get("tipo") in _TIPOS_LECAP
    if curva == "cer":
        return bool(r.get("is_zero_coupon"))
    return False


def _build_curva(
    snapshot: list[dict], fecha_referencia: date, curva: str,
) -> list[tuple[float, float]]:
    """[snapshot row] → [(vto_dias, tasa)] para los bonos del universo de
    interpolación, con la tasa que corresponda a la curva (tem o tea)."""
    cfg = _CONFIG_CURVA[curva]
    tasa_field = cfg["tasa_field"]
    out: list[tuple[float, float]] = []
    for r in snapshot:
        if not _es_universo_interpolacion(r, curva):
            continue
        tasa = r.get(tasa_field)
        d = _vto_dias(r.get("fecha_vencimiento"), fecha_referencia)
        if tasa is None or d is None or d <= 0:
            continue
        out.append((float(d), float(tasa)))
    return out


def _descomponer_un_bono(
    *, valor_ini: float, valor_fin: float, tasa_ini: float,
    vto_dias_ini: int, vto_dias_fin: int,
    curva_ini: list[tuple[float, float]],
    metodo: str, freq_dias: int,
) -> dict | None:
    """Núcleo del cálculo. None si los inputs no permiten una atribución limpia.

    Generalizado para soportar tasa_fija (valor=precio_sucio, tasa=TEM,
    freq=30) y cer (valor=paridad, tasa=TEA, freq=365).
    """
    if valor_ini <= 0 or valor_fin <= 0:
        return None
    if not -0.99 < tasa_ini < 5:
        return None
    dias_periodo = vto_dias_ini - vto_dias_fin
    if dias_periodo <= 0 or vto_dias_fin <= 0:
        return None

    r_total = valor_fin / valor_ini - 1
    carry = (1 + tasa_ini) ** (dias_periodo / freq_dias) - 1

    flujo_final = valor_ini * (1 + tasa_ini) ** (vto_dias_ini / freq_dias)

    tasa_at_fin = _interpolar(curva_ini, vto_dias_fin, metodo)
    if tasa_at_fin is None or tasa_at_fin <= -0.99:
        return None

    valor_si_curva_ini = flujo_final / (1 + tasa_at_fin) ** (vto_dias_fin / freq_dias)
    r_si_curva_ini = valor_si_curva_ini / valor_ini - 1

    rolldown = r_si_curva_ini - carry
    cambio_tasa = r_total - carry - rolldown

    return {
        "r_total": round(r_total, 6),
        "carry": round(carry, 6),
        "rolldown": round(rolldown, 6),
        "cambio_tasa": round(cambio_tasa, 6),
        "tasa_curva_ini_at_dias_fin": round(tasa_at_fin, 6),
    }


def _agregado_simple(bonos: list[dict], extras: tuple[str, ...] = ()) -> dict | None:
    if not bonos:
        return None
    base = ("r_total", "carry", "rolldown", "cambio_tasa", *extras)
    out = {}
    for k in base:
        vals = [b[k] for b in bonos if b.get(k) is not None]
        if vals:
            out[k] = round(sum(vals) / len(vals), 6)
    return out


# ─────────────────────────────────────────────────────────────────────
# CER-specific helpers
# ─────────────────────────────────────────────────────────────────────


def _cer_factor_periodo(desde: date, hasta: date) -> tuple[float | None, dict]:
    """Lee Trading.CER y devuelve (factor, debug) donde factor = CER_fin/CER_ini - 1.

    Si no hay valores en una de las fechas, busca el más cercano <= esa fecha
    (el CER tiene lag T-10 hábiles; cualquier fecha hábil del backend está OK).
    """
    db = get_db_trading()
    desde_str = desde.isoformat()
    hasta_str = hasta.isoformat()
    cer_ini_doc = db["CER"].find_one(
        {"fecha": {"$lte": desde_str}, "valor": {"$gt": 0}},
        {"_id": 0, "fecha": 1, "valor": 1},
        sort=[("fecha", -1)],
    )
    cer_fin_doc = db["CER"].find_one(
        {"fecha": {"$lte": hasta_str}, "valor": {"$gt": 0}},
        {"_id": 0, "fecha": 1, "valor": 1},
        sort=[("fecha", -1)],
    )
    if not cer_ini_doc or not cer_fin_doc:
        return None, {"error": "Trading.CER sin valores en el período"}
    cer_ini = float(cer_ini_doc["valor"])
    cer_fin = float(cer_fin_doc["valor"])
    if cer_ini <= 0:
        return None, {"error": "CER inicial = 0"}
    accrual = cer_fin / cer_ini - 1
    return round(accrual, 6), {
        "cer_ini": cer_ini,
        "cer_fin": cer_fin,
        "fecha_cer_ini": cer_ini_doc["fecha"],
        "fecha_cer_fin": cer_fin_doc["fecha"],
    }


def _cer_accrual_forward(horizonte_dias: int) -> tuple[float | None, dict]:
    """Estima el accrual del CER para los próximos `horizonte_dias` usando
    la mediana de inflación del REM. Cada mes del horizonte se compoundea
    con la mediana de IPC mensual (REM expectativas).

    Devuelve (accrual, debug). debug incluye cuántos meses uso y la mediana
    aplicada, para que el panel de auditoría muestre el cálculo.
    """
    try:
        from api.services.rem import get_rem_expectativas
    except Exception:
        return None, {"error": "rem service no disponible"}

    try:
        rem = get_rem_expectativas()
    except Exception as e:
        return None, {"error": f"rem fallo: {e}"}

    # rem.expectativas devuelve algo como [{periodo, mediana, ...}, ...]
    # con periodo = "YYYY-MM" o ISO. Tomamos los próximos N meses.
    n_meses = max(1, round(horizonte_dias / 30))
    expects = rem.get("expectativas") if isinstance(rem, dict) else rem
    if not isinstance(expects, list) or not expects:
        return None, {"error": "rem sin expectativas"}

    medianas: list[float] = []
    for e in expects[:n_meses]:
        m = e.get("mediana") if isinstance(e, dict) else None
        if m is None:
            continue
        # Convertir a decimal si vino en %.
        try:
            mf = float(m)
        except (TypeError, ValueError):
            continue
        if mf > 1:
            mf = mf / 100.0
        medianas.append(mf)

    if not medianas:
        return None, {"error": "rem sin medianas válidas"}

    factor = 1.0
    for m in medianas:
        factor *= (1 + m)
    accrual = factor - 1
    return round(accrual, 6), {
        "n_meses_compoundeados": len(medianas),
        "medianas_mensuales": [round(m, 6) for m in medianas],
        "fuente": "rem.expectativas (mediana)",
    }


# ─────────────────────────────────────────────────────────────────────
# Entry points
# ─────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def descomposicion_realizada(
    desde: str, hasta: str,
    metodo: str = "lineal",
    curva: str = "tasa_fija",
) -> dict:
    """Descomposición ex-post entre desde y hasta (YYYY-MM-DD).

    Para `curva="tasa_fija"` (default por back-compat): atribución sobre
    precio sucio + TEM (sin componente de indexación).
    Para `curva="cer"`: atribución sobre paridad + TEA real, más
    `cer_accrual` del período (de Trading.CER) y `r_total_ars` compuesto.
    """
    if metodo not in _METODOS:
        return {"error": f"metodo inválido (esperado uno de {_METODOS})"}
    if curva not in _CURVAS_SOPORTADAS:
        return {"error": f"curva inválida (esperado uno de {_CURVAS_SOPORTADAS})"}
    try:
        f_ini = date.fromisoformat(desde)
        f_fin = date.fromisoformat(hasta)
    except ValueError:
        return {"error": "fechas en formato YYYY-MM-DD"}
    if f_fin <= f_ini:
        return {"error": "hasta debe ser > desde"}

    cfg = _CONFIG_CURVA[curva]
    tasa_field = cfg["tasa_field"]
    valor_field = cfg["valor_field"]
    freq_dias = cfg["freq_dias"]

    snap_ini = snapshot_curva_historico(curva=curva, fecha=desde)
    snap_fin = snapshot_curva_historico(curva=curva, fecha=hasta)
    if not snap_ini or not snap_fin:
        return {"error": "snapshots vacíos en una o ambas fechas (¿día no hábil?)"}

    curva_ini = _build_curva(snap_ini, f_ini, curva)
    if len(curva_ini) < 2:
        return {"error": f"curva {curva} inicial con menos de 2 puntos válidos"}

    by_ticker_fin = {r["ticker"]: r for r in snap_fin}

    # CER accrual del período (común a todos los CER del listado).
    cer_accrual: float | None = None
    cer_debug: dict = {}
    if curva == "cer":
        cer_accrual, cer_debug = _cer_factor_periodo(f_ini, f_fin)

    bonos: list[dict] = []
    for r_ini in snap_ini:
        ticker = r_ini.get("ticker")
        # En CER incluimos en el output a Lecers + Boncers cupón (los con
        # cupón sí los descomponemos, solo los excluimos del universo de
        # interpolación). Filtramos universo elegible:
        if curva == "tasa_fija" and r_ini.get("tipo") not in _TIPOS_LECAP:
            continue
        # En CER no hay filtro extra acá — si tiene tasa, va.

        r_fin = by_ticker_fin.get(ticker)
        if r_fin is None:
            continue
        valor_ini = r_ini.get(valor_field)
        valor_fin = r_fin.get(valor_field)
        tasa_ini = r_ini.get(tasa_field)
        if valor_ini is None or valor_fin is None or tasa_ini is None:
            logger.debug(
                "descomp %s: %s sin valor/tasa (skip)", curva, ticker,
            )
            continue
        d_ini = _vto_dias(r_ini.get("fecha_vencimiento"), f_ini)
        d_fin = _vto_dias(r_ini.get("fecha_vencimiento"), f_fin)
        if d_ini is None or d_fin is None:
            continue
        desc = _descomponer_un_bono(
            valor_ini=float(valor_ini), valor_fin=float(valor_fin),
            tasa_ini=float(tasa_ini),
            vto_dias_ini=d_ini, vto_dias_fin=d_fin,
            curva_ini=curva_ini, metodo=metodo, freq_dias=freq_dias,
        )
        if desc is None:
            continue
        entry = {
            "ticker": ticker,
            "ticker_corto": r_ini.get("ticker_corto"),
            "tipo": r_ini.get("tipo"),
            "fecha_vencimiento": r_ini.get("fecha_vencimiento"),
            "vto_dias_ini": d_ini,
            "vto_dias_fin": d_fin,
            "precio_ini": float(r_ini.get("ultimo_precio") or 0) or None,
            "precio_fin": float(r_fin.get("ultimo_precio") or 0) or None,
            "valor_ini": float(valor_ini),
            "valor_fin": float(valor_fin),
            "tasa_ini": float(tasa_ini),
            **desc,
        }
        # Para CER sumamos el accrual y el retorno total ARS.
        if curva == "cer":
            entry["cer_accrual"] = cer_accrual
            entry["is_zero_coupon"] = bool(r_ini.get("is_zero_coupon"))
            if cer_accrual is not None:
                r_total_ars = (1 + desc["r_total"]) * (1 + cer_accrual) - 1
                entry["r_total_ars"] = round(r_total_ars, 6)
        bonos.append(entry)

    sort_key = "r_total_ars" if curva == "cer" else "r_total"
    bonos.sort(key=lambda b: b.get(sort_key) or b.get("r_total", 0), reverse=True)

    extras_agg = ("r_total_ars", "cer_accrual") if curva == "cer" else ()
    out = {
        "desde": desde,
        "hasta": hasta,
        "dias": (f_fin - f_ini).days,
        "metodo": metodo,
        "curva": curva,
        "bonos": bonos,
        "promedio_simple": _agregado_simple(bonos, extras=extras_agg),
    }
    if curva == "cer":
        out["cer_accrual_periodo"] = cer_accrual
        out["cer_debug"] = cer_debug
    return out


@cached(ttl=120)
def rolldown_esperado(
    horizonte_dias: int = 30,
    metodo: str = "lineal",
    curva: str = "tasa_fija",
) -> dict:
    """Atribución prospectiva: para cada bono actual, qué rinde a horizonte
    si la curva NO se mueve.

    tasa_fija: total = carry + rolldown (sobre precio sucio y TEM).
    cer: total = carry + rolldown (sobre paridad y TEA real). Sumamos
         además `cer_accrual_esperado` (del REM) y el retorno total ARS:
         R_ars = (1 + carry + rolldown) × (1 + cer_accrual) − 1.
    """
    if metodo not in _METODOS:
        return {"error": f"metodo inválido (esperado uno de {_METODOS})"}
    if curva not in _CURVAS_SOPORTADAS:
        return {"error": f"curva inválida (esperado uno de {_CURVAS_SOPORTADAS})"}
    if not 1 <= horizonte_dias <= 365:
        return {"error": "horizonte_dias entre 1 y 365"}

    cfg = _CONFIG_CURVA[curva]
    tasa_field = cfg["tasa_field"]
    valor_field = cfg["valor_field"]
    freq_dias = cfg["freq_dias"]

    curva_actual = listar_curva(curva)
    if not curva_actual:
        return {"error": f"curva {curva} vacía"}

    hoy = date.today()

    # Curva de referencia para interpolación: sólo bonos del universo
    # válido (tasa_fija → lecap/boncap; cer → zero coupon).
    pts: list[tuple[float, float]] = []
    for r in curva_actual:
        if not _es_universo_interpolacion(r, curva):
            continue
        tasa = r.get(tasa_field)
        d = _vto_dias(r.get("fecha_vencimiento"), hoy)
        if tasa is None or d is None or d <= 0:
            continue
        pts.append((float(d), float(tasa)))

    if len(pts) < 2:
        return {"error": f"curva {curva} con menos de 2 puntos zero coupon"}

    # CER accrual forward (común a todos los CER del listado).
    cer_accrual_esp: float | None = None
    cer_debug: dict = {}
    if curva == "cer":
        cer_accrual_esp, cer_debug = _cer_accrual_forward(horizonte_dias)

    # Bonos del universo de cálculo: en tasa_fija solo lecap/boncap; en
    # cer incluimos TODOS (zero coupon + cupón) — descomponemos a todos
    # contra la curva de referencia (que es solo de Lecers).
    bonos: list[dict] = []
    for r in curva_actual:
        if curva == "tasa_fija" and r.get("tipo") not in _TIPOS_LECAP:
            continue
        valor = r.get(valor_field)
        tasa = r.get(tasa_field)
        d_ini = _vto_dias(r.get("fecha_vencimiento"), hoy)
        if valor is None or tasa is None or d_ini is None or d_ini <= 0:
            continue
        if float(valor) <= 0:
            continue
        d_fin = d_ini - horizonte_dias
        if d_fin <= 0:
            continue
        flujo_final = float(valor) * (1 + float(tasa)) ** (d_ini / freq_dias)
        tasa_at_fin = _interpolar(pts, d_fin, metodo)
        if tasa_at_fin is None or tasa_at_fin <= -0.99:
            continue
        valor_esperado = flujo_final / (1 + tasa_at_fin) ** (d_fin / freq_dias)
        r_esperado = valor_esperado / float(valor) - 1
        carry = (1 + float(tasa)) ** (horizonte_dias / freq_dias) - 1
        rolldown = r_esperado - carry
        entry = {
            "ticker": r["ticker"],
            "ticker_corto": r.get("ticker_corto"),
            "tipo": r.get("tipo"),
            "fecha_vencimiento": r.get("fecha_vencimiento"),
            "precio": float(r.get("ultimo_precio") or 0) or None,
            "valor": float(valor),
            "tasa": float(tasa),
            "vto_dias": d_ini,
            "vto_dias_horizonte": d_fin,
            "tasa_curva_at_horizonte": round(tasa_at_fin, 6),
            "carry_esperado": round(carry, 6),
            "rolldown_esperado": round(rolldown, 6),
            "total_esperado": round(r_esperado, 6),
        }
        if curva == "cer":
            entry["is_zero_coupon"] = bool(r.get("is_zero_coupon"))
            if cer_accrual_esp is not None:
                total_ars = (1 + r_esperado) * (1 + cer_accrual_esp) - 1
                entry["cer_accrual_esperado"] = cer_accrual_esp
                entry["total_esperado_ars"] = round(total_ars, 6)
        bonos.append(entry)

    sort_key = "total_esperado_ars" if curva == "cer" else "total_esperado"
    bonos.sort(key=lambda b: b.get(sort_key) or b.get("total_esperado", 0), reverse=True)

    out = {
        "horizonte_dias": horizonte_dias,
        "metodo": metodo,
        "curva": curva,
        "fecha": hoy.isoformat(),
        "bonos": bonos,
    }
    if curva == "cer":
        out["cer_accrual_esperado"] = cer_accrual_esp
        out["cer_debug"] = cer_debug
    return out


# Backwards-compat: el código viejo importaba estos símbolos.
__all__ = [
    "_build_curva",
    "_descomponer_un_bono",
    "_interpolar",
    "descomposicion_realizada",
    "rolldown_esperado",
]
