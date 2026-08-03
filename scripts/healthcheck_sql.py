"""scripts/healthcheck_sql.py — smoke-test SQL post-decomiso Mongo (el "segundo check").

QUÉ HACE
========
Por cada dominio del producto EJECUTA el reader REAL que usa la app/los motores
(el mismo `api/services/*_sql.py` o `core/*_sql.py`) contra Postgres y reporta:

    🟢 PASS  — el reader corrió y devolvió algo (count / primer valor de muestra)
    🔴 FAIL  — el reader tiró una excepción (se captura el traceback, NO crashea)
    🟡 SKIP  — no se pudo conseguir un parámetro real (id_cuenta/ticker) → no es rojo

NO depende de mirar paneles a mano: si esto pasa en verde, la web lee SQL OK.

CÓMO CORRERLO (en el Droplet, con la .env de Postgres cargada):
    python -m scripts.healthcheck_sql

EXIT CODE
=========
1 si hay algún 🔴 en un dominio CRÍTICO (los que tumban la web/los motores);
0 si todo lo crítico está verde (aunque haya rojos/skips en dominios opcionales).

NOTAS DE FIDELIDAD (no inferibles — leídas del código):
- Varios readers de `core/` (dolar_sql, dolar_oficial, curvas_sql) ENVUELVEN sus
  queries en try/except y devuelven None/[] ante error → no tiran excepción. Por
  eso el primer check es CONECTIVIDAD A POSTGRES (SELECT 1, sí levanta): si la DB
  está caída, ese rojo lo explica todo aunque esos dominios reporten "vacío".
- `scanner_sql.get_cedears_scanner` es HÍBRIDO: el CCL live sale de Mongo
  (`scanner.get_ccl_live`). Va como dominio NO crítico aparte, para que una caída
  de Mongo no ensucie el veredicto SQL. El núcleo Scanner (universo + precios +
  ADR) sí es SQL puro y va como crítico.
- `agro_sql.get_mejoras_dispo` lee Mongo (FuturosDLRSnapshot) → NO se testea acá.
"""
from __future__ import annotations

import sys
import time
import traceback
from datetime import date, timedelta

# ── tipos de retorno de cada check ───────────────────────────────────────────
# Un check devuelve (status, sample, detail). FAIL se infiere de una excepción.
PASS = "PASS"
SKIP = "SKIP"


def _p(sample: object, detail: str = "") -> tuple[str, str, str]:
    return (PASS, str(sample), detail)


def _s(reason: str) -> tuple[str, str, str]:
    return (SKIP, "", reason)


# ── helpers para conseguir params reales desde SQL (None si no se puede) ──────
def _scalar(sql: str) -> object | None:
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return row[0] if row else None


def _try_scalar(sql: str) -> object | None:
    try:
        return _scalar(sql)
    except Exception:
        return None


def _id_cuenta() -> str | None:
    v = _try_scalar(
        "SELECT id_cuenta FROM portafolio.tenencia "
        "WHERE aum = 'si' AND id_cuenta IS NOT NULL LIMIT 1")
    return str(v) if v is not None else None


def _ticker_corto() -> str | None:
    v = _try_scalar(
        "SELECT ticker_corto FROM mercado.cedears "
        "WHERE activo IS TRUE AND ticker_corto IS NOT NULL LIMIT 1")
    return str(v) if v is not None else None


def _pyrofex_ticker() -> str | None:
    v = _try_scalar(
        "SELECT instruments->0->>'ticker' FROM manager.pyrofex_instruments "
        "WHERE jsonb_array_length(COALESCE(instruments, '[]'::jsonb)) > 0 "
        "AND instruments->0->>'ticker' IS NOT NULL LIMIT 1")
    return str(v) if v is not None else None


def _n(x) -> str:
    """Sample helper: 'n=<len>'."""
    try:
        return f"n={len(x)}"
    except TypeError:
        return str(x)


# ══════════════════════════════════════════════════════════════════════════════
# CHECKS — uno por dominio. Cada uno IMPORTA lazy (un import roto = 🔴 del dominio,
# no aborta el script) y devuelve (status, sample, detail).
# ══════════════════════════════════════════════════════════════════════════════
def chk_postgres():
    """Conectividad cruda — si esto falla, todo lo demás es ruido."""
    v = _scalar("SELECT 1")
    return _p(f"SELECT 1 -> {v}")


def chk_dolar():
    from core import dolar_sql
    snap = dolar_sql.snapshot_live()
    mep = dolar_sql.ultimo("mep")
    uva = dolar_sql.ultimo_uva()
    snap_mep = snap.get("mep") if snap else None
    ult_mep = mep.get("mep") if mep else None
    detail = ""
    if snap is None and mep is None and uva is None:
        detail = "todo None — feed caído o tablas vacías (los readers tragan el error)"
    return _p(f"snap.mep={snap_mep} ultimo.mep={ult_mep} uva={uva}", detail)


def chk_dolar_oficial():
    from core.dolar_oficial import mid_oficial_live
    d = mid_oficial_live("oficial")
    detail = "value=None — feed PC oficina caído (normal fuera de rueda)" if d.get(
        "value") is None else ""
    return _p(f"value={d.get('value')} source={d.get('source')}", detail)


def chk_macro():
    from core import series_macro
    dolar = series_macro.ultimo_valor("DOLAR")
    desde = (date.today() - timedelta(days=30)).isoformat()
    cer = series_macro.serie_dict("CER", desde=desde)
    return _p(f"DOLAR={dolar} CER(30d)={_n(cer)}")


def chk_curvas():
    from core import curvas_sql
    todos = curvas_sql.cargar_todos()
    tf = curvas_sql.por_curva("tasa_fija")
    detail = "cargar_todos() vacío — ¿mercado.curvas sin datos?" if not todos else ""
    return _p(f"todos={_n(todos)} tasa_fija={_n(tf)}", detail)


def chk_renta_fija():
    from api.services import mercado_hist_sql, renta_fija_sql
    curva = renta_fija_sql.listar_curva("tasa_fija")
    rf = renta_fija_sql.get_renta_fija()
    fwd = mercado_hist_sql.get_forwards()
    be = mercado_hist_sql.get_breakevens()
    cauc = mercado_hist_sql.get_caucion()
    dlr = mercado_hist_sql.get_futuros_dlr()
    return _p(f"curva_tf={_n(curva)} snapshot={_n(rf)} fwd={_n(fwd)} "
              f"be={_n(be)} cauc={_n(cauc)} dlr={_n(dlr)}")


def chk_opciones():
    from api.services import opciones_sql
    meta = opciones_sql.get_opciones_meta()
    chain = opciones_sql.get_opciones()
    return _p(f"meta.tasa={meta.get('tasa')} chain={_n(chain)}")


def chk_agro():
    from api.services import agro_sql
    pase = agro_sql.get_pase_agro()
    cam = agro_sql.get_camara_cereales()
    return _p(f"pase.bloques={_n(pase.get('bloques', []))} "
              f"camara={_n(cam.get('cereales', []))}")


def chk_market():
    from api.services import market_sql
    q = market_sql.quotes()
    return _p(f"quotes={_n(q)}")


def chk_news():
    from api.services import news_sql
    heads = news_sql.list_headlines(limit=5)
    st = news_sql.stats()
    return _p(f"headlines(5)={_n(heads)} stats.total={st.get('total')}")


def chk_scanner():
    from api.services import scanner_sql
    universo = scanner_sql.get_universo()
    tk = _ticker_corto()
    if not tk:
        return _s("sin ticker_corto activo en mercado.cedears")
    # @cached → SIEMPRE kwargs (el wrapper es wrapper(**kwargs); posicional revienta).
    rets = scanner_sql.get_ticker_returns(ticker=tk)
    quant = scanner_sql.get_quant_stats(ticker=tk)
    pivots = scanner_sql.get_pivot_points(ticker=tk)
    return _p(f"universo={_n(universo)} {tk}: returns={_n(rets.get('returns', []))} "
              f"beta_spy={quant.get('beta', {}).get('spy')} pivot_last={pivots.get('last')}")


def chk_scanner_live():
    """HÍBRIDO (CCL de Mongo) — separado para no contaminar el veredicto SQL."""
    from api.services import scanner_sql
    rows = scanner_sql.get_cedears_scanner()
    return _p(f"cedears_scanner={_n(rows)}", "incluye CCL live de Mongo (híbrido)")


def chk_tenencia():
    from api.services import portfolio_sql
    cuentas = portfolio_sql.listar_cuentas()
    snap = portfolio_sql._max_snap()
    if snap is None:
        return _p(f"cuentas={_n(cuentas)} total=—", "sin snapshot de tenencia (aum='si')")
    total = portfolio_sql.total_snapshot(fecha=snap.isoformat() if hasattr(snap, "isoformat")
                                         else str(snap))
    suma = round(sum(d["valuacion"] for d in total.get("docs", [])), 2)
    return _p(f"cuentas={_n(cuentas)} snap={snap} aum_total={suma}")


def chk_pnl():
    from api.services import pnl_sql
    todas = pnl_sql.pnl_todas_cuentas_sql()
    tot = todas.get("totales", {})
    idc = _id_cuenta()
    if not idc:
        return _p(f"todas.n_filas={tot.get('n_filas')} pnl_total={tot.get('pnl_total')}",
                  "sin id_cuenta para probar el motor por cuenta")
    cta = pnl_sql.pnl_por_cuenta_sql(idc)
    return _p(f"todas.n_filas={tot.get('n_filas')} por_cuenta[{idc}].rows="
              f"{_n(cta.get('rows', []))}")


def chk_operaciones():
    from api.services import operaciones_sql
    fechas = operaciones_sql.ops_fechas()
    serie = operaciones_sql.ops_serie(moneda="ARS")
    return _p(f"fechas={_n(fechas.get('fechas', []))} "
              f"serie_ARS={_n(serie.get('serie', []))}")


def chk_comercial():
    from api.services import comercial_sql
    dims = comercial_sql.dimensiones_comercial()
    inf = comercial_sql.informe_comercial()
    return _p(f"combos={_n(dims.get('combos', []))} "
              f"comerciales={_n(inf.get('comerciales', []))}")


def chk_manager_auth():
    from api.services import manager_infra_sql
    from core import grupos_sql, roles_sql
    users = roles_sql.list_users_sql()
    matrix = roles_sql.load_matrix_sql()
    grupos = grupos_sql.listar_grupos_sql()
    jobs = manager_infra_sql.jobs_history_sql(limit=5)
    return _p(f"users={_n(users)} matrix_roles={_n(matrix)} "
              f"grupos={_n(grupos)} jobs(5)={_n(jobs)}")


def chk_pyrofex():
    cnt = _scalar("SELECT count(*) FROM manager.pyrofex_instruments")
    tk = _pyrofex_ticker()
    if not tk:
        return _p(f"instruments={cnt}", "tabla vacía o sin ticker → no se prueba _existe_en_pyrofex")
    from api.routers.operar import _existe_en_pyrofex
    existe = _existe_en_pyrofex(tk)
    return _p(f"instruments={cnt} _existe('{tk}')={existe}")


def chk_contrapartes():
    from api.services import contrapartes_seg
    lista = contrapartes_seg.listar_contrapartes()
    segs = contrapartes_seg.segmentos_distinct()
    return _p(f"contrapartes={_n(lista.get('contrapartes', []))} "
              f"segmentos={_n(segs.get('segmentos', []))}")


def chk_comitentes():
    n = _scalar("SELECT count(*) FROM clientes.comitentes")
    activas = _scalar("SELECT count(*) FROM clientes.comitentes WHERE estado = 'Activa'")
    return _p(f"comitentes={n} activas={activas}")


def chk_acreencias():
    from api.services import acreencias
    por_dia = acreencias.por_dia()
    return _p(f"por_dia={_n(por_dia)}")


def chk_movimientos():
    from api.services import cashflow_sql
    flujos = cashflow_sql.listar_flujos()
    return _p(f"flujos={_n(flujos)}", "lee TODA operaciones.movimientos (sin filtro)")


# (label, función, es_crítico) — el orden es el de impresión.
CHECKS: list[tuple[str, object, bool]] = [
    ("Postgres (conectividad)", chk_postgres, True),
    ("Dólar / MEP / UVA", chk_dolar, True),
    ("Dólar oficial (MAE)", chk_dolar_oficial, False),
    ("Macro (series)", chk_macro, True),
    ("Curvas (master RF)", chk_curvas, True),
    ("Renta fija (live + hist)", chk_renta_fija, True),
    ("Opciones (GGAL)", chk_opciones, False),
    ("Agro (pase + cámara)", chk_agro, False),
    ("Market / Home", chk_market, True),
    ("News", chk_news, False),
    ("Scanner RV (universo + quant)", chk_scanner, True),
    ("Scanner RV live (cedears, híbrido)", chk_scanner_live, False),
    ("Tenencia / AuM", chk_tenencia, True),
    ("PnL Títulos", chk_pnl, False),
    ("Operaciones", chk_operaciones, True),
    ("Comercial", chk_comercial, False),
    ("Manager / Auth", chk_manager_auth, True),
    ("PyRofex (discovery)", chk_pyrofex, True),
    ("Contrapartes", chk_contrapartes, False),
    ("Comitentes", chk_comitentes, False),
    ("Acreencias", chk_acreencias, False),
    ("Movimientos / Flujos", chk_movimientos, False),
]

_EMOJI = {PASS: "🟢", "FAIL": "🔴", SKIP: "🟡"}


def _trunc(s: str, n: int) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> int:
    print("\n=== HEALTHCHECK SQL — readers reales por dominio (post-decomiso Mongo) ===\n")
    rows: list[dict] = []
    for label, func, critical in CHECKS:
        t0 = time.perf_counter()
        try:
            res = func()
            status, sample, detail = res if isinstance(res, tuple) and len(res) == 3 else (
                PASS, str(res), "")
        except Exception as exc:  # el punto es NUNCA crashear — todo rojo se reporta
            status = "FAIL"
            sample = ""
            detail = f"{type(exc).__name__}: {exc}"
            # traceback corto para diagnóstico (última línea del stack)
            tb = traceback.extract_tb(exc.__traceback__)
            if tb:
                last = tb[-1]
                detail += f"  @ {last.filename.split('/')[-1]}:{last.lineno}"
        ms = (time.perf_counter() - t0) * 1000
        rows.append({"label": label, "status": status, "sample": sample,
                     "detail": detail, "critical": critical, "ms": ms})

    # ── tabla ────────────────────────────────────────────────────────────────
    w_dom, w_sample, w_det = 36, 46, 44
    header = f"{'DOMINIO':<{w_dom}} {'ESTADO':<8} {'MUESTRA':<{w_sample}} DETALLE"
    print(header)
    print("─" * (w_dom + 8 + w_sample + w_det + 4))
    for r in rows:
        emoji = _EMOJI.get(r["status"], "❓")
        crit = "*" if r["critical"] else " "
        dom = _trunc(f"{crit}{r['label']}", w_dom)
        sample = _trunc(r["sample"] or "—", w_sample)
        detail = _trunc(r["detail"] or "", w_det)
        print(f"{dom:<{w_dom}} {emoji} {r['status']:<5} {sample:<{w_sample}} {detail}")

    # ── resumen ──────────────────────────────────────────────────────────────
    n_pass = sum(1 for r in rows if r["status"] == PASS)
    n_fail = sum(1 for r in rows if r["status"] == "FAIL")
    n_skip = sum(1 for r in rows if r["status"] == SKIP)
    fails_crit = [r["label"] for r in rows if r["status"] == "FAIL" and r["critical"]]
    fails_opt = [r["label"] for r in rows if r["status"] == "FAIL" and not r["critical"]]

    print("\n" + "─" * 60)
    print(f"RESUMEN:  {n_pass} 🟢  /  {n_fail} 🔴  /  {n_skip} 🟡   (* = dominio crítico)")
    if fails_crit:
        print(f"🔴 CRÍTICOS caídos: {', '.join(fails_crit)}")
    if fails_opt:
        print(f"🔴 opcionales caídos: {', '.join(fails_opt)}")
    if not n_fail:
        print("✅ Todos los readers SQL respondieron sin excepción.")
    print()

    return 1 if fails_crit else 0


if __name__ == "__main__":
    sys.exit(main())
