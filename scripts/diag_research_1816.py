"""scripts/diag_research_1816.py — prueba las queries del laboratorio de spreads
DIRECTO contra la base (sin API), para aislar "¿es la query o es el deploy?".

Llama a api.services.research_1816_sql (universo / series / spread) y muestra qué
devuelve. Si acá anda pero la vista dice "Cargando universo…", el problema es el
API (falta `systemctl restart api.service`), no la query.

Uso: python -m scripts.diag_research_1816
"""
from __future__ import annotations

from api.services import research_1816_sql as mkt


def main() -> None:
    print("=== universo() ===")
    u = mkt.universo()
    print(f"total bonos con series: {u.get('total')} · curvas: {len(u.get('curvas', []))}")
    for g in u.get("curvas", [])[:20]:
        tickers = ", ".join(b["ticker"] for b in g["bonos"])
        print(f"  {g['curva']:28} ({len(g['bonos'])}): {tickers[:80]}")

    print("\n=== series(['AL30','GD30'], 'tea') — últimos puntos ===")
    s = mkt.series(["AL30", "GD30"], "tea")
    for serie in s.get("series", []):
        pts = serie.get("puntos", [])
        print(f"  {serie['ticker']}: {len(pts)} puntos · último {pts[-1] if pts else '—'}")

    print("\n=== spread('AL30','GD30','tea') ===")
    sp = mkt.spread("AL30", "GD30", "tea")
    print(f"  puntos: {len(sp.get('puntos', []))}")
    print(f"  stats: {sp.get('stats')}")
    if sp.get("puntos"):
        print(f"  último: {sp['puntos'][-1]}")

    print("\nSi ves datos acá → la query anda; reiniciá el API "
          "(systemctl restart api.service). Si acá también sale vacío → avisame.")


if __name__ == "__main__":
    main()
