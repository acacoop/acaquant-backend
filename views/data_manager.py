"""
views/data_manager.py — Vista Data Manager para el dashboard Streamlit.

Consolida scripts de diagnóstico, backfills, validaciones y setup en una UI web.
Tabs: Diagnóstico | Backfills | Validaciones | Setup
"""

import io
import os
import re
import sys
import shutil
import threading
import subprocess
import contextlib
import tempfile
import requests
from pathlib import Path
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

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


# ─── AUDIT LOG ───────────────────────────────────────────────────────────────

def _audit_log(rows: list[dict]) -> None:
    """Inserta entradas en Manager.ChangeLog. Silencioso ante errores para no bloquear updates."""
    if not rows:
        return
    ahora = datetime.now(timezone.utc)
    for r in rows:
        r.setdefault("when", ahora)
    try:
        get_mongo_client()["Manager"]["ChangeLog"].insert_many(rows, ordered=False)
    except Exception as e:
        print(f"[audit] error insertando log: {e}")


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
    sk      = "dm_aum_bf"
    status  = st.session_state.get(f"{sk}_status", "idle")
    total   = st.session_state.get(f"{sk}_total", 0)
    done    = st.session_state.get(f"{sk}_done", 0)
    current = st.session_state.get(f"{sk}_current", "")

    if total > 0 and status in ("running", "done", "error"):
        pct = min(done / total, 1.0) if total else 0
        if status == "running":
            idx = min(done + 1, total)
            label = f"Procesando {idx}/{total} — {current} — {pct*100:.0f}%"
        elif status == "done":
            pct   = 1.0
            label = f"✅ Completado — {done}/{total} fechas"
        else:
            label = f"❌ Detenido en {done}/{total} fechas"
        st.progress(pct, text=label)

    _log_panel("aum_bf")


def _run_bg_aum_multi(fechas: list[str], key: str, extra_env: dict | None = None):
    """Ejecuta Excel/backfill_aum.py una vez por cada fecha, secuencialmente."""
    sk = f"dm_{key}"
    if st.session_state.get(f"{sk}_status") == "running":
        st.warning("Ya hay un proceso corriendo.")
        return

    tmpfd, tmppath = tempfile.mkstemp(suffix=".log", prefix=f"dm_{key}_")
    os.close(tmpfd)

    st.session_state[f"{sk}_tmpfile"]    = tmppath
    st.session_state[f"{sk}_status"]     = "running"
    st.session_state[f"{sk}_returncode"] = None
    st.session_state[f"{sk}_started_at"] = datetime.now()
    st.session_state[f"{sk}_total"]      = len(fechas)
    st.session_state[f"{sk}_done"]       = 0
    st.session_state[f"{sk}_current"]    = fechas[0] if fechas else ""

    script_path = os.path.join(PROJECT_ROOT, "Excel/backfill_aum.py")

    def _worker():
        env = os.environ.copy()
        env["PYTHONPATH"]       = PROJECT_ROOT
        env["PYTHONUNBUFFERED"] = "1"
        if extra_env:
            env.update(extra_env)

        all_ok    = True
        last_rc   = 0
        with open(tmppath, "w", buffering=1) as out:
            out.write(f"[{datetime.now().strftime('%H:%M:%S')}]  "
                      f"Procesando {len(fechas)} fecha(s): "
                      f"{fechas[0]} → {fechas[-1]}\n\n")
            out.flush()

            for i, fecha in enumerate(fechas, 1):
                st.session_state[f"{sk}_current"] = fecha
                ts = datetime.now().strftime("%H:%M:%S")
                out.write(f"\n[{ts}]  ══ Fecha {i}/{len(fechas)}: {fecha} ══\n")
                out.flush()

                cmd  = [sys.executable, "-u", script_path, fecha]
                proc = subprocess.Popen(cmd, stdout=out, stderr=out,
                                        env=env, cwd=PROJECT_ROOT)
                proc.wait()
                last_rc = proc.returncode

                st.session_state[f"{sk}_done"] = i
                ts2 = datetime.now().strftime("%H:%M:%S")
                if proc.returncode == 0:
                    out.write(f"[{ts2}]  ✅ {fecha} OK\n")
                else:
                    all_ok = False
                    out.write(f"[{ts2}]  ❌ {fecha} falló (rc={proc.returncode})\n")
                out.flush()

            out.write(f"\n[{datetime.now().strftime('%H:%M:%S')}]  "
                      f"Finalizado — {len(fechas)} fecha(s) procesada(s)\n")

        st.session_state[f"{sk}_returncode"] = 0 if all_ok else last_rc
        st.session_state[f"{sk}_status"]     = "done" if all_ok else "error"

    threading.Thread(target=_worker, daemon=True).start()


# ─── STATUS PANEL ────────────────────────────────────────────────────────────

_AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

# (db, coll, field_ts, nombre, umbral_seg, tz_si_naive)
# Los engines graban con datetime.now() o utcnow() sin tz; el TZ correcto
# depende de cada engine:
#   - TimeSales: main_valores.py:234 hace astimezone(ART).replace(tzinfo=None) → ART
#   - MarketSnapshot/ForwardsLive/BreakevensLive/OptionsSnapshot: datetime.now()
#     en el droplet (TZ=UTC) → UTC naive
_STATUS_LIVE = [
    ("Trading",  "TimeSales",       "timestamp",  "TimeSales",       300, _AR_TZ),
    ("Trading",  "MarketSnapshot",  "updated_at", "MarketSnapshot",  120, timezone.utc),
    ("Trading",  "ForwardsLive",    "updated_at", "ForwardsLive",     60, timezone.utc),
    ("Trading",  "BreakevensLive",  "updated_at", "BreakevensLive",   60, timezone.utc),
    ("Opciones", "OptionsSnapshot", "updated_at", "OptionsSnapshot", 180, timezone.utc),
]

# (db, coll, field, tipo, nombre, umbral_dias_habiles, descripcion, tz_si_naive)
_STATUS_PERIODICO = [
    ("Trading",     "CER",         "fecha",         "iso",     "CER (BCRA)",        2, "diario 20:00 UTC",      None),
    ("Trading",     "TAMAR",       "fecha",         "iso",     "TAMAR (BCRA)",      2, "diario 20:00 UTC",      None),
    ("Trading",     "DOLAR",       "fecha",         "iso",     "DOLAR (BCRA)",      2, "diario 20:00 UTC",      None),
    ("Trading",     "BADLAR",      "fecha",         "iso",     "BADLAR (BCRA)",     2, "diario 20:00 UTC",      None),
    ("Valuaciones", "AuM",         "fecha_snapshot","iso",     "AuM (cierre)",      2, "diario 23:00 UTC L-V",  None),
    ("Valuaciones", "Carteras",    "timestamp",     "datetime","Carteras",          1, "4x / día hábil",        timezone.utc),
    ("CashFlow",    "Movimientos", "fecha",         "ddmmyyyy","CashFlow Mov.",     2, "02:00 UTC mar-sáb",     None),
    ("CashFlow",    "Flujo",       "concertacion",  "iso",     "Flujo Contrapartes",2, "02:00 UTC mar-sáb",     None),
]


def _es_hora_rueda(now_ar: datetime | None = None) -> bool:
    """True si ahora es L-V AR y estamos dentro de 10:00-17:05 ARG."""
    n = now_ar or datetime.now(_AR_TZ)
    if n.weekday() >= 5:
        return False
    return time(10, 0) <= n.time() <= time(17, 5)


def _fmt_delta(segundos: float) -> str:
    s = int(segundos)
    if s < 0:               return "—"
    if s < 60:              return f"{s}s"
    if s < 3600:            return f"{s//60}m {s%60}s"
    if s < 86400:           return f"{s//3600}h {(s%3600)//60}m"
    return f"{s//86400}d {(s%86400)//3600}h"


def _parse_periodic_value(val, tipo: str, tz_naive) -> datetime | None:
    if val is None or val == "":
        return None
    try:
        if tipo == "datetime":
            v = val if isinstance(val, datetime) else datetime.fromisoformat(str(val))
            if v.tzinfo is None:
                v = v.replace(tzinfo=tz_naive or timezone.utc)
            return v
        if tipo == "iso":
            return datetime.combine(date.fromisoformat(str(val)[:10]),
                                    time(0, 0), tzinfo=_AR_TZ)
        if tipo == "ddmmyyyy":
            return datetime.combine(datetime.strptime(str(val), "%d/%m/%Y").date(),
                                    time(0, 0), tzinfo=_AR_TZ)
    except Exception:
        return None
    return None


def _status_live_rows(en_rueda: bool) -> list[dict]:
    ahora = datetime.now(_AR_TZ)
    rows = []
    for db_n, coll_n, field, nombre, umbral, tz_naive in _STATUS_LIVE:
        coll = get_mongo_client()[db_n][coll_n]
        doc  = coll.find_one({field: {"$exists": True}},
                             sort=[(field, -1)], projection={field: 1})
        if not doc or not doc.get(field):
            rows.append({"Colección": nombre, "Última": "—", "Hace": "—",
                         "Umbral": f"{umbral}s", "Estado": "⚪ Sin datos"})
            continue
        ts = doc[field]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=tz_naive)
        delta = (ahora - ts).total_seconds()

        if not en_rueda:
            estado = "⚪ Fuera de rueda"
        elif delta > umbral * 3:
            estado = "🔴 Crítico"
        elif delta > umbral:
            estado = "🟡 Lento"
        else:
            estado = "🟢 OK"

        rows.append({
            "Colección": nombre,
            "Última":    ts.astimezone(_AR_TZ).strftime("%H:%M:%S"),
            "Hace":      _fmt_delta(delta),
            "Umbral":    f"{umbral}s",
            "Estado":    estado,
        })
    return rows


def _status_periodico_rows() -> list[dict]:
    ahora = datetime.now(_AR_TZ)
    rows = []
    for db_n, coll_n, field, tipo, nombre, umbral_dias, desc, tz_naive in _STATUS_PERIODICO:
        coll = get_mongo_client()[db_n][coll_n]
        doc  = coll.find_one({field: {"$exists": True, "$nin": [None, ""]}},
                             sort=[(field, -1)], projection={field: 1})
        if not doc:
            rows.append({"Colección": nombre, "Último dato": "—",
                         "Hace": "—", "Frecuencia": desc, "Estado": "⚪ Sin datos"})
            continue

        ts = _parse_periodic_value(doc.get(field), tipo, tz_naive)
        if ts is None:
            rows.append({"Colección": nombre, "Último dato": str(doc.get(field))[:19],
                         "Hace": "—", "Frecuencia": desc, "Estado": "⚪ Error parse"})
            continue

        delta_dias = (ahora.date() - ts.date()).days
        if delta_dias <= 0:
            estado = "🟢 OK"
        elif delta_dias <= umbral_dias:
            estado = "🟡 Atrasado"
        else:
            estado = "🔴 Crítico"

        rows.append({
            "Colección":   nombre,
            "Último dato": ts.astimezone(_AR_TZ).strftime("%Y-%m-%d %H:%M"),
            "Hace":        _fmt_delta((ahora - ts).total_seconds()),
            "Frecuencia":  desc,
            "Estado":      estado,
        })
    return rows


@st.fragment(run_every=10)
def _frag_status():
    ahora_ar = datetime.now(_AR_TZ)
    en_rueda = _es_hora_rueda(ahora_ar)

    badge = "🟢 En rueda" if en_rueda else "⚪ Fuera de rueda"
    st.caption(f"{badge} · Chequeado: {ahora_ar.strftime('%Y-%m-%d %H:%M:%S')} ART · "
               f"Auto-refresh cada 10s")

    try:
        live_rows = _status_live_rows(en_rueda)
        st.markdown("**Live engines**")
        st.dataframe(pd.DataFrame(live_rows), hide_index=True,
                     use_container_width=True,
                     height=38 + len(live_rows) * 35)

        per_rows = _status_periodico_rows()
        st.markdown("**Periódicas (crons)**")
        st.dataframe(pd.DataFrame(per_rows), hide_index=True,
                     use_container_width=True,
                     height=38 + len(per_rows) * 35)
    except Exception as e:
        st.error(f"Error consultando estado: {e}")


# ─── TAB DIAGNÓSTICO ─────────────────────────────────────────────────────────

def _tab_diagnostico():
    st.caption("Inspección rápida del estado de los datos en MongoDB. Sin efectos secundarios.")

    # ── Status ────────────────────────────────────────────────────────────────
    with st.expander("Status — Salud de colecciones (live + crons)", expanded=True):
        _frag_status()

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


@st.dialog("Credenciales Aunesa")
def _dialog_aunesa_creds():
    st.caption("Ingresalas para esta sesión. No se guardan en disco ni en secrets.")
    user  = st.text_input("Usuario",  type="password", key="dlg_aunesa_user")
    passw = st.text_input("Password", type="password", key="dlg_aunesa_pass")
    if st.button("Guardar", key="dlg_aunesa_save"):
        if not (user.strip() and passw.strip()):
            st.error("Completá usuario y password.")
        else:
            st.session_state["aunesa_cred_user"] = user.strip()
            st.session_state["aunesa_cred_pass"] = passw.strip()
            st.rerun()


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


def _tab_backfills():
    st.caption("Carga de datos históricos.")

    # ─── Selector de sub-vista ────────────────────────────────────────────────
    subvista = st.radio(
        "Sección",
        ["Flujo vs Contrapartes", "AuM", "Assets", "Portfolio"],
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
                        if not _creds_ok():
                            _dialog_aunesa_creds()
                            st.stop()
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
                                    c_prev       = _aunesa_env()
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
                        _dialog_aunesa_creds()
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
        with st.expander("AuM Snapshot — backfill por fechas", expanded=True):
            st.info("Elegí una o más fechas y se procesan secuencialmente "
                    "(una corrida de backfill_aum por fecha). El progreso se "
                    "actualiza cada 2 segundos.")

            fechas_aum = st.session_state.setdefault("bf_aum_fechas", [])

            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                nueva_fecha = st.date_input("Agregar fecha",
                                            value=date.today() - timedelta(days=1),
                                            key="bf_aum_nueva")
            with c2:
                st.caption(" ")
                if st.button("➕ Agregar", key="bf_aum_add",
                             use_container_width=True):
                    iso = nueva_fecha.isoformat()
                    if iso not in fechas_aum:
                        fechas_aum.append(iso)
                        fechas_aum.sort()
                    st.rerun()
            with c3:
                st.caption(" ")
                if st.button("🗑️ Limpiar", key="bf_aum_clear",
                             use_container_width=True,
                             disabled=not fechas_aum):
                    st.session_state["bf_aum_fechas"] = []
                    st.rerun()

            if fechas_aum:
                st.markdown(f"**{len(fechas_aum)} fecha(s) seleccionada(s):**")
                # Mostrar como chips con botón X
                chips_per_row = 6
                for i in range(0, len(fechas_aum), chips_per_row):
                    cols = st.columns(chips_per_row)
                    for j, f in enumerate(fechas_aum[i:i+chips_per_row]):
                        with cols[j]:
                            if st.button(f"✕ {f}", key=f"bf_aum_rm_{f}",
                                         use_container_width=True):
                                fechas_aum.remove(f)
                                st.rerun()
            else:
                st.caption("Sin fechas seleccionadas.")

            status_aum   = st.session_state.get("dm_aum_bf_status", "idle")
            btn_disabled = status_aum == "running" or not fechas_aum
            badges       = {"idle": "⬜ Idle", "running": "🔄 Corriendo",
                            "done": "✅ Listo", "error": "❌ Error"}

            cb1, cb2 = st.columns([2, 4])
            with cb1:
                if st.button("▶ Ejecutar", key="bf_aum_btn", disabled=btn_disabled):
                    if not _creds_ok():
                        _dialog_aunesa_creds()
                    else:
                        _run_bg_aum_multi(list(fechas_aum), "aum_bf",
                                          extra_env=_aunesa_env())
                        st.rerun()
            with cb2:
                st.caption(f"Estado: {badges.get(status_aum, status_aum)}")
                tmppath = st.session_state.get("dm_aum_bf_tmpfile")
                if tmppath:
                    st.caption(f"Log: `{tmppath}`")

            _frag_aum()

    # ══════════════════════════════════════════════════════════════════════════
    # SUB-VISTA: Assets (Valuaciones.Assets)
    # ══════════════════════════════════════════════════════════════════════════
    elif subvista == "Assets":
        _subvista_assets()

    # ══════════════════════════════════════════════════════════════════════════
    # SUB-VISTA: Portfolio (Valuaciones.Carteras × Assets)
    # ══════════════════════════════════════════════════════════════════════════
    else:
        _subvista_portfolio()


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

    # ── Campos Vacíos ─────────────────────────────────────────────────────────
    with st.expander("Campos Vacíos", expanded=False):
        st.caption("Cantidad de documentos con el campo ausente, `null` o `\"\"` por campo.")

        if total_docs == 0:
            st.info("Colección vacía.")
        else:
            rows = []
            for campo in _ASSETS_CAMPOS:
                vacios    = col.count_documents(_asset_esta_vacio(campo))
                con_valor = total_docs - vacios
                rows.append({
                    "Campo":     campo,
                    "Vacíos":    vacios,
                    "Con valor": con_valor,
                    "Cobertura": f"{(con_valor / total_docs * 100):.1f}%",
                })
            df_diag = pd.DataFrame(rows)
            st.dataframe(df_diag, hide_index=True, use_container_width=True,
                         height=38 + len(df_diag) * 35)

            campo_ver = st.selectbox("Ver `unidad` sin valor para el campo",
                                     _ASSETS_CAMPOS, key="as_ver_campo")
            if st.button("Listar", key="as_ver_btn"):
                docs = list(col.find(_asset_esta_vacio(campo_ver),
                                     {"_id": 0, "unidad": 1}).limit(500))
                if not docs:
                    st.success(f"No hay docs con {campo_ver} vacío.")
                else:
                    st.caption(f"{len(docs)} unidades (máx 500 mostradas)")
                    st.dataframe(pd.DataFrame(docs), hide_index=True,
                                 use_container_width=True)

    # ── Insertar Valores ──────────────────────────────────────────────────────
    with st.expander("Insertar Valores", expanded=False):
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

        match_count = col.count_documents(filtro_final)
        st.markdown(f"**Documentos que matchean: `{match_count:,}`**")

        if match_count > 0:
            preview = list(col.find(filtro_final, {"_id": 0, "unidad": 1,
                                                    campo_set: 1}).limit(10))
            if preview:
                st.caption(f"Preview (máx 10 de {match_count:,})")
                st.dataframe(pd.DataFrame(preview), hide_index=True,
                             use_container_width=True)

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
            if faltantes:
                st.caption("Falta: " + ", ".join(faltantes))
        else:
            confirmar = st.checkbox(
                f"Confirmo setear `{campo_set} = \"{valor_set}\"` en {match_count:,} docs",
                key="as_confirm",
            )
            if confirmar and st.button("▶ Aplicar", key="as_apply_btn"):
                docs_antes = list(col.find(
                    filtro_final, {"_id": 1, "unidad": 1, campo_set: 1}
                ))
                result = col.update_many(filtro_final, {"$set": {campo_set: valor_set}})
                _audit_log([{
                    "where":  "Valuaciones.Assets",
                    "key":    {"unidad": d.get("unidad"), "_id": d.get("_id")},
                    "field":  campo_set,
                    "old":    d.get(campo_set),
                    "new":    valor_set,
                    "action": "mass_set",
                } for d in docs_antes])
                st.success(
                    f"✅ Update aplicado: matched={result.matched_count:,}, "
                    f"modified={result.modified_count:,}"
                )
                st.session_state["as_confirm"] = False

    # ── Actualizar Precios ────────────────────────────────────────────────────
    with st.expander("Actualizar Precios", expanded=False):
        st.caption("Para los docs de `Valuaciones.Carteras` sin `precio`, toma el "
                   "precio más reciente de `Valuaciones.AuM` por `unidad` y lo completa.")

        col_carteras = _dbv()["Carteras"]
        col_aum      = _dbv()["AuM"]

        pendientes = col_carteras.count_documents(_asset_esta_vacio("precio"))
        st.markdown(f"**Docs en Carteras sin precio: `{pendientes:,}`**")

        if pendientes == 0:
            st.success("Todos los docs de Carteras tienen precio.")
        elif st.button("▶ Actualizar Precios", key="as_upd_precios"):
            with st.spinner("Buscando precios en AuM y actualizando..."):
                unidades = [u for u in col_carteras.distinct(
                    "unidad", _asset_esta_vacio("precio")) if u]

                actualizados = 0
                sin_match    = []
                for u in unidades:
                    doc_aum = col_aum.find_one(
                        {"unidad": u, "precio": {"$exists": True, "$nin": [None, ""]}},
                        sort=[("fecha_snapshot", -1), ("timestamp", -1)],
                        projection={"precio": 1},
                    )
                    if not doc_aum:
                        sin_match.append(u)
                        continue
                    filtro_u = {"unidad": u, **_asset_esta_vacio("precio")}
                    docs_antes = list(col_carteras.find(
                        filtro_u, {"_id": 1, "unidad": 1, "id_cuenta": 1}
                    ))
                    res = col_carteras.update_many(
                        filtro_u,
                        {"$set": {"precio": doc_aum["precio"]}},
                    )
                    _audit_log([{
                        "where":  "Valuaciones.Carteras",
                        "key":    {"unidad": d.get("unidad"),
                                   "id_cuenta": d.get("id_cuenta"),
                                   "_id": d.get("_id")},
                        "field":  "precio",
                        "old":    None,
                        "new":    doc_aum["precio"],
                        "action": "backfill_precio",
                    } for d in docs_antes])
                    actualizados += res.modified_count

                st.success(f"✅ {actualizados:,} docs actualizados "
                           f"({len(unidades) - len(sin_match)}/{len(unidades)} unidades).")
                if sin_match:
                    st.warning(f"⚠️ {len(sin_match)} unidades sin precio en AuM:")
                    st.dataframe(pd.DataFrame({"unidad": sin_match}),
                                 hide_index=True, use_container_width=True)


# ─── SUB-VISTA PORTFOLIO ─────────────────────────────────────────────────────

_PORTFOLIO_CLAVE   = ["TICKER", "EMISOR", "CLASE_ACTIVO", "CARTERA"]
_PORTFOLIO_EDIT    = ["TICKER", "EMISOR", "CLASE_ACTIVO", "CARTERA",
                      "VENCIMIENTO", "CALIFICACION"]


def _subvista_portfolio():
    col_assets   = _dbv()["Assets"]
    col_carteras = _dbv()["Carteras"]

    st.markdown("**Unidades de Carteras con Asset incompleto**")
    st.caption("Detecta `unidad` presente en `Valuaciones.Carteras` cuyo Asset tiene "
               "al menos uno de TICKER / EMISOR / CLASE_ACTIVO / CARTERA vacío o "
               "que no tiene Asset.")

    # Agregar posiciones/valuación por unidad en Carteras
    pipeline = [
        {"$match": {"unidad": {"$ne": ""}}},
        {"$group": {
            "_id": "$unidad",
            "n_posiciones": {"$sum": 1},
            "valuacion":    {"$sum": {"$ifNull": ["$valuacion", 0]}},
        }},
    ]
    carteras_agg = {r["_id"]: r for r in col_carteras.aggregate(pipeline)}
    unidades_en_carteras = list(carteras_agg.keys())

    if not unidades_en_carteras:
        st.info("No hay posiciones en Valuaciones.Carteras.")
        return

    # Traer Assets para esas unidades
    proj = {"_id": 0, "unidad": 1,
            **{c: 1 for c in _PORTFOLIO_EDIT}}
    assets_map = {
        a["unidad"]: a
        for a in col_assets.find({"unidad": {"$in": unidades_en_carteras}}, proj)
    }

    def _vacio(v):
        return v in (None, "")

    rows = []
    for u in unidades_en_carteras:
        a = assets_map.get(u, {})
        vacios = [c for c in _PORTFOLIO_CLAVE if _vacio(a.get(c))]
        if not vacios and u in assets_map:
            continue
        rows.append({
            "unidad":       u,
            "TICKER":       a.get("TICKER") or "—",
            "EMISOR":       a.get("EMISOR") or "—",
            "CLASE_ACTIVO": a.get("CLASE_ACTIVO") or "—",
            "CARTERA":      a.get("CARTERA") or "—",
            "VENCIMIENTO":  a.get("VENCIMIENTO") or "—",
            "# pos":        carteras_agg[u]["n_posiciones"],
            "Valuación":    round(carteras_agg[u]["valuacion"] or 0, 2),
            "Vacíos":       len(vacios) if u in assets_map else len(_PORTFOLIO_CLAVE),
            "Sin Asset":    "❌" if u not in assets_map else "",
        })

    total = len(unidades_en_carteras)
    problema = len(rows)

    c1, c2, c3 = st.columns(3)
    c1.metric("Unidades en Carteras", f"{total:,}")
    c2.metric("Con problemas", f"{problema:,}")
    c3.metric("% afectado", f"{problema/total*100:.1f}%" if total else "—")

    if not rows:
        st.success("✅ Todas las unidades de Carteras tienen Asset completo.")
        return

    df = pd.DataFrame(rows).sort_values(
        ["Vacíos", "Valuación"], ascending=[False, False]
    ).reset_index(drop=True)
    st.dataframe(df, hide_index=True, use_container_width=True,
                 height=min(38 + len(df) * 35, 500))

    st.divider()

    # ── Editor por unidad ─────────────────────────────────────────────────────
    st.markdown("### Completar Asset")
    st.caption("Seleccioná una unidad y completá los campos faltantes. "
               "`Guardar` hace upsert en `Valuaciones.Assets`.")

    unidades_opts = df["unidad"].tolist()
    u_sel = st.selectbox("Unidad", unidades_opts, key="port_unidad")

    a_actual = assets_map.get(u_sel, {})
    es_nuevo = u_sel not in assets_map
    if es_nuevo:
        st.warning(f"No existe Asset para `{u_sel}` — se creará al guardar.")

    with st.form(key="port_form"):
        nuevos_vals = {}
        cols = st.columns(2)
        for idx, campo in enumerate(_PORTFOLIO_EDIT):
            actual = a_actual.get(campo) or ""
            distinct_vals = sorted(
                str(v) for v in col_assets.distinct(campo)
                if v not in (None, "")
            )
            with cols[idx % 2]:
                nuevos_vals[campo] = st.text_input(
                    campo, value=actual, key=f"port_f_{campo}",
                    help=f"Valores existentes en la colección: {len(distinct_vals)}"
                                + (f" — ej: {', '.join(distinct_vals[:5])}"
                                   if distinct_vals else ""),
                )

        submitted = st.form_submit_button("💾 Guardar")

        if submitted:
            update = {}
            for c, v in nuevos_vals.items():
                v_clean = v.strip()
                if v_clean != (a_actual.get(c) or ""):
                    update[c] = v_clean
            if not update and not es_nuevo:
                st.info("Sin cambios.")
            else:
                res = col_assets.update_one(
                    {"unidad": u_sel},
                    {"$set": {**update, "unidad": u_sel}},
                    upsert=True,
                )
                accion = "creado" if (res.upserted_id or es_nuevo) else "actualizado"
                _audit_log([{
                    "where":  "Valuaciones.Assets",
                    "key":    {"unidad": u_sel},
                    "field":  c,
                    "old":    a_actual.get(c),
                    "new":    v,
                    "action": "upsert_portfolio" if accion == "creado" else "set_portfolio",
                } for c, v in update.items()])
                st.success(f"✅ Asset {accion}: {sorted(update.keys()) or '(sin cambios)'}")
                st.rerun()


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
                    log_entries = []
                    for doc in docs:
                        seg = inferir_segmento(doc.get("denominacion"), doc.get("contraparte"))
                        if seg:
                            old_seg = doc.get("segmento")
                            if old_seg != seg:
                                col.update_one({"_id": doc["_id"]}, {"$set": {"segmento": seg}})
                                log_entries.append({
                                    "where":  "CashFlow.Contrapartes",
                                    "key":    {"_id": doc["_id"],
                                               "contraparte": doc.get("contraparte")},
                                    "field":  "segmento",
                                    "old":    old_seg,
                                    "new":    seg,
                                    "action": "auto_segmento",
                                })
                            auto_ok += 1
                        else:
                            sin_match.append(doc)
                    _audit_log(log_entries)
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
                doc_by_id = {d["_id"]: d for d in sin_match}
                log_entries = []
                for _id, seg in asignaciones.items():
                    if seg != "(saltar)":
                        doc_prev = doc_by_id.get(_id, {})
                        col.update_one({"_id": _id}, {"$set": {"segmento": seg}})
                        log_entries.append({
                            "where":  "CashFlow.Contrapartes",
                            "key":    {"_id": _id,
                                       "contraparte": doc_prev.get("contraparte")},
                            "field":  "segmento",
                            "old":    doc_prev.get("segmento"),
                            "new":    seg,
                            "action": "manual_segmento",
                        })
                        guardados += 1
                _audit_log(log_entries)
                st.success(f"✅ {guardados} docs actualizados")
                st.session_state["dm_seg_sin_match"] = []
                st.rerun()


# ─── LOGS ────────────────────────────────────────────────────────────────────

_LOGS_CANDIDATES = [
    Path("/root/TradingAV/logs"),
    Path(PROJECT_ROOT) / "logs",
]

_LOG_LEVEL_RE = re.compile(r"\b(ERROR|CRITICAL|FATAL|WARNING|WARN|INFO|DEBUG)\b", re.IGNORECASE)


def _logs_dir() -> Path | None:
    for p in _LOGS_CANDIDATES:
        try:
            if p.is_dir():
                return p
        except (PermissionError, OSError):
            continue
    return None


def _tail_bytes(path: Path, n_lines: int, max_bytes: int = 2_000_000) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(-max_bytes, os.SEEK_END)
            f.readline()  # descarta línea parcial
        data = f.read()
    try:
        texto = data.decode("utf-8", errors="replace")
    except Exception:
        texto = data.decode("latin-1", errors="replace")
    lineas = texto.splitlines()
    return lineas[-n_lines:]


def _filtrar_lineas(lineas: list[str], nivel: str) -> list[str]:
    if nivel == "Todos":
        return lineas
    niveles = {
        "Errores":   {"ERROR", "CRITICAL", "FATAL"},
        "Warnings+": {"ERROR", "CRITICAL", "FATAL", "WARNING", "WARN"},
    }
    objetivo = niveles.get(nivel, set())
    if not objetivo:
        return lineas
    out = []
    for l in lineas:
        m = _LOG_LEVEL_RE.search(l)
        if m and m.group(1).upper() in objetivo:
            out.append(l)
    return out


def _rotar_log(path: Path, keep_last: int = 2000) -> int:
    """Archiva el contenido actual a .1 y trunca el archivo manteniendo las últimas keep_last líneas."""
    archivo_archive = path.with_suffix(path.suffix + ".1")
    shutil.copy2(path, archivo_archive)
    ultimas = _tail_bytes(path, keep_last)
    with path.open("w", encoding="utf-8") as f:
        f.write("\n".join(ultimas))
        if ultimas:
            f.write("\n")
    return archivo_archive.stat().st_size


def _tab_logs():
    st.markdown("### Logs")

    d = _logs_dir()
    if d is None:
        st.warning(f"No encuentro un directorio de logs. Buscado en: {', '.join(str(p) for p in _LOGS_CANDIDATES)}")
        return
    st.caption(f"Directorio: `{d}`")

    archivos = sorted(
        [p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not archivos:
        st.info("No hay archivos en el directorio.")
        return

    resumen = pd.DataFrame([{
        "Archivo":  p.name,
        "Tamaño":   f"{p.stat().st_size / 1024:.1f} KB" if p.stat().st_size < 1_048_576 else f"{p.stat().st_size / 1_048_576:.2f} MB",
        "Modificado": datetime.fromtimestamp(p.stat().st_mtime, tz=_AR_TZ).strftime("%Y-%m-%d %H:%M"),
    } for p in archivos])
    st.dataframe(resumen, hide_index=True, use_container_width=True)

    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
    with c1:
        elegido = st.selectbox("Archivo", [p.name for p in archivos], key="logs_file")
    with c2:
        n_lineas = st.number_input("Líneas", min_value=50, max_value=5000, value=500, step=50, key="logs_n")
    with c3:
        nivel = st.selectbox("Filtro", ["Todos", "Errores", "Warnings+"], key="logs_nivel")
    with c4:
        autorefresh = st.toggle("Auto (10s)", key="logs_auto")

    path = d / elegido

    if autorefresh:
        @st.fragment(run_every=10)
        def _render():
            _render_log(path, n_lineas, nivel)
        _render()
    else:
        _render_log(path, n_lineas, nivel)

    st.divider()
    cc1, cc2 = st.columns([1, 5])
    with cc1:
        if st.button("Rotar ahora", key="logs_rotate", help=f"Copia a {elegido}.1 y trunca el actual a últimas 2000 líneas"):
            try:
                tamanio_archivo = _rotar_log(path)
                st.success(f"Rotado: {elegido} → {elegido}.1 ({tamanio_archivo/1024:.1f} KB)")
            except Exception as e:
                st.error(f"Error: {e}")
    with cc2:
        st.caption("Rotación manual: guarda copia `.1` y deja el archivo actual con las últimas 2000 líneas.")


def _render_log(path: Path, n_lineas: int, nivel: str):
    try:
        lineas = _tail_bytes(path, int(n_lineas))
    except FileNotFoundError:
        st.warning("Archivo desaparecido.")
        return
    lineas = _filtrar_lineas(lineas, nivel)
    if not lineas:
        st.info("Sin líneas para mostrar con ese filtro.")
        return
    st.caption(f"Mostrando {len(lineas)} líneas · actualizado {datetime.now(_AR_TZ).strftime('%H:%M:%S')}")
    st.code("\n".join(lineas), language="log")


# ─── HISTORIAL ───────────────────────────────────────────────────────────────

def _tab_historial():
    st.markdown("### Historial de cambios")
    st.caption("Registra cambios manuales aplicados desde Manager en `Manager.ChangeLog`.")

    col = get_mongo_client()["Manager"]["ChangeLog"]

    try:
        total = col.estimated_document_count()
    except Exception as e:
        st.error(f"No se pudo acceder a Manager.ChangeLog: {e}")
        return

    if total == 0:
        st.info("Sin cambios registrados todavía.")
        return

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        wheres = sorted({w for w in col.distinct("where") if w})
        sel_where = st.multiselect("Colección", wheres, default=wheres, key="hist_where")
    with c2:
        actions = sorted({a for a in col.distinct("action") if a})
        sel_action = st.multiselect("Acción", actions, default=actions, key="hist_action")
    with c3:
        limite = st.number_input("Últimos N", min_value=20, max_value=5000,
                                 value=200, step=50, key="hist_limit")

    filtro = {}
    if sel_where:  filtro["where"]  = {"$in": sel_where}
    if sel_action: filtro["action"] = {"$in": sel_action}

    docs = list(col.find(filtro).sort("when", -1).limit(int(limite)))
    if not docs:
        st.info("Sin resultados con los filtros actuales.")
        return

    def _fmt_when(w):
        if isinstance(w, datetime):
            if w.tzinfo is None:
                w = w.replace(tzinfo=timezone.utc)
            return w.astimezone(_AR_TZ).strftime("%Y-%m-%d %H:%M:%S")
        return str(w)

    def _fmt_key(k):
        if not isinstance(k, dict): return str(k)
        partes = []
        for kk in ("unidad", "contraparte", "id_cuenta"):
            if kk in k and k[kk] not in (None, ""):
                partes.append(f"{kk}={k[kk]}")
        return " · ".join(partes) if partes else str(k.get("_id", k))[:60]

    df = pd.DataFrame([{
        "Cuándo":  _fmt_when(d.get("when")),
        "Dónde":   d.get("where", ""),
        "Clave":   _fmt_key(d.get("key", {})),
        "Campo":   d.get("field", ""),
        "Anterior": "" if d.get("old") is None else str(d.get("old"))[:60],
        "Nuevo":    "" if d.get("new") is None else str(d.get("new"))[:60],
        "Acción":  d.get("action", ""),
    } for d in docs])

    st.dataframe(df, hide_index=True, use_container_width=True, height=500)
    st.caption(f"Mostrando {len(df):,} de {total:,} entradas totales.")


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

def vista_data_manager():
    st.markdown("## Manager")
    st.caption("Diagnóstico, backfills, validaciones, logs, historial y setup.")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        ["Diagnóstico", "Backfills", "Validaciones", "Logs", "Historial", "Setup"]
    )
    with tab1: _tab_diagnostico()
    with tab2: _tab_backfills()
    with tab3: _tab_validaciones()
    with tab4: _tab_logs()
    with tab5: _tab_historial()
    with tab6: _tab_setup()
