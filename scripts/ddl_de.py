"""READ-ONLY. Imprime el `CREATE TABLE` de una tabla que YA existe en la base.

Herramienta: diag RECURRENTE (no se borra — REGLA #5).

EL PUNTO
========

`scripts/diag_tablas_muertas` bloque ③ lista las tablas que existen en la base y
**no están en `sql/schema.sql`**. Eso es deuda concreta: `apply_schema` no las
puede recrear, así que en una base nueva —o si alguien las dropea— no vuelven.

Para saldarla hay que escribir su `CREATE TABLE` en el archivo, y ese DDL **solo
existe en la base**: nadie lo tiene escrito en ningún lado. Este script lo
reconstruye desde el catálogo de Postgres para poder pegarlo en el schema.

⚠️ **Reconstruido, no `pg_dump`.** Supabase es un Postgres gestionado y la
versión del `pg_dump` del Droplet no tiene por qué coincidir con la del server
(un `pg_dump` más viejo se niega a correr). El catálogo se lee igual siempre.

⚠️ **Las columnas GENERADAS (`GENERATED ... STORED`) NO se reconstruyen**: se
marcan en la salida para que se resuelvan a mano. Su expresión puede depender de
otras columnas y copiarla mal es peor que no copiarla.

⚠️ **Lo que SÍ o SÍ hay que revisar antes de pegar**: el DDL sale con la forma
que la tabla tiene HOY, que puede no ser la que se quiso. Si una columna quedó
`text` porque se creó a mano y debería ser `numeric`, esto lo va a copiar tal
cual. Es un punto de partida honesto, no un veredicto.

Uso:
    python -m scripts.ddl_de mercado.bonos_ohlc_daily
    python -m scripts.ddl_de mercado.bonos_ohlc_daily mercado.camara_cereales_audit
"""
from __future__ import annotations

import sys

from core.postgres import get_job_pool as get_pool


def _q(sql: str, params: tuple = ()) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def ddl(schema: str, tabla: str) -> str:
    cols = _q("""
        SELECT a.attname,
               format_type(a.atttypid, a.atttypmod),
               a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid),
               a.attidentity,          -- '' | 'a' ALWAYS | 'd' BY DEFAULT
               a.attgenerated          -- '' | 's' STORED
          FROM pg_attribute a
          JOIN pg_class c      ON c.oid = a.attrelid
          JOIN pg_namespace n  ON n.oid = c.relnamespace
          LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
         WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0
           AND NOT a.attisdropped
         ORDER BY a.attnum
    """, (schema, tabla))
    if not cols:
        return f"-- ✖ {schema}.{tabla} no existe en la base\n"

    ancho = max(len(c[0]) for c in cols)
    tipo_ancho = max(len(c[1]) for c in cols)
    lineas = []
    for nombre, tipo, notnull, default, ident, generada in cols:
        pieza = f"    {nombre:<{ancho}} {tipo:<{tipo_ancho}}"
        # ⚠️⚠️ **IDENTITY NO ES UN DEFAULT y no vive en `pg_attrdef`.** La primera
        # versión leía solo los defaults, así que
        # `mercado.camara_cereales_audit` salió con `id bigint NOT NULL` a secas
        # —se comió el `GENERATED ALWAYS AS IDENTITY`— y pegar eso en el schema
        # habría creado, en una base nueva, una tabla donde **todo INSERT falla**
        # por `null value in column "id"`. Y el `INSERT` que la usa no nombra la
        # columna, así que el error aparecería recién al auditar un precio.
        #
        # Se salvó porque el código que la crea estaba a mano
        # (`api/services/camara_cereales.py`) y decía la verdad. La próxima tabla
        # puede no tener quién la desmienta.
        if ident:
            pieza += (" GENERATED " + ("ALWAYS" if ident == "a" else "BY DEFAULT")
                      + " AS IDENTITY")
        elif generada:
            pieza += " (columna GENERADA — revisar a mano, no se reconstruye acá)"
        if notnull and not ident:
            pieza += " NOT NULL"
        if default:
            pieza += f" DEFAULT {default}"
        lineas.append(pieza)

    # Las CONSTRAINTS (PK, UNIQUE, CHECK, FK) van adentro del CREATE. Se piden
    # al catálogo ya renderizadas: escribirlas a mano desde las columnas es
    # cómo se pierde un ON DELETE o el orden de una PK compuesta.
    cons = _q("""
        SELECT con.conname, pg_get_constraintdef(con.oid)
          FROM pg_constraint con
          JOIN pg_class c     ON c.oid = con.conrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = %s AND c.relname = %s
         ORDER BY con.contype, con.conname
    """, (schema, tabla))
    for nombre, definicion in cons:
        lineas.append(f"    CONSTRAINT {nombre} {definicion}")

    out = [f"CREATE TABLE IF NOT EXISTS {schema}.{tabla} (",
           ",\n".join(lineas), ");"]

    # Los índices que NO son de una constraint (esos ya viajan arriba).
    nombres_con = {c[0] for c in cons}
    idx = _q("""
        SELECT i.relname, pg_get_indexdef(x.indexrelid)
          FROM pg_index x
          JOIN pg_class i     ON i.oid = x.indexrelid
          JOIN pg_class c     ON c.oid = x.indrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = %s AND c.relname = %s
         ORDER BY i.relname
    """, (schema, tabla))
    for nombre, definicion in idx:
        if nombre in nombres_con:
            continue
        # `pg_get_indexdef` no pone IF NOT EXISTS y el schema tiene que ser
        # idempotente: se corre entero en CADA deploy.
        out.append(definicion.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
                             .replace("CREATE UNIQUE INDEX ",
                                      "CREATE UNIQUE INDEX IF NOT EXISTS ", 1) + ";")
    return "\n".join(out) + "\n"


def main() -> int:
    pedidas = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not pedidas:
        print(__doc__)
        return 2
    for t in pedidas:
        if "." not in t:
            print(f"-- ✖ '{t}': falta el schema (ej. mercado.{t})\n")
            continue
        schema, tabla = t.split(".", 1)
        print(f"\n-- ── {t} " + "─" * max(0, 60 - len(t)))
        print(ddl(schema, tabla))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
