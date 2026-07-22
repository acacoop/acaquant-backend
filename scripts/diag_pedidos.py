"""diag_pedidos — ¿por qué el pedido que hice no aparece?

READ-ONLY. Responde las tres preguntas, en orden, sin adivinar ninguna:

  1. ¿La tabla tiene las columnas del triaje?  (si no: falta apply_schema)
  2. ¿Hay pedidos guardados?                   (con estado y si están triados)
  3. ¿El modelo LLAMÓ a la herramienta?        (busca en ia.trazas las
     conversaciones donde alguien pidió una mejora y muestra si se ejecutó
     `registrar_pedido` o si el asistente contestó de compromiso)

La 3 es la que suele explicarlo: si la vista donde escribiste no tenía la
herramienta cableada, el modelo no puede decir "no tengo cómo anotarlo" —
contesta amablemente y el pedido se pierde. Parece que quedó anotado y no.

Uso (Droplet):
    python -m scripts.diag_pedidos
    python -m scripts.diag_pedidos --horas 48
"""
from __future__ import annotations

import argparse

from psycopg.rows import dict_row

from core.postgres import get_pool

# Palabras con las que alguien pide una mejora. Solo se usan para BUSCAR
# conversaciones candidatas en las trazas — no deciden nada.
_PISTAS = ("pedido", "sugerencia", "estaría bueno", "estaria bueno", "falta",
           "agregar", "mejora", "no encuentro", "sería bueno", "seria bueno")


def _columnas(cur) -> set[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'manager' AND table_name = 'pedidos'")
    return {r["column_name"] for r in cur.fetchall()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horas", type=int, default=24,
                    help="ventana de trazas a revisar (default 24)")
    args = ap.parse_args()

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        print("── 1. estructura de manager.pedidos ──")
        cols = _columnas(cur)
        if not cols:
            print("  ✗ la tabla NO existe → python -m scripts.apply_schema")
            return
        faltan = {"impacto", "esfuerzo", "spec", "duplicado_de", "triado_at",
                  "decidido_por", "decidido_at"} - cols
        if faltan:
            print(f"  ✗ faltan columnas: {', '.join(sorted(faltan))}")
            print("    → python -m scripts.apply_schema")
        else:
            print("  ✓ la tabla tiene todas las columnas del triaje")

        print("\n── 2. pedidos guardados ──")
        cur.execute("SELECT count(*) AS n FROM manager.pedidos")
        total = cur.fetchone()["n"]
        if not total:
            print("  ✗ la tabla está VACÍA: no se guardó ni un pedido.")
            print("    Entonces el problema NO es el triaje — es la captura.")
        else:
            cur.execute(
                "SELECT id, ts, usuario, vista, tipo, estado, titulo, "
                "       (triado_at IS NOT NULL) AS triado "
                "FROM manager.pedidos ORDER BY ts DESC LIMIT 20")
            print(f"  {total} pedido(s) en total. Los últimos:")
            for p in cur.fetchall():
                marca = "triado" if p["triado"] else "SIN TRIAR"
                quien = (p["usuario"] or "—").split("@")[0]
                print(f"    #{p['id']} [{p['estado']}/{marca}] {str(p['ts'])[:16]} · "
                      f"{quien} · desde {p['vista'] or '—'} · {p['titulo']}")
            cur.execute(
                "SELECT count(*) AS n FROM manager.pedidos "
                "WHERE estado = 'nuevo' AND triado_at IS NULL")
            print(f"  → {cur.fetchone()['n']} en cola para el próximo triaje")

        print(f"\n── 3. ¿el modelo llamó a la herramienta? (últimas {args.horas}h) ──")
        cur.execute(
            """
            SELECT id, ts, tarea, usuario, ok,
                   left(coalesce(detalle, ''), 120)   AS pregunta,
                   (coalesce(respuesta, '') || coalesce(detalle, ''))
                       ILIKE '%%registrar_pedido%%'   AS uso_la_tool
            FROM ia.trazas
            WHERE ts >= now() - make_interval(hours => %s)
              AND tarea IN ('asistente_negocio', 'copiloto_vista', 'copiloto_vista_pro')
            ORDER BY ts DESC LIMIT 200
            """, (args.horas,))
        filas = cur.fetchall()
        if not filas:
            print("  (sin conversaciones con la IA en la ventana)")
            return
        candidatas = [f for f in filas
                      if any(p in (f["pregunta"] or "").lower() for p in _PISTAS)]
        con_tool = [f for f in filas if f["uso_la_tool"]]
        print(f"  {len(filas)} conversaciones · {len(candidatas)} parecen un pedido de "
              f"mejora · {len(con_tool)} ejecutaron registrar_pedido")
        for f in candidatas[:10]:
            marca = "✓ registró" if f["uso_la_tool"] else "✗ NO registró"
            print(f"    {str(f['ts'])[:16]} [{f['tarea']}] {marca} · «{f['pregunta']}»")
        if candidatas and not con_tool:
            print("\n  DIAGNÓSTICO: hubo pedidos de mejora y NINGUNO se registró.")
            print("  Causa conocida (arreglada 2026-07-22): la vista `negocio` no")
            print("  tenía la herramienta del buzón cableada. Si el api.service es")
            print("  posterior al fix y sigue pasando, avisá con esta salida.")


if __name__ == "__main__":
    main()
