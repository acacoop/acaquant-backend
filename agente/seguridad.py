"""agente/seguridad.py — ¿LOS PERMISOS SON REALES O ESTÁN EN LOS PAPELES?

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

DOS EXCEPCIONES, Y SON CATEGORÍAS DISTINTAS
============================================

    ABIERTOS_OK               público de verdad — no devuelve nada de nadie
    PROTEGIDOS_EN_EL_BORDE    el candado existe, pero vive en Cloudflare

La segunda NO es «abierto a propósito». Es *«el código no lo gatea y está bien,
porque lo gatea el borde»*, que es precisamente la situación en la que el user se
quemó con Vercel: repo impecable, borde abierto, cero errores visibles. Meterlas
en la misma bolsa las haría desaparecer del radar justo donde más hay que mirar,
así que se declaran aparte y **la prueba activa las incluye siempre**.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date

logger = logging.getLogger(__name__)

# La URL PÚBLICA. Se prueba contra ella y no contra `localhost` a propósito: por
# localhost se saltea Cloudflare, o sea justo la capa que interesa verificar.
# **Sin esta variable el chequeo NO corre** — no se inventa la URL: probar contra
# el host equivocado y salir en verde es peor que no probar.
URL_PUBLICA = os.getenv("AV_AGENT_URL_PUBLICA", "").rstrip("/")


def _url() -> str:
    """La URL, resuelta AL USARLA y no al importar el módulo.

    Este módulo se importa muy temprano (arrastrado por `api.superficie` →
    `api.main`), y `load_dotenv()` puede correr DESPUÉS. Con la constante leída
    al import, alguien cargaría la variable en el `.env`, reiniciaría, y el
    chequeo seguiría diciendo «falta AV_AGENT_URL_PUBLICA» sin ninguna pista de
    por qué. Un orden de imports no puede ser la razón por la que un chequeo de
    seguridad no corre.
    """
    return (URL_PUBLICA or os.getenv("AV_AGENT_URL_PUBLICA", "")).rstrip("/")

# Lo que se acepta como «me rechazó». 405 entra porque significa que la ruta
# existe y el método no aplica: tampoco filtró nada.
RECHAZOS = frozenset({401, 403, 405})
# Lo que NO dice nada en un sentido ni en el otro y por eso no se reporta.
MUDOS = frozenset({404, 502, 503, 504})
# Un 3xx que manda a loguearse ES un rechazo (así contesta CF Access cuando no
# hay sesión). Se reconoce por el host del `Location`, no por el código.
LOGIN_HOSTS = ("cloudflareaccess.com", "/cdn-cgi/access/login")

# ---------------------------------------------------------------------------
# LAS DOS LISTAS. La diferencia entre ellas no es cosmética: cambia qué hay que
# verificar. Meter todo en una sola («abiertos a propósito») es exactamente la
# media verdad que este módulo existe para no decir.
# ---------------------------------------------------------------------------

# (1) ABIERTOS DE VERDAD. No tienen gate en el código, no lo tienen en el borde,
# y está bien así porque no devuelven nada de nadie. Agregar uno acá es una
# decisión explícita, no un olvido que el chequeo deja pasar en silencio.
ABIERTOS_OK: dict[str, str] = {
    "/api/health": "liveness probe — no devuelve ningún dato",
    "/api/me": "la identidad del que pregunta; sin credencial no dice nada de nadie",
    # El handshake de OAuth 2.1 del MCP. Son públicos POR PROTOCOLO: el cliente
    # los lee ANTES de tener credencial — pedirle credencial para averiguar cómo
    # sacar una credencial no cierra nunca. Están en la app de CF Access
    # `acaquant-mcp-bypass` (BYPASS + Everyone) justamente para eso.
    "/.well-known/oauth-authorization-server":
        "discovery del server OAuth (RFC 8414) — metadata pública por diseño",
    "/.well-known/oauth-protected-resource":
        "discovery del recurso protegido (RFC 9728) — metadata pública por diseño",
    "/.well-known/oauth-protected-resource/mcp":
        "la misma metadata, scopeada a /mcp (RFC 9728)",
    "/oauth/register":
        "Dynamic Client Registration (RFC 7591) — el cliente se da de alta antes "
        "de tener nada; devuelve credenciales NUEVAS, no ajenas",
    "/oauth/token":
        "canje de code por token — su INPUT es la credencial (code + PKCE "
        "verifier); un GET sin nada no puede sacar un token",
}

# (2) PROTEGIDOS EN EL BORDE, NO EN EL CÓDIGO. Acá el backend no tiene gate y
# **eso es correcto**, pero quien protege es Cloudflare Access. Declararlos como
# «abiertos OK» sería mentir: no son públicos, es que su candado vive afuera del
# repo — o sea, exactamente el escenario Vercel del user (el código impecable y
# el borde abierto, sin ningún error visible).
#
# Por eso NO se saltean la prueba: son los ÚNICOS cuyo veredicto depende 100% de
# la prueba activa. Si contestan 200 sin credencial, el candado no está.
PROTEGIDOS_EN_EL_BORDE: dict[str, str] = {
    "/oauth/authorize":
        "acá logea el usuario — lo protege Cloudflare Access, NO el código. "
        "Sin sesión tiene que desviar al login; si contesta 200, el candado "
        "del borde no está puesto",
}

PAUSA_S = 0.15          # throttle: esto es tráfico contra producción
TIMEOUT_S = 8
MAX_RUTAS = 400


def declarado() -> dict:
    """**Lo que dice el código.** Qué rutas no tienen ningún gate.

    Separa en tres: las que están abiertas a propósito, las que dependen del
    borde (y por lo tanto SOLO las puede juzgar la prueba activa) y las que no
    están declaradas en ninguna de las dos — esas son el hallazgo.
    """
    from api import superficie

    r = superficie.resumen()
    inesperadas = [p for p in r["sin_gate"]
                   if p not in ABIERTOS_OK and p not in PROTEGIDOS_EN_EL_BORDE]
    return {**r, "abiertas_inesperadas": inesperadas,
            "abiertas_declaradas": sorted(ABIERTOS_OK),
            "en_el_borde": sorted(PROTEGIDOS_EN_EL_BORDE)}


def _veredicto(resp) -> str:
    """`rechaza` | `filtra` | `mudo`. Un 3xx al login es un rechazo, no un pase."""
    code = resp.status_code
    if code in RECHAZOS:
        return "rechaza"
    if 300 <= code < 400:
        destino = (resp.headers.get("location") or "").lower()
        return "rechaza" if any(h in destino for h in LOGIN_HOSTS) else "mudo"
    if code in MUDOS:
        return "mudo"
    return "filtra" if 200 <= code < 300 else "mudo"


def probar(*, limite: int = MAX_RUTAS) -> dict:
    """**Lo que hace el borde.** Pide SIN credenciales y mira qué contesta.

    Devuelve siempre `alcance`: qué capa se verificó de verdad. Un resultado de
    seguridad sin decir qué cubrió es una media verdad, y en seguridad una media
    verdad se lee como un sí.
    """
    base = _url()
    if not base:
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
    todas = superficie.rutas()
    objetivo = [r for r in todas
                if "GET" in r.metodos and "{" not in r.path
                and r.path.startswith("/api/") and not r.sin_gate][:limite]
    # Y las del borde SIEMPRE, aunque no arranquen con `/api/` y aunque el código
    # no las gatee: son las únicas que solo la prueba puede juzgar.
    objetivo += [r for r in todas
                 if r.path in PROTEGIDOS_EN_EL_BORDE and "GET" in r.metodos]

    sesion = requests.Session()
    # Sin ninguna credencial: ni bearer, ni cookie de CF Access, ni el header del
    # portal. Es el punto entero del chequeo.
    sesion.headers.update({"User-Agent": "av-agent/seguridad (chequeo propio)"})

    filtran, borde_abierto, rechazan, mudos, errores = [], [], 0, 0, 0
    for r in objetivo:
        try:
            resp = sesion.get(f"{base}{r.path}", timeout=TIMEOUT_S,
                              allow_redirects=False)
        except Exception:
            errores += 1
            time.sleep(PAUSA_S)
            continue
        v = _veredicto(resp)
        if v == "rechaza":
            rechazan += 1
        elif v == "mudo":
            mudos += 1
        else:
            # **El hallazgo.** Contestó algo sin que nadie se identificara.
            caso = {"path": r.path, "status": resp.status_code,
                    "bytes": len(resp.content or b""),
                    "gates": sorted(set(r.gates))}
            (borde_abierto if r.path in PROTEGIDOS_EN_EL_BORDE
             else filtran).append(caso)
        time.sleep(PAUSA_S)

    return {
        "ok": True, "url": base,
        "probadas": len(objetivo), "rechazan": rechazan,
        "filtran": filtran, "borde_abierto": borde_abierto,
        "mudos": mudos, "errores": errores,
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
        logger.warning("agente/seguridad: no pude leer la superficie: %s", e)
        return []

    # LA MEMORIA. Antes de juzgar, el agente mira cómo estaba la superficie ayer:
    # así puede distinguir un agujero NUEVO de uno que ya estaba, y —lo que
    # ninguna foto ve— un endpoint que PERDIÓ el gate que tenía.
    cambios = {}
    try:
        c = comparar()
        if c.get("ok") and not c.get("primera"):
            cambios = c
    except Exception as e:
        logger.warning("agente/seguridad: sin foto de ayer: %s", e)

    nuevos_hoy = {n["path"] for n in cambios.get("nuevos") or []}
    for f in cambios.get("perdieron_gate") or []:
        out.append(_h("perdio_el_gate", f["path"], "alta",
                      f"AYER tenía gate y HOY no ({f['metodos']})",
                      {"texto": f"en la foto del {cambios['fecha_previa']} pedía "
                                f"{f['gates_ayer'] or '—'} y hoy no pide nada. "
                                f"Esto una foto no lo ve: el total de endpoints "
                                f"abiertos puede no moverse y aun así haber un "
                                f"agujero nuevo."
                                + (" **Y ADEMÁS ESCRIBE.**" if f["escribe"] else ""),
                       "capa": "declarado", "gates_ayer": f["gates_ayer"],
                       "metodos": f["metodos"], "escribe": f["escribe"],
                       "desde": cambios["fecha_previa"]}))

    for path in d["abiertas_inesperadas"]:
        nuevo = path in nuevos_hoy
        out.append(_h("sin_gate", path,
                      "alta",
                      ("endpoint NUEVO y sin ningún gate" if nuevo else
                       "endpoint sin NINGÚN gate y no está declarado como abierto"),
                      {"texto": "no pide bearer, ni módulo, ni allowlist. Si es "
                                "a propósito hay que declararlo en "
                                "`ABIERTOS_OK` con el motivo; si el candado vive "
                                "en Cloudflare va a `PROTEGIDOS_EN_EL_BORDE`, "
                                "que además lo hace probar; si no, le falta el "
                                "gate. Esto sale de LEER el árbol de rutas, así "
                                "que vale aunque el borde esté bien."
                                + (" **Apareció hoy**: no estaba en la foto de "
                                   "ayer." if nuevo else ""),
                       "capa": "declarado",
                       # Sin memoria no se puede afirmar ninguna de las dos
                       # cosas, y "no sé" es una respuesta válida.
                       "desde": ("hoy" if nuevo else
                                 "ya estaba" if cambios else "sin foto previa")}))

    p = probar()

    # **El silencio no es un verde.** Si la mitad efectiva no corrió hay que
    # decirlo: lo declarado no puede afirmar nada sobre el borde, y el incidente
    # que originó este módulo fue exactamente un borde abierto con el código bien.
    if not p.get("ok"):
        out.append(_h("prueba_no_corrio", "(la prueba activa)", "media",
                      "solo se verificó lo DECLARADO: el borde no se probó",
                      {"texto": f"{p.get('motivo')}. O sea que de estos "
                                f"endpoints sabemos que el CÓDIGO los gatea, "
                                f"pero no que el borde lo aplique — que es "
                                f"justo lo que falló con Vercel.",
                       "capa": "efectivo", "corrio": False}))
        return out

    for f in p.get("filtran") or []:
        out.append(_h("responde_sin_credencial", f["path"], "alta",
                      f"contestó {f['status']} sin ninguna credencial",
                      {"texto": f"se le pidió desde afuera, sin bearer ni sesión, "
                                f"y devolvió {f['bytes']} bytes. En el código "
                                f"figura con gates {f['gates']} — o sea que el "
                                f"permiso está en los papeles pero el borde no "
                                f"lo aplica.",
                       "capa": "efectivo", "status": f["status"],
                       "bytes": f["bytes"], "gates": f["gates"]}))

    for f in p.get("borde_abierto") or []:
        out.append(_h("borde_sin_candado", f["path"], "alta",
                      f"lo protege Cloudflare, no el código — y contestó "
                      f"{f['status']} sin credencial",
                      {"texto": f"{PROTEGIDOS_EN_EL_BORDE.get(f['path'], '')}. "
                                f"Devolvió {f['bytes']} bytes en vez de desviar "
                                f"al login: la app de CF Access que lo cubre no "
                                f"está, se despublicó o le cambiaron el path.",
                       "capa": "efectivo", "status": f["status"],
                       "bytes": f["bytes"]}))
    return out


# ═══════════════════════════════════════════════════════════════════════════
# LA MEMORIA DE LA SUPERFICIE — para que esto NO sea una foto
# ═══════════════════════════════════════════════════════════════════════════
#
# *«Esto no tiene que ser estático: hago la solicitud, se encuentra algo o no
# pasa nada, y en el medio pasa el tiempo, avanza la app, se agregan cosas
# nuevas y se vuelve a quedar desactualizado todo. El agente debe PERSISTIR: no
# tengo que estar pidiéndole cosas, ya tiene que tener mapeado todo.»* (user)
#
# Un chequeo sin memoria solo sabe decir **cuántos** endpoints están abiertos
# hoy. Con memoria puede decir lo único que importa de verdad:
#
#     apareció un endpoint nuevo SIN gate     → alguien lo publicó así hoy
#     un endpoint PERDIÓ el gate que tenía    → una regresión, y es la peor
#
# La segunda es invisible para cualquier chequeo de foto: el total de «abiertos»
# puede quedar igual —uno se cerró, otro se abrió— y nadie se entera. Es el mismo
# razonamiento del tamaño de la base (§0.q): **lo que informa es el DELTA**.
#
# Dos fechas y purga en la misma transacción, igual que `db_tamano`: una tabla
# que vigila a la app y crece sin techo es un chiste que se cuenta solo.

FECHAS_QUE_SE_GUARDAN = 2


def sacar_foto(*, hoy: date | None = None) -> dict:
    """Congela la superficie de HOY: cada ruta con su gate efectivo.

    Re-sacarla el mismo día PISA la del día (la PK es fecha+path+métodos), así
    que correrla dos veces no duplica ni rompe el delta.
    """
    from api import superficie
    from core.postgres import get_pool

    f = hoy or date.today()
    rs = superficie.rutas()
    filas = [(f, r.path, ",".join(sorted(r.metodos)), ",".join(sorted(set(r.gates))),
              r.sin_gate, r.escribe) for r in rs]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO manager.superficie_dia "
            "(fecha, path, metodos, gates, sin_gate, escribe) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (fecha, path, metodos) DO UPDATE SET "
            "  gates = EXCLUDED.gates, sin_gate = EXCLUDED.sin_gate, "
            "  escribe = EXCLUDED.escribe", filas)
        # LA PURGA, en la misma transacción que la escritura.
        cur.execute(
            "DELETE FROM manager.superficie_dia WHERE fecha NOT IN "
            "(SELECT DISTINCT fecha FROM manager.superficie_dia "
            " ORDER BY fecha DESC LIMIT %s)", (FECHAS_QUE_SE_GUARDAN,))
        conn.commit()
    return {"fecha": f.isoformat(), "rutas": len(filas),
            "sin_gate": sum(1 for r in rs if r.sin_gate)}


def comparar() -> dict:
    """La superficie de hoy contra la de ayer. **No devuelve totales: cambios.**

    `primera=True` la primera vez, y ahí NO se reporta nada: el día uno todas las
    rutas son «nuevas» y avisar de 541 endpoints nuevos es la forma más rápida de
    que el aviso se apague para siempre.
    """
    from core.postgres import get_pool

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT fecha FROM manager.superficie_dia "
                    "ORDER BY fecha DESC LIMIT 2")
        fechas = [r[0] for r in cur.fetchall()]
        if len(fechas) < 2:
            return {"ok": True, "primera": True}
        hoy, ayer = fechas[0], fechas[1]
        cur.execute("SELECT fecha, path, metodos, gates, sin_gate, escribe "
                    "FROM manager.superficie_dia WHERE fecha = ANY(%s)",
                    ([hoy, ayer],))
        filas = cur.fetchall()

    def _mapa(f):
        return {(r[1], r[2]): {"gates": r[3], "sin_gate": r[4], "escribe": r[5]}
                for r in filas if r[0] == f}

    a, h = _mapa(ayer), _mapa(hoy)
    nuevos = [{"path": k[0], "metodos": k[1], **v}
              for k, v in h.items() if k not in a]
    desaparecidos = [{"path": k[0], "metodos": k[1]} for k in a if k not in h]
    # **La regresión.** Tenía gate ayer y hoy no. Un chequeo de foto no la ve:
    # el total de abiertos puede no moverse y aun así haber un agujero nuevo.
    perdieron = [{"path": k[0], "metodos": k[1], "gates_ayer": a[k]["gates"],
                  "escribe": v["escribe"]}
                 for k, v in h.items()
                 if k in a and v["sin_gate"] and not a[k]["sin_gate"]]
    return {"ok": True, "primera": False,
            "fecha": hoy.isoformat(), "fecha_previa": ayer.isoformat(),
            "nuevos": nuevos, "desaparecidos": desaparecidos,
            "perdieron_gate": perdieron, "total": len(h)}


def _h(regla: str, sujeto: str, severidad: str, motivo: str,
       evidencia: dict) -> dict:
    return {"tipo": "permiso_flojo", "ticker": sujeto, "regla": regla,
            "severidad": severidad, "motivo": motivo, "evidencia": evidencia}
