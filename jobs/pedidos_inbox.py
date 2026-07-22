"""jobs/pedidos_inbox.py — aplica las aprobaciones que llegan por Telegram.

La otra mitad de `jobs/pedidos_triage.py`: ese manda el digest con botones,
este recoge los taps y mueve el estado del pedido. Corre por cron cada minuto
(barato: si no hay taps es una sola llamada HTTP y termina).

POR QUÉ UN CRON Y NO UN SERVICIO SIEMPRE PRENDIDO
El server CONSULTA a Telegram (`getUpdates`), no al revés: no hay webhook, no
se abre ningún puerto y no hay proceso que se pueda colgar y haya que vigilar.
Telegram guarda los updates 24h, así que si el server estuvo caído las
aprobaciones se aplican igual cuando vuelve. Un minuto de latencia para tocar
un botón no le importa a nadie.

LO ÚNICO QUE PUEDE PASAR
Un tap es un voto, no un comando: el `callback_data` tiene la forma
`pedido:<aceptar|descartar>:<id>` y cualquier cosa que no matchee EXACTAMENTE
ese patrón se descarta sin mirar. No se interpreta texto libre, no se arma SQL
con el contenido, y los mensajes de texto que le manden al bot ni se leen
(`allowed_updates=["callback_query"]`).

QUIÉN PUEDE
`TELEGRAM_ADMIN_IDS` (ids numéricos de Telegram, coma-separados en el .env) +
el chat configurado. Vacío = nadie: default-deny. A un tap no autorizado se le
contesta que no puede y se registra quién fue — así el propio bot te dice el id
que tenés que agregar la primera vez.

Uso:
    python -m jobs.pedidos_inbox            # aplica lo que haya (cron cada 1')
    python -m jobs.pedidos_inbox --dry-run  # muestra los taps SIN aplicar nada
"""
from __future__ import annotations

import argparse
import logging
import re

from core.job_runs import JobRunLogger
from core.notify import leer_taps, responder_tap
from core.postgres import get_pool

logger = logging.getLogger(__name__)

TIPO_JOB = "pedidos_inbox"
_CURSOR_ID = "pedidos"

# La jaula: solo esta forma exacta se interpreta. Todo lo demás se ignora.
_DATO_RE = re.compile(r"^pedido:(aceptar|descartar):(\d{1,12})$")
_ESTADO = {"aceptar": "aceptado", "descartar": "descartado"}


def _leer_cursor(cur) -> int | None:
    cur.execute("SELECT offset_ FROM manager.telegram_cursor WHERE id = %s", (_CURSOR_ID,))
    row = cur.fetchone()
    return int(row[0]) if row else None


def _guardar_cursor(cur, offset: int) -> None:
    cur.execute(
        "INSERT INTO manager.telegram_cursor (id, offset_, ts) VALUES (%s, %s, now()) "
        "ON CONFLICT (id) DO UPDATE SET offset_ = EXCLUDED.offset_, ts = now()",
        (_CURSOR_ID, offset))


def _aplicar(cur, pedido_id: int, estado: str, quien: str) -> str:
    """Mueve el estado. Devuelve el texto que se le muestra a quien tocó.

    Idempotente y honesto: si el pedido ya estaba decidido no lo pisa (dos taps
    seguidos, o el mismo mensaje leído dos veces, no cambian nada) y lo dice."""
    cur.execute("SELECT estado, titulo FROM manager.pedidos WHERE id = %s", (pedido_id,))
    row = cur.fetchone()
    if not row:
        return f"#{pedido_id} no existe"
    actual, titulo = row[0], row[1]
    if actual == estado:
        return f"#{pedido_id} ya estaba {estado}"
    if actual == "hecho":
        return f"#{pedido_id} ya está hecho — no se cambia"
    cur.execute(
        "UPDATE manager.pedidos SET estado = %s, decidido_por = %s, decidido_at = now() "
        "WHERE id = %s", (estado, f"telegram:{quien}", pedido_id))
    corto = (titulo or "")[:40]
    return (f"✅ #{pedido_id} aceptado — entra a la cola" if estado == "aceptado"
            else f"❌ #{pedido_id} descartado") + (f" · {corto}" if corto else "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="muestra los taps pendientes SIN aplicarlos ni consumirlos")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    with JobRunLogger(TIPO_JOB) as run, get_pool().connection() as conn:
        with conn.cursor() as cur:
            offset = _leer_cursor(cur)
        taps, nuevo_offset = leer_taps(offset)
        if not taps:
            run.set_stat("aplicados", 0)
            return

        if args.dry_run:
            for t in taps:
                estado = "autorizado" if t["autorizado"] else "NO autorizado"
                run.log(f"  tap {t['dato']!r} de {t['quien']} ({estado})")
            run.log("--dry-run: no se aplicó nada y los taps siguen pendientes.")
            return

        aplicados = 0
        for t in taps:
            if not t["autorizado"]:
                # Se le contesta y se deja constancia: la primera vez, este log
                # es de dónde sale el id que hay que poner en TELEGRAM_ADMIN_IDS.
                run.log(f"tap IGNORADO de telegram id {t['quien']!r} "
                        "(no está en TELEGRAM_ADMIN_IDS)")
                responder_tap(t["callback_id"],
                              f"No tenés permiso para aprobar pedidos. Tu id: {t['quien']}")
                continue
            m = _DATO_RE.match(t["dato"])
            if not m:
                run.log(f"tap con dato no reconocido: {t['dato']!r}")
                continue
            accion, pid = m.group(1), int(m.group(2))
            with conn.cursor() as cur:
                msg = _aplicar(cur, pid, _ESTADO[accion], t["quien"])
            conn.commit()
            responder_tap(t["callback_id"], msg)
            run.log(msg)
            aplicados += 1

        # El cursor se guarda AL FINAL y solo si se procesó todo: si el job se
        # corta a la mitad, Telegram vuelve a entregar los taps y se re-aplican
        # (son idempotentes) en vez de perderse.
        if nuevo_offset is not None:
            with conn.cursor() as cur:
                _guardar_cursor(cur, nuevo_offset)
            conn.commit()
        run.set_stat("aplicados", aplicados)


if __name__ == "__main__":
    main()
