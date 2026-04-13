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
import tempfile
import requests
from collections import Counter
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
from pymongo import UpdateOne, InsertOne

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from mongo_manager import get_mongo_client
import config


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _db():   return get_mongo_client()["Trading"]
def _dbv():  return get_mongo_client()["Valuaciones"]
def _dbcf(): return get_mongo_client()["CashFlow"]


def _run_bg(script_relpath: str, args: list, key: str, extra_env: dict | None = None):
    """
    Lanza script como subprocess en background.
    Escribe stdout+stderr a un archivo temporal que el fragment lee periódicamente.
    extra_env: variables de entorno adicionales (ej. credenciales Aunesa ingresadas en UI).
    """
    sk = f"dm_{key}"
    if st.session_state.get(f"{sk}_status") == "running":
        st.warning("Ya hay un proceso corriendo para este script.")
        return

    tmpfd, tmppath = tempfile.mkstemp(suffix=".log", prefix=f"dm_{key}_")
    os.close(tmpfd)

    st.session_state[f"{sk}_tmpfile"]    = tmppath
    st.session_state[f"{sk}_status"]     = "running"
    st.session_state[f"{sk}_returncode"] = None
    st.session_state[f"{sk}_started_at"] = datetime.now()

    script_path = os.path.join(PROJECT_ROOT, script_relpath)

    def _worker():
        env = os.environ.copy()
        env["PYTHONPATH"]       = PROJECT_ROOT
        env["PYTHONUNBUFFERED"] = "1"
        if extra_env:
            env.update(extra_env)
        cmd = [sys.executable, "-u", script_path] + [str(a) for a in args]

        with open(tmppath, "w", buffering=1) as out:
            ts = datetime.now().strftime("%H:%M:%S")
            out.write(f"[{ts}]  Iniciando: {' '.join(cmd[-3:])}\n")
            out.write(f"[{ts}]  Directorio: {PROJECT_ROOT}\n\n")
            out.flush()

            proc = subprocess.Popen(
                cmd, stdout=out, stderr=out,
                env=env, cwd=PROJECT_ROOT,
            )
            ts2 = datetime.now().strftime("%H:%M:%S")
            out.write(f"[{ts2}]  PID: {proc.pid}\n\n")
            out.flush()

            proc.wait()

            ts3 = datetime.now().strftime("%H:%M:%S")
            out.write(f"\n[{ts3}]  Proceso finalizado — returncode: {proc.returncode}\n")

        st.session_state[f"{sk}_returncode"] = proc.returncode
        st.session_state[f"{sk}_status"] = "done" if proc.returncode == 0 else "error"

    threading.Thread(target=_worker, daemon=True).start()


def _read_tmplog(key: str) -> list[str]:
    """Lee el archivo temporal del subprocess y devuelve las líneas."""
    sk      = f"dm_{key}"
    tmppath = st.session_state.get(f"{sk}_tmpfile")
    if not tmppath or not os.path.exists(tmppath):
        return []
    try:
        with open(tmppath, "r") as f:
            content = f.read()
        return [l for l in content.splitlines() if l.strip()]
    except Exception:
        return []


def _log_panel(key: str):
    """Render log panel: status badge + contador + bloque scrolleable."""
    sk      = f"dm_{key}"
    status  = st.session_state.get(f"{sk}_status", "idle")
    started = st.session_state.get(f"{sk}_started_at")
    lines   = _read_tmplog(key)

    if status == "idle" and not lines:
        return

    elapsed = int((datetime.now() - started).total_seconds()) if started else 0

    c1, c2 = st.columns([5, 1])
    with c1:
        if status == "running":
            st.info(f"Ejecutando... {elapsed}s transcurridos — {len(lines)} líneas de log")
        elif status == "done":
            st.success(f"Completado en {elapsed}s — {len(lines)} líneas de log")
        elif status == "error":
            rc = st.session_state.get(f"{sk}_returncode")
            st.error(f"Error (returncode={rc}) — {len(lines)} líneas de log")
    with c2:
        if st.button("Limpiar", key=f"dm_clear_{key}"):
            tmppath = st.session_state.get(f"{sk}_tmpfile")
            if tmppath and os.path.exists(tmppath):
                try: os.remove(tmppath)
                except Exception: pass
            st.session_state[f"{sk}_status"]  = "idle"
            st.session_state[f"{sk}_tmpfile"] = None
            st.rerun()

    if lines:
        st.code("\n".join(lines[-300:]), language=None)


# ─── FRAGMENTS (auto-refresh mientras corre el subprocess) ───────────────────

@st.fragment(run_every=2)
def _frag_aum():
    _log_panel("aum_bf")


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

# Tipos excluidos por defecto en main_flujo_contrapartes.py
_TIPOS_EXCLUIR_DEFAULT = {
    "Concurrencia - Caución colocadora (Apertura)",
    "Concurrencia - Caución colocadora (Cierre)",
    "Futuros Financieros - Compra",
    "Futuros Financieros - Venta",
}

_CAMPOS_FLUJO = {"boleto", "concertacion", "tipoOperacion", "cuenta", "denominacion",
                 "instrumento", "condiciones", "bruto", "segmento", "contraparte"}

AUTH_URL_FLUJO  = "https://aca.aunesa.com/Irmo/api/login"
INFOS_URL_FLUJO = "https://aca.aunesa.com/Irmo/api/operaciones/informes"


def _autenticar_flujo(client_id=None, username=None, password=None):
    """Autentica en Aunesa. Usa los parámetros si se pasan; si no, cae al config/env."""
    resp = requests.post(
        AUTH_URL_FLUJO,
        json={
            "clientId": client_id or config.AUNESA_CLIENT_ID,
            "username":  username  or config.AUNESA_USERNAME,
            "password":  password  or config.AUNESA_PASSWORD,
        },
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json().get("token")
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def _inferir_moneda(condiciones):
    if not condiciones:
        return ""
    c = condiciones.upper()
    if "USD" in c: return "USD"
    if "ARS" in c: return "ARS"
    return ""


def _tab_backfills():
    st.caption("Carga de datos históricos.")

    # ─── Credenciales Aunesa ─────────────────────────────────────────────────
    creds_completas = (
        st.session_state.get("aunesa_cred_user", "").strip() and
        st.session_state.get("aunesa_cred_pass", "").strip()
    )
    with st.expander("Credenciales Aunesa", expanded=not creds_completas):
        st.caption("Ingresalas una vez por sesión. No se guardan en disco ni en secrets.")
        c1, c2 = st.columns(2)
        with c1:
            st.text_input("Usuario", type="password", key="aunesa_cred_user")
        with c2:
            st.text_input("Password", type="password", key="aunesa_cred_pass")
        if creds_completas:
            st.success("Credenciales cargadas para esta sesión.")

    def _creds_ok():
        return all([
            st.session_state.get("aunesa_cred_user", "").strip(),
            st.session_state.get("aunesa_cred_pass", "").strip(),
        ])

    def _aunesa_env():
        return {
            "AUNESA_CLIENT_ID": "",
            "AUNESA_USERNAME":  st.session_state.get("aunesa_cred_user", "").strip(),
            "AUNESA_PASSWORD":  st.session_state.get("aunesa_cred_pass", "").strip(),
        }

    st.divider()

    # ─── Selector de sub-vista ────────────────────────────────────────────────
    subvista = st.radio(
        "Sección",
        ["Flujo vs Contrapartes", "AuM", "Assets"],
        horizontal=True,
        label_visibility="collapsed",
        key="bf_subvista",
    )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SUB-VISTA: Flujo vs Contrapartes
    # ══════════════════════════════════════════════════════════════════════════
    if subvista == "Flujo vs Contrapartes":

        col_flujo = _dbcf()["Flujo"]

        # ── Fila principal: tabla tipoOperacion (izq) | Cargar Operaciones (der) ──
        col_tabla, col_carga = st.columns([2, 3])

        with col_tabla:
            st.markdown("**Tipos de operación**")
            pipeline = [
                {"$group": {"_id": "$tipoOperacion", "cantidad": {"$sum": 1}}},
                {"$sort": {"cantidad": -1}},
            ]
            rows = list(col_flujo.aggregate(pipeline))
            if rows:
                df_tipos = pd.DataFrame([
                    {"Tipo": r["_id"] or "(vacío)", "Cantidad": r["cantidad"]}
                    for r in rows
                ])
                st.dataframe(df_tipos, use_container_width=True, hide_index=True,
                             height=min(38 + len(df_tipos) * 35, 420))
            else:
                st.caption("Sin datos en la colección.")

        with col_carga:
            st.markdown("**Cargar Operaciones**")
            st.caption("Consulta Aunesa por cada contraparte con cuenta asignada "
                       "y guarda las operaciones. Elegí rango y tipos a incluir.")

            cc1, cc2 = st.columns(2)
            with cc1:
                fecha_flujo_desde = st.date_input(
                    "Desde", value=date.today() - timedelta(days=1), key="bf_flujo_desde"
                )
            with cc2:
                fecha_flujo_hasta = st.date_input(
                    "Hasta", value=date.today() - timedelta(days=1), key="bf_flujo_hasta"
                )

            if fecha_flujo_hasta < fecha_flujo_desde:
                st.error("La fecha Hasta debe ser mayor o igual a Desde.")
            else:
                desde_str = fecha_flujo_desde.strftime("%d/%m/%Y")
                hasta_str = fecha_flujo_hasta.strftime("%d/%m/%Y")
                n_dias = (fecha_flujo_hasta - fecha_flujo_desde).days + 1
                if n_dias > 1:
                    st.caption(f"Rango: {desde_str} → {hasta_str} ({n_dias} días)")

                tipos_en_mongo    = sorted(col_flujo.distinct("tipoOperacion"))
                tipos_disponibles = [t for t in tipos_en_mongo
                                     if t and t not in _TIPOS_EXCLUIR_DEFAULT]

                st.caption(f"{len(tipos_disponibles)} tipos disponibles "
                           "(excluidos por defecto no se muestran)")

                tipos_seleccionados = []
                if not tipos_disponibles:
                    st.warning("Sin tipos en la colección. Usá 'Preview tipos' para descubrirlos.")
                else:
                    st.markdown("**Tipos a incluir** (destildá los que no querés):")
                    mitad = (len(tipos_disponibles) + 1) // 2
                    col_left, col_right = st.columns(2)
                    for i, tipo in enumerate(tipos_disponibles):
                        col = col_left if i < mitad else col_right
                        with col:
                            if st.checkbox(tipo, value=True, key=f"flujo_tipo_{i}"):
                                tipos_seleccionados.append(tipo)

                # Preview tipos desde Aunesa
                with st.expander("Preview tipos desde Aunesa (llama a la API)", expanded=False):
                    st.caption("Consulta una sola contraparte para ver qué tipos devuelve Aunesa.")
                    if st.button("Consultar preview", key="flujo_preview_btn"):
                        with st.spinner("Consultando Aunesa..."):
                            try:
                                col_cp  = _dbcf()["Contrapartes"]
                                primera = col_cp.find_one(
                                    {"cuenta": {"$exists": True, "$ne": ""}},
                                    {"contraparte": 1, "cuenta": 1},
                                )
                                if not primera:
                                    st.warning("No hay contrapartes con cuenta asignada.")
                                else:
                                    c_prev       = _aunesa_env() if _creds_ok() else {}
                                    headers_prev = _autenticar_flujo(
                                        c_prev.get("AUNESA_CLIENT_ID"),
                                        c_prev.get("AUNESA_USERNAME"),
                                        c_prev.get("AUNESA_PASSWORD"),
                                    )
                                    try:
                                        cid = int(str(primera["cuenta"]).strip())
                                    except (ValueError, TypeError):
                                        cid = str(primera["cuenta"]).strip()
                                    params = {"cuenta": cid,
                                              "fechaConcDesde": desde_str,
                                              "fechaConcHasta": hasta_str}
                                    resp = requests.get(INFOS_URL_FLUJO, params=params,
                                                        headers=headers_prev, timeout=60)
                                    if resp.status_code == 204:
                                        st.info(f"[{primera['contraparte']}] sin operaciones en el rango.")
                                    else:
                                        resp.raise_for_status()
                                        data_prev = resp.json() or []
                                        tipos_api = sorted({r.get("tipoOperacion", "")
                                                            for r in data_prev if r.get("tipoOperacion")})
                                        st.success(f"Tipos en [{primera['contraparte']}]:")
                                        for t in tipos_api:
                                            excluido = t in _TIPOS_EXCLUIR_DEFAULT
                                            st.write(f"  {'🚫' if excluido else '✅'} {t}" +
                                                     (" (excluido por defecto)" if excluido else ""))
                            except Exception as e:
                                st.error(f"Error: {e}")

                tipos_excluir_run  = {t for t in tipos_disponibles if t not in tipos_seleccionados}
                tipos_excluir_run |= _TIPOS_EXCLUIR_DEFAULT
                extra = sorted(tipos_excluir_run - _TIPOS_EXCLUIR_DEFAULT)
                if extra:
                    st.caption(f"También se excluirán: {extra}")

                if st.button("▶ Cargar Operaciones", key="bf_flujo_run"):
                    if not _creds_ok():
                        st.error("Completá las credenciales de Aunesa arriba antes de ejecutar.")
                    else:
                        _ejecutar_flujo(desde_str, hasta_str, tipos_excluir_run, _aunesa_env())

        st.divider()

        # ── Validar duplicados por boleto ─────────────────────────────────────
        with st.expander("Validar duplicados — campo boleto", expanded=False):
            st.caption("Detecta documentos con el mismo valor de `boleto` (clave de negocio única). "
                       "Si hay duplicados, los muestra para que puedas decidir si borrarlos.")
            if st.button("Buscar duplicados", key="flujo_dup_btn"):
                with st.spinner("Analizando..."):
                    pipeline_dup = [
                        {"$group": {"_id": "$boleto", "count": {"$sum": 1},
                                    "ids": {"$push": {"$toString": "$_id"}}}},
                        {"$match": {"count": {"$gt": 1}}},
                        {"$sort": {"count": -1}},
                    ]
                    dups = list(col_flujo.aggregate(pipeline_dup))
                if not dups:
                    st.success(f"Sin duplicados. La colección está limpia.")
                else:
                    total_extras = sum(d["count"] - 1 for d in dups)
                    st.warning(f"{len(dups)} boletos duplicados — {total_extras} docs extras.")
                    df_dup = pd.DataFrame([
                        {"Boleto": d["_id"], "Repeticiones": d["count"]}
                        for d in dups
                    ])
                    st.dataframe(df_dup, use_container_width=True, hide_index=True)

                    st.session_state["flujo_dups_encontrados"] = dups

            if st.session_state.get("flujo_dups_encontrados"):
                dups = st.session_state["flujo_dups_encontrados"]
                total_extras = sum(d["count"] - 1 for d in dups)
                st.error(f"Se eliminarán **{total_extras} documentos** extras (se conserva 1 por boleto).")
                if st.checkbox(f"Confirmo que quiero borrar {total_extras} docs duplicados",
                               key="flujo_dup_confirm"):
                    if st.button("🗑️ Eliminar duplicados", key="flujo_dup_del_btn"):
                        ids_borrar = []
                        for d in dups:
                            ids_borrar.extend(d["ids"][1:])  # conserva el primero
                        from bson import ObjectId
                        result = col_flujo.delete_many(
                            {"_id": {"$in": [ObjectId(i) for i in ids_borrar]}}
                        )
                        st.success(f"✅ {result.deleted_count} duplicados eliminados.")
                        st.session_state.pop("flujo_dups_encontrados", None)
                        st.rerun()

        # ── Borrar por tipo de operación ──────────────────────────────────────
        with st.expander("Borrar operaciones por tipo", expanded=False):
            st.warning("Operación destructiva. Borra permanentemente docs según tipo y rango de fechas.")

            todos_tipos = sorted(t for t in col_flujo.distinct("tipoOperacion") if t)

            if not todos_tipos:
                st.info("No hay documentos en la colección.")
            else:
                tipos_a_borrar = st.multiselect("Tipos a borrar", todos_tipos, key="del_tipos")

                c1, c2 = st.columns(2)
                with c1:
                    del_desde = st.date_input("Desde (opcional)", value=None, key="del_desde")
                with c2:
                    del_hasta = st.date_input("Hasta (opcional)", value=None, key="del_hasta")

                if tipos_a_borrar:
                    filtro: dict = {"tipoOperacion": {"$in": tipos_a_borrar}}
                    if del_desde or del_hasta:
                        d_ini = del_desde or date(2000, 1, 1)
                        d_fin = del_hasta or date(2099, 12, 31)
                        fechas_rango = []
                        d_cur = d_ini
                        while d_cur <= d_fin:
                            fechas_rango.append(d_cur.strftime("%d/%m/%Y"))
                            d_cur += timedelta(days=1)
                        filtro["concertacion"] = {"$in": fechas_rango}

                    count_prev = col_flujo.count_documents(filtro)
                    if count_prev == 0:
                        st.info("No hay documentos que coincidan con ese filtro.")
                    else:
                        st.error(f"Se borrarán **{count_prev:,} documentos**. Esta acción no se puede deshacer.")
                        confirmar = st.checkbox(
                            f"Confirmo que quiero borrar {count_prev:,} docs", key="del_confirm"
                        )
                        if confirmar:
                            if st.button("🗑️ Borrar ahora", key="del_exec_btn"):
                                result = col_flujo.delete_many(filtro)
                                st.success(f"✅ {result.deleted_count:,} documentos eliminados.")
                                st.session_state["del_confirm"] = False
                                st.rerun()
                else:
                    st.caption("Seleccioná al menos un tipo para ver el preview.")

    # ══════════════════════════════════════════════════════════════════════════
    # SUB-VISTA: AuM
    # ══════════════════════════════════════════════════════════════════════════
    elif subvista == "AuM":
        with st.expander("AuM Snapshot — backfill para una fecha específica", expanded=True):
            st.info("Llama a Aunesa y guarda posiciones en Valuaciones.AuM para la fecha indicada. "
                    "Puede tardar 2-5 minutos. El log se actualiza automáticamente cada 2 segundos.")

            fecha_aum = st.date_input("Fecha snapshot", value=date.today() - timedelta(days=1),
                                       key="bf_aum_fecha")

            status_aum = st.session_state.get("dm_aum_bf_status", "idle")
            badges = {"idle": "⬜ Idle", "running": "🔄 Corriendo",
                      "done": "✅ Listo", "error": "❌ Error"}

            c1, c2 = st.columns([2, 4])
            with c1:
                btn_disabled = status_aum == "running"
                if st.button("▶ Ejecutar", key="bf_aum_btn", disabled=btn_disabled):
                    if not _creds_ok():
                        st.error("Completá las credenciales de Aunesa arriba antes de ejecutar.")
                    else:
                        _run_bg("Excel/backfill_aum.py", [fecha_aum.isoformat()],
                                "aum_bf", extra_env=_aunesa_env())
                        st.rerun()
            with c2:
                st.caption(f"Estado: {badges.get(status_aum, status_aum)}")
                tmppath = st.session_state.get("dm_aum_bf_tmpfile")
                if tmppath:
                    st.caption(f"Log: `{tmppath}`")

            _frag_aum()

    # ══════════════════════════════════════════════════════════════════════════
    # SUB-VISTA: Assets (Valuaciones.Assets)
    # ══════════════════════════════════════════════════════════════════════════
    else:
        _subvista_assets()


# ─── SUB-VISTA ASSETS ────────────────────────────────────────────────────────

_ASSETS_CAMPOS = ["CALIFICACION", "CARTERA", "CLASE_ACTIVO", "EMISOR", "TICKER", "VENCIMIENTO"]


def _asset_esta_vacio(campo: str):
    """Filtro Mongo para considerar un campo como vacío: no existe, null o ''. """
    return {"$or": [
        {campo: {"$exists": False}},
        {campo: None},
        {campo: ""},
    ]}


def _subvista_assets():
    col = _dbv()["Assets"]
    total_docs = col.count_documents({})

    st.markdown(f"**Valuaciones.Assets** — {total_docs:,} documentos")

    # ── Sección 1: Diagnóstico de campos vacíos ───────────────────────────────
    st.markdown("### Diagnóstico de campos vacíos")
    st.caption("Cantidad de documentos con el campo ausente, `null` o `\"\"` por campo.")

    if total_docs == 0:
        st.info("Colección vacía.")
    else:
        rows = []
        for campo in _ASSETS_CAMPOS:
            vacios = col.count_documents(_asset_esta_vacio(campo))
            con_valor = total_docs - vacios
            rows.append({
                "Campo":       campo,
                "Vacíos":      vacios,
                "Con valor":   con_valor,
                "Cobertura":   f"{(con_valor / total_docs * 100):.1f}%" if total_docs else "—",
            })
        df_diag = pd.DataFrame(rows)
        st.dataframe(df_diag, hide_index=True, use_container_width=True,
                     height=38 + len(df_diag) * 35)

        with st.expander("Ver `unidad` sin valor por campo", expanded=False):
            campo_ver = st.selectbox("Campo", _ASSETS_CAMPOS, key="as_ver_campo")
            if st.button("Listar", key="as_ver_btn"):
                docs = list(col.find(_asset_esta_vacio(campo_ver),
                                     {"_id": 0, "unidad": 1}).limit(500))
                if not docs:
                    st.success(f"No hay docs con {campo_ver} vacío.")
                else:
                    st.caption(f"{len(docs)} unidades (máx 500 mostradas)")
                    st.dataframe(pd.DataFrame(docs), hide_index=True,
                                 use_container_width=True)

    st.divider()

    # ── Sección 2: Update masivo condicional ──────────────────────────────────
    st.markdown("### Update masivo condicional")
    st.caption("Setea un valor en un campo para los docs que cumplan los filtros.")

    c1, c2 = st.columns([1, 2])
    with c1:
        campo_set = st.selectbox("Campo a setear", _ASSETS_CAMPOS, key="as_set_campo")
    with c2:
        valor_set = st.text_input(
            "Nuevo valor",
            key="as_set_valor",
            placeholder='Ej: "NO APLICA" o "2027-05-07 00:00:00"',
        )

    st.markdown("**Filtros** (se aplican con AND)")

    # Filtros dinámicos
    n_filtros = st.session_state.get("as_n_filtros", 1)
    cf1, cf2 = st.columns([1, 1])
    with cf1:
        if st.button("➕ Agregar filtro", key="as_add_filtro"):
            st.session_state["as_n_filtros"] = n_filtros + 1
            st.rerun()
    with cf2:
        if n_filtros > 0 and st.button("➖ Quitar último filtro", key="as_del_filtro"):
            st.session_state["as_n_filtros"] = max(0, n_filtros - 1)
            st.rerun()

    filtros_mongo = []
    filtros_desc  = []
    for i in range(n_filtros):
        col_a, col_b, col_c = st.columns([2, 2, 3])
        with col_a:
            f_campo = st.selectbox(f"Campo #{i+1}", _ASSETS_CAMPOS, key=f"as_f_campo_{i}")
        with col_b:
            f_tipo = st.selectbox(
                f"Condición #{i+1}",
                ["es igual a", "está vacío"],
                key=f"as_f_tipo_{i}",
            )
        with col_c:
            if f_tipo == "es igual a":
                valores_disp = sorted(
                    str(v) for v in col.distinct(f_campo)
                    if v not in (None, "")
                )
                if valores_disp:
                    f_val = st.selectbox(
                        f"Valor #{i+1}", valores_disp, key=f"as_f_val_{i}",
                    )
                else:
                    f_val = st.text_input(
                        f"Valor #{i+1}", key=f"as_f_val_{i}",
                        help="Sin valores distintos en la colección.",
                    )
            else:
                f_val = None
                st.caption("—")

        if f_tipo == "es igual a" and f_val:
            filtros_mongo.append({f_campo: f_val})
            filtros_desc.append(f"`{f_campo}` == `{f_val}`")
        elif f_tipo == "está vacío":
            filtros_mongo.append(_asset_esta_vacio(f_campo))
            filtros_desc.append(f"`{f_campo}` vacío")

    filtro_final = {"$and": filtros_mongo} if filtros_mongo else {}

    if filtros_desc:
        st.caption("Filtro: " + "  ∧  ".join(filtros_desc))
    else:
        st.warning("⚠️ Sin filtros — el update afectaría TODA la colección.")

    # Preview
    match_count = col.count_documents(filtro_final)
    st.markdown(f"**Documentos que matchean: `{match_count:,}`**")

    if match_count > 0:
        with st.expander(f"Preview (máx 10 de {match_count:,})", expanded=False):
            preview = list(col.find(filtro_final, {"_id": 0, "unidad": 1,
                                                    campo_set: 1}).limit(10))
            if preview:
                st.dataframe(pd.DataFrame(preview), hide_index=True,
                             use_container_width=True)

    # Confirmación + ejecución
    puede_ejecutar = (
        match_count > 0
        and valor_set.strip() != ""
        and len(filtros_mongo) > 0
    )

    if not puede_ejecutar:
        faltantes = []
        if valor_set.strip() == "":     faltantes.append("nuevo valor")
        if len(filtros_mongo) == 0:     faltantes.append("al menos un filtro")
        if match_count == 0:            faltantes.append("docs que matcheen")
        st.caption("Falta: " + ", ".join(faltantes) if faltantes else "")
    else:
        confirmar = st.checkbox(
            f"Confirmo setear `{campo_set} = \"{valor_set}\"` en {match_count:,} docs",
            key="as_confirm",
        )
        if confirmar:
            if st.button("▶ Aplicar update masivo", key="as_apply_btn"):
                result = col.update_many(filtro_final, {"$set": {campo_set: valor_set}})
                st.success(
                    f"✅ Update aplicado: matched={result.matched_count:,}, "
                    f"modified={result.modified_count:,}"
                )
                st.session_state["as_confirm"] = False


def _ejecutar_flujo(desde_str: str, hasta_str: str,
                    tipos_excluir: set, creds: dict | None = None):
    """Ejecuta la carga de flujo inline con log visible en pantalla."""
    log_area = st.empty()
    lineas   = []

    def log(msg):
        ts = datetime.now().strftime("%H:%M:%S")
        lineas.append(f"[{ts}]  {msg}")
        log_area.code("\n".join(lineas[-200:]), language=None)

    try:
        client           = get_mongo_client()
        col_contrapartes = client["CashFlow"]["Contrapartes"]
        col_flujo        = client["CashFlow"]["Flujo"]

        c = creds or {}
        rango = desde_str if desde_str == hasta_str else f"{desde_str} → {hasta_str}"
        log(f"Rango: {rango}")
        log(f"Tipos excluidos: {sorted(tipos_excluir)}")
        log("")

        # 1. Borrar docs del rango — generar lista de fechas DD/MM/YYYY
        fmt = "%d/%m/%Y"
        d_ini = datetime.strptime(desde_str, fmt).date()
        d_fin = datetime.strptime(hasta_str, fmt).date()
        fechas_rango = []
        d_cur = d_ini
        while d_cur <= d_fin:
            fechas_rango.append(d_cur.strftime(fmt))
            d_cur += timedelta(days=1)
        del_result = col_flujo.delete_many({"concertacion": {"$in": fechas_rango}})
        log(f"🗑️  {del_result.deleted_count} docs eliminados para {rango}")

        # 2. Auth
        log("Autenticando en Aunesa...")
        headers = _autenticar_flujo(c.get("AUNESA_CLIENT_ID"),
                                    c.get("AUNESA_USERNAME"),
                                    c.get("AUNESA_PASSWORD"))
        log("✅ Auth OK")
        log("")

        # 3. Contrapartes con cuenta
        docs_cp = list(col_contrapartes.find(
            {"cuenta": {"$exists": True, "$ne": ""}},
            {"_id": 0, "contraparte": 1, "cuenta": 1}
        ))
        log(f"{len(docs_cp)} contrapartes con cuenta asignada")
        log("")

        # 4. Fetch por contraparte
        registros = {}
        total_cp  = len(docs_cp)

        for idx, doc in enumerate(docs_cp, 1):
            cp = doc["contraparte"]
            try:
                cuenta_id = int(str(doc["cuenta"]).strip())
            except (ValueError, TypeError):
                log(f"  [{idx}/{total_cp}] SKIP {cp} — cuenta inválida ({doc['cuenta']})")
                continue

            params = {"cuenta": cuenta_id,
                      "fechaConcDesde": desde_str,
                      "fechaConcHasta": hasta_str}
            try:
                resp = requests.get(INFOS_URL_FLUJO, params=params,
                                    headers=headers, timeout=60)

                if resp.status_code == 204:
                    log(f"  [{idx}/{total_cp}] [{cuenta_id}] {cp} → sin operaciones")
                    continue

                if resp.status_code == 401:
                    log(f"  [{idx}/{total_cp}] Re-autenticando...")
                    headers = _autenticar_flujo(c.get("AUNESA_CLIENT_ID"),
                                                c.get("AUNESA_USERNAME"),
                                                c.get("AUNESA_PASSWORD"))
                    resp    = requests.get(INFOS_URL_FLUJO, params=params,
                                           headers=headers, timeout=60)

                resp.raise_for_status()
                data = resp.json() or []

                count = 0
                tipos_vistos = set()
                for r in data:
                    tipo = r.get("tipoOperacion", "")
                    tipos_vistos.add(tipo)
                    if tipo in tipos_excluir:
                        continue
                    r["contraparte"] = cp
                    rec = {k: r.get(k) for k in _CAMPOS_FLUJO}
                    rec["moneda"] = _inferir_moneda(rec.get("condiciones", ""))
                    boleto = rec.get("boleto")
                    if boleto is not None:
                        if boleto not in registros:
                            registros[boleto] = rec
                    else:
                        registros[f"_no_boleto_{len(registros)}"] = rec
                    count += 1

                tipos_excluidos_aqui = tipos_vistos & tipos_excluir
                detalle = f" (excluidos: {sorted(tipos_excluidos_aqui)})" if tipos_excluidos_aqui else ""
                log(f"  [{idx}/{total_cp}] [{cuenta_id}] {cp} → {count} operaciones incluidas{detalle}")

            except Exception as e:
                log(f"  [{idx}/{total_cp}] [{cuenta_id}] {cp} → ERROR: {e}")

        log("")

        # 5. Insertar
        if registros:
            ops = [InsertOne(r) for r in registros.values()]
            col_flujo.bulk_write(ops, ordered=False)
            log(f"✅ {len(registros)} documentos insertados para {rango}")
        else:
            log(f"⚠️  Sin operaciones para insertar en {rango}")

        # 6. Dedup global por boleto
        log("")
        log("Verificando duplicados en toda la colección...")
        pipeline = [
            {"$match": {"boleto": {"$ne": None}}},
            {"$group": {"_id": "$boleto", "ids": {"$push": "$_id"}, "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}},
        ]
        duplicados = list(col_flujo.aggregate(pipeline))
        if not duplicados:
            log("✅ Sin duplicados encontrados")
        else:
            ids_a_borrar = []
            for d in duplicados:
                ids_a_borrar.extend(d["ids"][1:])
            result = col_flujo.delete_many({"_id": {"$in": ids_a_borrar}})
            log(f"🧹 {result.deleted_count} duplicados eliminados ({len(duplicados)} boletos afectados)")

        log("")
        log("═" * 50)
        log(f"Proceso finalizado. Total insertados: {len(registros)}")
        client.close()

    except Exception as e:
        log(f"❌ Error inesperado: {e}")


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
                        progress   = st.progress(0)
                        total      = len(cuentas)
                        for i, (_, row) in enumerate(cuentas.iterrows()):
                            cid  = str(row["id"])
                            den  = row["denominacion"]
                            data, _ = consultar_posicion(cid, headers, desde)
                            if data:
                                df_raw = pd.DataFrame(data)
                                mask   = pd.Series([False] * len(df_raw))
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
                cp         = doc.get("contraparte", "?")
                den        = doc.get("denominacion", "")
                seg_actual = doc.get("segmento", "—")
                ca, cb     = st.columns([4, 2])
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
    st.caption("Diagnóstico, backfills, validaciones y setup desde el dashboard.")

    tab1, tab2, tab3, tab4 = st.tabs(["Diagnóstico", "Backfills", "Validaciones", "Setup"])
    with tab1: _tab_diagnostico()
    with tab2: _tab_backfills()
    with tab3: _tab_validaciones()
    with tab4: _tab_setup()
