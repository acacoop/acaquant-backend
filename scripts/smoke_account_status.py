"""Smoke read-only de los 3 endpoints de cuenta en pyRofex.

Trae todo el report y compara los bloques candidatos a "saldo disponible":
  - availableToOperate.cash.detailedCash  (lo que se puede operar AHORA)
  - currentSituation.cash.detailedCash    (efectivo disponible — lo que muestra Primary)
  - pendingMovementsCash.cash.detailedCash (movimientos del día — col "Movimientos" en Primary)

Por settle (CI=0, 24hs=2). Imprime un resumen comparativo en consola y deja
el JSON crudo de los 3 endpoints en `out_account_dump.json` para inspección.

Uso:
    ROFEX_ORDERS_ENV=live python -m scripts.smoke_account_status
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyRofex
from dotenv import load_dotenv

load_dotenv()

from core.rofex_orders_session import (  # noqa: E402
    _env_kind,
    cuenta_default,
    inicializar_para_envio,
)

OUT_FILE = Path("out_account_dump.json")

SETTLE_LABELS = {"0": "CI", "1": "48hs", "2": "24hs"}


def _ms_to_human(ms: int | None) -> str:
    if ms is None:
        return "—"
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return str(ms)


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:>18,.2f}"
    return str(v)


def _resumen_settle(settle_key: str, settle_data: dict) -> None:
    label = SETTLE_LABELS.get(settle_key, f"settle_{settle_key}")
    settle_date = _ms_to_human(settle_data.get("settlementDate"))
    print(f"\n  ─── {label} (settle {settle_key}) · {settle_date} ───")

    bloques = [
        ("availableToOperate.cash", settle_data.get("availableToOperate") or {}),
        ("currentSituation.cash",   settle_data.get("currentSituation") or {}),
        ("pendingMovementsCash",    settle_data.get("pendingMovementsCash") or {}),
    ]

    monedas: set[str] = set()
    valores: dict[str, dict[str, Any]] = {}
    totales: dict[str, Any] = {}
    for nombre, blk in bloques:
        cash = (blk.get("cash") or {}) if "cash" in blk else blk
        detailed = cash.get("detailedCash") or {}
        valores[nombre] = detailed
        totales[nombre] = cash.get("totalCash")
        monedas.update(detailed.keys())

    if not monedas:
        print("    (vacío en los 3 bloques)")
        return

    headers = ["moneda"] + [n for n, _ in bloques]
    print("    " + " | ".join(f"{h:<25}" for h in headers))
    print("    " + "-+-".join("-" * 25 for _ in headers))
    for m in sorted(monedas):
        row = [f"{m:<25}"] + [f"{_fmt(valores[n].get(m)):<25}" for n, _ in bloques]
        print("    " + " | ".join(row))
    print("    " + "-+-".join("-" * 25 for _ in headers))
    print("    " + " | ".join(
        [f"{'totalCash':<25}"] + [f"{_fmt(totales[n]):<25}" for n, _ in bloques]
    ))


def _resumen_report(report: dict) -> None:
    if not report or report.get("status") != "OK":
        print(f"\n  ⚠ get_account_report status no-OK: {report}")
        return

    account_data = report.get("accountData") or {}
    last_calc = _ms_to_human(account_data.get("lastCalculation"))
    detailed = account_data.get("detailedAccountReports") or {}

    print(f"\n  lastCalculation: {last_calc}")
    print(f"  settlement keys: {sorted(detailed.keys())}")

    for settle_key in sorted(detailed.keys()):
        _resumen_settle(settle_key, detailed[settle_key] or {})


def main() -> int:
    print(f"\nROFEX_ORDERS_ENV: {_env_kind()}")
    inicializar_para_envio()
    account = cuenta_default()
    print(f"Cuenta: {account}")

    dump: dict[str, Any] = {"account": account, "env": _env_kind()}
    for label, fn in [
        ("get_account_report",   lambda: pyRofex.get_account_report(account=account)),
        ("get_account_position", lambda: pyRofex.get_account_position(account=account)),
        ("get_detailed_position", lambda: pyRofex.get_detailed_position(account=account)),
    ]:
        try:
            resp = fn()
            dump[label] = resp
        except Exception as e:
            dump[label] = {"exception": str(e)}

    OUT_FILE.write_text(json.dumps(dump, indent=2, default=str, ensure_ascii=False))
    print(f"\nDump completo escrito en: {OUT_FILE.resolve()}")

    print("\n" + "═" * 80)
    print("  RESUMEN: detailedCash por settle / por bloque  (account_report)")
    print("═" * 80)
    _resumen_report(dump.get("get_account_report") or {})

    pos = (dump.get("get_account_position") or {}).get("positions") or []
    print("\n" + "═" * 80)
    print(f"  account_position — {len(pos)} posiciones")
    print("═" * 80)
    for p in pos[:30]:
        sym = p.get("instrument", {}).get("symbol") or p.get("symbol") or "?"
        print(f"    {sym:<45}  buyQty={_fmt(p.get('buySize'))}  buyPx={_fmt(p.get('buyPrice'))}  "
              f"sellQty={_fmt(p.get('sellSize'))}  sellPx={_fmt(p.get('sellPrice'))}")
    if len(pos) > 30:
        print(f"    ... y {len(pos) - 30} más (ver {OUT_FILE.name})")

    det = (dump.get("get_detailed_position") or {}).get("positions") or []
    print("\n" + "═" * 80)
    print(f"  detailed_position — {len(det)} posiciones")
    print("═" * 80)
    for p in det[:30]:
        sym = p.get("symbol") or p.get("instrument", {}).get("symbol") or "?"
        qty = p.get("currentQty") or p.get("totalDailyDiff")
        avg = p.get("averagePrice") or p.get("totalCost")
        print(f"    {sym:<45}  qty={_fmt(qty)}  avgPx={_fmt(avg)}")
    if len(det) > 30:
        print(f"    ... y {len(det) - 30} más (ver {OUT_FILE.name})")

    print("\n→ Comparar la columna que coincide con Primary y avisame cuál es.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
