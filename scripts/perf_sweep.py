"""perf_sweep.py — Barrido de performance de los services (foto real de una).

Corre EN EL DROPLET, contra Atlas real, una lista CURADA y SOLO-LECTURA de los
services que alimentan las vistas, y para cada uno mide:

  • cold_ms  → primera llamada (caché frío): lo que sufre el primer usuario.
  • warm_ms  → segunda llamada: si el service tiene @cached, acá se ve el ahorro.
  • %mongo / %cpu → de la corrida fría, cuánto del tiempo fue I/O (Mongo/red)
                    vs cómputo Python. ESTE es el dato que decide dónde atacar.

Imprime el ranking ordenado por cold_ms y guarda el árbol de llamadas (HTML de
pyinstrument) de los N más lentos en logs/perf_sweep_<ts>/ para abrir y mirar.

    python -m scripts.perf_sweep                 # barrido completo
    python -m scripts.perf_sweep --top 12        # guardar HTML de los 12 peores
    python -m scripts.perf_sweep --only curva    # solo targets que matcheen 'curva'

⚠️ SEGURIDAD: la lista es un ALLOWLIST explícito de funciones de lectura. NO
incluye nada que mande órdenes, cree operativas, escriba Mongo o pegue a APIs
externas (send_order, crear_operativa, update_opciones_tasa, fetch_y_consolidar,
set_*, etc.). Agregar un target = agregarlo a mano a TARGETS, nunca por reflexión.

Lectura del resultado: si un target lento tiene %mongo alto → el problema es
shape de query / índice / caching, NO el cómputo (Rust/numpy no ayudan). Si
tuviera %cpu alto → ahí sí hay algo para vectorizar. Ver scripts/profile_quant.py.
"""
from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"

# Frames cuyo file_path matchee alguno de estos → tiempo de I/O (espera), no CPU.
_IO_MARKERS = (
    "pymongo", "bson", "gridfs",        # Mongo
    "socket.py", "ssl.py", "_ssl", "selectors.py",  # red / TLS
    "asyncio",                          # await
    "urllib3", "httpx", "httpcore", "requests",     # HTTP externo (no debería, por las dudas)
)


# ──────────────────────────────────────────────────────────────────────────
# Contexto: args reales derivados de Mongo (read-only, defensivo)
# ──────────────────────────────────────────────────────────────────────────
def derive_context() -> dict:
    ctx: dict = {"curva": None, "tickers": [], "instrumento": None,
                 "id_cuenta": None, "operador": None, "cedear": None}
    try:
        from core.mongo import get_mongo_client_read
        cli = get_mongo_client_read()

        curvas = cli["Trading"]["Curvas"]
        nombres = curvas.distinct("curva")
        ctx["curva"] = "tasa_fija" if "tasa_fija" in nombres else (nombres[0] if nombres else None)
        for d in curvas.find({}, {"ticker_corto": 1, "ticker": 1}).limit(8):
            tc = d.get("ticker_corto")
            if tc:
                ctx["tickers"].append(tc)
            if not ctx["instrumento"] and d.get("ticker"):
                ctx["instrumento"] = d["ticker"]

        for col, db in (("AuM", "Valuaciones"), ("NegocioMovimientos", "CashFlow")):
            if ctx["id_cuenta"]:
                break
            try:
                ids = cli[db][col].distinct("id_cuenta")
                if ids:
                    ctx["id_cuenta"] = str(ids[0])
            except Exception:
                pass

        try:
            ced = cli["Trading"]["CedearsSnapshot"].distinct("ticker")
            ctx["cedear"] = ced[0] if ced else None
        except Exception:
            pass
    except Exception as e:
        print(f"  ⚠ no pude derivar contexto desde Mongo: {e}")
    return ctx


# ──────────────────────────────────────────────────────────────────────────
# Allowlist de targets — SOLO LECTURA. Cada uno: (label, thunk-zero-arg).
# ──────────────────────────────────────────────────────────────────────────
def build_targets(ctx: dict) -> list[tuple[str, Callable]]:
    from api.services import (
        analitica,
        argy,
        back_office_titulos,
        camara_cereales,
        canje,
        comercial,
        comparar_inversion,
        derivados,
        derivados_agro,
        fair_value,
        macro,
        mejoras_dispo,
        opciones,
        order_book,
        pnl,
        portfolio,
        rem,
        renta_fija,
        repo,
        scanner,
    )

    curva = ctx.get("curva")
    tk = ctx["tickers"][0] if ctx.get("tickers") else None
    idc = ctx.get("id_cuenta")
    ced = ctx.get("cedear")

    T: list[tuple[str, Callable]] = [
        # --- zero-arg / sin dependencias de contexto ---
        ("scanner.get_ccl_live", lambda: scanner.get_ccl_live()),
        ("scanner.get_cedears_scanner", lambda: scanner.get_cedears_scanner()),
        ("derivados.get_futuros_dlr", lambda: derivados.get_futuros_dlr()),
        ("derivados.get_forwards", lambda: derivados.get_forwards()),
        ("derivados.get_forwards_zscore", lambda: derivados.get_forwards_zscore()),
        ("derivados.get_breakevens", lambda: derivados.get_breakevens()),
        ("derivados.get_historico_breakevens", lambda: derivados.get_historico_breakevens()),
        ("macro.get_ultimo_mep", lambda: macro.get_ultimo_mep()),
        ("macro.get_badlar", lambda: macro.get_badlar()),
        ("macro.get_cer", lambda: macro.get_cer()),
        ("macro.get_dolar", lambda: macro.get_dolar()),
        ("macro.get_historico_mep", lambda: macro.get_historico_mep()),
        ("argy.get_argy_with_returns", lambda: argy.get_argy_with_returns()),
        ("derivados_agro.get_pase_agro", lambda: derivados_agro.get_pase_agro()),
        ("camara_cereales.get_camara_cereales", lambda: camara_cereales.get_camara_cereales()),
        ("back_office_titulos.get_titulos_mercado", lambda: back_office_titulos.get_titulos_mercado()),
        ("mejoras_dispo.get_mejoras_dispo", lambda: mejoras_dispo.get_mejoras_dispo()),
        ("comercial.listar_operadores_comercial", lambda: comercial.listar_operadores_comercial()),
        ("comercial.resumen_por_operador", lambda: comercial.resumen_por_operador()),
        ("comercial.analisis_comercial", lambda: comercial.analisis_comercial()),
        ("comparar_inversion.listar_bonos_seleccionables", lambda: comparar_inversion.listar_bonos_seleccionables()),
        ("opciones.get_opciones", lambda: opciones.get_opciones()),
        ("opciones.get_opciones_meta", lambda: opciones.get_opciones_meta()),
        ("opciones.get_vr_ggal_serie", lambda: opciones.get_vr_ggal_serie()),
        ("rem.listar_informes", lambda: rem.listar_informes()),
        ("rem.breakeven_acumulado", lambda: rem.breakeven_acumulado()),
        ("repo.get_caucion", lambda: repo.get_caucion()),
        ("renta_fija.get_renta_fija (all)", lambda: renta_fija.get_renta_fija()),
        ("renta_fija.get_historico_trades (all)", lambda: renta_fija.get_historico_trades()),
        ("canje.serie_canje (AL30)", lambda: canje.serie_canje()),
        # --- portfolio (sin scope = todas) ---
        ("portfolio.listar_cuentas", lambda: portfolio.listar_cuentas()),
        ("portfolio.tasa_fija_snapshot", lambda: portfolio.tasa_fija_snapshot()),
        ("portfolio.cer_snapshot", lambda: portfolio.cer_snapshot()),
        ("portfolio.fci_snapshot", lambda: portfolio.fci_snapshot()),
        ("portfolio.total_snapshot", lambda: portfolio.total_snapshot()),
        # --- PNL: cache vs compute (el compute es el peor caso real) ---
        ("pnl.pnl_todas_cuentas (cache)", lambda: pnl.pnl_todas_cuentas()),
        ("pnl.pnl_todas_cuentas_compute (HEAVY)", lambda: pnl.pnl_todas_cuentas_compute()),
    ]

    # --- targets que dependen de contexto (se omiten si falta el arg) ---
    if curva:
        T += [
            (f"renta_fija.listar_curva({curva})", lambda c=curva: renta_fija.listar_curva(curva=c)),
            (f"renta_fija.get_historico_curva({curva})", lambda c=curva: renta_fija.get_historico_curva(curva=c)),
            (f"renta_fija.get_retorno_total_data({curva})", lambda c=curva: renta_fija.get_retorno_total_data(curva=c)),
            (f"fair_value.get_fair_value_live({curva})", lambda c=curva: fair_value.get_fair_value_live(curva=c)),
            (f"fair_value.get_fair_value_cierre({curva})", lambda c=curva: fair_value.get_fair_value_cierre(curva=c)),
            (f"order_book.get_order_books_curva({curva})", lambda c=curva: order_book.get_order_books_curva(curva=c)),
        ]
    if tk:
        T += [
            (f"analitica.liquidez_secundario({tk})", lambda t=tk: analitica.liquidez_secundario(ticker=t)),
            (f"fair_value.get_fair_value_historico_bono({tk})", lambda t=tk: fair_value.get_fair_value_historico_bono(ticker=t)),
        ]
    if ced:
        T += [
            (f"scanner.get_ticker_returns({ced})", lambda t=ced: scanner.get_ticker_returns(ticker=t)),
            (f"scanner.get_pivot_points({ced})", lambda t=ced: scanner.get_pivot_points(ticker=t)),
            (f"scanner.get_quant_stats({ced})", lambda t=ced: scanner.get_quant_stats(ticker=t)),
        ]
    if idc:
        T += [
            (f"pnl.pnl_por_cuenta({idc})", lambda i=idc: pnl.pnl_por_cuenta(id_cuenta=i)),
            (f"comercial.portafolio_cliente({idc})", lambda i=idc: comercial.portafolio_cliente(id_cuenta=i)),
            (f"comercial.operaciones_cliente({idc})", lambda i=idc: comercial.operaciones_cliente(id_cuenta=i)),
        ]
    for var in ("cer", "badlar", "dolar_oficial"):
        T.append((f"macro.obtener_serie_macro({var})", lambda v=var: macro.obtener_serie_macro(variable=v)))

    return T


# ──────────────────────────────────────────────────────────────────────────
# Medición
# ──────────────────────────────────────────────────────────────────────────
def _io_cpu_split(session) -> tuple[float, float]:
    """Recorre el árbol de frames y reparte self-time en (io_s, cpu_s).

    Una vez que el árbol entra en un frame de I/O (pymongo/socket/asyncio…),
    TODO lo que cuelga abajo cuenta como I/O — incluido el `recv` a nivel C
    (built-in, path vacío) y el decode de BSON. Es lo correcto para 'esto es
    Mongo/red vs mi cómputo': el round-trip entero es costo de I/O.
    """
    io = cpu = 0.0

    def walk(fr, under_io: bool):
        nonlocal io, cpu
        if fr is None:
            return
        path = getattr(fr, "file_path", "") or ""
        here_io = under_io or any(m in path for m in _IO_MARKERS)
        self_t = getattr(fr, "total_self_time", 0.0) or 0.0
        if here_io:
            io += self_t
        else:
            cpu += self_t
        for c in (fr.children or []):
            walk(c, here_io)

    walk(session.root_frame(), False)
    return io, cpu


try:
    from pyinstrument import Profiler
    HAS_PYINSTRUMENT = True
except ImportError:
    HAS_PYINSTRUMENT = False


def measure(thunk) -> dict:
    if not HAS_PYINSTRUMENT:
        # Modo degradado: sin pyinstrument igual medimos timing (cold/warm) —
        # es el 80% del valor (el ranking). El split Mongo/CPU queda en None.
        t0 = time.perf_counter()
        thunk()
        cold_ms = (time.perf_counter() - t0) * 1000.0
        t1 = time.perf_counter()
        thunk()
        warm_ms = (time.perf_counter() - t1) * 1000.0
        return {"cold_ms": cold_ms, "warm_ms": warm_ms,
                "pct_io": None, "pct_cpu": None, "html": None}

    # 1) cold + profilado (caché frío → acá pega a Mongo de verdad)
    prof = Profiler(interval=0.001)
    prof.start()
    t0 = time.perf_counter()
    try:
        thunk()
    finally:
        prof.stop()
    cold_ms = (time.perf_counter() - t0) * 1000.0

    # 2) warm (segunda llamada, sin profiler → timing limpio del cache-hit)
    t1 = time.perf_counter()
    thunk()
    warm_ms = (time.perf_counter() - t1) * 1000.0

    io_s, cpu_s = _io_cpu_split(prof.last_session)
    tot = io_s + cpu_s
    pct_io = 100.0 * io_s / tot if tot > 0 else 0.0
    pct_cpu = 100.0 * cpu_s / tot if tot > 0 else 0.0
    return {
        "cold_ms": cold_ms, "warm_ms": warm_ms,
        "pct_io": pct_io, "pct_cpu": pct_cpu,
        "html": prof.output_html(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Barrido de performance de services (read-only)")
    ap.add_argument("--top", type=int, default=8, help="cuántos HTML (los más lentos) guardar")
    ap.add_argument("--only", type=str, default=None, help="filtrar targets que contengan este texto")
    args = ap.parse_args()

    print("Derivando contexto desde Mongo…")
    ctx = derive_context()
    print(f"  curva={ctx['curva']}  ticker={ctx['tickers'][:1]}  id_cuenta={ctx['id_cuenta']}  cedear={ctx['cedear']}")

    targets = build_targets(ctx)
    if args.only:
        targets = [(lbl, fn) for lbl, fn in targets if args.only.lower() in lbl.lower()]
    if not HAS_PYINSTRUMENT:
        print("  (pyinstrument no instalado → modo timing: cold/warm sí, split Mongo/CPU no.")
        print("   Para el split completo: venv/bin/pip install pyinstrument)")
    print(f"Corriendo {len(targets)} targets…\n")

    rows: list[dict] = []
    for lbl, thunk in targets:
        try:
            r = measure(thunk)
            r["label"] = lbl
            rows.append(r)
            print(f"  ✓ {lbl:<48} {r['cold_ms']:8.1f} ms")
        except Exception as e:
            rows.append({"label": lbl, "error": f"{type(e).__name__}: {e}",
                         "cold_ms": -1, "warm_ms": -1, "pct_io": 0, "pct_cpu": 0})
            print(f"  ✗ {lbl:<48} SKIP ({type(e).__name__}: {str(e)[:50]})")

    ok = [r for r in rows if r.get("cold_ms", -1) >= 0]
    ok.sort(key=lambda r: r["cold_ms"], reverse=True)

    print("\n" + "=" * 92)
    print(f"{'target':<48}{'cold_ms':>10}{'warm_ms':>10}{'%mongo/red':>12}{'%cpu':>8}")
    print("-" * 92)
    for r in ok:
        io = f"{r['pct_io']:>10.0f}%" if r["pct_io"] is not None else f"{'—':>11}"
        cpu = f"{r['pct_cpu']:>6.0f}%" if r["pct_cpu"] is not None else f"{'—':>8}"
        print(f"{r['label']:<48}{r['cold_ms']:>10.1f}{r['warm_ms']:>10.1f}{io}{cpu}")
    print("=" * 92)

    errs = [r for r in rows if r.get("cold_ms", 0) < 0]
    if errs:
        print(f"\n{len(errs)} targets salteados (args no derivables / firma): "
              + ", ".join(r["label"].split("(")[0].strip() for r in errs[:12]))

    # Guardar HTML de los más lentos (solo si hubo profiling)
    con_html = [r for r in ok if r.get("html")]
    if con_html:
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        outdir = LOGS_DIR / f"perf_sweep_{ts}"
        outdir.mkdir(parents=True, exist_ok=True)
        for r in con_html[: args.top]:
            safe = "".join(c if c.isalnum() else "_" for c in r["label"])[:50]
            (outdir / f"{r['cold_ms']:07.0f}ms_{safe}.html").write_text(r["html"])
        print(f"\nÁrbol de llamadas de los {min(args.top, len(con_html))} más lentos → {outdir}")
        print("Abrí los .html en el browser: ancho/% = tiempo; mirá si arriba hay pymongo (I/O) o código tuyo (CPU).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
