"""Motor de PnL por (cuenta, ticker) con cost-basis weighted-average.

Para cada ticker se mantienen DOS canales de PnL:

  pnl_realizado   = Σ (precio_venta − precio_promedio) × qty_vendida
                    para cada venta histórica (compras/ventas se cancelan
                    en orden cronológico; la ganancia "queda" cerrada).
  pnl_no_realizado = qty_actual_calc × precio_actual − costo_remanente
                     (= valor de mercado del stock vivo − lo que pagaste
                     por esas qty específicas).

Más:
  pnl_pasivo  = Σ importes de `categoria=acreencia` (cupones, dividendos,
                amortizaciones). NO afectan cantidad, solo aportan cash
                cobrado independiente.

  pnl_total   = pnl_realizado + pnl_no_realizado + pnl_pasivo

Cost-basis weighted-average:
  Compra (precio P, cantidad Q):
    costo_remanente += P × Q
    qty_actual      += Q
  Venta (precio P, cantidad Q):
    avg_cost          = costo_remanente / qty_actual
    pnl_realizado    += (precio_efectivo − avg_cost) × Q
    costo_remanente  −= avg_cost × Q   ← descuenta solo la porción "viva"
    qty_actual       −= Q

Pesificación: cada importe USD/USDC se convierte al MEP de su fecha. Si
falta MEP → fallback en moneda original (flag fechas_sin_mep).

Limitación conocida (opción A): si la cuenta tenía posiciones ANTES del
primer boleto disponible, qty_actual_calc < qty del AuM. En ese caso el
costo_remanente está incompleto y pnl_no_realizado queda subestimado
(la porción pre-data no aporta ganancia "papel"). Flag completeness =
"parcial". Para tickers sin ningún boleto: "sin_boletos".

`importe` viene neto de comisiones del lado de Aunesa, así que la suma
con signo nativo cubre comisiones automáticamente.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date
from typing import Any

from api.cache import cached
from api.db import get_db_cashflow, get_db_trading, get_db_valuaciones
from api.services._mep import get_mep_for_date
from api.services.assets_sql import assets_rows
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Reglas de normalización por tipo (mismas que jobs/aum.py::_calcular_valuacion).
# Duplicadas acá para evitar que api/services/ dependa de jobs/. Si en el
# futuro se centralizan, refactorizar ambos lados juntos.
_TIPOS_DIVISOR_100 = {
    "Títulos Públicos",
    "Letras del Tesoro Capitalizables en Pesos",
    "Letras del Tesoro Ajustables por CER en Pesos",
    "Títulos de Deuda",
    "Obligaciones Negociables",
    "Fideicomisos Financieros",
    "Cheques de Pago Diferido",
}
_TIPOS_FUTUROS = {"Futuros", "Forwards", "Derivados"}
_PLACEHOLDERS_INSTRUMENTO = {"", "NO APLICA"}


# Renta fija (cotiza en paridad → ÷100) y resto, decidido por CARTERA — el campo
# confiable y siempre presente en portafolio.tenencia. Mismas listas que el writer.
_CARTERAS_DIV_100 = {"HD", "DL", "ARS"}
_CARTERAS_DIV_1 = {"FCI", "RENTA VARIABLE", "MONEDAS", "DERIVADOS"}


def _aplicar_normalizer(precio: float, qty: float, cartera: str | None = None,
                        tipoTitulo: str | None = None) -> float:
    """Homogeneiza el precio vivo con valor_aum (mismo ÷100/+1 que el writer).

    Decide el ÷100 por CARTERA (HD/DL/ARS = renta fija en paridad) — confiable y
    siempre presente en portafolio.tenencia. Si la cartera no está clasificada
    (vacía/rara), cae al `tipoTitulo` legacy como red de seguridad. Futuros
    (precio+1) por tipoTitulo."""
    tipo = str(tipoTitulo or "")
    if any(f.lower() in tipo.lower() for f in _TIPOS_FUTUROS):
        precio = precio + 1.0
    c = (cartera or "").strip().upper()
    if c in _CARTERAS_DIV_100:
        return (precio * qty) / 100
    if c in _CARTERAS_DIV_1:
        return precio * qty
    # Cartera desconocida/vacía → fallback al tipoTitulo (comportamiento legacy).
    if tipo in _TIPOS_DIVISOR_100:
        return (precio * qty) / 100
    return precio * qty


def _valor_actual_live(
    db_t, db_v, unidad: str, qty_efectiva: float,
    tipoTitulo: str | None, valor_aum: float,
    *,
    cartera: str | None = None,
    instrumentos_by_unidad: dict[str, str] | None = None,
    portfolio_snap_by_ticker: dict[str, dict] | None = None,
    snapshots_cierre_by_ticker: dict[str, dict] | None = None,
) -> tuple[float, str]:
    """Cadena de fallback para `valor_actual` durante la rueda.

    Returns:
        (valor, fuente) donde fuente ∈ {"live", "cierre", "aum"}.

    Casos especiales:
      - qty_efectiva == 0 → posición cerrada (vendiste todo). Vale 0.
      - qty_efectiva < 0 → short (palanca, futuros, USD/ARS). Calculamos
        igual qty × precio (negativo = deuda mark-to-market).
      - qty_efectiva > 0 → long. Camino normal.

    Cadena de fallback:
      1. PortfolioSnapshot.last_price (motor live de tenencia).
      2. SnapshotsCierre.last_price (último cierre persistido).
      3. valor_aum directo (fallback definitivo).

    Si los kwargs `*_by_*` vienen pre-cargados (path bulk de
    pnl_todas_cuentas), las 3 lookups se hacen contra los dicts en
    memoria en vez de pegarle a Mongo. Sin ellos cae al find_one
    original (path single-cuenta sigue funcionando).
    """
    if qty_efectiva == 0:
        return 0.0, "live"   # cerrado por operación — vale cero
    if not unidad:
        return valor_aum, "aum"

    if instrumentos_by_unidad is not None:
        instrumento = instrumentos_by_unidad.get(unidad, "")
    else:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT instrumento FROM portafolio.assets WHERE unidad = %s", (unidad,))
            row = cur.fetchone()
        instrumento = ((row[0] if row else "") or "").strip()
    if instrumento and instrumento not in _PLACEHOLDERS_INSTRUMENTO:
        # 1. PortfolioSnapshot — motor live escribe acá.
        if portfolio_snap_by_ticker is not None:
            snap = portfolio_snap_by_ticker.get(instrumento)
        else:
            snap = db_t["PortfolioSnapshot"].find_one(
                {"ticker": instrumento},
                {"_id": 0, "last_price": 1, "closing_price": 1},
            )
        if snap:
            for campo in ("last_price", "closing_price"):
                px = snap.get(campo)
                try:
                    px = float(px) if px is not None else None
                except (TypeError, ValueError):
                    px = None
                if px is not None and px > 0:
                    return _aplicar_normalizer(px, qty_efectiva, cartera, tipoTitulo), "live"
        # 2. SnapshotsCierre — último cierre persistido.
        if snapshots_cierre_by_ticker is not None:
            snc = snapshots_cierre_by_ticker.get(instrumento)
        else:
            snc = db_t["SnapshotsCierre"].find_one(
                {"ticker": instrumento},
                {"_id": 0, "last_price": 1, "fecha": 1},
                sort=[("fecha", -1)],
            )
        if snc:
            try:
                px = float(snc.get("last_price")) if snc.get("last_price") is not None else None
            except (TypeError, ValueError):
                px = None
            if px is not None and px > 0:
                return _aplicar_normalizer(px, qty_efectiva, cartera, tipoTitulo), "cierre"
    return valor_aum, "aum"


_CATS_PAGO         = {"compra", "suscripcion_fci"}
_CATS_COBRO_VENTA  = {"venta", "rescate_fci"}
_CATS_COBRO_PASIVO = {"acreencia"}
_CATS_RELEVANTES   = _CATS_PAGO | _CATS_COBRO_VENTA | _CATS_COBRO_PASIVO

_OPS_PASIVOS = ("Cash dividend", "Interest payment", "Partial redemption")

# Fallback regex sólo si `Valuaciones.Assets.TICKER` no está set (gap de
# metadata). Path principal: leer el TICKER del doc de Assets directo,
# que es la tabla maestra del mapping unidad ↔ ticker corto.
_RE_TICKER_FALLBACK = re.compile(r"^\[\d+\]\s*(.+?)(?=\s+-\s+|$)")

# Para extraer id_cuenta del campo `cuenta` formato "[123] NOMBRE" cuando
# se hace bulk-load de NegocioMovimientos en pnl_todas_cuentas. La
# colección no tiene `id_cuenta` directo, sólo `cuenta` como string.
_RE_TICKER_FALLBACK_ID_CUENTA = re.compile(r"^\[(\d+)\]")


def _ticker_corto_fallback(unidad: str) -> str:
    """Solo si Valuaciones.Assets no tiene TICKER para esta unidad."""
    m = _RE_TICKER_FALLBACK.match(unidad or "")
    return m.group(1).strip() if m else (unidad or "")


@cached(ttl=300)
def _build_unidad_maps() -> tuple[dict[str, str], dict[str, str]]:
    """Lee Valuaciones.Assets y devuelve dos maps:

      unidad_to_match: {unidad → match_key}    — para joinear boletos↔AuM.
      match_to_display: {match_key → display}  — para mostrar en la UI.

    Precedencia para `match_key`:
      1. `CAFCI` — FCI. Coincide con `boleto.ticker` parseado del bracket.
      2. `TICKER` — acciones / bonos / ONs.
      3. Fallback regex sobre la unidad.

    Precedencia para `display`:
      1. `TICKER` humano de Assets — ej "AL30", "Consultatio Multimercado V".
      2. el match_key (FCI sin TICKER cargado → muestra el código CAFCI).

    Caso típico para FCI:
      unidad = "[3580] CAFCI3580-1199 - Consultatio..."
      match_key = "CAFCI3580-1199"  (matchea con boleto.ticker)
      display = "Consultatio Multimercado V - Clase A"

    Cacheado 5min: el mapping cambia mensualmente al alta de instrumentos.
    Antes se rebuilda 1× por cuenta dentro de pnl_todas_cuentas → N+1.
    """
    unidad_to_match: dict[str, str] = {}
    match_to_display: dict[str, str] = {}
    placeholders = {"", "NO APLICA"}
    for a in assets_rows(["TICKER", "CAFCI"]):
        unidad = a["unidad"]
        if not unidad:
            continue
        cafci = (a["CAFCI"] or "").strip()
        ticker = (a["TICKER"] or "").strip()
        ticker_clean = ticker if ticker and ticker not in placeholders else None
        cafci_clean = cafci if cafci and cafci not in placeholders else None

        if cafci_clean:
            match_key = cafci_clean
        elif ticker_clean:
            match_key = ticker_clean
        else:
            match_key = _ticker_corto_fallback(unidad)
        unidad_to_match[unidad] = match_key

        # display: TICKER humano si hay (ej "Consultatio...") sino el
        # match_key (que para no-FCI es el ticker; para FCI sin TICKER
        # cargado, queda el código CAFCI — best effort).
        match_to_display.setdefault(match_key, ticker_clean or match_key)
    return unidad_to_match, match_to_display


def _new_state() -> dict:
    return {
        "qty_actual":       0.0,    # cantidad neta — running
        "costo_remanente":  0.0,    # cost basis del stock vivo (en ARS)
        # Acumuladores USD paralelos — mismo algoritmo que los ARS pero con
        # el importe en USD nativo de cada boleto (USD/USDC crudo; ARS ÷ MEP
        # de su fecha). El COSTO en USD queda anclado al MEP histórico de
        # cada compra; el VALOR actual se convierte aparte al MEP de hoy.
        "costo_remanente_usd":  0.0,
        "pnl_realizado_usd":    0.0,
        "pnl_realizado_dia_usd": 0.0,
        "pnl_pasivo_usd":       0.0,
        "pnl_pasivo_dia_usd":   0.0,
        "importe_invertido": 0.0,   # Σ |importe| de TODAS las compras
                                    # (incluye posiciones ya cerradas)
        "pnl_realizado":    0.0,    # ganancias/pérdidas de ventas pasadas
        "pnl_realizado_dia": 0.0,   # solo de boletos > fecha_actual_aum
                                    # (day-trades intraday)
        "pnl_pasivo":       0.0,    # cupones + divs + amorts
        "pnl_pasivo_dia":   0.0,    # idem pero solo de los acreencia post-AuM
        "breakdown_pasivo": {op: 0.0 for op in _OPS_PASIVOS},
        "breakdown_otros":  0.0,    # acreencia con op desconocido
        "qty_compras":      0.0,    # bruto, para detectar pre-data
        "qty_ventas":       0.0,
        "monedas":          set(),
        "fechas_sin_mep":   set(),
        "n_movimientos":    0,
        "boletos":          [],     # detalle audit per ticker
    }


def _pnl_por_cuenta_core(
    *,
    id_cuenta: str,
    db_cf, db_v, db_t,
    unidad_to_match: dict[str, str],
    match_to_display: dict[str, str],
    instrumentos_by_unidad: dict[str, str] | None = None,
    portfolio_snap_by_ticker: dict[str, dict] | None = None,
    snapshots_cierre_by_ticker: dict[str, dict] | None = None,
    boletos_by_id_cuenta: dict[str, list] | None = None,
    aum_rows_by_id_cuenta: dict[str, list] | None = None,
    fecha_actual_aum_global: str | None = None,
    mep_hoy: float | None = None,
    mep_cache: dict[str, float | None] | None = None,
) -> dict:
    """Cálculo del PnL por ticker — toma todas las deps por kwarg.

    Cuando `pnl_todas_cuentas` precarga los maps + boletos + AuM en bulk
    y los pasa por kwargs, las queries Mongo per-cuenta se eliminan: 6
    queries totales independientes de N cuentas en lugar de ~5N. En el
    path single-cuenta los kwargs vienen None y se cae a los find/find_one
    tradicionales (mismo comportamiento de antes).
    """
    # ── 1. Boletos en orden cronológico ─────────────────────────────────
    # Crítico: el cost-basis depende del orden de procesamiento.
    # El campo `mep` viene en cada doc desde el job (snapshot inmutable
    # del día del boleto). Solo caemos a `get_mep_for_date` si no está
    # (boletos pre-fix sin reingestar, fechas anteriores al feed).
    if boletos_by_id_cuenta is not None:
        boletos = boletos_by_id_cuenta.get(id_cuenta, [])
    else:
        boletos = list(db_cf["NegocioMovimientos"].find(
            {
                # id_cuenta denormalizado + indexado (idcuenta_categoria_fecha).
                # Antes regex sobre `cuenta` → COLLSCAN de 348k docs (709ms);
                # ahora IXSCAN de los ~600 boletos de la cuenta (8ms). Cobertura
                # 100% y equivalencia verificadas (scripts/diag_pnl_cuenta).
                "id_cuenta": str(id_cuenta),
                "categoria": {"$in": list(_CATS_RELEVANTES)},
                "ticker":    {"$ne": None},
            },
            {"_id": 0, "fecha": 1, "categoria": 1, "op": 1,
             "ticker": 1, "cantidad": 1, "precio": 1, "importe": 1,
             "moneda": 1, "comprobante": 1, "mep": 1},
        ).sort([("fecha", 1), ("comprobante", 1)]))

    # ── 1b. Última fecha del AuM — para distinguir movimientos intraday.
    # Boletos con fecha > fecha_actual_aum son day-trades del período actual
    # (post último cierre persistido). Su realizado se acumula aparte
    # para que la UI lo pueda mostrar en tickers cerrados intraday
    # (donde qty_efectiva=0 pero hubo trading hoy).
    fecha_actual_aum: str | None
    if aum_rows_by_id_cuenta is not None:
        # En path bulk: si la cuenta tiene rows en el global latest, su
        # fecha_actual es ese global. Si no tiene rows (cuenta cerrada/
        # sin posiciones hoy) fecha_actual=None — mismo resultado que
        # find_one que devolvería None.
        fecha_actual_aum = (
            fecha_actual_aum_global
            if id_cuenta in aum_rows_by_id_cuenta
            else None
        )
    else:
        last_aum_doc = db_v["AuM"].find_one(
            {"id_cuenta": id_cuenta},
            {"_id": 0, "fecha_snapshot": 1},
            sort=[("fecha_snapshot", -1)],
        )
        fecha_actual_aum = (
            last_aum_doc["fecha_snapshot"] if last_aum_doc else None
        )

    # ── 2. Pesificación helper ──────────────────────────────────────────
    # Cache solo para fallback (fechas que no tenían mep en el doc). En el path
    # bulk se pasa un dict COMPARTIDO entre cuentas (mep_cache) → la misma fecha
    # histórica se busca 1 vez en Mongo y no N (era ~2.8k lookups → ~21s en el
    # cron). En single-cuenta arranca vacío (idéntico comportamiento de antes).
    mep_fallback_cache: dict[str, float | None] = mep_cache if mep_cache is not None else {}

    def _pesificar(b: dict) -> tuple[float, bool]:
        """Devuelve (importe_ars, mep_missing). Lee `mep` directo del doc;
        si no está, fallback a Valuaciones.Dolar."""
        try:
            importe = float(b.get("importe") or 0)
        except (TypeError, ValueError):
            return 0.0, False
        moneda = b.get("moneda") or "ARS"
        if moneda == "ARS":
            return importe, False

        mep = b.get("mep")
        if mep is None:
            fecha = b.get("fecha") or ""
            if fecha not in mep_fallback_cache:
                mep_fallback_cache[fecha] = get_mep_for_date(fecha)
            mep = mep_fallback_cache[fecha]
        if mep is None or mep <= 0:
            return importe, True  # fallback: queda en moneda original
        return importe * mep, False

    def _usdificar(b: dict, importe_ars: float) -> float | None:
        """USD nativo del boleto. USD/USDC → importe crudo (ya está en USD).
        ARS → importe_ars ÷ MEP de su fecha. None si no se puede convertir
        (ARS sin MEP) — corrompería el cost-basis USD, así que la fila se
        flagea vía `fechas_sin_mep` igual que en la pesificación."""
        moneda = b.get("moneda") or "ARS"
        try:
            importe = float(b.get("importe") or 0)
        except (TypeError, ValueError):
            return 0.0
        if moneda != "ARS":
            return importe  # ya está en USD
        mep = b.get("mep")
        if mep is None:
            fecha = b.get("fecha") or ""
            if fecha not in mep_fallback_cache:
                mep_fallback_cache[fecha] = get_mep_for_date(fecha)
            mep = mep_fallback_cache[fecha]
        if mep is None or mep <= 0:
            return None
        return importe_ars / mep

    # ── 3. Procesar boletos en orden, mantener cost-basis running ───────
    state: dict[str, dict] = defaultdict(_new_state)

    for b in boletos:
        ticker = (b.get("ticker") or "").strip()
        if not ticker:
            continue
        try:
            importe = float(b.get("importe") or 0)
            cantidad = abs(float(b.get("cantidad") or 0))
        except (TypeError, ValueError):
            continue
        if importe == 0 and cantidad == 0:
            continue

        moneda = b.get("moneda") or "ARS"
        fecha  = b.get("fecha") or ""
        cat    = b.get("categoria")
        op     = b.get("op") or ""
        importe_ars, mep_missing = _pesificar(b)
        importe_usd = _usdificar(b, importe_ars)

        st = state[ticker]
        st["monedas"].add(moneda)
        st["n_movimientos"] += 1
        if mep_missing or importe_usd is None:
            st["fechas_sin_mep"].add(fecha)
        # Si no se pudo convertir a USD, no movemos el acumulador USD (deja
        # el cost-basis USD intacto en vez de corromperlo); la fila queda
        # flageada en fechas_sin_mep.
        usd_ok = importe_usd is not None

        # Detalle audit — guardamos cada boleto procesado para que el
        # frontend pueda mostrarlos y vos puedas reconciliar contra los
        # KPIs y totales del ticker.
        try:
            cant_signed = float(b.get("cantidad") or 0)
        except (TypeError, ValueError):
            cant_signed = 0.0
        try:
            precio_b = float(b.get("precio") or 0)
        except (TypeError, ValueError):
            precio_b = 0.0
        st["boletos"].append({
            "fecha":       fecha,
            "categoria":   cat,
            "op":          op,
            "cantidad":    cant_signed,
            "precio":      precio_b,
            "importe":     importe,
            "importe_ars": importe_ars,
            "moneda":      moneda,
            "mep":         b.get("mep"),
        })

        if cat in _CATS_PAGO:
            # Compra: importe negativo → |importe| es el costo invertido.
            #
            # Caso especial: wash trades / caución / ROE / trasvaso.
            # Cuando previamente vino una venta que excedía el stock
            # disponible (ej. caución colocadora -50M sin tener 50M),
            # qty_actual quedó negativo. Si esta compra "cubre" ese
            # short, NO sumamos al cost-basis — la operación neta es
            # cero. Solo la parte que excede el short es compra real.
            costo_total = abs(importe_ars)
            costo_total_usd = abs(importe_usd) if usd_ok else 0.0
            if st["qty_actual"] < 0 and cantidad > 0:
                cubierto = min(cantidad, -st["qty_actual"])
                nueva_compra = cantidad - cubierto
                if nueva_compra > 0:
                    # Solo lo que excede el short entra al costo,
                    # proporcional al importe del boleto.
                    frac = nueva_compra / cantidad
                    st["costo_remanente"] += costo_total * frac
                    if usd_ok:
                        st["costo_remanente_usd"] += costo_total_usd * frac
            else:
                st["costo_remanente"] += costo_total
                if usd_ok:
                    st["costo_remanente_usd"] += costo_total_usd
            st["qty_actual"]  += cantidad
            st["qty_compras"] += cantidad

        elif cat in _CATS_COBRO_VENTA:
            ingreso_total = importe_ars   # positivo
            #
            # Sin clip — qty_actual puede ir a negativo. Esto refleja
            # ventas que exceden el stock conocido (caución, wash
            # trades, ROE) y permite que la compra contraparte las
            # cancele luego sin inflar el cost-basis. Resultado: qty
            # neto coincide con el AuM cuando los wash trades existen.
            es_post_aum = bool(
                fecha_actual_aum and (fecha or "") > fecha_actual_aum
            )
            if st["qty_actual"] > 0:
                avg_cost = st["costo_remanente"] / st["qty_actual"]
                avg_cost_usd = st["costo_remanente_usd"] / st["qty_actual"]
                qty_a_vender = min(cantidad, st["qty_actual"])
                # Realizado proporcional a la porción que sí tenía
                # cost-basis. La parte excedente (cantidad - qty_a_vender)
                # NO genera realizado — su contraparte (compra futura)
                # se compensa entera, generando un wash neutro.
                frac_vend = (qty_a_vender / cantidad) if cantidad > 0 else 0
                ingreso_proporcional = ingreso_total * frac_vend
                realizado_este = ingreso_proporcional - (avg_cost * qty_a_vender)
                st["pnl_realizado"] += realizado_este
                if es_post_aum:
                    st["pnl_realizado_dia"] += realizado_este
                st["costo_remanente"] -= avg_cost * qty_a_vender
                # Espejo USD: ingreso de la venta en USD nativo (ARS ÷ MEP
                # de la fecha de venta), costo al MEP histórico de la compra.
                if usd_ok:
                    ingreso_prop_usd = importe_usd * frac_vend
                    realizado_usd = ingreso_prop_usd - (avg_cost_usd * qty_a_vender)
                    st["pnl_realizado_usd"] += realizado_usd
                    if es_post_aum:
                        st["pnl_realizado_dia_usd"] += realizado_usd
                st["costo_remanente_usd"] -= avg_cost_usd * qty_a_vender
            st["qty_actual"]  -= cantidad
            st["qty_ventas"]  += cantidad

        elif cat in _CATS_COBRO_PASIVO:
            # Acreencia: cupón / dividendo / amortización. Cobro suelto
            # que NO afecta cantidad ni cost basis.
            es_post_aum = bool(
                fecha_actual_aum and (fecha or "") > fecha_actual_aum
            )
            st["pnl_pasivo"] += importe_ars
            if usd_ok:
                st["pnl_pasivo_usd"] += importe_usd
                if es_post_aum:
                    st["pnl_pasivo_dia_usd"] += importe_usd
            if es_post_aum:
                st["pnl_pasivo_dia"] += importe_ars
            if op in st["breakdown_pasivo"]:
                st["breakdown_pasivo"][op] += importe_ars
            else:
                st["breakdown_otros"] += importe_ars

    # ── 4. Posición actual del AuM (último snapshot) ───────────────────
    # Los maps unidad↔match y match↔display vienen pre-cargados (cacheados
    # globalmente o construidos por el wrapper). Para FCI el match_key es
    # el código CAFCI y el display es el nombre del fondo.
    fecha_actual = fecha_actual_aum
    aum_por_ticker: dict[str, dict] = {}
    if fecha_actual:
        # En bulk path leemos rows del dict; en single-cuenta find directo.
        if aum_rows_by_id_cuenta is not None:
            aum_docs = aum_rows_by_id_cuenta.get(id_cuenta, [])
        else:
            aum_docs = db_v["AuM"].find(
                {"id_cuenta": id_cuenta, "fecha_snapshot": fecha_actual},
                {"_id": 0, "unidad": 1, "cantidad": 1, "precio": 1,
                 "valuacion": 1, "tipoTitulo": 1},
            )
        for d in aum_docs:
            unidad = d.get("unidad", "")
            ticker = unidad_to_match.get(unidad) or _ticker_corto_fallback(unidad)
            if not ticker:
                continue
            aum_por_ticker[ticker] = {
                "unidad":     unidad,
                "cantidad":   float(d.get("cantidad") or 0),
                "precio":     float(d.get("precio") or 0),
                "valuacion":  float(d.get("valuacion") or 0),
                "tipoTitulo": d.get("tipoTitulo"),
                "cartera":    d.get("cartera"),   # ← para el ÷100 por cartera
            }

    # ── 5. Construir filas y totales ────────────────────────────────────
    rows: list[dict[str, Any]] = []
    tot = {
        "costo_remanente":   0.0,
        "valor_actual":      0.0,    # del AuM (lo que físicamente tenés)
        "pnl_realizado":     0.0,
        "pnl_realizado_dia": 0.0,    # day-trades intraday (post fecha_aum)
        "pnl_no_realizado":  0.0,
        "pnl_pasivo":        0.0,
        "pnl_pasivo_dia":    0.0,
        "pnl_total":         0.0,
        # Espejo USD
        "costo_remanente_usd":   0.0,
        "valor_actual_usd":      0.0,
        "pnl_realizado_usd":     0.0,
        "pnl_realizado_dia_usd": 0.0,
        "pnl_no_realizado_usd":  0.0,
        "pnl_pasivo_usd":        0.0,
        "pnl_pasivo_dia_usd":    0.0,
        "pnl_total_usd":         0.0,
    }

    todos = set(state) | set(aum_por_ticker)
    for ticker in todos:
        st  = state.get(ticker) or _new_state()
        aum = aum_por_ticker.get(ticker, {})

        qty_aum       = float(aum.get("cantidad") or 0)

        # Filtro de visibilidad: solo posiciones reales HOY o actividad
        # intraday. Si AuM dice qty=0 (no tenés) y no hay boletos del
        # día (no operaste hoy) — sacar la fila. Cubre los dos casos
        # de ruido:
        #   - cerrados históricos sin actividad (Schroder Retorno, EWZ,
        #     ARKK, GD30, etc).
        #   - fantasmas con cost residual de boletos viejos no reconciliados
        #     (TX26, GGAL, AL30, COME, TSLA, IBIT, PLTR — el motor cree
        #     que tenés stock por compras viejas, AuM dice que no).
        # Si qty_aum != 0 (long, short, palanca, futuros, USD/ARS),
        # SIEMPRE mostrar — es la verdad contable oficial.
        hay_actividad_post_aum = bool(
            fecha_actual and st["boletos"]
            and any((b.get("fecha") or "") > fecha_actual for b in st["boletos"])
        )
        if qty_aum == 0 and not hay_actividad_post_aum:
            continue
        precio_actual = float(aum.get("precio") or 0)
        valor_aum     = float(aum.get("valuacion") or 0)
        tipoTitulo    = aum.get("tipoTitulo")
        cartera       = aum.get("cartera")
        unidad_actual = aum.get("unidad", "")
        qty_calc      = st["qty_actual"]
        costo_rem     = st["costo_remanente"]
        costo_rem_usd = st["costo_remanente_usd"]
        pnl_real      = st["pnl_realizado"]
        pnl_real_usd  = st["pnl_realizado_usd"]
        pnl_pas       = st["pnl_pasivo"]
        pnl_pas_usd   = st["pnl_pasivo_usd"]

        # qty_efectiva — orden de precedencia:
        #   1. Si qty_aum == 0: el AuM (contabilidad oficial) dice que NO
        #      tenés. Forzamos 0 incluso si qty_calc > 0. Cubre el caso
        #      "boletos viejos sin reconciliar" (compraste 100, vendiste
        #      80 fuera del feed, AuM dice 0, motor calcula 20 fantasma)
        #      que infla pnl_no_realizado con -costo_rem irreales.
        #   2. Si hay boletos: qty_calc — verdad operativa (incluye
        #      intraday del día).
        #   3. Si NO hay boletos: qty_aum como única señal de tenencia.
        if qty_aum == 0:
            qty_efectiva = 0.0
        elif st["n_movimientos"] > 0:
            qty_efectiva = qty_calc
        else:
            qty_efectiva = qty_aum

        # Valor actual: cadena live → cierre → AuM. Live arregla el
        # descalce intraday del AuM (RKLB +433 hoy: AuM=25 stale,
        # qty_calc=458 real) y ETHA vendido (qty_calc=0 → valor=0
        # aunque AuM siga mostrando 650).
        valor_actual_live, fuente_valor = _valor_actual_live(
            db_t, db_v, unidad_actual, qty_efectiva, tipoTitulo, valor_aum,
            cartera=cartera,
            instrumentos_by_unidad=instrumentos_by_unidad,
            portfolio_snap_by_ticker=portfolio_snap_by_ticker,
            snapshots_cierre_by_ticker=snapshots_cierre_by_ticker,
        )

        # PnL no-realizado: usamos el valor live (qty_efectiva × precio
        # vivo) contra el cost basis acumulado. Para tickers sin boletos
        # pero con tenencia (sin_boletos), no podemos calcular pnl porque
        # falta cost basis.
        # Valor actual en USD: el VALOR (no el costo) se convierte al MEP de
        # HOY, igual que Portfolio (valuacion ARS ÷ MEP del día). El costo
        # ya está anclado a los MEP históricos de cada compra.
        valor_actual_usd = (
            valor_actual_live / mep_hoy if (mep_hoy and mep_hoy > 0) else None
        )

        if qty_efectiva > 0 and st["n_movimientos"] > 0 and costo_rem > 0:
            pnl_no_real: float | None = valor_actual_live - costo_rem
        elif qty_efectiva == 0:
            pnl_no_real = 0.0   # cerrado intraday: 0 no-realizado, todo en realizado
        else:
            pnl_no_real = None  # tenencia sin boletos — no calculable

        # No-realizado USD = valor(hoy) − costo(histórico). Captura el efecto
        # cambiario. Mismo criterio de calculabilidad que el ARS.
        if pnl_no_real is None or valor_actual_usd is None:
            pnl_no_real_usd: float | None = None if pnl_no_real is None else 0.0
        elif qty_efectiva == 0:
            pnl_no_real_usd = 0.0
        else:
            pnl_no_real_usd = valor_actual_usd - costo_rem_usd

        # Completeness: para entender qué tan confiable es el cálculo.
        if st["n_movimientos"] == 0:
            completeness = "sin_boletos"
        elif abs(qty_calc - qty_aum) < 0.01:
            completeness = "completa"
        else:
            completeness = "parcial"

        # Total = realizado + no-realizado + pasivo. Si no-realizado es
        # None (sin boletos), no lo sumamos.
        pnl_total = pnl_real + pnl_pas + (pnl_no_real or 0.0)
        pnl_total_usd = pnl_real_usd + pnl_pas_usd + (pnl_no_real_usd or 0.0)

        breakdown = {k: round(v, 2) for k, v in st["breakdown_pasivo"].items() if v != 0}
        if st["breakdown_otros"] != 0:
            breakdown["Otros"] = round(st["breakdown_otros"], 2)

        precio_promedio = (
            costo_rem / qty_calc if qty_calc > 0 else None
        )

        rows.append({
            "ticker":             ticker,
            "display_name":       match_to_display.get(ticker, ticker),
            "unidad":             aum.get("unidad", ""),
            "qty_aum":            round(qty_aum, 4),
            "qty_calc":           round(qty_calc, 4),
            "qty_efectiva":       round(qty_efectiva, 4),
            "qty_compras":        round(st["qty_compras"], 4),
            "qty_ventas":         round(st["qty_ventas"], 4),
            "precio_actual":      round(precio_actual, 4),
            "precio_promedio":    round(precio_promedio, 4) if precio_promedio is not None else None,
            "costo_remanente":    round(costo_rem, 2),
            "valor_actual_aum":   round(valor_aum, 2),
            "valor_actual_live":  round(valor_actual_live, 2),
            "valor_actual_source": fuente_valor,   # "live" | "cierre" | "aum"
            "pnl_realizado":      round(pnl_real, 2),
            "pnl_realizado_dia":  round(st["pnl_realizado_dia"], 2),
            "pnl_no_realizado":   round(pnl_no_real, 2) if pnl_no_real is not None else None,
            "pnl_pasivo":         round(pnl_pas, 2),
            "pnl_pasivo_dia":     round(st["pnl_pasivo_dia"], 2),
            "breakdown_pasivo":   breakdown,
            "pnl_total":          round(pnl_total, 2),
            # ── Espejo USD (costo a MEP histórico, valor a MEP de hoy) ──
            "costo_remanente_usd":   round(costo_rem_usd, 2),
            "valor_actual_usd":      round(valor_actual_usd, 2) if valor_actual_usd is not None else None,
            "pnl_realizado_usd":     round(pnl_real_usd, 2),
            "pnl_realizado_dia_usd": round(st["pnl_realizado_dia_usd"], 2),
            "pnl_no_realizado_usd":  round(pnl_no_real_usd, 2) if pnl_no_real_usd is not None else None,
            "pnl_pasivo_usd":        round(pnl_pas_usd, 2),
            "pnl_pasivo_dia_usd":    round(st["pnl_pasivo_dia_usd"], 2),
            "pnl_total_usd":         round(pnl_total_usd, 2),
            "completeness":       completeness,
            "moneda_mixta":       len(st["monedas"]) > 1,
            "n_movimientos":      st["n_movimientos"],
            "fechas_sin_mep":     sorted(st["fechas_sin_mep"]),
            "boletos":            st["boletos"],
        })

        tot["costo_remanente"]   += costo_rem
        # Total de valor_actual: usa valor_actual_live (que ya cae al
        # AuM como fallback). Coherente con el valor por fila mostrado.
        tot["valor_actual"]      += valor_actual_live
        tot["pnl_realizado"]     += pnl_real
        tot["pnl_realizado_dia"] += st["pnl_realizado_dia"]
        tot["pnl_no_realizado"]  += (pnl_no_real or 0.0)
        tot["pnl_pasivo"]        += pnl_pas
        tot["pnl_pasivo_dia"]    += st["pnl_pasivo_dia"]
        tot["pnl_total"]         += pnl_total
        # Espejo USD. valor_actual_usd cae al ARS÷mep_hoy; si no hay mep_hoy
        # los _usd quedan en 0 y el frontend muestra el toggle deshabilitado.
        tot["costo_remanente_usd"]   += costo_rem_usd
        tot["valor_actual_usd"]      += (valor_actual_usd or 0.0)
        tot["pnl_realizado_usd"]     += pnl_real_usd
        tot["pnl_realizado_dia_usd"] += st["pnl_realizado_dia_usd"]
        tot["pnl_no_realizado_usd"]  += (pnl_no_real_usd or 0.0)
        tot["pnl_pasivo_usd"]        += pnl_pas_usd
        tot["pnl_pasivo_dia_usd"]    += st["pnl_pasivo_dia_usd"]
        tot["pnl_total_usd"]         += pnl_total_usd

    rows.sort(key=lambda r: -r["pnl_total"])

    return {
        "id_cuenta":    id_cuenta,
        "fecha_actual": fecha_actual,
        "rows":         rows,
        "totales": {
            "costo_remanente":   round(tot["costo_remanente"], 2),
            "valor_actual":      round(tot["valor_actual"], 2),
            "pnl_realizado":     round(tot["pnl_realizado"], 2),
            "pnl_realizado_dia": round(tot["pnl_realizado_dia"], 2),
            "pnl_no_realizado":  round(tot["pnl_no_realizado"], 2),
            "pnl_pasivo":        round(tot["pnl_pasivo"], 2),
            "pnl_pasivo_dia":    round(tot["pnl_pasivo_dia"], 2),
            "pnl_total":         round(tot["pnl_total"], 2),
            # Espejo USD (costo a MEP histórico, valor a MEP de hoy).
            "costo_remanente_usd":   round(tot["costo_remanente_usd"], 2),
            "valor_actual_usd":      round(tot["valor_actual_usd"], 2),
            "pnl_realizado_usd":     round(tot["pnl_realizado_usd"], 2),
            "pnl_realizado_dia_usd": round(tot["pnl_realizado_dia_usd"], 2),
            "pnl_no_realizado_usd":  round(tot["pnl_no_realizado_usd"], 2),
            "pnl_pasivo_usd":        round(tot["pnl_pasivo_usd"], 2),
            "pnl_pasivo_dia_usd":    round(tot["pnl_pasivo_dia_usd"], 2),
            "pnl_total_usd":         round(tot["pnl_total_usd"], 2),
        },
        "n_tickers": len(rows),
    }


# ─────────────────────────────────────────────────────────────────
# Vista TOTALES — PnL agregado de TODAS las cuentas (mesa entera).
# ─────────────────────────────────────────────────────────────────


def _load_pnl_bulk_deps(db_v, db_cf, db_t) -> dict:
    """Pre-carga TODO lo que necesita _pnl_por_cuenta_core en bulk.

    Sin esto, cada cuenta dispara ~5 round-trips a Atlas:
      - 1 NegocioMovimientos.find con regex sobre `cuenta` (sin índice)
      - 1 AuM.find_one(id_cuenta, sort=fecha_snapshot)
      - 1 AuM.find(id_cuenta, fecha=last)
      - 3 find_one por ticker en _valor_actual_live (Assets, PortfolioSnapshot,
        SnapshotsCierre)
    Con 883 cuentas × ~250ms RTT = ~750s. Vercel/CF cortan a 60s → 502.

    Acá hacemos ~6 queries totales (independientes de N cuentas) y agrupamos
    en memoria. Per-cuenta core solo procesa data ya en RAM.

    Defensiva: cada load va con try/except. Si uno falla (típico:
    aggregate sin índice → 16MB cap, o memoria) el dict queda vacío y
    el core cae al path single-cuenta para esa fuente.

    Returns dict con (todos opcionales, default {}):
      unidad_to_match, match_to_display, instrumentos_by_unidad,
      portfolio_snap_by_ticker, snapshots_cierre_by_ticker,
      boletos_by_id_cuenta, aum_rows_by_id_cuenta, fecha_actual_aum_global.
    """
    unidad_to_match, match_to_display = _build_unidad_maps()  # cacheado

    # Pricing maps (Assets / PortfolioSnapshot / SnapshotsCierre).
    instrumentos_by_unidad: dict[str, str] = {}
    try:
        for a in assets_rows(["INSTRUMENTO"]):
            u = a["unidad"]
            instr = (a["INSTRUMENTO"] or "").strip()
            if u and instr and instr not in _PLACEHOLDERS_INSTRUMENTO:
                instrumentos_by_unidad[u] = instr
    except Exception:
        logger.warning("_load_pnl_bulk_deps: fallo precarga Assets/instrumentos "
                       "— ese pricing degrada a per-cuenta (N+1)", exc_info=True)
        instrumentos_by_unidad = {}

    portfolio_snap_by_ticker: dict[str, dict] = {}
    try:
        for d in db_t["PortfolioSnapshot"].find(
            {}, {"_id": 0, "ticker": 1, "last_price": 1, "closing_price": 1}
        ):
            t = d.get("ticker")
            if t:
                portfolio_snap_by_ticker[t] = d
    except Exception:
        logger.warning("_load_pnl_bulk_deps: fallo precarga PortfolioSnapshot "
                       "— ese pricing degrada a per-cuenta (N+1)", exc_info=True)
        portfolio_snap_by_ticker = {}

    # SnapshotsCierre: doc con fecha más reciente por ticker. $sort+$group con
    # allowDiskUse=True — la colección crece 1 doc/ticker/día.
    snapshots_cierre_by_ticker: dict[str, dict] = {}
    try:
        pipeline = [
            {"$sort": {"ticker": 1, "fecha": -1}},
            {"$group": {
                "_id":        "$ticker",
                "last_price": {"$first": "$last_price"},
                "fecha":      {"$first": "$fecha"},
            }},
        ]
        for d in db_t["SnapshotsCierre"].aggregate(pipeline, allowDiskUse=True):
            t = d.get("_id")
            if t:
                snapshots_cierre_by_ticker[t] = {
                    "last_price": d.get("last_price"),
                    "fecha":      d.get("fecha"),
                }
    except Exception:
        logger.warning("_load_pnl_bulk_deps: fallo precarga SnapshotsCierre "
                       "— ese pricing degrada a per-cuenta (N+1)", exc_info=True)
        snapshots_cierre_by_ticker = {}

    # NegocioMovimientos: 1 scan, agrupados por `id_cuenta` (denormalizado en la
    # ingesta). Usa el campo directo; fallback al regex sobre `cuenta`
    # ("[123] NOMBRE") solo si faltara id_cuenta (docs viejos sin backfill) — así
    # no se pierde ningún boleto. Antes era 1 regex query por cuenta → ~883 queries.
    boletos_by_id_cuenta: dict[str, list] = {}
    try:
        cursor = db_cf["NegocioMovimientos"].find(
            {
                "categoria": {"$in": list(_CATS_RELEVANTES)},
                "ticker":    {"$ne": None},
            },
            {"_id": 0, "id_cuenta": 1, "cuenta": 1, "fecha": 1, "categoria": 1, "op": 1,
             "ticker": 1, "cantidad": 1, "precio": 1, "importe": 1,
             "moneda": 1, "comprobante": 1, "mep": 1},
        ).sort([("fecha", 1), ("comprobante", 1)])
        for b in cursor:
            cid = str(b.get("id_cuenta") or "").strip()
            if not cid:
                m = _RE_TICKER_FALLBACK_ID_CUENTA.match(b.get("cuenta") or "")
                if not m:
                    continue
                cid = m.group(1)
            boletos_by_id_cuenta.setdefault(cid, []).append(b)
    except Exception:
        logger.warning("_load_pnl_bulk_deps: fallo precarga NegocioMovimientos "
                       "— los boletos degradan a 1 regex query POR CUENTA", exc_info=True)
        boletos_by_id_cuenta = {}

    # AuM: el cron escribe el mismo `fecha_snapshot` para TODAS las cuentas
    # en cada corrida. Asumimos que el global latest aplica a todas las que
    # tengan rows en él. Cuentas inactivas (sin rows en el snapshot global)
    # arrancan sin AuM en bulk → core las trata como sin posiciones (mismo
    # comportamiento que cuando find_one no devolvía nada).
    fecha_actual_aum_global: str | None = None
    aum_rows_by_id_cuenta: dict[str, list] = {}
    try:
        last = db_v["AuM"].find_one(
            {}, {"_id": 0, "fecha_snapshot": 1},
            sort=[("fecha_snapshot", -1)],
        )
        if last:
            fecha_actual_aum_global = last["fecha_snapshot"]
            for d in db_v["AuM"].find(
                {"fecha_snapshot": fecha_actual_aum_global},
                {"_id": 0, "id_cuenta": 1, "unidad": 1, "cantidad": 1,
                 "precio": 1, "valuacion": 1, "tipoTitulo": 1},
            ):
                cid = d.get("id_cuenta")
                if cid is not None:
                    aum_rows_by_id_cuenta.setdefault(str(cid), []).append(d)
    except Exception:
        logger.warning("_load_pnl_bulk_deps: fallo precarga AuM "
                       "— degrada a find_one POR CUENTA", exc_info=True)
        fecha_actual_aum_global = None
        aum_rows_by_id_cuenta = {}

    return {
        "unidad_to_match":            unidad_to_match,
        "match_to_display":           match_to_display,
        "instrumentos_by_unidad":     instrumentos_by_unidad,
        "portfolio_snap_by_ticker":   portfolio_snap_by_ticker,
        "snapshots_cierre_by_ticker": snapshots_cierre_by_ticker,
        "boletos_by_id_cuenta":       boletos_by_id_cuenta,
        "aum_rows_by_id_cuenta":      aum_rows_by_id_cuenta,
        "fecha_actual_aum_global":    fecha_actual_aum_global,
    }


def pnl_todas_cuentas_compute() -> list[dict]:
    """Cómputo PESADO del PnL de TODAS las cuentas — lo corre el cron.

    Recorre las cuentas del último snapshot de AuM, pre-carga los maps en
    bulk (`_load_pnl_bulk_deps`) y llama a `_pnl_por_cuenta_core` por cada
    una. Devuelve una entrada por cuenta:

        {id_cuenta, cuenta, rows, totales}

    `rows` y `totales` salen tal cual del core — los `rows` incluyen el
    detalle de boletos (lo que el frontend muestra en el panel derecho).

    Lo corre `jobs.pnl_totales_precompute`, que persiste el resultado en
    `Valuaciones.PnLTotalesCache`. El endpoint `pnl_todas_cuentas` SOLO lee
    esa colección — nunca recalcula en vivo (recorrer 883 cuentas en una
    request HTTP se pasaba del timeout → 502).
    """
    # SQL: la lista de cuentas sale de portafolio.tenencia (portfolio_sql); el
    # portfolio.py Mongo leía Valuaciones.AuM (eliminada) → devolvía [] y el cache
    # de PnL TOTALES quedaba vacío.
    from api.services.portfolio_sql import listar_cuentas

    cuentas = listar_cuentas()
    if not cuentas:
        return []

    db_cf = get_db_cashflow()
    db_v  = get_db_valuaciones()
    db_t  = get_db_trading()

    # Pre-load global maps + boletos + AuM — ~6 queries totales en lugar
    # de ~5 × N cuentas.
    deps = _load_pnl_bulk_deps(db_v, db_cf, db_t)
    # Guard anti-N+1 silencioso (AUDITORIA A3): sin boletos NI AuM en bulk,
    # las 883 cuentas degradarían a ~5 queries c/u. Eso no es "funcionar",
    # es castigar al M10 una hora — abortamos y JobRunLogger alerta.
    if not deps.get("boletos_by_id_cuenta") and not deps.get("aum_rows_by_id_cuenta"):
        raise RuntimeError(
            "pnl_todas_cuentas_compute: precarga bulk vacía (boletos y AuM) — "
            "se aborta para no degradar a N+1; ver warnings de _load_pnl_bulk_deps"
        )
    # MEP de hoy: una sola lectura para convertir el VALOR actual a USD en
    # todas las cuentas (el costo va al MEP histórico por boleto).
    mep_hoy = get_mep_for_date(date.today().isoformat())

    # Cache de MEP histórico COMPARTIDO entre todas las cuentas: hay solo ~1.3k
    # fechas posibles, pero el fallback se llamaba ~2.8k veces (mismas fechas
    # re-buscadas por cuenta). Compartirlo corta los round-trips a Atlas → el
    # cron baja de ~25s a ~5s. Es un memo de get_mep_for_date (función de la
    # fecha) → valores idénticos.
    mep_cache: dict[str, float | None] = {}

    out: list[dict] = []
    for c in cuentas:
        id_cta = c.get("id_cuenta")
        if not id_cta:
            continue
        try:
            r = _pnl_por_cuenta_core(
                id_cuenta=str(id_cta),
                db_cf=db_cf, db_v=db_v, db_t=db_t,
                mep_hoy=mep_hoy,
                mep_cache=mep_cache,
                **deps,
            )
        except Exception:
            continue
        out.append({
            "id_cuenta": id_cta,
            "cuenta":    c.get("cuenta") or "",
            "rows":      r.get("rows", []),
            "totales":   r.get("totales", {}) or {},
        })
    return out


@cached(ttl=60)
def pnl_todas_cuentas(
    filtro_cuenta: str = "todas",
    scope: tuple[str, ...] | None = None,
) -> dict:
    """PnL agregado de todas las cuentas: una fila por (cuenta, ticker).

    LECTURA LIVIANA: lee `Valuaciones.PnLTotalesCache`, precalculada por el
    cron `jobs.pnl_totales_precompute` (cada 30 min en la rueda). El
    endpoint NUNCA recalcula en vivo — recorrer 883 cuentas en una request
    HTTP se pasaba del timeout (502) y no escalaba con la cantidad de
    usuarios. Acá solo se lee la colección y se aplica el filtro de tipo
    de cuenta en memoria.

    Args:
        filtro_cuenta: "todas" | "accionistas" | "sin_accionistas" |
                       "cooperativas" | "productores".

    Returns:
        {
          rows: [{cuenta, id_cuenta, ticker, display_name, ...}, ...],
          totales: {n_cuentas, n_filas, costo_remanente, valor_actual,
                    pnl_no_realizado, pnl_pasivo, pnl_total,
                    pnl_realizado_dia},
          filtro_cuenta,
        }
        Si la colección está vacía → rows: [] (falta correr el cron).
    """
    from api.services._cuentas_filter import match_cuenta_filter

    db_v = get_db_valuaciones()
    docs = list(db_v["PnLTotalesCache"].find({}, {"_id": 0, "computed_at": 0}))

    # Scoping de grupos: subset de cuentas visibles para el usuario. None =
    # sin restricción (admin o usuario sin grupo). Se aplica ANTES de sumar
    # los totales para que el agregado refleje sólo lo que el usuario ve.
    if scope is not None:
        permitidas = set(scope)
        docs = [d for d in docs if str(d.get("id_cuenta", "")) in permitidas]

    # Filtro de tipo de cuenta — subset de las cuentas ya calculadas.
    if filtro_cuenta and filtro_cuenta != "todas":
        sub_match = match_cuenta_filter(filtro_cuenta)
        if sub_match:
            last = db_v["AuM"].find_one(
                {}, {"_id": 0, "fecha_snapshot": 1},
                sort=[("fecha_snapshot", -1)],
            )
            ids_match: set = set()
            if last:
                ids_match = set(db_v["AuM"].distinct(
                    "id_cuenta",
                    {**sub_match, "fecha_snapshot": last["fecha_snapshot"]},
                ))
            docs = [d for d in docs if d.get("id_cuenta") in ids_match]

    rows: list[dict] = []
    totales = {
        "costo_remanente":   0.0,
        "valor_actual":      0.0,
        "pnl_no_realizado":  0.0,
        "pnl_pasivo":        0.0,
        "pnl_realizado_dia": 0.0,
        "pnl_total":         0.0,
        "costo_remanente_usd":   0.0,
        "valor_actual_usd":      0.0,
        "pnl_no_realizado_usd":  0.0,
        "pnl_pasivo_usd":        0.0,
        "pnl_realizado_dia_usd": 0.0,
        "pnl_total_usd":         0.0,
    }
    for d in docs:
        id_cta = d.get("id_cuenta")
        cta_label = d.get("cuenta") or ""
        for row in d.get("rows", []):
            # TOTALES lista posiciones abiertas — qty_aum != 0. Los rows
            # con qty_aum=0 (cerrados intraday) se omiten del listado; su
            # realizado del día ya está sumado en el `totales` por cuenta.
            if float(row.get("qty_aum") or 0) == 0:
                continue
            r2 = dict(row)
            r2["cuenta"]    = cta_label
            r2["id_cuenta"] = id_cta
            rows.append(r2)
        t = d.get("totales", {}) or {}
        for k in totales:
            totales[k] += float(t.get(k) or 0)

    # Sort default: pnl_total descendente (las que mejor andan arriba).
    # Para pnl_total visible usamos no_real + pasivo + real_dia (mismo
    # cálculo que el frontend, evita inconsistencia).
    def _total_view(row: dict) -> float:
        return (
            float(row.get("pnl_no_realizado") or 0)
            + float(row.get("pnl_pasivo") or 0)
            + float(row.get("pnl_realizado_dia") or 0)
        )
    rows.sort(key=lambda r: -_total_view(r))

    return {
        "rows": rows,
        "totales": {
            "n_cuentas":         len(docs),
            "n_filas":           len(rows),
            "costo_remanente":   round(totales["costo_remanente"], 2),
            "valor_actual":      round(totales["valor_actual"], 2),
            "pnl_no_realizado":  round(totales["pnl_no_realizado"], 2),
            "pnl_pasivo":        round(totales["pnl_pasivo"], 2),
            "pnl_realizado_dia": round(totales["pnl_realizado_dia"], 2),
            "pnl_total":         round(totales["pnl_total"], 2),
            "costo_remanente_usd":   round(totales["costo_remanente_usd"], 2),
            "valor_actual_usd":      round(totales["valor_actual_usd"], 2),
            "pnl_no_realizado_usd":  round(totales["pnl_no_realizado_usd"], 2),
            "pnl_pasivo_usd":        round(totales["pnl_pasivo_usd"], 2),
            "pnl_realizado_dia_usd": round(totales["pnl_realizado_dia_usd"], 2),
            "pnl_total_usd":         round(totales["pnl_total_usd"], 2),
        },
        "filtro_cuenta": filtro_cuenta,
    }
