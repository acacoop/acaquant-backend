"""scripts/diag_index_advisor.py — ¿un índice arreglaría las queries caras?

CONTEXTO
========
El Tablero Comercial hace 8 viajes a la base por request pero cada uno cuesta
~56ms (medido con cProfile 2026-08-13): NO es el patrón N+1 de agro —que se
arreglaba agrupando y salía gratis—, son agregaciones caras de verdad sobre
`negocio_movimientos` (413k filas) y `portafolio.tenencia` (466k).

Las salidas posibles son tres y solo una es barata:
  1. un ÍNDICE las arregla            → barato, no cambia nada. ESTE script.
  2. no hay índice que ayude + cache   → cambia frescura.
  3. no hay índice que ayude + agregado→ proyecto (el patrón ops_agregado_diario).

Antes de pagar 2 o 3, hay que descartar 1 — y se puede preguntar sin crear
nada: Supabase trae `index_advisor` (+ `hypopg`), que PLANIFICA la query con
índices hipotéticos y dice si bajaría el costo. No escribe en la base ni crea
índices: solo simula.

QUÉ HACE
========
Toma del propio pg_stat_statements las queries más caras que tocan las tablas
del tablero y le pregunta a index_advisor por cada una. Imprime el costo antes
y después y el CREATE INDEX sugerido — que NO se ejecuta: queda para decidir.

OJO al leerlo: una sugerencia con poca mejora de costo NO se aplica. Un índice
que nadie usa se paga en cada INSERT/UPDATE y en disco (por eso el bloque 4 de
diag_costo_real lista los que no se usan).

Read-only. Uso (en el Droplet):
    python -m scripts.diag_index_advisor [tablas...]   # default: las del tablero
"""
from __future__ import annotations

import sys

from core.postgres import get_pool

_TABLAS_DEFAULT = ("negocio_movimientos", "tenencia")
_TOP = 6


def _q(cur, sql: str, params: tuple = ()) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


def _schema_de(cur, relname: str) -> str | None:
    r = _q(cur, "SELECT n.nspname FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace WHERE c.relname = %s LIMIT 1", (relname,))
    return r[0][0] if r else None


def main() -> int:
    tablas = tuple(sys.argv[1:]) or _TABLAS_DEFAULT
    pool = get_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        conn.autocommit = True     # un fallo no aborta lo que sigue

        ext = _schema_de(cur, "pg_stat_statements")
        if not ext:
            print("pg_stat_statements no está — sin queries que analizar.")
            return 1

        # ¿Está index_advisor? Es una FUNCIÓN, no una tabla.
        adv = _q(cur, "SELECT n.nspname FROM pg_proc p JOIN pg_namespace n "
                      "ON n.oid = p.pronamespace WHERE p.proname = 'index_advisor' LIMIT 1")
        if not adv:
            print("index_advisor no está instalada.")
            print("(Supabase → Database → Extensions → index_advisor)")
            return 1
        adv_schema = adv[0][0]
        print(f"index_advisor en `{adv_schema}` · pg_stat_statements en `{ext}`\n")

        # index_advisor llama a hypopg SIN calificar el schema. Si `extensions`
        # no está en el search_path, falla con "function hypopg_get_indexdef(oid)
        # does not exist" y devuelve error en vez de recomendación — parece que
        # no hay índice posible cuando en realidad ni se analizó (2026-08-13).
        #
        # Se AGREGA al search_path existente, no se reemplaza: la app conecta con
        # `-c search_path=...` (core/postgres.py) para que los nombres sin
        # calificar resuelvan, y las queries guardadas en pg_stat_statements
        # dependen de eso — dicen `comitentes` y `negocio_movimientos` a secas.
        # Pisarlo hacía fallar el análisis con "relation does not exist", que
        # otra vez PARECE un veredicto y no lo es.
        cur.execute("SHOW search_path")
        actual = cur.fetchone()[0]
        if adv_schema not in actual:
            cur.execute(f"SET search_path TO {actual}, {adv_schema}")
        print(f"search_path: {actual}"
              + ("" if adv_schema in actual else f" (+ {adv_schema})") + "\n")

        patrones = [f"%{t}%" for t in tablas]
        filas = _q(cur, f"""
            SELECT calls, mean_exec_time, total_exec_time, query
            FROM {ext}.pg_stat_statements
            WHERE query ILIKE ANY(%s)
              AND btrim(query) ILIKE %s      -- que EMPIECE con SELECT: sin esto
              AND btrim(query) NOT ILIKE %s  -- entran INSERT…SELECT y cuerpos de
            ORDER BY total_exec_time DESC LIMIT %s  -- función que contienen SELECT
        """, (patrones, "SELECT%", "%index_advisor%", _TOP))
        if not filas:
            print(f"sin queries que toquen {', '.join(tablas)}.")
            return 0

        for i, (calls, media, total, query) in enumerate(filas, 1):
            print(f"{'=' * 78}\n[{i}] {calls} llamadas · media {media:.1f}ms · "
                  f"total {total / 1000:.0f}s")
            print("   " + " ".join(query.split())[:150] + "…\n")
            try:
                # hypopg acumula los índices hipotéticos de cada análisis y se
                # queda sin OIDs ("not more oid available") a partir de la 3ª
                # query. Se limpia antes de cada una.
                try:
                    cur.execute(f"SELECT {adv_schema}.hypopg_reset()")
                except Exception:
                    pass          # sin hypopg el advisor igual reporta su error
                cur.execute(f"SELECT * FROM {adv_schema}.index_advisor(%s)", (query,))
                cols = [d[0] for d in cur.description]
                rec = [dict(zip(cols, f, strict=False)) for f in cur.fetchall()]
            except Exception as e:
                # Lo más común: la query viene parametrizada ($1, $2…) y el
                # planner no puede inferir los tipos. No es un error del script.
                print(f"   index_advisor no pudo analizarla: {str(e).splitlines()[0][:90]}")
                print("   (suele pasar con queries parametrizadas — se puede\n"
                      "    reintentar a mano pegando la query con valores reales)\n")
                continue
            if not rec:
                print("   sin sugerencias → NINGÚN índice mejoraría esta query.")
                print("   Si igual duele, el camino es cache o agregado.\n")
                continue
            for fila in rec:
                errores = fila.get("errors") or []
                if errores:
                    # Un error NO es "no hay índice posible": es que no se pudo
                    # analizar. Distinguirlo evita concluir de más.
                    print(f"   ⚠ no se pudo analizar: {'; '.join(map(str, errores))}\n")
                    continue
                idx = fila.get("index_statements") or []
                antes = fila.get("total_cost_before")
                desp = fila.get("total_cost_after")
                if antes and desp:
                    try:
                        mejora = (1 - float(desp) / float(antes)) * 100
                        print(f"   costo {float(antes):.0f} → {float(desp):.0f}  "
                              f"({mejora:+.0f}%)")
                    except (TypeError, ValueError, ZeroDivisionError):
                        print(f"   costo {antes} → {desp}")
                if not idx:
                    print("   sin índice sugerido para esta query.\n")
                    continue
                for stmt in idx:
                    print(f"   SUGERIDO (NO ejecutado): {stmt}")
                print()
        print("Recordá: una sugerencia con mejora chica NO se aplica — un índice")
        print("que nadie usa se paga en cada escritura y en disco.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
