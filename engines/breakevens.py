"""
main_breakevens.py — Motor de breakevens CER/Lecap en tiempo real.

Cada 30s:
  1. Lee última TEM por Lecap/Boncap y última paridad por CER desde TimeSales
  2. Empareja cada bono tasa_fija con el CER cuyo vto es más cercano
     (Trading.Curvas). Lecap/Boncap con vto ~M ↔ CER con vto ~M.
  3. Calcula breakeven de inflación mensual implícita entre HOY y el vto.
  4. Upsert SQL mercado.mercado_hist (coleccion='BreakevensHistorico') → 1 fila
     por fecha (histórico diario; la fila de HOY se reescribe en cada corrida).
     El "live" es la fila más reciente — ya no hay BreakevensLive en Mongo.

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
from datetime import UTC, date, datetime

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

    Cada par trae también los campos del CER que hacen falta para el BE
    por método Buscar Objetivo: flujo_vencimiento_lecap, valor_nominal_cer,
    cer_emision.
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
            "flujo_vto_lecap":   lecap.get("flujo_vencimiento"),
            "vn_cer":            mejor.get("valor_nominal", 100),
            "cer_emision":       mejor.get("cer_emision"),
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
# Lectura de mercado (mercado.market_snapshot)
# ─────────────────────────────────────────────

def obtener_metricas(tickers) -> dict[str, dict]:
    """TEM + paridad + TEA + last_price de todos los tickers en UNA query.

    Las 4 métricas viven en la misma fila de mercado.market_snapshot → pedirlas
    por separado son 4 round-trips para leer exactamente las mismas filas. El loop
    del motor usa esta; los helpers de abajo (una métrica c/u) quedan porque los
    consume api/routers/manager/checks.py.
    """
    from core.market_snapshot import cols_map
    return cols_map(tickers, ["tem", "paridad", "tea", "last_price"])


def _filtrar(snap: dict[str, dict], tickers, col: str, positivo: bool = False) -> dict:
    """{ticker: valor} de una columna del snapshot. Misma semántica que
    core.market_snapshot.metric_map: descarta NULL y, si positivo, los <= 0."""
    out = {}
    for t in tickers:
        v = (snap.get(t) or {}).get(col)
        if v is None or (positivo and v <= 0):
            continue
        out[t] = v
    return out


def obtener_tems(client, tickers):
    """Última TEM por ticker Lecap (SQL-only: mercado.market_snapshot)."""
    from core.market_snapshot import metric_map
    return metric_map(tickers, "tem")


def obtener_paridades(client, tickers):
    """Última paridad por ticker CER (SQL-only: mercado.market_snapshot)."""
    from core.market_snapshot import metric_map
    return metric_map(tickers, "paridad")


def obtener_precios(client, tickers):
    """Último precio (positivo) por ticker — SQL-only (mercado.market_snapshot.last_price,
    escrito por valores.py). Para el BE por método Buscar Objetivo (precio_lecap/precio_cer)."""
    from core.market_snapshot import metric_map
    return metric_map(tickers, "last_price", positivo=True)


def obtener_valor_cer(client, fecha_iso: str) -> float | None:
    """Valor del CER publicado para una fecha ISO (SQL-only: macro.series_macro)."""
    from core.series_macro import valor_en_fecha
    return valor_en_fecha("CER", fecha_iso)


def ultimo_ipc_publicado(client) -> str | None:
    """YYYY-MM del IPC más reciente en Trading.InflacionMensual (INDEC).

    Se usa para filtrar pares cuyo `mes_inflacion` ya salió — no tiene
    sentido mostrar el BE de un IPC que YA se publicó.

    Devuelve None si la colección está vacía (para que el motor no filtre
    nada y deje pasar todo en ese caso borde).
    """
    from core.series_macro import ultima_fecha
    f = ultima_fecha("InflacionMensual")  # SQL-only
    return f[:7] if f else None


def ultimo_cer_publicado(client) -> str | None:
    """YYYY-MM-DD del CER más reciente en Trading.CER.

    Lo usa calcular_breakevens como fecha de referencia para contar los
    meses pendientes hasta que se fije el CER de liquidación del bono.
    """
    from core.series_macro import ultima_fecha
    return ultima_fecha("CER")  # SQL-only (ya viene 'YYYY-MM-DD')


def obtener_teas_cer(client, tickers):
    """Última TEA por ticker CER (SQL-only: mercado.market_snapshot.tea)."""
    from core.market_snapshot import metric_map
    return metric_map(tickers, "tea")


# ─────────────────────────────────────────────
# Cálculo de breakevens
# ─────────────────────────────────────────────

def calcular_breakevens(
    pares, tems, paridades, teas_cer, fecha_ref,
    ultimo_ipc_mes=None, dias_habiles=None, fecha_cer_max=None,
    precios=None, cer_actual=None,
):
    """Resuelve el BE mensual por el método 'Buscar Objetivo' de Excel
    (cupón cero) usando precio y flujo directos — sin TEM ni paridad.

    Para un par Lecap/Boncap ↔ CER:

        retorno_lecap = flujo_vto_lecap / precio_lecap − 1
        retorno_cer(X) = (vn_cer · cer_vto(X) / cer_emision) / precio_cer − 1
        cer_vto(X) = cer_actual · (1 + X)^meses_pendientes

    Buscamos X tal que retorno_cer(X) = retorno_lecap. Despejando:

        X = [(1 + retorno_lecap) · (precio_cer · cer_emision) /
             (vn_cer · cer_actual)]^(1/meses_pendientes) − 1

    Esto evita el compounding de convenciones que generaba la fórmula con
    TEM/paridad (que daba números sobreestimados).

    Parámetros:
      fecha_ref: date — para calcular días a vencimiento.
      ultimo_ipc_mes: 'YYYY-MM' del último IPC publicado. Se descartan
        pares con mes_inflacion ≤ ese mes.
      dias_habiles: lista ISO de días hábiles AR. Para calcular
        fecha_cer_liq = vto − 10 hábiles.
      fecha_cer_max: 'YYYY-MM-DD' del último CER publicado.
      precios: dict {ticker: precio} para lecap y CER.
      cer_actual: float — valor del CER publicado más reciente.

    Si falta alguno de los datos necesarios para el método nuevo (precios,
    cer_actual, meses_pendientes), se cae a la fórmula vieja Fisher con
    TEM/paridad para no bloquear el cálculo.
    """
    if tems is None or paridades is None:
        # Defensa: si no hay dicts, tratamos como vacíos.
        tems = tems or {}
        paridades = paridades or {}
    precios = precios or {}
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
        precio_lecap = precios.get(par["lecap_ticker"]) if precios else None
        precio_cer   = precios.get(par["cer_ticker"])   if precios else None
        flujo_vto_lecap = par.get("flujo_vto_lecap")
        vn_cer          = par.get("vn_cer") or 100
        cer_emision     = par.get("cer_emision")

        if tem is not None:
            entry["tem_lecap"] = round(float(tem), 6)
        if tea_cer is not None:
            entry["tea_cer"] = round(float(tea_cer), 6)
        if paridad is not None:
            entry["paridad_cer"] = round(float(paridad), 4)
        if precio_lecap is not None:
            entry["precio_lecap"] = round(float(precio_lecap), 4)
        if precio_cer is not None:
            entry["precio_cer"] = round(float(precio_cer), 4)

        # ── Método Buscar Objetivo (preferido) ──
        # Requiere: precios, flujo_vto_lecap, cer_emision, cer_actual,
        # meses_pendientes. Si falta alguno, fallback al método viejo.
        pudo_buscar_objetivo = False
        if (
            precio_lecap and precio_lecap > 0
            and precio_cer and precio_cer > 0
            and flujo_vto_lecap and flujo_vto_lecap > 0
            and cer_emision and cer_emision > 0
            and cer_actual and cer_actual > 0
            and meses_pendientes and meses_pendientes > 0
        ):
            try:
                retorno_lecap = (float(flujo_vto_lecap) / float(precio_lecap)) - 1
                factor = (
                    (1 + retorno_lecap)
                    * float(precio_cer) * float(cer_emision)
                    / (float(vn_cer) * float(cer_actual))
                )
                if factor > 0:
                    bkv = factor ** (1 / meses_pendientes) - 1
                    if -0.5 < bkv < 10:
                        entry["retorno_acumulado"]   = round(retorno_lecap, 6)
                        entry["breakeven_mensual"]   = round(bkv, 6)
                        entry["metodo"]              = "BuscarObjetivo"
                        pudo_buscar_objetivo = True
            except Exception:
                pass

        # ── Fallback Fisher clásico (si no tenemos datos para Buscar Objetivo) ──
        # Usa TEM + paridad + días al vto (convención tradicional).
        if not pudo_buscar_objetivo and tem is not None and paridad is not None:
            try:
                retorno   = (1 + float(tem)) ** (dias / 30) - 1
                inflacion = (1 + retorno) * (float(paridad) / 100) - 1
                bkv       = (1 + inflacion) ** (30 / dias) - 1
                if -0.5 < bkv < 10:
                    entry["retorno_acumulado"]   = round(retorno, 6)
                    entry["inflacion_acumulada"] = round(inflacion, 6)
                    entry["breakeven_mensual"]   = round(bkv, 6)
                    entry["metodo"]              = "Fisher"
            except Exception:
                pass

        resultado.append(entry)

    return resultado


# ─────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────

def guardar(pares_result, ts, fecha_str):
    if not pares_result:
        return

    # SQL-NATIVE (decomiso Mongo 2026-06-28): el único write es a mercado_hist.
    # BreakevensLive (Mongo) ya no se escribe — el reader live (mercado_hist_sql.
    # get_breakevens) toma la fila MÁS RECIENTE de BreakevensHistorico. El doc es
    # IDÉNTICO al que escribía el sync desde Mongo (updated_at, pares, fecha).
    doc_hist = {"updated_at": ts, "pares": pares_result, "fecha": fecha_str}
    from core.pg_mirror import write_hist
    write_hist("BreakevensHistorico", fecha_str, "", doc_hist)


# ─────────────────────────────────────────────
# Loop principal
# ─────────────────────────────────────────────

def cargar_dias_habiles(client):
    """Lista ASC de días hábiles desde mercado.dias_habiles (SQL-only)."""
    from core.calendario import dias_habiles_ordenados
    return dias_habiles_ordenados()


def run():
    logger.info("Motor Breakevens iniciando...")
    # SQL-NATIVE (decomiso Mongo): el motor no lee ni escribe Mongo. Lecturas de
    # mercado salen de SQL (market_snapshot/series_macro/dias_habiles) y el write
    # va a mercado_hist. Las funciones helper aún aceptan `client` (las reusan
    # checks.py/backfills) pero lo IGNORAN → se les pasa None.
    client = None

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

            snap       = obtener_metricas(lecap_tickers + cer_tickers)
            tems       = _filtrar(snap, lecap_tickers, "tem")
            paridades  = _filtrar(snap, cer_tickers,   "paridad")
            teas_cer   = _filtrar(snap, cer_tickers,   "tea")
            precios    = _filtrar(snap, lecap_tickers + cer_tickers, "last_price", positivo=True)

            ipc_mes = ultimo_ipc_publicado(client)
            cer_max = ultimo_cer_publicado(client)
            cer_actual = obtener_valor_cer(client, cer_max) if cer_max else None
            pares_result = calcular_breakevens(
                pares, tems, paridades, teas_cer, fecha_ref,
                ultimo_ipc_mes=ipc_mes,
                dias_habiles=dias_habiles,
                fecha_cer_max=cer_max,
                precios=precios,
                cer_actual=cer_actual,
            )
            guardar(pares_result, ts, fecha_str)

            n_completos = sum(1 for p in pares_result if "breakeven_mensual" in p)
            logger.info(f"{len(pares_result)} pares | {n_completos} con breakeven calculado.")

        except Exception:
            logger.error(f"Error en loop:\n{traceback.format_exc()}")

        time.sleep(INTERVALO)


if __name__ == "__main__":
    run()
