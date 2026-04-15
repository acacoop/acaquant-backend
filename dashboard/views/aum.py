"""Vista AuM del dashboard: FCI, Análisis SG, Tasa Fija, CER, Renta Variable."""
import altair as alt
import pandas as pd
import streamlit as st

from dashboard.shared.db import get_db, get_db_valuaciones
from dashboard.shared.format import df_height


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_aum_ultimo():
    """Último snapshot de AuM (todas las carteras). Para tabs Tasa Fija / CER / RV."""
    db = get_db_valuaciones()
    last = db["AuM"].find_one(
        {}, {"fecha_snapshot": 1, "_id": 0},
        sort=[("fecha_snapshot", -1)],
    )
    if not last:
        return pd.DataFrame()
    fecha = last["fecha_snapshot"]
    docs = list(db["AuM"].find(
        {"fecha_snapshot": fecha},
        {"_id": 0, "id_cuenta": 1, "cuenta": 1, "unidad": 1,
         "cantidad": 1, "valuacion": 1, "fecha_snapshot": 1},
    ))
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    df["valuacion"] = pd.to_numeric(df["valuacion"], errors="coerce").fillna(0)
    df["cantidad"]  = pd.to_numeric(df["cantidad"],  errors="coerce").fillna(0)
    return df


def _fci_unidades():
    db = get_db_valuaciones()
    return [
        a["unidad"]
        for a in db["Assets"].find(
            {"CARTERA": "CARTERA FCI"}, {"unidad": 1, "_id": 0}
        )
        if a.get("unidad")
    ]


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_aum_fci_agg():
    """Agregación server-side para charts: (fecha_snapshot, unidad) → sum(valuacion).

    Colapsa cuentas en Mongo via $group. Usado por charts de evolución FCI y
    Análisis SG. Payload ~5-10× más chico que el histórico raw.
    """
    db = get_db_valuaciones()
    unidades = _fci_unidades()
    if not unidades:
        return pd.DataFrame()
    pipeline = [
        {"$match": {"unidad": {"$in": unidades}}},
        {"$group": {
            "_id":       {"fecha": "$fecha_snapshot", "unidad": "$unidad"},
            "valuacion": {"$sum": "$valuacion"},
        }},
        {"$project": {
            "_id":            0,
            "fecha_snapshot": "$_id.fecha",
            "unidad":         "$_id.unidad",
            "valuacion":      "$valuacion",
        }},
    ]
    rows = list(db["AuM"].aggregate(pipeline))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["valuacion"] = pd.to_numeric(df["valuacion"], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_aum_fci_snapshot(fecha):
    """Raw docs FCI para una fecha puntual. Usado para drill-down (emisor → ticker → cuenta)."""
    if not fecha:
        return pd.DataFrame()
    db = get_db_valuaciones()
    unidades = _fci_unidades()
    if not unidades:
        return pd.DataFrame()
    docs = list(db["AuM"].find(
        {"unidad": {"$in": unidades}, "fecha_snapshot": fecha},
        {"_id": 0, "cuenta": 1, "unidad": 1, "valuacion": 1, "fecha_snapshot": 1},
    ))
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    df["valuacion"] = pd.to_numeric(df["valuacion"], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_assets():
    """Devuelve dict {unidad: {CARTERA, EMISOR, TICKER, CLASE_ACTIVO, CALIFICACION, VENCIMIENTO}}."""
    db = get_db_valuaciones()
    docs = list(db["Assets"].find({}, {"_id": 0, "unidad": 1,
        "CARTERA": 1, "EMISOR": 1, "TICKER": 1,
        "CLASE_ACTIVO": 1, "CALIFICACION": 1, "VENCIMIENTO": 1}))
    return {d["unidad"]: d for d in docs}


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_curvas_tasa_fija():
    """Dict {ticker_corto: {fecha_vencimiento, flujo_vencimiento}} para curva=tasa_fija en Trading.Curvas."""
    docs = list(get_db()["Curvas"].find(
        {"curva": "tasa_fija"},
        {"_id": 0, "ticker_corto": 1, "fecha_vencimiento": 1, "flujo_vencimiento": 1},
    ))
    return {d["ticker_corto"]: d for d in docs}


@st.cache_data(ttl=300, show_spinner=False)
def _cargar_curvas_cer():
    """Dict {ticker_corto: {fecha_vencimiento}} para curva=cer en Trading.Curvas."""
    docs = list(get_db()["Curvas"].find(
        {"curva": "cer"},
        {"_id": 0, "ticker_corto": 1, "fecha_vencimiento": 1},
    ))
    return {d["ticker_corto"]: d for d in docs}


def _render_snapshot_fci(df_fci, key_prefix):
    """Tabla + torta de valuacion FCI por EMISOR para un df ya filtrado por fecha."""
    if df_fci.empty:
        st.info("Sin posiciones FCI para esta fecha.")
        return

    resumen = (
        df_fci.groupby("EMISOR", as_index=False)["valuacion"]
        .sum()
        .sort_values("valuacion", ascending=False)
        .reset_index(drop=True)
    )
    total = resumen["valuacion"].sum()
    resumen["% del Total"] = (resumen["valuacion"] / total * 100).round(2)
    resumen["Valuación"]   = resumen["valuacion"].apply(lambda v: f"{v:,.0f}")
    resumen["% del Total"] = resumen["% del Total"].apply(lambda v: f"{v:.2f}%")

    col_tabla, col_pie = st.columns([2, 1])
    with col_tabla:
        st.markdown(
            f"<div style='font-size:13px;color:#888;margin-bottom:4px'>Total FCI</div>"
            f"<div style='font-size:26px;font-weight:700;color:#094293'>${total:,.0f}</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(
            resumen[["EMISOR", "Valuación", "% del Total"]],
            hide_index=True, use_container_width=True,
            height=df_height(len(resumen)),
        )
    with col_pie:
        df_pie = resumen[resumen["valuacion"] > 0][["EMISOR", "valuacion"]].copy()
        df_pie["pct"]       = (df_pie["valuacion"] / total * 100).round(1)
        df_pie["pct_label"] = df_pie["pct"].apply(lambda x: f"{x:.1f}%")
        base = alt.Chart(df_pie).encode(
            theta=alt.Theta("valuacion:Q", stack=True),
            color=alt.Color("EMISOR:N", scale=alt.Scale(scheme="tableau20"),
                            legend=alt.Legend(orient="bottom", columns=2, labelFontSize=10)),
        )
        arc  = base.mark_arc(innerRadius=45, outerRadius=100).encode(
            tooltip=[alt.Tooltip("EMISOR:N", title="Emisor"),
                     alt.Tooltip("valuacion:Q", title="Valuación", format=",.0f"),
                     alt.Tooltip("pct:Q", title="%", format=".1f")]
        )
        text = base.mark_text(radius=75, size=11, color="white").encode(
            text=alt.Text("pct_label:N"),
        )
        st.altair_chart((arc + text).properties(height=380, padding={"top": 10}),
                        use_container_width=True)


def _render_barras_rango_fci(df_fci_agg, color_field, key_prefix):
    """Barras apiladas por fecha con rango slider. color_field: 'EMISOR' o None (total)."""
    fechas = sorted(df_fci_agg["fecha_snapshot"].unique())
    if len(fechas) < 2:
        st.info("Necesitás al menos 2 fechas de datos.")
        return

    fecha_desde, fecha_hasta = st.select_slider(
        "Período",
        options=fechas,
        value=(fechas[0], fechas[-1]),
        key=f"{key_prefix}_rango",
    )
    df_r = df_fci_agg[
        (df_fci_agg["fecha_snapshot"] >= fecha_desde) &
        (df_fci_agg["fecha_snapshot"] <= fecha_hasta)
    ].copy()

    fechas_rango = sorted(df_r["fecha_snapshot"].unique())

    if color_field:
        df_plot = df_r.groupby(["fecha_snapshot", color_field], as_index=False)["valuacion"].sum()
        chart = (
            alt.Chart(df_plot)
            .mark_bar()
            .encode(
                x=alt.X("fecha_snapshot:O", title="Fecha", sort=fechas_rango,
                         axis=alt.Axis(labelAngle=-45)),
                y=alt.Y("valuacion:Q", title="Valuación (ARS)", stack=True,
                         axis=alt.Axis(format=",.0f")),
                color=alt.Color(f"{color_field}:N", scale=alt.Scale(scheme="tableau20"),
                                legend=alt.Legend(orient="top", columns=4, labelFontSize=10)),
                tooltip=[alt.Tooltip("fecha_snapshot:O", title="Fecha"),
                         alt.Tooltip(f"{color_field}:N", title=color_field),
                         alt.Tooltip("valuacion:Q", format=",.0f", title="Valuación")],
            )
            .properties(height=420)
        )
    else:
        df_plot = df_r.groupby("fecha_snapshot", as_index=False)["valuacion"].sum()
        chart = (
            alt.Chart(df_plot)
            .mark_bar(color="#094293")
            .encode(
                x=alt.X("fecha_snapshot:O", title="Fecha", sort=fechas_rango,
                         axis=alt.Axis(labelAngle=-45)),
                y=alt.Y("valuacion:Q", title="Valuación total FCI (ARS)",
                         axis=alt.Axis(format=",.0f")),
                tooltip=[alt.Tooltip("fecha_snapshot:O", title="Fecha"),
                         alt.Tooltip("valuacion:Q", format=",.0f", title="Valuación")],
            )
            .properties(height=420)
        )

    st.altair_chart(chart, use_container_width=True)


def vista_aum():
    st.markdown("## ACAQuant | AuM")

    df = _cargar_aum_ultimo()
    df_fci_agg = _cargar_aum_fci_agg()
    if df.empty and df_fci_agg.empty:
        st.warning("Sin datos. Ejecutá `main_aum.py` para cargar las posiciones.")
        return

    assets = _cargar_assets()
    for _df in (df, df_fci_agg):
        if not _df.empty:
            _df["CARTERA"] = _df["unidad"].map(lambda u: assets.get(u, {}).get("CARTERA", ""))
            _df["EMISOR"]  = _df["unidad"].map(lambda u: assets.get(u, {}).get("EMISOR",  ""))

    tab_fci, tab_stock_soc, tab_tasa_fija, tab_cer, tab_rv = st.tabs(["FCI", "Análisis SG", "Tasa Fija", "CER", "Renta Variable"])

    # ── Tab 1: FCI (snapshot + stock lado a lado) ─────────────────────────────
    with tab_fci:
        snapshots = sorted(df_fci_agg["fecha_snapshot"].dropna().unique())
        fechas_all = sorted(df_fci_agg["fecha_snapshot"].dropna().unique())

        if not snapshots:
            st.info("Sin datos FCI.")
        else:
            # ── Gráfico a ancho completo arriba ───────────────────────────────
            if len(fechas_all) >= 2:
                fecha_desde, fecha_hasta = st.select_slider(
                    "Período",
                    options=fechas_all,
                    value=(fechas_all[0], fechas_all[-1]),
                    key="aum_fci_rango",
                )
                df_r = df_fci_agg[
                    (df_fci_agg["fecha_snapshot"] >= fecha_desde) &
                    (df_fci_agg["fecha_snapshot"] <= fecha_hasta)
                ]
                df_plot = df_r.groupby("fecha_snapshot", as_index=False)["valuacion"].sum()
                fechas_rango = sorted(df_plot["fecha_snapshot"].unique())
                chart = (
                    alt.Chart(df_plot)
                    .mark_line(color="#094293", strokeWidth=2,
                               point=alt.OverlayMarkDef(size=60, color="#094293"))
                    .encode(
                        x=alt.X("fecha_snapshot:O", title="Fecha", sort=fechas_rango,
                                axis=alt.Axis(labelAngle=-45)),
                        y=alt.Y("valuacion:Q", title="Valuación total FCI",
                                scale=alt.Scale(zero=False),
                                axis=alt.Axis(format=",.0f")),
                        tooltip=[alt.Tooltip("fecha_snapshot:O", title="Fecha"),
                                 alt.Tooltip("valuacion:Q", format=",.0f", title="Valuación")],
                    )
                    .properties(height=400)
                )
                st.altair_chart(chart, use_container_width=True)

            st.divider()

            # ── Dos columnas al mismo nivel ───────────────────────────────────
            fecha_sel = st.select_slider("Fecha snapshot", options=snapshots,
                                         value=snapshots[-1], key="aum_snap_fecha")
            df_fci_dia = _cargar_aum_fci_snapshot(fecha_sel)
            if not df_fci_dia.empty:
                df_fci_dia["EMISOR"] = df_fci_dia["unidad"].map(
                    lambda u: assets.get(u, {}).get("EMISOR", "")
                )

            resumen = (
                df_fci_dia.groupby("EMISOR", as_index=False)["valuacion"]
                .sum()
                .sort_values("valuacion", ascending=False)
                .reset_index(drop=True)
            )
            total = resumen["valuacion"].sum()
            resumen["Val."] = resumen["valuacion"].apply(lambda v: f"{v:,.0f}")
            resumen["%"]    = (resumen["valuacion"] / total * 100).apply(lambda v: f"{v:.1f}%")

            h_emisor = 38 + 35 * len(resumen)

            col_izq, col_der = st.columns(2)

            with col_izq:
                st.markdown(
                    f"<div style='font-size:12px;color:#888'>Total FCI</div>"
                    f"<div style='font-size:22px;font-weight:700;color:#094293'>${total:,.0f}</div>",
                    unsafe_allow_html=True,
                )
                ev_fci = st.dataframe(
                    resumen[["EMISOR", "Val.", "%"]],
                    hide_index=True, use_container_width=True,
                    height=h_emisor,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="aum_fci_tabla",
                )

            with col_der:
                sel_rows = ev_fci.selection.rows if ev_fci.selection.rows else []
                if not sel_rows:
                    with st.container(height=h_emisor, border=False):
                        st.markdown(
                            "<div style='font-size:13px;color:#888;padding:8px'>"
                            "Seleccioná un emisor para ver los fondos.</div>",
                            unsafe_allow_html=True,
                        )
                else:
                    emisor_det = resumen.iloc[sel_rows[0]]["EMISOR"]
                    df_det_src = df_fci_dia[df_fci_dia["EMISOR"] == emisor_det].copy()
                    df_det_src["TICKER"] = df_det_src["unidad"].map(
                        lambda u: assets.get(u, {}).get("TICKER", u)
                    )
                    df_det = (
                        df_det_src.groupby("TICKER", as_index=False)["valuacion"]
                        .sum()
                        .sort_values("valuacion", ascending=False)
                        .reset_index(drop=True)
                    )
                    with st.container(height=h_emisor, border=False):
                        st.markdown(
                            f"<div style='font-size:11px;color:#888;padding:2px 4px 6px'>{emisor_det}</div>",
                            unsafe_allow_html=True,
                        )
                        for _, row in df_det.iterrows():
                            ticker_nom = row["TICKER"]
                            val_total  = row["valuacion"]
                            with st.expander(f"{ticker_nom}   —   ${val_total:,.0f}"):
                                df_cuentas = (
                                    df_det_src[df_det_src["TICKER"] == ticker_nom]
                                    [["cuenta", "valuacion"]]
                                    .groupby("cuenta", as_index=False)["valuacion"].sum()
                                    .sort_values("valuacion", ascending=False)
                                    .reset_index(drop=True)
                                )
                                for _, cr in df_cuentas.iterrows():
                                    st.markdown(
                                        f"<div style='display:flex;justify-content:space-between;"
                                        f"padding:2px 4px;font-size:13px'>"
                                        f"<span>{cr['cuenta']}</span>"
                                        f"<span style='font-weight:600'>${cr['valuacion']:,.0f}</span>"
                                        f"</div>",
                                        unsafe_allow_html=True,
                                    )

    # ── Tab 2: Stock Soc. Gerente ─────────────────────────────────────────────
    with tab_stock_soc:
        snapshots  = sorted(df_fci_agg["fecha_snapshot"].dropna().unique())
        emisores   = sorted(df_fci_agg["EMISOR"].dropna().unique())
        fechas_all = snapshots

        if not snapshots:
            st.info("Sin datos FCI.")
        elif len(fechas_all) < 2:
            st.info("Necesitás al menos 2 fechas de datos.")
        else:
            modo = st.radio(
                "Modo", ["Individual", "Comparativo (base 100)"],
                horizontal=True, key="aum_soc_modo",
            )

            fecha_desde, fecha_hasta = st.select_slider(
                "Período",
                options=fechas_all,
                value=(fechas_all[0], fechas_all[-1]),
                key="aum_soc_rango",
            )

            if modo == "Individual":
                emisor_sel = st.selectbox(
                    "Soc. Gerente", [None] + emisores, index=0,
                    format_func=lambda x: "Elegí una Soc. Gerente..." if x is None else x,
                    key="aum_soc_emisores",
                )
                if emisor_sel is None:
                    st.info("Seleccioná una Soc. Gerente para ver la evolución.")
                else:
                    df_r = df_fci_agg[
                        (df_fci_agg["fecha_snapshot"] >= fecha_desde) &
                        (df_fci_agg["fecha_snapshot"] <= fecha_hasta) &
                        (df_fci_agg["EMISOR"] == emisor_sel)
                    ]
                    df_plot = df_r.groupby("fecha_snapshot", as_index=False)["valuacion"].sum()
                    f_rango = sorted(df_plot["fecha_snapshot"].unique())
                    chart = (
                        alt.Chart(df_plot)
                        .mark_line(color="#094293", strokeWidth=2,
                                   point=alt.OverlayMarkDef(size=60, color="#094293"))
                        .encode(
                            x=alt.X("fecha_snapshot:O", title="Fecha", sort=f_rango,
                                    axis=alt.Axis(labelAngle=-45)),
                            y=alt.Y("valuacion:Q", title="Valuación (ARS)",
                                    scale=alt.Scale(zero=False),
                                    axis=alt.Axis(format=",.0f")),
                            tooltip=[alt.Tooltip("fecha_snapshot:O", title="Fecha"),
                                     alt.Tooltip("valuacion:Q", format=",.0f", title="Valuación")],
                        )
                        .properties(height=420)
                    )
                    st.altair_chart(chart, use_container_width=True)

            else:  # Comparativo base 100
                emisores_sel = st.multiselect(
                    "Soc. Gerente", emisores, default=[],
                    placeholder="Elegí una o más...", key="aum_soc_emisores_comp",
                )
                if not emisores_sel:
                    st.info("Seleccioná al menos una Soc. Gerente para comparar.")
                else:
                    df_r = df_fci_agg[
                        (df_fci_agg["fecha_snapshot"] >= fecha_desde) &
                        (df_fci_agg["fecha_snapshot"] <= fecha_hasta) &
                        (df_fci_agg["EMISOR"].isin(emisores_sel))
                    ]
                    df_plot = df_r.groupby(["fecha_snapshot", "EMISOR"], as_index=False)["valuacion"].sum()
                    # Normalizar a base 100 desde la primera fecha del rango para cada emisor
                    bases = (
                        df_plot.sort_values("fecha_snapshot")
                        .groupby("EMISOR")["valuacion"]
                        .first()
                        .rename("base")
                    )
                    df_plot = df_plot.join(bases, on="EMISOR")
                    df_plot["base100"] = df_plot["valuacion"] / df_plot["base"] * 100
                    f_rango = sorted(df_plot["fecha_snapshot"].unique())
                    chart = (
                        alt.Chart(df_plot)
                        .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=60))
                        .encode(
                            x=alt.X("fecha_snapshot:O", title="Fecha", sort=f_rango,
                                    axis=alt.Axis(labelAngle=-45)),
                            y=alt.Y("base100:Q", title="Índice (base 100)",
                                    scale=alt.Scale(zero=False),
                                    axis=alt.Axis(format=".1f")),
                            color=alt.Color("EMISOR:N", scale=alt.Scale(scheme="tableau20"),
                                            legend=alt.Legend(orient="top", labelFontSize=11)),
                            tooltip=[
                                alt.Tooltip("fecha_snapshot:O", title="Fecha"),
                                alt.Tooltip("EMISOR:N", title="Emisor"),
                                alt.Tooltip("base100:Q", format=".2f", title="Base 100"),
                                alt.Tooltip("valuacion:Q", format=",.0f", title="Valuación (ARS)"),
                            ],
                        )
                        .properties(height=420)
                    )
                    st.altair_chart(chart, use_container_width=True)

    # ── Tab 3: Tasa Fija ──────────────────────────────────────────────────────
    with tab_tasa_fija:
        curvas_map = _cargar_curvas_tasa_fija()
        if not curvas_map:
            st.info("Sin instrumentos de tasa_fija en Trading.Curvas.")
        else:
            tasa_fija_set = set(curvas_map.keys())
            unidades_tf = {u for u, a in assets.items() if a.get("TICKER") in tasa_fija_set}
            df_tf_all = df[df["unidad"].isin(unidades_tf)].copy()

            if df_tf_all.empty:
                st.info("Sin posiciones de Tasa Fija en AuM.")
            else:
                df_tf_all["ticker_corto"] = df_tf_all["unidad"].map(
                    lambda u: assets.get(u, {}).get("TICKER", "")
                )
                df_tf_all["fecha_venc"] = df_tf_all["ticker_corto"].map(
                    lambda t: (curvas_map.get(t, {}).get("fecha_vencimiento") or "")[:10]
                )
                df_tf_all["flujo_venc"] = df_tf_all["ticker_corto"].map(
                    lambda t: float(curvas_map.get(t, {}).get("flujo_vencimiento") or 0)
                )
                df_tf_all["pago_final"] = df_tf_all["cantidad"] * df_tf_all["flujo_venc"] / 100

                # siempre el snapshot más reciente
                fecha_sel_tf = df_tf_all["fecha_snapshot"].dropna().max()
                df_tf = df_tf_all[df_tf_all["fecha_snapshot"] == fecha_sel_tf].copy()

                # tabla consolidada por ticker
                tbl = (
                    df_tf.groupby(["ticker_corto", "fecha_venc"], as_index=False)
                    .agg(valuacion=("valuacion", "sum"),
                         cantidad=("cantidad", "sum"),
                         pago_final=("pago_final", "sum"))
                    .sort_values("fecha_venc")
                    .reset_index(drop=True)
                )

                # chart data — solo cobros al vencimiento
                chart_rows = []
                for _, row in tbl.iterrows():
                    if row["pago_final"] > 0 and row["fecha_venc"]:
                        chart_rows.append({"fecha": row["fecha_venc"], "monto": row["pago_final"],
                                           "ticker": row["ticker_corto"]})

                total_val_tf = tbl["valuacion"].sum()
                total_cobro  = tbl["pago_final"].sum()
                col_metrics_tf, col_toggle_tf = st.columns([5, 1])
                with col_metrics_tf:
                    st.markdown(
                        f"<div style='display:flex;gap:40px;margin-bottom:8px'>"
                        f"<div><span style='font-size:11px;color:#888'>Valuación actual</span><br>"
                        f"<span style='font-size:17px;font-weight:600'>${total_val_tf:,.0f}</span></div>"
                        f"<div><span style='font-size:11px;color:#888'>Cobro proyectado</span><br>"
                        f"<span style='font-size:17px;font-weight:600'>${total_cobro:,.0f}</span></div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with col_toggle_tf:
                    st.markdown(
                        "<div style='display:flex;justify-content:flex-end;padding-top:14px'>",
                        unsafe_allow_html=True,
                    )
                    ver_vn_tf = st.toggle("Valor Nominal", key="tf_toggle_vn")
                    st.markdown("</div>", unsafe_allow_html=True)

                col_src_tf = "cantidad" if ver_vn_tf else "valuacion"
                col_lbl_tf = "VN" if ver_vn_tf else "Valuación"
                fmt_tf = (lambda v: f"{v:,.2f}") if ver_vn_tf else (lambda v: f"${v:,.0f}")

                h_tbl = 38 + 35 * len(tbl)

                # ── fila 1: tabla tickers | tabla cuentas (mismo tamaño) ──
                col_tbl, col_det = st.columns([2, 3])

                with col_tbl:
                    tbl_display = tbl[["ticker_corto", "fecha_venc", col_src_tf]].copy()
                    tbl_display[col_src_tf] = tbl_display[col_src_tf].apply(fmt_tf)
                    tbl_display.rename(columns={
                        "ticker_corto": "Ticker",
                        "fecha_venc":   "Vencimiento",
                        col_src_tf:     col_lbl_tf,
                    }, inplace=True)
                    ev_tf = st.dataframe(
                        tbl_display, hide_index=True, use_container_width=True,
                        height=h_tbl, on_select="rerun", selection_mode="single-row",
                        key="tf_tabla",
                        column_config={
                            "Ticker":      st.column_config.TextColumn(width="small"),
                            "Vencimiento": st.column_config.TextColumn(width="small"),
                            col_lbl_tf:    st.column_config.TextColumn(width="small"),
                        },
                    )

                with col_det:
                    sel_tf = ev_tf.selection.rows if ev_tf.selection.rows else []
                    if sel_tf:
                        ticker_det = tbl.iloc[sel_tf[0]]["ticker_corto"]
                        df_det_src = df_tf[df_tf["ticker_corto"] == ticker_det]
                    else:
                        df_det_src = df_tf
                    df_det = (
                        df_det_src
                        .groupby("cuenta", as_index=False)
                        .agg(valuacion=("valuacion", "sum"), cantidad=("cantidad", "sum"))
                        .sort_values(col_src_tf, ascending=False)
                        .reset_index(drop=True)
                    )
                    df_det[col_lbl_tf] = df_det[col_src_tf].apply(fmt_tf)
                    st.dataframe(
                        df_det[["cuenta", col_lbl_tf]].rename(columns={"cuenta": "Cuenta"}),
                        hide_index=True, use_container_width=True,
                        height=h_tbl,
                    )

                # ── fila 2: gráfico a ancho completo ─────────────────────
                if chart_rows:
                    df_chart = pd.DataFrame(chart_rows)
                    fechas_ord = sorted(df_chart["fecha"].unique())
                    bars = (
                        alt.Chart(df_chart)
                        .mark_bar(color="#094293")
                        .encode(
                            x=alt.X("fecha:O", title=None, sort=fechas_ord,
                                    axis=alt.Axis(labelAngle=-45)),
                            y=alt.Y("monto:Q", title="ARS", stack=True,
                                    axis=alt.Axis(format=",.0f")),
                            tooltip=[
                                alt.Tooltip("fecha:O", title="Vencimiento"),
                                alt.Tooltip("ticker:N", title="Ticker"),
                                alt.Tooltip("monto:Q", format=",.0f", title="Cobro (ARS)"),
                            ],
                        )
                        .properties(
                            height=320,
                            title=alt.TitleParams("Amortizaciones — Tasa Fija",
                                                  anchor="start", fontSize=13, fontWeight=600),
                        )
                    )
                    st.altair_chart(bars, use_container_width=True)


    # ── Tab 4: CER ────────────────────────────────────────────────────────────
    with tab_cer:
        curvas_cer = _cargar_curvas_cer()
        if not curvas_cer:
            st.info("Sin instrumentos CER en Trading.Curvas.")
        else:
            cer_set = set(curvas_cer.keys())
            unidades_cer = {u for u, a in assets.items() if a.get("TICKER") in cer_set}
            df_cer_all = df[df["unidad"].isin(unidades_cer)].copy()

            if df_cer_all.empty:
                st.info("Sin posiciones CER en AuM.")
            else:
                df_cer_all["ticker_corto"] = df_cer_all["unidad"].map(
                    lambda u: assets.get(u, {}).get("TICKER", "")
                )
                df_cer_all["fecha_venc"] = df_cer_all["ticker_corto"].map(
                    lambda t: (curvas_cer.get(t, {}).get("fecha_vencimiento") or "")[:10]
                )

                fecha_sel_cer = df_cer_all["fecha_snapshot"].dropna().max()
                df_cer = df_cer_all[df_cer_all["fecha_snapshot"] == fecha_sel_cer].copy()

                tbl_cer = (
                    df_cer.groupby(["ticker_corto", "fecha_venc"], as_index=False)
                    .agg(valuacion=("valuacion", "sum"),
                         cantidad=("cantidad", "sum"))
                    .sort_values("fecha_venc")
                    .reset_index(drop=True)
                )

                total_val_cer = tbl_cer["valuacion"].sum()
                col_metrics_cer, col_toggle_cer = st.columns([5, 1])
                with col_metrics_cer:
                    st.markdown(
                        f"<div style='margin-bottom:8px'>"
                        f"<span style='font-size:11px;color:#888'>Valuación actual</span><br>"
                        f"<span style='font-size:17px;font-weight:600'>${total_val_cer:,.0f}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with col_toggle_cer:
                    st.markdown(
                        "<div style='display:flex;justify-content:flex-end;padding-top:14px'>",
                        unsafe_allow_html=True,
                    )
                    ver_vn_cer = st.toggle("Valor Nominal", key="cer_toggle_vn")
                    st.markdown("</div>", unsafe_allow_html=True)

                col_src_cer = "cantidad" if ver_vn_cer else "valuacion"
                col_lbl_cer = "VN" if ver_vn_cer else "Valuación"
                fmt_cer = (lambda v: f"{v:,.2f}") if ver_vn_cer else (lambda v: f"${v:,.0f}")

                h_cer = 38 + 35 * len(tbl_cer)
                col_tbl_c, col_det_c = st.columns([2, 3])

                with col_tbl_c:
                    tbl_cer_disp = tbl_cer[["ticker_corto", "fecha_venc", col_src_cer]].copy()
                    tbl_cer_disp[col_src_cer] = tbl_cer_disp[col_src_cer].apply(fmt_cer)
                    tbl_cer_disp.rename(columns={
                        "ticker_corto": "Ticker",
                        "fecha_venc":   "Vencimiento",
                        col_src_cer:    col_lbl_cer,
                    }, inplace=True)
                    ev_cer = st.dataframe(
                        tbl_cer_disp, hide_index=True, use_container_width=True,
                        height=h_cer, on_select="rerun", selection_mode="single-row",
                        key="cer_tabla",
                        column_config={
                            "Ticker":      st.column_config.TextColumn(width="small"),
                            "Vencimiento": st.column_config.TextColumn(width="small"),
                            col_lbl_cer:   st.column_config.TextColumn(width="small"),
                        },
                    )

                with col_det_c:
                    sel_cer = ev_cer.selection.rows if ev_cer.selection.rows else []
                    if sel_cer:
                        ticker_det_c = tbl_cer.iloc[sel_cer[0]]["ticker_corto"]
                        df_det_c_src = df_cer[df_cer["ticker_corto"] == ticker_det_c]
                    else:
                        df_det_c_src = df_cer
                    df_det_c = (
                        df_det_c_src
                        .groupby("cuenta", as_index=False)
                        .agg(valuacion=("valuacion", "sum"), cantidad=("cantidad", "sum"))
                        .sort_values(col_src_cer, ascending=False)
                        .reset_index(drop=True)
                    )
                    df_det_c[col_lbl_cer] = df_det_c[col_src_cer].apply(fmt_cer)
                    st.dataframe(
                        df_det_c[["cuenta", col_lbl_cer]].rename(columns={"cuenta": "Cuenta"}),
                        hide_index=True, use_container_width=True,
                        height=h_cer,
                    )


    # ── Tab 5: Renta Variable ─────────────────────────────────────────────────
    with tab_rv:
        unidades_rv = {u for u, a in assets.items() if a.get("CLASE_ACTIVO") == "RENTA VARIABLE"}
        df_rv_all = df[df["unidad"].isin(unidades_rv)].copy()

        if df_rv_all.empty:
            st.info("Sin posiciones de Renta Variable en AuM.")
        else:
            fecha_sel_rv = df_rv_all["fecha_snapshot"].dropna().max()
            df_rv = df_rv_all[df_rv_all["fecha_snapshot"] == fecha_sel_rv].copy()

            tbl_rv = (
                df_rv.groupby("unidad", as_index=False)["valuacion"]
                .sum()
                .sort_values("valuacion", ascending=False)
                .reset_index(drop=True)
            )

            total_rv = tbl_rv["valuacion"].sum()
            st.markdown(
                f"<div style='margin-bottom:8px'>"
                f"<span style='font-size:11px;color:#888'>Valuación actual</span><br>"
                f"<span style='font-size:17px;font-weight:600'>${total_rv:,.0f}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            h_rv = 38 + 35 * min(len(tbl_rv), 20)
            col_tbl_rv, col_det_rv = st.columns([2, 3])

            with col_tbl_rv:
                tbl_rv_disp = tbl_rv.copy()
                tbl_rv_disp["valuacion"] = tbl_rv_disp["valuacion"].apply(lambda v: f"${v:,.0f}")
                tbl_rv_disp.rename(columns={"unidad": "Unidad", "valuacion": "Valuación"}, inplace=True)
                ev_rv = st.dataframe(
                    tbl_rv_disp, hide_index=True, use_container_width=True,
                    height=h_rv, on_select="rerun", selection_mode="single-row",
                    key="rv_tabla",
                    column_config={
                        "Unidad":    st.column_config.TextColumn(width="medium"),
                        "Valuación": st.column_config.TextColumn(width="small"),
                    },
                )

            with col_det_rv:
                sel_rv = ev_rv.selection.rows if ev_rv.selection.rows else []
                if sel_rv:
                    unidad_det = tbl_rv.iloc[sel_rv[0]]["unidad"]
                    df_det_rv_src = df_rv[df_rv["unidad"] == unidad_det]
                else:
                    df_det_rv_src = df_rv
                df_det_rv = (
                    df_det_rv_src
                    .groupby("cuenta", as_index=False)["valuacion"]
                    .sum()
                    .sort_values("valuacion", ascending=False)
                    .reset_index(drop=True)
                )
                df_det_rv["Valuación"] = df_det_rv["valuacion"].apply(lambda v: f"${v:,.0f}")
                st.dataframe(
                    df_det_rv[["cuenta", "Valuación"]].rename(columns={"cuenta": "Cuenta"}),
                    hide_index=True, use_container_width=True,
                    height=h_rv,
                )
