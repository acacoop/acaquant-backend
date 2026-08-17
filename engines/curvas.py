"""
curvas.py — Motor de enriquecimiento analítico en tiempo real (SQL-only).

Corre como servicio paralelo a engines/valores.py. Cada INTERVALO_SEGUNDOS lee el
last_price live de mercado.market_snapshot (lo escribe valores.py) para los tickers
de mercado.curvas y calcula TEA / TEM / duration / mod_duration / convexity /
paridad. **Qué FÓRMULA le toca a cada bono lo deciden sus EJES**
(`emisor_tipo`/`moneda_eje`/`ajuste`) vía `rama_calculo` — ya NO la palabra
`mercado.curvas.curva`, que se escribía a mano por fila (migrado 2026-08-16).

El resultado vuelve a mercado.market_snapshot con un upsert que toca SOLO las
columnas analíticas → no pisa las de precio/book que escribe valores.py sobre la
misma fila.

Datos de referencia con recarga periódica: CER (macro.series_macro), MEP
(valuaciones.dolar*) y A3500 (feed MAE mayorista). Cuando uno CAMBIA se invalida
el cache de cálculo solo de las curvas que dependen de él (ver `curva_depende_de`).

Uso:
    python -m engines.curvas
"""

import logging
import time
import traceback
from datetime import date, datetime, timedelta

import numpy as np

from core import market_snapshot, pg_mirror
from engines._curvas_loader import cargar_indexado_por_ticker
from quant.xirr import xirr as _xirr_quant

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("MotorCurvas")

INTERVALO_SEGUNDOS = 2          # bajado de 5 a 2: sin escrituras a TimeSales,
                                # podemos recalcular más rápido sin saturar.
INTERVALO_RECARGA_CER = 3600    # recarga CER cada 1 hora
INTERVALO_RECARGA_MEP = 60      # refresca MEP cada 1 min (para soberanos)
# TC dolar-linked: feed MAE mayorista (DolarOficialLive) se actualiza
# cada ~30s. Recargar cada 5 min alcanza para que paridad/TEA sigan al spot.
INTERVALO_RECARGA_A3500 = 300


# ─────────────────────────────────────────────
# Helpers de cálculo
# ─────────────────────────────────────────────

def xirr(fechas, flujos):
    """TEA implícita de los flujos — delega en quant.xirr (la implementación
    única y testeada: Newton con damping + bisección garantizada + rango
    apto inflación argentina). Este wrapper solo adapta la firma histórica
    del motor (listas paralelas) al formato cashflows [(fecha, monto)].

    Antes acá vivía una copia degradada (scipy.newton sin fallback) que en
    casos límite devolvía None donde quant/ resuelve — ver AUDITORIA A1.
    """
    return _xirr_quant(list(zip(fechas, flujos)))


def macaulay_duration(fechas_flujos, montos, tir, fecha_base):
    t = np.array([(f - fecha_base).days / 365.0 for f in fechas_flujos])
    cf = np.array(montos, dtype=float)
    pv = cf / ((1 + tir) ** t)
    total_pv = np.sum(pv)
    if total_pv <= 0:
        return None
    return round(float(np.sum(t * pv) / total_pv), 4)


def convexity(fechas_flujos, montos, tir, fecha_base):
    """Convexity (segunda derivada del precio respecto al yield, normalizada).

    Fórmula: C = (1/P) × Σ [t(t+1) × CF_t / (1+y)^(t+2)]

    Dimensiones: años². Usar en aproximación de Taylor de 2º orden:
        ΔP/P ≈ -D·Δy + (1/2)·C·Δy²

    El término convex es siempre positivo cuando los flujos son positivos,
    de ahí la propiedad financiera "la convexity te ayuda" (cuando cae la
    tasa el precio sube más de lo que duration estimaría; cuando sube,
    cae menos). Útil para escenarios de stress > 100 bps.

    Devuelve None si total_pv ≤ 0 (igual fallback que macaulay_duration).
    """
    t = np.array([(f - fecha_base).days / 365.0 for f in fechas_flujos])
    cf = np.array(montos, dtype=float)
    pv = cf / ((1 + tir) ** t)
    total_pv = np.sum(pv)
    if total_pv <= 0:
        return None
    sum_term = np.sum(t * (t + 1) * cf / ((1 + tir) ** (t + 2)))
    return round(float(sum_term / total_pv), 4)


def monto_flujo(f):
    if "monto" in f:
        return float(f["monto"])
    return float(f.get("amortizacion", 0)) + float(f.get("interes", 0))


def monto_flujo_cer(f, valor_nominal=100):
    vn = float(valor_nominal)
    amort = float(f.get("amortizacion_pct", 0)) / 100 * vn
    if "cupon_sobre_residual" in f:
        cupon = float(f.get("cupon_sobre_residual", 0)) * float(f.get("residual_previo_pct", 0)) / 100 * vn
    else:
        cupon = float(f.get("cupon_anual", 0)) * vn
    return amort + cupon


def monto_flujo_soberano(f, valor_nominal=100):
    """Flujo USD de un bono soberano hard-dollar.

    Atención con la semántica del campo: aunque `cupon_sobre_residual` suena
    a "tasa × residual", en los JSONs de soberanos del prospecto el valor
    ya viene MULTIPLICADO — es el monto del cupón en USD por cada 100 de VN.
    Ej. GD30 2026-07-13 con residual 72% y tasa anual 0.75%:
        tasa_semestral · residual · VN = 0.00375 · 0.72 · 100 = 0.27 USD
        → en el JSON aparece `cupon_sobre_residual: 0.27` (ya resuelto).

    Por eso el cálculo NO multiplica de nuevo por residual_previo_pct.
    La amortización sigue siendo % del VN original.

    amortizacion_pct · VN  +  cupon_sobre_residual · VN / 100
    """
    vn = float(valor_nominal)
    amort = float(f.get("amortizacion_pct", 0)) / 100 * vn
    cupon = float(f.get("cupon_sobre_residual", 0)) / 100 * vn
    return amort + cupon


def fecha_flujo(f):
    v = f.get("fecha")
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        return date.fromisoformat(v[:10])
    return None


def get_cer_en_fecha(cer_dict, fecha_date):
    for i in range(7):
        key = (fecha_date - timedelta(days=i)).isoformat()
        if key in cer_dict:
            return cer_dict[key]
    return None


# ─────────────────────────────────────────────
# Carga de datos de referencia
# ─────────────────────────────────────────────

def cargar_cer(dias: int = 90):
    """Carga CER solo de los últimos N días.

    El enriquecimiento de trades usa T-10 días hábiles como settlement,
    así que los valores más viejos nunca se consultan en tiempo real.
    Carga completa crece monotónica sin sentido.
    """
    from core.series_macro import serie_dict
    desde = (date.today() - timedelta(days=dias)).isoformat()
    cer = serie_dict("CER", desde)  # SQL-only (macro.series_macro)
    logger.info(f"CER cargado: {len(cer)} fechas (últimos {dias} días)")
    return cer


def cargar_dias_habiles():
    from core.calendario import dias_habiles_ordenados
    dias = dias_habiles_ordenados()  # SQL-only (mercado.dias_habiles)
    logger.info(f"Días hábiles cargados: {len(dias)}")
    return dias


def cargar_mep_actual() -> float | None:
    """Último MEP disponible. Prefiere DolarSnapshot live; cae al histórico.

    Usado para convertir precios de bonos soberanos ley-NY en pesos
    (ticker sin sufijo D/C) a USD antes del cálculo de YTM.
    """
    from core import dolar_sql
    snap = dolar_sql.snapshot_live()
    if snap and snap.get("mep"):
        return float(snap["mep"])
    doc = dolar_sql.ultimo("mep")
    if doc and doc.get("mep"):
        return float(doc["mep"])
    return None


def cargar_a3500_actual() -> float | None:
    """TC para valuar dolar-linked en tiempo real durante horas de mercado.

    Fuente única: feed MAE mayorista (UST$T plazo 000) vía
    `core.dolar_oficial.mid_oficial_live("oficial")`. La data la persiste
    `Valuaciones.DolarOficialLive` desde un script local en la PC oficina.
    Es el proxy intra-day del A3500.

    NO usamos Trading.DOLAR (A3500 BCRA fixing diario). Aunque ese sea
    el TC pactado en el prospecto, durante el día estaría 1 día stale
    y daría paridades/TEAs erradas. Si MAE está caído (PC apagada,
    Internet, etc.), devolvemos None: prefiero no enriquecer a
    enriquecer con dato viejo del BCRA.
    """
    from core.dolar_oficial import mid_oficial_live

    live = mid_oficial_live("oficial")
    valor = live.get("value")
    if valor and valor > 0:
        logger.info(
            "TC dolar-linked: %.4f (mae_mayorista, ts=%s)",
            float(valor), live.get("ts"),
        )
        return float(valor)

    logger.warning(
        "TC dolar-linked NO disponible — feed MAE (DolarOficialLive) vacío. "
        "Bonos dolar-linked van a quedar sin TEA/paridad. Verificar que el "
        "script local de la oficina esté corriendo."
    )
    return None


def precio_soberano_a_usd(precio: float, ticker_completo: str, mep: float | None) -> float | None:
    """Convierte precio de bono soberano a USD según el sufijo del ticker ROFEX.

    El detector mira el tercer segmento del ticker completo
    ('MERV - XMEV - <SIMBOLO> - 24hs'), porque el `ticker_corto` es un
    label humano y puede no reflejar la moneda (ej. usuario deja
    ticker_corto='GD30' aunque el ticker sea 'GD30D' en USD).

    - …/GD30D/…, …/GD30C/…  → precio ya en USD (retorna tal cual)
    - …/GD30/…               → precio en pesos, divide por MEP
    - Si falta MEP cuando se necesita → None (no enriquece).
    """
    if precio is None or precio <= 0 or not ticker_completo:
        return None
    partes = ticker_completo.split(" - ")
    simbolo = partes[2] if len(partes) >= 3 else ticker_completo
    sufijo = simbolo[-1].upper() if simbolo else ""
    if sufijo in ("D", "C"):
        return precio
    if not mep or mep <= 0:
        return None
    return precio / mep


def siguiente_dia_habil(dias_habiles, fecha_date):
    """Primer día hábil DESPUÉS de fecha_date según Trading.DiasHabiles."""
    fecha_str = fecha_date.isoformat()
    for f in dias_habiles:
        if f > fecha_str:
            return f
    return None


def get_cer_liquidacion(cer_dict, dias_habiles, fecha_str, n=10):
    """Retrocede n días hábiles desde fecha_str y retorna el valor CER de esa fecha."""
    idx = None
    for i, f in enumerate(dias_habiles):
        if f <= fecha_str:
            idx = i
    if idx is None or idx < n:
        return None
    fecha_n = date.fromisoformat(dias_habiles[idx - n])
    return get_cer_en_fecha(cer_dict, fecha_n)


def fecha_cer_liquidacion(dias_habiles, fecha_str, n=10):
    """Devuelve la fecha ISO (YYYY-MM-DD) del CER de liquidación de un flujo
    que settlea en fecha_str — o sea `fecha_str − n días hábiles`. No busca
    el valor en cer_dict; solo resuelve la fecha. None si no hay suficientes
    días hábiles atrás."""
    idx = None
    for i, f in enumerate(dias_habiles):
        if f <= fecha_str:
            idx = i
    if idx is None or idx < n:
        return None
    return dias_habiles[idx - n]


# ─────────────────────────────────────────────
# Qué FÓRMULA le corresponde a cada bono
# ─────────────────────────────────────────────

def rama_calculo(instrumento: dict) -> str:
    """La fórmula que le toca a este bono. **La decide la CLASIFICACIÓN, no la palabra.**

    La TEA no se calcula igual para todos: una Lecap, un CER (hay que ajustar por
    inflación) y un hard dólar (hay que pasar por el MEP) son tres cuentas
    distintas. Hasta el 2026-08-16 la elección salía de `mercado.curvas.curva`, una
    palabra escrita a mano por fila — con lo cual dar de alta un bono y olvidarse
    esa palabra lo mandaba a la fórmula equivocada sin un solo error en pantalla
    (los 4 BOPREAL del BCRA calculaban con la matemática de las ONs, y 6
    corporativos estaban escritos como `soberanos`).

    Ahora sale de los EJES, que es la MISMA clasificación que usa la vista
    (`core.curvas_ejes`). Una sola verdad para "qué es este bono".

    ⚠️ **El orden de las preguntas NO es el de la cadena vieja, y es a propósito.**
    Con la palabra, un bono tenía UNA curva y el orden daba igual. Con los ejes, un
    corporativo en dólares a tasa fija cumple la condición de `soberanos` Y la de
    `on`; para reproducir lo que el motor venía haciendo, `corporativo` se pregunta
    PRIMERO.

    **Fallback a la palabra** cuando faltan ejes: son los que nadie clasificó
    todavía. Sin esto se caerían a "solo duration" y perderían la TEA que hoy
    muestran. Es una rampa de transición — cuando no quede ninguno sin clasificar,
    se borra junto con la columna.

    Verificado bono por bono con `scripts/diag_motor_ejes` y `diag_tea_dos_ramas`
    antes de migrar: los que cambian de rama están medidos, con su delta.
    """
    emisor = instrumento.get("emisor_tipo")
    moneda = instrumento.get("moneda_eje")
    ajuste = instrumento.get("ajuste")
    if emisor and moneda and ajuste:
        if emisor == "corporativo":
            return "on"                      # la rama ON despacha por moneda_flujo adentro
        if ajuste == "cer":
            return "cer"
        if ajuste == "dolar_linked":
            return "dolar_linked"
        if ajuste == "fija":
            return "soberanos" if moneda in ("USD", "EUR") else "tasa_fija"
        return "otros"                       # tamar / badlar / tpm / caución
    # Sin ejes → la palabra vieja, para no perder lo que hoy funciona.
    c = (instrumento.get("curva") or "").strip()
    if c in ("tasa_fija", "cer", "soberanos", "dolar_linked"):
        return c
    if c == "on" or c.startswith("on_"):
        return "on"
    return "otros"


# Las TRES puertas que abre el `if` de la rama ON, y nada más. Es el vocabulario
# del MOTOR: cualquier otra palabra cae en el `else` y se valúa como peso nativo.
MONEDAS_FLUJO = ("USD", "DL", "ARS")


def moneda_flujo_esperada(instrumento: dict) -> str:
    """Qué debería decir `moneda_flujo` según los EJES del bono.

    ⚠️ **Existe porque hay DOS vocabularios para el mismo hecho y nadie los
    concilia** (medido 2026-08-17: **30 de 140 bonos** de la rama ON se
    contradicen). `rama_calculo` se migró a los ejes el 2026-08-16, pero el
    `if` de la rama ON sigue despachando por `moneda_flujo`, que es un campo
    aparte y se carga a mano. Mientras coinciden no pasa nada; cuando divergen,
    **el motor calcula con uno y la vista clasifica con el otro**.

    Y el modo de falla es silencioso por partida doble: el `else` de la rama ON
    no valida nada, así que una palabra de OTRO sistema —se encontraron cuatro
    bonos con `HD`, que es el vocabulario de la CARTERA (`portafolio.assets`)—
    no da error ni celda vacía: se valúa en pesos y publica una paridad de
    156.570 que parece un dato.

    Es una FUNCIÓN y no una tabla, y vive **acá** —pegada al `if` que la
    consume— por la misma razón que `rama_calculo`: si la respuesta viviera en
    otro módulo, las dos podrían volver a separarse.
    """
    emisor = instrumento.get("emisor_tipo")
    moneda = (instrumento.get("moneda_eje") or "").upper()
    ajuste = instrumento.get("ajuste")
    if not (emisor and moneda and ajuste):
        return ""          # sin ejes no se puede afirmar nada (≠ "está mal")
    if ajuste == "dolar_linked":
        return "DL"
    return "USD" if moneda in ("USD", "EUR") else "ARS"


# ─────────────────────────────────────────────
# Cálculo principal por documento
# ─────────────────────────────────────────────

def calcular_campos(
    doc, instrumento, cer_dict, dias_habiles,
    mep: float | None = None,
    tc_a3500: float | None = None,
):
    precio = doc.get("price")
    timestamp = doc.get("timestamp")

    if not precio or precio <= 0 or not timestamp:
        return None

    fecha_trade = timestamp.date() if isinstance(timestamp, datetime) else None
    if not fecha_trade:
        return None

    fecha_vto_str = instrumento.get("fecha_vencimiento")
    if not fecha_vto_str:
        return None

    try:
        fecha_vto = date.fromisoformat(fecha_vto_str[:10])
    except Exception:
        return None

    dias_a_vto = (fecha_vto - fecha_trade).days
    if dias_a_vto <= 0:
        return None

    # La rama la deciden los EJES (ver `rama_calculo`), ya no la palabra `curva`.
    curva = rama_calculo(instrumento)
    flujos_raw = instrumento.get("flujos") or []
    flujo_vto = instrumento.get("flujo_vencimiento")
    resultado = {}

    # ── TASA FIJA ─────────────────────────────────────────────────
    if curva == "tasa_fija":
        # Fecha base = settlement (T+1 hábil), igual que las otras curvas.
        # Antes era fecha_trade — daba TEM ~50bps arriba en Lecaps cortos
        # vs la calculadora local de la mesa. Ver memoria:
        # =POW(payoff/precio, 30.4166/dias_settle)-1.
        settlement_str_tf = siguiente_dia_habil(dias_habiles, fecha_trade)
        fecha_settlement_tf = (
            date.fromisoformat(settlement_str_tf) if settlement_str_tf else fecha_trade
        )
        dias_a_vto_s = (fecha_vto - fecha_settlement_tf).days
        if dias_a_vto_s <= 0:
            return None

        flujos_futuros = [
            (fecha_flujo(f), monto_flujo(f))
            for f in flujos_raw
            if fecha_flujo(f) and fecha_flujo(f) > fecha_settlement_tf and monto_flujo(f) > 0
        ]

        try:
            conv = None
            if flujos_futuros:
                fechas_dt = [datetime.combine(fecha_settlement_tf, datetime.min.time())] + \
                            [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros]
                cf = [-precio] + [m for _, m in flujos_futuros]
                tea = xirr(fechas_dt, cf)
                if tea is not None:
                    fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros]
                    montos_flujos    = [m for _, m in flujos_futuros]
                    fecha_base_dt    = datetime.combine(fecha_settlement_tf, datetime.min.time())
                    dur  = macaulay_duration(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                    conv = convexity(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                else:
                    dur = round(dias_a_vto_s / 365, 4)
            elif flujo_vto and flujo_vto > 0:
                tea = (flujo_vto / precio) ** (365.0 / dias_a_vto_s) - 1
                dur = round(dias_a_vto_s / 365, 4)
                # Zero coupon: un único flujo al vto. C = t(t+1)/(1+y)^2.
                fecha_base_dt = datetime.combine(fecha_settlement_tf, datetime.min.time())
                fecha_vto_dt  = datetime.combine(fecha_vto,   datetime.min.time())
                conv = convexity([fecha_vto_dt], [flujo_vto], tea, fecha_base_dt)
            else:
                return None

            if tea is None or not (-0.5 < tea < 50):
                return None

            tem = (1 + tea) ** (1 / 12) - 1
            resultado["TEA"] = round(tea, 6)
            resultado["TEM"] = round(tem, 6)
            resultado["duration"] = dur
            if dur is not None and tea > -1:
                resultado["mod_duration"] = round(dur / (1 + tea), 4)
            if conv is not None:
                resultado["convexity"] = conv

        except Exception:
            return None

    # ── CER ───────────────────────────────────────────────────────
    elif curva == "cer":
        cer_emision = instrumento.get("cer_emision")
        if not cer_emision or cer_emision <= 0:
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado

        # Settlement = primer día hábil después del trade
        settlement_str = siguiente_dia_habil(dias_habiles, fecha_trade)
        if not settlement_str:
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado
        fecha_settlement = date.fromisoformat(settlement_str)

        # CER de liquidación = CER en (settlement - 10 días hábiles)
        cer_liq = get_cer_liquidacion(cer_dict, dias_habiles, settlement_str, n=10)
        if not cer_liq:
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado

        ratio = cer_liq / cer_emision
        valor_nominal = float(instrumento.get("valor_nominal", 100))
        precio_tecnico = valor_nominal * ratio
        resultado["paridad"] = round(precio / precio_tecnico * 100, 4)

        dias_a_vto_s = (fecha_vto - fecha_settlement).days
        if dias_a_vto_s <= 0:
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado

        flujos_futuros = [
            (fecha_flujo(f), monto_flujo_cer(f, valor_nominal) * ratio)
            for f in flujos_raw
            if fecha_flujo(f) and fecha_flujo(f) > fecha_settlement and monto_flujo_cer(f, valor_nominal) > 0
        ]

        try:
            conv = None
            if flujos_futuros:
                fechas_dt = [datetime.combine(fecha_settlement, datetime.min.time())] + \
                            [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros]
                cf = [-precio] + [m for _, m in flujos_futuros]
                tea = xirr(fechas_dt, cf)
                if tea is not None:
                    fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros]
                    montos_flujos    = [m for _, m in flujos_futuros]
                    fecha_base_dt    = datetime.combine(fecha_settlement, datetime.min.time())
                    dur  = macaulay_duration(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                    conv = convexity(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
                else:
                    dur = round(dias_a_vto_s / 365, 4)
            else:
                resultado["duration"] = round(dias_a_vto_s / 365, 4)
                return resultado

            if tea is None or not (-0.99 < tea < 50):
                resultado["duration"] = round(dias_a_vto_s / 365, 4)
                return resultado

            resultado["TEA"] = round(tea, 6)
            resultado["duration"] = dur
            if dur is not None and tea > -1:
                resultado["mod_duration"] = round(dur / (1 + tea), 4)
            if conv is not None:
                resultado["convexity"] = conv

        except Exception:
            resultado["duration"] = round(dias_a_vto_s / 365, 4)

    # ── SOBERANOS (Globales/Bonares hard-dollar) ──────────────────
    elif curva == "soberanos":
        # Settlement T+1 (primer día hábil después del trade).
        settlement_str = siguiente_dia_habil(dias_habiles, fecha_trade)
        if settlement_str:
            fecha_settlement = date.fromisoformat(settlement_str)
        else:
            fecha_settlement = fecha_trade

        valor_nominal = float(instrumento.get("valor_nominal", 100))
        ticker_completo = instrumento.get("ticker") or ""

        # Precio a USD — divide por MEP solo si el ticker cotiza en pesos.
        # Decide según el sufijo del símbolo ROFEX (no del ticker_corto, que
        # es un label humano y puede no coincidir con la moneda real).
        precio_usd = precio_soberano_a_usd(precio, ticker_completo, mep)
        if precio_usd is None:
            # Sin MEP no podemos calcular YTM USD; dejamos duration ingenua.
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado

        # Filtrar solo flujos futuros al settlement.
        flujos_futuros = [
            (fecha_flujo(f), monto_flujo_soberano(f, valor_nominal), f)
            for f in flujos_raw
            if fecha_flujo(f)
            and fecha_flujo(f) > fecha_settlement
            and monto_flujo_soberano(f, valor_nominal) > 0
        ]

        if not flujos_futuros:
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado

        # Paridad = precio_usd / (residual_previo del primer flujo vivo).
        # Es el % de valor técnico pagado respecto al nominal vivo.
        primer_flujo = flujos_futuros[0][2]
        residual_vivo = float(primer_flujo.get("residual_previo_pct", 100))
        if residual_vivo > 0:
            resultado["paridad"] = round(precio_usd / residual_vivo * 100, 4)

        try:
            fechas_dt = [datetime.combine(fecha_settlement, datetime.min.time())] + \
                        [datetime.combine(fd, datetime.min.time()) for fd, _, _ in flujos_futuros]
            cf = [-precio_usd] + [m for _, m, _ in flujos_futuros]
            tea = xirr(fechas_dt, cf)

            if tea is None or not (-0.5 < tea < 10):
                # YTM no convergió o está fuera de rango razonable USD (50%-1000%
                # para soberanos stressed, pero >1000% es error numérico).
                resultado["duration"] = round(dias_a_vto / 365, 4)
                return resultado

            fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _, _ in flujos_futuros]
            montos_flujos    = [m for _, m, _ in flujos_futuros]
            fecha_base_dt    = datetime.combine(fecha_settlement, datetime.min.time())

            dur  = macaulay_duration(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
            conv = convexity(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)

            resultado["TEA"] = round(tea, 6)
            resultado["duration"] = dur if dur is not None else round(dias_a_vto / 365, 4)
            if dur is not None and tea > -1:
                resultado["mod_duration"] = round(dur / (1 + tea), 4)
            if conv is not None:
                resultado["convexity"] = conv

        except Exception:
            resultado["duration"] = round(dias_a_vto / 365, 4)

    # ── DOLAR LINKED (paga ARS a TC del momento del pago) ─────────
    elif curva == "dolar_linked":
        # Sin A3500 actual no podemos convertir el precio en pesos a USD
        # ni reportar paridad — dejamos solo duration ingenua.
        if not tc_a3500 or tc_a3500 <= 0:
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado

        # Settlement T+1.
        settlement_str = siguiente_dia_habil(dias_habiles, fecha_trade)
        if settlement_str:
            fecha_settlement = date.fromisoformat(settlement_str)
        else:
            fecha_settlement = fecha_trade

        valor_nominal = float(instrumento.get("valor_nominal", 100))

        # Precio en USD implícito por el TC actual. Análogo a dividir por
        # MEP en soberanos en pesos, pero acá usamos A3500 (lo que el bono
        # pacta como TC de referencia, según el campo tasa_referencia).
        precio_usd = precio / tc_a3500

        # Flujos en USD nominal — el shape es porcentual sobre VN igual
        # que soberanos. monto_flujo_soberano hace exactamente lo que
        # necesitamos: amort_pct/100·VN + cupon_sobre_residual/100·VN.
        flujos_futuros = [
            (fecha_flujo(f), monto_flujo_soberano(f, valor_nominal), f)
            for f in flujos_raw
            if fecha_flujo(f)
            and fecha_flujo(f) > fecha_settlement
            and monto_flujo_soberano(f, valor_nominal) > 0
        ]

        if not flujos_futuros:
            resultado["duration"] = round(dias_a_vto / 365, 4)
            return resultado

        # Paridad = precio_usd / VN — mide cuánto cotiza el bono respecto
        # al nominal en USD. Para zero coupon a la par sería 100%; bajo
        # la par (yield positivo) < 100%.
        if valor_nominal > 0:
            resultado["paridad"] = round(precio_usd / valor_nominal * 100, 4)

        try:
            fechas_dt = [datetime.combine(fecha_settlement, datetime.min.time())] + \
                        [datetime.combine(fd, datetime.min.time()) for fd, _, _ in flujos_futuros]
            cf = [-precio_usd] + [m for _, m, _ in flujos_futuros]
            tea = xirr(fechas_dt, cf)

            if tea is None or not (-0.5 < tea < 10):
                resultado["duration"] = round(dias_a_vto / 365, 4)
                return resultado

            fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _, _ in flujos_futuros]
            montos_flujos    = [m for _, m, _ in flujos_futuros]
            fecha_base_dt    = datetime.combine(fecha_settlement, datetime.min.time())

            dur  = macaulay_duration(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
            conv = convexity(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)

            # TEA en USD para dolar_linked. La TEM se reporta para tener
            # un equivalente mensual comparable a tasa_fija (operativo —
            # no es una TEM "real" porque la tasa subyacente es anual USD).
            resultado["TEA"] = round(tea, 6)
            resultado["TEM"] = round((1 + tea) ** (1 / 12) - 1, 6)
            resultado["duration"] = dur if dur is not None else round(dias_a_vto / 365, 4)
            if dur is not None and tea > -1:
                resultado["mod_duration"] = round(dur / (1 + tea), 4)
            if conv is not None:
                resultado["convexity"] = conv

        except Exception:
            resultado["duration"] = round(dias_a_vto / 365, 4)

    # ── ONs (obligaciones negociables corporativas) ───────────────
    # El sector va codificado en la curva: "on", "on_energia", "on_financiera",
    # etc. Todas comparten la misma matemática (dispatch por moneda_flujo).
    elif curva == "on":
        # USD → math hard-dollar (igual que soberanos): precio a USD (sufijo
        # D as-is, pesos ÷MEP) y YTM en USD. ARS → precio peso directo (math
        # tasa_fija en pesos). Los flujos vienen en shape nativo BondsMaster
        # (montos absolutos por 100 VN) → monto_flujo = amortizacion + interes.
        settlement_str = siguiente_dia_habil(dias_habiles, fecha_trade)
        fecha_settlement = (
            date.fromisoformat(settlement_str) if settlement_str else fecha_trade
        )
        dias_a_vto_s = (fecha_vto - fecha_settlement).days
        if dias_a_vto_s <= 0:
            return None

        moneda = (instrumento.get("moneda_flujo") or "USD").upper()
        ticker_completo = instrumento.get("ticker") or ""
        if moneda == "USD":
            precio_calc = precio_soberano_a_usd(precio, ticker_completo, mep)
            if precio_calc is None:
                resultado["duration"] = round(dias_a_vto / 365, 4)
                return resultado
        elif moneda == "DL":
            # Dólar-linked: paga en pesos al TC A3500 y los flujos están por 100
            # VN en escala USD. Algunas ONs tienen guardada la pata PESO (precio
            # ~144.000) y otras la pata USD (precio ~100). Si el precio ya está en
            # escala USD (< 1000) lo usamos tal cual; si es escala peso, lo
            # dividimos por el A3500 (= dólar oficial live, el de la home).
            if precio >= 1000:
                if not tc_a3500 or tc_a3500 <= 0:
                    resultado["duration"] = round(dias_a_vto / 365, 4)
                    return resultado
                precio_calc = precio / tc_a3500
            else:
                precio_calc = precio
        else:
            precio_calc = precio  # ARS peso nativo

        flujos_futuros = [
            (fecha_flujo(f), monto_flujo(f), f)
            for f in flujos_raw
            if fecha_flujo(f) and fecha_flujo(f) > fecha_settlement and monto_flujo(f) > 0
        ]
        if not flujos_futuros:
            resultado["duration"] = round(dias_a_vto_s / 365, 4)
            return resultado

        # Paridad = precio / residual vivo. El residual vivo (nominal que aún
        # falta amortizar) = Σ de las amortizaciones futuras — robusto, no depende
        # del campo `valor_residual` (que puede venir en otra escala que el flujo).
        residual_vivo = sum(float(f.get("amortizacion") or 0) for _, _, f in flujos_futuros)
        if residual_vivo > 0:
            resultado["paridad"] = round(precio_calc / residual_vivo * 100, 4)

        try:
            fechas_dt = [datetime.combine(fecha_settlement, datetime.min.time())] + \
                        [datetime.combine(fd, datetime.min.time()) for fd, _, _ in flujos_futuros]
            cf = [-precio_calc] + [m for _, m, _ in flujos_futuros]
            tea = xirr(fechas_dt, cf)

            if tea is None or not (-0.5 < tea < 50):
                resultado["duration"] = round(dias_a_vto_s / 365, 4)
                return resultado

            fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _, _ in flujos_futuros]
            montos_flujos    = [m for _, m, _ in flujos_futuros]
            fecha_base_dt    = datetime.combine(fecha_settlement, datetime.min.time())

            dur  = macaulay_duration(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
            conv = convexity(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)

            resultado["TEA"] = round(tea, 6)
            resultado["TEM"] = round((1 + tea) ** (1 / 12) - 1, 6)
            resultado["duration"] = dur if dur is not None else round(dias_a_vto_s / 365, 4)
            if dur is not None and tea > -1:
                resultado["mod_duration"] = round(dur / (1 + tea), 4)
            if conv is not None:
                resultado["convexity"] = conv

        except Exception:
            resultado["duration"] = round(dias_a_vto_s / 365, 4)

    # ── TAMAR / DUAL / otros ──────────────────────────────────────
    else:
        resultado["duration"] = round(dias_a_vto / 365, 4)

    return resultado if resultado else None


# ─────────────────────────────────────────────
# Loop principal
# ─────────────────────────────────────────────

_CAMPOS_ANALITICOS = ("TEA", "TEM", "duration", "mod_duration", "convexity", "paridad")


def dep_tasa_disponible(curva: str | None, mep: float | None, a3500: float | None) -> bool:
    """¿Está disponible la dependencia externa que necesita ESTE tipo de curva para
    calcular la tasa? Se usa para decidir si un "no pude calcular TEA" es genuino
    (dato roto → hay que LIMPIAR la TEA vieja) o transitorio (feed caído → NO tocar,
    para no borrar una tasa buena).

      - tasa_fija    → sin dependencia externa (siempre computable si hay flujos).
      - soberanos    → precisa MEP.
      - dolar_linked → precisa A3500 (feed MAE mayorista).
      - cer / on / tamar / etc → dep por-fecha más frágil: conservador, NO forzamos
        limpieza (devolvemos False) para no arriesgar borrar tasas válidas.

    ⚠️ El argumento es la RAMA (`rama_calculo`), no la columna `curva`. Tienen el
    mismo vocabulario a propósito, pero si acá entrara la palabra y allá el eje,
    el motor podría limpiar la tasa de un bono que en realidad calculó bien.
    """
    if curva == "tasa_fija":
        return True
    if curva == "soberanos":
        return bool(mep)
    if curva == "dolar_linked":
        return bool(a3500)
    return False


def curva_depende_de(curva: str | None, dep: str) -> bool:
    """¿El resultado de `calcular_campos` para ESTA RAMA depende del dato externo
    `dep` ('cer' | 'mep' | 'a3500')? Se usa para invalidar el cache de cálculo solo
    donde hace falta cuando uno de los tres se recarga y CAMBIA.

    Espejo de las ramas de `calcular_campos`:
      - cer    → solo la curva `cer` usa cer_dict.
      - mep    → `soberanos` (precio_soberano_a_usd) y ONs (rama moneda USD).
      - a3500  → `dolar_linked` y ONs (rama moneda DL).

    Las ONs quedan atadas a AMBOS TC porque la rama se elige por `moneda_flujo` y no
    lo miramos acá: conservador a propósito (invalidar de más nunca cambia un número;
    invalidar de menos sí).
    """
    c = curva or ""
    es_on = c == "on" or c.startswith("on_")
    if dep == "cer":
        return c == "cer"
    if dep == "mep":
        return c == "soberanos" or es_on
    if dep == "a3500":
        return c == "dolar_linked" or es_on
    return True  # dep desconocida → invalidar todo (conservador)


def invalidar_por_dep(ultimo_calculado: dict, curvas: dict, dep: str) -> int:
    """Saca del cache los tickers cuya curva depende de `dep`. Devuelve cuántos."""
    afectados = [t for t in ultimo_calculado
                 if curva_depende_de(rama_calculo(curvas.get(t) or {}), dep)]
    for t in afectados:
        del ultimo_calculado[t]
    return len(afectados)


def run():
    logger.info("Motor Curvas iniciando...")

    curvas = cargar_indexado_por_ticker()
    logger.info(f"Curvas cargadas: {len(curvas)} instrumentos")
    cer_dict = cargar_cer()
    dias_habiles = cargar_dias_habiles()
    mep_actual = cargar_mep_actual()
    tc_a3500_actual = cargar_a3500_actual()
    tickers = list(curvas.keys())

    ultimo_reload_cer = time.time()
    ultimo_reload_mep = time.time()
    ultimo_reload_a3500 = time.time()

    # Cache RAM ticker → último last_price con el que YA calculamos.
    # Si el last_price actual coincide, skipeamos (cero cambio en mercado).
    # Una recarga de CER/MEP/A3500 invalida el cache SOLO si el valor cambió, y
    # SOLO de las curvas que dependen de ese valor (ver curva_depende_de): un MEP
    # idéntico no puede mover la TEA de una Lecap → recalcularla es CPU tirada.
    ultimo_calculado: dict[str, float] = {}

    logger.info(
        "Escuchando %d tickers via MarketSnapshot. Loop cada %ds. MEP: %s · A3500: %s",
        len(tickers), INTERVALO_SEGUNDOS, mep_actual, tc_a3500_actual,
    )

    while True:
        try:
            # Recargas periódicas. Solo un cambio REAL del valor invalida cache, y
            # solo el de las curvas que dependen de ese valor.
            if time.time() - ultimo_reload_cer > INTERVALO_RECARGA_CER:
                nuevo_cer = cargar_cer()
                ultimo_reload_cer = time.time()
                if nuevo_cer != cer_dict:
                    cer_dict = nuevo_cer
                    invalidar_por_dep(ultimo_calculado, curvas, "cer")

            if time.time() - ultimo_reload_mep > INTERVALO_RECARGA_MEP:
                nuevo_mep = cargar_mep_actual()
                ultimo_reload_mep = time.time()
                if nuevo_mep != mep_actual:
                    mep_actual = nuevo_mep
                    invalidar_por_dep(ultimo_calculado, curvas, "mep")

            if time.time() - ultimo_reload_a3500 > INTERVALO_RECARGA_A3500:
                nuevo_a3500 = cargar_a3500_actual()
                ultimo_reload_a3500 = time.time()
                if nuevo_a3500 != tc_a3500_actual:
                    tc_a3500_actual = nuevo_a3500
                    invalidar_por_dep(ultimo_calculado, curvas, "a3500")

            # Lectura del snapshot live SQL-only (mercado.market_snapshot.last_price,
            # escrito por valores.py). Se arma el mismo shape {ticker, updated_at,
            # metrics.last_price} que consumía el loop.
            ms_docs = [
                {"ticker": d["ticker"], "updated_at": d["updated_at"],
                 "metrics": {"last_price": d["last_price"]}}
                for d in market_snapshot.last_prices(tickers)
            ]

            if not ms_docs:
                time.sleep(INTERVALO_SEGUNDOS)
                continue

            # SQL-only (mercado.market_snapshot): ya NO se escribe Trading.MarketSnapshot.
            pg_rows = []
            for ms_doc in ms_docs:
                ticker = ms_doc.get("ticker")
                last_price = (ms_doc.get("metrics") or {}).get("last_price")
                if not ticker or not last_price:
                    continue

                # Skip si ya calculamos para este last_price (no hubo trade
                # nuevo desde la última iteración, y CER/MEP/A3500 no se
                # recargaron tampoco — sino el cache estaría limpio).
                if ultimo_calculado.get(ticker) == last_price:
                    continue

                instrumento = curvas.get(ticker)
                if not instrumento:
                    continue

                # calcular_campos espera un "doc" con price y timestamp.
                # Le armamos uno desde MarketSnapshot — semánticamente
                # equivalente al trade que generó ese last_price.
                fake_doc = {
                    "ticker":    ticker,
                    "price":     last_price,
                    "timestamp": ms_doc.get("updated_at") or datetime.utcnow(),
                }
                campos = calcular_campos(
                    fake_doc, instrumento, cer_dict, dias_habiles,
                    mep_actual, tc_a3500_actual,
                )
                # Marcamos como visto aunque el cálculo falle, para no
                # iterar el mismo ticker indefinidamente sin progreso.
                ultimo_calculado[ticker] = last_price
                if not campos:
                    continue

                # Solo las columnas analíticas presentes en `campos` (TEA→tea, etc).
                # El upsert SQL actualiza SOLO esas columnas → no pisa las de precio
                # que escribe valores.py sobre la misma fila.
                row = {"ticker": ticker}
                row.update({f.lower(): campos[f]
                            for f in _CAMPOS_ANALITICOS if f in campos})
                # Anti-TEA-fantasma: si NO se calculó TEA pero la dependencia del tipo
                # de curva está disponible (no es feed caído), la tasa que quedó pegada
                # en el snapshot es basura → escribir NULL para no mostrar un valor viejo.
                if "TEA" not in campos and dep_tasa_disponible(
                        rama_calculo(instrumento), mep_actual, tc_a3500_actual):
                    row["tea"] = None
                    row["tem"] = None
                if len(row) > 1:
                    pg_rows.append(row)

            if pg_rows:
                # SQL-native, SIN throttle: este loop escribe DELTAS (solo tickers con
                # trade nuevo) — descartar un flush perdería el update.
                pg_mirror.write_snapshot("market_snapshot", ["ticker"], pg_rows)
                logger.info(f"{len(pg_rows)} tickers enriquecidos en market_snapshot (SQL).")

        except Exception:
            logger.error(f"Error en loop:\n{traceback.format_exc()}")

        time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    run()
