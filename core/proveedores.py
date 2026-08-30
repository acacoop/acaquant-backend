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
# ⚠️ **CORTO.** Regla del user (2026-08-20): *«si está caído Aunesa decí AUNESA
# CAÍDO + motivo simple y listo. Nada de palabras raras ni tanto texto, con la
# hora de actualización. Lo mismo para todo»*. `rompe` se lee de un vistazo: es
# para decidir en dos segundos, no para explicar el sistema. Test que lo limita.
PROVEEDORES: dict[str, Proveedor] = {
    "aunesa": Proveedor(
        "AUNESA", "aca.aunesa.com",
        "Tesorería, saldos, tenencia e informes. Lo cargado a mano sí está"),
    "1816": Proveedor(
        "1816", "api.1816.com.ar",
        "precios de referencia, emisores y TAMAR. Las curvas siguen"),
    "interbanking": Proveedor(
        "INTERBANKING", "interbanking.com.ar", "saldos y extractos de bancos"),
    "bcra": Proveedor(
        "BCRA", "api.bcra.gob.ar",
        "el CER del día. Los bonos CER quedan con el de ayer"),
}

# No se escribe más seguido que esto por proveedor y por proceso. Un daemon
# pegándole a un proveedor caído reintenta muchas veces por minuto: sin freno,
# una caída de una hora son miles de escrituras para decir siempre lo mismo.
CADA_S = 60

_lock = threading.Lock()
_ultimo: dict[str, tuple[float, bool]] = {}      # proveedor → (cuándo, estaba_ok)


def es_caida(codigo: int) -> bool:
    """¿Ese código HTTP significa que **el proveedor** está caído?

    ⚠️ **UNA sola definición, y por eso vive acá.** Había dos caminos para dejar
    rastro (`mirar` suelto y el hook de la sesión) y cada uno decidía con su
    propio umbral: `< 400` uno, `< 500` el otro. Con los dos enganchados a la
    misma llamada, **un 400 escribía «AUNESA CAÍDO» y acto seguido
    «recuperado»** — una caída inventada, prendiéndose y apagándose sola. Es
    REGLA #9 en vivo: dos copias del mismo criterio, sin árbitro, cada una
    coherente consigo misma. `jobs/cashflow` maneja el 400 de Aunesa
    explícitamente, así que no era hipotético.

    **Solo 5xx.** Un 400 o un 404 son un problema NUESTRO (pedimos mal) y
    mandarían a la mesa a llamar al proveedor por un bug propio. El 401 tampoco:
    es el token vencido, y los jobs lo resuelven re-autenticando.
    """
    return codigo >= 500


def mirar(resp, proveedor: str = "aunesa", donde: str = "") -> None:
    """Deja el rastro a partir de una respuesta de `requests`. **Nunca levanta.**

    ⚠️ **POR QUÉ HACE FALTA, y lo que costó no tenerlo** (2026-08-20): el AuM no
    se escribió ese día — `jobs/portafolio_backfill` murió con un `500` de Aunesa
    a las 11:00. Y el detector de caídas **no vio nada**, porque ese job le pega
    a Aunesa con su propio `requests` en vez del cliente único, así que su fallo
    no dejaba rastro en esta tabla. Sin rastro no hay `proveedor_caido`, y sin
    eso el aviso decía «la última corrida falló» sin decir por qué.

    Migrar los cuatro clientes sueltos a `core/aunesa` es lo correcto y es otro
    trabajo. Esto es la parte que se puede hacer hoy sin tocar su lógica: una
    línea por llamada, y un test que exige que ningún módulo que le hable a un
    proveedor se quede mudo.
    """
    try:
        codigo = int(getattr(resp, "status_code", 0) or 0)
    except (TypeError, ValueError):
        return
    if not codigo:
        return
    anotar(proveedor, ok=not es_caida(codigo), error=f"HTTP {codigo}",
           donde=donde or _ruta(getattr(resp, "url", "")))


def rastrear(session, proveedor: str = "aunesa") -> None:
    """Engancha el rastro a TODAS las respuestas de una `requests.Session`.

    **Es `vigilar` con otro nombre**, y se deja porque hay llamadores. Tenía su
    propia implementación —y su propio umbral— hasta que las dos se engancharon
    a la misma sesión e inventaron una caída: ver `es_caida`.
    """
    vigilar(session, proveedor)


def _ruta(url: str) -> str:
    """`https://aca.aunesa.com/Irmo/api/login?x=1` → `/Irmo/api/login`."""
    u = (url or "").split("://", 1)[-1]
    return ("/" + u.split("/", 1)[1].split("?")[0]) if "/" in u else ""


def sesion_vigilada(proveedor: str, donde: str = ""):
    """Una `requests.Session` que **deja rastro sola** en cada respuesta.

    ⚠️ **POR QUÉ EXISTE, y es la deuda que se hizo visible el 2026-08-20.** Cuatro
    módulos le pegan a Aunesa con su propio `requests`, por fuera de
    `core/aunesa`: `jobs/aum`, `jobs/cashflow`, `jobs/sync_comitentes` y
    `api/services/aunesa_negocio`. Ese día Aunesa devolvió **HTTP 500 a las 11:00**,
    `jobs/aum` murió y **el AuM del día no se escribió** — y el agente no pudo
    decir que era por Aunesa, porque esa falla no dejó ninguna huella.

    El detector de caídas (§0.ad) mira `manager.proveedor_estado`, que se llena
    desde adentro del cliente. Un módulo que no pasa por el cliente es invisible
    para él, aunque sea el que rompe el dato más importante del sistema.

    Migrarlos al cliente entero es otro trabajo y toca cuatro flujos distintos.
    **Esto es lo que arregla el 100% de la ceguera con una línea por módulo**: un
    hook de `requests` se dispara en CADA respuesta de esa sesión, así que una
    llamada nueva en ese archivo queda cubierta sin que nadie se acuerde.

    De yapa una `Session` reusa la conexión TCP, que en un job que hace cientos
    de llamadas seguidas no es poco.
    """
    import requests

    s = requests.Session()
    vigilar(s, proveedor, donde)
    return s


def vigilar(session, proveedor: str, donde: str = "") -> None:
    """Le engancha el rastro a una sesión que ya existe. Idempotente.

    **Nunca levanta y nunca cambia la respuesta**: el hook devuelve `None`, que
    para `requests` significa «dejala como está». Un instrumento que altera lo
    que mide no es un instrumento.
    """
    hooks = getattr(session, "hooks", None)
    if not isinstance(hooks, dict):
        return
    previos = hooks.setdefault("response", [])
    if any(getattr(h, "_av_agent", False) for h in previos):
        return                                   # ya está vigilada

    def _rastro(resp, *a, **k):
        try:
            codigo = int(getattr(resp, "status_code", 0) or 0)
            if not codigo:
                return None
            anotar(proveedor, ok=not es_caida(codigo), error=f"HTTP {codigo}",
                   donde=donde or _de_donde(resp))
        except Exception:                        # el instrumento nunca rompe
            logger.debug("proveedores: el rastro falló", exc_info=True)
        return None

    _rastro._av_agent = True
    previos.append(_rastro)


def _de_donde(resp) -> str:
    """El último tramo de la URL, para que el aviso diga QUÉ endpoint falló."""
    try:
        return (getattr(resp, "url", "") or "").split("?")[0].rstrip("/").split("/")[-1]
    except Exception:
        return ""


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


# ═══════════════════════════════════════════════════════════════════════════
# PROBARLO A PROPÓSITO — «si el error es 500 es porque está caído»
# ═══════════════════════════════════════════════════════════════════════════
#
# Pedido del user (2026-08-20): *«el agente sí o sí tiene que usar como función
# el poder llamar a Aunesa para ver la conexión y entender el error»*.
#
# **No contradice al rastro pasivo, lo completa.** El rastro dice QUE falló, con
# el error real de una llamada real. Esto contesta la pregunta que sigue y que el
# rastro no puede: **¿es de ellos o es nuestro?** Y la contesta de la única forma
# que no admite discusión: mandando una request **SIN credenciales**.
#
#     5xx sin credenciales  →  se rompió ANTES de leerlas: es de ELLOS.
#     4xx sin credenciales  →  su app está viva y rechaza, que es lo correcto.
#                              Si NUESTROS logins también fallan, ahí sí mirá el .env.
#
# Ese razonamiento ya se equivocó una vez en la dirección contraria: un diag dijo
# «es su servidor» en un paso y «es NUESTRO» tres líneas después, y mandó a
# revisar una configuración que siempre había sido así.
#
# TRES REGLAS DE LA PRUEBA, y las tres son para no hacer daño:
#   · **Nunca manda credenciales.** Un login fallido repetido bloquea la cuenta,
#     y para saber si el host está vivo no hacen falta.
#   · **Un solo intento.** Es un diagnóstico, no un reintento.
#   · **Solo se prueba a pedido**, o cuando el rastro YA marcó una falla. Nunca
#     en el camino feliz: 1816 cobra por llamada.

TIMEOUT_PRUEBA_S = 10

# El contrato que publica Aunesa (doc del custodio, 2026-08-20). Lo que importa
# no es el número sino QUÉ SIGNIFICA cada uno para decidir de quién es el
# problema — sin esto, un 403 y un 500 se leen igual: «no anda».
SIGNIFICADO: dict[int, tuple[str, str]] = {
    200: ("anda", "contestó bien"),
    204: ("anda", "contestó vacío, pero contestó"),
    400: ("anda", "rechazó el pedido (esperable sin credenciales)"),
    401: ("anda", "pidió autenticación (esperable sin credenciales)"),
    403: ("anda", "rechazó por permisos (esperable sin credenciales)"),
    500: ("caido", "error interno de su servidor"),
}


def probar(proveedor: str = "aunesa") -> dict:
    """Le pega al proveedor SIN credenciales y dice de quién es el problema.

    Devuelve `{proveedor, alcanzable, veredicto, status, detalle, ms, cuerpo}`.
    **Nunca levanta**: es un diagnóstico y tiene que poder contestar «no pude».
    """
    import time as _t

    import requests

    p = PROVEEDORES.get(proveedor)
    if p is None or proveedor != "aunesa":
        # Solo Aunesa por ahora: es el único con un endpoint que se puede tocar
        # sin credenciales y sin costo. 1816 COBRA por llamada — probarlo sería
        # gastar cuota para saber algo que su propio uso ya cuenta.
        return {"proveedor": proveedor, "alcanzable": None,
                "veredicto": "no_se_puede_probar", "status": None,
                "detalle": ("no hay una prueba segura para este proveedor "
                            "(o cobra por llamada): lo que se sabe de él sale "
                            "del rastro de las llamadas reales"),
                "ms": None, "cuerpo": None}

    url = "https://aca.aunesa.com/Irmo/api/login"
    t0 = _t.monotonic()
    try:
        resp = requests.post(url, json={},
                             headers={"Content-Type": "application/json"},
                             timeout=TIMEOUT_PRUEBA_S)
    except Exception as e:
        return {"proveedor": proveedor, "alcanzable": False,
                "veredicto": "caido", "status": None,
                "detalle": (f"ni siquiera contesta: {type(e).__name__}: {e}. "
                            "Puede ser su servicio entero abajo, o la red del "
                            "Droplet"),
                "ms": round((_t.monotonic() - t0) * 1000), "cuerpo": None}

    ms = round((_t.monotonic() - t0) * 1000)
    estado, que_es = SIGNIFICADO.get(
        resp.status_code,
        ("caido" if resp.status_code >= 500 else "anda",
         f"HTTP {resp.status_code}, fuera de su contrato documentado"))
    return {"proveedor": proveedor, "alcanzable": True, "veredicto": estado,
            "status": resp.status_code, "ms": ms,
            "detalle": (f"HTTP {resp.status_code} · {que_es}"
                        + (f" · {x}" if (x := _leer_error(resp)) else "")),
            "cuerpo": (resp.text or "")[:300]}


def _leer_error(resp) -> str:
    """Lo que el proveedor dice de su propio error, en sus palabras.

    ⚠️ **Y si NO viene en su formato, eso también es información.** Aunesa
    documenta que un 500 vuelve como `{"errors":[{"title","detail"}]}`. Cuando en
    vez de eso llega el HTML de error de Tomcat, significa que **se rompió antes
    de llegar a su propio manejador de errores** — o sea que no es una condición
    prevista por su aplicación, es su aplicación caída. Distinguirlo es la
    diferencia entre «me rechazaron» y «se les cayó».
    """
    tipo = (resp.headers.get("content-type") or "").lower()
    if resp.status_code < 400:
        return ""
    if "json" in tipo:
        try:
            errores = (resp.json() or {}).get("errors") or []
            if errores:
                e = errores[0]
                return " ".join(x for x in (e.get("title"), e.get("detail")) if x)
        except Exception:
            pass
        return "sin detalle"
    if "html" in tipo:
        # Corto, pero sin perder el dato: contestar HTML en vez de su JSON de
        # error significa que se rompió antes de su propio manejador.
        return "página de error, no su JSON: se les rompió antes"
    return "sin detalle"


# ═══════════════════════════════════════════════════════════════════════════
# EL BARRIDO — ¿le pasa a TODAS las APIs de Aunesa o a una sola?
# ═══════════════════════════════════════════════════════════════════════════
#
# Pedido del user (2026-08-20): *«que el agente intente conectarse a todas las
# APIs que usamos de Aunesa, a ver si todas le dan el mismo error, ya que esto
# impacta en muchos lados, y darme un análisis general»*.
#
# **Es la pregunta correcta y una sola prueba no la contesta.** Un 500 en el
# login puede ser su servicio entero abajo o el login solo; y son dos situaciones
# muy distintas para el back office: en la primera no hay nada que hacer, en la
# segunda el resto de los datos podría estar entrando igual. Sin barrer, la única
# forma de saberlo es que alguien abra las cinco pantallas.
#
# ⚠️ **El orden importa y no es un detalle**: sin token no se puede probar NADA
# más, así que si el login está caído el barrido se corta y ESO ya es el
# diagnóstico completo — «todo Aunesa está bloqueado porque no se puede entrar».
# Seguir pegándole a los cinco endpoints para juntar cinco 401 sería ruido.

# Las cinco APIs que el sistema usa DE VERDAD, cada una con qué alimenta. Están
# acá y no desperdigadas porque la pregunta «¿qué se rompe si Aunesa se cae?»
# tiene que poder contestarse leyendo un solo lugar.
ENDPOINTS_AUNESA: tuple[tuple[str, str, dict], ...] = (
    ("cuentas/listadoCuentas", "el padrón de cuentas", {}),
    ("cuentas/consultaMovDocsSolicitados",
     "los movimientos del día de Tesorería", {"fechaDesde": "", "fechaHasta": ""}),
    ("operaciones/consolidadosGenerales",
     "la tenencia del día y el cost-basis", {}),
    ("operaciones/informes", "los boletos de operaciones", {"cuenta": "805"}),
    ("cuentas/805/posiciones", "los saldos liquidados por cuenta", {}),
)

TIMEOUT_BARRIDO_S = 20


def barrer_aunesa() -> dict:
    """Prueba las CINCO APIs que usamos y devuelve un análisis general.

    Devuelve `{login, endpoints: [...], analisis, veredicto}`. **Nunca levanta.**
    """
    import time as _t

    import requests

    prueba = probar("aunesa")
    if prueba["veredicto"] != "anda":
        # Sin login no hay token, y sin token no se puede probar nada más. Eso
        # NO es una limitación del barrido: es el diagnóstico.
        return {"login": prueba, "endpoints": [], "veredicto": "todo_caido",
                "analisis": ("Falla el login: no entra nada de ninguna de "
                             "las 5 APIs. " + prueba["detalle"])}

    from core import aunesa
    try:
        cabeceras = aunesa.auth_headers()
    except Exception as e:
        return {"login": prueba, "endpoints": [], "veredicto": "sin_token",
                "analisis": (f"Contesta pero no entramos: {type(e).__name__}. "
                             "Son las credenciales, no su servicio.")}

    filas = []
    for path, para_que, params in ENDPOINTS_AUNESA:
        t0 = _t.monotonic()
        try:
            r = requests.get(f"{aunesa.BASE_URL}/{path}", params=params,
                             headers=cabeceras, timeout=TIMEOUT_BARRIDO_S)
            filas.append({"path": path, "para_que": para_que,
                          "status": r.status_code,
                          "ms": round((_t.monotonic() - t0) * 1000),
                          "ok": r.status_code < 500,
                          "detalle": _leer_error(r) if r.status_code >= 400 else ""})
        except Exception as e:
            filas.append({"path": path, "para_que": para_que, "status": None,
                          "ms": round((_t.monotonic() - t0) * 1000), "ok": False,
                          "detalle": f"{type(e).__name__}: {e}"})
    return {"login": prueba, "endpoints": filas, **_analizar(filas)}


def _analizar(filas: list[dict]) -> dict:
    """El ANÁLISIS GENERAL: qué significa el conjunto, no cada fila.

    Es lo que el user pidió y lo que una lista de cinco status no dice sola. Que
    fallen las cinco y que falle una son dos problemas distintos, y se responden
    distinto: en el primero no hay nada que hacer de este lado; en el segundo el
    resto de los datos sigue entrando y conviene decirlo, porque si no el equipo
    da por perdido todo.
    """
    rotos = [f for f in filas if not f["ok"]]
    if not rotos:
        return {"veredicto": "anda",
                "analisis": ("Las 5 APIs contestan. Si una pantalla sigue "
                             "diciendo caído, esperá 45 s.")}
    if len(rotos) == len(filas):
        return {"veredicto": "todo_caido",
                "analisis": ("Fallan las 5: es su servicio entero. Hay que "
                             "avisarles. Nada que arreglar de este lado.")}
    nombres = ", ".join(f["para_que"] for f in rotos)
    return {"veredicto": "parcial",
            "analisis": (f"Fallan {len(rotos)} de {len(filas)}: {nombres}. "
                         "El resto entra bien.")}
