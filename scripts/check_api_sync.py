"""Chequea sincronización entre colecciones fuente y sus copias API derivadas.

Cada colección `*API.*API` es un snapshot rebuilt con drop+insert_many por
`scripts.api_migrate`. Si el `sync_api_copies` encadenado en el crontab
falla o no corre, la API queda atrasada vs la fuente de verdad.

Para cada par verifica:
  - Count de docs en fuente y copia.
  - Última fecha/timestamp en cada una (según el campo más relevante).
  - Estado: OK si counts coinciden y última fecha coincide; DESFASADO si no.

Uso:
    python -m scripts.check_api_sync               # todas las pares
    python -m scripts.check_api_sync --only flujo  # sólo una
"""
from __future__ import annotations

import argparse
from typing import Any

from core.mongo import get_mongo_client

# ─────────────────────────────────────────────────────────────────────────────
# Config: pares fuente → API derivada
#
# campo_ts: campo a comparar para saber "cuán reciente". Puede ser None si
#           la colección no tiene un timestamp útil (casos manuales).
# tipo_ts:  "iso" (string YYYY-MM-DD) | "datetime" (objeto) | "str"
# ─────────────────────────────────────────────────────────────────────────────

_PARES: list[dict[str, Any]] = [
    {
        "nombre": "flujo",
        "src": ("CashFlow", "Flujo"),
        "dst": ("OperacionesAPI", "MesaAPI"),
        "campo_ts": "concertacion",
        "tipo_ts": "iso",
    },
    {
        "nombre": "movimientos",
        "src": ("CashFlow", "Movimientos"),
        "dst": ("OperacionesAPI", "FlujosAPI"),
        "campo_ts": "fecha",
        "tipo_ts": "str",  # formato ddmmyyyy legacy
    },
    {
        "nombre": "carteras",
        "src": ("Valuaciones", "Carteras"),
        "dst": ("PortfolioAPI", "CarterasAPI"),
        "campo_ts": "timestamp",
        "tipo_ts": "datetime",
    },
    {
        "nombre": "aum",
        "src": ("Valuaciones", "AuM"),
        "dst": ("PortfolioAPI", "AumAPI"),
        "campo_ts": "fecha_snapshot",
        "tipo_ts": "iso",
    },
    {
        "nombre": "assets",
        "src": ("Valuaciones", "Assets"),
        "dst": ("TitulosAPI", "AssetsAPI"),
        "campo_ts": None,
    },
    {
        "nombre": "accionistas",
        "src": ("CashFlow", "Accionistas"),
        "dst": ("CuentasAPI", "AccionistasAPI"),
        "campo_ts": None,
    },
    {
        "nombre": "contrapartes",
        "src": ("CashFlow", "Contrapartes"),
        "dst": ("CuentasAPI", "ContrapartesAPI"),
        "campo_ts": None,
    },
]


def _ultimo(coll, campo: str) -> Any:
    doc = coll.find_one({campo: {"$ne": None}}, sort=[(campo, -1)])
    return doc.get(campo) if doc else None


def _fmt_ts(v: Any) -> str:
    if v is None:
        return "—"
    return str(v)[:19]


def _check_par(client, par: dict[str, Any]) -> dict[str, Any]:
    src = client[par["src"][0]][par["src"][1]]
    dst = client[par["dst"][0]][par["dst"][1]]

    src_count = src.count_documents({})
    dst_count = dst.count_documents({})

    result: dict[str, Any] = {
        "nombre":    par["nombre"],
        "src_path":  ".".join(par["src"]),
        "dst_path":  ".".join(par["dst"]),
        "src_count": src_count,
        "dst_count": dst_count,
        "src_last":  None,
        "dst_last":  None,
    }

    if par["campo_ts"]:
        result["src_last"] = _ultimo(src, par["campo_ts"])
        result["dst_last"] = _ultimo(dst, par["campo_ts"])

    # Estado
    count_match = src_count == dst_count
    last_match = result["src_last"] == result["dst_last"]
    if count_match and last_match:
        result["estado"] = "OK"
    elif dst_count == 0:
        result["estado"] = "API_VACIA"
    elif dst_count < src_count * 0.95:
        result["estado"] = "DESFASADO"
    else:
        result["estado"] = "CASI_OK"  # pequeña diferencia

    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        help="Chequear sólo esta par (nombre: flujo|movimientos|carteras|aum|"
        "assets|accionistas|contrapartes)",
    )
    args = parser.parse_args()

    pares = _PARES
    if args.only:
        pares = [p for p in _PARES if p["nombre"] == args.only]
        if not pares:
            print(f"No existe par '{args.only}'. "
                  f"Disponibles: {[p['nombre'] for p in _PARES]}")
            return 1

    client = get_mongo_client()

    print(f"{'Nombre':<14} {'Fuente':<28} {'API':<30} "
          f"{'src#':>8} {'dst#':>8}  {'Estado':<10}")
    print("─" * 120)

    worst = "OK"
    for par in pares:
        r = _check_par(client, par)
        print(f"{r['nombre']:<14} {r['src_path']:<28} {r['dst_path']:<30} "
              f"{r['src_count']:>8} {r['dst_count']:>8}  {r['estado']:<10}")
        if par["campo_ts"]:
            print(f"    └─ últ fuente: {_fmt_ts(r['src_last'])}   "
                  f"últ API: {_fmt_ts(r['dst_last'])}")

        # Peor estado global
        order = {"OK": 0, "CASI_OK": 1, "DESFASADO": 2, "API_VACIA": 2}
        if order.get(r["estado"], 2) > order.get(worst, 0):
            worst = r["estado"]

    print("─" * 120)
    print(f"Peor estado global: {worst}")
    if worst != "OK":
        print()
        print("Resincar todo:     python -m jobs.sync_api_copies --all")
        print("Resincar una par:  python -m jobs.sync_api_copies --flujo")
    return 0 if worst == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
