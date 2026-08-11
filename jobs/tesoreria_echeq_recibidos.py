"""tesoreria_echeq_recibidos.py — espejo automático de los DEPÓSITOS de cheque
hacia Tesorería → tab CHEQUES → RECIBIDOS.

QUÉ RESUELVE. Hasta ahora los cheques RECIBIDOS eran 100% carga manual: el back
office tipeaba en la app algo que Aunesa ya tenía registrado. Los EMITIDOS ya
tenían espejo (`tesoreria.sincronizar_echeq_emitidos`); este es el gemelo del
otro lado.

POR QUÉ UN CRON Y NO EL POLL DE LA VISTA. Los depósitos NO vienen por el feed
bancario que usa Tesorería (`consultaMovDocsSolicitados`): medido el 2026-08-10,
ese feed dio 0 depósitos e-cheq en 3 días contra 20 extracciones. Vienen por el
feed del COMITENTE (`consolidadosGenerales`), que devuelve TODOS los boletos del
día —miles de filas—: pagarlo en cada poll de 20s haría inusable la pantalla.

LA VENTANA HORARIA NO ES CAPRICHO. El back office carga los depósitos en Aunesa
**a la mañana siguiente, con la fecha del día anterior**: el 11/08 a la mañana
entran los depósitos fechados 10/08, y la plata llega al banco el 11/08. Por eso
el cron corre de 9 a 14 ART y mira los últimos días hábiles, no solo hoy. Es el
mismo motivo por el que `jobs/cashflow.py` perdía los depósitos: corría a las 23
del día D mirando el día D, cuando todavía no existían.

QUÉ CREA. Una fila `lado='recibido'`, `origen='aunesa'` (la vista le pone el chip
AUTO), con comitente, CUIT del padrón, importe, moneda y `fecha_pago` = próximo
día hábil. Nace **SIN BANCO** —Aunesa no manda la cuenta operativa— y en estado
`pendiente`: la plata entra al saldo recién cuando el back office le asigna el
banco y la marca `finalizado`. El espejo no mueve un peso solo.

SOLO ESPEJA E-CHEQ (`tesoreria.ESPEJO_TIPOS_RECIBIDO`). Un "Depósito de cheques"
de papel es un LOTE —una fila de Aunesa por N cheques que el equipo registra de a
uno— y espejarlo sumaría una fila encima de las individuales, duplicando el
ingreso al finalizarlas. Sumar 'fisico' es una línea, pero primero hay que
confirmar cómo los carga el equipo.

NO REEMPLAZA LA CARGA MANUAL: el botón «+ NUEVO» sigue igual, para lo que no
venga por la API.

Idempotente por `mov_id` (= `dep:<comprobante>`): re-correrlo no duplica ni pisa
lo que hayan editado.

Uso (desde la raíz):
    python -m jobs.tesoreria_echeq_recibidos --dry     # no escribe: muestra qué crearía
    python -m jobs.tesoreria_echeq_recibidos
    python -m jobs.tesoreria_echeq_recibidos --fecha 2026-08-10
    python -m jobs.tesoreria_echeq_recibidos --dias 5
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime, timedelta

sys.path.insert(0, ".")

# Días hábiles hacia atrás que se revisan en cada corrida (hoy incluido). Cargan
# con fecha de AYER, y si un día se atrasan hay que seguir alcanzándolos → 3.
_LOOKBACK_HABILES = 2  # + hoy = 3 días


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fecha", help="YYYY-MM-DD; procesa SOLO ese día")
    ap.add_argument("--dias", type=int, default=_LOOKBACK_HABILES + 1,
                    help="días hábiles a revisar, hoy incluido (default 3)")
    ap.add_argument("--dry", action="store_true",
                    help="no escribe: lista los cheques que crearía")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("tesoreria_echeq_recibidos")

    from api.services.tesoreria import sincronizar_depositos_recibidos
    from core.calendario import ultimos_habiles

    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()  # ART
    if args.fecha:
        try:
            dias = [datetime.strptime(args.fecha, "%Y-%m-%d").date()]
        except ValueError:
            print(f"--fecha mal formada: {args.fecha}")
            return 1
    else:
        dias = ultimos_habiles(hoy, max(0, args.dias - 1))

    from core.job_runs import JobRunLogger
    with JobRunLogger("tesoreria_echeq_recibidos") as jr:
        tot_cand = tot_creados = 0
        for i, d in enumerate(dias):
            try:
                res = sincronizar_depositos_recibidos(dia=d, dry=args.dry)
            except Exception as e:  # un día que falle no puede tumbar los otros
                log.error("%s: %s", d, str(e).splitlines()[0][:200])
                continue
            tot_cand += res["candidatos"]
            tot_creados += res["creados"]
            log.info("%s → %d depósito(s) de cheque, %d fila(s) nueva(s)%s",
                     d, res["candidatos"], res["creados"], "  [DRY]" if args.dry else "")
            for f in res["filas"]:
                log.info("      %s  %s %s  %-9s  pago=%s  %s",
                         f["mov"], f["unidad"], f"{f['importe']:,.2f}", f["tipo"],
                         f["fp"], (f["denom"] or "")[:40])
            if i < len(dias) - 1:
                time.sleep(2)  # throttle suave entre días (REGLA #4)
        jr.set_stat("dias", len(dias))
        jr.set_stat("candidatos", tot_cand)
        jr.set_stat("creados", tot_creados)
        jr.set_stat("dry", bool(args.dry))

    print(f"\n→ {len(dias)} día(s) · {tot_cand} depósito(s) de cheque · "
          f"{tot_creados} fila(s) creada(s){' [DRY — no se escribió]' if args.dry else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
