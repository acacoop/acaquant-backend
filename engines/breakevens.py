"""
main_breakevens.py — Motor de breakevens CER/Lecap en tiempo real.

Cada 30s:
  1. Lee última TEM por Lecap/Boncap y última paridad por CER desde TimeSales
  2. Empareja cada bono tasa_fija con el CER cuyo vto es más cercano
     (Trading.Curvas). Lecap/Boncap con vto ~M ↔ CER con vto ~M.
  3. Calcula breakeven de inflación mensual implícita entre HOY y el vto.
  4. Upsert Trading.BreakevensLive  → 1 doc global (tiempo real)
  5. Upsert Trading.BreakevensHistorico → 1 doc por fecha (histórico diario)

Fórmulas:
  retorno_acumulado   = (1 + TEM)^(días/30) - 1
  inflacion_acumulada = (1 + retorno_acumulado) * (paridad/100) - 1
  breakeven_mensual   = (1 + inflacion_acumulada)^(30/días) - 1

Interpretación del resultado: el BE mensual de una Lecap con vto en mes M
es la inflación mensual implícita que pricea el mercado para el IPC del
mes M−2 (por rezago del CER: settlement T-10 hábiles + IPC publicado
con 1 mes de delay). Por eso cada par trae `mes_inflacion=YYYY-MM` con
el mes del IPC al que refiere.

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
from engines.curvas import fecha_cer_liquidacion

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorBreakevens")

INTERVALO = 30          # segundos entre corridas
MAX_DIFF_DIAS = 20      # diferencia máxima entre vto Lecap/Boncap y vto CER
# MIN_DIAS_PLAZO: bonos con vto < 50 días implican inflación de meses ya
# publicados (IPC del mes M-2 cuando M está a <60d = M-2 ya publicado).
# No tiene utilidad analítica mostrar esos BE. 50 = 60 − 10 de tolerancia.
MIN_DIAS_PLAZO = 50


# ─────────────────────────────────────────────
# Carga y emparejamiento de instrumentos
# ─────────────────────────────────────────────

def cargar_pares():
    """Devuelve lista de pares (Lecap/Boncap, CER) ordenados por vto.

    Reglas (emparejamiento por mismo vto):
      - Cada Lecap/Boncap se empareja con el CER cuyo vto está más cerca.
      - Tolerancia: MAX_DIFF_DIAS (±20 días).
      - Si un mismo CER aparece en varios pares, se queda con el de menor
        diferencia absoluta de vto.
    """
    grupos = cargar_por_curva()
    lecaps = grupos.get("tasa_fija", [])  # incluye Lecap Y Boncap (ambos curva=tasa_fija)
    cers   = grupos.get("cer", [])

    candidatos = []
    for lecap in lecaps:
        try:
            fecha_lec = date.fromisoformat(lecap["fecha_vencimiento"][:10])
        except Exception:
            continue

        mejor, mejor_diff = None, None
        for cer in cers:
            try:
                fecha_cer = date.fromisoformat(cer["fecha_vencimiento"][:10])
            except Exception:
                continue
            diff = abs((fecha_lec - fecha_cer).days)
            if mejor_diff is None or diff < mejor_diff:
                mejor_diff = diff
                mejor = cer

        if mejor is None or mejor_diff > MAX_DIFF_DIAS:
            continue

        candidatos.append({
            "lecap_ticker":      lecap["ticker"],
            "lecap_corto":       lecap["ticker_corto"],
            "cer_ticker":        mejor["ticker"],
            "cer_corto":         mejor["ticker_corto"],
            "fecha_vencimiento": lecap["fecha_vencimiento"][:10],
            "_diff":             mejor_diff,
        })

    # Dedup: si un CER aparece en varios pares, conservar solo el de menor diff.
    mejor_por_cer = {}
    for c in candidatos:
        key = c["cer_ticker"]
        if key not in mejor_por_cer or c["_diff"] < mejor_por_cer[key]["_diff"]:
            mejor_por_cer[key] = c

    pares = sorted(mejor_por_cer.values(), key=lambda p: p["fecha_vencimiento"])
    for p in pares:
        del p["_diff"]

    logger.info("Pares CER/Lecap-Boncap cargados: %d", len(pares))
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


def ultimo_ipc_publicado(client) -> str | None:
    """YYYY-MM del IPC más reciente en Trading.InflacionMensual (INDEC).

    Se usa para filtrar pares cuyo `mes_inflacion` ya salió — no tiene
    sentido mostrar el BE de un IPC que YA se publicó.

    Devuelve None si la colección está vacía (para que el motor no filtre
    nada y deje pasar todo en ese caso borde).
    """
    doc = client["Trading"]["InflacionMensual"].find_one(
        {}, sort=[("fecha", -1)], projection={"_id": 0, "fecha": 1},
    )
    if not doc or not doc.get("fecha"):
        return None
    return str(doc["fecha"])[:7]


def ultimo_cer_publicado(client) -> str | None:
    """YYYY-MM-DD del CER más reciente en Trading.CER.

    Lo usa calcular_breakevens como fecha de referencia para contar los
    meses pendientes hasta que se fije el CER de liquidación del bono.
    """
    doc = client["Trading"]["CER"].find_one(
        {}, sort=[("fecha", -1)], projection={"_id": 0, "fecha": 1},
    )
    if not doc or not doc.get("fecha"):
        return None
    return str(doc["fecha"])[:10]


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

def calcular_breakevens(
    pares, tems, paridades, teas_cer, fecha_ref,
    ultimo_ipc_mes=None, dias_habiles=None, fecha_cer_max=None,
):
    """Resuelve el BE mensual por el método 'Buscar Objetivo' de Excel.

    Para un par Lecap/Boncap ↔ CER (cupón cero), la inflación mensual X
    que iguala los retornos es la solución de:

        (1 + TEM_lecap)^(días_lecap/30) · (paridad_cer/100) = (1 + X)^meses_pendientes

    donde `meses_pendientes` es la cantidad de meses de inflación que
    faltan pricear: desde el CER publicado más reciente (fecha_cer_max)
    hasta el CER de liquidación del bono (vto − 10 hábiles). Despejando:

        X = [(1 + TEM)^(días/30) · (paridad/100)]^(1/meses_pendientes) − 1

    Esto es la forma cerrada equivalente al brentq de scipy — coincide
    exactamente porque la ecuación es algebráica y tiene solución única
    en el rango [0, ∞).

    Parámetros:
      fecha_ref: date — para calcular días a vencimiento.
      ultimo_ipc_mes: 'YYYY-MM' del último IPC publicado. Se descartan
        pares con mes_inflacion ≤ ese mes (ya no es expectativa).
      dias_habiles: lista ISO de días hábiles AR. Necesario para
        calcular la fecha del CER de liquidación del bono.
      fecha_cer_max: 'YYYY-MM-DD' del último CER publicado por BCRA.
        Necesario para contar los meses pendientes. Si no se pasa, se
        cae a la fórmula vieja (Fisher con 30/días al vto).
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

        # Info extra del CER de liquidación (T-10 hábiles). Se expone en
        # el output para el panel de debug, pero la fórmula del BE usa el
        # plazo completo hasta el vto (convención Fisher clásica).
        fecha_liq_cer = None
        if dias_habiles:
            fecha_liq_cer_str = fecha_cer_liquidacion(
                dias_habiles, par["fecha_vencimiento"], n=10,
            )
            if fecha_liq_cer_str:
                fecha_liq_cer = date.fromisoformat(fecha_liq_cer_str)

        # Mes del IPC cuya inflación pricean estos breakevens. Por la
        # convención del CER (settlement T-10 hábiles + IPC publicado con
        # 1 mes de rezago), un par Lecap(M) ↔ CER(M) refleja la inflación
        # implícita del IPC del mes M−2.
        y = fecha_vto.year
        m = fecha_vto.month - 2
        if m <= 0:
            m += 12
            y -= 1
        mes_inflacion = f"{y:04d}-{m:02d}"

        # Si el IPC de ese mes ya fue publicado por INDEC, el BE no tiene
        # utilidad (es un número conocido, no una expectativa). Filtramos.
        if ultimo_ipc_mes and mes_inflacion <= ultimo_ipc_mes:
            continue

        n += 1

        # Meses de inflación pendientes a pricear: entre el último CER
        # publicado y la liquidación del CER del bono. Si `fecha_cer_max`
        # o `fecha_liq_cer` faltan, fallback a días al vto / 30.
        meses_pendientes: float | None = None
        if fecha_cer_max and fecha_liq_cer:
            try:
                fecha_cer_max_d = date.fromisoformat(fecha_cer_max)
                delta_dias = (fecha_liq_cer - fecha_cer_max_d).days
                if delta_dias > 0:
                    meses_pendientes = delta_dias / 30.0
            except Exception:
                meses_pendientes = None

        entry = {
            "n":                 n,
            "lecap":             par["lecap_corto"],
            "cer":               par["cer_corto"],
            "fecha_vencimiento": par["fecha_vencimiento"],
            "fecha_cer_liq":     fecha_liq_cer.isoformat() if fecha_liq_cer else None,
            "fecha_cer_max":     fecha_cer_max,
            "meses_pendientes":  round(meses_pendientes, 4) if meses_pendientes else None,
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
                # Anualización: preferimos `meses_pendientes` (método
                # Buscar Objetivo). Si no está disponible, fallback al
                # clásico Fisher con días al vto.
                exponente = (1 / meses_pendientes) if meses_pendientes else (30 / dias)
                bkv       = (1 + inflacion) ** exponente - 1
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

def cargar_dias_habiles(client):
    """Lista ordenada ASC de días hábiles desde Trading.DiasHabiles."""
    docs = list(client["Trading"]["DiasHabiles"].find({}, {"fecha": 1, "_id": 0}))
    return sorted(d["fecha"] for d in docs)


def run():
    logger.info("Motor Breakevens iniciando...")
    client = get_mongo_client()

    pares         = cargar_pares()
    lecap_tickers = [p["lecap_ticker"] for p in pares]
    cer_tickers   = [p["cer_ticker"]   for p in pares]
    # Los días hábiles los cargamos una vez al arrancar — la tabla cambia
    # solo al fin de año (job dias_habiles corre 1×/año). Si el motor corre
    # por meses sin reinicio, la lista sigue siendo válida.
    dias_habiles = cargar_dias_habiles(client)

    logger.info(
        f"Monitoreando {len(pares)} pares Lecap/CER · {len(dias_habiles)} días hábiles cargados.",
    )

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

            ipc_mes = ultimo_ipc_publicado(client)
            cer_max = ultimo_cer_publicado(client)
            pares_result = calcular_breakevens(
                pares, tems, paridades, teas_cer, fecha_ref,
                ultimo_ipc_mes=ipc_mes,
                dias_habiles=dias_habiles,
                fecha_cer_max=cer_max,
            )
            guardar(client, pares_result, ts, fecha_str)

            n_completos = sum(1 for p in pares_result if "breakeven_mensual" in p)
            logger.info(f"{len(pares_result)} pares | {n_completos} con breakeven calculado.")

        except Exception:
            logger.error(f"Error en loop:\n{traceback.format_exc()}")

        time.sleep(INTERVALO)


if __name__ == "__main__":
    run()
