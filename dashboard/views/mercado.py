"""Vista Mercado del dashboard: microstructure, Libro, Breakevens, Forwards, Volúmenes, Retorno Total."""
import math
from datetime import datetime, timedelta

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from dashboard.shared.db import _cargar_tickers_merv, get_db
from dashboard.shared.format import df_height, fmt_money, last_update_badge, short_name


# ==========================================
# RENDER FUNCTIONS — LIBRO
# ==========================================
def render_depth(book):
    bids   = book.get("bids", [])
    offers = book.get("offers", [])
    rows = []
    for i in range(5):
        rows.append({
            "Bid Q": f"{bids[i]['size']:,.0f}"    if i < len(bids)   else "-",
            "Bid P": bids[i]['price']              if i < len(bids)   else None,
            "Ask P": offers[i]['price']            if i < len(offers) else None,
            "Ask Q": f"{offers[i]['size']:,.0f}"   if i < len(offers) else "-",
        })
    df = pd.DataFrame(rows)
    styler = (
        df.style
        .map(lambda v: "color: #00cc66; font-weight: bold" if pd.notna(v) else "", subset=["Bid P"])
        .map(lambda v: "color: #ff4444; font-weight: bold" if pd.notna(v) else "", subset=["Ask P"])
        .format({
            "Bid P": lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
            "Ask P": lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
        })
    )
    st.caption("DEPTH")
    st.dataframe(styler, hide_index=True, use_container_width=True, height=df_height(5))


def render_quant(m):
    closing   = m.get('closing_price', 0) or 0
    last      = m.get('last_price', 0) or 0
    open_     = m.get('open_price', 0) or 0
    vs_cierre = (last / closing - 1) if closing > 0 and last > 0 else None
    intraday  = (last / open_ - 1)   if open_  > 0 and last > 0 else None

    rows = [
        ("Total Money",     fmt_money(m.get('total_money', 0))),
        ("Buy Session",     fmt_money(m.get('buy_money', 0))),
        ("Sell Session",    fmt_money(m.get('sell_money', 0))),
        ("VWAP (Daily)",    f"${m.get('vwap', 0):,.2f}"),
        ("Micro-Price",     f"{m.get('micro_price', 0):,.4f}"),
        ("Spread",          f"{m.get('spread', 0):,.2f}"),
        ("Order Imbalance", f"{m.get('imbalance', 0):.2%}"),
        ("Intraday",        f"{intraday:+.2%}" if intraday is not None else "-"),
        ("Cierre Anterior", f"${closing:,.2f}" if closing > 0 else "-"),
        ("Vs. Cierre",      f"{vs_cierre:+.2%}" if vs_cierre is not None else "-"),
    ]
    st.caption("QUANT ANALYTICS")
    st.dataframe(
        pd.DataFrame(rows, columns=["Métrica", "Valor"]),
        hide_index=True,
        use_container_width=True,
        height=df_height(len(rows)),
    )


_HOURLY_HEIGHT = df_height(7)  # 10hs a 16hs = 7 filas

def render_hourly(hourly_stats):
    rows = []
    for h in range(10, 17):
        d = hourly_stats.get(str(h), {"buy": 0, "sell": 0, "total": 0})
        rows.append({
            "Hora":  f"{h}hs",
            "Total": fmt_money(d.get("total", 0)),
            "Buy":   fmt_money(d.get("buy", 0)),
            "Sell":  fmt_money(d.get("sell", 0)),
        })
    st.caption("HOURLY VOL")
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=_HOURLY_HEIGHT)


def render_tape(trades, height=None):
    rows = []
    for t in trades[:30]:
        ts   = t.get("timestamp")
        hora = ts.strftime("%H:%M:%S") if hasattr(ts, 'strftime') else str(ts)[:8]
        rows.append({
            "Hora":   hora,
            "Precio": t.get('price', 0),
            "Size":   t.get('size', 0),
            "Side":   t.get("side", "MID"),
        })
    st.caption("TAPE")
    if not rows:
        st.caption("Sin trades recientes.")
        return
    df = pd.DataFrame(rows)
    styler = (
        df.style
        .map(lambda v: (
            "color: #00cc66; font-weight: bold" if v == "BUY" else
            "color: #ff4444; font-weight: bold" if v == "SELL" else
            "color: #aaa"
        ), subset=["Side"])
        .format({"Precio": "{:,.2f}", "Size": "{:,.0f}"})
    )
    h = height if height is not None else df_height(len(rows), max_h=1200)
    st.dataframe(styler, hide_index=True, use_container_width=True, height=h)


def render_whales(top_trades):
    rows = []
    for t in top_trades[:15]:
        money = t.get("money", (t.get("price", 0) / 100) * t.get("size", 0))
        rows.append({
            "Precio": t.get('price', 0),
            "Monto":  fmt_money(money),
            "Side":   t.get("side", "MID"),
        })
    st.caption("TOP 15 WHALES (CASH)")
    if not rows:
        st.info("Sin datos.")
        return
    df = pd.DataFrame(rows)
    styler = (
        df.style
        .map(lambda v: (
            "color: #00cc66; font-weight: bold" if v == "BUY" else
            "color: #ff4444; font-weight: bold" if v == "SELL" else
            "color: #aaa"
        ), subset=["Side"])
        .format({"Precio": "{:,.2f}"})
    )
    st.dataframe(styler, hide_index=True, use_container_width=True, height=df_height(len(rows), max_h=600))


# ==========================================
# RENDER FUNCTIONS — MERCADO
# ==========================================
def render_mercado_table(snaps, enriched=None):
    if enriched is None:
        enriched = {}
    rows = []
    for snap in snaps:
        ticker     = snap.get("ticker", "")
        m          = snap.get("metrics", {})
        total      = m.get("total_money", 0) or 0
        last_price = m.get("last_price",  0) or 0
        open_price = m.get("open_price",  0) or 0
        closing    = m.get("closing_price", 0) or 0
        vwap       = m.get("vwap",        0) or 0
        if total == 0:
            continue
        intraday  = (last_price / open_price - 1) if open_price > 0 and last_price > 0 else None
        vs_cierre = (last_price / closing - 1) if closing > 0 and last_price > 0 else None

        enc = enriched.get(ticker, {})
        tea = enc.get("TEA")
        dur = enc.get("duration")

        rows.append({
            "Ticker":   short_name(ticker),
            "Last":     last_price if last_price > 0 else None,
            "TEA":      tea,
            "Dur":      dur,
            "Total $":  fmt_money(total),
            "VWAP":     vwap if vwap > 0 else None,
            "Intraday": intraday,
            "1D%":      vs_cierre,
        })
    if not rows:
        st.info("Todos los tickers sin volumen aún.")
        return
    df = pd.DataFrame(rows)

    def pct_color(v):
        if pd.isna(v): return ""
        return "color: #00cc66; font-weight: bold" if v >= 0 else "color: #ff4444; font-weight: bold"

    fmt = {
        "Last":     lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
        "TEA":      lambda v: f"{v:.2%}"  if pd.notna(v) else "-",
        "Dur":      lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
        "VWAP":     lambda v: f"{v:,.2f}" if pd.notna(v) else "-",
        "Intraday": lambda v: f"{v:+.2%}" if pd.notna(v) else "-",
        "1D%":      lambda v: f"{v:+.2%}" if pd.notna(v) else "-",
    }

    styler = (
        df.style
        .map(pct_color, subset=["Intraday", "1D%"])
        .format(fmt)
    )
    st.caption("RESUMEN DE MERCADO")
    st.dataframe(styler, hide_index=True, use_container_width=True, height=df_height(len(df), max_h=900))


def render_tramo_vol(snaps, enriched):
    """Tabla: volumen por tramo (clickable) → detalle de tickers del tramo seleccionado."""
    TRAMOS = [
        ("Corto",  0.0,  0.5,  "≤ 6m"),
        ("Medio",  0.5,  1.5,  "6m–18m"),
        ("Largo",  1.5,  99.0, "> 18m"),
    ]

    buckets = {label: {
        "vol": 0.0, "tea_vol": 0.0, "intra_vol": 0.0, "oned_vol": 0.0,
        "intra_w": 0.0, "oned_w": 0.0,
        "items": [],   # [(snap, enc, total), ...]
    } for label, *_ in TRAMOS}

    for snap in snaps:
        ticker     = snap.get("ticker", "")
        m          = snap.get("metrics", {})
        total      = m.get("total_money", 0) or 0
        if total == 0:
            continue
        enc = enriched.get(ticker, {})
        dur = enc.get("duration")
        tea = enc.get("TEA")
        if dur is None:
            continue

        last_price = m.get("last_price",  0) or 0
        open_price = m.get("open_price",  0) or 0
        closing    = m.get("closing_price", 0) or 0
        intraday   = (last_price / open_price - 1) if open_price > 0 and last_price > 0 else None
        vs_cierre  = (last_price / closing   - 1) if closing   > 0 and last_price > 0 else None

        for label, low, high, _ in TRAMOS:
            if low <= dur < high:
                b = buckets[label]
                b["vol"] += total
                if tea is not None:
                    b["tea_vol"] += tea * total
                if intraday is not None:
                    b["intra_vol"] += intraday * total
                    b["intra_w"]   += total
                if vs_cierre is not None:
                    b["oned_vol"]  += vs_cierre * total
                    b["oned_w"]    += total
                b["items"].append((snap, enc, total))
                break

    total_global = sum(b["vol"] for b in buckets.values())
    if total_global == 0:
        return

    labels_order = [label for label, *_ in TRAMOS]
    rows = []
    for label, _, _, rango in TRAMOS:
        b   = buckets[label]
        vol = b["vol"]
        pct = vol / total_global if total_global > 0 else 0
        tea_p   = b["tea_vol"]  / vol           if vol > 0           else None
        intra_p = b["intra_vol"] / b["intra_w"] if b["intra_w"] > 0  else None
        oned_p  = b["oned_vol"]  / b["oned_w"]  if b["oned_w"]  > 0  else None
        rows.append({
            "Tramo":     f"{label} ({rango})",
            "Vol $":     fmt_money(vol)        if vol > 0            else "—",
            "% Vol":     f"{pct:.0%}"          if vol > 0            else "—",
            "TEA pond.": f"{tea_p:.2%}"        if tea_p   is not None else "—",
            "Intraday":  f"{intra_p:+.2%}"     if intra_p is not None else "—",
            "1D%":       f"{oned_p:+.2%}"      if oned_p  is not None else "—",
        })

    df_summary = pd.DataFrame(rows)

    st.caption("VOLUMEN POR TRAMO · RETORNO PONDERADO")
    ev = st.dataframe(
        df_summary,
        hide_index=True,
        use_container_width=True,
        height=df_height(len(df_summary)),
        on_select="rerun",
        selection_mode="single-row",
        key="tramo_vol_sel",
    )

    # ── Detalle del tramo seleccionado ────────────────────────────────────────
    sel_rows = ev.selection.rows if hasattr(ev, "selection") and ev.selection.rows else []
    if not sel_rows:
        return

    idx       = sel_rows[0]
    label_sel = labels_order[idx]
    items     = sorted(buckets[label_sel]["items"], key=lambda x: x[2], reverse=True)
    if not items:
        return

    st.caption(f"DETALLE — {rows[idx]['Tramo']}")

    det_rows = []
    for snap, enc, total in items:
        m          = snap.get("metrics", {})
        last       = m.get("last_price",    0) or 0
        open_      = m.get("open_price",    0) or 0
        closing    = m.get("closing_price", 0) or 0
        tea        = enc.get("TEA")
        dur        = enc.get("duration")
        intraday   = (last / open_   - 1) if open_   > 0 and last > 0 else None
        vs_cierre  = (last / closing - 1) if closing > 0 and last > 0 else None
        det_rows.append({
            "Ticker":   short_name(snap.get("ticker", "")),
            "TEA":      tea,
            "Dur":      dur,
            "Total $":  fmt_money(total),
            "Intraday": intraday,
            "1D%":      vs_cierre,
        })

    df_det = pd.DataFrame(det_rows)

    def _pct_color(v):
        if pd.isna(v): return ""
        return "color: #00cc66; font-weight: bold" if v >= 0 else "color: #ff4444; font-weight: bold"

    styler_det = (
        df_det.style
        .map(_pct_color, subset=["Intraday", "1D%"])
        .format({
            "TEA":      lambda v: f"{v:.2%}"  if pd.notna(v) else "—",
            "Dur":      lambda v: f"{v:.2f}"  if pd.notna(v) else "—",
            "Intraday": lambda v: f"{v:+.2%}" if pd.notna(v) else "—",
            "1D%":      lambda v: f"{v:+.2%}" if pd.notna(v) else "—",
        })
    )
    st.dataframe(styler_det, hide_index=True, use_container_width=True,
                 height=df_height(len(df_det)))


# ==========================================
# VISTAS (st.fragment → auto-refresh 1s, sin sleep ni rerun global)
# ==========================================

def vista_libro():
    db = get_db()

    tickers = _cargar_tickers_merv()

    if "selected_ticker" not in st.session_state or st.session_state.selected_ticker not in tickers:
        st.session_state.selected_ticker = tickers[0] if tickers else None

    if not tickers:
        st.warning("Sin tickers en Trading.Curvas.")
        return

    options_short = [short_name(t) for t in tickers]

    # Fila header: selector | vacío | última actualización (alineado sobre Quant)
    col_ticker, _, col_badge = st.columns([1, 1, 1])
    with col_ticker:
        current_idx   = tickers.index(st.session_state.selected_ticker)
        selected_short = st.selectbox(
            "Ticker", options_short,
            index=current_idx,
            label_visibility="collapsed",
        )
        st.session_state.selected_ticker = tickers[options_short.index(selected_short)]

    ticker = st.session_state.selected_ticker
    snap   = db["MarketSnapshot"].find_one({"ticker": ticker})

    if not snap:
        st.warning(f"Sin datos para {ticker}. ¿El motor está corriendo?")
        return

    with col_badge:
        last_update_badge(snap.get("updated_at"))

    book          = snap.get("book", {"bids": [], "offers": []})
    metrics       = snap.get("metrics", {})
    hourly_stats  = snap.get("hourly_stats", {})
    recent_trades = snap.get("recent_trades", [])

    # Tape height: depth (5r) + hourly (7r) + captions/espaciado
    _TAPE_HEIGHT = df_height(5) + _HOURLY_HEIGHT + 80

    # Fila 1: depth+hourly | tape | quant
    col_left, col_center, col_right = st.columns([1, 1, 1])
    with col_left:
        render_depth(book)
        st.write("")
        render_hourly(hourly_stats)
    with col_center:
        render_tape(recent_trades, height=_TAPE_HEIGHT)
    with col_right:
        render_quant(metrics)

    # Fila 2: LAST MINUTES | VOLUME PROFILE
    col_last, col_vp = st.columns([1, 1])

    with col_last:
        trades_sorted = sorted(recent_trades, key=lambda x: x.get("timestamp", datetime.min))
        trade_rows = [
            {
                "Hora":   t["timestamp"] if hasattr(t.get("timestamp"), "strftime") else None,
                "Precio": t.get("price", 0),
                "TEA":    t.get("TEA"),
            }
            for t in trades_sorted
            if t.get("price", 0) > 0 and hasattr(t.get("timestamp"), "strftime")
        ]
        cap_col, _ = st.columns([1, 3])
        with cap_col:
            st.caption("LAST MINUTES")
        if trade_rows:
            chart_df = pd.DataFrame(trade_rows)
            tea_mode  = st.toggle("TEA", key="libro_tea_mode", value=False)

            if tea_mode:
                df_tea = chart_df.dropna(subset=["TEA"])
                if not df_tea.empty:
                    line = (
                        alt.Chart(df_tea)
                        .mark_line(point=True)
                        .encode(
                            x=alt.X("Hora:T", title=None, axis=alt.Axis(format="%H:%M:%S")),
                            y=alt.Y("TEA:Q",   scale=alt.Scale(zero=False), title=None,
                                    axis=alt.Axis(format=".1%")),
                        )
                    )
                    st.altair_chart(
                        line.properties(height=_HOURLY_HEIGHT).interactive(),
                        use_container_width=True,
                    )
                else:
                    st.caption("Sin TEA en los últimos trades.")
            else:
                vwap_val = metrics.get("vwap", 0) or 0
                line = (
                    alt.Chart(chart_df)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("Hora:T", title=None, axis=alt.Axis(format="%H:%M:%S")),
                        y=alt.Y("Precio:Q", scale=alt.Scale(zero=False), title=None),
                    )
                )
                vwap_rule = (
                    alt.Chart(pd.DataFrame({"vwap": [vwap_val]}))
                    .mark_rule(color="#00cc66", strokeWidth=1.5, strokeDash=[6, 3])
                    .encode(y=alt.Y("vwap:Q"))
                )
                st.altair_chart(
                    alt.layer(line, vwap_rule).properties(height=_HOURLY_HEIGHT).interactive(),
                    use_container_width=True,
                )
        else:
            st.caption("Sin trades recientes.")

    with col_vp:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        trades_hoy = list(db["TimeSales"].find(
            {"ticker": ticker, "timestamp": {"$gte": today_start}},
            {"price": 1, "money": 1, "_id": 0},
        ))
        st.caption("VOLUME PROFILE")
        if trades_hoy:
            df_vp = pd.DataFrame(trades_hoy)
            df_vp = df_vp[(df_vp["price"] > 0) & (df_vp["money"] > 0)]
            if not df_vp.empty and len(df_vp) >= 2:
                price_range = df_vp["price"].max() - df_vp["price"].min()
                if price_range > 0:
                    raw_tick = price_range / 25
                    magnitude = 10 ** math.floor(math.log10(raw_tick))
                    normalized = raw_tick / magnitude
                    if normalized < 1.5:
                        nice = 1
                    elif normalized < 3.5:
                        nice = 2
                    elif normalized < 7.5:
                        nice = 5
                    else:
                        nice = 10
                    tick = round(nice * magnitude, 10)
                else:
                    tick = 0.01
                df_vp["bucket"] = (df_vp["price"] / tick).round() * tick
                df_vp["bucket"] = df_vp["bucket"].round(10)
                df_agg = (
                    df_vp.groupby("bucket", as_index=False)["money"]
                    .sum()
                    .rename(columns={"bucket": "price_mid"})
                    .sort_values("price_mid")
                )
                vp_chart = (
                    alt.Chart(df_agg)
                    .mark_bar(color="#4c9be8", opacity=0.85)
                    .encode(
                        x=alt.X("price_mid:Q", title=None, axis=alt.Axis(format=",.2f")),
                        y=alt.Y("money:Q",      title=None, axis=alt.Axis(format=",.0f")),
                        tooltip=[
                            alt.Tooltip("price_mid:Q", title="Precio",  format=",.2f"),
                            alt.Tooltip("money:Q",      title="Money",   format=",.0f"),
                        ],
                    )
                    .properties(height=_HOURLY_HEIGHT)
                    .interactive()
                )
                st.altair_chart(vp_chart, use_container_width=True)
            else:
                st.caption("Pocos datos para graficar.")
        else:
            st.caption("Sin trades hoy.")


_MESES_ES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}


def _fmt_plazo(fecha_str):
    from datetime import date as _date
    d = _date.fromisoformat(fecha_str[:10])
    return f"{_MESES_ES[d.month]} {str(d.year)[2:]}"


def _tabla_breakevens(pares):
    """Convierte lista de pares a DataFrame compacto para st.dataframe."""
    rows = []
    for p in pares:
        bkv     = p.get("breakeven_mensual")
        tem     = p.get("tem_lecap")
        tea_cer = p.get("tea_cer")
        par     = p.get("paridad_cer")
        rows.append({
            "Lecap":       p["lecap"],
            "CER":         p["cer"],
            "Plazo":       _fmt_plazo(p["fecha_vencimiento"]),
            "TEM Lecap":   f"{tem * 100:.2f}%" if tem is not None else "—",
            "TEA CER":     f"{tea_cer * 100:.2f}%" if tea_cer is not None else "—",
            "Paridad":     f"{par:.1f}%" if par is not None else "—",
            "BE mensual":  f"{bkv * 100:.2f}%" if bkv is not None else "—",
        })
    return pd.DataFrame(rows)


def _chart_breakevens(pares):
    """Scatter + línea de breakeven mensual por fecha de vencimiento."""
    import altair as alt
    rows = [
        {
            "fecha":      p["fecha_vencimiento"],
            "be_pct":     round(p["breakeven_mensual"] * 100, 4),
            "label":      p["lecap"],
        }
        for p in pares if p.get("breakeven_mensual") is not None
    ]
    if not rows:
        return None

    df = pd.DataFrame(rows)
    df["fecha"] = pd.to_datetime(df["fecha"])

    base = alt.Chart(df).encode(
        x=alt.X("fecha:T", title="Vencimiento", axis=alt.Axis(format="%b %y", labelAngle=-45)),
        y=alt.Y("be_pct:Q", title="BE mensual (%)", scale=alt.Scale(zero=False)),
    )

    line   = base.mark_line(color="#4C9BE8", strokeWidth=1.5)
    points = base.mark_point(color="#4C9BE8", size=80, filled=True)
    labels = base.mark_text(dy=-12, fontSize=10, color="#cccccc").encode(text="label:N")

    return alt.layer(line, points, labels).properties(height=320)


def _resumen_breakevens(pares):
    """Devuelve (df_tramos, bkv_ponderado) para mostrar debajo de la tabla principal."""
    tramos = {"Corto (<90d)": [], "Medio (90-180d)": [], "Largo (>180d)": []}
    pesos_total, bkv_pond = 0.0, 0.0

    for p in pares:
        bkv  = p.get("breakeven_mensual")
        dias = p.get("dias", 0)
        if bkv is None or dias <= 0:
            continue

        # Promedio ponderado global (peso = días)
        bkv_pond    += bkv * dias
        pesos_total += dias

        if dias < 90:
            tramos["Corto (<90d)"].append(bkv)
        elif dias <= 180:
            tramos["Medio (90-180d)"].append(bkv)
        else:
            tramos["Largo (>180d)"].append(bkv)

    rows = []
    for nombre, vals in tramos.items():
        if vals:
            rows.append({
                "Tramo":              nombre,
                "Pares":              len(vals),
                "BE promedio":        f"{(sum(vals)/len(vals))*100:.2f}%",
            })

    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    pond = (bkv_pond / pesos_total) if pesos_total > 0 else None
    return df, pond


@st.cache_data(ttl=60, show_spinner=False)
def _cargar_breakevens_historico():
    db = get_db()
    return list(db["BreakevensHistorico"].find(
        {},
        {"fecha": 1, "pares": 1, "_id": 0}
    ))


def _render_breakevens(db):
    from datetime import date as _date

    tab_live, tab_hist, tab_grafico, tab_simulador = st.tabs(["Tiempo Real", "Histórico", "Gráfico", "Simulador"])

    def _render_pares(pares):
        col_tbl, col_chart = st.columns([4, 5])
        with col_tbl:
            st.dataframe(
                _tabla_breakevens(pares),
                hide_index=True,
                use_container_width=True,
                height=df_height(len(pares)),
            )
            df_tramos, bkv_pond = _resumen_breakevens(pares)
            if bkv_pond is not None:
                st.metric("BE ponderado curva", f"{bkv_pond * 100:.2f}%")
            if not df_tramos.empty:
                st.dataframe(df_tramos, hide_index=True, use_container_width=True)
        with col_chart:
            chart = _chart_breakevens(pares)
            if chart:
                st.altair_chart(chart, use_container_width=True)

    with tab_live:
        doc = db["BreakevensLive"].find_one({"_id": "breakevens"})
        if not doc:
            st.info("Sin datos. ¿El motor de breakevens está corriendo?")
        else:
            updated = doc.get("updated_at")
            if updated:
                last_update_badge(updated)
            pares = doc.get("pares", [])
            if pares:
                _render_pares(pares)
            else:
                st.info("Motor activo pero sin pares calculados aún.")

    with tab_hist:
        fechas = sorted(
            [d["fecha"] for d in db["BreakevensHistorico"].find({}, {"fecha": 1, "_id": 0})],
            reverse=True,
        )
        if not fechas:
            st.info("Sin historial disponible aún.")
        else:
            fecha_sel = st.select_slider("Fecha", options=fechas, key="bkv_fecha_slider")
            doc_hist = db["BreakevensHistorico"].find_one({"fecha": fecha_sel})
            if doc_hist:
                pares = doc_hist.get("pares", [])
                if pares:
                    _render_pares(pares)

    with tab_grafico:
        docs_hist = _cargar_breakevens_historico()
        if not docs_hist:
            st.info("Sin historial disponible aún.")
            return

        # Pares disponibles desde el doc más reciente, ordenados por vencimiento
        doc_ref = max(docs_hist, key=lambda d: d["fecha"])
        pares_ref = doc_ref.get("pares", [])

        def _fmt_fecha(s):
            try:
                return _date.fromisoformat(s[:10]).strftime("%d/%m/%y")
            except Exception:
                return s[:10] if s else ""

        lecap_info = {}
        for p in pares_ref:
            lecap = p.get("lecap")
            if not lecap:
                continue
            lecap_info[lecap] = {
                "fecha_vto": p.get("fecha_vencimiento", ""),
                "dias": p.get("dias", 0),
            }

        # label = "dd/mm/yy · Nd · lecap"
        label_to_lecap = {}
        for lecap, info in sorted(lecap_info.items(), key=lambda kv: kv[1]["fecha_vto"]):
            label = f"{_fmt_fecha(info['fecha_vto'])} · {info['dias']}d · {lecap}"
            label_to_lecap[label] = lecap

        labels_disp = list(label_to_lecap.keys())
        if not labels_disp:
            st.info("Sin plazos disponibles.")
            return

        labels_sel = st.multiselect(
            "Plazos", labels_disp,
            default=labels_disp[:2] if len(labels_disp) >= 2 else labels_disp,
            key="bkv_lecaps_grafico",
        )
        if not labels_sel:
            st.info("Seleccioná al menos un plazo.")
            return

        lecap_to_label = {v: k for k, v in label_to_lecap.items()}
        lecaps_sel = {label_to_lecap[l] for l in labels_sel}

        rows = []
        for doc in docs_hist:
            fecha = doc["fecha"]
            for p in doc.get("pares", []):
                lecap = p.get("lecap")
                bkv = p.get("breakeven_mensual")
                if lecap in lecaps_sel and bkv is not None:
                    rows.append({"fecha": fecha, "plazo": lecap_to_label[lecap], "breakeven": bkv * 100})

        if not rows:
            st.info("Sin datos para los plazos seleccionados.")
            return

        df_bkv = pd.DataFrame(rows).sort_values("fecha")
        fechas_ord = sorted(df_bkv["fecha"].unique())
        chart = (
            alt.Chart(df_bkv)
            .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=40))
            .encode(
                x=alt.X("fecha:O", title="Fecha", sort=fechas_ord,
                        axis=alt.Axis(labelAngle=-45)),
                y=alt.Y("breakeven:Q", title="Breakeven mensual (%)",
                        axis=alt.Axis(format=".2f"),
                        scale=alt.Scale(zero=False)),
                color=alt.Color("plazo:N",
                                scale=alt.Scale(scheme="tableau10"),
                                legend=alt.Legend(orient="top")),
                tooltip=[
                    alt.Tooltip("fecha:O", title="Fecha"),
                    alt.Tooltip("plazo:N", title="Plazo"),
                    alt.Tooltip("breakeven:Q", title="BE mensual (%)", format=".3f"),
                ],
            )
            .properties(height=420)
        )
        st.altair_chart(chart, use_container_width=True)

    with tab_simulador:
        _render_simulador(db)


@st.cache_data(ttl=60, show_spinner=False)
def _cargar_datos_simulador():
    """Carga datos necesarios para el simulador: curvas, CER, días hábiles, últimos precios."""
    db = get_db()
    curvas = list(db["Curvas"].find({}))
    cer_docs = list(db["CER"].find({}, {"fecha": 1, "valor": 1, "_id": 0}))
    dias_habiles = sorted(d["fecha"] for d in db["DiasHabiles"].find({}, {"fecha": 1, "_id": 0}))
    # último precio por ticker desde MarketSnapshot
    snaps = list(db["MarketSnapshot"].find({}, {"ticker": 1, "metrics": 1, "_id": 0}))
    last_price = {}
    for s in snaps:
        t = s.get("ticker")
        p = (s.get("metrics") or {}).get("last_price")
        if t and p:
            last_price[t] = float(p)
    cer_dict = {d["fecha"]: float(d["valor"]) for d in cer_docs}
    return curvas, cer_dict, dias_habiles, last_price


def _render_simulador(db):
    from datetime import date as _date
    from datetime import timedelta

    doc_bkv = db["BreakevensLive"].find_one({"_id": "breakevens"})
    if not doc_bkv or not doc_bkv.get("pares"):
        st.info("Sin pares de breakevens. ¿El motor está corriendo?")
        return

    curvas_list, cer_dict, dias_habiles, last_price = _cargar_datos_simulador()
    if not curvas_list or not cer_dict or not dias_habiles:
        st.info("Faltan datos de referencia (Curvas / CER / DiasHabiles).")
        return

    curvas_por_corto = {d.get("ticker_corto"): d for d in curvas_list if d.get("ticker_corto")}

    # ── Settlement de hoy y CER liquidación ───────────────────────────────────
    def _siguiente_habil(fecha_d):
        s = fecha_d.isoformat()
        for f in dias_habiles:
            if f > s:
                return _date.fromisoformat(f)
        return None

    def _retroceder_n_habiles(fecha_d, n):
        s = fecha_d.isoformat()
        idx = None
        for i, f in enumerate(dias_habiles):
            if f <= s:
                idx = i
        if idx is None or idx < n:
            return None
        return _date.fromisoformat(dias_habiles[idx - n])

    def _cer_en_fecha(fecha_d):
        for i in range(7):
            key = (fecha_d - timedelta(days=i)).isoformat()
            if key in cer_dict:
                return cer_dict[key]
        return None

    def _monto_flujo_cer(f, vn=100):
        amort = float(f.get("amortizacion_pct", 0)) / 100 * vn
        if "cupon_sobre_residual" in f:
            cupon = (float(f.get("cupon_sobre_residual", 0))
                     * float(f.get("residual_previo_pct", 0)) / 100 * vn)
        else:
            cupon = float(f.get("cupon_anual", 0)) * vn
        return amort + cupon

    def _fecha_flujo(f):
        v = f.get("fecha")
        if isinstance(v, str):
            try:
                return _date.fromisoformat(v[:10])
            except Exception:
                return None
        return None

    hoy = _date.today()
    settlement_hoy = _siguiente_habil(hoy)
    if not settlement_hoy:
        st.warning("No se pudo calcular settlement T+1.")
        return
    fecha_cer_liq = _retroceder_n_habiles(settlement_hoy, 10)
    if not fecha_cer_liq:
        st.warning("No se pudo calcular fecha de CER liquidación.")
        return
    cer_liq = _cer_en_fecha(fecha_cer_liq)
    if not cer_liq:
        st.warning("Sin valor de CER para la fecha de liquidación.")
        return

    # ── Input de escenarios ───────────────────────────────────────────────────
    col_info, col_input = st.columns([1, 2])
    with col_info:
        st.markdown(
            f"<div style='font-size:12px;color:#888'>"
            f"Settlement T+1: <b>{settlement_hoy.strftime('%d/%m/%y')}</b> · "
            f"CER liq (<b>{fecha_cer_liq.strftime('%d/%m/%y')}</b>): <b>{cer_liq:,.4f}</b>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with col_input:
        escenarios_str = st.text_input(
            "Escenarios de inflación mensual (%)",
            value="2.0, 2.5, 3.0, 3.2, 3.5",
            key="sim_escenarios",
        )

    try:
        escenarios = [float(x.strip()) / 100 for x in escenarios_str.split(",") if x.strip()]
        escenarios = sorted(set(escenarios))
    except ValueError:
        st.error("Escenarios inválidos. Usá números separados por coma (ej: 2.0, 2.5, 3.0).")
        return

    if not escenarios:
        st.info("Ingresá al menos un escenario.")
        return

    # ── Calcular filas por par ────────────────────────────────────────────────
    filas = []
    for par in doc_bkv["pares"]:
        lecap_corto = par.get("lecap")
        cer_corto = par.get("cer")
        if not lecap_corto or not cer_corto:
            continue
        lecap_doc = curvas_por_corto.get(lecap_corto)
        cer_doc = curvas_por_corto.get(cer_corto)
        if not lecap_doc or not cer_doc:
            continue

        precio_lecap = last_price.get(lecap_doc.get("ticker"))
        precio_cer = last_price.get(cer_doc.get("ticker"))
        flujo_lecap = lecap_doc.get("flujo_vencimiento")
        cer_emision = cer_doc.get("cer_emision")
        if not (precio_lecap and precio_cer and flujo_lecap and cer_emision):
            continue

        ret_lecap = flujo_lecap / precio_lecap - 1

        flujos_cer = cer_doc.get("flujos") or []
        flujos_pendientes = []
        for f in flujos_cer:
            fd = _fecha_flujo(f)
            monto_vn = _monto_flujo_cer(f, float(cer_doc.get("valor_nominal", 100)))
            if fd and fd > settlement_hoy and monto_vn > 0:
                flujos_pendientes.append((fd, monto_vn))

        if not flujos_pendientes:
            continue

        fila = {
            "Par": f"{lecap_corto} · {cer_corto}",
            "Vto": par.get("fecha_vencimiento", "")[:10],
            "Días": par.get("dias", 0),
            "Ret. Lecap": ret_lecap,
            "BE mensual": par.get("breakeven_mensual"),
        }

        for infl in escenarios:
            flujo_cer_est = 0.0
            for fd, monto_vn in flujos_pendientes:
                meses = (fd - fecha_cer_liq).days / 30.0
                cer_k_est = cer_liq * (1 + infl) ** meses
                flujo_cer_est += monto_vn * (cer_k_est / cer_emision)
            ret_cer = flujo_cer_est / precio_cer - 1
            pnl = ret_cer - ret_lecap
            fila[f"{infl * 100:.1f}%"] = pnl

        filas.append(fila)

    if not filas:
        st.info("No hay pares con datos completos para simular.")
        return

    df_sim = pd.DataFrame(filas).sort_values("Días").reset_index(drop=True)

    # ── Render tabla con formato y color ──────────────────────────────────────
    escenarios_cols = [f"{i * 100:.1f}%" for i in escenarios]

    def _fmt_pnl(v):
        if v is None or pd.isna(v):
            return ""
        bps = v * 10000
        signo = "+" if bps >= 0 else ""
        return f"{signo}{bps:,.0f} bps"

    def _color_pnl(v):
        if v is None or pd.isna(v):
            return ""
        if v > 0:
            return "color:#2ea043;font-weight:600"
        if v < 0:
            return "color:#e66767;font-weight:600"
        return ""

    def _fmt_pct(v):
        return f"{v * 100:.2f}%" if v is not None and not pd.isna(v) else ""

    styled = (
        df_sim.style
        .format({"Ret. Lecap": _fmt_pct, "BE mensual": _fmt_pct,
                 **{c: _fmt_pnl for c in escenarios_cols}})
        .map(_color_pnl, subset=escenarios_cols)
    )

    st.dataframe(styled, hide_index=True, use_container_width=True,
                 height=df_height(len(df_sim)))

    st.caption(
        "P&L = Retorno CER − Retorno Lecap bajo el escenario. "
        "Verde: CER le gana a Lecap. Rojo: Lecap le gana a CER. "
        "El BE mensual debería caer entre los dos escenarios donde cambia el signo."
    )


def _render_curva_rendimiento(db):
    from datetime import date as _date

    import altair as alt

    curvas_disp = sorted(db["ForwardsHistorico"].distinct("curva"))
    if not curvas_disp:
        st.info("Sin datos históricos de curvas.")
        return

    col1, col2, col3 = st.columns([2, 2, 2])
    with col1:
        curva_sel = st.selectbox("Curva", curvas_disp, key="curva_rend_sel")
    with col2:
        tipo_fit = st.radio("Ajuste", ["Logarítmico", "Polinomial grado 2"], horizontal=True, key="curva_fit_tipo")
    with col3:
        if curva_sel == "tasa_fija":
            metrica = st.radio("Métrica", ["TEA", "TEM"], horizontal=True, key="curva_metrica")
        else:
            metrica = "TEA"

    fechas = sorted([
        d["fecha"] for d in db["ForwardsHistorico"].find(
            {"curva": curva_sel}, {"fecha": 1, "_id": 0}
        )
    ], reverse=True)

    if not fechas:
        st.info("Sin datos para esta curva.")
        return

    fecha_sel = st.select_slider("Fecha", options=fechas, key="curva_fecha_slider")

    doc = db["ForwardsHistorico"].find_one({"curva": curva_sel, "fecha": fecha_sel})
    if not doc:
        return

    tasas = doc.get("tasas", {})
    fecha_ref = _date.fromisoformat(fecha_sel)

    meta_map = {d["ticker_corto"]: d for d in db["Curvas"].find({"curva": curva_sel})}

    puntos = []
    for ticker_corto, tea in tasas.items():
        meta = meta_map.get(ticker_corto)
        if not meta or not meta.get("fecha_vencimiento"):
            continue
        try:
            fecha_vto = _date.fromisoformat(meta["fecha_vencimiento"][:10])
        except Exception:
            continue
        dur = (fecha_vto - fecha_ref).days / 365.0
        if dur <= 0:
            continue
        tea_pct = tea * 100
        tem_pct = ((1 + tea) ** (1 / 12) - 1) * 100
        valor_y = tem_pct if metrica == "TEM" else tea_pct
        puntos.append({"Ticker": ticker_corto, "Duration": round(dur, 4), metrica: round(valor_y, 4)})

    if len(puntos) < 2:
        st.info("Menos de 2 instrumentos con datos para esta curva y fecha.")
        return

    df_pts = pd.DataFrame(puntos).sort_values("Duration")

    x = df_pts["Duration"].values
    y = df_pts[metrica].values

    try:
        if tipo_fit == "Logarítmico":
            coeffs = np.polyfit(np.log(x), y, 1)
            x_fit = np.linspace(x.min(), x.max(), 200)
            y_fit = coeffs[0] * np.log(x_fit) + coeffs[1]
        else:
            coeffs = np.polyfit(x, y, 2)
            x_fit = np.linspace(x.min(), x.max(), 200)
            y_fit = np.polyval(coeffs, x_fit)
        df_fit = pd.DataFrame({"Duration": x_fit, metrica: y_fit})
    except Exception:
        df_fit = None

    # Eje Y dinámico: incluye puntos + línea de fit, padding absoluto
    all_y = list(y)
    if df_fit is not None:
        all_y += list(df_fit[metrica].values)
    rango = max(all_y) - min(all_y)
    padding = rango * 0.10 if rango > 0 else 1.0
    y_min = min(all_y) - padding
    y_max = max(all_y) + padding
    y_fmt = ".1f" if curva_sel == "cer" else ".2f"
    y_title = f"{metrica} (%)"

    y_scale = alt.Scale(domain=[y_min, y_max], zero=False)

    puntos_chart = (
        alt.Chart(df_pts)
        .mark_circle(size=80, color="#00cc66")
        .encode(
            x=alt.X("Duration:Q", title="Duration (años)"),
            y=alt.Y(f"{metrica}:Q", title=y_title, scale=y_scale,
                    axis=alt.Axis(format=y_fmt)),
            tooltip=["Ticker:N",
                     alt.Tooltip("Duration:Q", format=".2f"),
                     alt.Tooltip(f"{metrica}:Q", format=y_fmt)],
        )
    )

    labels_chart = (
        alt.Chart(df_pts)
        .mark_text(dy=-12, fontSize=11, color="#aaa")
        .encode(
            x="Duration:Q",
            y=alt.Y(f"{metrica}:Q", scale=y_scale),
            text="Ticker:N",
        )
    )

    chart = puntos_chart + labels_chart

    if df_fit is not None:
        fit_chart = (
            alt.Chart(df_fit)
            .mark_line(color="#4488ff", strokeWidth=2)
            .encode(
                x="Duration:Q",
                y=alt.Y(f"{metrica}:Q", scale=y_scale),
            )
        )
        chart = chart + fit_chart

    st.altair_chart(chart.properties(height=420), use_container_width=True)


def vista_mercado():
    db = get_db()

    st.markdown("## ACAQuant | Mercado")

    tab_mercado, tab_libro, tab_curvas, tab_breakevens, tab_forwards, tab_retorno, tab_vol, tab_estrategias = st.tabs(["Mercado", "Libro", "Curvas", "Breakevens", "Forwards", "Retorno Total", "Volúmenes", "Estrategias"])

    with tab_mercado:
        @st.fragment(run_every=30)
        def _tab_mercado_live():
            all_snaps = list(db["MarketSnapshot"].find({}))

            # ── Metadata de curvas ────────────────────────────────────────────
            curvas_docs = list(db["Curvas"].find(
                {}, {"ticker": 1, "curva": 1, "fecha_vencimiento": 1, "_id": 0}
            ))
            curvas_meta = {d["ticker"]: d for d in curvas_docs if d.get("ticker")}

            CURVA_LABELS = {"tasa_fija": "Tasa Fija", "cer": "CER"}
            curvas_raw   = sorted({d.get("curva", "") for d in curvas_docs if d.get("curva")})
            opciones     = ["Todas"] + [CURVA_LABELS.get(c, c) for c in curvas_raw]
            raw_map      = {CURVA_LABELS.get(c, c): c for c in curvas_raw}

            # ── Header: filtro (izq) + última actualización (der) ────────────
            col_fil, col_badge = st.columns([1, 3])
            with col_fil:
                filtro_label = st.selectbox(
                    "curva", opciones, key="merc_filtro_curva",
                    label_visibility="collapsed"
                )
            with col_badge:
                if all_snaps:
                    ultimo_ts = max(
                        (s.get("updated_at") for s in all_snaps if s.get("updated_at")),
                        default=None
                    )
                    if ultimo_ts:
                        ts_art = ultimo_ts - timedelta(hours=3)
                        badge = f"Última actualización: {ts_art.strftime('%H:%M:%S')} · auto 30s"
                    else:
                        badge = "auto 30s"
                else:
                    badge = "auto 30s"
                st.markdown(
                    f"<div style='text-align:right;color:gray;font-size:0.8em;padding-top:8px'>"
                    f"{badge}</div>",
                    unsafe_allow_html=True
                )

            # ── Enriquecimiento TEA/Duration desde TimeSales ─────────────────
            curvas_tickers = list(curvas_meta.keys())
            enriched = {}
            if curvas_tickers:
                pipeline = [
                    {"$match": {"ticker": {"$in": curvas_tickers}, "duration": {"$exists": True}}},
                    {"$sort": {"timestamp": -1}},
                    {"$group": {
                        "_id":      "$ticker",
                        "TEA":      {"$first": "$TEA"},
                        "TEM":      {"$first": "$TEM"},
                        "duration": {"$first": "$duration"},
                        "paridad":  {"$first": "$paridad"},
                    }},
                ]
                for r in db["TimeSales"].aggregate(pipeline):
                    enriched[r["_id"]] = r

            # ── Filtro y orden ────────────────────────────────────────────────
            all_snaps.sort(
                key=lambda s: s.get("metrics", {}).get("total_money", 0) or 0,
                reverse=True
            )
            if filtro_label != "Todas":
                curva_sel       = raw_map.get(filtro_label, filtro_label)
                tickers_filtro  = {t for t, d in curvas_meta.items() if d.get("curva") == curva_sel}
                snaps_show      = [s for s in all_snaps if s.get("ticker") in tickers_filtro]
            else:
                snaps_show = all_snaps

            # ── Tablas side by side ───────────────────────────────────────────
            col_main, col_tramo = st.columns([3, 2])
            with col_main:
                render_mercado_table(snaps_show, enriched)
            with col_tramo:
                render_tramo_vol(snaps_show, enriched)

        _tab_mercado_live()

    with tab_libro:
        @st.fragment(run_every=2)
        def _tab_libro_live():
            vista_libro()
        _tab_libro_live()

    with tab_curvas:
        _render_curva_rendimiento(db)

    with tab_breakevens:
        _render_breakevens(db)

    with tab_forwards:
        @st.fragment(run_every=30)
        def _tab_forwards_live():
            _render_forwards(get_db(), key_prefix="fwd_mercado")
        _tab_forwards_live()

    with tab_retorno:
        _render_retorno_total(key_prefix="rt_mercado")

    with tab_vol:
        _render_volumenes()

    with tab_estrategias:
        _render_estrategias()


# ══════════════════════════════════════════════════════════════════════════════
# MERCADO → ESTRATEGIAS
# ══════════════════════════════════════════════════════════════════════════════

def _carry_rolldown_data():
    """
    Retorna lista de dicts con carry, roll-down y retorno total esperado a 30 días
    para cada bono de la curva tasa_fija con datos enriquecidos disponibles.

    Algoritmo:
      - Carry = TEM (rendimiento cierto a 30 días de un cupón-cero)
      - Roll-down: interpolación lineal de la curva TEM vs duration para encontrar
        la TEM en (duration_actual - 30/365). Luego:
        roll_down ≈ duration_actual × (TEM_actual − TEM_futura)
      - Retorno_total = carry + roll_down
    """
    db = get_db()

    # 1. Metadata de curvas — solo tasa_fija
    curvas_docs = list(db["Curvas"].find(
        {"curva": "tasa_fija"},
        {"ticker": 1, "ticker_corto": 1, "fecha_vencimiento": 1, "_id": 0}
    ))
    if not curvas_docs:
        return []
    tickers_tf = [d["ticker"] for d in curvas_docs if d.get("ticker")]
    curva_meta = {d["ticker"]: d for d in curvas_docs}

    # 2. Último trade enriquecido por ticker
    pipeline = [
        {"$match": {"ticker": {"$in": tickers_tf}, "TEM": {"$exists": True}, "duration": {"$exists": True}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id":      "$ticker",
            "TEM":      {"$first": "$TEM"},
            "TEA":      {"$first": "$TEA"},
            "duration": {"$first": "$duration"},
            "price":    {"$first": "$price"},
        }},
    ]
    enriched = {r["_id"]: r for r in db["TimeSales"].aggregate(pipeline)}
    if not enriched:
        return []

    # 3. Construir curva: lista de (duration, TEM) ordenada por duration
    curva_pts = sorted(
        [(enriched[t]["duration"], enriched[t]["TEM"], t) for t in enriched],
        key=lambda x: x[0]
    )
    durations = [p[0] for p in curva_pts]
    tems      = [p[1] for p in curva_pts]

    def _interp_tem(d_target):
        """Interpolación lineal de TEM en d_target. Extrapolación flat en extremos."""
        if d_target <= durations[0]:
            return tems[0]
        if d_target >= durations[-1]:
            return tems[-1]
        for i in range(len(durations) - 1):
            if durations[i] <= d_target <= durations[i + 1]:
                w = (d_target - durations[i]) / (durations[i + 1] - durations[i])
                return tems[i] + w * (tems[i + 1] - tems[i])
        return tems[-1]

    # 4. Calcular carry + roll-down para cada bono
    DIAS_30 = 30 / 365
    rows = []
    for ticker, data in enriched.items():
        tem      = data["TEM"]
        dur      = data["duration"]
        tea      = data.get("TEA")
        price    = data.get("price")
        meta     = curva_meta.get(ticker, {})
        vto      = meta.get("fecha_vencimiento", "")
        nombre   = meta.get("ticker_corto", ticker)

        carry    = tem
        dur_fut  = max(dur - DIAS_30, 0.0)
        tem_fut  = _interp_tem(dur_fut)
        rolldown = dur * (tem - tem_fut)
        total    = carry + rolldown

        rows.append({
            "Ticker":       nombre or ticker,
            "Duration":     dur,
            "TEM":          tem,
            "Carry":        carry,
            "Roll-Down":    rolldown,
            "Retorno Total": total,
            "TEA":          tea,
            "Precio":       price,
            "Vencimiento":  vto,
        })

    rows.sort(key=lambda r: r["Retorno Total"], reverse=True)
    return rows


def _render_carry_rolldown():
    rows = _carry_rolldown_data()
    if not rows:
        st.info("Sin datos enriquecidos disponibles. El motor de curvas debe estar activo.")
        return

    import pandas as _pd

    df = _pd.DataFrame(rows)

    # Tabla principal
    df_disp = df[["Ticker", "Vencimiento", "Duration", "TEM", "Carry", "Roll-Down", "Retorno Total"]].copy()
    for col in ["TEM", "Carry", "Roll-Down", "Retorno Total"]:
        df_disp[col] = df_disp[col].map(lambda v: f"{v*100:.3f}%" if v is not None else "-")
    df_disp["Duration"] = df_disp["Duration"].map(lambda v: f"{v:.3f}")

    st.dataframe(df_disp, hide_index=True, use_container_width=True,
                 height=df_height(len(df_disp), max_h=600))

    # Gráfico: Carry vs Roll-Down por bono, ordenado por duration
    df_chart = df.copy()
    df_melt = _pd.melt(
        df_chart[["Ticker", "Duration", "Carry", "Roll-Down"]],
        id_vars=["Ticker", "Duration"],
        value_vars=["Carry", "Roll-Down"],
        var_name="Componente",
        value_name="Rendimiento",
    )
    df_melt = df_melt.sort_values("Duration")
    tickers_orden = df_chart.sort_values("Duration")["Ticker"].tolist()

    bars = alt.Chart(df_melt).mark_bar().encode(
        x=alt.X("Ticker:N", sort=tickers_orden, axis=alt.Axis(labelAngle=-35, title=None)),
        y=alt.Y("Rendimiento:Q", axis=alt.Axis(format=".2%", title="Rendimiento 30d")),
        color=alt.Color(
            "Componente:N",
            scale=alt.Scale(domain=["Carry", "Roll-Down"], range=["#1F3864", "#5B9BD5"]),
            legend=alt.Legend(title=None, orient="top"),
        ),
        tooltip=["Ticker:N", "Componente:N", alt.Tooltip("Rendimiento:Q", format=".3%")],
    ).properties(height=280)

    st.altair_chart(bars, use_container_width=True)


def _render_estrategias():
    tab_crd, tab_vr, tab_fly = st.tabs([
        "Carry + Roll-Down",
        "Valor Relativo",
        "Butterfly",
    ])

    with tab_crd:
        _render_carry_rolldown()

    with tab_vr:
        st.info("Módulo Valor Relativo — próximamente.")

    with tab_fly:
        st.info("Módulo Butterfly Scanner — próximamente.")


def render_forward_matrix(doc):
    tickers  = doc.get("tickers", [])   # ordenados por maturity ascendente
    matrix   = doc.get("matrix", {})
    ts       = doc.get("updated_at")

    if len(tickers) < 2:
        st.info("Menos de 2 instrumentos con TEA disponible.")
        return

    if ts:
        st.caption(f"Última actualización: {ts.strftime('%d/%m/%Y %H:%M:%S')}")

    st.caption("MATRIZ DE TASAS FORWARD (TEA)")

    # Construir DataFrame NxN
    # Filas = instrumento largo, Columnas = instrumento corto
    data = {}
    for t_largo in tickers:
        row = {}
        for t_corto in tickers:
            val = matrix.get(t_largo, {}).get(t_corto)
            row[t_corto] = val
        data[t_largo] = row

    df = pd.DataFrame(data, index=tickers).T.astype(float)

    def fmt_cell(v):
        if pd.isna(v):
            return ""
        return f"{v:.2%}"

    # Escala rojo → amarillo → verde centrada en la mediana
    todos_vals = [v for row in data.values() for v in row.values() if v is not None and not pd.isna(v)]

    def bg_cell(v):
        if pd.isna(v) or not todos_vals:
            return ""
        vmin = min(todos_vals)
        vmax = max(todos_vals)
        p50  = sorted(todos_vals)[len(todos_vals) // 2]
        if vmax == vmin:
            return "background-color: #ffdd00; color: #000"
        # Normalizar: por debajo de p50 → [0, 0.5], por encima → [0.5, 1]
        if v <= p50:
            t = (v - vmin) / (p50 - vmin) * 0.5 if p50 > vmin else 0.5
        else:
            t = 0.5 + (v - p50) / (vmax - p50) * 0.5 if vmax > p50 else 0.5
        t = max(0.0, min(1.0, t))
        # Interpolar rojo(0) → amarillo(0.5) → verde(1)
        if t <= 0.5:
            r = 255
            g = int(t / 0.5 * 221)   # 0 → 221
            b = 0
        else:
            r = int((1 - (t - 0.5) / 0.5) * 255)
            g = int(221 + (t - 0.5) / 0.5 * (204 - 221))
            b = 0
        luminancia = 0.299 * r + 0.587 * g + 0.114 * b
        txt = "#000" if luminancia > 140 else "#fff"
        return f"background-color: rgb({r},{g},{b}); color: {txt}; font-weight: bold"

    styler = (
        df.style
        .format(fmt_cell)
        .map(bg_cell)
    )
    st.dataframe(styler, use_container_width=True, height=df_height(len(tickers) + 1))


@st.cache_data(ttl=60, show_spinner=False)
def _cargar_forwards_historico(curva):
    db = get_db()
    return list(db["ForwardsHistorico"].find(
        {"curva": curva},
        {"fecha": 1, "matrix": 1, "_id": 0}
    ))


def _render_forwards(db, key_prefix="fwd"):
    curvas_live = set(db["ForwardsLive"].distinct("curva"))
    curvas_hist = set(db["ForwardsHistorico"].distinct("curva"))
    curvas_todas = sorted(curvas_live | curvas_hist)

    if not curvas_todas:
        st.info("Sin datos. ¿El motor de forwards está corriendo?")
        return

    curva_sel = st.selectbox("Curva", curvas_todas, key=f"{key_prefix}_curva")

    tab_live, tab_hist, tab_grafico = st.tabs(["Tiempo Real", "Histórico", "Gráfico"])

    with tab_live:
        doc = db["ForwardsLive"].find_one({"curva": curva_sel})
        if doc:
            render_forward_matrix(doc)
        else:
            st.info("Sin datos en tiempo real para esta curva.")

    with tab_hist:
        fechas = sorted([
            d["fecha"] for d in db["ForwardsHistorico"].find(
                {"curva": curva_sel}, {"fecha": 1, "_id": 0}
            )
        ], reverse=True)

        if not fechas:
            st.info("Sin historial disponible aún.")
        else:
            fecha_sel = st.select_slider("Fecha", options=fechas, key=f"{key_prefix}_fecha")
            doc_hist = db["ForwardsHistorico"].find_one(
                {"curva": curva_sel, "fecha": fecha_sel}
            )
            if doc_hist:
                render_forward_matrix(doc_hist)

    with tab_grafico:
        # Cargar todos los docs históricos de esta curva (cacheado 60s)
        docs_hist = _cargar_forwards_historico(curva_sel)
        if not docs_hist:
            st.info("Sin historial disponible aún.")
        else:
            # Construir lista de pares disponibles desde el doc más reciente
            doc_ref = max(docs_hist, key=lambda d: d["fecha"])
            matrix_ref = doc_ref.get("matrix", {})
            pares = []
            for t_largo, inner in matrix_ref.items():
                for t_corto, val in inner.items():
                    if val is not None:
                        pares.append(f"{t_largo} → {t_corto}")
            pares = sorted(pares)

            if not pares:
                st.info("Sin pares disponibles en la matriz.")
            else:
                pares_sel = st.multiselect(
                    "Pares", pares, default=pares[:2] if len(pares) >= 2 else pares,
                    key=f"{key_prefix}_pares"
                )
                if not pares_sel:
                    st.info("Seleccioná al menos un par.")
                else:
                    # Armar DataFrame fecha × par → forward rate
                    rows = []
                    for doc in docs_hist:
                        fecha = doc["fecha"]
                        matrix = doc.get("matrix", {})
                        for par in pares_sel:
                            t_largo, t_corto = par.split(" → ")
                            val = matrix.get(t_largo, {}).get(t_corto)
                            if val is not None:
                                rows.append({"fecha": fecha, "par": par, "forward": val * 100})
                    if not rows:
                        st.info("Sin datos para los pares seleccionados.")
                    else:
                        df_fwd = pd.DataFrame(rows).sort_values("fecha")
                        fechas_ord = sorted(df_fwd["fecha"].unique())
                        chart = (
                            alt.Chart(df_fwd)
                            .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=40))
                            .encode(
                                x=alt.X("fecha:O", title="Fecha", sort=fechas_ord,
                                        axis=alt.Axis(labelAngle=-45)),
                                y=alt.Y("forward:Q", title="Tasa Forward (%)",
                                        axis=alt.Axis(format=".2f")),
                                color=alt.Color("par:N",
                                                scale=alt.Scale(scheme="tableau10"),
                                                legend=alt.Legend(orient="top")),
                                tooltip=[
                                    alt.Tooltip("fecha:O", title="Fecha"),
                                    alt.Tooltip("par:N", title="Par"),
                                    alt.Tooltip("forward:Q", title="Forward (%)", format=".3f"),
                                ],
                            )
                            .properties(height=420)
                        )
                        st.altair_chart(chart, use_container_width=True)


@st.fragment(run_every=30)
def vista_forwards():
    db = get_db()
    st.markdown("## ACAQuant | Forwards")
    _render_forwards(db, key_prefix="fwd_page")


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_tickers_curvas():
    """Tickers en Trading.Curvas con su label corto. DataFrame: ticker, ticker_corto, curva."""
    db = get_db()
    rows = [
        {"ticker": d["ticker"],
         "ticker_corto": d.get("ticker_corto") or d["ticker"],
         "curva": d.get("curva", "")}
        for d in db["Curvas"].find({}, {"ticker": 1, "ticker_corto": 1, "curva": 1})
    ]
    return pd.DataFrame(rows).sort_values("ticker_corto").reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_volumen_diario_tickers(tickers_key: tuple):
    """Suma de money por (fecha, ticker) en los últimos 15 días corridos."""
    if not tickers_key:
        return pd.DataFrame()
    db = get_db()
    fecha_min = datetime.utcnow() - timedelta(days=15)
    pipeline = [
        {"$match": {"ticker": {"$in": list(tickers_key)},
                    "money": {"$gt": 0},
                    "timestamp": {"$gte": fecha_min}}},
        {"$group": {
            "_id": {
                "fecha":  {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
                "ticker": "$ticker",
            },
            "money": {"$sum": "$money"},
        }},
    ]
    rows = [
        {"fecha": r["_id"]["fecha"], "ticker": r["_id"]["ticker"], "money": r["money"]}
        for r in db["TimeSales"].aggregate(pipeline)
    ]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["money_mm"] = df["money"] / 1_000_000
    return df.sort_values(["fecha", "ticker"])


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_precios_intraday(tickers_key: tuple):
    """Serie intradiaria (ticker, timestamp, price) de los últimos 15 días corridos."""
    if not tickers_key:
        return pd.DataFrame()
    db = get_db()
    fecha_min = datetime.utcnow() - timedelta(days=15)
    cursor = db["TimeSales"].find(
        {"ticker": {"$in": list(tickers_key)},
         "price": {"$gt": 0},
         "timestamp": {"$gte": fecha_min}},
        {"_id": 0, "ticker": 1, "timestamp": 1, "price": 1},
    )
    rows = list(cursor)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp")


def _render_volumenes():
    import altair as alt

    meta = _cargar_tickers_curvas()
    if meta.empty:
        st.info("Sin tickers configurados en Trading.Curvas.")
        return

    tickers_disp = meta["ticker_corto"].tolist()
    t2short = dict(zip(meta["ticker"], meta["ticker_corto"]))
    short2t = dict(zip(meta["ticker_corto"], meta["ticker"]))

    col1, _ = st.columns([3, 2])
    with col1:
        default_sel = tickers_disp[: min(4, len(tickers_disp))]
        sel_cortos = st.multiselect(
            "Tickers", tickers_disp, default=default_sel, key="vol_tickers"
        )
    if not sel_cortos:
        st.info("Seleccioná al menos un ticker.")
        return

    sel_tickers = tuple(sorted(short2t[s] for s in sel_cortos))

    with st.spinner("Cargando datos..."):
        df_vol   = _cargar_volumen_diario_tickers(sel_tickers)
        df_price = _cargar_precios_intraday(sel_tickers)

    if df_vol.empty and df_price.empty:
        st.info("Sin datos en los últimos 15 días para los tickers seleccionados.")
        return

    fechas_vol = sorted(df_vol["fecha"].unique()) if not df_vol.empty else []
    ts_min = df_price["timestamp"].min() if not df_price.empty else None
    ts_max = df_price["timestamp"].max() if not df_price.empty else None
    if ts_min is None and fechas_vol:
        ts_min = pd.to_datetime(fechas_vol[0])
        ts_max = pd.to_datetime(fechas_vol[-1]) + pd.Timedelta(days=1)

    fechas_disp = fechas_vol or [
        d.strftime("%Y-%m-%d")
        for d in pd.date_range(ts_min.normalize(), ts_max.normalize(), freq="D")
    ]
    if len(fechas_disp) < 2:
        st.info("Necesitás al menos 2 días de datos.")
        return

    fecha_desde, fecha_hasta = st.select_slider(
        "Período",
        options=fechas_disp,
        value=(fechas_disp[0], fechas_disp[-1]),
        key="vol_rango",
    )
    ts_desde = pd.to_datetime(fecha_desde)
    ts_hasta = pd.to_datetime(fecha_hasta) + pd.Timedelta(days=1)

    df_vol_r = df_vol[
        (df_vol["fecha"] >= fecha_desde) & (df_vol["fecha"] <= fecha_hasta)
    ].copy() if not df_vol.empty else pd.DataFrame()
    df_price_r = df_price[
        (df_price["timestamp"] >= ts_desde) & (df_price["timestamp"] < ts_hasta)
    ].copy() if not df_price.empty else pd.DataFrame()

    if not df_vol_r.empty:
        df_vol_r["ticker_corto"] = df_vol_r["ticker"].map(t2short)
        df_vol_r["fecha_ts"] = pd.to_datetime(df_vol_r["fecha"])
    if not df_price_r.empty:
        df_price_r["ticker_corto"] = df_price_r["ticker"].map(t2short)

    if df_vol_r.empty and df_price_r.empty:
        st.info("Sin datos en el rango seleccionado.")
        return

    color_enc = alt.Color("ticker_corto:N", title="Ticker",
                          legend=alt.Legend(orient="top"))

    layers = []
    if not df_vol_r.empty:
        bars = (
            alt.Chart(df_vol_r)
            .mark_bar(opacity=0.45)
            .encode(
                x=alt.X("fecha_ts:T", title="Fecha", axis=alt.Axis(format="%d-%b")),
                y=alt.Y("money_mm:Q",
                        title="Volumen (MM ARS)",
                        stack=True,
                        axis=alt.Axis(format=",.0f", orient="right")),
                color=color_enc,
                tooltip=[
                    alt.Tooltip("fecha:N", title="Fecha"),
                    alt.Tooltip("ticker_corto:N", title="Ticker"),
                    alt.Tooltip("money_mm:Q", format=",.0f", title="Volumen (MM ARS)"),
                ],
            )
        )
        layers.append(bars)

    if not df_price_r.empty:
        lines = (
            alt.Chart(df_price_r)
            .mark_line(strokeWidth=2)
            .encode(
                x=alt.X("timestamp:T", title="Fecha"),
                y=alt.Y("price:Q", title="Precio",
                        scale=alt.Scale(zero=False),
                        axis=alt.Axis(format=",.2f")),
                color=color_enc,
                tooltip=[
                    alt.Tooltip("timestamp:T", title="Fecha", format="%d-%b %H:%M"),
                    alt.Tooltip("ticker_corto:N", title="Ticker"),
                    alt.Tooltip("price:Q", format=",.2f", title="Precio"),
                ],
            )
        )
        layers.append(lines)

    chart = alt.layer(*layers).resolve_scale(y="independent").properties(height=460)
    st.altair_chart(chart, use_container_width=True)

    if not df_vol_r.empty:
        resumen = (
            df_vol_r.groupby("ticker_corto")["money_mm"]
            .sum()
            .reset_index()
            .rename(columns={"ticker_corto": "Ticker", "money_mm": "Total (MM ARS)"})
            .sort_values("Total (MM ARS)", ascending=False)
            .reset_index(drop=True)
        )
        resumen["Total (MM ARS)"] = resumen["Total (MM ARS)"].apply(lambda v: f"{v:,.0f}")
        st.dataframe(resumen, hide_index=True, use_container_width=True,
                     height=df_height(len(resumen)))


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_precios_diarios_curva(curva):
    """
    Último precio por ticker por día para todos los instrumentos de una curva.
    Retorna DataFrame largo con columnas: fecha (str), ticker (ticker_corto), price.
    """
    db = get_db()
    meta = {
        d["ticker"]: d["ticker_corto"]
        for d in db["Curvas"].find({"curva": curva}, {"ticker": 1, "ticker_corto": 1})
    }
    if not meta:
        return pd.DataFrame()

    pipeline = [
        {"$match": {"ticker": {"$in": list(meta.keys())}, "price": {"$gt": 0}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": {
                "ticker": "$ticker",
                "fecha": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            },
            "price": {"$first": "$price"},
        }},
    ]
    rows = [
        {"fecha": r["_id"]["fecha"], "ticker": meta[r["_id"]["ticker"]], "price": r["price"]}
        for r in db["TimeSales"].aggregate(pipeline)
        if r["_id"]["ticker"] in meta
    ]
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["ticker", "fecha"]).reset_index(drop=True)


def _render_retorno_total(key_prefix="rt"):
    import altair as alt

    db = get_db()
    curvas = sorted(db["Curvas"].distinct("curva"))
    if not curvas:
        st.info("Sin curvas configuradas en Trading.Curvas.")
        return

    curva_sel = st.selectbox("Curva", curvas, key=f"{key_prefix}_curva")

    with st.spinner("Cargando precios históricos..."):
        df_raw = _cargar_precios_diarios_curva(curva_sel)

    if df_raw.empty:
        st.info("Sin datos de precios en TimeSales para esta curva.")
        return

    fechas_ord = sorted(df_raw["fecha"].unique())

    if len(fechas_ord) < 2:
        st.info("Necesitás al menos 2 días de datos para calcular retorno.")
        return

    # ── Selector de rango (desde / hasta) ────────────────────────
    st.caption("El retorno parte de 0% en la fecha de inicio. Ajustá ambos extremos del slider.")
    fecha_base, fecha_fin = st.select_slider(
        "Período (desde → hasta)",
        options=fechas_ord,
        value=(fechas_ord[0], fechas_ord[-1]),
        key=f"{key_prefix}_rango",
    )

    # ── Calcular retorno acumulado en el rango ────────────────────
    df_pivot = df_raw.pivot_table(index="fecha", columns="ticker", values="price", aggfunc="last")
    df_pivot = df_pivot.sort_index()

    df_desde = df_pivot.loc[(df_pivot.index >= fecha_base) & (df_pivot.index <= fecha_fin)].copy()
    base = df_desde.iloc[0]
    df_retorno = (df_desde.div(base) - 1) * 100

    df_long = (
        df_retorno
        .reset_index()
        .melt(id_vars="fecha", var_name="Ticker", value_name="Retorno (%)")
        .dropna(subset=["Retorno (%)"])
    )
    fechas_rango = sorted(df_long["fecha"].unique())

    # ── Gráfico de líneas ─────────────────────────────────────────
    regla_cero = (
        alt.Chart(pd.DataFrame({"y": [0]}))
        .mark_rule(color="#555", strokeWidth=1)
        .encode(y=alt.Y("y:Q"))
    )
    lineas = (
        alt.Chart(df_long)
        .mark_line(point=alt.OverlayMarkDef(size=50))
        .encode(
            x=alt.X("fecha:O", title="Fecha", sort=fechas_rango,
                    axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("Retorno (%):Q", title="Retorno acumulado (%)",
                    axis=alt.Axis(format=".2f")),
            color=alt.Color("Ticker:N", legend=alt.Legend(title="Instrumento")),
            tooltip=[
                alt.Tooltip("fecha:O", title="Fecha"),
                alt.Tooltip("Ticker:N"),
                alt.Tooltip("Retorno (%):Q", format=".2f", title="Retorno (%)"),
            ],
        )
    )
    st.altair_chart(
        (regla_cero + lineas).properties(height=450),
        use_container_width=True,
    )

    # ── Tabla: retorno al último día del rango ────────────────────
    fecha_ultimo = fechas_rango[-1]
    st.markdown(f"#### Retorno acumulado: {fecha_base} → {fecha_ultimo}")

    df_tabla = (
        df_long[df_long["fecha"] == fecha_ultimo][["Ticker", "Retorno (%)"]]
        .copy()
        .sort_values("Retorno (%)", ascending=False)
        .reset_index(drop=True)
    )
    precios_base  = df_desde.iloc[0]
    precios_final = df_desde.loc[fecha_ultimo] if fecha_ultimo in df_desde.index else pd.Series(dtype=float)
    df_tabla["Precio base"]  = df_tabla["Ticker"].map(precios_base).round(4)
    df_tabla["Precio final"] = df_tabla["Ticker"].map(precios_final).round(4)
    df_tabla["Retorno (%)"]  = df_tabla["Retorno (%)"].round(2).astype(str) + "%"

    st.dataframe(df_tabla, hide_index=True, use_container_width=True,
                 height=df_height(len(df_tabla)))


def vista_retorno_total():
    st.markdown("## ACAQuant | Retorno Total")
    _render_retorno_total(key_prefix="rt_page")


