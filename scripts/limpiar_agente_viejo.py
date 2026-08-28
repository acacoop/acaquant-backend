"""`scripts/limpiar_agente_viejo.py` — DROPEA las tablas del agente viejo.

Doc: `docs/AGENT_2.0.md` §9. **Destructivo, y por eso NO hace nada por default.**

El rediseño dejó las 18 tablas `agente.av_agent_*` sin escribirse pero sin
borrar, con un criterio explícito: *borrar código se revierte, borrar datos no*.
Este script es el paso siguiente, cuando el agente nuevo ya demostró que anda.

## Qué mira antes de tocar nada

Dropear a ciegas sería exactamente el anti-patrón que este agente existe para
evitar. Así que primero **verifica que el reemplazo esté funcionando**:

  · las 4 tablas nuevas existen
  · el agente LATIÓ hace poco (o sea: el daemon corre)
  · hay habilidades que corrieron con resultado `ok`

Si algo de eso falla, **aborta**. Y muestra cuánto pesa y cuántas filas tiene
cada tabla vieja antes de borrarla: el que aprieta tiene que saber qué se lleva.

## Uso

    python -m scripts.limpiar_agente_viejo            # SOLO MIRA (default)
    python -m scripts.limpiar_agente_viejo --aplicar  # dropea de verdad
    python -m scripts.limpiar_agente_viejo --aplicar --sin-guardas   # ⚠️
"""
from __future__ import annotations

import argparse
import sys

from core.postgres import get_pool

# Las 18 del agente viejo. Se listan a mano y no con un `LIKE 'av_agent%'`:
# un patrón dropea lo que todavía no existe cuando alguien lo escriba.
VIEJAS = (
    "av_agent_acciones", "av_agent_aviso_items", "av_agent_avisos",
    "av_agent_centinela", "av_agent_control", "av_agent_errores",
    "av_agent_evals", "av_agent_evaluado", "av_agent_hallazgos",
    "av_agent_ignorados", "av_agent_items", "av_agent_latido",
    "av_agent_lecciones", "av_agent_preguntas", "av_agent_propuestas",
    "av_agent_runs", "av_agent_seguimiento", "av_agent_trazas",
)
NUEVAS = ("habilidades", "hallazgos", "reincidencias", "acciones")


def _filas(sql: str, params: tuple = ()) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _guardas() -> list[str]:
    """¿El reemplazo está andando? Si no, no se borra nada."""
    problemas = []

    hay = {r[0] for r in _filas(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'agente'")}
    faltan = [t for t in NUEVAS if t not in hay]
    if faltan:
        problemas.append(f"las tablas nuevas no existen: {', '.join(faltan)}. "
                         "¿Corrió `apply_schema`?")

    lat = _filas("SELECT EXTRACT(epoch FROM now() - at)::int "
                 "FROM agente.latido WHERE id = 1")
    if not lat:
        problemas.append("el agente nuevo NUNCA latió: el daemon `agente.service` "
                         "no arrancó o no completó una pasada")
    elif lat[0][0] > 3600:
        problemas.append(f"el último latido fue hace {lat[0][0] // 60} min: el "
                         "daemon no está corriendo")

    ok = _filas("SELECT count(*) FROM agente.habilidades "
                "WHERE ultimo_resultado = 'ok'")[0][0]
    if ok < 5:
        problemas.append(f"solo {ok} habilidad(es) corrieron con resultado `ok`: "
                         "el agente nuevo todavía no demostró que mira")
    return problemas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true",
                    help="dropea de verdad (sin esto solo muestra)")
    ap.add_argument("--sin-guardas", action="store_true",
                    help="⚠️ dropea aunque el agente nuevo no esté andando")
    a = ap.parse_args()

    print("\n═══ LO QUE SE LLEVA ═══\n")
    print(f"  {'TABLA':28} {'FILAS':>10} {'PESO':>10}")
    total_bytes = total_filas = existen = 0
    for t in VIEJAS:
        r = _filas(
            "SELECT pg_total_relation_size(c.oid) "
            "  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            " WHERE n.nspname = 'agente' AND c.relname = %s", (t,))
        if not r:
            print(f"  {t:28} {'—':>10} {'(ya no está)':>10}")
            continue
        existen += 1
        b = int(r[0][0] or 0)
        # ⚠️⚠️ **COUNT(*) DE VERDAD, NO `reltuples`.** Acá se leía
        # `c.reltuples`, y desde Postgres 10 **`reltuples = -1` es el centinela
        # de «esta tabla nunca fue analizada»** — no un conteo. La columna
        # FILAS mostraba literalmente `-1` para las 18, y el total decía
        # «~0 filas» sumando con `max(filas, 0)`: o sea, **el número que el
        # operador lee justo antes de un DROP irreversible era una estadística
        # ausente disfrazada de cero**. Son 18 tablas chicas: contarlas de
        # verdad es gratis, y es el único número que puede sostener la frase
        # «sabés qué te llevás».
        filas = int(_filas(f'SELECT count(*) FROM agente."{t}"')[0][0])
        total_filas += filas
        total_bytes += b
        print(f"  {t:28} {filas:>10,} {b / (1 << 20):>9,.1f} MB")
    print(f"\n  {existen} tabla(s) · {total_filas:,} filas (contadas) · "
          f"{total_bytes / (1 << 20):,.1f} MB")

    if not existen:
        print("\n✔ Ya está limpio: no queda ninguna tabla del agente viejo.\n")
        return 0

    print("\n═══ ¿EL REEMPLAZO ESTÁ ANDANDO? ═══\n")
    problemas = _guardas()
    if not problemas:
        print("  ✔ las 4 tablas nuevas existen")
        print("  ✔ el agente latió hace poco")
        print("  ✔ hay habilidades corriendo en `ok`")
    else:
        for p in problemas:
            print(f"  ✗ {p}")

    if not a.aplicar:
        print("\n── Esto fue SOLO UNA MIRADA. Nada se tocó. ──")
        print("   Para borrarlas: `python -m scripts.limpiar_agente_viejo --aplicar`")
        print("   ⚠️ Es IRREVERSIBLE: no hay vuelta atrás sin un backup.\n")
        return 0

    if problemas and not a.sin_guardas:
        print("\n✗ NO BORRO NADA: el agente nuevo todavía no demostró que anda.")
        print("  Dropear el viejo mientras el nuevo no mira deja al sistema sin")
        print("  ninguno de los dos, y sin cómo volver.")
        print("  Si igual querés: agregá `--sin-guardas`.\n")
        return 1

    print("\n═══ BORRANDO ═══\n")
    with get_pool().connection() as conn, conn.cursor() as cur:
        for t in VIEJAS:
            cur.execute(f'DROP TABLE IF EXISTS agente."{t}" CASCADE')
            print(f"  ✓ agente.{t}")
    print(f"\n✔ {existen} tabla(s) borradas · "
          f"{total_bytes / (1 << 20):,.1f} MB liberados\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
