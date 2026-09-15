"""Controles que corren ANTES de ejecutar cualquier herramienta. Un control
devuelve un dict (el error que lee el modelo) para cortar, o None para dejar
pasar. Se disparan por el ARGUMENTO, nunca por el nombre de la herramienta."""
from __future__ import annotations

import logging

from asistente import permitido

logger = logging.getLogger(__name__)


def cuenta_habilitada(nombre: str, args: dict) -> dict | None:
    """Ninguna herramienta corre sobre una cuenta que no esté habilitada."""
    if "cuenta" not in args:
        return None
    if not permitido.cuentas():
        return permitido.como_error()
    pedida = str(args.get("cuenta") or "").strip()
    if pedida not in permitido.cuentas():
        logger.warning("asistente: %s pidió la cuenta %r, que no está habilitada",
                       nombre, pedida)
        return {
            "error": f"la cuenta {pedida!r} no está habilitada para el asistente",
            "cuentas_habilitadas": permitido.cuentas(),
            "que_hacer": ("Preguntale al usuario cuál de las cuentas habilitadas "
                          "quiere. NO contestes con otra cuenta."),
        }
    return None


CONTROLES = (cuenta_habilitada,)


def revisar(nombre: str, args: dict | None) -> dict | None:
    """None = que corra. Un dict = no corre, y eso es lo que lee el modelo.
    Un control que revienta se loguea y deja pasar: cada herramienta valida lo suyo."""
    if not isinstance(args, dict):
        return None
    for control in CONTROLES:
        try:
            if (corte := control(nombre, args)) is not None:
                return corte
        except Exception as e:
            logger.warning("asistente: el control %s reventó (%s) — dejo pasar",
                           control.__name__, e)
    return None
