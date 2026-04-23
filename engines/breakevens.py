"""
main_breakevens.py — Motor de breakevens CER/Lecap en tiempo real.

Cada 30s:
  1. Lee última TEM por Lecap y última paridad por CER desde Trading.TimeSales
  2. Empareja cada Lecap(M) con el CER más cercano a M+60 días (Trading.Curvas)
     — no al CER del mismo vto, por la convención del CER (settlement T-10
     hábiles + rezago de 1 mes en la publicación del IPC). El CER que vence
     en M captura inflación hasta ~M-2; para medir inflación hasta M se
     empareja con el CER(M+2).
  3. Calcula breakeven de inflación mensual implícita
  4. Upsert Trading.BreakevensLive  → 1 doc global (tiempo real)
  5. Upsert Trading.BreakevensHistorico → 1 doc por fecha (histórico diario)

Fórmulas:
  retorno_acumulado   = (1 + TEM)^(días/30) - 1
  inflacion_acumulada = (1 + retorno_acumulado) * (paridad/100) - 1
  breakeven_mensual   = (1 + inflacion_acumulada)^(30/días) - 1

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/main_breakevens.py
"""

import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

from core.mongo import get_mongo_client
from engines._curvas_loader import cargar_por_curva

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorBreakevens")

INTERVALO = 30               # segundos entre corridas
# Desfasaje CER por convención: el CER que vence en el mes M captura
# inflación hasta ~M-2 (settlement T-10 hábiles + IPC publicado con rezago
# de 1 mes). Para medir la inflación implícita "real" hasta el vto de la
# Lecap, emparejamos Lecap(M) con CER cercano a M+2 meses.
TARGET_GAP_DIAS   = 60        # distancia objetivo entre vto Lecap y vto CER (CER posterior)
TOLERANCIA_GAP_DIAS = 20      # ±20 días sobre el target (40-80d). Fuera de eso → sin par.
MIN_DIAS_PLAZO = 30           # días mínimos al vto desde hoy para incluir el par


# ─────────────────────────────────────────────
# Carga y emparejamiento de instrumentos
# ─────────────────────────────────────────────

def cargar_pares():
    """Devuelve lista de pares (Lecap, CER) ordenados por vto de la Lecap.

    Reglas (emparejamiento desfasado para corregir el rezago del CER):
      - Target: CER que vence a ~TARGET_GAP_DIAS después de la Lecap.
      - Se elige el CER cuyo gap real (fecha_cer − fecha_lecap, con signo)
        esté más cerca del TARGET_GAP_DIAS.
      - Solo se incluyen pares con |gap_real − TARGET| ≤ TOLERANCIA_GAP_DIAS.
        Si ninguno entra en la tolerancia → la Lecap queda sin par.
      - Si un mismo CER aparece como par de varias Lecaps, se queda solo con
        la que tiene mejor matching (menor distancia al target).
      - En cada par se guarda `gap_cer_dias` = días reales entre vto Lecap
        y vto CER (positivo = CER posterior), para que el frontend muestre
        cuán limpio salió el matching.
    """
    grupos = cargar_por_curva()
    lecaps = grupos.get("tasa_fija", [])
    cers   = grupos.get("cer", [])

    candidatos = []
    for lecap in lecaps:
        try:
            fecha_lec = date.fromisoformat(lecap["fecha_vencimiento"][:10])
        except Exception:
            continue

        mejor, mejor_dist_target, mejor_gap = None, None, None
        for cer in cers:
            try:
                fecha_cer = date.fromisoformat(cer["fecha_vencimiento"][:10])
            except Exception:
                continue
            gap = (fecha_cer - fecha_lec).days  # signed
            dist_target = abs(gap - TARGET_GAP_DIAS)
            if mejor_dist_target is None or dist_target < mejor_dist_target:
                mejor_dist_target = dist_target
                mejor = cer
                mejor_gap = gap

        if mejor is None or mejor_dist_target > TOLERANCIA_GAP_DIAS:
            continue

        candidatos.append({
            "lecap_ticker":      lecap["ticker"],
            "lecap_corto":       lecap["ticker_corto"],
            "cer_ticker":        mejor["ticker"],
            "cer_corto":         mejor["ticker_corto"],
            "fecha_vencimiento": lecap["fecha_vencimiento"][:10],
            "fecha_vto_cer":     mejor["fecha_vencimiento"][:10],
            "gap_cer_dias":      mejor_gap,
            "_dist_target":      mejor_dist_target,
        })

    # Dedup: si un CER aparece en varios pares, quedarnos con el que tiene
    # mejor matching (menor distancia al target).
    mejor_por_cer = {}
    for c in candidatos:
        key = c["cer_ticker"]
        if key not in mejor_por_cer or c["_dist_target"] < mejor_por_cer[key]["_dist_target"]:
            mejor_por_cer[key] = c

    pares = sorted(mejor_por_cer.values(), key=lambda p: p["fecha_vencimiento"])
    for p in pares:
        del p["_dist_target"]

    logger.info(
        "Pares CER/Lecap cargados: %d (target_gap=%dd, tolerancia=±%dd)",
        len(pares), TARGET_GAP_DIAS, TOLERANCIA_GAP_DIAS,
    )
    return pares


# ─────────────────────────────────────────────
# Lectura de mercado desde TimeSales
# ─────────────────────────────────────────────

def obtener_tems(client, tickers):
    """Última TEM por ticker Lecap."""
    pipeline = [
        {"$match": {"ticker": {"$in": tickers}, "TEM": {"$exists": True}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": "$ticker", "TEM": {"$first": "$TEM"}}},
    ]
    return {r["_id"]: r["TEM"] for r in client["Trading"]["TimeSales"].aggregate(pipeline)}


def obtener_paridades(client, tickers):
    """Última paridad por ticker CER."""
    pipeline = [
        {"$match": {"ticker": {"$in": tickers}, "paridad": {"$exists": True}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": "$ticker", "paridad": {"$first": "$paridad"}}},
    ]
    return {r["_id"]: r["paridad"] for r in client["Trading"]["TimeSales"].aggregate(pipeline)}


def obtener_teas_cer(client, tickers):
    """Última TEA por ticker CER (calculada por main_curvas.py)."""
    pipeline = [
        {"$match": {"ticker": {"$in": tickers}, "TEA": {"$exists": True}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": "$ticker", "TEA": {"$first": "$TEA"}}},
    ]
    return {r["_id"]: r["TEA"] for r in client["Trading"]["TimeSales"].aggregate(pipeline)}


# ─────────────────────────────────────────────
# Cálculo de breakevens
# ─────────────────────────────────────────────

def calcular_breakevens(pares, tems, paridades, teas_cer, fecha_ref):
    """
    fecha_ref: date — se usa para calcular días a vencimiento.
    Devuelve lista de dicts con los resultados.
    Descarta pares con menos de MIN_DIAS_PLAZO días al vencimiento.
    """
    resultado = []
    n = 0
    for par in pares:
        try:
            fecha_vto = date.fromisoformat(par["fecha_vencimiento"])
            dias = (fecha_vto - fecha_ref).days
        except Exception:
            continue

        if dias < MIN_DIAS_PLAZO:
            continue

        n += 1

        # Mes del IPC cuya inflación pricean estos breakevens. Convención:
        # el CER emparejado con vto +60 días está "publicado" (liquidación
        # ~vto_cer-10háb) usando el IPC del mes anterior, que es ~2 meses
        # antes del vto de la Lecap. Por eso el breakeven de una Lecap con
        # vto en 'M' representa la inflación implícita del IPC de 'M-2'.
        y = fecha_vto.year
        m = fecha_vto.month - 2
        if m <= 0:
            m += 12
            y -= 1
        mes_inflacion = f"{y:04d}-{m:02d}"

        entry = {
            "n":                 n,
            "lecap":             par["lecap_corto"],
            "cer":               par["cer_corto"],
            "fecha_vencimiento": par["fecha_vencimiento"],
            "fecha_vto_cer":     par.get("fecha_vto_cer"),
            "gap_cer_dias":      par.get("gap_cer_dias"),
            "mes_inflacion":     mes_inflacion,
            "dias":              dias,
        }

        tem     = tems.get(par["lecap_ticker"])
        paridad = paridades.get(par["cer_ticker"])
        tea_cer = teas_cer.get(par["cer_ticker"])

        if tem is not None:
            entry["tem_lecap"] = round(float(tem), 6)

        if tea_cer is not None:
            entry["tea_cer"] = round(float(tea_cer), 6)

        if paridad is not None:
            entry["paridad_cer"] = round(float(paridad), 4)

        if tem is not None and paridad is not None:
            try:
                retorno    = (1 + float(tem)) ** (dias / 30) - 1
                inflacion  = (1 + retorno) * (float(paridad) / 100) - 1
                bkv        = (1 + inflacion) ** (30 / dias) - 1
                if -0.5 < bkv < 10:
                    entry["retorno_acumulado"]   = round(retorno, 6)
                    entry["inflacion_acumulada"] = round(inflacion, 6)
                    entry["breakeven_mensual"]   = round(bkv, 6)
            except Exception:
                pass

        resultado.append(entry)

    return resultado


# ─────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────

def guardar(client, pares_result, ts, fecha_str):
    if not pares_result:
        return

    doc_base = {
        "updated_at": ts,
        "pares":      pares_result,
    }

    # BreakevensLive: 1 doc global, siempre pisado
    client["Trading"]["BreakevensLive"].update_one(
        {"_id": "breakevens"},
        {"$set": doc_base},
        upsert=True,
    )

    # BreakevensHistorico: 1 doc por fecha, actualizado durante el día
    client["Trading"]["BreakevensHistorico"].update_one(
        {"fecha": fecha_str},
        {"$set": {**doc_base, "fecha": fecha_str}},
        upsert=True,
    )


# ─────────────────────────────────────────────
# Loop principal
# ─────────────────────────────────────────────

def run():
    logger.info("Motor Breakevens iniciando...")
    client = get_mongo_client()

    pares         = cargar_pares()
    lecap_tickers = [p["lecap_ticker"] for p in pares]
    cer_tickers   = [p["cer_ticker"]   for p in pares]

    logger.info(f"Monitoreando {len(pares)} pares Lecap/CER.")

    while True:
        try:
            ts         = datetime.now(UTC)
            fecha_ref  = ts.date()
            fecha_str  = fecha_ref.isoformat()

            with ThreadPoolExecutor(max_workers=3) as ex:
                f_tems      = ex.submit(obtener_tems,      client, lecap_tickers)
                f_paridades = ex.submit(obtener_paridades, client, cer_tickers)
                f_teas      = ex.submit(obtener_teas_cer,  client, cer_tickers)
                tems, paridades, teas_cer = f_tems.result(), f_paridades.result(), f_teas.result()

            pares_result = calcular_breakevens(pares, tems, paridades, teas_cer, fecha_ref)
            guardar(client, pares_result, ts, fecha_str)

            n_completos = sum(1 for p in pares_result if "breakeven_mensual" in p)
            logger.info(f"{len(pares_result)} pares | {n_completos} con breakeven calculado.")

        except Exception:
            logger.error(f"Error en loop:\n{traceback.format_exc()}")

        time.sleep(INTERVALO)


if __name__ == "__main__":
    run()
