"""jobs/estrategia_resolver.py — resuelve el RESULTADO de cada señal ESTRATEGIA QUANT.

Doc vivo: docs/ESTRATEGIA_QUANT.md.

Por qué corre INTRADÍA (cada 5' durante la rueda, cron): el camino de precios
está en mercado.cedears_time_sales, que SE VACÍA al cierre (cleanup). Si el
resolver esperara a la noche, la evidencia ya no existiría — la señal quedaría
sin etiquetar PARA SIEMPRE. Por eso etiqueta apenas vence cada horizonte.

Para cada señal de hoy cuyo horizonte (15/30/60 min) ya venció y no tiene
resultado:
  - lee los cierres por minuto POSTERIORES al ts de la señal (sin look-ahead:
    jamás mira datos previos ni recalcula el score),
  - ret_pct DIRECCIONAL al horizonte (signo de la dirección de la señal:
    positivo = la señal ganó),
  - MFE/MAE: máxima excursión a favor / en contra dentro del horizonte,
  - toco_objetivo: tocó +objetivo antes que −stop (config),
  - gano = ret_pct > 0.

Idempotente: PK (senal_id, horizonte_min) + ON CONFLICT DO NOTHING → re-correr
no duplica ni pisa. Con --cierre (última corrida del día, 20:10 UTC) resuelve
también las señales cuyo horizonte NO llegó a vencer, marcadas parcial=true.

Uso:
    python -m jobs.estrategia_resolver            # horizontes vencidos
    python -m jobs.estrategia_resolver --cierre   # + parciales de fin de rueda
"""
from __future__ import annotations

import sys
from datetime import timedelta

from config import (
    ESTRATEGIA_HORIZONTES,
    ESTRATEGIA_OBJETIVO_PCT,
    ESTRATEGIA_STOP_PCT,
)
from core import estrategia_sql as db
from core.job_runs import JobRunLogger


def medir(
    precio_senal: float,
    direccion: str,
    minutos: list[tuple],  # [(datetime, close)] asc, posteriores a la señal
    hasta_min: int,
) -> dict | None:
    """MFE/MAE/retorno direccional dentro de la ventana [señal, señal+hasta_min].

    Direccional: LONG mide (px/precio−1); SHORT lo invierte. None si no hubo
    ningún trade en la ventana (papel sin operar → no se puede etiquetar).
    """
    if not precio_senal or precio_senal <= 0 or not minutos:
        return None
    signo = 1 if direccion == "LONG" else -1
    limite = minutos[0][0] + timedelta(minutes=hasta_min)
    mfe = mae = 0.0
    ultimo = None
    toco_obj = None
    for ts, px in minutos:
        if ts > limite:
            break
        ret = signo * (px / precio_senal - 1) * 100
        mfe = max(mfe, ret)
        mae = min(mae, ret)
        ultimo = ret
        if toco_obj is None:
            if ret >= ESTRATEGIA_OBJETIVO_PCT:
                toco_obj = True
            elif ret <= -ESTRATEGIA_STOP_PCT:
                toco_obj = False
    if ultimo is None:
        return None
    return {
        "ret_pct": round(ultimo, 4),
        "mfe_pct": round(mfe, 4),
        "mae_pct": round(mae, 4),
        "toco_objetivo": toco_obj,
        "gano": ultimo > 0,
    }


def resolver(run: JobRunLogger, *, cierre: bool = False) -> None:
    resueltas = saltadas = 0
    for horizonte in ESTRATEGIA_HORIZONTES:
        pendientes = (db.senales_de_hoy_sin_resultado(horizonte) if cierre
                      else db.senales_pendientes(horizonte))
        for s in pendientes:
            precio = float(s["precio"]) if s["precio"] is not None else None
            if precio is None:
                saltadas += 1
                continue
            minutos = db.minutos_desde(s["ticker"], s["ts"])
            m = medir(precio, s["direccion"], minutos, horizonte)
            if m is None:
                if cierre:
                    # Fin de rueda sin un solo trade posterior → se etiqueta
                    # como sin-dato (ret NULL, parcial) para que no quede
                    # pendiente eterno (mañana el tape ya no existe).
                    db.insertar_resultado(
                        senal_id=s["id"], horizonte_min=horizonte,
                        ret_pct=None, mfe_pct=None, mae_pct=None,
                        toco_objetivo=None, gano=None, parcial=True,
                    )
                    resueltas += 1
                else:
                    saltadas += 1
                continue
            db.insertar_resultado(
                senal_id=s["id"], horizonte_min=horizonte, parcial=cierre,
                **m,
            )
            resueltas += 1
            run.log(f"señal #{s['id']} {s['ticker']} {s['direccion']} "
                    f"h{horizonte}' → ret={m['ret_pct']}% gano={m['gano']}")
    run.set_stat("resueltas", resueltas)
    run.set_stat("saltadas", saltadas)
    run.set_stat("cierre", cierre)


def main() -> None:
    cierre = "--cierre" in sys.argv
    with JobRunLogger("estrategia_resolver") as run:
        resolver(run, cierre=cierre)


if __name__ == "__main__":
    main()
