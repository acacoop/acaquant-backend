"""intraday.py — métricas intradía sobre series de precios por minuto.

Funciones puras (sin Mongo, sin cache, sin FastAPI). Alimentan el TRADE LAB
intradía (api/services/day_trading.py): cuántas "vueltas" del tamaño objetivo
hizo un papel hoy, momentum reciente, posición dentro del rango del día.

Concepto "vuelta" (zigzag): un movimiento COMPLETO de al menos `umbral_pct`
en una dirección, medido pivote → extremo. Cuando el precio revierte desde el
extremo en al menos `umbral_pct`, la pata se da por terminada y se cuenta.
Es la métrica natural del scalper: "¿cuántas veces HOY este papel ya pagó el
movimiento que busco?" — sin importar si fue para arriba o para abajo (long
y short valen igual).
"""
from __future__ import annotations


def contar_vueltas(closes: list[float], umbral_pct: float) -> tuple[int, float]:
    """Cuenta las patas zigzag ≥ umbral_pct sobre la serie de cierres.

    Args:
        closes: cierres por minuto (orden cronológico). Se ignoran None/<=0.
        umbral_pct: tamaño mínimo de la pata en % (ej. 0.5).

    Returns:
        (n_vueltas, mejor_pct):
            n_vueltas — patas completadas ≥ umbral (la última pata en curso
                cuenta si ya superó el umbral, aunque no haya revertido).
            mejor_pct — la pata más grande del día en % (0.0 si no hubo).
    """
    serie = [c for c in closes if c is not None and c > 0]
    if len(serie) < 2 or umbral_pct <= 0:
        return 0, 0.0

    n = 0
    mejor = 0.0
    pivote = serie[0]   # inicio de la pata actual
    extremo = serie[0]  # máximo (si sube) o mínimo (si baja) de la pata
    dir_: int = 0       # 0 indefinido, +1 subiendo, -1 bajando

    for c in serie[1:]:
        if dir_ == 0:
            # Sin dirección todavía: esperar el primer movimiento ≥ umbral.
            if (c - pivote) / pivote * 100 >= umbral_pct:
                dir_, extremo = 1, c
            elif (pivote - c) / pivote * 100 >= umbral_pct:
                dir_, extremo = -1, c
        elif dir_ == 1:
            if c > extremo:
                extremo = c
            elif (extremo - c) / extremo * 100 >= umbral_pct:
                # Reversión ≥ umbral → la pata alcista terminó en `extremo`.
                pata = (extremo - pivote) / pivote * 100
                n += 1
                mejor = max(mejor, pata)
                pivote, extremo, dir_ = extremo, c, -1
        else:  # dir_ == -1
            if c < extremo:
                extremo = c
            elif (c - extremo) / extremo * 100 >= umbral_pct:
                pata = (pivote - extremo) / pivote * 100
                n += 1
                mejor = max(mejor, pata)
                pivote, extremo, dir_ = extremo, c, 1

    # Pata en curso: si ya superó el umbral, cuenta (es una vuelta "viva").
    if dir_ != 0:
        pata = abs(extremo - pivote) / pivote * 100
        if pata >= umbral_pct:
            n += 1
            mejor = max(mejor, pata)

    return n, round(mejor, 2)


def momentum_pct(closes: list[float], minutos: int) -> float | None:
    """Retorno % entre el último cierre y el de hace `minutos` barras.

    Asume barras de 1 minuto consecutivas (suficiente para el lab — los
    huecos sin trades acortan la ventana efectiva, no la alargan).
    """
    serie = [c for c in closes if c is not None and c > 0]
    if len(serie) < 2 or minutos <= 0:
        return None
    base = serie[-minutos - 1] if len(serie) > minutos else serie[0]
    if base <= 0:
        return None
    return round((serie[-1] / base - 1) * 100, 2)


def posicion_en_rango(last: float, low: float, high: float) -> float | None:
    """Dónde está `last` dentro del rango [low, high] del día, en 0-100.

    0 = en el mínimo del día (piso) · 100 = en el máximo (techo).
    None si el rango es degenerado.
    """
    if low is None or high is None or last is None:
        return None
    if high <= low or last <= 0:
        return None
    pos = (last - low) / (high - low) * 100
    return round(min(max(pos, 0.0), 100.0), 1)
