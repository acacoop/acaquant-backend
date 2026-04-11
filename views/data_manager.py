"""
views/data_manager.py — Vista Data Manager para el dashboard Streamlit.

Consolida scripts de diagnóstico, backfills, validaciones y setup en una UI web.
Tabs: Diagnóstico | Backfills | Validaciones | Setup
"""

import io
import os
import sys
import threading
import subprocess
import contextlib
from collections import Counter
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
from pymongo import UpdateOne

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Aseguramos que el root esté en sys.path para imports internos
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from mongo_manager import get_mongo_client


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _db():   return get_mongo_client()["Trading"]
def _dbv():  return get_mongo_client()["Valuaciones"]
def _dbcf(): return get_mongo_client()["CashFlow"]


def _run_bg(script_relpath: str, args: list, key: str):
    """Lanza script como subprocess en background; acumula output en session_state."""
    sk = f"dm_{key}"
    if st.session_state.get(f"{sk}_status") == "running":
        st.warning("Ya hay un proceso corriendo para este script.")
        return
    st.session_state[f"{sk}_log"]        = []
    st.session_state[f"{sk}_status"]     = "running"
    st.session_state[f"{sk}_returncode"] = None

    script_path = os.path.join(PROJECT_ROOT, script_relpath)

    def _worker():
        env = os.environ.copy()
        env["PYTHONPATH"] = PROJECT_ROOT
        cmd = [sys.executable, script_path] + [str(a) for a in args]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env, cwd=PROJECT_ROOT,
        )
        for line in proc.stdout:
            st.session_state[f"{sk}_log"].append(line.rstrip())
        proc.wait()
        st.session_state[f"{sk}_returncode"] = proc.returncode
        st.session_state[f"{sk}_status"] = "done" if proc.returncode == 0 else "error"

    threading.Thread(target=_worker, daemon=True).start()


def _log_panel(key: str):
    """Render log panel: badge de estado + bloque de código scrolleable."""
    sk     = f"dm_{key}"
    status = st.session_state.get(f"{sk}_status", "idle")
    lines  = st.session_state.get(f"{sk}_log", [])

    if status == "idle" and not lines:
        return

    c1, c2 = st.columns([5, 1])
    with c1:
        if status == "running":
            st.info("Ejecutando...")
        elif status == "done":
            st.success("Completado")
        elif status == "error":
            rc = st.session_state.get(f"{sk}_returncode")
            st.error(f"Error (código {rc})")
    with c2:
        if st.button("Limpiar", key=f"dm_clear_{key}"):
            st.session_state[f"{sk}_log"]    = []
            st.session_state[f"{sk}_status"] = "idle"
            st.rerun()

    if lines:
        st.code("\n".join(lines[-300:]), language=None)


# ─── FRAGMENTS (auto-refresh mientras corre el subprocess) ───────────────────

@st.fragment(run_every=2)
def _frag_aum():   _log_panel("aum_bf")

@st.fragment(run_every=2)
def _frag_flujo(): _log_panel("flujo_bf")


# ─── TAB DIAGNÓSTICO ─────────────────────────────────────────────────────────

def _tab_diagnostico():
    st.caption("Inspección rápida del estado de los datos en MongoDB. Sin efectos secundarios.")

    # ── Curvas pendientes ─────────────────────────────────────────────────────
    with st.expander("Curvas Pendientes — docs sin `duration` en TimeSales"):
        if st.button("Ejecutar", key="d_cp"):
            with st.spinner("Consultando TimeSales..."):
                docs = list(_db()["TimeSales"].find(
                    {"duration": {"$exists": False}}, {"ticker": 1}
                ))
            if not docs:
                st.success("No hay docs pendientes de enriquecer.")
            else:
                conteo = Counter(d["ticker"] for d in docs)
                total  = sum(conteo.values())
                st.warning(f"Total pendientes: {total:,}")
                df = pd.DataFrame([
                    {"Ticker": t, "Pendientes": n}
                    for t, n in sorted(conteo.items(), key=lambda x: -x[1])
                ])
                st.dataframe(df, hide_index=True, use_container_width=True)

    # ── Check Forwards ────────────────────────────────────────────────────────
    with st.expander("Check Forwards — TEA disponible por instrumento en Curvas"):
        if st.button("Ejecutar", key="d_fwd"):
            with st.spinner("Consultando..."):
                db    = _db()
                grupos: dict = {}
                for d in db["Curvas"].find({}):
                    grupos.setdefault(d.get("curva", "?"), []).append(d)

            for curva, instrumentos in sorted(grupos.items()):
                tickers = [i["ticker"] for i in instrumentos if i.get("ticker")]
                pipeline = [
                    {"$match": {"ticker": {"$in": tickers},
                                "TEA":      {"$exists": True},
                                "duration": {"$exists": True}}},
                    {"$sort": {"timestamp": -1}},
                    {"$group": {"_id":      "$ticker",
                                "TEA":      {"$first": "$TEA"},
                                "duration": {"$first": "$duration"},
                                "ts":       {"$first": "$timestamp"}}},
                ]
                teas  = {r["_id"]: r for r in db["TimeSales"].aggregate(pipeline)}
                insts = sorted(instrumentos, key=lambda x: x.get("fecha_vencimiento", "9999"))

                st.markdown(f"**Curva: {curva}** ({len(instrumentos)} instrumentos)")
                rows = []
                for inst in insts:
                    tk    = inst.get("ticker", "?")
                    tc    = inst.get("ticker_corto", "?")
                    datos = teas.get(tk)
                    rows.append({
                        "Ticker":       tc,
                        "Vencimiento":  str(inst.get("fecha_vencimiento", "?"))[:10],
                        "TEA":          f"{datos['TEA']:.2%}" if datos else "—",
                        "Duration":     f"{datos['duration']:.3f}" if datos else "—",
                        "Último trade": datos["ts"].strftime("%d/%m %H:%M")
                                        if datos and datos.get("ts") else "—",
                        "OK":           "✅" if datos else "❌",
                    })
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

                live = db["ForwardsLive"].find_one({"curva": curva})
                if live:
                    ol = live.get("tickers", [])
                    ov = [i.get("ticker_corto") for i in insts if teas.get(i.get("ticker"))]
                    if ol == ov:
                        st.caption("ForwardsLive: orden correcto ✅")
                    else:
                        st.warning("ForwardsLive: orden difiere del esperado")
                        st.caption(f"Live:     {ol}")
                        st.caption(f"Esperado: {ov}")
                else:
                    st.warning(f"Sin doc en ForwardsLive para curva={curva}")
                st.divider()

    # ── Check CER Valuación ───────────────────────────────────────────────────
    with st.expander("Check CER Valuación — CER usado por cada bono CER"):
        if st.button("Ejecutar", key="d_cer"):
            with st.spinner("Consultando..."):
                db = _db()
                curvas_cer = list(db["Curvas"].find({"curva": "cer"}, {
                    "ticker": 1, "ticker_corto": 1, "cer_emision": 1
                }))
                if not curvas_cer:
                    st.warning("No hay instrumentos CER en Trading.Curvas.")
                else:
                    cer_dict = {d["fecha"]: float(d["valor"])
                                for d in db["CER"].find({}, {"fecha": 1, "valor": 1})}
                    dias_hab = sorted(
                        d["fecha"] for d in db["DiasHabiles"].find({}, {"fecha": 1, "_id": 0})
                    )

                    def _cer_en_fecha(fd: date):
                        for i in range(7):
                            k = (fd - timedelta(days=i)).isoformat()
                            if k in cer_dict:
                                return k, cer_dict[k]
                        return None, None

                    def _cer_liquidacion(settle_str, n=10):
                        idx = next((i for i, f in enumerate(dias_hab) if f <= settle_str), None)
                        if idx is None or idx < n:
                            return None, None
                        return _cer_en_fecha(date.fromisoformat(dias_hab[idx - n]))

                    def _next_habil(fd: date):
                        s = fd.isoformat()
                        return next((f for f in dias_hab if f > s), None)

                    col_ts = db["TimeSales"]
                    rows = []
                    for inst in curvas_cer:
                        doc = col_ts.find_one(
                            {"ticker": inst["ticker"], "duration": {"$exists": True}},
                            sort=[("timestamp", -1)]
                        )
                        if not doc:
                            rows.append({"Ticker": inst.get("ticker_corto", "?"),
                                         "Último trade": "—", "Settlement": "—",
                                         "CER fecha": "—", "CER valor": "—",
                                         "CER emisión": "—", "Ratio": "—", "Paridad": "—"})
                            continue
                        ts = doc["timestamp"]
                        fd = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
                        settle  = _next_habil(fd)
                        cf, cv  = _cer_liquidacion(settle) if settle else (None, None)
                        ce      = inst.get("cer_emision")
                        ratio   = (cv / ce) if (cv and ce) else None
                        rows.append({
                            "Ticker":       inst.get("ticker_corto", "?"),
                            "Último trade": ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)[:16],
                            "Settlement":   settle or "—",
                            "CER fecha":    cf or "—",
                            "CER valor":    f"{cv:.6f}" if cv else "—",
                            "CER emisión":  f"{ce:.6f}" if ce else "—",
                            "Ratio":        f"{ratio:.6f}" if ratio else "—",
                            "Paridad":      f"{doc.get('paridad', 0):.2f}" if doc.get("paridad") else "—",
                        })

                    if cer_dict:
                        ult = max(cer_dict.keys())
                        st.caption(f"CER más reciente: {ult} = {cer_dict[ult]:.6f} | "
                                   f"Días hábiles cargados: {len(dias_hab)}")
                    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # ── Check Tasa Fija ───────────────────────────────────────────────────────
    with st.expander("Check Tasa Fija — estado de instrumentos en AuM"):
        if st.button("Ejecutar", key="d_tf"):
            with st.spinner("Consultando..."):
                db  = _db()
                dbv = _dbv()
                curvas_tf = list(db["Curvas"].find({"curva": "tasa_fija"},
                                                   {"_id": 0, "ticker_corto": 1}))
                if not curvas_tf:
                    st.warning("No hay instrumentos con curva=tasa_fija en Trading.Curvas.")
                else:
                    t2u: dict = {}
                    for a in dbv["Assets"].find({}, {"_id": 0, "TICKER": 1, "unidad": 1}):
                        t2u.setdefault(a.get("TICKER", ""), []).append(a["unidad"])

                    uf  = dbv["AuM"].find_one(sort=[("fecha_snapshot", -1)],
                                              projection={"fecha_snapshot": 1})
                    fm  = uf["fecha_snapshot"] if uf else None
                    aum = list(dbv["AuM"].find({"fecha_snapshot": fm},
                                               {"_id": 0, "unidad": 1, "valuacion": 1})) if fm else []
                    con_pos = {d["unidad"] for d in aum if (d.get("valuacion") or 0) != 0}

                    rows = []
                    for c in sorted(curvas_tf, key=lambda x: x.get("ticker_corto", "")):
                        tc  = c["ticker_corto"]
                        uns = t2u.get(tc, [])
                        if not uns:
                            estado = "❌ Sin Assets"
                        elif any(u in con_pos for u in uns):
                            estado = "✅ En vista"
                        else:
                            estado = "⚠️ Sin posición en AuM"
                        rows.append({"Ticker": tc, "Estado": estado})

                    st.caption(f"Snapshot: {fm}")
                    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                    ok = sum(1 for r in rows if "✅" in r["Estado"])
                    sp = sum(1 for r in rows if "⚠" in r["Estado"])
                    sa = sum(1 for r in rows if "❌" in r["Estado"])
                    st.caption(f"✅ En vista: {ok}   ⚠️ Sin posición: {sp}   ❌ Sin Assets: {sa}")

    # ── Debug Forward ─────────────────────────────────────────────────────────
    with st.expander("Debug Forward — cálculo paso a paso entre dos instrumentos"):
        db  = _db()
        tcs = sorted({d["ticker_corto"] for d in db["Curvas"].find({}, {"ticker_corto": 1})
                      if d.get("ticker_corto")})
        c1, c2 = st.columns(2)
        with c1: tc_a = st.selectbox("Ticker A", tcs, key="dbf_a")
        with c2: tc_b = st.selectbox("Ticker B", tcs, key="dbf_b",
                                      index=min(1, len(tcs) - 1))

        if st.button("Calcular forward", key="dbf_btn"):
            if tc_a == tc_b:
                st.warning("Seleccioná dos tickers distintos.")
            else:
                def _ultima_tea(tc):
                    full = db["Curvas"].find_one({"ticker_corto": tc}, {"ticker": 1})
                    if not full:
                        return None, None, None
                    doc = db["TimeSales"].find_one(
                        {"ticker": full["ticker"],
                         "TEA": {"$exists": True}, "duration": {"$exists": True}},
                        sort=[("timestamp", -1)]
                    )
                    if not doc:
                        return None, None, None
                    return doc.get("TEA"), doc.get("duration"), doc.get("timestamp")

                with st.spinner("Buscando TEA en TimeSales..."):
                    tea_a, dur_a, ts_a = _ultima_tea(tc_a)
                    tea_b, dur_b, ts_b = _ultima_tea(tc_b)

                c1, c2 = st.columns(2)
                with c1:
                    st.metric(f"{tc_a} TEA", f"{tea_a:.2%}" if tea_a else "N/A")
                    if dur_a:
                        st.caption(f"Duration: {dur_a:.4f} | "
                                   f"{ts_a.strftime('%d/%m %H:%M') if ts_a else '—'}")
                with c2:
                    st.metric(f"{tc_b} TEA", f"{tea_b:.2%}" if tea_b else "N/A")
                    if dur_b:
                        st.caption(f"Duration: {dur_b:.4f} | "
                                   f"{ts_b.strftime('%d/%m %H:%M') if ts_b else '—'}")

                if all(x is not None for x in [tea_a, tea_b, dur_a, dur_b]):
                    if dur_a <= dur_b:
                        ta, ra, na = dur_a, tea_a, tc_a
                        tb, rb, nb = dur_b, tea_b, tc_b
                    else:
                        ta, ra, na = dur_b, tea_b, tc_b
                        tb, rb, nb = dur_a, tea_a, tc_a

                    dt = tb - ta
                    if dt <= 0:
                        st.error("Δt ≤ 0, no se puede calcular la forward.")
                    else:
                        num = (1 + rb) ** tb
                        den = (1 + ra) ** ta
                        fwd = (num / den) ** (1 / dt) - 1

                        st.divider()
                        st.markdown(f"**Forward {na} → {nb}**")
                        pasos = [
                            ("t corto (duration)",    f"{ta:.6f}"),
                            ("t largo (duration)",    f"{tb:.6f}"),
                            ("Δt",                    f"{dt:.6f}"),
                            (f"(1+TEA_{nb})^t_largo", f"{num:.8f}"),
                            (f"(1+TEA_{na})^t_corto", f"{den:.8f}"),
                            ("Cociente num/den",       f"{num/den:.8f}"),
                            ("Forward resultante",     f"{fwd*100:.4f}%"),
                        ]
                        st.dataframe(pd.DataFrame(pasos, columns=["Paso", "Valor"]),
                                     hide_index=True, use_container_width=True)
                        st.metric("Forward TEA", f"{fwd:.4%}")
                else:
                    st.warning("Faltan datos de TEA o Duration para uno o ambos tickers.")


# ─── TAB BACKFILLS ────────────────────────────────────────────────────────────

def _tab_backfills():
    st.caption("Carga de datos históricos. Los scripts de subprocess muestran log en tiempo real.")

    # ── AuM Snapshot ──────────────────────────────────────────────────────────
    with st.expander("AuM Snapshot — backfill para una fecha específica"):
        st.info("Llama a Aunesa y guarda posiciones en Valuaciones.AuM para la fecha indicada. "
                "Puede tardar 2-5 minutos.")
        fecha_aum = st.date_input("Fecha snapshot", value=date.today() - timedelta(days=1),
                                   key="bf_aum_fecha")
        c1, c2 = st.columns([2, 5])
        with c1:
            if st.button("▶ Ejecutar", key="bf_aum_btn"):
                _run_bg("Excel/backfill_aum.py", [fecha_aum.isoformat()], "aum_bf")
                st.rerun()
        with c2:
            status = st.session_state.get("dm_aum_bf_status", "idle")
            badges = {"idle": "⬜ Idle", "running": "🔄 Corriendo", "done": "✅ Listo", "error": "❌ Error"}
            st.caption(f"Estado: {badges.get(status, status)}")
        _frag_aum()

    # ── BCRA Data ─────────────────────────────────────────────────────────────
    with st.expander("BCRA Data — CER / TAMAR / DOLAR / BADLAR por rango de fechas"):
        c1, c2 = st.columns(2)
        with c1: desde_bcra = st.date_input("Desde", value=date.today() - timedelta(days=7),
                                             key="bf_bcra_desde")
        with c2: hasta_bcra = st.date_input("Hasta", value=date.today(), key="bf_bcra_hasta")

        if st.button("▶ Ejecutar", key="bf_bcra_btn"):
            with st.spinner("Descargando de API BCRA..."):
                buf = io.StringIO()
                try:
                    from data_bcra import fetch_y_guardar, VARIABLES_BCRA
                    with contextlib.redirect_stdout(buf):
                        for nombre, id_var in VARIABLES_BCRA.items():
                            fetch_y_guardar(nombre, id_var,
                                            desde_bcra.strftime("%Y-%m-%d"),
                                            hasta_bcra.strftime("%Y-%m-%d"))
                    st.success("BCRA actualizado correctamente")
                except Exception as e:
                    st.error(f"Error: {e}")
                output = buf.getvalue()
                if output:
                    st.code(output, language=None)

    # ── Flujo Contrapartes ────────────────────────────────────────────────────
    with st.expander("Flujo Contrapartes — re-cargar operaciones del día"):
        st.warning("Ejecuta el mismo script del cron: borra lo de HOY y recarga desde Aunesa.")
        c1, c2 = st.columns([2, 5])
        with c1:
            if st.button("▶ Ejecutar", key="bf_flujo_btn"):
                _run_bg("Excel/main_flujo_contrapartes.py", [], "flujo_bf")
                st.rerun()
        with c2:
            status = st.session_state.get("dm_flujo_bf_status", "idle")
            badges = {"idle": "⬜ Idle", "running": "🔄 Corriendo", "done": "✅ Listo", "error": "❌ Error"}
            st.caption(f"Estado: {badges.get(status, status)}")
        _frag_flujo()

    # ── Días Hábiles ──────────────────────────────────────────────────────────
    with st.expander("Días Hábiles — generar calendario argentino para un año"):
        year_dh = st.number_input("Año", min_value=2024, max_value=2035,
                                   value=date.today().year + 1, step=1, key="bf_dh_year")

        if st.button("▶ Generar", key="bf_dh_btn"):
            with st.spinner(f"Generando días hábiles {year_dh}..."):
                try:
                    import holidays
                    arg_holidays = holidays.Argentina(years=int(year_dh))
                    dias = []
                    d = date(int(year_dh), 1, 1)
                    while d <= date(int(year_dh), 12, 31):
                        if d.weekday() < 5 and d not in arg_holidays:
                            dias.append(d.isoformat())
                        d += timedelta(days=1)

                    col = get_mongo_client()["Trading"]["DiasHabiles"]
                    ops = [UpdateOne({"fecha": f}, {"$set": {"fecha": f}}, upsert=True)
                           for f in dias]
                    col.bulk_write(ops, ordered=False)
                    st.success(f"✅ {len(dias)} días hábiles cargados para {int(year_dh)}")
                    st.code("\n".join(f"  {d}" for d in dias[:5]) + f"\n  ... ({len(dias)} total)",
                            language=None)
                except Exception as e:
                    st.error(f"Error: {e}")


# ─── TAB VALIDACIONES ────────────────────────────────────────────────────────

def _tab_validaciones():
    st.caption("Validación de datos e integración con Aunesa. Algunos actualizan MongoDB.")

    # ── Match Contrapartes ────────────────────────────────────────────────────
    with st.expander("Match Contrapartes — vincular cuentas Aunesa ↔ MongoDB"):
        st.info("Lee CashFlow.Contrapartes, busca el ID de cuenta correspondiente en Aunesa "
                "y actualiza el campo `cuenta`. Coincidencia exacta y parcial.")
        if st.button("▶ Ejecutar (modifica Mongo)", key="v_mc_btn"):
            with st.spinner("Autenticando y buscando matches..."):
                buf = io.StringIO()
                try:
                    from Excel.test_match_contrapartes import main as mc_main
                    with contextlib.redirect_stdout(buf):
                        mc_main()
                    st.success("Match completado. Campo `cuenta` actualizado.")
                except Exception as e:
                    st.error(f"Error: {e}")
                output = buf.getvalue()
                if output:
                    st.code(output, language=None)

    # ── AuM Raw Search ────────────────────────────────────────────────────────
    with st.expander("AuM Raw — buscar posición en datos crudos de Aunesa"):
        st.info("Consulta directamente la API de Aunesa y muestra los datos sin procesar. "
                "Puede tardar si no filtrás por cuenta.")
        c1, c2 = st.columns([3, 2])
        with c1: keyword  = st.text_input("Keyword (busca en todos los campos)",
                                           placeholder="ej: MAX, FCI PAMPA", key="v_aum_kw")
        with c2: cuenta_f = st.text_input("Filtrar por cuenta (opcional)",
                                           placeholder="ej: 004", key="v_aum_cuenta")

        if st.button("▶ Buscar", key="v_aum_btn"):
            if not keyword.strip():
                st.warning("Ingresá una keyword.")
            else:
                with st.spinner(f"Buscando '{keyword}' en Aunesa..."):
                    try:
                        from Excel.main_aum import (autenticar, obtener_cuentas,
                                                     consultar_posicion, fecha_t2)
                        kw_upper = keyword.strip().upper()
                        headers  = autenticar()
                        cuentas  = obtener_cuentas(headers)
                        desde    = fecha_t2()

                        if cuenta_f.strip():
                            cuentas = cuentas[cuentas["id"].astype(str) == str(cuenta_f.strip())]
                            if cuentas.empty:
                                st.warning(f"No se encontró la cuenta {cuenta_f}.")

                        resultados = []
                        progress = st.progress(0)
                        total = len(cuentas)
                        for i, (_, row) in enumerate(cuentas.iterrows()):
                            cid  = str(row["id"])
                            den  = row["denominacion"]
                            data, _ = consultar_posicion(cid, headers, desde)
                            if data:
                                df_raw = pd.DataFrame(data)
                                mask = pd.Series([False] * len(df_raw))
                                for col_name in df_raw.columns:
                                    try:
                                        mask |= df_raw[col_name].astype(str).str.contains(
                                            kw_upper, case=False, na=False)
                                    except Exception:
                                        pass
                                matches = df_raw[mask].copy()
                                if not matches.empty:
                                    matches["_cuenta"]       = cid
                                    matches["_denominacion"] = den
                                    resultados.append(matches)
                            progress.progress((i + 1) / total)

                        progress.empty()
                        if resultados:
                            df_final = pd.concat(resultados, ignore_index=True)
                            st.success(f"{len(df_final)} filas encontradas con '{keyword}'")
                            cols_prio = ["_cuenta", "_denominacion", "informacion", "unidad",
                                         "cantidad", "precio", "tipoTitulo"]
                            cols_show = [c for c in cols_prio if c in df_final.columns]
                            st.dataframe(df_final[cols_show],
                                         hide_index=True, use_container_width=True)
                        else:
                            st.info(f"Sin resultados para '{keyword}'.")
                    except Exception as e:
                        st.error(f"Error: {e}")


# ─── TAB SETUP ───────────────────────────────────────────────────────────────

def _tab_setup():
    st.caption("Configuración puntual. Idempotente y seguro de re-ejecutar.")

    # ── Crear Índices ─────────────────────────────────────────────────────────
    with st.expander("Crear Índices MongoDB — idempotente"):
        st.caption("Crea 9 índices en TimeSales, ForwardsHistorico, BreakevensHistorico, "
                   "MarketSnapshot y AuM. Si ya existen, los salta.")
        if st.button("▶ Crear índices", key="s_idx_btn"):
            with st.spinner("Creando índices..."):
                buf = io.StringIO()
                try:
                    from crear_indices import main as ci_main
                    with contextlib.redirect_stdout(buf):
                        ci_main()
                    st.success("Índices creados correctamente")
                except Exception as e:
                    st.error(f"Error: {e}")
                output = buf.getvalue()
                if output:
                    st.code(output, language=None)

    # ── Set Segmento Contrapartes ─────────────────────────────────────────────
    with st.expander("Set Segmento Contrapartes — asignar Fondos / ALYC / Bancos"):
        st.caption("Aplica reglas automáticas. Los sin match se asignan manualmente abajo.")

        if st.button("▶ Aplicar reglas automáticas", key="s_seg_auto"):
            with st.spinner("Aplicando reglas..."):
                try:
                    from Excel.set_segmento_contrapartes import inferir_segmento
                    col   = _dbcf()["Contrapartes"]
                    docs  = list(col.find({}, {"_id": 1, "denominacion": 1,
                                               "contraparte": 1, "segmento": 1}))
                    auto_ok   = 0
                    sin_match = []
                    for doc in docs:
                        seg = inferir_segmento(doc.get("denominacion"), doc.get("contraparte"))
                        if seg:
                            col.update_one({"_id": doc["_id"]}, {"$set": {"segmento": seg}})
                            auto_ok += 1
                        else:
                            sin_match.append(doc)
                    st.success(f"✅ {auto_ok} docs actualizados automáticamente")
                    if sin_match:
                        st.session_state["dm_seg_sin_match"] = sin_match
                        st.warning(f"⚠️ {len(sin_match)} sin match — asignación manual abajo")
                    else:
                        st.session_state["dm_seg_sin_match"] = []
                except Exception as e:
                    st.error(f"Error: {e}")

        sin_match = st.session_state.get("dm_seg_sin_match", [])
        if sin_match:
            st.markdown("**Asignación manual:**")
            opciones = ["(saltar)", "Fondos", "ALYC", "Bancos"]
            asignaciones = {}
            for doc in sin_match:
                cp  = doc.get("contraparte", "?")
                den = doc.get("denominacion", "")
                seg_actual = doc.get("segmento", "—")
                ca, cb = st.columns([4, 2])
                with ca:
                    st.text(f"{cp}  —  {den}")
                    st.caption(f"Segmento actual: {seg_actual}")
                with cb:
                    sel = st.selectbox("Asignar", opciones, key=f"seg_{doc['_id']}")
                    asignaciones[doc["_id"]] = sel
                st.divider()

            if st.button("💾 Guardar asignaciones manuales", key="s_seg_save"):
                col       = _dbcf()["Contrapartes"]
                guardados = 0
                for _id, seg in asignaciones.items():
                    if seg != "(saltar)":
                        col.update_one({"_id": _id}, {"$set": {"segmento": seg}})
                        guardados += 1
                st.success(f"✅ {guardados} docs actualizados")
                st.session_state["dm_seg_sin_match"] = []
                st.rerun()


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

def vista_data_manager():
    st.markdown("## Data Manager")
    st.caption("Herramientas de diagnóstico, carga histórica, validaciones y setup.")

    tab1, tab2, tab3, tab4 = st.tabs(["Diagnóstico", "Backfills", "Validaciones", "Setup"])
    with tab1: _tab_diagnostico()
    with tab2: _tab_backfills()
    with tab3: _tab_validaciones()
    with tab4: _tab_setup()
