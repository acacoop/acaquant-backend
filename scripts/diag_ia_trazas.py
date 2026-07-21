"""scripts/diag_ia_trazas.py — últimas llamadas al gateway de IA (¿por qué None?).

Lee `ia.trazas` (el medidor de core/ai): cada llamada al LLM deja tarea, modelo,
ok/error, tokens y latencia. Sirve para ver POR QUÉ una llamada devolvió None
(presupuesto agotado, error HTTP del proveedor, respuesta vacía, sin key…) y
cuánto se gastó hoy.

Con --buscar recupera CONVERSACIONES enteras: filtra por texto en la pregunta o
la respuesta y las imprime completas (para revisar cómo respondió el copiloto a
un usuario — ej. la consulta de EWZ del 2026-07-20 que quedó tapada por una
batería en el panel de OBSERVABILIDAD).

Uso:
    python -m scripts.diag_ia_trazas                       # últimas 15 (resumen)
    python -m scripts.diag_ia_trazas --limite 40
    python -m scripts.diag_ia_trazas --tarea copiloto_vista
    python -m scripts.diag_ia_trazas --buscar EWZ          # conversaciones con "EWZ"
"""
from __future__ import annotations

import argparse

from core.postgres import connect


def _resumen(cur, limite: int, tarea: str | None) -> None:
    cond, params = "", []
    if tarea:
        cond = "WHERE tarea = %s"
        params.append(tarea)
    cur.execute(
        f"SELECT ts, tarea, usuario, modelo, ok, tokens_in, tokens_out, latencia_ms, "
        f"cache_hit_tokens, detalle, error "
        f"FROM ia.trazas {cond} ORDER BY ts DESC LIMIT %s",
        (*params, limite),
    )
    filas = cur.fetchall()
    if not filas:
        print("ia.trazas sin filas para ese filtro.")
        return

    def _n(v) -> str:
        return "-" if v is None else str(v)

    print(f"{'ts (UTC)':<20} {'tarea':<20} {'usuario':<22} {'ok':<3} "
          f"{'tok_in':>7} {'tok_out':>7} {'cache%':>6} {'lat_ms':>7}  detalle/error")
    print("─" * 124)
    errores: list[tuple] = []
    for ts, tar, usr, _mod, ok, ti, to, lat, hit, det, err in filas:
        estado = "✓" if ok else "✗"
        # % del input que pegó en el caché del proveedor (~10x más barato)
        cache = f"{round(100 * hit / ti)}%" if (hit is not None and ti) else "-"
        # una línea por traza: el error se resume acá y va COMPLETO abajo
        extra = " ".join((err or det or "").split())[:52]
        print(f"{str(ts)[:19]:<20} {(tar or '')[:20]:<20} {(usr or '')[:22]:<22} "
              f"{estado:<3} {_n(ti):>7} {_n(to):>7} {cache:>6} {_n(lat):>7}  {extra}")
        if err:
            errores.append((ts, tar, usr, err))

    # Los errores ENTEROS al final: cortados a 52 chars no se puede diagnosticar
    # nada (un 400 del proveedor dice QUÉ parámetro rechaza recién en el medio).
    if errores:
        print("\n" + "═" * 124)
        print("ERRORES COMPLETOS")
        for ts, tar, usr, err in errores:
            print("─" * 124)
            print(f"{str(ts)[:19]} · {tar} · {usr or '-'}")
            print(err)


def _buscar(cur, texto: str, limite: int) -> None:
    """Conversaciones completas que mencionan `texto` (en pregunta o respuesta)."""
    cur.execute(
        """
        SELECT ts, tarea, usuario, modelo, tokens_in, tokens_out, detalle, respuesta, feedback
        FROM ia.trazas
        WHERE ok AND (detalle ILIKE %s OR respuesta ILIKE %s)
        ORDER BY ts DESC LIMIT %s
        """,
        (f"%{texto}%", f"%{texto}%", limite),
    )
    filas = cur.fetchall()
    if not filas:
        print(f"Sin llamadas que mencionen {texto!r}.")
        return
    for ts, tarea, usuario, modelo, ti, to, detalle, respuesta, fb in filas:
        print("=" * 78)
        print(f"{str(ts)[:19]} · {tarea} · {usuario or '-'} · {modelo} · "
              f"in {ti or '-'} / out {to or '-'}"
              + (f" · feedback {'👍' if fb == 1 else '👎'}" if fb in (1, -1) else ""))
        print(f"\nPREGUNTA:\n{(detalle or '').strip()[:600]}")
        print(f"\nRESPUESTA:\n{(respuesta or '').strip()[:2500]}")
        print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=15)
    ap.add_argument("--tarea", default=None, help="filtrar el resumen a una tarea")
    ap.add_argument("--buscar", default=None,
                    help="texto a buscar en pregunta/respuesta → imprime las conversaciones")
    args = ap.parse_args()

    with connect() as conn, conn.cursor() as cur:
        if args.buscar:
            _buscar(cur, args.buscar, args.limite)
        else:
            _resumen(cur, args.limite, args.tarea)
        cur.execute(
            "SELECT coalesce(sum(coalesce(tokens_in,0)+coalesce(tokens_out,0)),0) "
            "FROM ia.trazas WHERE ts >= date_trunc('day', now())"
        )
        hoy = cur.fetchone()[0]

    print(f"\nTokens gastados HOY (UTC): {hoy:,}")
    print("Presupuesto diario: editable en Manager → OBSERVABILIDAD → pill IA.")


if __name__ == "__main__":
    main()
