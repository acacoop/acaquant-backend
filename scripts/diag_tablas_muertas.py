"""READ-ONLY. ¿Qué tablas de la base ya no sirve nadie?

Herramienta: diag RECURRENTE (no se borra — REGLA #5).

EL PUNTO
========

Una tabla no se borra sola. Se deja de usar —se migró el dominio, se dio de
baja la integración, se renombró— y **queda**: ocupando espacio, apareciendo en
cada inventario, y sobre todo **haciéndose mirar por el AV AGENT**, que juzga
lo que existe en el catálogo de Postgres y no lo que el código usa.

El caso que lo destapó (2026-08-28): el schema `partner` tiene DOS tablas en la
base (`api_users`, `cartera`) y **cero líneas de código** que las lean o las
escriban — restos de la época Mongo, cuando existía `PARTNER_MONGO_URI`. El
agente las venía mirando y les exigía frescura. Ninguna estaba rota: ya no son
de nadie.

CÓMO SE DECIDE — TRES EJES, y ninguno alcanza solo
==================================================

1. **¿La declara `sql/schema.sql`?**  Si no, `apply_schema` no la puede
   recrear: o falta declararla, o no debería existir.
2. **¿Alguien la escribe, según el CÓDIGO?**  `core.escribe.quien_escribe`.
3. **¿Tiene datos, y de cuándo?**  Filas, peso en disco y última escritura.

⚠️⚠️ **«NO LE ENCONTRÉ ESCRITOR» NO ES «NO TIENE ESCRITOR».** El eje 2 es un
regex sobre el código: un INSERT cuyo nombre de tabla viaja en una variable no
lo ve (le pasa a `camara_cereales_audit`, que por eso está declarada a mano en
`escribe.POR_OCASION`). Por eso este diag **no dice «borrala»**: ordena por cuán
seguro es el caso y **el veredicto lo pone una persona**.

Y por eso mismo el corte duro es por DATOS, no por código: una tabla con filas
NUNCA entra en la lista de borrado automático, por más huérfana que se vea.

Uso:
    python -m scripts.diag_tablas_muertas            # el informe
    python -m scripts.diag_tablas_muertas --sql      # + genera sql/drop_muertas.sql
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

from agente import peso, tablas
from core import escribe
from core.postgres import get_pool

# Cuánto silencio hace falta para llamarla «congelada». Un trimestre: más que
# cualquier job mensual, así un cierre de trimestre no la marca por dormida.
DIAS_CONGELADA = 120


def _peso_y_ultima(schema: str, tabla: str, col: str | None) -> tuple[int, datetime | None]:
    """Bytes en disco y última escritura. La fecha es `None` si no hay columna."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_total_relation_size(%s)", (f'"{schema}"."{tabla}"',))
        bytes_ = int((cur.fetchone() or [0])[0] or 0)
        ult = None
        if col:
            # Identificadores citados: vienen del catálogo, no de un input.
            cur.execute(f'SELECT MAX("{col}") FROM "{schema}"."{tabla}"')
            ult = (cur.fetchone() or [None])[0]
    return bytes_, ult


def _mb(b: int) -> str:
    return f"{b / 1024 / 1024:,.1f} MB" if b >= 1024 * 1024 else f"{b / 1024:,.0f} kB"


def _edad_dias(ult) -> int | None:
    if not ult:
        return None
    if isinstance(ult, datetime):
        d = ult if ult.tzinfo else ult.replace(tzinfo=UTC)
    else:  # date
        d = datetime(ult.year, ult.month, ult.day, tzinfo=UTC)
    return (datetime.now(UTC) - d).days


def main() -> int:
    quiere_sql = "--sql" in sys.argv

    inv = tablas.inventario()
    nuestros = peso.schemas_nuestros()
    declaradas = peso.declaradas()

    if not nuestros:
        print("\n  ✖ No pude leer sql/schema.sql — sin eso no puedo opinar. Corto.\n")
        return 1

    filas = []
    for t in inv:
        if t["schema"] not in nuestros:
            continue          # territorio ajeno (Supabase): no es nuestro problema
        nombre = f'{t["schema"]}.{t["tabla"]}'
        escritores = escribe.quien_escribe(nombre)
        bytes_, ult = _peso_y_ultima(t["schema"], t["tabla"], t["col_fecha"])
        filas.append({
            "nombre": nombre, "filas": t["filas"], "bytes": bytes_,
            "col": t["col_fecha"], "ultima": ult, "edad": _edad_dias(ult),
            "escritores": escritores, "declarada": nombre in declaradas,
        })

    # ── Los tres montones. El orden es de MÁS a MENOS seguro. ────────────────
    # 1. VACÍA Y SIN DUEÑO: 0 filas + nadie la escribe. Borrarla no puede
    #    perder un dato, porque no hay dato.
    muertas = [f for f in filas if not f["escritores"] and f["filas"] == 0]
    # 2. CON DATOS PERO SIN DUEÑO: alguien la llenó alguna vez y ya nadie la
    #    toca. Acá SÍ se puede perder algo → decide una persona, mirando qué es.
    congeladas = [f for f in filas
                  if not f["escritores"] and f["filas"] > 0
                  and (f["edad"] is None or f["edad"] >= DIAS_CONGELADA)]
    # 3. SIN DECLARAR: existe en la base y no está en el archivo. No es basura
    #    necesariamente — es deuda: `apply_schema` no la puede recrear.
    sin_declarar = [f for f in filas if not f["declarada"]]

    print(f"\n{'=' * 78}\n TABLAS QUE YA NO SIRVE NADIE — {len(filas)} tablas nuestras"
          f"\n{'=' * 78}")

    def bloque(titulo: str, sub: str, items: list[dict]) -> None:
        print(f"\n  {titulo}  ({len(items)})")
        print(f"  {sub}")
        if not items:
            print("      — ninguna\n")
            return
        print(f"\n      {'TABLA':46} {'FILAS':>9} {'PESO':>10}  ÚLTIMA ESCRITURA")
        for f in sorted(items, key=lambda x: -x["bytes"]):
            if f["ultima"]:
                cuando = f"{f['ultima']:%Y-%m-%d} · hace {f['edad']} d"
            elif f["col"]:
                cuando = "vacía"
            else:
                cuando = "sin columna de fecha"
            marca = "" if f["declarada"] else "  ⚠ sin declarar"
            print(f"      {f['nombre']:46} {f['filas']:>9,} {_mb(f['bytes']):>10}"
                  f"  {cuando}{marca}")
        print(f"\n      → juntas pesan {_mb(sum(f['bytes'] for f in items))}\n")

    bloque("① VACÍAS Y SIN DUEÑO — candidatas a DROP",
           "0 filas y ningún módulo del repo las escribe. Borrarlas no pierde datos.",
           muertas)
    bloque(f"② CON DATOS Y SIN DUEÑO — hace ≥{DIAS_CONGELADA} d que no escriben",
           "Alguien las llenó y ya nadie las toca. ⚠ Acá SÍ se puede perder algo:\n"
           "      mirá QUÉ son antes de decidir. No hay DROP automático para éstas.",
           congeladas)
    bloque("③ SIN DECLARAR en sql/schema.sql — deuda, no basura",
           "Existen en la base y no están en el archivo: `apply_schema` no las\n"
           "      puede recrear si se pierden. O se declaran, o se borran.",
           sin_declarar)

    print("  ⚠️ RECORDÁ: «no le encontré escritor» NO es «no tiene escritor». El eje\n"
          "     del código es un regex; un INSERT con el nombre en una variable no se\n"
          "     ve. Por eso el bloque ② no se borra solo y el ① exige además 0 filas.\n")

    if quiere_sql:
        if not muertas:
            print("  (--sql) No hay nada en el bloque ①: no genero archivo.\n")
            return 0
        ruta = "sql/drop_muertas.sql"
        with open(ruta, "w", encoding="utf-8") as fh:
            fh.write("-- Generado por scripts/diag_tablas_muertas --sql\n")
            fh.write(f"-- {datetime.now(UTC):%Y-%m-%d %H:%M} UTC · "
                     f"solo el bloque ① (0 filas + sin escritor en el código).\n")
            fh.write("-- LEELO ANTES DE CORRERLO. Nada de esto se ejecuta solo.\n\n")
            fh.write("BEGIN;\n")
            for f in sorted(muertas, key=lambda x: x["nombre"]):
                sch, tab = f["nombre"].split(".", 1)
                fh.write(f'DROP TABLE IF EXISTS "{sch}"."{tab}";\n')
            fh.write("COMMIT;\n")
        print(f"  ✔ Escrito {ruta} con {len(muertas)} DROP. Revisalo y corrélo a mano.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
