"""scripts/fci_admin.py — la puerta de escritura manual del mercado FCI.

Herramienta: fci · Gerentes (alias/seguida), estante de un fondo, alta de un bilateral y VCP manual

Doc madre: `docs/FCI.md`. Hasta que exista la pantalla en Manager, todo lo que la
mesa decide sobre el universo FCI pasa por acá. Nada de esto lo pisan los jobs.

Uso:
    python -m scripts.fci_admin gerentes                              # lista con cuántos fondos tiene cada una
    python -m scripts.fci_admin gerente TORONTO --alias "Toronto Trust" --alias BACS
    python -m scripts.fci_admin gerente PELLEGRINI --dejar             # sale del universo (sus fondos también)
    python -m scripts.fci_admin gerente PELLEGRINI --seguir
    python -m scripts.fci_admin fondos --gerente schroder              # fondos del universo (con fci_id)
    python -m scripts.fci_admin fondos --sin-simbolo                   # los que no están en Primary (bilaterales / sin link)
    python -m scripts.fci_admin categoria 123 124 --set "T+1"          # el estante ('' = borrar)
    python -m scripts.fci_admin alta "Delta Pesos - Clase B" --gerente DELTA --moneda ARS --categoria T+1 [--unidad "[123] CAFCI…"]
    python -m scripts.fci_admin vcp 123 2026-09-10 1234.5678           # VCP manual (no pisa primary/tenencia)
"""
from __future__ import annotations

import argparse
from datetime import date

from core.fci_match import normalizar
from core.postgres import get_pool


def _q(sql, p=()):
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, p)
        filas = cur.fetchall() if cur.description else []
        conn.commit()
        return filas


def cmd_gerentes(_a):
    filas = _q("SELECT g.gerente, g.seguida, g.alias, count(f.fci_id), "
               "count(f.simbolo_primary), count(f.unidad) "
               "FROM mercado.fci_gerentes g LEFT JOIN mercado.fci f ON f.gerente = g.gerente AND f.activo "
               "GROUP BY g.gerente, g.seguida, g.alias ORDER BY g.seguida DESC, 4 DESC, 1")
    print(f"{'✓':1} {'gerente':<16} {'fondos':>6} {'primary':>7} {'tenidos':>7}  alias")
    for g, seg, alias, n, npr, nun in filas:
        print(f"{'✓' if seg else ' '} {g:<16} {n:>6} {npr:>7} {nun:>7}  {', '.join(alias or [])}")
    return 0


def cmd_gerente(a):
    g = a.gerente.strip().upper()
    if a.alias:
        _q("INSERT INTO mercado.fci_gerentes (gerente, alias, origen) VALUES (%s, %s, 'manual') "
           "ON CONFLICT (gerente) DO UPDATE SET alias = EXCLUDED.alias, actualizado_en = now()",
           (g, a.alias))
        print(f"  {g}: alias = {a.alias}")
    if a.seguir or a.dejar:
        _q("UPDATE mercado.fci_gerentes SET seguida = %s, actualizado_en = now() WHERE gerente = %s",
           (bool(a.seguir), g))
        print(f"  {g}: {'seguida' if a.seguir else 'NO seguida'} (correr jobs.fci_universo para reflejarlo)")
    return 0


def cmd_fondos(a):
    where, p = ["f.activo"], []
    if a.gerente:
        where.append("f.gerente ILIKE %s")
        p.append(f"%{a.gerente}%")
    if a.q:
        where.append("f.nombre_norm LIKE %s")
        p.append(f"%{normalizar(a.q)}%")
    if a.sin_simbolo:
        where.append("f.simbolo_primary IS NULL")
    filas = _q("SELECT f.fci_id, f.gerente, f.moneda, f.plazo, f.categoria, f.simbolo_primary, "
               "f.unidad, f.nombre FROM mercado.fci f WHERE " + " AND ".join(where) +
               " ORDER BY f.gerente, f.nombre", p)
    print(f"{'id':>5} {'gerente':<12} {'mon':3} {'T+':>2} {'estante':<18} {'primary':<28} {'unidad':<8} nombre")
    for fid, g, m, pl, cat, sym, uni, nom in filas:
        print(f"{fid:>5} {(g or '')[:12]:<12} {m or '?':3} {pl if pl is not None else '?':>2} "
              f"{(cat or '')[:18]:<18} {(sym or '—')[:28]:<28} {'✓' if uni else '—':<8} {nom}")
    print(f"({len(filas)} fondos)")
    return 0


def cmd_categoria(a):
    cat = a.set.strip() or None
    for fid, nom in _q("UPDATE mercado.fci SET categoria = %s, actualizado_en = now() "
                       "WHERE fci_id = ANY(%s) RETURNING fci_id, nombre", (cat, a.ids)):
        print(f"  {fid} {nom} → {cat or '(sin estante)'}")
    return 0


def cmd_alta(a):
    filas = _q("INSERT INTO mercado.fci (nombre, nombre_norm, gerente, unidad, moneda, categoria, origen) "
               "VALUES (%s, %s, %s, %s, %s, %s, 'manual') RETURNING fci_id",
               (a.nombre, normalizar(a.nombre), a.gerente.upper(), a.unidad, a.moneda, a.categoria))
    print(f"  alta fci_id={filas[0][0]}: {a.nombre} ({a.gerente.upper()}, {a.moneda})")
    return 0


def cmd_vcp(a):
    n = _q("INSERT INTO mercado.fci_vcp (fci_id, fecha, vcp, fuente) VALUES (%s, %s, %s, 'manual') "
           "ON CONFLICT (fci_id, fecha) DO UPDATE SET vcp = EXCLUDED.vcp "
           "WHERE mercado.fci_vcp.fuente = 'manual' RETURNING fci_id", (a.fci_id, a.fecha, a.vcp))
    print(f"  {'escrito' if n else 'NO escrito: ya hay un VCP de primary/tenencia ese día'}: "
          f"fci {a.fci_id} · {a.fecha} · {a.vcp}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gerentes").set_defaults(fn=cmd_gerentes)
    p = sub.add_parser("gerente"); p.add_argument("gerente")
    p.add_argument("--alias", action="append"); p.add_argument("--seguir", action="store_true")
    p.add_argument("--dejar", action="store_true"); p.set_defaults(fn=cmd_gerente)
    p = sub.add_parser("fondos"); p.add_argument("--gerente"); p.add_argument("--q")
    p.add_argument("--sin-simbolo", action="store_true"); p.set_defaults(fn=cmd_fondos)
    p = sub.add_parser("categoria"); p.add_argument("ids", type=int, nargs="+")
    p.add_argument("--set", required=True); p.set_defaults(fn=cmd_categoria)
    p = sub.add_parser("alta"); p.add_argument("nombre"); p.add_argument("--gerente", required=True)
    p.add_argument("--moneda", required=True, choices=["ARS", "USD"]); p.add_argument("--categoria")
    p.add_argument("--unidad"); p.set_defaults(fn=cmd_alta)
    p = sub.add_parser("vcp"); p.add_argument("fci_id", type=int)
    p.add_argument("fecha", type=date.fromisoformat); p.add_argument("vcp", type=float)
    p.set_defaults(fn=cmd_vcp)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
