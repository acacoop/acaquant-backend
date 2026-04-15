"""Vista Operaciones del dashboard: Cash Flow, Contrapartes, Análisis, Flujo vs AuM."""
import re

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.shared.db import get_db_cashflow, get_db_valuaciones
from dashboard.shared.format import fmt_nom


# ==========================================
# OPERACIONES — Cash Flow
# ==========================================
@st.cache_data(ttl=300, show_spinner=False)
def _cargar_movimientos():
    db = get_db_cashflow()
    docs = list(db["Movimientos"].find({}, {"_id": 0, "fecha": 1, "total": 1, "unidad": 1, "informacion": 1, "cuenta": 1}))
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    df["fecha"] = pd.to_datetime(df["fecha"], format="%d/%m/%Y", errors="coerce")
    df["total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0)
    return df.dropna(subset=["fecha"]).sort_values("fecha")


@st.cache_data(ttl=600, show_spinner=False)
def _cargar_accionistas():
    """Devuelve dict {cuenta: accionista} desde CashFlow.Accionistas."""
    db = get_db_cashflow()
    docs = list(db["Accionistas"].find({}, {"_id": 0, "cuenta": 1, "accionista": 1}))
    return {d["cuenta"]: d["accionista"] for d in docs if "cuenta" in d}


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_contrapartes():
    db = get_db_cashflow()
    docs = list(db["Flujo"].find(
        {}, {"_id": 0, "bruto": 1, "concertacion": 1, "contraparte": 1, "moneda": 1, "tipoOperacion": 1}
    ))
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    df["concertacion"] = pd.to_datetime(df["concertacion"], errors="coerce")
    df["bruto"] = pd.to_numeric(df["bruto"], errors="coerce").fillna(0)
    df = df.dropna(subset=["concertacion"]).sort_values("concertacion")

    # Join segmento desde CashFlow.Contrapartes
    cp_docs = list(db["Contrapartes"].find({}, {"_id": 0, "contraparte": 1, "segmento": 1}))
    seg_map = {d["contraparte"]: d.get("segmento") or "Sin clasificar" for d in cp_docs}
    df["segmento"] = df["contraparte"].map(seg_map).fillna("Sin clasificar")

    return df


def _render_flujo_chart_y_cards(df_f, monedas_sel, granularity):
    """Renderiza chart por moneda + tarjetas de resumen sobre df_f ya filtrado."""
    COLOR_ARS = "#094293"
    COLOR_USD = "#00cc66"

    if granularity == "Mensual":
        df_f = df_f.copy()
        df_f["_key"] = df_f["fecha"].dt.strftime("%Y-%m")
        df_agg = df_f.groupby(["_key", "unidad"], as_index=False)["total"].sum()
        df_agg = df_agg.sort_values("_key")
        df_agg["label"] = pd.to_datetime(df_agg["_key"] + "-01").dt.strftime("%b %Y")
    else:
        df_f = df_f.copy()
        df_f["_key"] = df_f["fecha"].dt.strftime("%Y-%m-%d")
        df_agg = df_f.groupby(["_key", "unidad"], as_index=False)["total"].sum()
        df_agg = df_agg.sort_values("_key")
        df_agg["label"] = pd.to_datetime(df_agg["_key"]).dt.strftime("%d/%m/%y")

    x_order = list(dict.fromkeys(df_agg["label"].tolist()))
    df_agg = df_agg.drop(columns="_key")

    def make_chart(moneda, color):
        data = df_agg[df_agg["unidad"] == moneda].copy()
        if data.empty:
            return None
        max_abs = data["total"].abs().max()
        if max_abs >= 1e9:
            data["valor"] = data["total"] / 1e9
            y_title = f"Billones {moneda}"
            y_fmt = ",.2f"
        elif max_abs >= 1e6:
            data["valor"] = data["total"] / 1e6
            y_title = f"Millones {moneda}"
            y_fmt = ",.1f"
        elif max_abs >= 1e3:
            data["valor"] = data["total"] / 1e3
            y_title = f"Miles {moneda}"
            y_fmt = ",.1f"
        else:
            data["valor"] = data["total"]
            y_title = moneda
            y_fmt = ",.0f"

        x_enc = alt.X("label:O", sort=x_order, axis=alt.Axis(labelAngle=-45, title=None))
        y_enc = alt.Y("valor:Q", axis=alt.Axis(title=y_title, titleColor=color, format=y_fmt))
        tip = [alt.Tooltip("label:O", title="Fecha"),
               alt.Tooltip("valor:Q", title=y_title, format=y_fmt)]

        bars = (
            alt.Chart(data)
            .mark_bar(color=color, opacity=0.85,
                      cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
            .encode(x=x_enc, y=y_enc, tooltip=tip)
        )

        if granularity != "Mensual":
            return bars.properties(height=220)

        max_val = data["valor"].abs().max()
        umbral = max_val * 0.20
        padding = max_val * 0.04

        data["mid"] = data["valor"] / 2
        data["exterior"] = data["valor"].apply(
            lambda v: v + padding if v >= 0 else v - padding
        )

        grandes = data[data["valor"].abs() >= umbral]
        chicas = data[data["valor"].abs() < umbral]

        txt_inside = (
            alt.Chart(grandes)
            .mark_text(align="center", fontSize=10, fontWeight=600, color="white")
            .encode(x=x_enc, y=alt.Y("mid:Q"), text=alt.Text("valor:Q", format=",.1f"))
        )
        txt_outside = (
            alt.Chart(chicas)
            .mark_text(align="center", fontSize=10, fontWeight=600, color=color)
            .encode(x=x_enc, y=alt.Y("exterior:Q"), text=alt.Text("valor:Q", format=",.1f"))
        )

        return alt.layer(bars, txt_inside, txt_outside).properties(height=260)

    for moneda, color in [("ARS", COLOR_ARS), ("USD", COLOR_USD)]:
        if moneda not in monedas_sel:
            continue
        chart = make_chart(moneda, color)
        if chart:
            st.altair_chart(chart, use_container_width=True)

    tarjeta_cols = st.columns(len(monedas_sel)) if monedas_sel else []
    for i, moneda in enumerate(monedas_sel):
        color = COLOR_ARS if moneda == "ARS" else COLOR_USD
        sub = df_f[df_f["unidad"] == moneda]
        entradas = sub[sub["total"] > 0]["total"].sum()
        salidas = sub[sub["total"] < 0]["total"].sum()
        neto = entradas + salidas
        neto_color = "#00cc66" if neto >= 0 else "#ff4444"
        with tarjeta_cols[i]:
            st.markdown(f"""
<div style="border:1px solid {color};border-radius:8px;padding:14px 18px;margin-top:8px">
  <div style="color:{color};font-weight:700;font-size:13px;letter-spacing:1px;margin-bottom:10px">{moneda}</div>
  <div style="display:flex;gap:24px;flex-wrap:wrap">
    <div>
      <div style="color:#888;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Entradas</div>
      <div style="color:#00cc66;font-size:20px;font-weight:600">{fmt_nom(entradas)}</div>
    </div>
    <div>
      <div style="color:#888;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Salidas</div>
      <div style="color:#ff4444;font-size:20px;font-weight:600">{fmt_nom(abs(salidas))}</div>
    </div>
    <div>
      <div style="color:#888;font-size:10px;text-transform:uppercase;letter-spacing:.5px">Flujo Neto</div>
      <div style="color:{neto_color};font-size:20px;font-weight:600">{("-" if neto < 0 else "+") + fmt_nom(abs(neto))}</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)


_COOP_RE = re.compile(r"\bcoop", re.IGNORECASE)


def _es_cooperativa(cuenta_str):
    return bool(cuenta_str) and bool(_COOP_RE.search(str(cuenta_str)))


def vista_operaciones():
    st.markdown("## ACAQuant | Operaciones")

    tab_cf, tab_cp, tab_analisis_cp, tab_fva = st.tabs(["Cash Flow", "Contrapartes", "Análisis", "Flujo vs AuM"])

    # ── Tab: Cash Flow (sin cambios) ──────────────────────────────────────────
    with tab_cf:
        df = _cargar_movimientos()
        if df.empty:
            st.warning("Sin datos. Ejecutá `main_cashflow.py` para cargar el historial.")
        else:
            min_date = df["fecha"].min().date()
            max_date = df["fecha"].max().date()

            COLOR_ARS = "#094293"
            COLOR_USD = "#00cc66"

            # ── Slider (fila completa) ────────────────────────────────────────
            rango = st.slider(
                "Rango de fechas",
                min_value=min_date,
                max_value=max_date,
                value=(min_date, max_date),
                format="DD/MM/YY",
                key="ops_rango",
            )

            # ── Filtros en una fila ───────────────────────────────────────────
            c_ars, c_usd, c_sep, c_gran = st.columns([1, 1, 3, 3])
            with c_ars:
                show_ars = st.checkbox("ARS", value=True, key="ops_ars")
            with c_usd:
                show_usd = st.checkbox("USD", value=True, key="ops_usd")
            with c_gran:
                granularity = st.radio(
                    "", ["Diario", "Mensual"], horizontal=True,
                    key="ops_gran", label_visibility="collapsed"
                )

            # ── Filtro cuentas ────────────────────────────────────────────────
            acc_map = _cargar_accionistas()   # {cuenta: accionista}

            fa_col, fb_col = st.columns([2, 5])
            with fa_col:
                filtro_acc = st.selectbox(
                    "Cuentas",
                    ["Todas", "Sin accionistas", "Solo accionistas", "Solo cooperativas"],
                    key="ops_filtro_acc",
                    label_visibility="collapsed",
                )

            todas_cuentas = df["cuenta"].dropna().unique().tolist()
            if filtro_acc == "Solo accionistas":
                accionistas_disponibles = sorted({
                    acc_map[c] for c in todas_cuentas if c in acc_map
                })
                opciones = ["Todos"] + accionistas_disponibles
                label_sel = "Accionista"
            elif filtro_acc == "Sin accionistas":
                opciones = ["Todas"] + sorted(c for c in todas_cuentas if c not in acc_map)
                label_sel = "Cuenta"
            elif filtro_acc == "Solo cooperativas":
                cuentas_coop = sorted(
                    c for c in todas_cuentas
                    if c not in acc_map and _es_cooperativa(c)
                )
                opciones = ["Todas"] + cuentas_coop
                label_sel = "Cooperativa"
            else:
                opciones = ["Todas"] + sorted(todas_cuentas)
                label_sel = "Cuenta"

            with fb_col:
                seleccion = st.selectbox(
                    label_sel, opciones, key=f"ops_sel_{filtro_acc}",
                    label_visibility="collapsed",
                )

            # ── Filtrar ───────────────────────────────────────────────────────
            df_f = df[(df["fecha"].dt.date >= rango[0]) & (df["fecha"].dt.date <= rango[1])].copy()
            monedas_sel = (["ARS"] if show_ars else []) + (["USD"] if show_usd else [])
            df_f = df_f[df_f["unidad"].isin(monedas_sel)].copy()

            df_f["_accionista"] = df_f["cuenta"].map(acc_map)
            if filtro_acc == "Sin accionistas":
                df_f = df_f[df_f["_accionista"].isna()].copy()
                if seleccion != "Todas":
                    df_f = df_f[df_f["cuenta"] == seleccion].copy()
            elif filtro_acc == "Solo accionistas":
                df_f = df_f[df_f["_accionista"].notna()].copy()
                if seleccion != "Todos":
                    df_f = df_f[df_f["_accionista"] == seleccion].copy()
            elif filtro_acc == "Solo cooperativas":
                df_f = df_f[df_f["_accionista"].isna() & df_f["cuenta"].apply(_es_cooperativa)].copy()
                if seleccion != "Todas":
                    df_f = df_f[df_f["cuenta"] == seleccion].copy()
            else:
                if seleccion != "Todas":
                    df_f = df_f[df_f["cuenta"] == seleccion].copy()

            if df_f.empty:
                st.info("Sin datos para el rango/moneda seleccionados.")
            else:
                _render_flujo_chart_y_cards(df_f, monedas_sel, granularity)

    # ── Tab: Contrapartes ─────────────────────────────────────────────────────
    with tab_cp:
        df_cp = _cargar_contrapartes()
        if df_cp.empty:
            st.warning("Sin datos en CashFlow.Flujo.")
        else:
            df_cp["_mes"] = df_cp["concertacion"].dt.strftime("%Y-%m")
            df_cp["label"] = df_cp["concertacion"].dt.strftime("%b %Y")

            # ── Filtros: segmento (izq) + moneda (der) en una sola fila ─────────
            segs_disp   = sorted(df_cp["segmento"].dropna().unique().tolist())
            monedas_disp = sorted(df_cp["moneda"].dropna().unique().tolist())

            # Columnas: título_seg | seg×N | spacer | título_mon | mon×N
            n_seg = len(segs_disp)
            n_mon = len(monedas_disp)
            widths = [0.6] + [0.7] * n_seg + [3] + [0.6] + [0.7] * n_mon
            fcols = st.columns(widths)

            with fcols[0]:
                st.caption("SEGMENTO")
            segs_sel = []
            for i, s in enumerate(segs_disp):
                with fcols[1 + i]:
                    if st.checkbox(s, value=True, key=f"cp_seg_{s}"):
                        segs_sel.append(s)

            with fcols[1 + n_seg + 1]:
                st.caption("MONEDA")
            monedas_sel_cp = []
            for i, m in enumerate(monedas_disp):
                with fcols[1 + n_seg + 2 + i]:
                    if st.checkbox(m, value=True, key=f"cp_mon_{m}"):
                        monedas_sel_cp.append(m)

            df_cp = df_cp[df_cp["segmento"].isin(segs_sel)].copy() if segs_sel else df_cp.iloc[0:0]
            df_cp = df_cp[df_cp["moneda"].isin(monedas_sel_cp)].copy() if monedas_sel_cp else df_cp.iloc[0:0]

            if df_cp.empty:
                st.info("Sin datos para las monedas seleccionadas.")
            else:
                labels_all = (
                    df_cp.drop_duplicates("_mes")
                    .sort_values("_mes")["label"]
                    .tolist()
                )

                # ── Selector de rango de fechas ───────────────────────────────
                if len(labels_all) >= 2:
                    desde_lbl, hasta_lbl = st.select_slider(
                        "Período",
                        options=labels_all,
                        value=(labels_all[0], labels_all[-1]),
                        key="cp_rango",
                    )
                else:
                    desde_lbl = hasta_lbl = labels_all[0]

                desde_mes = df_cp.loc[df_cp["label"] == desde_lbl, "_mes"].iloc[0]
                hasta_mes = df_cp.loc[df_cp["label"] == hasta_lbl, "_mes"].iloc[0]
                df_f = df_cp[(df_cp["_mes"] >= desde_mes) & (df_cp["_mes"] <= hasta_mes)].copy()

                # Un gráfico por moneda seleccionada
                COLOR_CP = {"ARS": "#094293", "USD": "#00cc66"}
                for moneda in monedas_sel_cp:
                    df_m = df_f[df_f["moneda"] == moneda].copy()
                    if df_m.empty:
                        continue
                    df_mes = (
                        df_m.groupby(["_mes", "label"], as_index=False)["bruto"]
                        .sum()
                        .sort_values("_mes")
                        .reset_index(drop=True)
                    )
                    df_mes["acumulado"] = df_mes["bruto"].cumsum()
                    mes_order = df_mes["label"].tolist()
                    color = COLOR_CP.get(moneda, "#094293")

                    max_val = df_mes["acumulado"].abs().max()
                    if max_val >= 1e9:
                        df_mes["y"] = df_mes["acumulado"] / 1e9
                        y_title = f"Billones {moneda}"
                        y_fmt   = ",.2f"
                    elif max_val >= 1e6:
                        df_mes["y"] = df_mes["acumulado"] / 1e6
                        y_title = f"Millones {moneda}"
                        y_fmt   = ",.1f"
                    else:
                        df_mes["y"] = df_mes["acumulado"]
                        y_title = moneda
                        y_fmt   = ",.0f"

                    base = alt.Chart(df_mes).encode(
                        x=alt.X("label:O", sort=mes_order, axis=alt.Axis(labelAngle=-45, title=None)),
                    )
                    area = base.mark_area(color=color, opacity=0.12, interpolate="monotone").encode(
                        y=alt.Y("y:Q", axis=alt.Axis(title=y_title, format=y_fmt))
                    )
                    line = base.mark_line(color=color, strokeWidth=2, interpolate="monotone",
                                          point=alt.OverlayMarkDef(size=60, color=color)).encode(
                        y=alt.Y("y:Q"),
                        tooltip=[
                            alt.Tooltip("label:O", title="Mes"),
                            alt.Tooltip("y:Q", format=y_fmt, title=y_title),
                        ]
                    )
                    st.altair_chart(
                        alt.layer(area, line).properties(height=300),
                        use_container_width=True
                    )

                st.divider()

                # Selector de moneda para la tabla (solo si hay más de una)
                if len(monedas_sel_cp) > 1:
                    moneda_tabla = st.radio(
                        "", monedas_sel_cp, horizontal=True,
                        key="cp_mon_tabla", label_visibility="collapsed"
                    )
                else:
                    moneda_tabla = monedas_sel_cp[0]

                df_tabla = df_f[df_f["moneda"] == moneda_tabla]
                total_tabla = df_tabla["bruto"].sum()
                resumen_cp = (
                    df_tabla.groupby("contraparte", as_index=False)["bruto"]
                    .sum()
                    .sort_values("bruto", ascending=False)
                    .reset_index(drop=True)
                )
                resumen_cp["Bruto"] = resumen_cp["bruto"].apply(lambda v: f"{v:,.0f}")
                resumen_cp["%"]     = (resumen_cp["bruto"] / total_tabla * 100).apply(lambda v: f"{v:.1f}%")

                h_cp = 38 + 35 * len(resumen_cp)

                col_izq, col_der = st.columns(2)

                with col_izq:
                    st.markdown(
                        f"<div style='font-size:12px;color:#888'>Contrapartes · {moneda_tabla} · {desde_lbl} → {hasta_lbl}</div>",
                        unsafe_allow_html=True,
                    )
                    ev_cp = st.dataframe(
                        resumen_cp[["contraparte", "Bruto", "%"]],
                        hide_index=True, use_container_width=True,
                        height=h_cp,
                        on_select="rerun",
                        selection_mode="single-row",
                        key="cp_tabla",
                    )

                with col_der:
                    sel_rows = ev_cp.selection.rows if ev_cp.selection.rows else []
                    if not sel_rows:
                        st.markdown(
                            "<div style='font-size:13px;color:#888;padding:8px'>"
                            "Seleccioná una contraparte para ver el detalle.</div>",
                            unsafe_allow_html=True,
                        )
                    else:
                        cp_sel = resumen_cp.iloc[sel_rows[0]]["contraparte"]
                        df_det = df_tabla[df_tabla["contraparte"] == cp_sel].copy()

                        st.markdown(
                            f"<div style='font-size:11px;color:#888;padding:2px 4px 6px'>"
                            f"{cp_sel} · {moneda_tabla}</div>",
                            unsafe_allow_html=True,
                        )

                        # Tabla 1: volumen mensual (más reciente → más antiguo), seleccionable
                        df_mes_det = (
                            df_det.groupby(["_mes", "label"], as_index=False)["bruto"]
                            .sum()
                            .sort_values("_mes", ascending=False)
                            .reset_index(drop=True)
                        )
                        df_mes_det["Bruto"] = df_mes_det["bruto"].apply(lambda v: f"{v:,.0f}")
                        # h_mes: contenido natural pero sin pasar de la mitad
                        h_mes = min(38 + 35 * len(df_mes_det), h_cp // 2)

                        ev_mes = st.dataframe(
                            df_mes_det[["label", "Bruto"]].rename(columns={"label": "Mes"}),
                            hide_index=True, use_container_width=True,
                            height=h_mes,
                            on_select="rerun",
                            selection_mode="multi-row",
                            key="cp_mes_tabla",
                        )

                        # Tabla 2: tipoOperacion filtrada por meses seleccionados (o total)
                        sel_mes_rows = ev_mes.selection.rows if ev_mes.selection.rows else []
                        if sel_mes_rows:
                            meses_sel = df_mes_det.iloc[sel_mes_rows]["_mes"].tolist()
                            df_tipo_src = df_det[df_det["_mes"].isin(meses_sel)]
                            tipo_titulo = ", ".join(df_mes_det.iloc[sel_mes_rows]["label"].tolist())
                        else:
                            df_tipo_src = df_det
                            tipo_titulo = "Total"

                        total_tipo = df_tipo_src["bruto"].sum()
                        df_tipo = (
                            df_tipo_src.groupby("tipoOperacion", as_index=False)["bruto"]
                            .sum()
                            .sort_values("bruto", ascending=False)
                            .reset_index(drop=True)
                        )
                        df_tipo["Bruto"] = df_tipo["bruto"].apply(lambda v: f"{v:,.0f}")
                        df_tipo["%"]     = (df_tipo["bruto"] / total_tipo * 100).apply(lambda v: f"{v:.1f}%") if total_tipo else "—"
                        # h_tipo: ocupa el espacio restante hasta h_cp, ajustado al contenido
                        h_tipo = min(38 + 35 * len(df_tipo), h_cp - h_mes)

                        st.markdown(
                            f"<div style='font-size:10px;color:#888;padding:2px 4px 2px'>Tipo op. · {tipo_titulo}</div>",
                            unsafe_allow_html=True,
                        )
                        st.dataframe(
                            df_tipo[["tipoOperacion", "Bruto", "%"]].rename(columns={"tipoOperacion": "Tipo"}),
                            hide_index=True, use_container_width=True,
                            height=h_tipo,
                        )

    # ── Tab: Análisis contrapartes ────────────────────────────────────────────
    with tab_analisis_cp:
        df_an = _cargar_contrapartes()
        if df_an.empty:
            st.warning("Sin datos en CashFlow.Flujo.")
        else:
            df_an["_mes"] = df_an["concertacion"].dt.strftime("%Y-%m")
            df_an["label"] = df_an["concertacion"].dt.strftime("%b %Y")

            monedas_an = sorted(df_an["moneda"].dropna().unique().tolist())

            segmentos = sorted(df_an["segmento"].dropna().unique().tolist())

            # ── Fila: Modo | Moneda ───────────────────────────────────────────
            col_modo, col_mon = st.columns([3, 1])
            with col_modo:
                modo = st.radio(
                    "Modo", ["Individual", "Comparativo"],
                    horizontal=True, key="an_modo",
                )
            with col_mon:
                if len(monedas_an) > 1:
                    moneda_an = st.radio(
                        "Moneda", monedas_an, horizontal=True, key="an_moneda",
                    )
                else:
                    moneda_an = monedas_an[0]
                    st.markdown(
                        f"<div style='font-size:12px;color:#888;padding-top:4px'>Moneda: {moneda_an}</div>",
                        unsafe_allow_html=True,
                    )

            # ── Filtro segmento ───────────────────────────────────────────────
            with st.expander("Segmento", expanded=False):
                segmentos_sel = st.multiselect(
                    "", segmentos, default=segmentos,
                    key="an_segmentos", label_visibility="collapsed",
                )

            df_an = df_an[
                (df_an["moneda"] == moneda_an) &
                (df_an["segmento"].isin(segmentos_sel) if segmentos_sel else True)
            ].copy()

            meses_an = (
                df_an.drop_duplicates("_mes")
                .sort_values("_mes")["label"]
                .tolist()
            )
            contrapartes_an = sorted(df_an["contraparte"].dropna().unique().tolist())

            if len(meses_an) < 2:
                st.info("Necesitás al menos 2 meses de datos para ver la evolución.")
            else:

                desde_an, hasta_an = st.select_slider(
                    "Período",
                    options=meses_an,
                    value=(meses_an[0], meses_an[-1]),
                    key="an_rango",
                )
                desde_mes_an = df_an.loc[df_an["label"] == desde_an, "_mes"].iloc[0]
                hasta_mes_an = df_an.loc[df_an["label"] == hasta_an, "_mes"].iloc[0]
                df_an_f = df_an[(df_an["_mes"] >= desde_mes_an) & (df_an["_mes"] <= hasta_mes_an)]

                # Rango completo de meses (para rellenar con 0 los meses sin actividad)
                full_range = pd.date_range(desde_mes_an + "-01", hasta_mes_an + "-01", freq="MS")
                df_rango = pd.DataFrame({
                    "_mes":  full_range.strftime("%Y-%m"),
                    "label": full_range.strftime("%b %Y"),
                })
                mes_order_an = df_rango["label"].tolist()

                color_an = {"ARS": "#094293", "USD": "#00cc66"}.get(moneda_an, "#094293")

                if modo == "Individual":
                    cp_ind = st.selectbox(
                        "Contraparte", [None] + contrapartes_an, index=0,
                        format_func=lambda x: "Elegí una contraparte..." if x is None else x,
                        key="an_cp_ind",
                    )
                    if cp_ind is None:
                        st.info("Seleccioná una contraparte para ver la evolución mensual.")
                    else:
                        df_agg = (
                            df_an_f[df_an_f["contraparte"] == cp_ind]
                            .groupby("_mes", as_index=False)["bruto"].sum()
                        )
                        df_plot = (
                            df_rango.merge(df_agg, on="_mes", how="left")
                            .fillna({"bruto": 0})
                        )
                        max_v = df_plot["bruto"].abs().max()
                        if max_v >= 1e9:
                            df_plot["y"] = df_plot["bruto"] / 1e9; y_ttl = f"Billones {moneda_an}"; y_f = ",.2f"
                        elif max_v >= 1e6:
                            df_plot["y"] = df_plot["bruto"] / 1e6; y_ttl = f"Millones {moneda_an}"; y_f = ",.1f"
                        else:
                            df_plot["y"] = df_plot["bruto"]; y_ttl = moneda_an; y_f = ",.0f"
                        chart = (
                            alt.Chart(df_plot)
                            .mark_line(color=color_an, strokeWidth=2,
                                       point=alt.OverlayMarkDef(size=60, color=color_an))
                            .encode(
                                x=alt.X("label:O", sort=mes_order_an, axis=alt.Axis(labelAngle=-45, title=None)),
                                y=alt.Y("y:Q", axis=alt.Axis(title=y_ttl, format=y_f, tickCount=6)),
                                tooltip=[alt.Tooltip("label:O", title="Mes"),
                                         alt.Tooltip("y:Q", format=y_f, title=y_ttl)],
                            )
                            .properties(height=400)
                        )
                        st.altair_chart(chart, use_container_width=True)

                else:  # Comparativo
                    cps_sel = st.multiselect(
                        "Contrapartes", contrapartes_an, default=[],
                        placeholder="Elegí una o más...", key="an_cp_comp",
                    )
                    if not cps_sel:
                        st.info("Seleccioná al menos una contraparte para comparar.")
                    else:
                        # Rellenar con 0 para cada contraparte en el rango completo
                        pieces = []
                        for cp in cps_sel:
                            df_agg = (
                                df_an_f[df_an_f["contraparte"] == cp]
                                .groupby("_mes", as_index=False)["bruto"].sum()
                            )
                            df_cp = df_rango.merge(df_agg, on="_mes", how="left").fillna({"bruto": 0})
                            df_cp["contraparte"] = cp
                            pieces.append(df_cp)
                        df_plot = pd.concat(pieces, ignore_index=True)

                        _step = 20_000_000_000
                        _max  = int(df_plot["bruto"].max())
                        _ticks = list(range(0, _max + _step, _step))
                        chart = (
                            alt.Chart(df_plot)
                            .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=60))
                            .encode(
                                x=alt.X("label:O", sort=mes_order_an, axis=alt.Axis(labelAngle=-45, title=None)),
                                y=alt.Y("bruto:Q",
                                        title=f"Bruto ({moneda_an})",
                                        axis=alt.Axis(format=",.0f", values=_ticks)),
                                color=alt.Color("contraparte:N",
                                                scale=alt.Scale(scheme="tableau20"),
                                                legend=alt.Legend(orient="top", labelFontSize=11)),
                                tooltip=[
                                    alt.Tooltip("label:O", title="Mes"),
                                    alt.Tooltip("contraparte:N", title="Contraparte"),
                                    alt.Tooltip("bruto:Q", format=",.0f", title=f"Bruto ({moneda_an})"),
                                ],
                            )
                            .properties(height=400)
                        )
                        st.altair_chart(chart, use_container_width=True)

    with tab_fva:
        _render_flujo_vs_aum()


# ==========================================
# Flujo vs AuM (Fondos)
# ==========================================

@st.cache_data(ttl=300, show_spinner=False)
def _cargar_fondos_flujo_aum():
    """
    Devuelve:
      - fondos: lista de emisores con segmento=Fondos
      - df_flujo: (emisor, fecha, bruto) — trades individuales ARS
      - df_aum:   (emisor, fecha_snapshot, valuacion) — AuM diario agregado por emisor
    """
    db_cf  = get_db_cashflow()
    db_val = get_db_valuaciones()

    # 1. Fondos
    fondos = list(dict.fromkeys(
        d["contraparte"] for d in db_cf["Contrapartes"].find(
            {"segmento": "Fondos"}, {"_id": 0, "contraparte": 1}
        )
    ))
    if not fondos:
        return [], pd.DataFrame(), pd.DataFrame()

    # 2. Flujo ARS — nivel trade
    flujo_docs = list(db_cf["Flujo"].find(
        {"contraparte": {"$in": fondos}, "moneda": "ARS"},
        {"_id": 0, "contraparte": 1, "concertacion": 1, "bruto": 1}
    ))
    if flujo_docs:
        df_fl = pd.DataFrame(flujo_docs)
        df_fl["fecha"]  = pd.to_datetime(df_fl["concertacion"], errors="coerce")
        df_fl["bruto"]  = pd.to_numeric(df_fl["bruto"], errors="coerce").fillna(0)
        df_fl["emisor"] = df_fl["contraparte"]
        df_flujo = df_fl[["emisor", "fecha", "bruto"]].dropna(subset=["fecha"]).sort_values("fecha")
    else:
        df_flujo = pd.DataFrame()

    # 3. AuM diario por emisor (suma valuacion de todas las unidades FCI del emisor)
    assets_docs = list(db_val["Assets"].find(
        {"EMISOR": {"$in": fondos}, "CARTERA": "CARTERA FCI"},
        {"_id": 0, "unidad": 1, "EMISOR": 1}
    ))
    if assets_docs:
        df_assets   = pd.DataFrame(assets_docs)
        unidades    = df_assets["unidad"].tolist()
        emisor_map  = df_assets.set_index("unidad")["EMISOR"].to_dict()
        aum_docs    = list(db_val["AuM"].find(
            {"unidad": {"$in": unidades}},
            {"_id": 0, "unidad": 1, "valuacion": 1, "fecha_snapshot": 1}
        ))
        if aum_docs:
            df_a = pd.DataFrame(aum_docs)
            df_a["valuacion"]      = pd.to_numeric(df_a["valuacion"], errors="coerce").fillna(0)
            df_a["fecha_snapshot"] = pd.to_datetime(df_a["fecha_snapshot"], errors="coerce")
            df_a["emisor"]         = df_a["unidad"].map(emisor_map)
            df_aum = (
                df_a.dropna(subset=["fecha_snapshot", "emisor"])
                .groupby(["emisor", "fecha_snapshot"], as_index=False)["valuacion"]
                .sum()
                .sort_values("fecha_snapshot")
            )
        else:
            df_aum = pd.DataFrame()
    else:
        df_aum = pd.DataFrame()

    return fondos, df_flujo, df_aum


def _render_flujo_vs_aum():
    fondos, df_flujo, df_aum = _cargar_fondos_flujo_aum()

    if not fondos:
        st.caption("Sin contrapartes con segmento=Fondos.")
        return

    col_emisor, col_desde, col_hasta = st.columns([2, 1, 1])
    with col_emisor:
        emisor = st.selectbox(
            "Emisor", sorted(fondos),
            label_visibility="collapsed",
            key="fva_emisor",
        )

    fl = df_flujo[df_flujo["emisor"] == emisor].copy() if not df_flujo.empty else pd.DataFrame()
    am = df_aum[df_aum["emisor"] == emisor].copy()    if not df_aum.empty  else pd.DataFrame()

    if am.empty:
        st.caption("Sin datos de AuM para este emisor.")
        return

    data_min = am["fecha_snapshot"].min().date()
    data_max  = pd.Timestamp.today().normalize().date()

    with col_desde:
        desde = st.date_input(
            "Desde", value=data_min,
            min_value=data_min, max_value=data_max,
            key="fva_desde",
        )
    with col_hasta:
        hasta = st.date_input(
            "Hasta", value=data_max,
            min_value=data_min, max_value=data_max,
            key="fva_hasta",
        )

    if desde > hasta:
        st.warning("'Desde' debe ser anterior a 'Hasta'.")
        return

    start_date = pd.Timestamp(desde)
    end_date   = pd.Timestamp(hasta)

    # AuM forward-fill → línea continua sin gaps
    am_ff = (
        am[["fecha_snapshot", "valuacion"]]
        .set_index("fecha_snapshot")
        .reindex(pd.date_range(start_date, end_date, freq="D"))
        .ffill()
        .reset_index()
        .rename(columns={"index": "fecha_snapshot"})
        .dropna()
    )

    # Flujo: solo días con operaciones en el rango seleccionado
    if not fl.empty:
        fl = fl[(fl["fecha"] >= start_date) & (fl["fecha"] <= end_date)].sort_values("fecha")
        fl = fl.groupby("fecha", as_index=False)["bruto"].sum()

    # Formato Y dinámico
    y_expr = (
        "abs(datum.value) >= 1e9 ? format(datum.value/1e9, ',.2f') + 'B' : "
        "abs(datum.value) >= 1e6 ? format(datum.value/1e6, ',.1f') + 'M' : "
        "format(datum.value, ',.0f')"
    )
    rango_dias = (end_date - start_date).days
    if rango_dias <= 30:
        tick_count = "day"
        x_fmt = "%d %b"
    elif rango_dias <= 120:
        tick_count = "week"
        x_fmt = "%d %b"
    else:
        tick_count = "month"
        x_fmt = "%b %Y"
    x_axis = alt.Axis(format=x_fmt, labelAngle=-45, tickCount=tick_count, title=None)

    layers = []

    # Barras flujo — verde entrada / rojo salida
    if not fl.empty:
        bars = (
            alt.Chart(fl)
            .mark_bar(opacity=0.85)
            .encode(
                x=alt.X("fecha:T", axis=x_axis),
                y=alt.Y("bruto:Q", title="Flujo ARS",
                        axis=alt.Axis(labelExpr=y_expr)),
                color=alt.condition(
                    alt.datum.bruto > 0,
                    alt.value("#00cc66"),
                    alt.value("#e05252"),
                ),
                tooltip=[
                    alt.Tooltip("fecha:T",  title="Fecha", format="%d/%m/%Y"),
                    alt.Tooltip("bruto:Q",  title="Flujo ARS", format=",.0f"),
                ],
            )
        )
        layers.append(bars)

    # Dominio AuM con padding visible (no arranca en 0)
    aum_min = am_ff["valuacion"].min()
    aum_max = am_ff["valuacion"].max()
    aum_pad = (aum_max - aum_min) * 0.15 if aum_max > aum_min else aum_max * 0.05
    aum_scale = alt.Scale(domain=[aum_min - aum_pad, aum_max + aum_pad], zero=False)

    # Línea AuM continua — naranja
    line_aum = (
        alt.Chart(am_ff)
        .mark_line(color="#f4a261", strokeWidth=2)
        .encode(
            x=alt.X("fecha_snapshot:T", axis=x_axis),
            y=alt.Y("valuacion:Q", title="AuM",
                    scale=aum_scale,
                    axis=alt.Axis(labelExpr=y_expr)),
            tooltip=[
                alt.Tooltip("fecha_snapshot:T", title="Fecha", format="%d/%m/%Y"),
                alt.Tooltip("valuacion:Q",       title="AuM",  format=",.0f"),
            ],
        )
    )
    layers.append(line_aum)

    # ── Leyenda con valores actuales ─────────────────────────────────────────
    def fmt_val(v):
        if abs(v) >= 1e9: return f"{v/1e9:,.2f}B"
        if abs(v) >= 1e6: return f"{v/1e6:,.1f}M"
        return f"{v:,.0f}"

    aum_actual   = am_ff["valuacion"].iloc[-1] if not am_ff.empty else 0
    flujo_acum   = fl["bruto"].sum()           if not fl.empty    else 0

    st.caption(f"{emisor} — FLUJO DIARIO vs AuM")
    leg1, leg2, _ = st.columns([1.2, 1.2, 4])
    with leg1:
        st.markdown(
            f"<span style='color:#f4a261;font-size:16px'>■</span> "
            f"<span style='font-size:12px;color:#aaa'>AuM actual</span><br>"
            f"<span style='font-size:13px;font-weight:600'>{fmt_val(aum_actual)}</span>",
            unsafe_allow_html=True,
        )
    with leg2:
        st.markdown(
            f"<span style='color:#00cc66;font-size:16px'>■</span> "
            f"<span style='font-size:12px;color:#aaa'>Flujo acumulado</span><br>"
            f"<span style='font-size:13px;font-weight:600'>{fmt_val(flujo_acum)}</span>",
            unsafe_allow_html=True,
        )

    chart = (
        alt.layer(*layers)
        .resolve_scale(y="independent")
        .properties(height=380)
        .interactive()
    )
    st.altair_chart(chart, use_container_width=True)

