"""diag_curvas_render.py — POR QUÉ un bono/ON no aparece en Renta Fija (read-only).

Audita `mercado.curvas` (el master de renta fija) y, para cada instrumento, dice
si RENDERIZA en la vista Renta Fija y, si no, POR QUÉ. La vista arma cada curva con
`listar_curva` (api/services/renta_fija_sql.py), que exige:

  1. una fila en `mercado.curvas` con
  2. `curva` ∈ {cer, tasa_fija, tamar, soberanos, dolar_linked} o familia ON (on / on_<sector>), y
  3. `fecha_vencimiento` NO nula (las filas sin vto se descartan).

Además cruza contra:
  - `portafolio.assets` (ticker == ticker_corto) → sin esto NO entra al AuM/Portfolios
    (aunque SÍ puede verse en Renta Fija).
  - `mercado.market_snapshot` → si hay precio/metrics live (si no, la fila igual
    aparece pero vacía).

100% lectura. No escribe nada.

Uso:
    python -m scripts.diag_curvas_render                 # audita todo el master
    python -m scripts.diag_curvas_render AL30            # filtra por ticker (substring, ci)
    python -m scripts.diag_curvas_render --curva cer     # solo una curva
"""
from __future__ import annotations

import sys

from psycopg.rows import dict_row

from api.services.renta_fija import _CURVAS_VALIDAS, _es_curva_on
from core.postgres import get_pool


def _curva_valida(curva: str | None) -> bool:
    if not curva:
        return False
    return curva in _CURVAS_VALIDAS or _es_curva_on(curva)


def _motivos(row: dict, en_assets: bool, en_snapshot: bool, px: float | None) -> list[str]:
    """Motivos por los que NO renderiza / advertencias. [] = OK."""
    motivos = []
    curva = row.get("curva")
    if not curva:
        motivos.append("❌ curva NULL → no cae en ninguna vista de Renta Fija")
    elif not _curva_valida(curva):
        motivos.append(f"❌ curva={curva!r} no es válida (ni ON) → ninguna vista la lista")
    if not row.get("fecha_vencimiento"):
        motivos.append("❌ fecha_vencimiento NULL → listar_curva la descarta")
    if not row.get("ticker_corto"):
        motivos.append("⚠️ ticker_corto NULL → no se puede resolver por ticker corto")
    if not en_assets:
        motivos.append("⚠️ sin fila en portafolio.assets (ticker==ticker_corto) → NO entra al AuM/Portfolios")
    if not en_snapshot:
        motivos.append("⚠️ sin fila en market_snapshot → nunca lo vio un motor (¿ticker mal? ¿no suscripto?)")
    elif not px or px <= 0:
        motivos.append("• sin last_price>0 (aparece en la curva pero sin precio/TEA)")
    return motivos


def main() -> None:
    argv = sys.argv[1:]
    curva_filtro = None
    ticker_filtro = None
    if "--curva" in argv:
        i = argv.index("--curva")
        curva_filtro = argv[i + 1] if i + 1 < len(argv) else None
    else:
        ticker_filtro = argv[0] if argv else None

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        where = ""
        params: list = []
        if curva_filtro:
            where = "WHERE curva = %s"
            params.append(curva_filtro)
        elif ticker_filtro:
            where = "WHERE ticker ILIKE %s OR ticker_corto ILIKE %s"
            params += [f"%{ticker_filtro}%", f"%{ticker_filtro}%"]
        cur.execute(
            f"SELECT ticker, ticker_corto, curva, tipo, fecha_vencimiento, fecha_emision "
            f"FROM mercado.curvas {where} ORDER BY curva, fecha_vencimiento",
            tuple(params),
        )
        curvas = cur.fetchall()

        # Sets de cruce (una query cada uno).
        cortos = [c["ticker_corto"] for c in curvas if c.get("ticker_corto")]
        tickers = [c["ticker"] for c in curvas if c.get("ticker")]

        assets_set: set[str] = set()
        if cortos:
            cur.execute("SELECT ticker FROM portafolio.assets WHERE ticker = ANY(%s)", (cortos,))
            assets_set = {r["ticker"] for r in cur.fetchall()}

        snap_px: dict[str, float | None] = {}
        if tickers:
            cur.execute(
                "SELECT ticker, last_price FROM mercado.market_snapshot WHERE ticker = ANY(%s)",
                (tickers,),
            )
            snap_px = {r["ticker"]: r["last_price"] for r in cur.fetchall()}

    if not curvas:
        print("Sin filas en mercado.curvas para ese filtro.")
        return

    # ── Resumen por curva ──
    por_curva: dict[str, int] = {}
    for c in curvas:
        por_curva[c.get("curva") or "(NULL)"] = por_curva.get(c.get("curva") or "(NULL)", 0) + 1
    print(f"\n═══ mercado.curvas — {len(curvas)} instrumentos ═══")
    print("Por curva:")
    for k in sorted(por_curva):
        marca = "" if _curva_valida(None if k == "(NULL)" else k) else "  ⟵ NO renderiza en Renta Fija"
        print(f"   {k:16} {por_curva[k]:>4}{marca}")

    # ── Detalle: solo los que tienen algún problema ──
    problemas = []
    ok = 0
    for c in curvas:
        tk = c.get("ticker") or ""
        en_assets = c.get("ticker_corto") in assets_set
        en_snap = tk in snap_px
        px = snap_px.get(tk)
        motivos = _motivos(c, en_assets, en_snap, px)
        # Solo los ❌/⚠️ (los "•" de precio no son bloqueantes) cuentan como problema.
        bloqueantes = [m for m in motivos if m.startswith(("❌", "⚠️"))]
        if bloqueantes:
            problemas.append((c, motivos))
        else:
            ok += 1

    print(f"\n═══ {ok} OK · {len(problemas)} con problemas/advertencias ═══")
    for c, motivos in problemas:
        vto = str(c.get("fecha_vencimiento"))[:10] if c.get("fecha_vencimiento") else "SIN VTO"
        print(f"\n  {c.get('ticker_corto') or '?':10} | {c.get('ticker') or '?'}")
        print(f"     curva={c.get('curva')!r}  tipo={c.get('tipo')!r}  vto={vto}")
        for m in motivos:
            print(f"     {m}")

    if not problemas:
        print("  ✅ Todos los instrumentos renderizan sin problemas.")


if __name__ == "__main__":
    main()
