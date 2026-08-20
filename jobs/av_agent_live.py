"""jobs/av_agent_live.py — EL AGENTE, PRENDIDO EN RUEDA.

Doc madre: **`docs/AV_AGENT.md`** §0.k.

Pedido del user (2026-08-18, en plena rueda): *«necesito que esté prendido el
agente… al menos de 10:30 a 17. Porque por ej. el AO29 no está con precio, o sea
no se suscribió, y quisiera saberlo en rueda. El GD46 está con el precio de ARS
en la curva USD»*.

**Por qué esto no es "el mismo agente pero más seguido".** Los dos hallazgos que
busca **no existen de noche**: que un símbolo no tenga precio a las 11 de la
mañana es un problema, a las 3 de la madrugada es lo normal. Su verdad depende de
la hora, y por eso viven en detectores propios (`detectar_sin_precio` y
`detectar_precio_fuera_de_moneda`) y en su propio job.

**Cero red, cero créditos.** `relevar()` censa 1816 (~29 créditos) y corre una
vez por noche; esto corre cada 5 minutos y no puede pagar nada. Juntarlos habría
obligado a elegir entre monitorear seguido o no quemar la cuota del día antes del
mediodía.

**Idempotente y sin histórico.** Cada corrida REEMPLAZA los hallazgos live
(`alcance='live'`). Un hallazgo de rueda es una foto del momento: acumularlos
dejaría 84 avisos de "AO29 sin precio" al final del día, uno por corrida, y el
que mire no sabría si sigue pasando o pasó a las 10:05. La historia de las
transiciones es de SALUD; esto contesta «¿qué está mal AHORA?».

    python -m jobs.av_agent_live          # una pasada
    python -m jobs.av_agent_live --ver    # sin escribir, imprime lo que ve
"""
from __future__ import annotations

import argparse
import logging

from core.job_runs import JobRunLogger

logger = logging.getLogger(__name__)


def _guardar(hallazgos: list[dict]) -> int:
    """Reemplaza los hallazgos live. El INSERT vive en `av_agent` porque el
    monitor de sistema escribe igual: dos copias de la misma transacción se
    separan el día que una cambia."""
    from api.services import av_agent

    return av_agent.reemplazar_hallazgos("live", hallazgos)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ver", action="store_true",
                    help="no escribe: imprime lo que ve (para mirar a mano)")
    args = ap.parse_args()

    from api.services import av_agent

    if args.ver:
        r = av_agent.relevar_live()
        print(f"{r['bonos']} bonos · {r['con_snapshot']} con snapshot · "
              f"MEP {r['mep']}")
        for h in r["hallazgos"]:
            print(f"  [{h['severidad']}] {h['ticker']:<8} {h['regla']:<26} {h['motivo']}")
        print(f"\n{len(r['hallazgos'])} hallazgos (NO se escribió nada)")
        return

    with JobRunLogger("av_agent_live") as jr:
        r = av_agent.relevar_live()

        # ⚠️ **ANTES de pisar**: la corrida anterior sigue en la tabla hasta que
        # `reemplazar_hallazgos` haga su DELETE, así que este es el único momento
        # en que se puede saber QUÉ SE ARREGLÓ. Sin esto, una recuperación es una
        # fila que deja de escribirse — o sea, silencio (§0.ah).
        from api.services.av_agent_recuperados import detectar_recuperados
        volvieron = detectar_recuperados(r["hallazgos"])
        if volvieron:
            r["hallazgos"] = list(r["hallazgos"]) + volvieron

        # ⚠️ **LO QUE SE PIDIÓ Y TODAVÍA NO CONTESTÓ** (§0.ak). Una pata recién
        # suscripta no puede tener precio: el motor la levanta a los 5 s y la
        # punta la pone el mercado cuando quiere. Acá se releen las propuestas
        # que quedaron `esperando` y se cierran las que ya tienen respuesta —
        # incluida la respuesta «no cotiza», que es la que le saca el tema de
        # encima al que mira la pantalla. Va en este job porque la espera se
        # mide en tiempo de RUEDA, y este es el único que corre con el mercado
        # abierto.
        from api.services.av_agent_respuesta import revisar
        respuestas = revisar()
        if respuestas:
            r["hallazgos"] = list(r["hallazgos"]) + respuestas

        n = _guardar(r["hallazgos"])
        por_regla: dict[str, int] = {}
        for h in r["hallazgos"]:
            por_regla[h["regla"]] = por_regla.get(h["regla"], 0) + 1
        jr.set_stat("hallazgos", n)
        jr.set_stat("bonos", r["bonos"])
        for regla, c in por_regla.items():
            jr.set_stat(regla, c)
        if volvieron:
            jr.set_stat("recuperados", len(volvieron))
        jr.log(f"{n} hallazgos live · {r['bonos']} bonos · MEP {r['mep']} · "
               + (", ".join(f"{k}={v}" for k, v in por_regla.items()) or "nada")
               + (f" · VOLVIERON {len(volvieron)}" if volvieron else ""))


if __name__ == "__main__":
    main()
