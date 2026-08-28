"""READ-ONLY. ¿Qué tablas mira el agente que NO son nuestras?

Herramienta: diag · Mide antes de filtrar.

EL PUNTO
========

`agente/tablas.py` toma TODAS las tablas del catálogo de Postgres menos las del
sistema. Eso incluye los schemas que crea **Supabase** para sus propios
servicios (`realtime`, `auth`, `storage`, …): tablas que no escribimos, no
controlamos y de las que no sabemos cada cuánto deberían escribir.

El 2026-08-28 el agente marcó `realtime.schema_migrations` como «dejó de
escribir». No está rota: no es nuestra.

Lo que se quiere es mirar **lo que esté en nuestro schema**, derivado de
`sql/schema.sql` y no de una lista a mano — si mañana se agrega un schema
nuevo al archivo, entra solo.

⚠️ **POR QUÉ SE MIDE ANTES DE FILTRAR.** El riesgo no es dejar afuera a
Supabase: es dejar afuera algo NUESTRO. `schema.sql` tiene un bloque que mueve
tablas viejas de `public` a su schema, así que si quedó alguna suelta ahí,
filtrar por schema declarado la volvería invisible — peor que el problema que
se está arreglando.

Uso:
    python -m scripts.diag_schemas_ajenos
"""
from __future__ import annotations

from agente import peso, tablas


def main() -> int:
    inv = tablas.inventario()
    nuestros = {t.split(".")[0] for t in peso.declaradas()}

    por_schema: dict[str, list[dict]] = {}
    for t in inv:
        por_schema.setdefault(t["schema"], []).append(t)

    print(f"\n{'=' * 74}\n QUÉ MIRA EL AGENTE HOY — {len(inv)} tablas\n{'=' * 74}")
    print(f"\n  Schemas declarados en sql/schema.sql: {len(nuestros)}")
    print(f"  {', '.join(sorted(nuestros))}\n")

    print(f"  {'SCHEMA':22} {'TABLAS':>7}  {'¿NUESTRO?':10}  con col. de fecha")
    dentro = fuera = 0
    for sch in sorted(por_schema, key=lambda s: -len(por_schema[s])):
        filas = por_schema[sch]
        con_fecha = sum(1 for f in filas if f["col_fecha"])
        es_nuestro = sch in nuestros
        dentro += len(filas) if es_nuestro else 0
        fuera += 0 if es_nuestro else len(filas)
        print(f"  {sch:22} {len(filas):>7}  {'sí' if es_nuestro else '← AJENO':10}  "
              f"{con_fecha}")

    print(f"\n  → quedarían DENTRO: {dentro}   ·   quedarían AFUERA: {fuera}")

    # ⚠️ Lo único que importa de verdad: ¿hay algo NUESTRO en un schema que no
    # declaramos? `public` es el sospechoso — el schema.sql mueve tablas de ahí.
    ajenas = [f'{t["schema"]}.{t["tabla"]}' for t in inv
              if t["schema"] not in nuestros and t["col_fecha"]]
    if ajenas:
        print(f"\n  LAS {len(ajenas)} QUE SE DEJARÍAN DE MIRAR (solo las que tienen"
              f" columna de fecha,\n  que son las únicas que el agente puede juzgar):")
        for a in sorted(ajenas):
            print(f"      {a}")
        print("\n  ⚠️ Revisá esta lista: si reconocés alguna como NUESTRA, el filtro\n"
              "     por schema no alcanza y hay que pensarlo distinto.")
    else:
        print("\n  ✔ Ninguna tabla de un schema ajeno tiene columna de fecha:\n"
              "    el filtro no puede tapar nada que el agente estuviera juzgando.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
