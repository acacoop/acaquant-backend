"""Vista Opciones del dashboard: cadena GGAL, estrategias dinámicas, volúmenes."""
from datetime import datetime, timedelta

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from core.mongo import get_mongo_client_read
from dashboard.shared.db import get_db_opciones, get_meta_col
from dashboard.shared.format import df_height, fmt_vol
from quant.black_scholes import bs_price as _bs_price


@st.cache_data(ttl=5, show_spinner=False)
def _get_options_snapshot():
    return list(get_db_opciones()["OptionsSnapshot"].find({}))


@st.cache_data(ttl=5, show_spinner=False)
def _get_metadata():
    docs = list(get_meta_col().find({"type": {"$in": ["vr_ggal", "config"]}}))
    return {d.get("type"): d for d in docs}


@st.cache_data(ttl=120, show_spinner=False)
def _fetch_estrategia_historico(symbols_key: frozenset, dias: int):
    desde = datetime.utcnow() - timedelta(days=dias)
    return list(get_db_opciones()["Data"].find(
        {"symbol": {"$in": list(symbols_key)}, "timestamp": {"$gte": desde}},
        {"_id": 0, "symbol": 1, "timestamp": 1, "last": 1, "bid": 1, "offer": 1},
    ))


# ==========================================
# RENDER FUNCTIONS — OPCIONES
# ==========================================
def render_cadena_opciones(docs, spot):
    por_strike = {}
    for d in docs:
        k = d.get('strike')
        t = d.get('tipo')
        if k is None or t is None:
            continue
        if k not in por_strike:
            por_strike[k] = {}
        por_strike[k][t] = d

    if not por_strike:
        st.info("Sin datos de opciones. ¿El motor de opciones está corriendo?")
        return

    def fmt_hl(h, l):
        return f"{h:.1f}/{l:.1f}" if h and h > 0 else "-"

    def intraday(last, open_):
        if last and last > 0 and open_ and open_ > 0:
            return last / open_ - 1
        return None

    rows = []
    for K in sorted(por_strike.keys()):
        c = por_strike[K].get('CALL', {})
        p = por_strike[K].get('PUT',  {})
        c_mid = (c.get('bid', 0) + c.get('offer', 0)) / 2 if c.get('bid', 0) > 0 and c.get('offer', 0) > 0 else c.get('last', 0)
        p_mid = (p.get('bid', 0) + p.get('offer', 0)) / 2 if p.get('bid', 0) > 0 and p.get('offer', 0) > 0 else p.get('last', 0)
        if c_mid == 0 and p_mid == 0:
            continue
        rows.append({
            "C Δ%":    intraday(c.get('last'), c.get('open')),
            "C Vol":   fmt_vol(c.get('ev')),
            "C H/L":   fmt_hl(c.get('high', 0) or 0, c.get('low', 0) or 0),
            "C Delta": c.get('delta'),
            "C IV %":  (c.get('iv') or 0) * 100 if c.get('iv') else None,
            "C Bid":   c.get('bid')   if c.get('bid',   0) > 0 else None,
            "C Offer": c.get('offer') if c.get('offer', 0) > 0 else None,
            "STRIKE":  K,
            "P Bid":   p.get('bid')   if p.get('bid',   0) > 0 else None,
            "P Offer": p.get('offer') if p.get('offer', 0) > 0 else None,
            "P IV %":  (p.get('iv') or 0) * 100 if p.get('iv') else None,
            "P Delta": p.get('delta'),
            "P H/L":   fmt_hl(p.get('high', 0) or 0, p.get('low', 0) or 0),
            "P Vol":   fmt_vol(p.get('ev')),
            "P Δ%":    intraday(p.get('last'), p.get('open')),
        })

    if not rows:
        st.info("Sin precios disponibles.")
        return

    df = pd.DataFrame(rows)

    def pct_color(v):
        if pd.isna(v): return ""
        return "color: #00cc66; font-weight: bold" if v >= 0 else "color: #ff4444; font-weight: bold"

    styler = (
        df.style
        .map(pct_color, subset=["C Δ%", "P Δ%"])
        .map(lambda v: "color: #52946a" if pd.notna(v) else "", subset=["C Bid", "P Bid"])
        .map(lambda v: "color: #b05858" if pd.notna(v) else "", subset=["C Offer", "P Offer"])
        .map(lambda v: "color: #f97316; font-weight: bold", subset=["STRIKE"])
        .format({
            "C Δ%":    lambda v: f"{v:+.1%}" if pd.notna(v) else "-",
            "C Delta": lambda v: f"{v:.3f}"  if pd.notna(v) else "-",
            "C IV %":  lambda v: f"{v:.1f}%" if pd.notna(v) else "-",
            "C Bid":   lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
            "C Offer": lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
            "STRIKE":  "{:,.1f}",
            "P Bid":   lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
            "P Offer": lambda v: f"{v:.2f}"  if pd.notna(v) else "-",
            "P IV %":  lambda v: f"{v:.1f}%" if pd.notna(v) else "-",
            "P Delta": lambda v: f"{v:.3f}"  if pd.notna(v) else "-",
            "P Δ%":    lambda v: f"{v:+.1%}" if pd.notna(v) else "-",
        })
    )

    spot_str = f"${spot:,.2f}" if spot else "N/A"
    st.caption(f"CADENA DE OPCIONES GGAL — SPOT: {spot_str}")
    st.dataframe(styler, hide_index=True, use_container_width=True, height=df_height(len(df), max_h=900))


# ==========================================
# RENDER FUNCTIONS — ESTRATEGIAS DINÁMICAS
# ==========================================
#
# Cada template: (nombre, [(offset, tipo, lado, qty), ...])
# offset relativo al strike central elegido por el usuario.
# lado 'buy'  → ejecución a offer (peor precio para comprador = más conservador)
# lado 'sell' → ejecución a bid
#
def _build_strategy_templates():
    # Cada entrada: (categoria, nombre, patas) — mínimo 5 variantes por categoría
    t = []
    for n in range(1, 7):
        t.append(("Spread Alcista", f"Spread Alcista (Calls) +{n}", [(0,'CALL','buy',1), (+n,'CALL','sell',1)]))
    for n in range(1, 7):
        t.append(("Spread Bajista", f"Spread Bajista (Puts)  -{n}", [(0,'PUT','buy',1), (-n,'PUT','sell',1)]))
    t.append(("Cono / Cuna", "Cono ATM", [(0,'CALL','buy',1), (0,'PUT','buy',1)]))
    for n in range(1, 6):
        t.append(("Cono / Cuna", f"Cuna                   {n}w", [(+n,'CALL','buy',1), (-n,'PUT','buy',1)]))
    for n in range(1, 6):
        t.append(("Ratio", f"Ratio Call 1×2         +{n}", [(0,'CALL','buy',1), (+n,'CALL','sell',2)]))
    for n in range(1, 6):
        t.append(("Ratio", f"Ratio Put  1×2         -{n}", [(0,'PUT','buy',1), (-n,'PUT','sell',2)]))
    for n in range(1, 6):
        t.append(("Backspread", f"Backspread Call        +{n}", [(0,'CALL','sell',1), (+n,'CALL','buy',2)]))
    for n in range(1, 6):
        t.append(("Backspread", f"Backspread Put         -{n}", [(0,'PUT','sell',1), (-n,'PUT','buy',2)]))
    t.append(("Cóndor de Hierro", "Cóndor de Hierro  1|2", [(-2,'PUT','buy',1),(-1,'PUT','sell',1),(+1,'CALL','sell',1),(+2,'CALL','buy',1)]))
    t.append(("Cóndor de Hierro", "Cóndor de Hierro  2|3", [(-3,'PUT','buy',1),(-2,'PUT','sell',1),(+2,'CALL','sell',1),(+3,'CALL','buy',1)]))
    t.append(("Cóndor de Hierro", "Cóndor de Hierro  1|3", [(-3,'PUT','buy',1),(-1,'PUT','sell',1),(+1,'CALL','sell',1),(+3,'CALL','buy',1)]))
    t.append(("Cóndor de Hierro", "Cóndor de Hierro  1|4", [(-4,'PUT','buy',1),(-1,'PUT','sell',1),(+1,'CALL','sell',1),(+4,'CALL','buy',1)]))
    t.append(("Cóndor de Hierro", "Cóndor de Hierro  2|4", [(-4,'PUT','buy',1),(-2,'PUT','sell',1),(+2,'CALL','sell',1),(+4,'CALL','buy',1)]))
    t.append(("Venta de Vol", "Cono Vendido",         [(0,'CALL','sell',1), (0,'PUT','sell',1)]))
    for n in range(1, 5):
        t.append(("Venta de Vol", f"Cuna Vendida           {n}w", [(+n,'CALL','sell',1), (-n,'PUT','sell',1)]))
    return t

STRATEGY_TEMPLATES = _build_strategy_templates()


def render_estrategias_dinamicas(docs, spot, por_strike=None, liquid_strikes=None, center_idx=None, categoria_sel="Todas"):
    # Permite recibir datos pre-computados desde vista_estrategias (evita recalcular)
    if por_strike is None:
        por_strike = {}
        for d in docs:
            k = d.get('strike')
            t = d.get('tipo')
            if k and t:
                if k not in por_strike:
                    por_strike[k] = {}
                por_strike[k][t] = d

    if liquid_strikes is None:
        def is_liquid(d):
            if not d: return False
            return (d.get('bid', 0) or 0) > 0 or (d.get('offer', 0) or 0) > 0
        liquid_strikes = sorted([
            k for k, v in por_strike.items()
            if is_liquid(v.get('CALL')) or is_liquid(v.get('PUT'))
        ])

    if not liquid_strikes or spot <= 0:
        st.info("Sin suficientes datos de mercado para construir estrategias.")
        return

    if center_idx is None:
        center_idx = min(range(len(liquid_strikes)), key=lambda i: abs(liquid_strikes[i] - spot))

    atm_K = liquid_strikes[center_idx]

    def get_px(d, side):
        if not d: return 0
        offer = d.get('offer', 0) or 0
        bid   = d.get('bid',   0) or 0
        last  = d.get('last',  0) or 0
        return (offer if side == 'buy' else bid) if (offer > 0 and bid > 0) else last

    rows = []
    for cat, name, legs in STRATEGY_TEMPLATES:
        if categoria_sel != "Todas" and cat != categoria_sel:
            continue
        neto = d_net = g_net = t_net = 0.0
        valid = True
        used_K = []
        leg_evs = []          # volumen negociado de cada pata (para calcular el cuello de botella)

        for offset, tipo, side, qty in legs:
            idx = center_idx + offset
            if idx < 0 or idx >= len(liquid_strikes):
                valid = False
                break
            K  = liquid_strikes[idx]
            d  = por_strike.get(K, {}).get(tipo)
            px = get_px(d, side)
            if px <= 0:
                valid = False
                break
            m = 1 if side == 'buy' else -1
            neto  += px * qty * m
            d_net += (d.get('delta', 0) or 0) * qty * m
            g_net += (d.get('gamma', 0) or 0) * qty * m
            t_net += (d.get('theta', 0) or 0) * qty * m
            used_K.append(K)
            leg_evs.append((d.get('ev') or 0) / max(qty, 1))   # EV ajustado por ratio

        # Volumen de la pata más restrictiva
        vol_min = min(leg_evs) if valid and leg_evs else None

        rows.append({
            "Estrategia":  name,
            "Strikes":     "/".join(f"{k:,.0f}" for k in sorted(set(used_K))) if valid else "-",
            "Costo/Prima": neto    if valid else None,
            "Vol (pata)":  vol_min if valid else None,
            "Delta":       d_net   if valid else None,
            "Gamma":       g_net   if valid else None,
            "Theta":       t_net   if valid else None,
        })

    df = pd.DataFrame(rows)

    styler = (
        df.style
        .map(lambda v: (
            "color: #ff4444; font-weight: bold" if pd.notna(v) and v > 0 else
            "color: #00cc66; font-weight: bold" if pd.notna(v) else
            "color: #555"
        ), subset=["Costo/Prima"])
        .format({
            "Costo/Prima": lambda v: f"${v:.2f}"   if pd.notna(v) else "Sin Liq",
            "Vol (pata)":  lambda v: fmt_vol(v)     if pd.notna(v) else "-",
            "Delta":       lambda v: f"{v:.3f}"     if pd.notna(v) else "-",
            "Gamma":       lambda v: f"{v:.4f}"     if pd.notna(v) else "-",
            "Theta":       lambda v: f"{v:.2f}"     if pd.notna(v) else "-",
        })
    )
    atm_label = " (ATM)" if center_idx == min(range(len(liquid_strikes)), key=lambda i: abs(liquid_strikes[i] - spot)) else ""
    st.caption(
        f"ESTRATEGIAS — Strike central: {atm_K:,.0f}{atm_label} | Spot: ${spot:,.2f}  |  "
        f"Costo>0 = debit (pagás), Costo<0 = credit (recibís)  |  Vol = EV del leg más restrictivo"
    )
    st.dataframe(styler, hide_index=True, use_container_width=True, height=df_height(len(df), max_h=900))


# ==========================================
# HELPERS — ESTRATEGIAS (datos + gráficos)
# ==========================================

def _calcular_estrategias(por_strike, liquid_strikes, center_idx, spot, categoria_sel="Spread Alcista"):
    """Construye filas de la tabla y la lista de patas resueltas (symbol, K, side, qty, px)."""
    def get_px(d, side):
        if not d: return 0
        offer = d.get('offer', 0) or 0
        bid   = d.get('bid',   0) or 0
        last  = d.get('last',  0) or 0
        return (offer if side == 'buy' else bid) if (offer > 0 and bid > 0) else last

    rows, resolved_legs_list = [], []

    for cat, name, legs in STRATEGY_TEMPLATES:
        if cat != categoria_sel:
            continue
        neto = d_net = g_net = t_net = 0.0
        valid = True
        used_K, leg_evs, resolved_legs = [], [], []

        for offset, tipo, side, qty in legs:
            idx = center_idx + offset
            if idx < 0 or idx >= len(liquid_strikes):
                valid = False; break
            K  = liquid_strikes[idx]
            d  = por_strike.get(K, {}).get(tipo)
            px = get_px(d, side)
            if px <= 0:
                valid = False; break
            m = 1 if side == 'buy' else -1
            neto  += px * qty * m
            d_net += (d.get('delta', 0) or 0) * qty * m
            g_net += (d.get('gamma', 0) or 0) * qty * m
            t_net += (d.get('theta', 0) or 0) * qty * m
            used_K.append(K)
            leg_evs.append((d.get('ev') or 0) / max(qty, 1))
            _iv    = (d.get('iv')    or 0) if d else 0
            _vega  = (d.get('vega')  or 0) if d else 0
            _gamma = (d.get('gamma') or 0) if d else 0
            _dspot = (d.get('spot')  or 0) if d else 0
            _vence = (d.get('vence') or '') if d else ''
            # T exacto: vega/gamma = S²·σ·T  →  T = vega/(gamma·S²·σ)
            if _vega > 0 and _gamma > 0 and _dspot > 0 and _iv > 0:
                _T_leg = _vega / (_gamma * _dspot * _dspot * _iv)
            elif _vence:
                try:
                    _T_leg = max((datetime.strptime(_vence, "%Y%m%d") - datetime.now()).days, 1) / 365.0
                except Exception:
                    _T_leg = None
            else:
                _T_leg = None
            resolved_legs.append({
                'symbol': (d.get('symbol') or '') if d else '',
                'K': K, 'tipo': tipo, 'side': side, 'qty': qty, 'px': px,
                'iv': _iv, 'vence': _vence, 'T': _T_leg,
            })

        rows.append({
            "Estrategia":  name,
            "Strikes":     "/".join(f"{k:,.0f}" for k in sorted(set(used_K))) if valid else "-",
            "Costo/Prima": neto * 100                if valid else None,
            "Vol (pata)":  min(leg_evs) if leg_evs  else None,
            "Delta":       d_net                     if valid else None,
            "Gamma":       g_net                     if valid else None,
            "Theta":       t_net                     if valid else None,
        })
        resolved_legs_list.append(resolved_legs if valid else [])

    return rows, resolved_legs_list


def _chart_historico_estrategia(db_opciones, resolved_legs, costo_actual=None, dias=10):
    """Línea temporal del costo de la estrategia usando Opciones.Data."""
    symbols = [leg['symbol'] for leg in resolved_legs if leg.get('symbol')]
    if not symbols:
        return None

    docs = _fetch_estrategia_historico(frozenset(symbols), dias)
    if not docs:
        return None

    df = pd.DataFrame(docs)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['px'] = df.apply(
        lambda r: (r.get('bid', 0) + r.get('offer', 0)) / 2
        if (r.get('bid') or 0) > 0 and (r.get('offer') or 0) > 0
        else (r.get('last') or 0),
        axis=1,
    )
    df = df[df['px'] > 0]
    if df.empty:
        return None

    df = (df.set_index('timestamp')
           .groupby('symbol')['px']
           .resample('15min').last()
           .reset_index()
           .dropna())

    df_pivot = df.pivot_table(index='timestamp', columns='symbol', values='px', aggfunc='last')
    df_pivot = df_pivot.ffill().dropna()
    if df_pivot.empty:
        return None

    leg_map = {leg['symbol']: leg for leg in resolved_legs}
    costs = []
    for ts, row in df_pivot.iterrows():
        neto = sum(
            px * leg_map[sym]['qty'] * (1 if leg_map[sym]['side'] == 'buy' else -1)
            for sym, px in row.items() if sym in leg_map
        )
        costs.append({'Fecha': ts, 'Costo': round(neto * 100, 2)})

    df_cost = pd.DataFrame(costs)
    if df_cost.empty:
        return None

    # Eje ordinal: solo timestamps con datos reales, sin huecos por feriados/noches
    df_cost = df_cost.sort_values('Fecha').reset_index(drop=True)
    df_cost['segmento'] = (df_cost['Fecha'].diff() > pd.Timedelta(hours=2)).cumsum()
    df_cost['x_ord'] = df_cost['Fecha'].dt.strftime('%d/%m %H:%M')
    sort_order = df_cost['x_ord'].tolist()

    # ~8 etiquetas distribuidas uniformemente
    step = max(1, len(df_cost) // 8)
    tick_values = df_cost['x_ord'].iloc[::step].tolist()

    line = alt.Chart(df_cost).mark_line(color='#4a9eff', strokeWidth=1.5).encode(
        x=alt.X('x_ord:O', sort=sort_order, title=None,
                axis=alt.Axis(values=tick_values, labelAngle=-30)),
        y=alt.Y('Costo:Q', title='Costo ($)'),
        detail='segmento:N',
        tooltip=[alt.Tooltip('x_ord:N', title='Fecha'), alt.Tooltip('Costo:Q', format='$.2f')],
    )
    zero = alt.Chart(pd.DataFrame({'y': [0]})).mark_rule(color='#555', strokeDash=[4, 4]).encode(y='y:Q')

    layers = [line, zero]
    if costo_actual is not None:
        actual_r = alt.Chart(pd.DataFrame({'y': [costo_actual]})).mark_rule(
            color='#ffcc00', strokeDash=[6, 3], strokeWidth=1.5
        ).encode(y='y:Q')
        layers.append(actual_r)

    return alt.layer(*layers).properties(height=400)


def _chart_payoff_estrategia(resolved_legs, spot, neto):
    """Diagrama de payoff al vencimiento. Retorna (chart, lista_breakevens)."""
    if not resolved_legs or spot <= 0:
        return None, []

    ggal = np.linspace(spot * 0.65, spot * 1.35, 400)
    intrinseco = np.zeros(len(ggal))
    for leg in resolved_legs:
        m = 1 if leg['side'] == 'buy' else -1
        if leg['tipo'] == 'CALL':
            intrinseco += m * leg['qty'] * np.maximum(ggal - leg['K'], 0)
        else:
            intrinseco += m * leg['qty'] * np.maximum(leg['K'] - ggal, 0)

    pl = intrinseco * 100 - (neto or 0)
    df = pd.DataFrame({'GGAL': ggal, 'PL': pl, 'PL_pos': pl.clip(0), 'PL_neg': pl.clip(None, 0)})

    # Breakevens: cruces de cero por interpolación lineal
    breakevens = []
    sign_changes = np.where(np.diff(np.sign(pl)))[0]
    for i in sign_changes:
        x0, x1, y0, y1 = ggal[i], ggal[i + 1], pl[i], pl[i + 1]
        if y1 != y0:
            be = x0 - y0 * (x1 - x0) / (y1 - y0)
            breakevens.append(round(be, 0))

    base   = alt.Chart(df)
    area_g = base.mark_area(color='#00cc66', opacity=0.55).encode(x='GGAL:Q', y=alt.Y('PL_pos:Q', stack=None))
    area_r = base.mark_area(color='#ff4444', opacity=0.55).encode(x='GGAL:Q', y=alt.Y('PL_neg:Q', stack=None))
    line   = base.mark_line(color='white', strokeWidth=1.2).encode(
        x=alt.X('GGAL:Q', title='GGAL al vencimiento ($)',
                axis=alt.Axis(tickCount=8, format='$,.0f', labelAngle=-30)),
        y=alt.Y('PL:Q', title='P&L ($)', stack=None),
        tooltip=[alt.Tooltip('GGAL:Q', format=',.0f', title='GGAL'), alt.Tooltip('PL:Q', format=',.2f', title='P&L')],
    )
    spot_r = alt.Chart(pd.DataFrame({'x': [spot]})).mark_rule(
        color='#ffcc00', strokeDash=[4, 4], strokeWidth=1.5
    ).encode(x='x:Q')
    zero_r = alt.Chart(pd.DataFrame({'y': [0]})).mark_rule(
        color='#555', strokeDash=[4, 4]
    ).encode(y='y:Q')

    layers = [area_g, area_r, line, spot_r, zero_r]

    if breakevens:
        df_be = pd.DataFrame({'x': breakevens, 'label': [f"BE ${int(b):,}" for b in breakevens]})
        be_r = alt.Chart(df_be).mark_rule(color='#ffffff', strokeDash=[6, 3], strokeWidth=1.2).encode(x='x:Q')
        be_t = alt.Chart(df_be).mark_text(
            color='#ffffff', dy=-8, fontSize=11, fontWeight=600
        ).encode(x='x:Q', text='label:N')
        layers += [be_r, be_t]

    return alt.layer(*layers).properties(height=400), breakevens



@st.fragment(run_every=30)
def _tab_opciones_mercado():
    """Solo este fragment se refresca cada 30s."""
    meta_col = get_meta_col()

    docs = _get_options_snapshot()
    spot = next((d.get("spot", 0) for d in docs if d.get("spot", 0) > 0), 0) if docs else 0

    meta = _get_metadata()
    vr_doc   = meta.get("vr_ggal") or {}
    vr_local = vr_doc.get("vr_local", 0)
    vr_adr   = vr_doc.get("vr_adr",   0)

    cfg_doc = meta.get("config") or {}
    tasa_actual = cfg_doc.get("tasa", 0.242)
    if "tasa_display" not in st.session_state:
        st.session_state["tasa_display"] = tasa_actual

    ultimo_ts = max((d.get("updated_at") for d in docs if d.get("updated_at")), default=None) if docs else None
    ts_str = (ultimo_ts - timedelta(hours=3)).strftime("%H:%M:%S") if ultimo_ts else "—"

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("SPOT", f"${spot:,.2f}" if spot else "—")
    c2.metric("VR GGAL (40r)", f"{vr_local:.1%}" if vr_local else "—")
    c3.metric("ADR", f"{vr_adr:.1%}" if vr_adr else "—")
    with c4:
        nueva_tasa = st.number_input(
            "Tasa libre de riesgo",
            min_value=0.0, max_value=3.0,
            value=st.session_state["tasa_display"],
            step=0.005, format="%.3f",
            key="tasa_input",
            help="Cambiá el valor y el motor lo aplicará en ~60s",
        )
        if abs(nueva_tasa - st.session_state["tasa_display"]) > 1e-6:
            meta_col.update_one({"type": "config"}, {"$set": {"tasa": nueva_tasa}}, upsert=True)
            st.session_state["tasa_display"] = nueva_tasa
            st.toast(f"Tasa actualizada a {nueva_tasa:.3f}", icon="✅")
    c5.metric("Última act.", ts_str)

    st.divider()

    if not docs:
        st.warning("Sin datos de opciones. ¿El motor de opciones está corriendo?")
    else:
        render_cadena_opciones(docs, spot)

        smile_rows = {}
        for d in docs:
            k = d.get("strike"); t = d.get("tipo"); iv = d.get("iv")
            if k and t and iv and iv > 0:
                if k not in smile_rows:
                    smile_rows[k] = {}
                smile_rows[k][t] = round(iv * 100, 2)
        if smile_rows:
            smile_df = (
                pd.DataFrame.from_dict(smile_rows, orient="index")
                .rename(columns={"CALL": "CALL IV%", "PUT": "PUT IV%"})
                .sort_index()
            )
            st.caption("VOLATILITY SMILE — IV% por strike")
            st.line_chart(smile_df, use_container_width=True)


def vista_opciones():
    db_op = get_db_opciones()

    st.markdown("## ACAQuant | Opciones")
    tab_merc, tab_est, tab_vol = st.tabs(["Mercado", "Estrategias", "Volúmenes"])

    # ── Tab Mercado — auto-refresh 30s ────────────────────────────────────
    with tab_merc:
        _tab_opciones_mercado()

    # ── Tab Estrategias ───────────────────────────────────────────────────
    with tab_est:
        docs_e = _get_options_snapshot()
        spot_e = next((d.get("spot", 0) for d in docs_e if d.get("spot", 0) > 0), 0) if docs_e else 0

        if not docs_e:
            st.warning("Sin datos de opciones. ¿El motor de opciones está corriendo?")
        else:
            por_strike = {}
            for d in docs_e:
                k = d.get('strike'); t = d.get('tipo')
                if k and t:
                    if k not in por_strike:
                        por_strike[k] = {}
                    por_strike[k][t] = d

            def is_liquid(d):
                if not d: return False
                return (d.get('bid', 0) or 0) > 0 or (d.get('offer', 0) or 0) > 0

            liquid_strikes = sorted([
                k for k, v in por_strike.items()
                if is_liquid(v.get('CALL')) or is_liquid(v.get('PUT'))
            ])

            if not liquid_strikes:
                st.info("Sin strikes con liquidez aún.")
            else:
                atm_idx_default = min(range(len(liquid_strikes)), key=lambda i: abs(liquid_strikes[i] - spot_e))
                atm_K_default   = liquid_strikes[atm_idx_default]

                ultimo_ts_e = max((d.get("updated_at") for d in docs_e if d.get("updated_at")), default=None)
                ts_str_e = (ultimo_ts_e - timedelta(hours=3)).strftime("%H:%M:%S") if ultimo_ts_e else "—"

                categorias_ordenadas = ["Spread Alcista", "Spread Bajista", "Cono / Cuna",
                                        "Ratio", "Backspread", "Cóndor de Hierro", "Venta de Vol"]
                # Solo mostrar categorías que tienen templates definidos
                cats_disponibles = [c for c in categorias_ordenadas
                                    if any(cat == c for cat, _, _ in STRATEGY_TEMPLATES)]

                col_strike, col_cat, col_ts = st.columns([3, 3, 2])
                with col_strike:
                    strike_sel = st.selectbox(
                        "Strike central",
                        options=liquid_strikes,
                        index=atm_idx_default,
                        format_func=lambda k: f"{k:,.0f}{'  ← ATM' if k == atm_K_default else ''}",
                        key="estrategias_strike",
                    )
                with col_cat:
                    categoria_sel = st.selectbox(
                        "Tipo de estrategia",
                        options=cats_disponibles,
                        index=0,
                        key="estrategias_cat",
                    )
                with col_ts:
                    st.metric("Última act.", ts_str_e)

                center_idx = liquid_strikes.index(strike_sel)
                st.divider()

                rows, resolved_legs_list = _calcular_estrategias(
                    por_strike, liquid_strikes, center_idx, spot_e, categoria_sel
                )

                df_est = pd.DataFrame(rows)
                atm_label = " (ATM)" if center_idx == atm_idx_default else ""
                st.caption(
                    f"Strike central: {strike_sel:,.0f}{atm_label} | Spot: ${spot_e:,.2f}  |  "
                    f"Costo>0 = debit (pagás), Costo<0 = credit (recibís)"
                )
                styler = (
                    df_est.style
                    .map(lambda v: (
                        "color: #ff4444; font-weight: bold" if pd.notna(v) and v > 0 else
                        "color: #00cc66; font-weight: bold" if pd.notna(v) else
                        "color: #555"
                    ), subset=["Costo/Prima"])
                    .format({
                        "Costo/Prima": lambda v: f"${v:.2f}"  if pd.notna(v) else "Sin Liq",
                        "Vol (pata)":  lambda v: fmt_vol(v)    if pd.notna(v) else "-",
                        "Delta":       lambda v: f"{v:.3f}"    if pd.notna(v) else "-",
                        "Gamma":       lambda v: f"{v:.4f}"    if pd.notna(v) else "-",
                        "Theta":       lambda v: f"{v:.2f}"    if pd.notna(v) else "-",
                    })
                )
                selection = st.dataframe(
                    styler, hide_index=True, use_container_width=True,
                    height=df_height(len(df_est), max_h=700),
                    on_select="rerun", selection_mode="single-row",
                    key="estrategias_tabla",
                )

                st.divider()

                sel_rows = selection.selection.rows if hasattr(selection, 'selection') else []

                # Default: primera fila con liquidez
                if not sel_rows:
                    default_idx = next(
                        (i for i, r in enumerate(rows) if r.get("Costo/Prima") is not None),
                        0
                    )
                    sel_rows = [default_idx]

                row_idx  = sel_rows[0]
                sel_name = rows[row_idx]["Estrategia"]
                sel_cost = rows[row_idx]["Costo/Prima"]
                sel_legs = resolved_legs_list[row_idx]

                tipo_cost = "DEBIT" if (sel_cost or 0) > 0 else "CREDIT"
                st.markdown(f"### {sel_name}  —  {tipo_cost} ${abs(sel_cost or 0):.2f}")

                # ── Histórico de costo a ancho completo ──────────────────
                chart_h = _chart_historico_estrategia(db_op, sel_legs, costo_actual=sel_cost)
                if chart_h:
                    st.altair_chart(chart_h, use_container_width=True)
                else:
                    st.info("Sin datos históricos suficientes para esta estrategia.")

                st.divider()

                # ── Payoff (izq) + Tabla spread (der) ────────────────────
                chart_p, breakevens = _chart_payoff_estrategia(sel_legs, spot_e, sel_cost)

                _SPREAD_HEIGHT = 560

                col_payoff, col_tabla_spread = st.columns([3, 2])

                with col_payoff:
                    if chart_p:
                        chart_p_tall = chart_p.properties(height=_SPREAD_HEIGHT)
                        st.altair_chart(chart_p_tall, use_container_width=True)
                        be_str = "  Break-even: " + "  /  ".join(f"${int(b):,}" for b in breakevens) if breakevens else ""
                        st.caption(f"Línea amarilla = Spot actual (${spot_e:,.0f}){be_str}")

                with col_tabla_spread:
                    if sel_legs and spot_e > 0:
                        # Tasa y T para pricing teórico
                        _cfg = _get_metadata().get("config") or {}
                        _r   = _cfg.get("tasa", 0.242)
                        # T: promedio de los T calculados por pata (vega/gamma·S²·σ)
                        _t_vals = [lg['T'] for lg in sel_legs if lg.get('T') and lg['T'] > 0]
                        _T = sum(_t_vals) / len(_t_vals) if _t_vals else None

                        pct_steps = [i * 0.02 for i in range(-7, 8)]
                        spread_rows = []
                        for pct in pct_steps:
                            precio = spot_e * (1 + pct)
                            pl_finish = 0.0
                            pl_teo    = 0.0
                            for leg in sel_legs:
                                m = 1 if leg['side'] == 'buy' else -1
                                if leg['tipo'] == 'CALL':
                                    pl_finish += m * leg['qty'] * max(precio - leg['K'], 0) * 100
                                else:
                                    pl_finish += m * leg['qty'] * max(leg['K'] - precio, 0) * 100
                                if _T and (leg.get('iv') or 0) > 0:
                                    pl_teo += m * leg['qty'] * _bs_price(precio, leg['K'], _T, _r, leg['iv'], leg['tipo']) * 100
                            pl_finish -= (sel_cost or 0)
                            row = {"Precio GGAL": precio, "Var %": pct, "A finish": pl_finish}
                            if _T:
                                row["Teórico"] = pl_teo - (sel_cost or 0)
                            spread_rows.append(row)

                        df_spread = pd.DataFrame(spread_rows)
                        money_cols = ["A finish"] + (["Teórico"] if _T else [])
                        fmt = {"Precio GGAL": "${:,.2f}", "Var %": "{:+.0%}",
                               "A finish": "${:,.2f}"}
                        if _T:
                            fmt["Teórico"] = "${:,.2f}"
                        styler_sp = (
                            df_spread.style
                            .map(lambda v: (
                                "color: #00cc66; font-weight: bold" if isinstance(v, float) and v > 0 else
                                "color: #ff4444; font-weight: bold" if isinstance(v, float) and v < 0 else
                                ""
                            ), subset=money_cols)
                            .format(fmt)
                        )
                        cap = f"±2% por paso | spot ${spot_e:,.2f} | r={_r:.1%}"
                        if _T:
                            cap += f" | T={_T*365:.0f}d (estimado de Greeks)"
                        else:
                            cap += " | Teórico no disponible (Greeks insuficientes)"
                        st.caption(cap)
                        st.dataframe(styler_sp, hide_index=True, use_container_width=True,
                                     height=_SPREAD_HEIGHT)

    # ── Tab Volúmenes ─────────────────────────────────────────────────────
    with tab_vol:
        _render_volumenes_opciones(db_op)


@st.cache_data(ttl=300)
def _fetch_vol_historico():
    """Lee rollup diario de Opciones.DataHistorica (alimentado por jobs.options_rollup).

    Devuelve filas (fecha, Strike, Tipo, EV_M) de los últimos 20 días.
    """
    fecha_min = (datetime.utcnow() - timedelta(days=20)).strftime("%Y-%m-%d")
    pipeline = [
        {"$match": {"fecha": {"$gte": fecha_min}, "ev": {"$gt": 0},
                    "strike": {"$exists": True}, "tipo": {"$exists": True}}},
        {"$group": {
            "_id": {"fecha": "$fecha", "strike": "$strike", "tipo": "$tipo"},
            "ev_total": {"$sum": "$ev"},
        }},
    ]
    docs = list(get_mongo_client_read()["Opciones"]["DataHistorica"].aggregate(pipeline))
    return [{
        "fecha":  d["_id"]["fecha"],
        "Strike": d["_id"]["strike"],
        "Tipo":   d["_id"]["tipo"],
        "EV_M":   round(d["ev_total"] / 1_000_000, 3),
    } for d in docs]


def _render_volumenes_opciones(_db_op_ignored):
    """Volumen operado (EV) por strike y tipo (CALL/PUT) usando Opciones.Data."""
    rows = _fetch_vol_historico()
    if not rows:
        st.info("Sin datos en Opciones.Data.")
        return

    df_all = pd.DataFrame(rows)
    fechas = sorted(df_all["fecha"].unique())
    if not fechas:
        st.info("Sin fechas disponibles.")
        return

    color_scale = alt.Scale(domain=["CALL", "PUT"], range=["#4a9eff", "#ff4444"])

    # ── Volumen por strike — rango desde / hasta ─────────────────────────
    desde, hasta = st.select_slider(
        "Rango de fechas",
        options=fechas,
        value=(fechas[0], fechas[-1]),
        key="vol_strike_rango",
    )
    df_fil = df_all[(df_all["fecha"] >= desde) & (df_all["fecha"] <= hasta)]
    df_dia = df_fil.groupby(["Strike", "Tipo"], as_index=False)["EV_M"].sum()
    label_dia = desde if desde == hasta else f"{desde} → {hasta}"

    total_call  = df_dia[df_dia["Tipo"] == "CALL"]["EV_M"].sum()
    total_put   = df_dia[df_dia["Tipo"] == "PUT"]["EV_M"].sum()
    total_all_d = total_call + total_put

    c1, c2, c3, _ = st.columns([2, 2, 2, 3])
    c1.metric(label_dia, f"${total_all_d:.1f}M")
    c2.metric("CALLs", f"${total_call:.1f}M")
    c3.metric("PUTs",  f"${total_put:.1f}M")

    if df_dia.empty:
        st.info("Sin datos para esta fecha.")
        return

    strikes_order = [f"{k:,.0f}" for k in sorted(df_dia["Strike"].unique())]
    df_dia["Strike_lbl"] = df_dia["Strike"].apply(lambda k: f"{k:,.0f}")

    strike_bars = alt.Chart(df_dia).mark_bar().encode(
        x=alt.X("Strike_lbl:O", sort=strikes_order, title="Strike",
                axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("EV_M:Q", title="Volumen ($M)", stack=True),
        color=alt.Color("Tipo:N", scale=color_scale,
                        legend=alt.Legend(title=None, orient="top-right")),
        order=alt.Order("Tipo:N", sort="ascending"),
        tooltip=[
            alt.Tooltip("Strike_lbl:N", title="Strike"),
            alt.Tooltip("Tipo:N",       title="Tipo"),
            alt.Tooltip("EV_M:Q",       title="$M", format=".3f"),
        ],
    ).properties(height=380)
    st.altair_chart(strike_bars, use_container_width=True)


