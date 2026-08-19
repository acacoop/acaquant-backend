"""api/services/av_agent_seguridad.py — ¿LOS PERMISOS SON REALES O ESTÁN EN LOS PAPELES?

Doc madre: **`docs/AV_AGENT.md`** §0.s.

Pedido del user (2026-08-19):

    *«Que sea capaz de detectar si algún endpoint está mal hecho y se puede
    consultar a la fuerza por tener solo permisos "en los papeles". Una vez me
    había pasado que Vercel me dejaba todo sin protección y no me daba cuenta.
    Todos los endpoints debería ser capaz de controlar que solo los ves si tenés
    el permiso.»*

SON DOS PREGUNTAS DISTINTAS Y HAY QUE SEPARARLAS
=================================================

    LO DECLARADO   ¿cada endpoint tiene el gate que le corresponde?   → se LEE
    LO EFECTIVO    ¿el borde realmente lo aplica?                     → se PRUEBA

La primera se contesta leyendo el árbol de rutas (`api/superficie.py`). La
segunda **no se puede leer de ninguna manera**: el backend puede estar impecable
y el borde abierto, que es exactamente lo que le pasó al user con Vercel. La
única forma de saberlo es **pedir sin credenciales y ver qué contesta**.

Es la misma idea de **recompensa verificable** que ya usan las ACCIONES (§0.j),
aplicada a seguridad: no *«creo que está protegido»* sino *«pedí y me dio 401»*.

TRES LÍMITES QUE ESTO TIENE, Y HAY QUE DECIRLOS
================================================

1. **Un agente que prueba endpoints sin credenciales es, técnicamente, un
   scanner.** Por eso: SOLO `GET`, SOLO rutas del inventario propio (no adivina
   URLs), con throttle, y **jamás una escritura** — probar un `POST` «a ver si me
   deja» puede escribir de verdad, y probar un `DELETE` es impensable.
2. **Desde dónde se prueba cambia qué se prueba.** Desde el Droplet, pegarle a la
   URL pública sale a internet y vuelve por Cloudflare → verifica CF Access y el
   backend. **NO verifica Vercel**, que es donde el user tuvo el problema. Por eso
   el resultado dice SIEMPRE qué capa cubrió: un verde que no aclara su alcance da
   falsa tranquilidad justo donde hubo un incidente.
3. **Solo prueba rutas sin parámetros de path.** Con `{id}` no se puede armar una
   URL real, y un valor inventado devuelve 404 — que no dice nada sobre permisos.
   Esas quedan cubiertas por la lectura, no por la prueba.

EL INVARIANTE
=============

    Ningún endpoint puede contestar algo distinto de 401/403 sin credencial.

Es binario, no depende de que nadie mantenga una lista, y sirve igual el día que
se agregue una ruta: entra sola al inventario.
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# La URL PÚBLICA. Se prueba contra ella y no contra `localhost` a propósito: por
# localhost se saltea Cloudflare, o sea justo la capa que interesa verificar.
# **Sin esta variable el chequeo NO corre** — no se inventa la URL: probar contra
# el host equivocado y salir en verde es peor que no probar.
URL_PUBLICA = os.getenv("AV_AGENT_URL_PUBLICA", "").rstrip("/")

# Lo que se acepta como «me rechazó». 405 entra porque significa que la ruta
# existe y el método no aplica: tampoco filtró nada.
RECHAZOS = frozenset({401, 403, 405})
# Lo que NO dice nada en un sentido ni en el otro y por eso no se reporta.
MUDOS = frozenset({404, 502, 503, 504})

# Abiertos A PROPÓSITO. Se enumeran acá para que agregar uno sea una decisión
# explícita y no un olvido que el chequeo deja pasar en silencio.
ABIERTOS_OK: dict[str, str] = {
    "/api/health": "liveness probe — no devuelve ningún dato",
    "/api/me": "la identidad del que pregunta; sin credencial no dice nada de nadie",
}

PAUSA_S = 0.15          # throttle: esto es tráfico contra producción
TIMEOUT_S = 8
MAX_RUTAS = 400


def declarado() -> dict:
    """**Lo que dice el código.** Qué rutas no tienen ningún gate."""
    from api import superficie

    r = superficie.resumen()
    inesperadas = [p for p in r["sin_gate"] if p not in ABIERTOS_OK]
    return {**r, "abiertas_inesperadas": inesperadas,
            "abiertas_declaradas": sorted(ABIERTOS_OK)}


def probar(*, limite: int = MAX_RUTAS) -> dict:
    """**Lo que hace el borde.** Pide SIN credenciales y mira qué contesta.

    Devuelve siempre `alcance`: qué capa se verificó de verdad. Un resultado de
    seguridad sin decir qué cubrió es una media verdad, y en seguridad una media
    verdad se lee como un sí.
    """
    if not URL_PUBLICA:
        return {"ok": False,
                "motivo": "falta AV_AGENT_URL_PUBLICA — no invento la URL: "
                          "probar contra el host equivocado y salir en verde es "
                          "peor que no probar",
                "alcance": None}
    try:
        import requests
    except ImportError:
        return {"ok": False, "motivo": "sin cliente HTTP", "alcance": None}

    from api import superficie

    # Solo GET, sin path params, del inventario propio. Nada de adivinar URLs.
    objetivo = [r for r in superficie.rutas()
                if "GET" in r.metodos and "{" not in r.path
                and r.path.startswith("/api/") and not r.sin_gate][:limite]

    sesion = requests.Session()
    # Sin ninguna credencial: ni bearer, ni cookie de CF Access, ni el header del
    # portal. Es el punto entero del chequeo.
    sesion.headers.update({"User-Agent": "av-agent/seguridad (chequeo propio)"})

    filtran, rechazan, mudos, errores = [], 0, 0, 0
    for r in objetivo:
        try:
            resp = sesion.get(f"{URL_PUBLICA}{r.path}", timeout=TIMEOUT_S,
                              allow_redirects=False)
            code = resp.status_code
        except Exception:
            errores += 1
            time.sleep(PAUSA_S)
            continue
        if code in RECHAZOS:
            rechazan += 1
        elif code in MUDOS:
            mudos += 1
        elif 200 <= code < 400:
            # **El hallazgo.** Contestó algo sin que nadie se identificara.
            filtran.append({"path": r.path, "status": code,
                            "bytes": len(resp.content or b""),
                            "gates": sorted(set(r.gates))})
        else:
            mudos += 1
        time.sleep(PAUSA_S)

    return {
        "ok": True, "url": URL_PUBLICA,
        "probadas": len(objetivo), "rechazan": rechazan,
        "filtran": filtran, "mudos": mudos, "errores": errores,
        # Qué se verificó DE VERDAD. Ver el límite 2 del encabezado.
        "alcance": ("Cloudflare Access + el backend. **NO cubre el front de "
                    "Vercel**: esta prueba pega a la API, no a la app."),
    }


def detectar_seguridad() -> list[dict]:
    """El detector. Lo declarado siempre; lo efectivo solo si está configurado.

    La parte que LEE es gratis y corre siempre. La que PRUEBA hace tráfico real
    contra producción, así que corre en el job nocturno y no en el monitor de
    rueda — una superficie mal gateada no se arregla sola en cinco minutos, pero
    400 requests cada cinco minutos sí molestan.
    """
    out: list[dict] = []
    try:
        d = declarado()
    except Exception as e:
        logger.warning("av_agent_seguridad: no pude leer la superficie: %s", e)
        return []

    for path in d["abiertas_inesperadas"]:
        out.append(_h("sin_gate", path, "alta",
                      "endpoint sin NINGÚN gate y no está declarado como abierto",
                      {"texto": "no pide bearer, ni módulo, ni allowlist. Si es "
                                "a propósito hay que declararlo en "
                                "`ABIERTOS_OK` con el motivo; si no, le falta el "
                                "gate. Esto sale de LEER el árbol de rutas, así "
                                "que vale aunque el borde esté bien.",
                       "capa": "declarado"}))

    for f in (probar().get("filtran") or []):
        out.append(_h("responde_sin_credencial", f["path"], "alta",
                      f"contestó {f['status']} sin ninguna credencial",
                      {"texto": f"se le pidió desde afuera, sin bearer ni sesión, "
                                f"y devolvió {f['bytes']} bytes. En el código "
                                f"figura con gates {f['gates']} — o sea que el "
                                f"permiso está en los papeles pero el borde no "
                                f"lo aplica.",
                       "capa": "efectivo", "status": f["status"],
                       "bytes": f["bytes"], "gates": f["gates"]}))
    return out


def _h(regla: str, sujeto: str, severidad: str, motivo: str,
       evidencia: dict) -> dict:
    return {"tipo": "permiso_flojo", "ticker": sujeto, "regla": regla,
            "severidad": severidad, "motivo": motivo, "evidencia": evidencia}
