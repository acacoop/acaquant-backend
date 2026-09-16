"""scripts/diag_rofex_crudo.py — órdenes/ejecuciones de ROFEX TAL CUAL las manda el broker.

Herramienta: diag · one-shot · READ-ONLY total (solo GETs; jamás `send_order`
ni `cancel_order`). Sin WS, sin reintentos, sin cron.

EL PUNTO
========

Todo lo que el sistema sabe de una orden pasa antes por un normalizador nuestro
(`_upsert_live_from_er` en `engines/motor_ordenes.py`), que renombra, castea y
TIRA lo que no mapea. Este script hace lo contrario: vuelca el `orderReport`
entero, sin tocar una sola clave, para poder mirar con los propios ojos qué
campos manda ROFEX de verdad — incluidos los que hoy estamos descartando sin
saberlo.

TRES COSAS QUE HAY QUE SABER (y que el script ya respeta)
=========================================================

1. **No existe un "traeme todo".** `get_all_orders_status` pega a
   `rest/order/all?accountId=X` y pide UNA cuenta. La cuenta master NO ve las
   órdenes de las comitentes (bug histórico documentado en
   `engines/motor_ordenes.py::_recovery`) → hay que iterar cuenta por cuenta.

2. **Las "operaciones" no son un endpoint aparte.** Son los MISMOS orderReport:
   los que traen `lastQty > 0` (cada fill parcial dispara uno). `get_trade_history`
   NO son nuestros trades — es el tape público del mercado por instrumento, no
   sirve para esto.

3. **El `id_cuenta` de `clientes.cuentas` no es el número que ROFEX acepta.**
   Una cuenta real es "004" y otra es "6", sin regla de formateo que cubra a las
   dos. La traducción la hace `core.rofex_orders_session.resolver_cuenta_rofex()`
   (prueba contra el broker y cachea) — acá se usa esa, no se reinventa.

QUÉ DEJA
========

  · un JSONL con UNA LÍNEA POR REPORT:
    `{"cuenta_pedida", "cuenta_rofex", "raw": <el orderReport entero>}`
  · por consola: reports por cuenta, cuántos son ejecuciones (`lastQty > 0`) y
    el INVENTARIO DE CLAVES — todas las que aparecieron, con un valor de
    ejemplo de cada una (las anidadas con notación `a.b`).

Si una cuenta falla, se loguea y sigue con la siguiente.

Uso (desde la raíz del repo):
    python -m scripts.diag_rofex_crudo
    python -m scripts.diag_rofex_crudo --cuentas 100,255,805
    python -m scripts.diag_rofex_crudo --limite 20 --salida /tmp/rofex_crudo.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from typing import Any

import pyRofex

from core.postgres import get_job_pool
from core.rofex_orders_session import ensure_session_envio, resolver_cuenta_rofex

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MAX_EJEMPLO = 120  # recorte del valor de ejemplo en el inventario de claves


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _cuentas_sql() -> list[tuple[str, str]]:
    """(id_cuenta, denominacion) de clientes.cuentas. Un SELECT, nada más."""
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id_cuenta, COALESCE(denominacion, '') "
            "FROM clientes.cuentas ORDER BY id_cuenta"
        )
        return [(str(r[0]), r[1]) for r in cur.fetchall()]


def _es_ejecucion(rep: dict[str, Any]) -> bool:
    """`lastQty > 0` = este report es un fill (total o parcial). Sin castear el
    dato de origen: se lee una copia para contar, el JSONL guarda el crudo."""
    try:
        return float(rep.get("lastQty") or 0) > 0
    except (TypeError, ValueError):
        return False


def _aplanar(obj: Any, prefijo: str = "") -> list[tuple[str, Any]]:
    """Claves con notación `a.b` para el inventario. Solo desciende por dicts:
    una lista se reporta como el valor que es (interesa saber que está)."""
    salida: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            ruta = f"{prefijo}{k}"
            if isinstance(v, dict) and v:
                salida.extend(_aplanar(v, f"{ruta}."))
            else:
                salida.append((ruta, v))
    return salida


def _ejemplo(valor: Any) -> str:
    try:
        txt = json.dumps(valor, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        txt = str(valor)
    return txt if len(txt) <= MAX_EJEMPLO else txt[:MAX_EJEMPLO] + "…"


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Vuelca los orderReport de ROFEX crudos, cuenta por cuenta (READ-ONLY).",
    )
    ap.add_argument("--cuentas", help="lista separada por comas; default: todas las de SQL")
    ap.add_argument("--limite", type=int, help="procesar solo las primeras N cuentas")
    ap.add_argument("--salida", help="ruta del JSONL (default: diag_rofex_crudo_<ts>.jsonl)")
    ap.add_argument("--pausa", type=float, default=0.2,
                    help="segundos entre cuentas, para no martillar al broker (default 0.2)")
    args = ap.parse_args()

    entorno = os.getenv("ROFEX_ORDERS_ENV", "remarket")
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    salida = args.salida or f"diag_rofex_crudo_{ts}.jsonl"

    if args.cuentas:
        cuentas = [(c.strip(), "") for c in args.cuentas.split(",") if c.strip()]
    else:
        cuentas = _cuentas_sql()
    if args.limite:
        cuentas = cuentas[: args.limite]

    print(f"Entorno pyRofex (ROFEX_ORDERS_ENV) = {entorno!r}")
    print(f"Cuentas a consultar: {len(cuentas)}")
    print(f"Salida JSONL: {salida}\n")
    if not cuentas:
        print("Nada que consultar.")
        return

    try:
        ensure_session_envio()
    except Exception as e:
        print(f"✗ No se pudo inicializar la sesión pyRofex: {e}")
        raise SystemExit(2) from None

    por_cuenta: list[dict[str, Any]] = []
    claves: dict[str, Any] = {}   # clave → primer valor no-nulo visto (ejemplo)
    claves_vistas: set[str] = set()
    total_reports = 0
    total_fills = 0

    with open(salida, "w", encoding="utf-8") as fh:
        for i, (id_cuenta, denom) in enumerate(cuentas, 1):
            etiqueta = f"[{i}/{len(cuentas)}] {id_cuenta}" + (f" {denom}" if denom else "")
            try:
                cuenta_rofex = resolver_cuenta_rofex(id_cuenta)
            except Exception as e:
                print(f"{etiqueta}: ✗ resolver_cuenta_rofex falló: {e}")
                por_cuenta.append({"cuenta": id_cuenta, "rofex": "?", "error": str(e)})
                continue

            try:
                resp = pyRofex.get_all_orders_status(account=cuenta_rofex)
            except Exception as e:
                print(f"{etiqueta} → ROFEX {cuenta_rofex!r}: ✗ get_all_orders_status falló: {e}")
                por_cuenta.append({"cuenta": id_cuenta, "rofex": cuenta_rofex, "error": str(e)})
                continue

            if not isinstance(resp, dict) or resp.get("status") != "OK":
                print(f"{etiqueta} → ROFEX {cuenta_rofex!r}: respuesta no-OK: {resp}")
                por_cuenta.append({"cuenta": id_cuenta, "rofex": cuenta_rofex,
                                   "error": f"no-OK: {resp}"})
                continue

            # El broker envuelve cada orden en {"orderReport": {...}}; a veces
            # (según versión) manda el report pelado. Se acepta cualquiera de
            # las dos y se vuelca el report ENTERO, sin tocar sus claves.
            ordenes = resp.get("orders") or []
            n_rep = n_fill = 0
            for o in ordenes:
                rep = o.get("orderReport", o) if isinstance(o, dict) else o
                fh.write(json.dumps(
                    {"cuenta_pedida": id_cuenta, "cuenta_rofex": cuenta_rofex, "raw": rep},
                    ensure_ascii=False, default=str,
                ) + "\n")
                n_rep += 1
                if isinstance(rep, dict):
                    if _es_ejecucion(rep):
                        n_fill += 1
                    for k, v in _aplanar(rep):
                        claves_vistas.add(k)
                        if v is not None and v != "" and k not in claves:
                            claves[k] = v

            total_reports += n_rep
            total_fills += n_fill
            por_cuenta.append({"cuenta": id_cuenta, "rofex": cuenta_rofex,
                               "reports": n_rep, "fills": n_fill})
            print(f"{etiqueta} → ROFEX {cuenta_rofex!r}: {n_rep} report(s), {n_fill} con lastQty>0")

            if args.pausa:
                time.sleep(args.pausa)

    # ── Resumen ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("REPORTS POR CUENTA")
    print("=" * 78)
    print(f"{'cuenta':>10} {'rofex':>10} {'reports':>9} {'lastQty>0':>10}  estado")
    for f in por_cuenta:
        if "error" in f:
            print(f"{f['cuenta']:>10} {f['rofex']:>10} {'-':>9} {'-':>10}  ERROR: {f['error'][:60]}")
        else:
            print(f"{f['cuenta']:>10} {f['rofex']:>10} {f['reports']:>9} {f['fills']:>10}  ok")
    errores = sum(1 for f in por_cuenta if "error" in f)
    print(f"\nTOTAL: {total_reports} report(s), {total_fills} ejecución(es) "
          f"(lastQty>0), {errores} cuenta(s) con error.")

    print("\n" + "=" * 78)
    print(f"CLAVES DISTINTAS EN LOS REPORTS ({len(claves_vistas)}) — con valor de ejemplo")
    print("=" * 78)
    if not claves_vistas:
        print("(ningún report: no hay claves que mostrar)")
    for k in sorted(claves_vistas):
        if k in claves:
            print(f"  {k:<34} = {_ejemplo(claves[k])}")
        else:
            print(f"  {k:<34} = (siempre null/vacío)")

    print(f"\nCrudo completo en: {salida}")


if __name__ == "__main__":
    main()
