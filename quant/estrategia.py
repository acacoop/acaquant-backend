"""quant/estrategia.py — motor de señal intradía ESTRATEGIA QUANT (funciones PURAS).

Doc vivo (LEER antes de tocar): docs/ESTRATEGIA_QUANT.md — el modelo, las
fórmulas de cada factor, los pesos y el changelog viven ahí. Todo cambio acá
se refleja en ese doc en el mismo commit.

Pregunta que responde: "¿el contexto favorece CONTINUACIÓN o REVERSAL en este
papel, y para qué lado?" — UNA señal (score -100..100), no un tablero.

4 factores, cada uno devuelve un aporte con signo en [-1, +1]
(+ = favorece LONG, − = favorece SHORT):

  F1 recorrido_indice  — cuánta "nafta" le queda al ÍNDICE de referencia
                          (QQQ/SPY): recorrido hasta el próximo pivote arriba
                          vs abajo + posición en el rango del día. El dolor
                          original: entrar largo cuando al índice no le queda
                          recorrido → reversal en contra.
  F2 alineacion        — ¿papel e índice están del MISMO lado de su PP?
                          Ponderado por la correlación diaria (papel-índice):
                          alineados con corr alta = continuación confiable;
                          divergentes con corr alta = alerta de reversal.
  F3 nafta_papel       — rango de HOY del papel vs su rango promedio (~20
                          ruedas, la "costumbre"). Si ya gastó su movida
                          típica, castiga PERSEGUIR la dirección actual.
  F4 confluencia       — zonas donde coinciden pivotes de varios timeframes
                          del SUBYACENTE USD (diario/semanal/mensual/anual):
                          soporte fuerte cerca abajo = piso (+), resistencia
                          fuerte cerca arriba = techo (−).

score = Σ w_i × f_i × 100, clamp [-100, 100]. Los pesos entran como PARÁMETRO
(vienen de estrategia.modelo_pesos, versionados) — la lógica no los conoce.

PURO: sin SQL, sin cache, sin FastAPI. Todo entra por argumentos → testeable
con casos fijos (tests/unit/test_estrategia.py).
"""
from __future__ import annotations

# Pesos v1 (hipótesis manual, hasta calibrar contra resultados reales).
# La fuente operativa es estrategia.modelo_pesos; esto es el seed/fallback.
PESOS_V1: dict[str, float] = {
    "recorrido_indice": 0.35,
    "alineacion": 0.30,
    "nafta_papel": 0.20,
    "confluencia": 0.15,
}
PESOS_V1_VERSION = "v1-manual"

# Saturación del % vs PP: a ±0.5% del PP el factor de alineación ya "carga" al
# máximo (los CEDEARs líquidos rara vez se alejan mucho más intradía).
_SAT_PCT_VS_PP = 0.5
# Zona de confluencia: dos niveles de timeframes distintos a menos de este %
# entre sí cuentan como UNA zona (más fuerte).
_TOL_CONFLUENCIA_PCT = 0.35
# Solo consideran presión las zonas a menos de este % del precio.
_ALCANCE_CONFLUENCIA_PCT = 1.5
# Umbral de "rango ya gastado" a partir del cual F3 castiga perseguir.
_NAFTA_UMBRAL = 0.8


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ──────────────────────────────────────────────────────────────────────────
# F1 — recorrido del índice
# ──────────────────────────────────────────────────────────────────────────
def factor_recorrido_indice(
    last: float,
    pivots: dict[str, float],
    high_dia: float | None,
    low_dia: float | None,
) -> float | None:
    """Nafta del índice: (recorrido a resistencia − recorrido a soporte),
    normalizado, combinado con la posición en el rango del día.

    - room: +1 = todo el recorrido está ARRIBA (alcista), −1 = todo abajo.
      Se toma el próximo nivel de pivote en cada dirección (r1..r3 / s1..s3,
      con pp haciendo de nivel también). Si el precio escapó del abanico
      (arriba de r3 / abajo de s3) el recorrido de ese lado se considera 0.
    - rango: +1 = está en el mínimo del día (le queda todo por subir),
      −1 = en el máximo. Si no hay high/low todavía, pesa 0.

    f1 = 0.6·room + 0.4·rango. None si faltan pivots o last inválido.
    """
    if not pivots or not last or last <= 0:
        return None
    niveles = sorted(
        v for k, v in pivots.items()
        if k in ("pp", "r1", "r2", "r3", "s1", "s2", "s3")
        and isinstance(v, (int, float)) and v > 0
    )
    if not niveles:
        return None
    arriba = [n for n in niveles if n > last]
    abajo = [n for n in niveles if n < last]
    dist_up = (min(arriba) / last - 1) * 100 if arriba else 0.0
    dist_dn = (1 - max(abajo) / last) * 100 if abajo else 0.0
    total = dist_up + dist_dn
    room = (dist_up - dist_dn) / total if total > 0 else 0.0

    rango = 0.0
    if high_dia and low_dia and high_dia > low_dia > 0:
        pos = (last - low_dia) / (high_dia - low_dia)  # 0 = mínimo, 1 = máximo
        rango = _clamp((0.5 - pos) * 2)

    return _clamp(0.6 * room + 0.4 * rango)


# ──────────────────────────────────────────────────────────────────────────
# F2 — alineación papel ↔ índice (ponderada por correlación)
# ──────────────────────────────────────────────────────────────────────────
def factor_alineacion(
    pct_vs_pp_papel: float | None,
    pct_vs_pp_indice: float | None,
    corr: float | None,
) -> float | None:
    """Confirmación del índice, ponderada por |corr|.

    s_p / s_i: % vs PP de papel/índice saturado a ±1 en ±_SAT_PCT_VS_PP.

    - Alineados (mismo lado del PP): el índice CONFIRMA → empuja en esa
      dirección: f2 = |corr| · promedio(s_p, s_i).
    - Divergentes (lados opuestos): el movimiento del papel NO está
      confirmado → posible reversal: castiga la dirección actual del papel:
      f2 = −|corr| · s_p.

    Sin corr (papel nuevo, sin historia) → None (el factor no opina).
    """
    if pct_vs_pp_papel is None or pct_vs_pp_indice is None or corr is None:
        return None
    c = min(1.0, abs(corr))
    s_p = _clamp(pct_vs_pp_papel / _SAT_PCT_VS_PP)
    s_i = _clamp(pct_vs_pp_indice / _SAT_PCT_VS_PP)
    if s_p == 0 or s_i == 0:
        return 0.0
    if (s_p > 0) == (s_i > 0):
        return _clamp(c * (s_p + s_i) / 2)
    return _clamp(-c * s_p)


# ──────────────────────────────────────────────────────────────────────────
# F3 — nafta del papel (rango de hoy vs su costumbre)
# ──────────────────────────────────────────────────────────────────────────
def factor_nafta_papel(
    rango_hoy_pct: float | None,
    rango_promedio_pct: float | None,
    direccion_actual: int,
) -> float | None:
    """¿Al papel le queda movida típica, o ya la hizo?

    uso = rango_hoy / rango_promedio (costumbre ~20 ruedas).
    - uso < _NAFTA_UMBRAL → todavía tiene nafta: aporte LEVE a favor de la
      dirección actual (máx +0.4).
    - uso ≥ _NAFTA_UMBRAL → castiga PERSEGUIR: aporte contra la dirección
      actual, creciendo hasta −1 en uso = 1.5.

    direccion_actual: +1 si el papel viene subiendo (last > PP), −1 bajando,
    0 indefinido (→ 0.0, el factor no empuja). None si falta la costumbre.
    """
    if rango_hoy_pct is None or not rango_promedio_pct or rango_promedio_pct <= 0:
        return None
    if direccion_actual == 0:
        return 0.0
    uso = rango_hoy_pct / rango_promedio_pct
    if uso < _NAFTA_UMBRAL:
        return _clamp(direccion_actual * (_NAFTA_UMBRAL - uso) * 0.5, -0.4, 0.4)
    return _clamp(-direccion_actual * (uso - _NAFTA_UMBRAL) / 0.7)


# ──────────────────────────────────────────────────────────────────────────
# F4 — confluencia multi-timeframe (subyacente USD)
# ──────────────────────────────────────────────────────────────────────────
def zonas_confluencia(
    frames: dict[str, dict[str, float]],
    tol_pct: float = _TOL_CONFLUENCIA_PCT,
) -> list[dict]:
    """Agrupa los niveles de los 4 timeframes en zonas: niveles a < tol_pct
    entre sí colapsan en una zona con fuerza = cantidad de niveles.

    frames: {timeframe: {pp, r1..r3, s1..s3}} (los que haya).
    Returns: [{precio (centro), fuerza, niveles: ['diario R1', ...]}] asc.
    """
    todos: list[tuple[float, str]] = []
    for tf, lv in (frames or {}).items():
        for k, v in (lv or {}).items():
            if k in ("pp", "r1", "r2", "r3", "s1", "s2", "s3") and v and v > 0:
                todos.append((float(v), f"{tf} {k.upper()}"))
    todos.sort()
    zonas: list[dict] = []
    for precio, etiqueta in todos:
        if zonas and (precio / zonas[-1]["precio"] - 1) * 100 < tol_pct:
            z = zonas[-1]
            z["niveles"].append(etiqueta)
            z["fuerza"] = len(z["niveles"])
            # centro = promedio incremental
            z["precio"] = z["precio"] + (precio - z["precio"]) / z["fuerza"]
        else:
            zonas.append({"precio": precio, "fuerza": 1, "niveles": [etiqueta]})
    return zonas


def factor_confluencia(last_usd: float | None, zonas: list[dict]) -> float | None:
    """Presión de las zonas de confluencia cercanas sobre el precio USD.

    Cada zona a < _ALCANCE_CONFLUENCIA_PCT del precio aporta
    fuerza × (1 − dist/alcance), con signo: soporte (abajo) empuja +,
    resistencia (arriba) frena −. Solo pesan de verdad las zonas con
    fuerza ≥ 2 (una sola coincidencia no es confluencia: aporta a mitad).
    Normalizado a [-1, 1] (satura con fuerza total 4).
    """
    if not last_usd or last_usd <= 0 or not zonas:
        return None
    presion = 0.0
    for z in zonas:
        dist_pct = abs(z["precio"] / last_usd - 1) * 100
        if dist_pct >= _ALCANCE_CONFLUENCIA_PCT:
            continue
        peso = z["fuerza"] * (1 - dist_pct / _ALCANCE_CONFLUENCIA_PCT)
        if z["fuerza"] < 2:
            peso *= 0.5
        presion += peso if z["precio"] < last_usd else -peso
    return _clamp(presion / 4.0)


# ──────────────────────────────────────────────────────────────────────────
# Score combinado
# ──────────────────────────────────────────────────────────────────────────
def score_estrategia(
    factores: dict[str, float | None],
    pesos: dict[str, float],
) -> dict:
    """Combina los factores en UN score -100..100.

    Los factores None (sin dato) NO promedian: su peso se redistribuye entre
    los presentes (el score no se diluye por datos faltantes, pero `cobertura`
    lo transparenta). Si no hay NINGÚN factor → score 0, cobertura 0.

    Returns:
        {score, direccion ('LONG'|'SHORT'|'NEUTRO' según signo — el umbral de
         emisión lo aplica el caller), cobertura (0-1: fracción del peso total
         con dato), factores (eco de los aportes)}
    """
    presentes = {k: v for k, v in factores.items() if v is not None and k in pesos}
    peso_total = sum(pesos[k] for k in presentes)
    if peso_total <= 0:
        return {"score": 0.0, "direccion": "NEUTRO", "cobertura": 0.0,
                "factores": dict(factores)}
    bruto = sum(pesos[k] * v for k, v in presentes.items()) / peso_total
    score = round(_clamp(bruto) * 100, 1)
    if score > 0:
        direccion = "LONG"
    elif score < 0:
        direccion = "SHORT"
    else:
        direccion = "NEUTRO"
    cobertura = round(sum(pesos[k] for k in presentes) / sum(pesos.values()), 2)
    return {"score": score, "direccion": direccion, "cobertura": cobertura,
            "factores": dict(factores)}
