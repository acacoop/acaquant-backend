"""backfill_aranceles.py — agrega `aranceles` a CashFlow.NegocioMovimientos.

NegocioMovimientos tiene todo el boleto MENOS el arancel (su fuente,
consolidadosGenerales, no lo trae). Este backfill toma las cuentas presentes
en NegocioMovimientos, pide a Aunesa `/operaciones/informes` por cuenta y
matchea por `boleto == comprobante` → `$set` del arancel en el doc del boleto.

Escribe en cada boleto:
    aranceles : {"ARS": 1102.80}     # dict por moneda (lo que trae informes)
    arancel   : 1102.80              # atajo: monto ARS (0.0 si no hay)

Optimizado para correr 2 años:
  - Llamadas a Aunesa en paralelo (ThreadPoolExecutor, --workers).
  - 1 sola query de NM por cuenta (comprobante→_id en memoria), no 1 por boleto.
  - Escritura en lotes (flush cada 2000) → el progreso parcial persiste.
  - Reintento automático de las cuentas que fallan (timeouts transitorios).
  - Registro de errores a logs/backfill_aranceles_errores_<ts>.json (pase lo que pase).

Idempotente (re-corrible). DRY-RUN por default.

Uso (en el Droplet, desde la raíz):
    python -m scripts.backfill_aranceles --cuenta 1346 --dry-run
    python -m scripts.backfill_aranceles --desde 2023-01-01 --hasta 2024-12-31 --apply
    python -m scripts.backfill_aranceles --cuenta 101 --cuenta 106 --apply   # reintentar puntuales
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

from pymongo import UpdateOne

from api.services.aunesa_informes import aranceles_por_boleto
from core.mongo import get_mongo_client

DB = "CashFlow"
COL = "NegocioMovimientos"
# Margen para la ventana de LIQUIDACIÓN: un boleto concertado en `hasta` liquida
# días después (T+1/T+2/...) → ampliamos el techo para no perder esos boletos.
_MARGEN_LIQ_DIAS = 15
_FLUSH = 2000
LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


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
    liq_desde = _ddmmyyyy(desde)
    liq_hasta = _ddmmyyyy(hasta + timedelta(days=_MARGEN_LIQ_DIAS))

    col = get_mongo_client()[DB][COL]
    if args.cuenta:
        cuentas = [str(c) for c in args.cuenta]
    else:
        cuentas = sorted(
            str(c) for c in col.distinct(
                "id_cuenta", {"fecha": {"$gte": desde.isoformat(), "$lte": hasta.isoformat()}},
            ) if c
        )
    if args.limit:
        cuentas = cuentas[: args.limit]

    print(f"Rango concertación {desde}..{hasta} | liquidación {liq_desde}..{liq_hasta}")
    print(f"Cuentas: {len(cuentas)} | workers: {args.workers} | "
          f"modo: {'APPLY' if args.apply else 'DRY-RUN'}\n", flush=True)

    stats = {"inf": 0, "match": 0, "sin_match": 0, "escritos": 0}
    pendientes: list[UpdateOne] = []
    ejemplos: list[str] = []

    def _flush(force: bool = False) -> None:
        if pendientes and (force or len(pendientes) >= _FLUSH):
            if args.apply:
                res = col.bulk_write(pendientes, ordered=False)
                stats["escritos"] += res.modified_count
            pendientes.clear()

    def _fetch_one(cuenta: str):
        try:
            return cuenta, aranceles_por_boleto(cuenta, liq_desde, liq_hasta), None
        except Exception as e:
            return cuenta, None, str(e)

    def _procesar_db(cuenta: str, mapa: dict) -> None:
        if not mapa:
            return
        stats["inf"] += len(mapa)
        # 1 sola query: comprobante → _id de los boletos de esta cuenta.
        # Excluye futuros DLR (unidad="USDL"): no tienen arancel del proyecto —
        # si no se filtran, /informes nunca los devuelve y el boleto queda como
        # "sin match" falso (inflaba el contador sin_match).
        from api.services._negocio_futuros import match_no_futuros
        nm_map = {
            d["comprobante"]: d["_id"]
            for d in col.find(
                {"id_cuenta": cuenta, **match_no_futuros()},
                {"_id": 1, "comprobante": 1},
            )
        }
        for boleto, aranceles in mapa.items():
            _id = nm_map.get(boleto)
            if _id is None:
                stats["sin_match"] += 1
                continue
            stats["match"] += 1
            pendientes.append(UpdateOne(
                {"_id": _id},
                {"$set": {"aranceles": aranceles, "arancel": round(aranceles.get("ARS", 0.0), 2)}},
            ))
            if len(ejemplos) < 5:
                ejemplos.append(f"{boleto} (cuenta {cuenta}) → {aranceles}")
        _flush()

    def _pass(cs: list[str], workers: int, etiqueta: str) -> list[dict]:
        errs: list[dict] = []
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_fetch_one, c): c for c in cs}
            for fut in as_completed(futs):
                cuenta, mapa, err = fut.result()
                done += 1
                if err is not None:
                    errs.append({"cuenta": cuenta, "error": err})
                else:
                    _procesar_db(cuenta, mapa)
                if done % 50 == 0 or done == len(cs):
                    print(f"   [{etiqueta}] {done}/{len(cs)} | match={stats['match']} "
                          f"sin_match={stats['sin_match']} err={len(errs)}", flush=True)
        return errs

    errores: list[dict] = []
    try:
        errores = _pass(cuentas, args.workers, "pass1")
        if errores:
            reintentar = [e["cuenta"] for e in errores]
            print(f"\nReintentando {len(reintentar)} cuentas que fallaron…", flush=True)
            errores = _pass(reintentar, max(2, args.workers // 3), "retry")
        _flush(force=True)
    finally:
        LOGS_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        rep = LOGS_DIR / f"backfill_aranceles_errores_{ts}.json"
        rep.write_text(json.dumps({
            "generado": ts,
            "rango": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
            "modo": "apply" if args.apply else "dry-run",
            "cuentas_total": len(cuentas),
            "stats": stats,
            "n_errores": len(errores),
            "errores": errores,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n📝 Registro: {rep}", flush=True)

    print(f"\nResumen: informes={stats['inf']} | match={stats['match']} | "
          f"sin_match={stats['sin_match']} | escritos={stats['escritos']} | errores={len(errores)}")
    if errores:
        print("Cuentas con error (tras reintento):", ", ".join(e["cuenta"] for e in errores))
    if not args.apply:
        print("\n[DRY-RUN] no se escribió. Ejemplos de lo que escribiría:")
        for ej in ejemplos:
            print(f"   {ej}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
