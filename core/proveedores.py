"""core/proveedores.py — LOS DE AFUERA SE CAEN, Y HAY QUE ENTERARSE.

Doc madre: **`docs/AV_AGENT.md`** §0.ad.

Pedido del user (2026-08-20), con Aunesa devolviendo HTTP 500 en su login
mientras lo escribía: *«esto es una funcionalidad que la vi de milagro… sí o sí
el agente tiene que detectar cuándo esto está caído, avisar y dar el motivo
exacto»*.

**Lo que fallaba no era la detección.** La vista de Tesorería ya captura el error
de Aunesa y lo muestra en un cartel rojo con el mensaje exacto — está bien hecho
y degrada como corresponde. Lo que falla es **cuándo**: ese cartel existe *solo
mientras alguien tiene la pantalla abierta*. Si nadie entra, nadie sabe; y el
back office puede pasar una mañana entera creyendo que el saldo del día está
completo cuando le falta la mitad. Es el mismo patrón que ya se corrigió con
SALUD y con la latencia: **una señal que te espera no es un aviso**.

CÓMO SE ENTERA, Y POR QUÉ NO SE PREGUNTA
========================================

La tentación es que el agente le pegue cada 5 minutos a cada proveedor. No:

  · **1816 cobra por llamada.** Un chequeo de salud cada 5 minutos se come la
    cuota del día antes del mediodía.
  · **Un health check puede mentir.** Un proveedor que contesta el ping y
    devuelve 500 en el endpoint que usamos de verdad sale VERDE.

Así que al revés: **cada llamada real deja su rastro**. Los daemons ya le pegan a
Aunesa todo el tiempo (`control_saldos`, `tenencia_live`), así que una caída
queda registrada en segundos, sin una sola llamada extra y con el error REAL del
endpoint que importa.

Se escribe **solo cuando falla** (y como mucho una vez por minuto por proceso).
Un proveedor sano no cuesta ni una escritura.

⚠️ **Y POR ESO EL HALLAZGO VENCE.** Si el proveedor se recupera, los fallos dejan
de anotarse — no hay nada que "apague" el registro. El detector mira que el
último fallo sea RECIENTE: sin eso, un 500 de la semana pasada seguiría en la
pantalla para siempre, que es exactamente lo que pasó con los hallazgos de rueda
(§0.u). El registro que envejece se apaga solo.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Proveedor:
    nombre: str      # cómo se lo llama en voz alta
    host: str        # para reconocerlo en una URL
    rompe: str       # QUÉ deja de andar — es la mitad útil del aviso


# El catálogo. Sumar uno es UNA línea, y lo que importa de cada entrada es
# `rompe`: sin eso el aviso dice «se cayó X» y el que lo lee tiene que adivinar
# si eso le arruina el día o no le toca en nada.
PROVEEDORES: dict[str, Proveedor] = {
    "aunesa": Proveedor(
        "Aunesa (el custodio)", "aca.aunesa.com",
        "Tesorería se queda sin los movimientos del día (ingresos, egresos y "
        "saldo final incompletos), y no se actualizan los saldos liquidados, "
        "la tenencia del día ni los informes de operaciones. Lo cargado a mano "
        "—saldos, cheques, mercados, banco a banco, registros y VEPs— sí está"),
    "1816": Proveedor(
        "1816 (market data)", "api.1816.com.ar",
        "no entran precios de referencia, la ficha de emisores ni la tasa TAMAR "
        "de los duales; las curvas siguen andando con Primary"),
    "interbanking": Proveedor(
        "Interbanking", "interbanking.com.ar",
        "no se actualizan saldos ni extractos bancarios"),
    "bcra": Proveedor(
        "BCRA", "api.bcra.gob.ar",
        "no entra el CER del día (y sin CER forward, los bonos CER quedan con "
        "el ajuste de ayer)"),
}

# No se escribe más seguido que esto por proveedor y por proceso. Un daemon
# pegándole a un proveedor caído reintenta muchas veces por minuto: sin freno,
# una caída de una hora son miles de escrituras para decir siempre lo mismo.
CADA_S = 60

_lock = threading.Lock()
_ultimo: dict[str, tuple[float, bool]] = {}      # proveedor → (cuándo, estaba_ok)


def de_url(url: str) -> str | None:
    """Qué proveedor es esa URL, o None si no es de ninguno conocido."""
    u = (url or "").lower()
    for clave, p in PROVEEDORES.items():
        if p.host in u:
            return clave
    return None


def anotar(proveedor: str, *, ok: bool, error: str = "", donde: str = "") -> None:
    """Deja constancia de cómo contestó. **Nunca levanta.**

    Esto se llama desde adentro de un cliente HTTP, o sea en el camino de una
    request real. Si fallara, rompería la llamada que estaba tratando de
    describir — el instrumento no puede ser la causa de la falla.
    """
    if proveedor not in PROVEEDORES:
        return
    ahora = time.time()
    with _lock:
        cuando, antes_ok = _ultimo.get(proveedor, (0.0, True))
        # Un OK solo se escribe si veníamos de un fallo: eso es la RECUPERACIÓN,
        # que sí es información. Un OK detrás de otro OK no dice nada nuevo y
        # costaría una escritura por request.
        if ok and antes_ok:
            return
        # Un fallo detrás de otro fallo, dentro del minuto, tampoco: es la misma
        # caída contada de nuevo.
        if not ok and not antes_ok and ahora - cuando < CADA_S:
            return
        _ultimo[proveedor] = (ahora, ok)
    try:
        _guardar(proveedor, ok=ok, error=error[:400], donde=donde[:120])
    except Exception as e:                                  # nunca hacia arriba
        logger.debug("proveedores: no pude anotar %s (%s)", proveedor, e)


def _guardar(proveedor: str, *, ok: bool, error: str, donde: str) -> None:
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO manager.proveedor_estado
                   (proveedor, ok, ultimo_error, ultimo_error_at, ultimo_ok_at,
                    fallos_seguidos, donde, actualizado_at)
            VALUES (%(p)s, %(ok)s,
                    CASE WHEN %(ok)s THEN NULL ELSE %(err)s END,
                    CASE WHEN %(ok)s THEN NULL ELSE now() END,
                    CASE WHEN %(ok)s THEN now() ELSE NULL END,
                    CASE WHEN %(ok)s THEN 0 ELSE 1 END,
                    %(donde)s, now())
            ON CONFLICT (proveedor) DO UPDATE SET
                ok = EXCLUDED.ok,
                -- El error se PISA con el último: el de hoy describe la caída de
                -- hoy. El histórico de caídas no es de esta tabla.
                ultimo_error    = EXCLUDED.ultimo_error,
                ultimo_error_at = COALESCE(EXCLUDED.ultimo_error_at,
                                           manager.proveedor_estado.ultimo_error_at),
                ultimo_ok_at    = COALESCE(EXCLUDED.ultimo_ok_at,
                                           manager.proveedor_estado.ultimo_ok_at),
                fallos_seguidos = CASE WHEN EXCLUDED.ok THEN 0
                                       ELSE manager.proveedor_estado.fallos_seguidos + 1 END,
                donde           = EXCLUDED.donde,
                actualizado_at  = now()
            """,
            {"p": proveedor, "ok": ok, "err": error or "sin detalle",
             "donde": donde})


def estado() -> list[dict]:
    """Cómo viene contestando cada proveedor. Lo lee el agente."""
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT proveedor, ok, ultimo_error, ultimo_error_at, ultimo_ok_at, "
            "       fallos_seguidos, donde, actualizado_at "
            "FROM manager.proveedor_estado")
        filas = cur.fetchall()
    return [{"proveedor": f[0], "ok": f[1], "ultimo_error": f[2],
             "ultimo_error_at": f[3], "ultimo_ok_at": f[4],
             "fallos_seguidos": f[5], "donde": f[6], "actualizado_at": f[7]}
            for f in filas]
