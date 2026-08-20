"""api/services/av_agent_proveedores.py — SI SE CAYÓ UNO DE AFUERA, EL AGENTE AVISA.

Doc madre: **`docs/AV_AGENT.md`** §0.ad.

Pedido del user (2026-08-20), mientras Aunesa devolvía HTTP 500 en su login:
*«esto es una funcionalidad que la vi de milagro… sí o sí el agente tiene que
detectar cuándo esto está caído, avisar y dar el motivo exacto»*.

El registro lo escribe `core/proveedores` desde adentro de los clientes HTTP —
cada llamada REAL deja su rastro, sin health checks (1816 cobra por llamada, y
un ping que contesta bien no prueba que el endpoint que usamos ande). Acá solo
se lee y se convierte en hallazgo.

**EL AVISO TIENE QUE DECIR TRES COSAS**, y las tres estaban en el cartel que el
user vio de milagro:

    1. QUIÉN se cayó          →  Aunesa (el custodio)
    2. QUÉ deja de andar      →  Tesorería sin los movimientos del día…
    3. EL MOTIVO EXACTO       →  HTTPError: 500 Server Error for url: …/login

La 3 es la que convierte un aviso en algo accionable: sin el error textual, el
que lo lee no puede distinguir «se cayó el proveedor» de «se nos vencieron las
credenciales», que se resuelven en lugares distintos y por personas distintas.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# ⚠️ **EL HALLAZGO VENCE.** Solo se canta si el último fallo es RECIENTE.
#
# Nadie apaga el registro cuando el proveedor se recupera: simplemente dejan de
# anotarse fallos. Sin esta ventana, un 500 de la semana pasada seguiría en la
# pantalla para siempre — y una pantalla con un problema viejo enseña a
# ignorarla, que es justo lo que pasó con los hallazgos de rueda (§0.u).
#
# 20 minutos: los daemons de Aunesa corren cada pocos minutos, así que una caída
# de verdad se re-anota sola varias veces dentro de la ventana. Si en 20 minutos
# nadie volvió a fallar, o se arregló o nadie lo está usando — en los dos casos
# no hay nada que avisar AHORA.
VENTANA_S = 20 * 60

# Un fallo aislado puede ser un timeout de red. Dos seguidos ya es el proveedor.
MINIMO_FALLOS = 1


def detectar_proveedores(*, ahora: datetime | None = None) -> list[dict]:
    """Los proveedores externos que están fallando **ahora**.

    **Nunca levanta**: corre adentro del monitor de rueda, junto a los detectores
    de precio, y una excepción acá apagaría el ciclo entero.
    """
    try:
        from core.proveedores import PROVEEDORES, estado
        filas = estado()
    except Exception as e:
        logger.warning("av_agent_proveedores: no pude leer el estado: %s", e)
        return []

    ahora = ahora or datetime.now(UTC)
    out = []
    for f in filas:
        if f.get("ok"):
            continue
        cuando = f.get("ultimo_error_at")
        if not cuando:
            continue
        if cuando.tzinfo is None:
            cuando = cuando.replace(tzinfo=UTC)
        hace = (ahora - cuando).total_seconds()
        if hace > VENTANA_S or (f.get("fallos_seguidos") or 0) < MINIMO_FALLOS:
            continue

        p = PROVEEDORES.get(f["proveedor"])
        nombre = p.nombre if p else f["proveedor"]
        rompe = p.rompe if p else "no sé qué depende de él"
        veces = f.get("fallos_seguidos") or 1
        out.append({
            "tipo": "proveedor_caido", "ticker": f["proveedor"],
            "regla": "no_responde",
            # ALTA sin matices: no es un dato feo, es media aplicación andando a
            # ciegas. Y el que la usa no tiene forma de darse cuenta solo.
            "severidad": "alta",
            "motivo": f"{nombre} no responde" + (f" ({veces} intentos)" if veces > 1 else ""),
            "evidencia": {
                "texto": (f"QUÉ DEJA DE ANDAR: {rompe}.\n\n"
                          f"MOTIVO EXACTO: {f.get('ultimo_error') or 'sin detalle'}\n\n"
                          f"Falló en «{f.get('donde') or '?'}», "
                          f"hace {_hace(hace)}"
                          + (f", {veces} veces seguidas" if veces > 1 else "")
                          + (f". El último OK fue {_fecha(f.get('ultimo_ok_at'))}"
                             if f.get("ultimo_ok_at") else
                             ". No hay registro de que haya contestado bien.")),
                "proveedor": f["proveedor"], "rompe": rompe,
                "error": f.get("ultimo_error"), "donde": f.get("donde"),
                "fallos_seguidos": veces, "hace_s": round(hace),
                "ultimo_ok_at": _fecha(f.get("ultimo_ok_at"))}})
    # El que más viene fallando, primero.
    out.sort(key=lambda h: -h["evidencia"]["fallos_seguidos"])
    return out


def _hace(seg: float) -> str:
    if seg < 120:
        return f"{int(seg)} segundos"
    if seg < 7200:
        return f"{int(seg / 60)} minutos"
    return f"{seg / 3600:.1f} horas"


def _fecha(dt) -> str | None:
    return dt.strftime("%d/%m %H:%M") if dt else None


def resumen() -> dict:
    """**¿Están andando los de afuera?** — para el explicador.

    Devuelve el estado de TODOS, no solo los caídos: la pregunta «¿anda Aunesa?»
    tiene que poder contestarse que sí.
    """
    try:
        from core.proveedores import PROVEEDORES, estado
        filas = {f["proveedor"]: f for f in estado()}
    except Exception as e:
        # «No pude preguntar» no es «están todos bien» (§0.s).
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "proveedores": [
        {"proveedor": k, "nombre": p.nombre, "rompe": p.rompe,
         # Sin fila = nunca falló desde que existe el registro. No es lo mismo
         # que «contestó bien recién», y por eso se dice distinto.
         "estado": ("sin registro" if k not in filas
                    else "anda" if filas[k]["ok"] else "no responde"),
         "ultimo_error": (filas.get(k) or {}).get("ultimo_error"),
         "ultimo_error_at": _fecha((filas.get(k) or {}).get("ultimo_error_at"))}
        for k, p in PROVEEDORES.items()]}
