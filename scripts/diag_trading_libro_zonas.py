"""Diag READ-ONLY de los dos problemas reportados en la vista TRADING.

  1) LIBRO desactualizado — `GET /api/operar/order-book` devuelve la fila del
     snapshot si TIENE PUNTAS, sin mirar hace cuánto se refrescó (el `or` de
     `_tiene_puntas(book) or fresca`). Con el motor caído o el papel sin
     suscripción viva, la pantalla pinta puntas de hace horas SIN avisar. Este
     diag muestra la EDAD real de la fila para confirmarlo.

  2) ZONAS ADR vacío — el chart pide `/api/trading/adr-zonas` y, si la respuesta
     no viene OK, se queda en blanco sin mensaje (`if (!r.ok) return`). Acá se
     mira si el ADR tiene velas diarias en `mercado.precios_acciones`, que es de
     donde salen.

Uso:
    python -m scripts.diag_trading_libro_zonas            # RKLB (el reportado)
    python -m scripts.diag_trading_libro_zonas GGAL NVDA  # los que quieras
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

from api.services._sql import _q

DEFAULT = ["RKLB"]


def _edad(iso) -> str:
    if not iso:
        return "nunca"
    try:
        ts = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        seg = (datetime.now(UTC) - ts).total_seconds()
    except Exception:
        return f"?? ({iso})"
    if seg < 90:
        return f"{seg:.0f}s  ← FRESCA"
    if seg < 3600:
        return f"{seg / 60:.0f} min  ⚠ RANCIA"
    if seg < 86400:
        return f"{seg / 3600:.1f} horas  ⚠⚠ MUY RANCIA"
    return f"{seg / 86400:.1f} días  ⚠⚠⚠ ABANDONADA"


def libro(tk: str) -> None:
    print(f"\n── 1) LIBRO · {tk} ──────────────────────────────────────────")
    # Columnas REALES de mercado.market_snapshot (ver sql/schema.sql): el book es una
    # columna jsonb propia, no un `data` genérico, y el último precio es `last_price`.
    rows = _q(
        "SELECT ticker, updated_at, last_price, "
        "       jsonb_array_length(COALESCE(book->'bids','[]'::jsonb))   AS n_bids, "
        "       jsonb_array_length(COALESCE(book->'offers','[]'::jsonb)) AS n_offers, "
        "       book->'bids'->0->>'price'   AS mejor_bid, "
        "       book->'offers'->0->>'price' AS mejor_offer "
        "FROM mercado.market_snapshot WHERE ticker ILIKE %(p)s ORDER BY ticker",
        {"p": f"%{tk}%"},
    )
    if not rows:
        print(f"   ✗ NO hay ninguna fila en mercado.market_snapshot que matchee '{tk}'")
        print("     → el endpoint cae al camino de suscripción (202) y el libro no se llena.")
        return
    for r in rows:
        print(f"\n   {r['ticker']}")
        print(f"     refrescada hace : {_edad(r['updated_at'])}")
        print(f"     puntas          : {r['n_bids']} bids / {r['n_offers']} offers")
        print(f"     mejor bid/offer : {r['mejor_bid']} / {r['mejor_offer']}")
        print(f"     last_price      : {r['last_price']}")
        if (r["n_bids"] or r["n_offers"]) and r["updated_at"]:
            print("     NOTA: si la fila es RANCIA y aun asi tiene puntas, el endpoint la")
            print("           devuelve igual y la pantalla las muestra como si fueran de ahora.")


def zonas(tk: str) -> None:
    print(f"\n── 2) ZONAS ADR · {tk} ──────────────────────────────────────")
    rows = _q(
        "SELECT COUNT(*) AS n, MIN(fecha) AS desde, MAX(fecha) AS hasta "
        "FROM mercado.precios_acciones WHERE ticker = %(t)s",
        {"t": tk},
    )
    n = rows[0]["n"] if rows else 0
    if not n:
        print(f"   ✗ {tk} NO tiene velas diarias en mercado.precios_acciones → el chart")
        print("     no tiene nada que dibujar. Por eso sale el marco vacío, sin error.")
        # ¿Está el papel en el universo? Si no figura, el job nunca le va a bajar velas.
        # OJO: en mercado.cedears el símbolo US es `underlying` (`ticker` es el de BYMA).
        for tabla, col in (("mercado.cedears", "underlying"), ("mercado.adr_snapshot", "ticker")):
            try:
                hit = _q(f"SELECT {col} FROM {tabla} WHERE {col} ILIKE %(t)s LIMIT 3",
                         {"t": f"%{tk}%"})
                print(f"     {tabla}.{col}: {[h[col] for h in hit] or 'NO figura'}")
            except Exception as e:
                print(f"     {tabla}: no pude consultar ({type(e).__name__})")
    else:
        print(f"   ✓ {n} velas diarias · {rows[0]['desde']} → {rows[0]['hasta']}")
        print("     Si el chart igual sale vacío, el problema NO son los datos:")
        print("     está en el endpoint /api/trading/adr-zonas o en el render.")


def universo() -> None:
    """Cuántos ADR tienen velas: distingue 'RKLB puntual' de 'el job no corre'."""
    print("\n── CONTEXTO: ¿es solo este papel o es el feed? ───────────────")
    try:
        r = _q("SELECT COUNT(DISTINCT ticker) AS tickers, MAX(fecha) AS ultima "
               "FROM mercado.precios_acciones")
        print(f"   mercado.precios_acciones: {r[0]['tickers']} tickers · "
              f"última vela {r[0]['ultima']}")
        print("   (si la última vela es de hace días, el que falla es jobs.precios_acciones_daily)")
    except Exception as e:
        print(f"   no pude leer el universo: {type(e).__name__}: {e}")


def endpoint_zonas(tk: str) -> None:
    """Llama al service REAL del chart y muestra qué devuelve (o con qué revienta).

    Es el paso que faltaba: ya sabemos que las velas existen, así que si la pantalla
    sigue vacía el problema está acá. Si `get_pivot_points` tira una excepción, el
    endpoint responde 500 y el front hace `if (!r.ok) return` — marco en blanco, sin
    una sola pista de qué pasó.
    """
    print(f"\n── 3) ENDPOINT /api/trading/adr-zonas · {tk} ────────────────")
    try:
        from api.services import trading as tsvc
    except Exception as e:
        print(f"   no pude importar el service: {type(e).__name__}: {e}")
        return
    try:
        # `dias=400` es exactamente lo que manda el chart.
        out = tsvc.get_adr_zonas(ticker=tk, dias=400)
    except Exception as e:
        import traceback
        print(f"   ✗ REVENTÓ: {type(e).__name__}: {e}")
        print("     → el endpoint devuelve 500 y el chart se queda en blanco sin avisar.")
        traceback.print_exc()
        return
    velas = out.get("velas") or []
    frames = out.get("frames") or {}
    print(f"   sin_datos   : {out.get('sin_datos', False)}")
    print(f"   underlying  : {out.get('underlying')}")
    print(f"   last        : {out.get('last')}  (fuente: {out.get('last_source')})")
    print(f"   velas       : {len(velas)}" + (f"  ({velas[0]['t']} → {velas[-1]['t']})"
                                              if velas else "   ⚠ VACÍO"))
    print(f"   frames      : {list(frames) or '⚠ VACÍO'}")
    for nombre, fr in frames.items():
        lv = (fr or {}).get("levels") or {}
        print(f"     - {nombre}: {len(lv)} niveles {list(lv)[:4]}")
    if velas and frames:
        print("   ✓ el backend devuelve todo — si la pantalla sigue vacía, es el RENDER")
        print("     del chart en el front, no los datos.")


def libros_fantasma() -> None:
    """¿RKLB era un caso aislado o hay más libros viejos servidos como vigentes?"""
    print("\n── EXTRA: otras filas con puntas pero ABANDONADAS ───────────")
    try:
        rows = _q(
            "SELECT ticker, updated_at, "
            "       EXTRACT(EPOCH FROM (now() - updated_at))/86400 AS dias "
            "FROM mercado.market_snapshot "
            "WHERE (jsonb_array_length(COALESCE(book->'bids','[]'::jsonb)) > 0 "
            "    OR jsonb_array_length(COALESCE(book->'offers','[]'::jsonb)) > 0) "
            "  AND updated_at < now() - interval '1 day' "
            "ORDER BY updated_at LIMIT 25")
    except Exception as e:
        print(f"   no pude consultar: {type(e).__name__}: {e}")
        return
    if not rows:
        print("   ✓ ninguna: no hay libros viejos con puntas dando vueltas.")
        return
    print(f"   ⚠ {len(rows)} filas (tope 25) con puntas y más de 1 día sin refrescar:")
    for r in rows:
        print(f"     {r['dias']:>6.1f} días  {r['ticker']}")


def main() -> int:
    tickers = [t.upper() for t in sys.argv[1:]] or DEFAULT
    print(f"\n{'=' * 70}\nDIAG TRADING — libro desactualizado + ZONAS ADR vacío\n{'=' * 70}")
    universo()
    libros_fantasma()
    for tk in tickers:
        libro(tk)
        zonas(tk)
        endpoint_zonas(tk)
    print("\nQué mirar: (1) la EDAD de la fila del libro — si es rancia y tiene puntas,")
    print("confirmado el bug del `or`. (2) si el ADR tiene 0 velas, el chart no puede")
    print("dibujar nada y el front debería DECIRLO en vez de quedarse en blanco.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
