"""Job de aranceles — pega a Aunesa /informes y completa los boletos sin arancel.

Encadenado al cron de `jobs.negocio_movimientos` (cada hora L-V 15-22 UTC en
`deploy/crontab.txt`): primero corre negocio_movimientos (que inserta boletos
nuevos sin arancel), inmediatamente después corre este job, que rellena el
arancel de los últimos 7 días.

Ventana de 7 días = robusta a:
- Liquidación T+2 que tarda más de lo previsto.
- Reintento automático de cuentas que fallaron por timeout.
- Cambios retroactivos eventuales en Aunesa.

Reusa la lógica core de `api/services/aunesa_aranceles.py` (la misma que usa
el endpoint POST /api/manager/aunesa/boletos/backfill desde la UI). Cero
duplicación.

Tracking:
- Manager.JobRuns: 1 doc por corrida, tipo="aranceles". Si hay errores no
  fatales (cuentas que fallaron tras reintento), status=partial → alerta
  Telegram automática (vía JobRunLogger).
- También deja un doc en Manager.AranceelesJobRuns con actor="cron@aranceles"
  para que aparezca en el HISTORIAL de la UI (BOLETOS → BACKFILL).

Uso (manual, para probar):
    python -m jobs.aranceles                          # default: últimos 7 días, APPLY
    python -m jobs.aranceles --dias 30                # ventana custom
    python -m jobs.aranceles --desde 2026-01-01 --hasta 2026-05-29
    python -m jobs.aranceles --dry-run                # no escribe (debug)
"""
from __future__ import annotations

import argparse
import uuid
from datetime import UTC, date, datetime, timedelta

from api.services.aunesa_aranceles import run_backfill
from core.job_runs import JobRunLogger
from core.mongo import get_mongo_client

# Ventana default — cubre liquidación T+2 con margen para reintentos y
# cambios retroactivos. Si Aunesa nunca rebote, podés bajar a 3.
_DIAS_DEFAULT = 7

# Workers contra Aunesa. Bajo para no estresar el endpoint cuando corre
# cada hora — la UI usa 6 por default, pero la UI se dispara una vez por
# uso, este job se dispara N veces por día.
_WORKERS = 4

# Colección que comparte la UI para el HISTORIAL. El doc del cron también
# vive acá (con actor="cron@aranceles") para que se vea en BOLETOS→BACKFILL.
_UI_JOBS_COL = "AranceelesJobRuns"
_CRON_ACTOR = "cron@aranceles"


def main() -> int:
    ap = argparse.ArgumentParser(description="Job nightly de aranceles.")
    ap.add_argument("--dias", type=int, default=_DIAS_DEFAULT,
                    help=f"ventana en días desde hoy (default {_DIAS_DEFAULT}).")
    ap.add_argument("--desde", default=None,
                    help="override exacto: YYYY-MM-DD (ignora --dias).")
    ap.add_argument("--hasta", default=None,
                    help="override exacto: YYYY-MM-DD (ignora --dias, default hoy).")
    ap.add_argument("--workers", type=int, default=_WORKERS,
                    help=f"threads paralelos contra Aunesa (default {_WORKERS}).")
    ap.add_argument("--dry-run", action="store_true",
                    help="no escribe Mongo (útil para auditar).")
    args = ap.parse_args()

    hasta_d = date.fromisoformat(args.hasta) if args.hasta else date.today()
    desde_d = date.fromisoformat(args.desde) if args.desde else hasta_d - timedelta(days=args.dias)
    apply = not args.dry_run

    with JobRunLogger("aranceles") as run:
        run.log(f"Rango {desde_d}..{hasta_d} | workers={args.workers} | "
                f"modo={'APPLY' if apply else 'DRY-RUN'}")

        # Doc en la colección que usa la UI para HISTORIAL. _id es un UUID
        # propio del cron — no se mezcla con los UUID del POST endpoint.
        ui_job_id = str(uuid.uuid4())
        ui_col = get_mongo_client()["Manager"][_UI_JOBS_COL]
        ui_doc_base = {
            "_id":           ui_job_id,
            "status":        "running",
            "actor":         _CRON_ACTOR,
            "desde":         desde_d.isoformat(),
            "hasta":         hasta_d.isoformat(),
            "cuentas":       None,
            "workers":       args.workers,
            "apply":         apply,
            "started_at":    datetime.now(UTC),
            "updated_at":    datetime.now(UTC),
            "finished_at":   None,
            "cuentas_total": 0,
            "cuentas_done":  0,
            "stats":         {"inf": 0, "match": 0, "sin_match": 0, "escritos": 0},
            "ejemplos":      [],
            "errores":       [],
            "error":         None,
        }
        try:
            ui_col.insert_one(ui_doc_base)
        except Exception as e:
            # Si no podemos crear el doc UI, seguimos igual — JobRuns sigue siendo
            # la fuente principal de tracking. Solo nos perdemos la visibilidad
            # en la UI para esta corrida.
            run.error(f"no se pudo crear doc en {_UI_JOBS_COL}: {e}")
            ui_job_id = None

        def on_progress(state: dict) -> None:
            if ui_job_id is None:
                return
            try:
                ui_col.update_one(
                    {"_id": ui_job_id},
                    {"$set": {
                        "updated_at":    datetime.now(UTC),
                        "cuentas_total": state["cuentas_total"],
                        "cuentas_done":  state["cuentas_done"],
                        "stats": {
                            "inf":       state["inf"],
                            "match":     state["match"],
                            "sin_match": state["sin_match"],
                            "escritos":  state["escritos"],
                        },
                        "ejemplos": state["ejemplos"],
                        "errores":  state["errores"],
                    }},
                )
            except Exception as e:
                # No tumbar el job por un fallo en la actualización del doc UI.
                # JobRuns sigue capturando todo lo importante.
                run.error(f"actualizando {_UI_JOBS_COL}: {e}")

        try:
            state = run_backfill(
                desde=desde_d, hasta=hasta_d, cuentas=None,
                workers=args.workers, apply=apply,
                on_progress=on_progress, progress_every=10,
            )
        except Exception as e:
            # Excepción fatal → JobRunLogger marca error y dispara Telegram.
            # Cerramos el doc UI con status=error antes de re-raise.
            if ui_job_id is not None:
                try:
                    ui_col.update_one(
                        {"_id": ui_job_id},
                        {"$set": {"status": "error", "error": str(e),
                                  "finished_at": datetime.now(UTC)}},
                    )
                except Exception:
                    pass
            raise

        # Stats al JobRunLogger (los persiste a Manager.JobRuns).
        run.set_stat("cuentas_total", state["cuentas_total"])
        run.set_stat("informes_obtenidos", state["inf"])
        run.set_stat("match", state["match"])
        run.set_stat("sin_match", state["sin_match"])
        run.set_stat("escritos", state["escritos"])
        run.set_stat("errores", len(state["errores"]))
        run.set_stat("apply", apply)
        run.set_stat("desde", desde_d.isoformat())
        run.set_stat("hasta", hasta_d.isoformat())

        # Marcamos errores no-fatales como partial → Telegram automático.
        for e in state["errores"]:
            run.error(f"cuenta {e['cuenta']}: {e['error']}")

        run.log(f"Resumen: informes={state['inf']} match={state['match']} "
                f"sin_match={state['sin_match']} escritos={state['escritos']} "
                f"errores_cuentas={len(state['errores'])}")

        # Cerramos el doc UI como done — el HISTORIAL de la vista lo va a mostrar.
        if ui_job_id is not None:
            try:
                ui_col.update_one(
                    {"_id": ui_job_id},
                    {"$set": {"status": "done", "finished_at": datetime.now(UTC)}},
                )
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
