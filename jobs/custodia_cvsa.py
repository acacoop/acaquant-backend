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
from core import custodia_escritura
from core.job_runs import JobRunLogger

logger = logging.getLogger(__name__)

# CVSA purga a los 7 días: `balanceDate` más atrás devuelve vacío. Se corta acá
# con un mensaje claro en vez de dejar que el job "ande" y no traiga nada.
DIAS_MAX_ATRAS = 7


def correr(fecha: date) -> dict:
    """Baja la tenencia de CVSA de `fecha` y reemplaza la foto de ese día.

    ⚠️ BYMA primero, base después. Si BYMA se cae, `BymaCaido` sale ACÁ y la base
    no se toca: lo de ayer queda. Una foto vieja es información; una tabla vacía
    es una mentira.

    La persistencia está en `core/custodia_escritura.py` porque tiene otro
    llamador —el endpoint de ingesta que usa la PC con Okta— y las dos tienen
    que comportarse idéntico.
    """
    filas = byma.holdings(fecha.isoformat())
    return custodia_escritura.guardar(fecha, filas)


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
