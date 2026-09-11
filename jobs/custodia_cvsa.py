"""jobs/custodia_cvsa.py — la tenencia según la CAJA DE VALORES, a Postgres.

QUÉ ES Y POR QUÉ EXISTE
=======================
Toda la tenencia del sistema sale hoy de UNA fuente: Aunesa. Nadie la puede
contrastar, así que un descalce se descubre cuando falla una liquidación. CVSA
es el REGISTRO —lo que la Caja tiene anotado a nombre nuestro—, y cuando las dos
difieren la que tiene razón legal es la Caja.

Además trae algo que Aunesa no da: `sub_balance_type`, o sea **qué parte de la
tenencia está trabada** (EMBARGO, BLOCKED_FOR_PLEDGE, PENDING_REDEMPTION…). Un
papel embargado figura en la tenencia y no se puede entregar ni garantizar.

Doc: `docs/BYMA_CUSTODIA.md`. Cliente: `core/byma_custodia.py`.
Writer ÚNICO de `portafolio.custodia_cvsa`.

EL RITMO — cada hora, y no es arbitrario
========================================
El gateway de BYMA declara `cache_milliseconds = 3.600.000`: **cachea su propia
respuesta 60 minutos**. Pedirla más seguido devuelve exactamente lo mismo y
gasta cuota. Una corrida usa ~5 requests (el disparo más los polls del uuid), o
sea ~80 de los 10.000 diarios y ~1,5 MB de los 4.096. Sobra.

CÓMO ESCRIBE, Y LAS DOS GUARDAS QUE IMPORTAN
============================================
Cada corrida REEMPLAZA la foto del día: `DELETE` de la fecha + `INSERT`, en una
transacción. No es upsert a propósito — con upsert, un papel que la cuenta tenía
a las 10 y ya no tiene a las 15 quedaría como fila fantasma, presente acá y
ausente en la Caja, sin que nada falle.

  ⚠️ **GUARDA 1 — una respuesta vacía NO borra la foto.** Si BYMA contesta 200
     con cero filas (pasa temprano, antes de que arme el día), no se escribe
     nada: queda la foto anterior envejeciendo a la vista por `actualizado_at`.
     Sin esto, el primer barrido de la mañana borraría el día entero y la
     pantalla diría «sin tenencia» con total seguridad.

  ⚠️ **GUARDA 2 — una caída de BYMA no toca la base.** `BymaCaido` sale antes
     del DELETE. Lo de ayer queda, que es lo correcto: una foto vieja es
     información, una tabla vacía es una mentira.

LO QUE ESTE JOB NO HACE
=======================
* NO escribe en `tenencia`, `tenencia_live`, `assets`, AuM ni PnL. Solo su tabla.
* NO compara contra Aunesa. Persiste el hecho; comparar es de la vista.
* NO valoriza: CVSA informa NOMINALES.

Uso:
    python -m jobs.custodia_cvsa                 # hoy
    python -m jobs.custodia_cvsa --fecha 2026-09-10   # otro día (máx. 7 atrás)
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from core import byma_custodia as byma
from core.job_runs import JobRunLogger
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# CVSA purga a los 7 días: `balanceDate` más atrás devuelve vacío. Se corta acá
# con un mensaje claro en vez de dejar que el job "ande" y no traiga nada.
DIAS_MAX_ATRAS = 7


def _mapa_codigo_a_unidad() -> dict[str, str]:
    """`assets.codigo_cnv` → `assets.unidad`.

    ⚠️ `codigo_cnv` guarda el código de CVSA (pese al nombre). NO es único: el
    código es por INSTRUMENTO y varios assets pueden compartirlo (AL30 y AL30D
    son el mismo 5921). Con `ORDER BY unidad` la elección es estable corrida a
    corrida — que el mapeo no cambie solo importa más que cuál de los dos gane.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT btrim(codigo_cnv), unidad FROM portafolio.assets "
                    "WHERE codigo_cnv IS NOT NULL AND btrim(codigo_cnv) <> '' "
                    "ORDER BY unidad")
        mapa: dict[str, str] = {}
        for codigo, unidad in cur.fetchall():
            mapa.setdefault(codigo, unidad)
        return mapa


def _cantidad(valor: str | None):
    """`holding` viene como string. Una cantidad ilegible es None, no 0.

    Cero y «no sé» son cosas distintas: un 0 se suma y desaparece en un total,
    un None se ve.
    """
    try:
        return float(str(valor).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def correr(fecha: date) -> dict:
    """Baja la tenencia de CVSA de `fecha` y reemplaza la foto de ese día."""
    stats: dict = {"fecha": fecha.isoformat()}

    # 1. BYMA primero. Si se cae, sale acá y la base no se toca (GUARDA 2).
    filas = byma.holdings(fecha.isoformat())
    stats["filas_byma"] = len(filas)

    if not filas:
        # GUARDA 1: vacío no es "no hay tenencia", es "todavía no la armaron".
        stats["escrito"] = 0
        stats["motivo"] = "BYMA devolvió 0 filas: no se toca la foto anterior"
        logger.warning("custodia_cvsa %s: 0 filas, no se escribe nada", fecha)
        return stats

    # 2. Traducir el código de la Caja a nuestro instrumento.
    mapa = _mapa_codigo_a_unidad()
    stats["assets_con_codigo"] = len(mapa)

    registros = []
    sin_cuenta = 0
    sin_unidad = set()
    for f in filas:
        id_cta = byma.id_cuenta(f.get("accountNumber", ""))
        if not id_cta:
            # Un accountNumber con otra forma no se adivina: se cuenta y se mira.
            sin_cuenta += 1
            continue
        cvsa_id = (f.get("cvsaIdentifier") or "").strip()
        unidad = mapa.get(cvsa_id)
        if not unidad:
            sin_unidad.add(cvsa_id)
        registros.append((
            fecha, id_cta, cvsa_id,
            (f.get("subBalanceType") or "").strip() or "SIN_ESTADO",
            _cantidad(f.get("holding")), unidad,
            f.get("accountNumber"), f.get("identAccountComposite"),
        ))

    stats["sin_cuenta_reconocible"] = sin_cuenta
    stats["codigos_sin_asset"] = len(sin_unidad)
    stats["cuentas"] = len({r[1] for r in registros})

    # 3. DELETE + INSERT de la fecha, en UNA transacción: o queda la foto nueva
    #    entera o queda la vieja entera. Nunca una mezcla de dos horas.
    #    La PK absorbe el caso de que BYMA repita una fila (visto en la muestra).
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM portafolio.custodia_cvsa WHERE fecha = %s", (fecha,))
        cur.executemany(
            "INSERT INTO portafolio.custodia_cvsa "
            "(fecha, id_cuenta, cvsa_id, sub_balance_type, cantidad, unidad, "
            " account_number, ident_composite) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (fecha, id_cuenta, cvsa_id, sub_balance_type) DO UPDATE "
            "SET cantidad = EXCLUDED.cantidad, unidad = EXCLUDED.unidad, "
            "    actualizado_at = now()",
            registros)
        conn.commit()

    stats["escrito"] = len(registros)
    logger.info("custodia_cvsa %s: %d filas, %d cuentas, %d códigos sin asset",
                fecha, len(registros), stats["cuentas"], len(sin_unidad))
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Tenencia de CVSA → Postgres.")
    ap.add_argument("--fecha", help="YYYY-MM-DD (default: hoy). Máx. 7 días atrás.")
    args = ap.parse_args()

    fecha = date.fromisoformat(args.fecha) if args.fecha else date.today()
    if fecha < date.today() - timedelta(days=DIAS_MAX_ATRAS):
        print(f"❌ {fecha} está a más de {DIAS_MAX_ATRAS} días: CVSA ya la purgó "
              "y va a devolver vacío. No tiene sentido pedirla.")
        return 2

    with JobRunLogger("custodia_cvsa") as run:
        stats = correr(fecha)
        run.stats.update(stats)
        if not stats.get("escrito"):
            run.error(stats.get("motivo", "no se escribió nada"))
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
