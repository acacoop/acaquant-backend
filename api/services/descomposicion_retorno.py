"""descomposicion_retorno.py — Atribución carry / rolldown / cambio_tasa.

Solo Lecap y Boncap (tipo IN (lecap, boncap), curva=tasa_fija). El cálculo
limpio del PDF asume zero coupon, pesos puros y sin opciones — esos 3
tipos lo cumplen. Boncer / tasa fija con cupones quedan fuera porque
distorsionan el "carry puro".

Forma exacta del PDF:
  R_total              = precio_fin / precio_ini - 1
  Carry                = (1 + TEM_ini)^(días/30) - 1
  Flujo_final          = precio_ini × (1 + TEM_ini)^(vto_dias_ini/30)
  Precio_si_curva_ini  = Flujo_final / (1 + TEM_curva_ini(vto_dias_fin))^(vto_dias_fin/30)
  Rolldown             = (Precio_si_curva_ini / precio_ini - 1) - Carry
  Cambio_tasa          = R_total - Carry - Rolldown

`Cambio_tasa` es el residuo: lo que el movimiento real de la curva sumó/
restó al precio. La interpolación TEM_curva_ini(vto_dias_fin) usa el
método elegido (`lineal` default; `cuadratica` para regresión polinómica
sobre toda la curva, consistente con el Bloque 1.1 de residuos).

Dos puntos de entrada:
  - descomposicion_realizada(desde, hasta, metodo) → ex-post.
  - rolldown_esperado(horizonte_dias, metodo)      → ex-ante.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from api.cache import cached
from api.services.analitica import snapshot_curva_historico
from api.services.renta_fija import listar_curva

_TIPOS_LECAP = ("lecap", "boncap")
_METODOS = ("lineal", "cuadratica")


def _interpolar(
    puntos: list[tuple[float, float]], target: float, metodo: str,
) -> float | None:
    """Interpola TEM en función de vto_dias.

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


def _build_curva(
    snapshot: list[dict], fecha_referencia: date,
) -> list[tuple[float, float]]:
    """[{tipo, tem, fecha_vencimiento}] → [(vto_dias, tem)] sólo Lecap/Boncap vivos."""
    out: list[tuple[float, float]] = []
    for r in snapshot:
        if r.get("tipo") not in _TIPOS_LECAP:
            continue
        tem = r.get("tem")
        d = _vto_dias(r.get("fecha_vencimiento"), fecha_referencia)
        if tem is None or d is None or d <= 0:
            continue
        out.append((float(d), float(tem)))
    return out


def _descomponer_un_bono(
    *, precio_ini: float, tem_ini: float,
    vto_dias_ini: int, vto_dias_fin: int,
    precio_fin: float,
    curva_ini: list[tuple[float, float]],
    metodo: str,
) -> dict | None:
    """Núcleo del cálculo. None si los inputs no permiten una atribución limpia."""
    if precio_ini <= 0 or precio_fin <= 0:
        return None
    if not -0.99 < tem_ini < 5:
        return None
    dias_periodo = vto_dias_ini - vto_dias_fin
    if dias_periodo <= 0 or vto_dias_fin <= 0:
        return None

    r_total = precio_fin / precio_ini - 1
    carry = (1 + tem_ini) ** (dias_periodo / 30) - 1

    # Flujo único al vto del bono (constante en pesos, no depende de la curva).
    flujo_final = precio_ini * (1 + tem_ini) ** (vto_dias_ini / 30)

    tem_at_fin = _interpolar(curva_ini, vto_dias_fin, metodo)
    if tem_at_fin is None or tem_at_fin <= -0.99:
        return None

    precio_si_curva_ini = flujo_final / (1 + tem_at_fin) ** (vto_dias_fin / 30)
    r_si_curva_ini = precio_si_curva_ini / precio_ini - 1

    rolldown = r_si_curva_ini - carry
    cambio_tasa = r_total - carry - rolldown

    return {
        "r_total": round(r_total, 6),
        "carry": round(carry, 6),
        "rolldown": round(rolldown, 6),
        "cambio_tasa": round(cambio_tasa, 6),
        "tem_curva_ini_at_dias_fin": round(tem_at_fin, 6),
    }


def _agregado_simple(bonos: list[dict]) -> dict | None:
    if not bonos:
        return None
    n = len(bonos)
    return {
        k: round(sum(b[k] for b in bonos) / n, 6)
        for k in ("r_total", "carry", "rolldown", "cambio_tasa")
    }


@cached(ttl=300)
def descomposicion_realizada(
    desde: str, hasta: str, metodo: str = "lineal",
) -> dict:
    """Descomposición ex-post entre desde y hasta (YYYY-MM-DD).

    Devuelve por bono y un agregado promedio simple (no ponderado por
    pesos de portfolio — esa atribución vive en otro endpoint cuando se
    arme).
    """
    if metodo not in _METODOS:
        return {"error": f"metodo inválido (esperado uno de {_METODOS})"}
    try:
        f_ini = date.fromisoformat(desde)
        f_fin = date.fromisoformat(hasta)
    except ValueError:
        return {"error": "fechas en formato YYYY-MM-DD"}
    if f_fin <= f_ini:
        return {"error": "hasta debe ser > desde"}

    # @cached solo acepta kwargs — pasar posicional dispara TypeError.
    snap_ini = snapshot_curva_historico(curva="tasa_fija", fecha=desde)
    snap_fin = snapshot_curva_historico(curva="tasa_fija", fecha=hasta)
    if not snap_ini or not snap_fin:
        return {"error": "snapshots vacíos en una o ambas fechas (¿día no hábil?)"}

    curva_ini = _build_curva(snap_ini, f_ini)
    if len(curva_ini) < 2:
        return {"error": "curva inicial con menos de 2 Lecap/Boncap válidos"}

    by_ticker_fin = {
        r["ticker"]: r for r in snap_fin if r.get("tipo") in _TIPOS_LECAP
    }

    bonos: list[dict] = []
    for r_ini in snap_ini:
        if r_ini.get("tipo") not in _TIPOS_LECAP:
            continue
        ticker = r_ini.get("ticker")
        r_fin = by_ticker_fin.get(ticker)
        if r_fin is None:
            continue  # vencido durante el período o sin precio al final
        precio_ini = r_ini.get("ultimo_precio")
        precio_fin = r_fin.get("ultimo_precio")
        tem_ini = r_ini.get("tem")
        if precio_ini is None or precio_fin is None or tem_ini is None:
            continue
        d_ini = _vto_dias(r_ini.get("fecha_vencimiento"), f_ini)
        d_fin = _vto_dias(r_ini.get("fecha_vencimiento"), f_fin)
        if d_ini is None or d_fin is None:
            continue
        desc = _descomponer_un_bono(
            precio_ini=float(precio_ini), tem_ini=float(tem_ini),
            vto_dias_ini=d_ini, vto_dias_fin=d_fin,
            precio_fin=float(precio_fin),
            curva_ini=curva_ini, metodo=metodo,
        )
        if desc is None:
            continue
        bonos.append({
            "ticker": ticker,
            "ticker_corto": r_ini.get("ticker_corto"),
            "tipo": r_ini.get("tipo"),
            "fecha_vencimiento": r_ini.get("fecha_vencimiento"),
            "vto_dias_ini": d_ini,
            "vto_dias_fin": d_fin,
            "precio_ini": float(precio_ini),
            "precio_fin": float(precio_fin),
            "tem_ini": float(tem_ini),
            **desc,
        })

    bonos.sort(key=lambda b: b["r_total"], reverse=True)
    return {
        "desde": desde,
        "hasta": hasta,
        "dias": (f_fin - f_ini).days,
        "metodo": metodo,
        "bonos": bonos,
        "promedio_simple": _agregado_simple(bonos),
    }


@cached(ttl=120)
def rolldown_esperado(
    horizonte_dias: int = 30, metodo: str = "lineal",
) -> dict:
    """Atribución prospectiva: para cada Lecap/Boncap actual, qué rinde a
    horizonte_dias si la curva NO se mueve.

    Total esperado = carry + rolldown_esperado. Bonos que vencen antes del
    horizonte se descartan.
    """
    if metodo not in _METODOS:
        return {"error": f"metodo inválido (esperado uno de {_METODOS})"}
    if not 1 <= horizonte_dias <= 365:
        return {"error": "horizonte_dias entre 1 y 365"}

    curva_actual = listar_curva("tasa_fija")
    if not curva_actual:
        return {"error": "curva tasa_fija vacía"}

    hoy = date.today()
    pts: list[tuple[float, float]] = []
    bonos_input: list[dict] = []
    for r in curva_actual:
        if r.get("tipo") not in _TIPOS_LECAP:
            continue
        tem = r.get("tem")
        precio = r.get("ultimo_precio")
        d = _vto_dias(r.get("fecha_vencimiento"), hoy)
        if tem is None or precio is None or precio <= 0 or d is None or d <= 0:
            continue
        pts.append((float(d), float(tem)))
        bonos_input.append({
            "ticker": r["ticker"],
            "ticker_corto": r.get("ticker_corto"),
            "tipo": r.get("tipo"),
            "fecha_vencimiento": r.get("fecha_vencimiento"),
            "precio": float(precio),
            "tem": float(tem),
            "vto_dias": d,
        })

    if len(pts) < 2:
        return {"error": "curva actual con menos de 2 Lecap/Boncap válidos"}

    bonos: list[dict] = []
    for b in bonos_input:
        d_ini = b["vto_dias"]
        d_fin = d_ini - horizonte_dias
        if d_fin <= 0:
            continue
        flujo_final = b["precio"] * (1 + b["tem"]) ** (d_ini / 30)
        tem_at_fin = _interpolar(pts, d_fin, metodo)
        if tem_at_fin is None or tem_at_fin <= -0.99:
            continue
        precio_esperado = flujo_final / (1 + tem_at_fin) ** (d_fin / 30)
        r_esperado = precio_esperado / b["precio"] - 1
        carry = (1 + b["tem"]) ** (horizonte_dias / 30) - 1
        rolldown = r_esperado - carry
        bonos.append({
            **b,
            "vto_dias_horizonte": d_fin,
            "tem_curva_at_horizonte": round(tem_at_fin, 6),
            "carry_esperado": round(carry, 6),
            "rolldown_esperado": round(rolldown, 6),
            "total_esperado": round(r_esperado, 6),
        })

    bonos.sort(key=lambda b: b["total_esperado"], reverse=True)
    return {
        "horizonte_dias": horizonte_dias,
        "metodo": metodo,
        "fecha": hoy.isoformat(),
        "bonos": bonos,
    }
