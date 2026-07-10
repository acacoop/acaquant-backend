"""scripts/diag_ia_trazas.py — últimas llamadas al gateway de IA (¿por qué None?).

Lee `ia.trazas` (el medidor de core/ai): cada llamada al LLM deja tarea, modelo,
ok/error, tokens y latencia. Sirve para ver POR QUÉ una llamada devolvió None
(presupuesto agotado, error HTTP del proveedor, respuesta vacía, sin key…) y
cuánto se gastó hoy.

Uso:  python -m scripts.diag_ia_trazas
"""
from __future__ import annotations

from core.postgres import connect


def main() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ts, tarea, modelo, ok, tokens_in, tokens_out, latencia_ms, error "
            "FROM ia.trazas ORDER BY ts DESC LIMIT 15"
        )
        filas = cur.fetchall()
        cur.execute(
            "SELECT coalesce(sum(coalesce(tokens_in,0)+coalesce(tokens_out,0)),0) "
            "FROM ia.trazas WHERE ts >= date_trunc('day', now())"
        )
        hoy = cur.fetchone()[0]

    if not filas:
        print("ia.trazas VACÍA — ni una llamada registrada. ¿DEEPSEEK_API_KEY seteada?")
        print("(sin key, core.ai.completar devuelve None SIN dejar traza — sería ruido.)")
        return

    print(f"{'ts (UTC)':<20} {'tarea':<20} {'modelo':<16} {'ok':<3} "
          f"{'tok_in':>7} {'tok_out':>7} {'lat_ms':>7}  error")
    print("─" * 110)
    for ts, tarea, modelo, ok, ti, to, lat, err in filas:
        print(f"{str(ts)[:19]:<20} {(tarea or '')[:20]:<20} {(modelo or '')[:16]:<16} "
              f"{'✓' if ok else '✗':<3} {str(ti if ti is not None else '-'):>7} "
              f"{str(to if to is not None else '-'):>7} {str(lat if lat is not None else '-'):>7}  "
              f"{(err or '')[:70]}")

    print(f"\nTokens gastados HOY (UTC): {hoy:,}")
    print("Presupuesto diario default: 2.000.000 tokens (env AI_BUDGET_TOKENS_DIA).")
    print("→ Si las filas de 'triage_incidente' tienen ok=✗, mirá su columna 'error':")
    print("  'presupuesto diario agotado' = tope · 'HTTP 4xx/5xx' = problema del proveedor/modelo.")


if __name__ == "__main__":
    main()
