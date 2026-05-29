"""backfill_aranceles.py — CLI manual del backfill de aranceles.

La lógica core vive en `api/services/aunesa_aranceles.py` (compartida con el
endpoint del Manager `POST /api/manager/aunesa/boletos/backfill`). Este
archivo es solo el thin wrapper CLI que ofrece --dry-run / --apply, logs en
disco y prints de progreso para correr desde el Droplet.

Uso:
    python -m scripts.backfill_aranceles --cuenta 1346
    python -m scripts.backfill_aranceles --desde 2023-01-01 --hasta 2024-12-31 --apply
    python -m scripts.backfill_aranceles --cuenta 101 --cuenta 106 --apply
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from api.services.aunesa_aranceles import run_backfill

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill de aranceles en NegocioMovimientos.")
    ap.add_argument("--cuenta", action="append", default=None,
                    help="restringir a esta(s) cuenta(s) (id). Repetible. Default: todas las de NM.")
    ap.add_argument("--desde", default=None, help="concertación desde YYYY-MM-DD (default -120d).")
    ap.add_argument("--hasta", default=None, help="concertación hasta YYYY-MM-DD (default hoy).")
    ap.add_argument("--limit", type=int, default=0, help="máximo de cuentas a procesar (0 = todas).")
    ap.add_argument("--workers", type=int, default=6, help="llamadas a Aunesa en paralelo (default 6).")
    ap.add_argument("--apply", action="store_true", help="escribe. Sin esto, DRY-RUN.")
    args = ap.parse_args()

    hasta = date.fromisoformat(args.hasta) if args.hasta else date.today()
    desde = date.fromisoformat(args.desde) if args.desde else (hasta - timedelta(days=120))

    cuentas: list[str] | None
    if args.cuenta:
        cuentas = [str(c) for c in args.cuenta]
    else:
        from api.services.aunesa_aranceles import resolver_cuentas
        cuentas = resolver_cuentas(desde, hasta)
    if args.limit:
        cuentas = cuentas[: args.limit]

    print(f"Rango concertación {desde}..{hasta} | cuentas: {len(cuentas)} | "
          f"workers: {args.workers} | modo: {'APPLY' if args.apply else 'DRY-RUN'}\n",
          flush=True)

    last_print = [0]

    def on_progress(state: dict) -> None:
        # Print cada 50 cuentas para no inundar journalctl.
        done = state["cuentas_done"]
        if done - last_print[0] >= 50 or done == state["cuentas_total"]:
            last_print[0] = done
            print(f"   {done}/{state['cuentas_total']} | match={state['match']} "
                  f"sin_match={state['sin_match']} err={len(state['errores'])}",
                  flush=True)

    state = run_backfill(
        desde=desde, hasta=hasta, cuentas=cuentas,
        workers=args.workers, apply=args.apply,
        on_progress=on_progress, progress_every=1,
    )

    LOGS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rep = LOGS_DIR / f"backfill_aranceles_errores_{ts}.json"
    rep.write_text(json.dumps({
        "generado":      ts,
        "rango":         {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
        "modo":          "apply" if args.apply else "dry-run",
        "cuentas_total": state["cuentas_total"],
        "stats":         {k: state[k] for k in ("inf", "match", "sin_match", "escritos")},
        "n_errores":     len(state["errores"]),
        "errores":       state["errores"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n📝 Registro: {rep}", flush=True)

    print(f"\nResumen: informes={state['inf']} | match={state['match']} | "
          f"sin_match={state['sin_match']} | escritos={state['escritos']} | "
          f"errores={len(state['errores'])}")
    if state["errores"]:
        print("Cuentas con error (tras reintento):",
              ", ".join(e["cuenta"] for e in state["errores"]))
    if not args.apply:
        print("\n[DRY-RUN] no se escribió. Ejemplos de lo que escribiría:")
        for ej in state["ejemplos"]:
            print(f"   {ej}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
