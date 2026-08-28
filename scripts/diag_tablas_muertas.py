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

import pathlib
import subprocess
import sys
from datetime import UTC, datetime

from agente import peso, tablas
from core import escribe
from core.postgres import get_pool

RAIZ = pathlib.Path(__file__).resolve().parents[1]

# Cuánto silencio hace falta para ETIQUETARLA «congelada». Un trimestre: más que
# cualquier job mensual, así un cierre de trimestre no la marca por dormida.
# Es una etiqueta y NADA MÁS: no filtra qué se imprime. Ver el agujero que abrió
# cuando sí filtraba, documentado en el bloque ② de `main`.
DIAS_CONGELADA = 120


# Carpetas donde vive el sistema. `scripts/` NO cuenta: un script puede
# nombrar una tabla justamente para diagnosticarla o para borrarla.
CODIGO = ("api", "agente", "jobs", "core", "engines", "quant")


def _nombrada(nombre: str) -> int:
    """¿Cuántos archivos del sistema nombran a `schema.tabla`, CALIFICADA?

    ⚠️⚠️ **ESTE EJE EXISTE PORQUE `quien_escribe` NO ALCANZA.** Es un regex
    sobre `INSERT INTO <literal>`, así que **no ve la tabla cuyo nombre vive en
    una variable** — que es como se escribe medio repo:

        _T_EVENTOS   = "manager.salud_eventos"        (api/services/salud.py)
        _TABLE_MANUAL = "mercado.breakevens_manuales" (breakevens_admin.py)

    Medido el 2026-08-28 sobre la primera corrida real: de las **23** tablas que
    el diag propuso dropear, **CUATRO estaban vivas** — `manager.salud_eventos`,
    `mercado.breakevens_manuales`, `ap5.activo_integrado` (¡tema abierto de esta
    semana!) y `agente.avisos_dirigidos`, que **la escribe el agente mismo**
    (`agente/mensajes.py`). Las cuatro, vacías nada más que porque todavía
    nadie las llenó.

    Y se busca el nombre **CALIFICADO** (`agente.avisos_dirigidos`), no el
    pelado, porque el pelado sobra-detecta al revés: las 19 `agente.av_agent_*`
    del agente viejo aparecen por todos lados en PROSA de comentarios que
    cuentan su historia, y con el nombre pelado quedarían vivas para siempre.
    Medido: calificado → 4/4 vivas rescatadas y 19/19 muertas dejadas pasar.

    ⚠️ **Y NO ALCANZA CON EL CALIFICADO.** Segunda corrida real: la tabla viva
    `manager.aranceles_job_runs` lo guarda así —

        _JOBS_TABLE = "aranceles_job_runs"    (api/services/aranceles_jobs.py)

    — en una variable **y sin el schema adelante**. Cero coincidencias con el
    calificado. Así que también se busca el nombre PELADO **entre comillas**,
    que es como se escribe un nombre de tabla en Python y no como se escribe en
    un comentario.

    Ese segundo eje sobre-detecta y se acepta a propósito: medido, `"cartera"`
    da 21 archivos, `"config"` 4, `"snapshots"` y `"pedidos"` 1 — son claves de
    diccionario, no tablas. **Rescatar de más cuesta una tabla de 16 kB que se
    queda; rescatar de menos borra algo que el sistema usa.** El desempate no
    es simétrico, así que se elige el lado barato.

    Lo que igual NO rescata es la prosa: los comentarios citan con acentos
    graves (`av_agent_trazas`), no con comillas — por eso las 18 del agente
    viejo salieron las 18.
    """
    def _n(args: list[str]) -> int:
        try:
            r = subprocess.run(["git", "grep", "-l", *args, "--",
                                *[f"{c}/" for c in CODIGO]],
                               cwd=RAIZ, capture_output=True, text=True, timeout=30)
            return len([ln for ln in r.stdout.splitlines() if ln.strip()])
        except Exception:
            return 0

    pelado = nombre.split(".", 1)[-1]
    return _n(["-F", nombre]) + _n(["-E", f"[\"']{pelado}[\"']"])


def _de_verdad_vacia(schema: str, tabla: str) -> bool:
    """¿Está REALMENTE vacía? Se pregunta a las filas, no a las estadísticas.

    ⚠️⚠️ **`n_live_tup` Y `reltuples` NO SON UN CONTEO.** Son estadísticas del
    autovacuum, y desde Postgres 10 `reltuples = -1` es el centinela de «esta
    tabla NUNCA fue analizada» — que es exactamente lo que muestra
    `limpiar_agente_viejo` en su columna FILAS para las 18 del agente viejo.
    `n_live_tup = 0`, que es de donde salía nuestro «0 filas», tiene la MISMA
    ambigüedad: puede querer decir «vacía» o «nunca se juntó estadística».

    Y sobre esa ambigüedad se apoyaba la única garantía del bloque ①: *borrarlas
    no pierde datos*. Antes de un DROP irreversible eso no alcanza.

    `EXISTS` y no `COUNT(*)`: corta en la primera fila, así que si la estadística
    mintió y la tabla tiene millones, la pregunta igual es barata.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f'SELECT EXISTS (SELECT 1 FROM "{schema}"."{tabla}")')
        return not (cur.fetchone() or [False])[0]


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
            "nombrada": _nombrada(nombre),
        })

    # ── Los montones. El orden es de MÁS a MENOS seguro. ─────────────────────
    # Que el código la NOMBRE saca a una tabla de todo montón de borrado, aunque
    # esté vacía y sin escritor detectado: alguien escribió su nombre a mano.
    huerfana = [f for f in filas if not f["escritores"] and not f["nombrada"]]
    # 1. VACÍA Y HUÉRFANA: 0 filas. Borrarla no puede perder un dato.
    # La estadística sólo elige a QUIÉN preguntarle; el veredicto lo da la tabla.
    muertas = [f for f in huerfana if f["filas"] == 0
               and _de_verdad_vacia(*f["nombre"].split(".", 1))]
    # Y si la estadística decía 0 pero tenía filas, no se pierde: cae acá.
    mintio = [f for f in huerfana if f["filas"] == 0 and f not in muertas]
    # 2. CON DATOS Y HUÉRFANA, y MEDIDA: sabemos cuándo escribió por última vez.
    #    Acá SÍ se puede perder algo → decide una persona.
    #
    #    ⚠️⚠️ **SIN FILTRO POR EDAD, Y ESE FUE UN AGUJERO REAL.** Este bloque
    #    pedía además `edad >= DIAS_CONGELADA`, así que una huérfana con datos y
    #    MENOS de 120 días **no caía en ningún bloque y no se imprimía**.
    #    Es lo que pasó el 2026-08-28 con `agente.av_agent_control` y
    #    `agente.av_agent_latido`: tenían UNA fila cada una, el informe no las
    #    mostró, y de ese silencio se concluyó —en voz alta— que «ya no existen
    #    en la base». Existían.
    #
    #    Un umbral está bien para ORDENAR y para poner una etiqueta; no para
    #    decidir si algo se imprime. Un informe que decide borrados tiene que
    #    poder demostrar que no se le cayó nada — de ahí el `assert` de abajo.
    congeladas = [f for f in huerfana
                  if f["filas"] > 0 and f["edad"] is not None]
    # 2bis. CON DATOS, HUÉRFANA y SIN COLUMNA DE FECHA. ⚠️ Este montón existe
    #    aparte porque la primera versión lo mezclaba con el anterior y les
    #    ponía el cartel «hace ≥120 d que no escriben» — que era MENTIRA: sin
    #    columna de fecha no se sabe cuándo escribieron. Es el mismo invariante
    #    que rige adentro del agente: una corrida que no pudo mirar no cierra
    #    nada. No puedo medirlo ≠ está muerta.
    sin_medir = [f for f in huerfana if f["filas"] > 0 and f["edad"] is None] + mintio

    # EL CANDADO: los tres montones tienen que cubrir a TODAS las huérfanas.
    # Sin esto, un filtro nuevo vuelve a abrir el agujero de arriba en silencio.
    cubiertas = {f["nombre"] for f in muertas + congeladas + sin_medir}
    perdidas = sorted({f["nombre"] for f in huerfana} - cubiertas)
    assert not perdidas, f"huérfanas que no caen en ningún bloque: {perdidas}"
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
            if f["edad"] is not None and f["edad"] >= DIAS_CONGELADA:
                marca = "  · congelada" + marca
            print(f"      {f['nombre']:46} {f['filas']:>9,} {_mb(f['bytes']):>10}"
                  f"  {cuando}{marca}")
        print(f"\n      → juntas pesan {_mb(sum(f['bytes'] for f in items))}\n")

    bloque("① VACÍAS Y HUÉRFANAS — candidatas a DROP",
           "Vacías CONFIRMADO contra la tabla (no contra la estadística), nadie\n"
           "      las escribe y NINGÚN archivo del sistema las nombra.",
           muertas)
    bloque("② CON DATOS Y HUÉRFANAS — con fecha de última escritura",
           "Alguien las llenó y ya nadie las toca. ⚠ Acá SÍ se puede perder algo:\n"
           "      mirá QUÉ son antes de decidir. No hay DROP automático para éstas.",
           congeladas)
    bloque("②bis CON DATOS Y HUÉRFANAS — pero SIN COLUMNA DE FECHA",
           "No sé cuándo escribieron por última vez, así que NO digo que estén\n"
           "      congeladas. Suelen ser catálogos que se cargan a mano.",
           sin_medir)
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
