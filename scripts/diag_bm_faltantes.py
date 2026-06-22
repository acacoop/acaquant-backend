"""diag_bm_faltantes.py — qué son y si están EN USO los assets de BondsMaster que no están en Curvas.

Read-only. Para decidir migrar vs descartar (regla: lo fuera de uso se borra, no se migra).
Para cada BM-asset sin doc on_* en Curvas: dump del doc BM (tipo/sector/moneda/vto/#flujos/
tickers) + si está en la ÚLTIMA tenencia AuM (SQL portafolio.tenencia, aum='si').

    python -m scripts.diag_bm_faltantes
"""
from __future__ import annotations

import re

from core.mongo import get_mongo_client_read
from core.postgres import get_pool

_RE_CODIGO = re.compile(r"^\s*(?:\[\d+\]\s*)?([A-Za-z0-9]+)")


def _codigo(unidad: str | None) -> str:
    m = _RE_CODIGO.match(unidad or "")
    return m.group(1).upper() if m else (unidad or "")


def main() -> int:
    trd = get_mongo_client_read()["Trading"]
    bm_assets = {b.get("asset") for b in trd["BondsMaster"].find({}, {"_id": 0, "asset": 1})
                 if b.get("asset")}
    curva_tk = {c.get("ticker_corto") for c in trd["Curvas"].find(
        {"curva": {"$regex": "^on"}}, {"_id": 0, "ticker_corto": 1})}
    faltan = sorted(bm_assets - curva_tk)

    # ¿Qué tiene la última tenencia AuM?
    held: set[str] = set()
    with get_pool().connection() as cn, cn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        fecha = cur.fetchone()[0]
        if fecha:
            cur.execute("SELECT DISTINCT unidad FROM portafolio.tenencia "
                        "WHERE aum = 'si' AND fecha = %s", (fecha,))
            held = {_codigo(r[0]) for r in cur.fetchall()}
    print(f"Última tenencia AuM: {fecha} · {len(held)} unidades\n")

    print(f"═══ {len(faltan)} en BondsMaster pero NO en Curvas ═══\n")
    for a in faltan:
        d = trd["BondsMaster"].find_one({"asset": a}, {"_id": 0}) or {}
        flujos = d.get("flujos") or []
        en_aum = "✅ EN AuM" if _codigo(a) in held else "—"
        print(f"  {a:<8} {en_aum:<10} tipo={d.get('tipo') or d.get('tipo_tasa') or '?':<10} "
              f"sector={d.get('sector') or '?':<10} moneda={d.get('moneda_flujo') or '?':<5} "
              f"vto={d.get('vencimiento') or '?':<12} #flujos={len(flujos)} "
              f"tickers={d.get('tickers') or {}}")
    print("\n✅ = lo tienen los clientes hoy → MIGRAR a Curvas.  — = sin tenencia → candidato a DESCARTAR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
