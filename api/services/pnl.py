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
from typing import Any

from api.services._mep import get_mep_for_date

logger = logging.getLogger(__name__)

# Reglas de normalización por tipo (mismas que jobs/aum.py::_calcular_valuacion).
# Duplicadas acá para evitar que api/services/ dependa de jobs/. Si en el
# futuro se centralizan, refactorizar ambos lados juntos.
_TIPOS_DIVISOR_100 = {
    "Títulos Públicos",
    "Letras del Tesoro Capitalizables en Pesos",
    "Letras del Tesoro Ajustables por CER en Pesos",
    "LEDE",   # letras a descuento (ej. S13N6), cotizan en paridad — 2026-07-24
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
    unidad: str, qty_efectiva: float,
    tipoTitulo: str | None, valor_aum: float,
    *,
    cartera: str | None = None,
    instrumentos_by_unidad: dict[str, str],
    portfolio_snap_by_ticker: dict[str, dict],
    snapshots_cierre_by_ticker: dict[str, dict],
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

    Las lookups se hacen SOLO contra los dicts pre-cargados en bulk desde SQL
    (`portfolio_snap_by_ticker`, `snapshots_cierre_by_ticker`,
    `instrumentos_by_unidad`) — obligatorios por firma para que un caller
    futuro no pueda reintroducir el N+1 (2 queries por ticker por cuenta)
    que la carga bulk eliminó.
    """
    if qty_efectiva == 0:
        return 0.0, "live"   # cerrado por operación — vale cero
    if not unidad:
        return valor_aum, "aum"

    instrumento = instrumentos_by_unidad.get(unidad, "")
    if instrumento and instrumento not in _PLACEHOLDERS_INSTRUMENTO:
        # 1. PortfolioSnapshot (SQL bulk) — motor live de tenencia.
        snap = portfolio_snap_by_ticker.get(instrumento)
        if snap:
            for campo in ("last_price", "closing_price"):
                px = snap.get(campo)
                try:
                    px = float(px) if px is not None else None
                except (TypeError, ValueError):
                    px = None
                if px is not None and px > 0:
                    return _aplicar_normalizer(px, qty_efectiva, cartera, tipoTitulo), "live"
        # 2. snapshots_cierre (SQL bulk) — último cierre persistido por ticker
        # (1 fila/ticker con el último, no hace falta sort).
        snc = snapshots_cierre_by_ticker.get(instrumento)
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

# Ajustes manuales por eventos corporativos (splits, canjes, pre-data). NO son
# categorías de negocio_movimientos — vienen de `operaciones.pnl_ajustes` y los
# mergea pnl_ajustes_sql.merge_ajustes_en_boletos al stream cronológico (por eso
# NO integran _CATS_RELEVANTES, que es también el filtro SQL de boletos).
_CAT_AJUSTE_SPLIT    = "ajuste_split"
_CAT_AJUSTE_CANTIDAD = "ajuste_cantidad"
_CATS_AJUSTE         = {_CAT_AJUSTE_SPLIT, _CAT_AJUSTE_CANTIDAD}

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
    unidad_to_match: dict[str, str],
    match_to_display: dict[str, str],
    instrumentos_by_unidad: dict[str, str],
    portfolio_snap_by_ticker: dict[str, dict],
    snapshots_cierre_by_ticker: dict[str, dict],
    boletos_by_id_cuenta: dict[str, list] | None = None,
    aum_rows_by_id_cuenta: dict[str, list] | None = None,
    fecha_actual_aum_global: str | None = None,
    mep_hoy: float | None = None,
    mep_cache: dict[str, float | None] | None = None,
) -> dict:
    """Cálculo del PnL por ticker — toma todas las deps por kwarg.

    Las deps (maps + boletos + AuM + snapshots) las precarga en bulk desde SQL
    `pnl_sql._deps_sql` y se pasan por kwargs → este motor NO lee Mongo (decomiso).
    Una pasada de SQL alimenta las N cuentas (sin queries per-cuenta).
    """
    # ── 1. Boletos en orden cronológico ─────────────────────────────────
    # Crítico: el cost-basis depende del orden de procesamiento. Los boletos
    # YA vienen ordenados (fecha, comprobante) y filtrados desde el bulk loader
    # SQL (`pnl_sql._deps_sql`); este motor no lee Mongo (decomiso).
    boletos = (boletos_by_id_cuenta or {}).get(id_cuenta, [])

    # ── 1b. Última fecha del AuM — para distinguir movimientos intraday.
    # Boletos con fecha > fecha_actual_aum son day-trades del período actual
    # (post último cierre persistido). Su realizado se acumula aparte
    # para que la UI lo pueda mostrar en tickers cerrados intraday
    # (donde qty_efectiva=0 pero hubo trading hoy).
    # Si la cuenta tiene rows en el global latest (bulk SQL), su fecha_actual es ese
    # global; si no tiene rows (cuenta cerrada/sin posiciones hoy) → None.
    fecha_actual_aum: str | None = (
        fecha_actual_aum_global
        if id_cuenta in (aum_rows_by_id_cuenta or {})
        else None
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
        if importe == 0:
            # Sin plata no hay nada que convertir (ej. un ajuste/split sin
            # costo): 0 USD directo, sin lookup de MEP ni falso fechas_sin_mep.
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
        cat = b.get("categoria")
        # Un split viaja sin importe ni cantidad (solo factor) — el guard de
        # boletos vacíos no le aplica a los ajustes.
        if importe == 0 and cantidad == 0 and cat not in _CATS_AJUSTE:
            continue

        moneda = b.get("moneda") or "ARS"
        fecha  = b.get("fecha") or ""
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
        fila_audit = {
            "fecha":       fecha,
            "categoria":   cat,
            "op":          op,
            "cantidad":    cant_signed,
            "precio":      precio_b,
            "importe":     importe,
            "importe_ars": importe_ars,
            "moneda":      moneda,
            "mep":         b.get("mep"),
        }
        st["boletos"].append(fila_audit)

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

        elif cat == _CAT_AJUSTE_SPLIT:
            # Split / reverse split manual (operaciones.pnl_ajustes). Un split
            # multiplica la cantidad viva SIN tocar el costo: la plata invertida
            # no cambia, solo baja (o sube) el promedio por unidad. No genera
            # realizado ni pasivo, ni en ARS ni en USD. Sobre qty 0 es no-op;
            # sobre qty negativa (short/wash abierto) escala la deuda igual —
            # el short también se multiplica en un split real.
            try:
                factor = float(b.get("factor") or 0)
            except (TypeError, ValueError):
                factor = 0.0
            if factor > 0 and st["qty_actual"] != 0:
                antes_split = st["qty_actual"]
                st["qty_actual"] = antes_split * factor
                # El detalle audit muestra el delta de cantidad que aplicó el
                # split (depende del stock vivo al momento del evento).
                fila_audit["cantidad"] = st["qty_actual"] - antes_split
            fila_audit["factor"] = factor

        elif cat == _CAT_AJUSTE_CANTIDAD:
            # Ajuste manual de cantidad (canje de especie, posición pre-data,
            # dividendo en acciones). delta > 0: entra cantidad con costo
            # opcional (el `importe` del ajuste, pesificado como cualquier
            # boleto). delta < 0: sale cantidad liberando costo PROPORCIONAL,
            # SIN generar realizado — no es una venta, la plata no se movió
            # (el par entrante del canje recibe ese costo en el otro ticker).
            delta = cant_signed
            if delta > 0:
                st["costo_remanente"] += abs(importe_ars)
                if usd_ok:
                    st["costo_remanente_usd"] += abs(importe_usd)
                st["qty_actual"] += delta
            elif delta < 0:
                if st["qty_actual"] > 0:
                    avg_cost = st["costo_remanente"] / st["qty_actual"]
                    avg_cost_usd = st["costo_remanente_usd"] / st["qty_actual"]
                    q_sale = min(-delta, st["qty_actual"])
                    st["costo_remanente"] -= avg_cost * q_sale
                    st["costo_remanente_usd"] -= avg_cost_usd * q_sale
                st["qty_actual"] += delta

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
        aum_docs = (aum_rows_by_id_cuenta or {}).get(id_cuenta, [])
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
            unidad_actual, qty_efectiva, tipoTitulo, valor_aum,
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
