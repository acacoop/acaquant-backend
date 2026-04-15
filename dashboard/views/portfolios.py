"""Vista Portfolios del dashboard: Reportes ejecutivos mensuales por cuenta."""
from datetime import datetime, timedelta

import pandas as pd
import altair as alt
import streamlit as st

from dashboard.shared.db import get_db_valuaciones
from dashboard.shared.format import fmt_money, df_height


@st.cache_data(ttl=600, show_spinner=False)
def _get_carteras_ii_df():
    """
    Lee Valuaciones.CarterasII (snapshot del primer día hábil del mes anterior)
    + join con Assets. Usa 'valuacion' ya calculada en el snapshot.
    """
    db_val = get_db_valuaciones()
    docs = list(db_val["CarterasII"].find({}, {"_id": 0}))
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    df["valuación"] = pd.to_numeric(df.get("valuacion"), errors="coerce").fillna(0)

    assets_docs = list(db_val["Assets"].find({}, {"_id": 0, "unidad": 1,
        "CALIFICACION": 1, "CARTERA": 1, "CLASE_ACTIVO": 1,
        "EMISOR": 1, "TICKER": 1, "VENCIMIENTO": 1}))
    if assets_docs:
        df = df.merge(pd.DataFrame(assets_docs), on="unidad", how="left")

    for col in ["TICKER", "EMISOR", "CLASE_ACTIVO", "CARTERA", "CALIFICACION", "VENCIMIENTO"]:
        if col in df.columns:
            df[col] = df[col].fillna("-")
    return df


@st.cache_data(ttl=60, show_spinner=False)
def _get_valor_mep():
    """Último valor MEP desde Valuaciones.Dolar (sort por timestamp desc)."""
    doc = get_db_valuaciones()["Dolar"].find_one(sort=[("timestamp", -1)])
    if not doc or "mep" not in doc:
        return None
    try:
        return float(doc["mep"])
    except (TypeError, ValueError):
        return None


# ==========================================
# PORTFOLIOS → REPORTES (layout replica informe ejecutivo mensual)
# Datos dummy — a conectar con Mongo tras validación de layout
# ==========================================

_REP_NAVY = "#1F3864"
_REP_NAVY_SOFT = "#2E5596"
_REP_BLUE_BG = "#E7EAF4"

def _rep_section_header(title: str):
    st.markdown(
        f"""<div style="background:{_REP_NAVY};color:#fff;padding:8px 14px;
        border-radius:3px;font-weight:700;letter-spacing:0.3px;margin:18px 0 12px 0;
        text-align:center;font-size:14px;">{title}</div>""",
        unsafe_allow_html=True,
    )


def _rep_kpi(label: str, value: str):
    st.markdown(
        f"""<div style="padding:4px 0">
        <div style="font-size:12px;color:#666;font-weight:600">{label}</div>
        <div style="font-size:18px;color:{_REP_NAVY};font-weight:700">{value}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def _rep_dummy_carteras(cuenta: str, mes: str):
    """Devuelve (ARS, DL, HD, FCI) en ARS para mes dado — dummy."""
    import hashlib
    seed = int(hashlib.md5(f"{cuenta}{mes}".encode()).hexdigest()[:8], 16)
    import random
    rnd = random.Random(seed)
    ars = rnd.uniform(30, 55) * 1e9
    dl  = rnd.uniform(15, 30) * 1e9
    hd  = rnd.uniform(35, 60) * 1e9
    fci = rnd.uniform(10, 30) * 1e9
    return ars, dl, hd, fci


def _render_reporte_ejecutivo():
    import datetime as _dt

    df_carteras = _get_carteras_df()
    if df_carteras.empty:
        st.warning("Sin datos en Valuaciones.Carteras.")
        return
    cuentas_disponibles = sorted(df_carteras["id_cuenta"].dropna().astype(str).unique().tolist())

    col_sel, col_info = st.columns([2, 5])
    with col_sel:
        cuenta_sel = st.selectbox(
            "Cuenta",
            cuentas_disponibles,
            key="rep_cuenta_sel",
        )
    with col_info:
        st.info("📋 Reporte — datos reales conectándose por sección. Secciones pendientes siguen en dummy.")

    fecha_hoy = _dt.date.today()
    fecha_str = fecha_hoy.strftime("%d/%m/%Y")
    mes_actual = fecha_hoy.strftime("%B - %Y").capitalize()
    mes_prev_d = (fecha_hoy.replace(day=1) - _dt.timedelta(days=1))
    mes_prev = mes_prev_d.strftime("%B - %Y").capitalize()

    # ───────────────── 1. RESUMEN EJECUTIVO ─────────────────
    _rep_section_header("RESUMEN EJECUTIVO")

    val_mep = _get_valor_mep() or 0.0
    val_a3500 = _get_dolar_oficial() or 0.0

    # Valuación ARS real = suma de columna 'valuación' para la cuenta
    _df_cta = df_carteras[df_carteras["id_cuenta"].astype(str) == str(cuenta_sel)]
    total_ars = float(_df_cta["valuación"].sum())

    # Breakdown real por cartera — mes actual (groupby CARTERA)
    if "CARTERA" in _df_cta.columns and not _df_cta.empty:
        _grp_cart = _df_cta.groupby("CARTERA")["valuación"].sum()
        ars_mes = float(_grp_cart.get("CARTERA ARS", 0.0))
        dl_mes  = float(_grp_cart.get("CARTERA DL",  0.0))
        hd_mes  = float(_grp_cart.get("CARTERA HD",  0.0))
        fci_mes = float(_grp_cart.get("CARTERA FCI", 0.0))
    else:
        ars_mes = dl_mes = hd_mes = fci_mes = 0.0
    val_a3500_total = total_ars / val_a3500 if val_a3500 else 0.0
    val_usd_total = total_ars / val_mep if val_mep else 0.0

    # Mes anterior: desde Valuaciones.CarterasII (primer día hábil mes anterior)
    df_cii = _get_carteras_ii_df()
    if not df_cii.empty and "CARTERA" in df_cii.columns:
        _df_cta_prev = df_cii[df_cii["id_cuenta"].astype(str) == str(cuenta_sel)]
        _grp_prev = _df_cta_prev.groupby("CARTERA")["valuación"].sum()
        ars_prev = float(_grp_prev.get("CARTERA ARS", 0.0))
        dl_prev  = float(_grp_prev.get("CARTERA DL",  0.0))
        hd_prev  = float(_grp_prev.get("CARTERA HD",  0.0))
        fci_prev = float(_grp_prev.get("CARTERA FCI", 0.0))
    else:
        ars_prev = dl_prev = hd_prev = fci_prev = 0.0

    # Fila 1: KPIs — bloque izq (Informe/MEP/A3500 stackeados) + 3 valuaciones horizontales
    _kpi_label = f"font-size:14px;color:#666;font-weight:600"
    _kpi_val   = f"font-size:22px;color:{_REP_NAVY};font-weight:700"
    kpi_grid = f"""
    <div style='display:flex;gap:32px;align-items:flex-start;margin-bottom:24px'>
      <div style='display:flex;flex-direction:column;gap:12px;min-width:180px'>
        <div><div style='{_kpi_label}'>Informe al</div>
             <div style='{_kpi_val}'>{fecha_str}</div></div>
        <div><div style='{_kpi_label}'>Valor MEP</div>
             <div style='{_kpi_val}'>{val_mep:,.2f}</div></div>
        <div><div style='{_kpi_label}'>Valor A3500</div>
             <div style='{_kpi_val}'>{val_a3500:,.2f}</div></div>
      </div>
      <div style='display:flex;flex:1;justify-content:space-around;align-items:flex-start;padding-top:4px'>
        <div><div style='{_kpi_label}'>Valuación ARS</div>
             <div style='{_kpi_val}'>{total_ars:,.0f}</div></div>
        <div><div style='{_kpi_label}'>Valuación A3500</div>
             <div style='{_kpi_val}'>{val_a3500_total:,.0f}</div></div>
        <div><div style='{_kpi_label}'>Valuación USD MEP</div>
             <div style='{_kpi_val}'>{val_usd_total:,.0f}</div></div>
      </div>
    </div>
    """
    st.markdown(kpi_grid, unsafe_allow_html=True)

    # Fila 2: donut (izq) + tablas (der) — tablas subidas con margin-top negativo
    col_donut, col_tablas = st.columns([1, 1.1])

    with col_donut:
        import pandas as _pd
        donut_df = _pd.DataFrame({
            "Cartera": ["Cartera ARS", "Cartera DL", "Cartera HD", "Cartera FCI"],
            "Monto": [ars_mes, dl_mes, hd_mes, fci_mes],
        })
        donut_df = donut_df[donut_df["Monto"] > 0].reset_index(drop=True)
        _s = donut_df["Monto"].sum()
        donut_df["pct"] = (donut_df["Monto"] / _s) if _s else 0.0
        donut_df["label"] = donut_df["pct"].map(lambda v: f"{v:.1%}")
        _palette = {
            "Cartera ARS": "#4472C4", "Cartera DL": "#5B9BD5",
            "Cartera HD":  "#8FAADC", "Cartera FCI": "#B4C7E7",
        }
        _domain = donut_df["Cartera"].tolist()
        _range = [_palette[c] for c in _domain]
        arc = alt.Chart(donut_df).mark_arc(innerRadius=85, outerRadius=140).encode(
            theta=alt.Theta("Monto:Q"),
            color=alt.Color(
                "Cartera:N",
                scale=alt.Scale(domain=_domain, range=_range),
                legend=None,
            ),
            tooltip=["Cartera:N", alt.Tooltip("Monto:Q", format=",.0f"),
                     alt.Tooltip("pct:Q", format=".1%")],
        )
        labels = alt.Chart(donut_df).mark_text(radius=112, size=12, color="white", fontWeight="bold").encode(
            theta=alt.Theta("Monto:Q", stack=True),
            text=alt.Text("label:N"),
        )
        st.altair_chart(
            (arc + labels).properties(
                title=alt.TitleParams(mes_actual.upper(), anchor="middle", fontSize=14),
                height=340,
            ),
            use_container_width=True,
        )
        # Leyenda custom pegada al donut
        _legend_items = "".join(
            f"<div style='display:flex;align-items:center;gap:6px;font-size:12px;color:#333'>"
            f"<span style='width:10px;height:10px;background:{_palette[c]};border-radius:50%;display:inline-block'></span>{c}</div>"
            for c in _domain
        )
        st.markdown(
            f"<div style='display:flex;justify-content:center;gap:20px;margin-top:-24px'>{_legend_items}</div>",
            unsafe_allow_html=True,
        )

    with col_tablas:
        def _tabla_html(titulo, ars, dl, hd, fci):
            total = ars + dl + hd + fci
            total_dolar = dl + hd
            total_pesos = ars + fci
            def _p(v):
                return (v / total) if total else 0.0
            carteras = [
                ("Cartera ARS", ars, _p(ars)),
                ("Cartera DL",  dl,  _p(dl)),
                ("Cartera HD",  hd,  _p(hd)),
                ("Cartera FCI", fci, _p(fci)),
            ]
            totales = [
                ("Total Dolarizado", total_dolar, _p(total_dolar)),
                ("Total Pesos",      total_pesos, _p(total_pesos)),
            ]
            carteras = [r for r in carteras if r[1] > 0]
            totales  = [r for r in totales  if r[1] > 0]
            if not carteras and not totales:
                return ""

            def _render_rows(items):
                return "".join(
                    f"<tr><td style='padding:4px 8px;border-bottom:1px solid #eee'>{label}</td>"
                    f"<td style='padding:4px 8px;border-bottom:1px solid #eee;text-align:right'>{monto:,.0f}</td>"
                    f"<td style='padding:4px 8px;border-bottom:1px solid #eee;text-align:right'>{pct:.1%}</td></tr>"
                    for label, monto, pct in items
                )
            sep_row = "<tr><td colspan='3' style='padding:0;height:10px;background:#fff;border:none'></td></tr>"
            tr_rows = _render_rows(carteras)
            if carteras and totales:
                tr_rows += sep_row
            tr_rows += _render_rows(totales)
            return (
                f"<table style='width:100%;border-collapse:collapse;font-size:13px;margin-bottom:14px'>"
                f"<thead><tr style='background:{_REP_NAVY};color:#fff'>"
                f"<th style='padding:6px 8px;text-align:left;font-style:italic'>{titulo}</th>"
                f"<th style='padding:6px 8px;text-align:right'>Monto ARS</th>"
                f"<th style='padding:6px 8px;text-align:right'>Ponderación</th>"
                f"</tr></thead><tbody>{tr_rows}</tbody></table>"
            )

        html_tablas = (
            _tabla_html(mes_actual, ars_mes, dl_mes, hd_mes, fci_mes)
            + _tabla_html(mes_prev, ars_prev, dl_prev, hd_prev, fci_prev)
        )
        st.markdown(
            f"<div style='margin-top:-80px'>{html_tablas}</div>",
            unsafe_allow_html=True,
        )

    # ───────────────── 2. CARTERAS vs BENCHMARKS ─────────────────
    _rep_section_header("Detalle de las carteras vs benchmarks")

    import pandas as _pd
    meses_serie = ["may-25","jun-25","jul-25","ago-25","sept-25","oct-25","nov-25","dic-25","ene-26","feb-26","mar-26"]

    def _serie(seed_key, start=0.0, step_mu=0.03, step_sig=0.02):
        import random as _r
        rnd = _r.Random(hash(f"{cuenta_sel}{seed_key}") & 0xffffffff)
        cur = start
        out = [cur]
        for _ in range(len(meses_serie) - 1):
            cur += rnd.gauss(step_mu, step_sig)
            out.append(cur)
        return out

    serie_total_ars = _serie("total_ars", step_mu=0.032)
    serie_pesos     = _serie("pesos",     step_mu=0.035)
    serie_usd_cart  = _serie("usd_cart",  step_mu=0.013, step_sig=0.015)
    serie_total_usd = _serie("total_usd", step_mu=0.018, step_sig=0.025)
    serie_badlar    = _serie("badlar",    step_mu=0.025, step_sig=0.005)
    serie_infl      = _serie("infl",      step_mu=0.024, step_sig=0.004)
    serie_a3500     = _serie("a3500",     step_mu=0.015, step_sig=0.03)

    def _chart_multi(series_dict, title, colors):
        rows = []
        for nombre, serie in series_dict.items():
            for m, v in zip(meses_serie, serie):
                rows.append({"Mes": m, "Serie": nombre, "Valor": v})
        dfc = _pd.DataFrame(rows)
        line = alt.Chart(dfc).mark_line(point=True).encode(
            x=alt.X("Mes:N", sort=meses_serie, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("Valor:Q", axis=alt.Axis(format=".0%")),
            color=alt.Color("Serie:N",
                            scale=alt.Scale(domain=list(series_dict.keys()), range=colors),
                            legend=alt.Legend(title=None, orient="top")),
            tooltip=["Mes:N", "Serie:N", alt.Tooltip("Valor:Q", format=".2%")],
        ).properties(title=title, height=330)
        return line

    g1, g2 = st.columns(2)
    with g1:
        st.altair_chart(_chart_multi({
            "Badlar": serie_badlar, "Inflacion": serie_infl,
            "Cartera Total ACA BIO": serie_total_ars, "A3500": serie_a3500,
        }, "Cartera Total BIO en ARS vs Benchmarks",
        colors=["#9BB8E0", "#A9D18E", "#1F3864", "#E4A9D4"]), use_container_width=True)
    with g2:
        st.altair_chart(_chart_multi({
            "Cartera Total ACA BIO": serie_total_usd,
        }, "Cartera Total BIO en USD",
        colors=["#1F3864"]), use_container_width=True)

    g3, g4 = st.columns(2)
    with g3:
        st.altair_chart(_chart_multi({
            "Badlar": serie_badlar, "Inflacion": serie_infl,
            "Cartera ARS ACA BIO": serie_pesos,
        }, "Cartera Pesos BIO vs Benchmarks",
        colors=["#9BB8E0", "#A9D18E", "#2E75B6"]), use_container_width=True)
    with g4:
        st.altair_chart(_chart_multi({
            "Cartera USD ACA BIO": serie_usd_cart,
        }, "Cartera USD/DL BIO",
        colors=["#1F3864"]), use_container_width=True)

    # ───────────────── 3. VARIACIONES DEL MES ─────────────────
    _rep_section_header("Variaciones del mes")

    meses_tabla = ["jul-25","ago-25","sept-25","oct-25","nov-25","dic-25","ene-26","feb-26","mar-26","abr-26","may-26","jun-26"]
    def _acum(seed_key, step=0.03, sig=0.02):
        import random as _r
        rnd = _r.Random(hash(f"{cuenta_sel}{seed_key}v2") & 0xffffffff)
        cur = 0
        out = []
        for i in range(9):
            cur += rnd.gauss(step, sig)
            out.append(cur)
        out.extend([None, None, None])
        return out

    tabla_rows = _pd.DataFrame({
        "Mes": meses_tabla,
        "Cart. Total en USD": _acum("tu", 0.015, 0.025),
        "Inflacion":          _acum("inf", 0.024, 0.004),
        "A3500":               _acum("a3", 0.015, 0.03),
        "Badlar":             _acum("bl", 0.025, 0.005),
        "Cart. Pesos":        _acum("cp", 0.035, 0.015),
        "Cart. USD":          _acum("cu", 0.013, 0.015),
    })

    def _pct(v):
        return f"{v*100:,.2f}%" if v is not None else ""

    tabla_disp = tabla_rows.copy()
    for c in tabla_disp.columns:
        if c != "Mes":
            tabla_disp[c] = tabla_disp[c].map(_pct)

    c_tbl, c_var = st.columns([3, 1])
    with c_tbl:
        st.dataframe(tabla_disp, hide_index=True, use_container_width=True,
                     height=df_height(len(tabla_disp), max_h=500))
    with c_var:
        st.markdown(f"<div style='background:{_REP_NAVY};color:#fff;text-align:center;"
                    f"padding:6px;font-weight:700;border-radius:3px'>Variaciones del mes</div>",
                    unsafe_allow_html=True)
        import random as _r
        rnd = _r.Random(hash(f"{cuenta_sel}var") & 0xffffffff)
        var_rows = [
            ("Cart. Total $",      rnd.uniform(0.005, 0.035)),
            ("Cartera Total U$S",  rnd.uniform(0.01, 0.05)),
            ("Cart. Pesos",        rnd.uniform(0.01, 0.05)),
            ("Cart. USD",          rnd.uniform(0.005, 0.025)),
            ("Inflacion",          rnd.uniform(0.015, 0.03)),
            ("Badlar",             rnd.uniform(0.015, 0.03)),
            ("A3500",              rnd.uniform(-0.03, 0.03)),
        ]
        for label, v in var_rows:
            color = "#00aa55" if v >= 0 else "#d14343"
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;"
                f"padding:4px 10px;border-bottom:1px solid #eee'>"
                f"<span>{label}</span><span style='color:{color};font-weight:700'>{v*100:,.1f}%</span></div>",
                unsafe_allow_html=True,
            )

    # ───────────────── 4-7. DETALLE DE ACTIVOS ─────────────────
    def _dummy_activos(clase_grupos, monto_total, seed):
        """Genera filas de activos dummy para una cartera."""
        import random as _r
        rnd = _r.Random(hash(f"{cuenta_sel}{seed}") & 0xffffffff)
        rows = []
        emisores = ["TESORO","PROV. CORDOBA","PROV. MENDOZA","YPF","TECO","PAE",
                    "BCO. MACRO","BCRA","BCO. COMAFI","CGC","GENNEIA","ARCOR","IRSA"]
        califs = ["CCC+","AAA","AA+","AA","AA-.ar","A+","BBB+","BBB.ar"]
        total = 0
        tickers_pool = [f"TK{rnd.randint(10,99)}{chr(rnd.randint(65,90))}" for _ in range(30)]
        for clase in clase_grupos:
            n = rnd.randint(3, 6)
            for _ in range(n):
                vn = rnd.uniform(1e8, 5e9)
                px = rnd.uniform(90, 1400)
                monto = vn * px / 100 if clase != "HD" and clase != "DL" else vn * px
                monto = monto * rnd.uniform(0.1, 0.4)
                total += monto
                rows.append({
                    "Ticker": rnd.choice(tickers_pool),
                    "Emisor": rnd.choice(emisores),
                    "Calif.": rnd.choice(califs),
                    "Clase Act.": clase,
                    "Venc.": f"{rnd.randint(1,28)}/{rnd.randint(1,12)}/{rnd.choice([2026,2027,2028,2029,2030])}",
                    "VN": vn,
                    "Px": px,
                    "Monto": monto,
                    "Tasa": rnd.uniform(-0.15, 0.35),
                    "% Share": monto,
                })
        dfa = _pd.DataFrame(rows)
        factor = monto_total / dfa["Monto"].sum()
        dfa["Monto"] *= factor
        dfa["% Share"] = dfa["Monto"] / monto_total
        return dfa

    def _render_tabla_activos(df_act, titulo, total_label, total_valor):
        _rep_section_header(titulo)
        disp = df_act.copy()
        disp["VN"] = disp["VN"].map(lambda v: f"{v:,.0f}")
        disp["Px"] = disp["Px"].map(lambda v: f"{v:,.2f}")
        disp["Monto"] = disp["Monto"].map(lambda v: f"{v:,.0f}")
        disp["Tasa"] = disp["Tasa"].map(lambda v: f"{v*100:,.1f}%")
        disp["% Share"] = disp["% Share"].map(lambda v: f"{v*100:,.0f}%")
        st.dataframe(disp, hide_index=True, use_container_width=True,
                     height=df_height(len(disp), max_h=600))
        st.markdown(
            f"<div style='text-align:right;font-weight:700;color:{_REP_NAVY};padding:4px'>"
            f"{total_label}: {total_valor:,.0f}</div>",
            unsafe_allow_html=True,
        )

    df_ars = _dummy_activos(["CER", "FIJA", "TAMAR"], ars_mes, "pesos")
    _render_tabla_activos(df_ars, "Detalle de Activos - Cartera Pesos", "CARTERA PESOS", ars_mes)

    df_fci = _dummy_activos(["ARS T1", "MM ARS", "MM USD", "HD T1", "RENTA VARIABLE"], fci_mes, "fci")
    _render_tabla_activos(df_fci, "Detalle de Activos - Cartera FCI", "CARTERA FCI", fci_mes)

    df_hd = _dummy_activos(["HD"], hd_mes, "hd")
    _render_tabla_activos(df_hd, "Detalle de Activos - Cartera HD", "CARTERA HD", hd_mes)

    df_dl = _dummy_activos(["DL"], dl_mes, "dl")
    _render_tabla_activos(df_dl, "Detalle de Activos - Cartera DL", "CARTERA DL", dl_mes)

    # ───────────────── 8. MÉTRICAS GENERALES ─────────────────
    _rep_section_header("Metricas Generales")

    mc1, mc2, mc3 = st.columns(3)

    def _breakdown_tabla(titulo, df_src, group_col, total):
        grp = df_src.groupby(group_col)["Monto"].sum().reset_index()
        grp = grp.sort_values("Monto", ascending=False)
        grp["%"] = grp["Monto"] / total
        grp[group_col] = grp[group_col].astype(str)
        disp = grp.copy()
        disp["Monto"] = disp["Monto"].map(lambda v: f"{v:,.0f}")
        disp["%"] = disp["%"].map(lambda v: f"{v:.0%}")
        st.markdown(f"**{titulo}**")
        st.dataframe(disp, hide_index=True, use_container_width=True,
                     height=df_height(len(disp), max_h=400))
        return grp

    with mc1:
        grp_fci = _breakdown_tabla("CARTERA FCI", df_fci, "Clase Act.", fci_mes)
    with mc2:
        grp_ars = _breakdown_tabla("CARTERA ARS", df_ars, "Clase Act.", ars_mes)
    with mc3:
        df_priv = _pd.concat([df_hd, df_dl])
        df_priv_no_tesoro = df_priv[df_priv["Emisor"] != "TESORO"]
        total_priv = df_priv_no_tesoro["Monto"].sum() if not df_priv_no_tesoro.empty else 1
        _breakdown_tabla("CRÉDITOS PRIVADOS MÁS REPRESENTATIVOS", df_priv_no_tesoro,
                         "Emisor", total_priv)

    mc4, mc5 = st.columns(2)
    with mc4:
        def _donut_mini(df_grp, label_col, titulo):
            dfp = df_grp.copy().rename(columns={label_col: "cat"})
            dfp["pct"] = dfp["Monto"] / dfp["Monto"].sum()
            dfp["lbl"] = dfp["pct"].map(lambda v: f"{v:.0%}")
            arc = alt.Chart(dfp).mark_arc(innerRadius=50, outerRadius=95).encode(
                theta="Monto:Q",
                color=alt.Color("cat:N", legend=alt.Legend(title=None, orient="right")),
                tooltip=["cat:N", alt.Tooltip("pct:Q", format=".1%")],
            )
            lbl = alt.Chart(dfp).mark_text(radius=72, color="white", size=10, fontWeight="bold").encode(
                theta=alt.Theta("Monto:Q", stack=True), text="lbl:N",
            )
            st.altair_chart((arc + lbl).properties(
                title=alt.TitleParams(titulo, anchor="middle"), height=240
            ), use_container_width=True)
        _donut_mini(grp_fci, "Clase Act.", "CARTERA FCI")
    with mc5:
        _donut_mini(grp_ars, "Clase Act.", "CARTERA ARS")

    mc6, mc7 = st.columns(2)
    with mc6:
        df_hd_em = df_hd.groupby("Emisor")["Monto"].sum().reset_index().sort_values("Monto", ascending=False)
        df_hd_em["%"] = df_hd_em["Monto"] / hd_mes
        disp_hd = df_hd_em.copy()
        disp_hd["Monto"] = disp_hd["Monto"].map(lambda v: f"{v:,.0f}")
        disp_hd["%"] = disp_hd["%"].map(lambda v: f"{v:.0%}")
        st.markdown("**CARTERA HD**")
        st.dataframe(disp_hd, hide_index=True, use_container_width=True,
                     height=df_height(len(disp_hd), max_h=400))
    with mc7:
        df_dl_em = df_dl.groupby("Emisor")["Monto"].sum().reset_index().sort_values("Monto", ascending=False)
        df_dl_em["%"] = df_dl_em["Monto"] / dl_mes
        disp_dl = df_dl_em.copy()
        disp_dl["Monto"] = disp_dl["Monto"].map(lambda v: f"{v:,.0f}")
        disp_dl["%"] = disp_dl["%"].map(lambda v: f"{v:.0%}")
        st.markdown("**CARTERA DL**")
        st.dataframe(disp_dl, hide_index=True, use_container_width=True,
                     height=df_height(len(disp_dl), max_h=400))


@st.fragment(run_every=3600)
def vista_portfolios():
    """Vista Portfolios: informe ejecutivo mensual por cuenta."""
    st.markdown("## ACAQuant | Portfolios")
    _render_reporte_ejecutivo()

