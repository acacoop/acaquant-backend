"""api/services/av_agent_motores.py — SI UN MOTOR SE CAYÓ, EL AGENTE SE ENTERA.

Doc madre: **`docs/AV_AGENT.md`** §0.r.

Pedido del user (2026-08-19): *«con los logs de los motores lo mismo: quiero que
si hay alguno caído enterarme rápido (y a futuro que pueda hacer algo)»*.

**Todo el trabajo ya estaba hecho y el agente no lo miraba.**
`api/services/diagnostico_registry.py` tiene **50 piezas** —15 motores, 30 jobs,
5 APIs— cada una con su cadencia, su ventana horaria, su umbral de frescura y de
dónde se lee. `diagnostico.arbol()` las evalúa. Pero eso vivía SOLO en la pantalla
de Manager → OBSERVABILIDAD → DIAGNÓSTICO, o sea que había que ir a mirarla.

Este módulo no reimplementa nada: **lee el mismo árbol y convierte lo que está
mal en un hallazgo**, que es lo que hace que la señal te busque en vez de
esperarte. Es exactamente el mismo movimiento que se hizo con SALUD.

LA VENTANA ES LO QUE HACE QUE ESTO NO MIENTA
=============================================

Un motor fuera de rueda **no está caído: está apagado**, y los motores de mercado
los prende y los apaga el cron de lunes a viernes. Sin mirar la ventana, este
detector cantaría quince motores muertos todos los sábados — y en dos fines de
semana nadie volvería a leerlo.

`_en_ventana` ya resuelve eso en `diagnostico.py` (rueda 10:00-17:05 ART, agro
10:30, `always`, `diario`) y por eso las piezas fuera de ventana llegan acá con
estado `fuera_rueda`, que **no se reporta**.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Qué estados del árbol son un problema DE VERDAD. `lento` queda afuera a
# propósito: un motor que tarda el doble de su umbral sigue produciendo, y
# mezclarlo con uno muerto es cómo se pierde la diferencia entre las dos cosas.
_ROTOS = {"critico", "error", "sin_datos"}

# `fuera_rueda` y `sin_datos` fuera de ventana NO son problemas: son el sistema
# funcionando como tiene que funcionar.
_IGNORAR = {"fuera_rueda", "ok", "lento", "error_parse"}


def detectar_motores() -> list[dict]:
    """Los motores, jobs y APIs que están rotos **ahora y en su ventana**."""
    try:
        from api.services import diagnostico
        arbol = diagnostico.arbol()
    except Exception as e:
        logger.warning("av_agent_motores: no pude leer el árbol: %s", e)
        return []

    if not arbol.get("en_rueda"):
        # Fuera de rueda solo se miran las piezas `always`/`diario`; el árbol ya
        # las devuelve con su estado real y las de rueda como `fuera_rueda`.
        logger.debug("av_agent_motores: fuera de rueda")

    out = []
    for vista in arbol.get("vistas") or []:
        for grupo in vista.get("grupos") or []:
            for p in grupo.get("piezas") or []:
                estado = (p.get("estado") or "").strip()
                if estado in _IGNORAR or estado not in _ROTOS:
                    continue
                out.append(_hallazgo(vista.get("vista") or "?", p, estado))
    # Lo más grave primero, y dentro de eso los motores antes que los jobs: un
    # motor caído deja a la mesa sin precios AHORA; un job se recupera en la
    # corrida siguiente.
    out.sort(key=lambda h: (0 if h["severidad"] == "alta" else 1,
                            0 if h["evidencia"]["tipo"] == "motor" else 1))
    return out


def _hallazgo(vista: str, p: dict, estado: str) -> dict:
    tipo = p.get("tipo") or "pieza"
    label = str(p.get("label") or "?")
    # Un MOTOR caído es alta siempre: es el feed de precios de la mesa. Un job
    # crítico también; un `sin_datos` de una API externa es media — puede ser
    # que el proveedor esté caído y no nosotros.
    severidad = "alta" if (tipo == "motor" or estado == "critico") else "media"
    return {
        "tipo": "motor_caido", "ticker": label,
        "regla": {"critico": "sin_producir", "error": "fallo",
                  "sin_datos": "sin_datos"}.get(estado, estado),
        "severidad": severidad,
        "motivo": (f"{tipo} de {vista}: "
                   + {"critico": "hace rato que no produce",
                      "error": "la última corrida falló",
                      "sin_datos": "nunca escribió nada"}.get(estado, estado)),
        "evidencia": {
            "texto": (f"{label} — cadencia esperada {p.get('cadencia') or '—'}, "
                      f"último dato {p.get('hace') or 'nunca'}"
                      + (f", último run {p.get('run_status')}"
                         if p.get("run_status") else "")
                      + f". El umbral de esta pieza es {p.get('umbral_s')} s y "
                        f"está DENTRO de su ventana horaria: no es que esté "
                        f"apagado."),
            "tipo": tipo, "vista": vista, "estado": estado,
            "cadencia": p.get("cadencia"), "ultima": p.get("ultima"),
            "hace": p.get("hace"), "umbral_s": p.get("umbral_s"),
            "run_status": p.get("run_status")}}


def resumen() -> dict:
    """**¿Está la app funcionando bien AHORA?** — para el explicador.

    El user: *«no quiero que me muestre todos los endpoints; yo quiero saber que
    en horario de mercado la aplicación funciona bien y no hay nada
    colapsando»*. Esto devuelve el conteo, no la lista de 50 piezas.
    """
    try:
        from api.services import diagnostico
        arbol = diagnostico.arbol()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    piezas = [p for v in (arbol.get("vistas") or [])
              for g in (v.get("grupos") or []) for p in (g.get("piezas") or [])]
    return {
        "ok": True,
        "en_rueda": bool(arbol.get("en_rueda")),
        "ahora_ar": arbol.get("ahora_ar"),
        "total": len(piezas),
        "bien": sum(1 for p in piezas if p.get("estado") == "ok"),
        "lentas": sum(1 for p in piezas if p.get("estado") == "lento"),
        "rotas": sum(1 for p in piezas if (p.get("estado") or "") in _ROTOS),
        "apagadas": sum(1 for p in piezas if p.get("estado") == "fuera_rueda"),
        "detalle_rotas": [
            {"label": p.get("label"), "tipo": p.get("tipo"),
             "estado": p.get("estado"), "hace": p.get("hace")}
            for p in piezas if (p.get("estado") or "") in _ROTOS],
    }
