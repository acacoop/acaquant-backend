"""core/mercado_1816.py — cliente de la API de Mercado de 1816 (vista RESEARCH).

Doc madre: docs/RESEARCH.md. Cambiar de proveedor / URL = tocar solo acá.

Lo que resuelve (verificado el 2026-07-18):
- **Auth**: POST /v1/auth/token (apiKey + module=mercado) → JWT 24h. Cacheado en
  memoria del proceso; re-auth automático al vencer o ante 401.
- **RATE LIMIT DURO (429)**: la API tira "Demasiadas solicitudes" al encadenar
  llamadas. Por eso hay THROTTLE (intervalo mínimo entre requests) + BACKOFF
  exponencial en 429. NADA de hammering — este cliente es para jobs de fondo, la
  latencia no importa.
- **Créditos**: se controlan con balance() (100k/día, 3.1M/mes); el consumo por
  endpoint está en docs/RESEARCH.md §A.4.2.

Env vars (.env del Droplet):
  MERCADO_1816_API_KEY   — sin ella el cliente está apagado (disponible() = False).
  MERCADO_1816_BASE_URL  — override, default https://api.1816.com.ar (verificado).
"""
from __future__ import annotations

import base64
import contextlib
import contextvars
import datetime as _dt
import json
import logging
import os
import re
import threading
import time

from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))

logger = logging.getLogger(__name__)

_BASE = os.getenv("MERCADO_1816_BASE_URL", "https://api.1816.com.ar").rstrip("/")
_MIN_INTERVALO_S = 2.5   # throttle mínimo entre llamadas del MISMO proceso
# El límite que publica el plan es **1 petición por segundo**, y es GLOBAL: no le
# importa cuántos procesos nuestros haya. Se pide 1,2 s para no rozar el borde por
# un redondeo de reloj.
_MIN_INTERVALO_GLOBAL_S = 1.2
_TIMEOUT = 45
_MAX_REINTENTOS = 5

# **RLock, no Lock** (2026-08-17): `_token()` toma el lock y adentro llama a
# `_auth()`, que ahora hace throttle — y `_throttle()` toma el MISMO lock. Con un
# `Lock` simple eso es un deadlock instantaneo. Reentrante es lo correcto acá y
# ademas es lo que queremos: mientras un hilo re-autentica (con sus esperas), los
# demas se quedan en la puerta en vez de salir todos a pedir token a la vez.
_lock = threading.RLock()
_estado: dict = {"token": None, "exp": 0.0, "ultima": 0.0}

# ── LOS LÍMITES REALES DEL PLAN (verificados en el panel, 2026-08-17) ────────
#
#     Créditos diarios       3.863 / 100.000    ← sobra, nunca fue el problema
#     Máx. peticiones/seg    1
#     **Máx. tokens por día    50**             ← ESTE era el «auth HTTP 429»
#
# El token dura **24 h**, así que UNO alcanza para todo el día. Pero vivía en un
# dict de módulo —memoria de CADA proceso— y los procesos que tocan 1816 son
# muchos: `jobs/tamar_1816` corre 15 veces por día, `mercado_1816_series` 1,
# `api.service` pide uno nuevo en cada restart (o sea en cada deploy), más cada
# corrida manual del agente. Cada uno quemaba un token de los 50.
#
# ⚠️ **Y EL BACKOFF LO EMPEORABA.** Un `_auth` que falla reintenta 5 veces, y
# reintentar contra una CUOTA consume justo el recurso que se agotó: cerca del
# tope, cada intento de arreglarlo lo hunde más. Es la trampa conceptual del
# incidente: **el backoff es la respuesta correcta a un rate limit (transitorio)
# y la peor posible a una cuota diaria (no lo es).**
_LOGINS_MAX_DIA = 45          # de 50, con margen: quedarse sin login es peor
_AUTH_REINTENTOS = 2          # NO 5: contra una cuota, reintentar es gastar
_PROVEEDOR = "1816"

# ── PRESUPUESTO DE TIEMPO POR CONTEXTO ───────────────────────────────────────
#
# **El backoff que arregla un job rompe un request interactivo, y el error que
# deja es peor** (incidente 2026-08-17). Al ponerle reintentos a `_auth` contra
# el 429, una llamada pasó a poder tardar 5+10+20+40+60 = 135s solo en
# autenticar, más los reintentos de `_get`. Detrás de Cloudflare eso NO es
# lentitud: es un **HTTP 524** a los 100s — la respuesta se pierde y el usuario
# ve un error que no dice nada de lo que pasó.
#
# La raíz es que la paciencia estaba escrita como una constante del MÓDULO
# cuando en realidad **depende de quién espera**:
#
#     un job de cron        → puede esperar minutos, nadie mira
#     un request del front  → tiene ~100s de Cloudflare y hay alguien mirando
#
# Por eso el presupuesto es un **contextvar** y no un parámetro: se declara UNA
# vez en el borde (el router) y lo respetan TODAS las llamadas de adentro, sin
# tener que pasarlo por seis funciones que no deberían saber de esto. Si se
# agota, se levanta enseguida diciendo que se acabó el tiempo — que es una
# respuesta útil — en vez de seguir esperando hasta que el proxy corte.
_presupuesto: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "mercado_1816_deadline", default=None)


@contextlib.contextmanager
def presupuesto(segundos: float):
    """Acota a `segundos` TODO lo que este bloque le pida a 1816 (esperas de
    throttle y de backoff incluidas). Para usar en el borde HTTP."""
    token = _presupuesto.set(time.monotonic() + float(segundos))
    try:
        yield
    finally:
        _presupuesto.reset(token)


def _resta() -> float | None:
    """Segundos que quedan, o None si nadie puso presupuesto (modo job)."""
    fin = _presupuesto.get()
    return None if fin is None else fin - time.monotonic()


def _alcanza(espera: float) -> bool:
    """¿Entra otra espera de `espera` segundos dentro del presupuesto?"""
    r = _resta()
    return r is None or r > espera


class Error1816(RuntimeError):
    """Fallo de la API de 1816 (auth, rate limit agotado, HTTP no-200)."""


def _api_key() -> str | None:
    return os.getenv("MERCADO_1816_API_KEY")


def disponible() -> bool:
    """True si hay API key configurada (sin ella el cliente no opera)."""
    return bool(_api_key())


_RE_ESPECIE = re.compile(r"^([A-Z]+\d+)[DC]$")   # AL30D/GD30C → AL30/GD30


def normalizar_ticker(t: str | None) -> str:
    """Nuestro ticker → la forma de 1816: mayúsculas y sin la ESPECIE (D/C) final.

    Vive acá porque es una convención DEL PROVEEDOR: nuestros `ticker_corto`
    traen la especie (AL30D/GD30C) y 1816 publica el base (AL30/GD30). Estaba
    duplicada en el job de discovery y en los diags; si las dos copias divergen,
    el cruce de cada una da un universo distinto y nadie se entera.
    Exige letras + dígitos antes de la especie para no mutilar un ticker que
    termina en C/D sin serlo (p.ej. una ON como AER9O queda intacta).
    """
    t = (t or "").strip().upper()
    m = _RE_ESPECIE.match(t)
    return m.group(1) if m else t


def _auth() -> str:
    """Pide un token. **Con throttle, backoff y reintentos, igual que `_get`.**

    ⚠️ **ESTA FUNCION ERA LA CAUSA RAIZ DEL «429» QUE ROMPIA TODO** (2026-08-17).
    `_get` tenía throttle + backoff + 5 reintentos contra el rate limit; `_auth`
    no tenía **nada**: posteaba y levantaba `Error1816` al primer 429. Y como
    `_token()` la llama cada vez que el token falta o vencio, **un solo 429 en el
    endpoint de AUTH tumbaba todo lo que toca 1816** — el agente y los cuatro
    jobs (`tamar_1816` cada 30 min, `ficha_1816`, `mercado_1816_series`,
    `research_mail`), todos a la vez y con el mismo mensaje.

    El sintoma que lo delata es literal y estaba a la vista en la pantalla:
    **«auth HTTP 429»**, no «/cashflow HTTP 429». El path del error decía cuál de
    las dos funciones había fallado y las dos se leían igual de lejos.

    La leccion general: **el reintento se puso donde se veía el trafico (las
    consultas) y no donde estaba el cuello (la autenticacion)**. Toda llamada de
    red que pueda dar 429 necesita la misma disciplina, incluidas las que uno no
    piensa como «consultas».
    """
    key = _api_key()
    if not key:
        raise Error1816("falta MERCADO_1816_API_KEY")
    import requests

    ultimo = ""
    # ⚠️ **DOS INTENTOS, NO CINCO.** Contra un rate limit reintentar es correcto;
    # contra la CUOTA DE 50 TOKENS/DÍA es gastar más de lo que falta. Y desde acá
    # no se puede distinguir un caso del otro —el proveedor manda 429 en los dos—
    # así que se elige el error menos grave: intentar de menos deja al que espera
    # con un mensaje claro; intentar de más se come el presupuesto de mañana.
    for intento in range(_AUTH_REINTENTOS):
        _throttle()
        try:
            r = requests.post(f"{_BASE}/v1/auth/token",
                              json={"apiKey": key, "module": "mercado"},
                              timeout=_TIMEOUT)
        except requests.RequestException as e:
            ultimo = f"{type(e).__name__}: {e}"
            if not _alcanza(3 * (intento + 1)):
                raise Error1816(f"1816 no responde y se agotó el tiempo ({ultimo})") from e
            time.sleep(3 * (intento + 1))
            continue
        if r.status_code == 200:
            d = r.json()
            tok = d.get("token")
            if not tok:
                raise Error1816("auth sin token en la respuesta")
            _estado["token"] = tok
            _estado["exp"] = time.time() + int(d.get("expiresIn", 86400)) - 300
            # **Se comparte inmediatamente.** Es lo que convierte 16-30 logins
            # diarios en 1-2: el próximo proceso lo adopta en vez de pedir otro.
            _guardar_token(tok, _estado["exp"])
            logger.info("mercado_1816: token renovado (expira en %ss) y compartido",
                        d.get("expiresIn"))
            return tok
        if r.status_code == 429 or r.status_code >= 500:
            espera = min(5 * 2 ** intento, 60)
            ultimo = f"HTTP {r.status_code}: {r.text[:120]}"
            # El mensaje NOMBRA la sospecha correcta. Durante todo el incidente del
            # 2026-08-17 decía «rate limit» y mandaba a «reintentá en un par de
            # minutos» — que es exactamente lo que NO había que hacer si el que se
            # había acabado era el cupo de 50 tokens diarios. Un error que sugiere
            # la acción equivocada cuesta más que uno que no sugiere ninguna.
            # ⚠️ **EL CONTADOR NO ES EL DEL PROVEEDOR Y NO PUEDE SERLO.** Cuenta los
            # logins que hicimos NOSOTROS desde que existe la fila: no ve los de
            # antes, ni los de otra máquina, ni los que gastó un proceso que murió
            # sin escribir. Es un piso, no el número real — y la primera versión de
            # este mensaje llegó a decir «van 0 logins hoy, es una ráfaga» mientras
            # el proveedor nos rechazaba, que es justo la conclusión opuesta.
            # Sirve para detectar que NOSOTROS estamos quemando de más; no sirve
            # para afirmar que queda cupo.
            est = estado_token()
            msg = (f"1816 rechazó el login (auth {r.status_code}). El plan da **50 "
                   "tokens por día** y ese suele ser el motivo real de un 429 en "
                   "auth (los créditos, en cambio, sobran). **Reintentar gasta más "
                   "del recurso que falta.** Si el cupo está agotado se recupera "
                   "mañana — o se pega un token de la sesión web con "
                   "`python -m scripts.set_token_1816`. "
                   f"(Nosotros registramos {est['logins_hoy']} login/s hoy, pero "
                   "ese contador es un PISO: no ve los de antes de que existiera "
                   "la tabla ni los de otros procesos.)")
            if not _alcanza(espera) or intento + 1 >= _AUTH_REINTENTOS:
                raise Error1816(msg)
            logger.warning("mercado_1816 auth HTTP %s — backoff %ss (intento %s/%s, "
                           "%s logins hoy)", r.status_code, espera, intento + 1,
                           _AUTH_REINTENTOS, est["logins_hoy"])
            time.sleep(espera)
            continue
        raise Error1816(f"auth HTTP {r.status_code}: {r.text[:200]}")
    raise Error1816(f"auth: agotados {_AUTH_REINTENTOS} reintentos ({ultimo})")


# ── EL TOKEN COMPARTIDO ─────────────────────────────────────────────────────
#
# Todo lo de abajo degrada a "como antes" si Postgres no está: un cliente de red
# no puede quedar inutilizable porque la base no contesta. Sin la tabla se pierde
# el ahorro, no el servicio.


def exp_del_jwt(tok: str) -> float:
    """La expiración que declara el PROPIO token (claim `exp`), o 0 si no se puede
    leer.

    **No se valida la firma** — no es nuestra y no hace falta: acá el JWT se lee
    como lo que es, un sobre con la fecha escrita afuera.

    Existe porque un token puede llegar por fuera del cliente (pegado a mano en
    `manager.tokens_externos` desde la sesión web, que es la vía de escape cuando
    se agotan los 50 logins del día) y en ese caso `expira_at` queda NULL. Sin
    esto, el token **se ignoraría por parecer vencido** y todo seguiría fallando
    igual, que es exactamente lo que pasó la primera vez.
    """
    try:
        payload = (tok or "").split(".")[1]
        payload += "=" * (-len(payload) % 4)          # padding base64url
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp") or 0)
    except Exception:
        return 0.0


def _fila_token() -> dict:
    """La fila del proveedor. `{}` si no se pudo leer — nunca revienta."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT token, extract(epoch from expira_at), dia, "
                        "logins_dia, extract(epoch from llamadas_at) "
                        "FROM manager.tokens_externos WHERE proveedor = %s",
                        (_PROVEEDOR,))
            r = cur.fetchone()
        if not r:
            return {}
        # `expira_at` puede venir NULL (token pegado a mano). El token sabe cuándo
        # vence: se le pregunta a él antes de darlo por muerto.
        exp = float(r[1] or 0) or exp_del_jwt(r[0] or "")
        if not r[1] and exp:
            # Se deja escrita, una sola vez: que la columna diga lo mismo que el
            # token evita que alguien mire la tabla y crea que no vence nunca.
            _sellar_expiracion(r[0], exp)
        return {"token": r[0], "exp": exp, "dia": r[2],
                "logins_dia": int(r[3] or 0), "ultima": float(r[4] or 0),
                "exp_del_jwt": not r[1]}
    except Exception:
        logger.debug("mercado_1816: sin token compartido (Postgres no responde)")
        return {}


def _guardar_token(tok: str, exp: float, *, cuenta_login: bool = True) -> None:
    """Persiste el token para que **los otros procesos no tengan que pedir otro**,
    y suma 1 al contador del día. El contador se resetea solo al cambiar de fecha:
    sin eso el presupuesto quedaría trabado en el número de ayer.

    `cuenta_login=False` para un token que NO nos costó un login (el que se pega a
    mano desde la sesión web): contarlo inflaría un número que ya de por sí es
    frágil."""
    try:
        import os

        from core.postgres import get_pool
        quien = f"{os.getenv('JOB_NAME') or 'api'}:{os.getpid()}"
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO manager.tokens_externos "
                "(proveedor, token, expira_at, obtenido_at, obtenido_por, dia, "
                " logins_dia) VALUES (%s, %s, to_timestamp(%s), now(), %s, "
                " current_date, 1) "
                "ON CONFLICT (proveedor) DO UPDATE SET token = EXCLUDED.token, "
                "  expira_at = EXCLUDED.expira_at, obtenido_at = now(), "
                "  obtenido_por = EXCLUDED.obtenido_por, dia = current_date, "
                "  logins_dia = CASE WHEN manager.tokens_externos.dia = current_date "
                "               THEN manager.tokens_externos.logins_dia + %s ELSE %s END",
                (_PROVEEDOR, tok, exp, quien, int(cuenta_login), int(cuenta_login)))
    except Exception:
        logger.warning("mercado_1816: no se pudo persistir el token compartido",
                       exc_info=True)


def _sellar_expiracion(tok: str, exp: float) -> None:
    """Escribe en la columna la expiración que declaraba el token."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE manager.tokens_externos "
                        "SET expira_at = to_timestamp(%s) "
                        "WHERE proveedor = %s AND token = %s AND expira_at IS NULL",
                        (exp, _PROVEEDOR, tok))
    except Exception:
        logger.debug("mercado_1816: no se pudo sellar la expiración del token")


def _invalidar_token_compartido(muerto: str | None) -> None:
    """Borra el token compartido **solo si sigue siendo el que acaba de fallar**."""
    if not muerto:
        return
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE manager.tokens_externos SET token = NULL, "
                        "expira_at = NULL WHERE proveedor = %s AND token = %s",
                        (_PROVEEDOR, muerto))
    except Exception:
        logger.debug("mercado_1816: no se pudo invalidar el token compartido")


def guardar_token_manual(tok: str, exp_epoch: float) -> None:
    """Persiste un token conseguido POR FUERA del cliente (la sesión web).

    **No suma al contador de logins**: ese token no nos costó uno de los 50 — es
    justamente la vía de escape cuando el endpoint de auth ya no nos da más.
    """
    _guardar_token(tok, exp_epoch, cuenta_login=False)
    with _lock:
        _estado["token"], _estado["exp"] = tok, exp_epoch


def estado_token() -> dict:
    """Cuánto queda del presupuesto de logins. Para SALUD y para mirarlo a ojo:
    **un límite que no se puede ver se descubre siempre cuando ya se agotó.**"""
    f = _fila_token()
    usados = f.get("logins_dia", 0) if f.get("dia") else 0
    return {"proveedor": _PROVEEDOR, "logins_hoy": usados,
            "tope": _LOGINS_MAX_DIA, "restantes": max(0, _LOGINS_MAX_DIA - usados),
            "token_vigente": bool(f.get("token") and time.time() < f.get("exp", 0)),
            "expira_en_s": int(max(0, f.get("exp", 0) - time.time()))}


def _token() -> str:
    """El token, del lugar más barato al más caro. **Un login es un recurso
    escaso** (50 por día) y esta función es la única que los gasta."""
    with _lock:
        # 1) el de este proceso, si sigue vigente → 0 queries, 0 logins
        if _estado["token"] and time.time() < _estado["exp"]:
            return _estado["token"]
        # 2) el COMPARTIDO: otro proceso ya pagó por él y dura 24 h
        f = _fila_token()
        if f.get("token") and time.time() < f["exp"]:
            _estado["token"], _estado["exp"] = f["token"], f["exp"]
            logger.info("mercado_1816: token compartido adoptado (expira en %ds)",
                        int(f["exp"] - time.time()))
            return _estado["token"]
        # 3) recién acá se gasta uno de los 50, y con el presupuesto a la vista
        if f.get("dia") and f.get("logins_dia", 0) >= _LOGINS_MAX_DIA:
            raise Error1816(
                f"presupuesto de tokens agotado: {f['logins_dia']} logins hoy "
                f"(tope {_LOGINS_MAX_DIA} de los 50 del plan). Pedir otro sería "
                "gastar el recurso que falta — se recupera mañana. Si hace falta "
                "antes, revisar qué proceso los está quemando "
                "(`manager.tokens_externos.obtenido_por`).")
        _auth()
        return _estado["token"]


def _marcar_llamada() -> float:
    """Estampa la llamada en la fila compartida y devuelve **cuánto hay que esperar
    para no pasarse de 1 petición por segundo GLOBAL**.

    ⚠️ El throttle de memoria garantiza el intervalo *dentro de un proceso*, y los
    que tocan 1816 son varios y a la vez: `api.service` sirviendo el modal mientras
    `tamar_1816` corre su cron. Dos procesos con 2,5 s cada uno pueden mandar dos
    peticiones en el mismo segundo sin enterarse — y el plan dice **máx. 1/seg**.
    Un `UPDATE ... RETURNING` es atómico, así que el que llega segundo ve el
    timestamp del primero y espera.

    Cuesta un roundtrip (~8,5 ms) contra un throttle de 2,5 s: 0,3 % de overhead
    por una garantía que antes no existía. Si Postgres no contesta devuelve 0 y
    manda el throttle local — degradar es perder la garantía, no el servicio.
    """
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            # En el `SET`, `manager.tokens_externos.llamadas_at` es el valor VIEJO;
            # en el `RETURNING` ya sería el nuevo. Por eso el previo se guarda en su
            # propia columna dentro del MISMO update: así el intervalo se mide en
            # una sola operación atómica y dos procesos no pueden leer lo mismo.
            cur.execute(
                "INSERT INTO manager.tokens_externos (proveedor, llamadas_at) "
                "VALUES (%s, now()) ON CONFLICT (proveedor) DO UPDATE "
                "SET llamadas_at = now(), "
                "    llamadas_prev_at = manager.tokens_externos.llamadas_at "
                "RETURNING extract(epoch from (now() - llamadas_prev_at))",
                (_PROVEEDOR,))
            r = cur.fetchone()
        desde = float(r[0]) if r and r[0] is not None else 999.0
        return max(0.0, _MIN_INTERVALO_GLOBAL_S - desde)
    except Exception:
        return 0.0


def _throttle() -> None:
    """Garantiza _MIN_INTERVALO_S entre llamadas (rate limit). Serializa con el lock
    para que dos hilos no disparen juntos."""
    with _lock:
        espera = max(_MIN_INTERVALO_S - (time.monotonic() - _estado["ultima"]),
                     _marcar_llamada())
        if espera > 0:
            # Ni siquiera el throttle puede pasarse del presupuesto: si no entra,
            # es mejor cortar acá con un mensaje claro que agotar el reloj del
            # proxy y devolver un 524 que no explica nada.
            if not _alcanza(espera):
                raise Error1816("se agotó el tiempo disponible para consultar a "
                                "1816 (el throttle no entra en el presupuesto)")
            time.sleep(espera)
        _estado["ultima"] = time.monotonic()


def _get(path: str, params: dict | None = None) -> dict:
    """GET autenticado con throttle + backoff en 429 + re-auth en 401. Levanta
    Error1816 si se agotan los reintentos o ante un 4xx no recuperable."""
    import requests

    ultimo = ""
    for intento in range(_MAX_REINTENTOS):
        _throttle()
        try:
            r = requests.get(f"{_BASE}{path}",
                             headers={"Authorization": f"Bearer {_token()}"},
                             params=params, timeout=_TIMEOUT)
        except requests.RequestException as e:
            ultimo = f"{type(e).__name__}: {e}"
            if not _alcanza(3 * (intento + 1)):
                raise Error1816(f"1816 no responde y se agotó el tiempo ({ultimo})") from e
            time.sleep(3 * (intento + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:                      # rate limit → backoff
            espera = min(5 * 2 ** intento, 60)
            if not _alcanza(espera):
                raise Error1816(
                    f"1816 está rechazando por rate limit ({path}) y no queda "
                    "tiempo para esperar el backoff — reintentá en un par de "
                    "minutos")
            logger.warning("mercado_1816 429 en %s — backoff %ss (intento %s)",
                           path, espera, intento + 1)
            time.sleep(espera)
            continue
        if r.status_code == 401:                      # token vencido → re-auth
            with _lock:
                muerto = _estado["token"]
                _estado["token"] = None
            # ⚠️ **Y hay que matarlo también en la fila compartida**, o el próximo
            # `_token()` lo adopta de vuelta y se queda en un loop de 401 sin
            # entender por qué. Se borra SOLO si sigue siendo el mismo: si otro
            # proceso ya escribió uno fresco, ese sirve y pisarlo lo tiraría a la
            # basura para nada.
            _invalidar_token_compartido(muerto)
            ultimo = "401 (re-auth)"
            continue
        if r.status_code >= 500:                      # server → retry
            ultimo = f"HTTP {r.status_code}"
            time.sleep(3 * (intento + 1))
            continue
        raise Error1816(f"{path} HTTP {r.status_code}: {r.text[:200]}")  # 4xx no-recup.
    raise Error1816(f"{path}: agotados {_MAX_REINTENTOS} reintentos ({ultimo})")


# ── Endpoints ────────────────────────────────────────────────────────────────


def balance() -> dict:
    """{daily:{used,limit}, monthly:{used,limit}} — control de créditos."""
    return _get("/v1/creditos/balance")


def curvas(texto: str | None = None) -> list[dict]:
    return _get("/v1/mercado/curvas", {"texto": texto} if texto else None)


def instrumentos(texto: str | None = None, curva_id: int | None = None,
                 solo_performing: bool | None = None) -> list[dict]:
    """Catálogo de instrumentos. `solo_performing=False` incluye los VENCIDOS
    (el default de la API es true = solo vigentes). Costo: 1 crédito."""
    p: dict = {}
    if solo_performing is not None:
        p["soloPerforming"] = "true" if solo_performing else "false"
    if texto:
        p["texto"] = texto
    if curva_id:
        p["curvaId"] = curva_id
    return _get("/v1/mercado/instrumentos", p)


def indicadores(tickers: list[str], campos: list[str], fuente: str = "byma",
                plazo: int = 1, moneda: str = "ars",
                fecha_operacion: str | None = None) -> dict:
    """Indicadores a precio de mercado (≤50 tickers por llamada). Devuelve
    {fechaOperacion, fuente, plazo, moneda, instrumentos: {ticker: {campo: v}}}.
    Costo: tickers × campos. OJO: el default de la API es fechaOperacion=HOY —
    en fin de semana/feriado devuelve vacío (aprendido 2026-07-18, corrida de
    sábado con 0 datos): pasar `fecha_operacion` del último día hábil."""
    p: dict = {"tickers": list(tickers)[:50], "campos": list(campos),
               "fuente": fuente, "plazo": plazo, "moneda": moneda}
    if fecha_operacion:
        p["fechaOperacion"] = fecha_operacion
    return _get("/v1/mercado/indicadores", p)


# Cuántas ruedas retroceder cuando la pedida vuelve vacía. Cubre un fin de semana
# largo; más que eso ya no es "todavía no hubo rueda", es que el ticker no tiene
# datos y hay que decirlo en vez de seguir buscando.
MAX_RETROCESO = 4


def _habil_anterior(d: _dt.date) -> _dt.date:
    """Día hábil anterior (solo fines de semana). Los feriados los resuelve el
    retroceso por respuesta vacía — para eso no hace falta un calendario."""
    d -= _dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= _dt.timedelta(days=1)
    return d


# ⚠️ **Campos que 1816 devuelve SIEMPRE**, haya operado el papel o no: son la
# ficha del pedido, no datos de mercado. Distinguirlos NO es cosmético — es lo
# que hace que «¿esta rueda trajo datos?» signifique algo.
#
# **El bug que esto arregla (2026-08-17, TMG27).** El predicado de "trajo datos"
# era `any(campo is not None)`. Mientras se pidieron 4 campos —todos de valor—
# funcionó. Al ampliar la lista a 13 para enriquecer el diagnóstico entraron
# `fuente`, `convencionTna` y `fechaLiquidacion`, que vienen llenos aunque el
# precio sea `null`: el predicado daba **True en la primera vuelta** y el
# retroceso **nunca corría**. Un domingo, o con un papel que no operó, el
# resultado era «no publicó precio» en vez de buscar la rueda anterior.
CAMPOS_METADATA = frozenset({
    "convencionTna", "denominacion", "fechaLiquidacion", "fechaOperacion",
    "fuente", "moneda", "plazo", "ticker",
})


def indicadores_vigentes(tickers: list[str], campos: list[str], *,
                         fecha: str | None = None, max_retroceso: int = MAX_RETROCESO,
                         al_retroceder=None, campos_dato: list[str] | None = None,
                         **kw) -> dict:
    """`indicadores` de la última rueda CON DATOS. Devuelve la respuesta cruda
    más `fechaOperacion` resuelta, o `{}` si ninguna rueda trajo nada.

    **Existe porque esta trampa se pagó dos veces.** Sin `fechaOperacion` la API
    usa HOY, y un domingo devuelve todos los campos en `null`: el 2026-08-16 eso
    hizo parecer que el campo `spread` no existía. Y antes de las 11 ART tampoco
    hay rueda de hoy — en los dos casos el número bueno es el del último día con
    datos, no un vacío.

    Estaba resuelto dentro de `jobs/tamar_1816` y solo ahí. Dos criterios para la
    misma pregunta terminan siempre igual: uno de los dos se queda viejo. Acá vive
    una vez y lo hereda el que llame.

    `al_retroceder(fecha)` es un callback opcional para logear el intento.

    `campos_dato` acota QUÉ campos deciden que la rueda sirve, sin acotar lo que
    se pide. Existe porque «tiene datos» depende del que pregunta: a
    `jobs/tamar_1816` le alcanza con la tasa, pero el simulador del AV AGENT
    necesita el **precio** —lo demás no le sirve para correr el motor— y una
    rueda con TEA modelada y sin operaciones lo dejaba plantado. Por default son
    todos los campos de valor pedidos.
    """
    # Solo los campos de VALOR deciden si la rueda trajo datos — la metadata viene
    # llena siempre y diría que sí aunque no haya un solo precio (ver CAMPOS_METADATA).
    # El `or list(campos)` cubre al que pide únicamente metadata: ahí no hay nada
    # mejor que el criterio viejo, y quedarse con una lista vacía haría que el
    # predicado fuera False SIEMPRE y agotara el retroceso contra la API.
    datos = list(campos_dato or [c for c in campos if c not in CAMPOS_METADATA]
                 or campos)
    d = _dt.date.fromisoformat(fecha) if fecha else _dt.date.today()
    if d.weekday() >= 5:
        d = _habil_anterior(d)
    for _ in range(max_retroceso + 1):
        resp = indicadores(list(tickers), list(campos),
                           fecha_operacion=d.isoformat(), **kw)
        inst = resp.get("instrumentos") or {}
        # "Trajo datos" = algún campo de VALOR no nulo en algún instrumento. No se
        # ata a un campo puntual: el que pide `precioClean` y el que pide `tea`
        # tienen la misma pregunta, y hardcodear uno rompería al otro en silencio.
        if any(v.get(c) is not None for v in inst.values() if v for c in datos):
            return {**resp, "fechaOperacion": resp.get("fechaOperacion") or d.isoformat()}
        if al_retroceder:
            al_retroceder(d)
        d = _habil_anterior(d)
    return {}


# ── Contrato REAL de la API (OpenAPI 1.1.0, `/v1/doc/openapi.json`) ─────────
#
# Relevado el 2026-08-17 contra el spec + `scripts/diag_1816_indicadores`. Antes
# se adivinaban los valores y la API rechaza la llamada ENTERA cuando uno no
# existe, así que un valor inventado no falla en su campo: hace fallar todo.

# ⚠️ **NO existe `usd`.** Y la diferencia entre estas tres NO es cosmética: el
# spec dice *«para instrumentos pagaderos en moneda distinta a ARS, para calcular
# indicadores las cotizaciones se dividen por CCL»* con el default `ars`.
# **NUESTRO motor divide por MEP** (`engines/curvas.py::precio_soberano_a_usd`),
# así que pedir el default y comparar tasas es comparar dos tipos de cambio
# distintos — eso fueron los 202 bps de GD46, no la fórmula.
MONEDAS = ("ars", "ccl", "mep")


def moneda_series(moneda_pago: str | None) -> str:
    """En qué `moneda` pedirle —y leerle— las series a 1816, según en qué moneda
    PAGA el instrumento (`monedaPago` del catálogo, `mkt_1816_instrumentos`).

    **Una sola función porque el writer y el reader no pueden opinar distinto.**
    `jobs/mercado_1816_series` la usa para elegir cómo pedir cada lote y
    `api/services/research_1816_sql` para elegir qué filas leer; si cada uno
    derivara la suya, el día que difieran el gráfico no falla — muestra la serie
    del otro dólar y nadie se entera (REGLA #9). Por eso la regla vive acá, en el
    cliente, que es de quien es la convención.

    **Medido en prod el 2026-09-09** (`scripts/diag_1816_moneda_series --pedir`,
    misma rueda, mismos campos): pedir el default `ars` para un bono que paga en
    dólares devuelve los indicadores al CCL de ellos, no al MEP nuestro —
    BPOB7 dio **TEA 7,25% en `ars` contra 2,44% en `mep`** (481 bps) y paridad
    98,22% contra 102,20% (3,98 pp).

    ⚠️ **Paga ≠ denomina.** Los dólar-linked y los duales están DENOMINADOS en
    USD y pagan en pesos: para ellos `ars` es lo correcto, y encima 1816 no
    publica `mep` (medido con D30O6: todo `None`). Por eso el predicado es
    `monedaPago`, nunca `monedaDenom`. Sin ficha en el catálogo se contesta
    `ars`, que es el default de la API: ante la duda, no cambiar de convención.
    """
    return "mep" if (moneda_pago or "").strip().upper() == "USD" else "ars"


def monedas_de(tickers) -> dict[str, str]:
    """{ticker: moneda con la que hay que pedírselo a 1816}, para una lista.

    Es `moneda_series` aplicada al catálogo: busca el `monedaPago` de cada
    ticker en `research.mkt_1816_instrumentos` (lo llena
    `jobs/mercado_1816_discovery --apply --catalogo`, que corre todos los días)
    y devuelve `mep` para los que pagan en dólares, `ars` para el resto.

    **Existe para que ningún consumidor de 1816 tenga que acordarse.** El
    default de la API es `ars`, que para un bono en dólares calcula al CCL de
    ellos, y omitirlo NO falla: devuelve un número plausible al dólar
    equivocado. Cada llamador que resolviera esto por su cuenta sería otra copia
    del criterio esperando divergir (REGLA #9).

    Acepta la grafía exacta de 1816, sufijos de pata incluidos (`TXMD9 @TAMAR`):
    el catálogo las guarda tal cual. Lo que no está en el catálogo cae en `ars`,
    que es el default de la API — ante la duda no se cambia de convención, y la
    habilidad `hd_1816_al_ccl` del AV AGENT canta si alguno quedó ahí.
    """
    tks = [str(t).strip().upper() for t in (tickers or []) if str(t).strip()]
    if not tks:
        return {}
    # Import diferido: este módulo es el cliente HTTP y no arrastra la base en
    # el import (lo cargan scripts y jobs que a veces corren sin Postgres).
    from core.postgres import get_pool
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker, moneda_pago FROM research.mkt_1816_instrumentos "
                        "WHERE ticker = ANY(%s)", (tks,))
            ficha = dict(cur.fetchall())
    except Exception as e:
        logger.warning("mercado_1816.monedas_de: no pude leer el catálogo (%s) — "
                       "todo a `ars` (el default de la API)", e)
        ficha = {}
    return {tk: moneda_series(ficha.get(tk)) for tk in tks}


def por_moneda(tickers) -> dict[str, list[str]]:
    """{moneda: [tickers]} — el mismo criterio, ya agrupado para llamar.

    `/indicadores` y `/series` llevan UNA moneda por llamada, así que todo el
    que pide un lote mixto tiene que partirlo. Se agrupa acá una vez y no en
    cada job.
    """
    out: dict[str, list[str]] = {}
    for tk, mon in monedas_de(tickers).items():
        out.setdefault(mon, []).append(tk)
    return {m: sorted(tks) for m, tks in out.items()}

# Los campos que `/indicadores` acepta, TEXTUAL del enum del spec.
CAMPOS_INDICADORES = (
    "convencionTna", "currentYield", "denominacion", "duration", "durationMod",
    "fechaLiquidacion", "fechaOperacion", "fuente", "moneda", "paridad", "plazo",
    "precioClean", "precioDirty", "spread", "tea", "tem", "ticker", "tna",
    "ultimaOperacion", "volumenMontoDiario", "volumenNominalDiario",
)

# Las referencias de cálculo que acepta el endpoint de INPUT MANUAL. **Exactamente
# UNA** por llamada (lo dice el spec y lo valida la API).
REFERENCIAS_MANUALES = ("precioClean", "precioDirty", "tna", "tea", "tem",
                        "spread", "paridad")


def indicadores_de(ticker: str, campos: list[str], *, moneda: str = "ars",
                   plazo: int = 1, fecha_operacion: str | None = None,
                   convencion_tna: str | None = None, **referencia) -> dict:
    """`/indicadores/{ticker}` — **input MANUAL**: le das UN precio y te devuelve
    los indicadores calculados a ESE precio.

    **Es el control cruzado perfecto y no lo estábamos usando.** Comparar nuestra
    TEA contra la de ellos tiene un problema: cada uno la calcula sobre SU precio,
    así que una diferencia puede ser la fórmula o puede ser el insumo, y no hay
    forma de saber cuál. Con esto se le pasa NUESTRO precio y lo que vuelve es su
    tasa sobre el MISMO número: cualquier diferencia que quede es pura convención
    o cronograma. Se elimina la variable.

    Costo: **campos** (no tickers × campos) — una comparación sale ~4 créditos.

    `referencia` tiene que traer EXACTAMENTE UNA de `REFERENCIAS_MANUALES`.
    """
    dadas = {k: v for k, v in referencia.items()
             if k in REFERENCIAS_MANUALES and v is not None}
    if len(dadas) != 1:
        raise Error1816(
            f"indicadores_de necesita EXACTAMENTE una referencia de cálculo "
            f"({', '.join(REFERENCIAS_MANUALES)}); llegaron {list(dadas) or 'ninguna'}")
    tk = normalizar_ticker(ticker)
    p: dict = {"campos": list(campos), "moneda": moneda, "plazo": plazo, **dadas}
    if fecha_operacion:
        p["fechaOperacion"] = fecha_operacion
    if convencion_tna:
        p["convencionTna"] = convencion_tna
    return _get(f"/v1/mercado/indicadores/{tk}", p)


def series(tickers: list[str], campos: list[str], desde: str, hasta: str,
           fuente: str = "byma", plazo: int = 1, moneda: str = "ars",
           convencion: str | None = None) -> dict:
    """Series históricas (≤10 tickers, ≤1 año). Devuelve el JSON crudo de la API;
    usar parse_series() para aplanarlo. Costo: tickers × campos × días."""
    p: dict = {"tickers": list(tickers), "campos": list(campos),
               "fechaInicial": desde, "fechaFinal": hasta,
               "fuente": fuente, "plazo": plazo, "moneda": moneda}
    if convencion:
        p["convencionTna"] = convencion
    return _get("/v1/mercado/series", p)


CAMPOS_CASHFLOW = ("fechaPagoEfectiva", "fechaPagoTeorica", "flujoAmortizacion",
                   "flujoInteres", "flujoTotal")


def cashflow(ticker: str, campos: list[str] | None = None) -> dict:
    """Cupones (cashflow) de UN instrumento — `GET /v1/mercado/cashflow/{ticker}`.

    Devuelve {ticker, fechaOperacion, plazo, cashflow: [{campo: valor}, …]} tal
    cual lo manda la API (nada se recalcula acá — decisión 5 de
    docs/RESEARCH.md: 1816 es la fuente de la verdad de los números).
    Costo: **1 crédito por cupón** devuelto → un bono con 20 cupones sale 20.
    `campos` es obligatorio en la API; el default de acá pide los cinco.
    """
    tk = (ticker or "").strip().upper()
    if len(tk) < 3:
        raise Error1816(f"cashflow: ticker inválido {ticker!r} (mínimo 3 caracteres)")
    return _get(f"/v1/mercado/cashflow/{tk}",
                {"campos": list(campos or CAMPOS_CASHFLOW)})


def censar(vencidos: bool = False) -> dict:
    """Universo COMPLETO de 1816: recorre `/curvas` y pide `/instrumentos` por cada
    una. → `{curvas: [{id, nombre, vigentes, total, error}], instrumentos: {ticker: inst}}`,
    donde cada `inst` viene enriquecido con `_curva_id` y `_curva`.

    **Costo: 1 crédito por llamada** → ~29 el censo (1 + 28 curvas), ~57 con
    `vencidos=True`. Es la operación más barata del proveedor y la base del
    AV Agent (`docs/AGENT.md` E1).

    Vive acá y no en un `scripts/diag_*` porque **es el censo del PROVEEDOR** y lo
    usan tres consumidores (el diag de cashflow, el de mapeo y el job del
    AV Agent). Duplicado, dos cruces podían dar universos distintos sin que nadie
    se entere — la misma razón por la que `normalizar_ticker` vive acá.

    Una curva que falla NO aborta el censo: queda con su `error` en la fila y el
    resto sigue. Medido 2026-08-15: 887 tickers únicos vigentes en 28 curvas, y la
    suma por curva da exactamente 887 (ningún ticker se publica en dos curvas)."""
    curvas_cat = curvas() or []
    logger.info("mercado_1816.censar: %s curvas en el catálogo", len(curvas_cat))

    universo: dict[str, dict] = {}
    filas: list[dict] = []
    for c in curvas_cat:
        cid = c.get("id") or c.get("curvaId")
        nombre = c.get("name") or c.get("nombre") or c.get("descripcion") or f"curva {cid}"
        if cid is None:
            continue
        fila = {"id": cid, "nombre": nombre, "vigentes": 0, "total": 0, "error": ""}
        try:
            vig = instrumentos(curva_id=int(cid)) or []
        except Exception as e:
            fila["error"] = str(e)[:80]
            filas.append(fila)
            continue
        fila["vigentes"] = len(vig)
        for inst in vig:
            tk = (inst.get("ticker") or "").strip().upper()
            if tk:
                inst["_curva_id"], inst["_curva"] = cid, nombre
                universo.setdefault(tk, inst)
        if vencidos:
            try:
                fila["total"] = len(instrumentos(curva_id=int(cid),
                                                 solo_performing=False) or [])
            except Exception as e:
                fila["error"] = str(e)[:80]
        filas.append(fila)

    return {"curvas": filas, "instrumentos": universo}


def parse_series(data: dict) -> list[dict]:
    """{instrumentos:{ticker:{campo:[[fecha,valor],…]}}} → filas tidy
    [{ticker, fecha, campo, valor, fuente, moneda, plazo, convencion_tna}]. Pura
    (testeable sin red). Saltea puntos con valor null o mal formados."""
    meta = {
        "fuente": data.get("fuente"), "moneda": data.get("moneda"),
        "plazo": data.get("plazo"), "convencion_tna": data.get("convencionTna"),
    }
    out: list[dict] = []
    for ticker, campos in (data.get("instrumentos") or {}).items():
        for campo, serie in (campos or {}).items():
            for punto in serie or []:
                if not isinstance(punto, (list, tuple)) or len(punto) < 2:
                    continue
                fecha, valor = punto[0], punto[1]
                if fecha is None or valor is None:
                    continue
                out.append({"ticker": ticker, "fecha": fecha, "campo": campo,
                            "valor": valor, **meta})
    return out


# ── LA GRAFÍA CON LA QUE 1816 CONOCE A NUESTRO TICKER ──────────────────────
#
# 1816 publica las patas de un dual como tickers APARTE (`TXMD9 @TAMAR`,
# `TTD26 @BONCAP`), y la grafía exacta —el espacio, la mayúscula, el sufijo— la
# decide su catálogo, no nosotros. Armarla a mano (`f"{tk} @TAMAR"`) parece
# obvio y es frágil: un espacio distinto y la llamada devuelve vacío sin un solo
# error, que se lee como «1816 no lo tiene».
#
# Esta función contesta UNA pregunta: «¿con qué grafía le pido a 1816 la pata
# `ajuste` de este ticker?». Vivía copiada en `scripts/diag_tasa_fija`,
# `scripts/diag_tea_corp_hd` y —con la pregunta hermana, ticker→TODAS sus
# patas— en `jobs/tamar_1816.universo`. Tres copias de la misma tabla de sufijos
# es la REGLA #9 esperando a pasar; acá vive una vez y la leen el detector
# `tasa_vs_1816` y los diags.
SUFIJO_A_AJUSTE = {"TAMAR": "tamar", "CER": "cer", "TASA FIJA": "fija",
                   "BONCAP": "fija", "USD-L": "dolar_linked"}


def _sufijo_1816(tk: str) -> str:
    return tk.split("@", 1)[1].strip().upper() if "@" in tk else ""


def grafias_de(tickers: list[str], ajuste: str = "fija") -> dict[str, str]:
    """`grafía de 1816 → nuestro ticker`, para la pata `ajuste`.

    Un ticker SIN variantes `@` en el catálogo se pide pelado — es lo correcto
    para un bono de una sola pata. Con variantes, entran las que mapean al
    `ajuste` pedido, y también el ALIAS de la `denominacion` cuando difiere de la
    grafía del ticker: en TTD26/TTS26 el catálogo guarda `@TASA FIJA` y la
    denominación dice `@BONCAP`, y sólo la segunda trae datos (2026-08-16). Se
    piden las dos y gana la que conteste; el caller resuelve el empate.

    Lee `research.mkt_1816_instrumentos` (la llena `mercado_1816_discovery
    --catalogo`). Si la tabla no se puede leer, cae a «pelado» para todos: peor
    que un dual mal pedido es no pedir nada.
    """
    por_base: dict[str, list[dict]] = {}
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker, denominacion FROM research.mkt_1816_instrumentos "
                        "WHERE ticker ILIKE '%@%'")
            for t, den in cur.fetchall():
                base = str(t).split("@", 1)[0].strip().upper()
                por_base.setdefault(base, []).append({"ticker": t, "denominacion": den})
    except Exception as e:                       # sin catálogo: todos pelados
        logger.warning("mercado_1816.grafias_de: sin catálogo de variantes (%s)", e)

    out: dict[str, str] = {}
    for tk in tickers:
        tk = (tk or "").strip()
        if not tk:
            continue
        vs = por_base.get(tk.upper())
        if not vs:
            out[tk] = tk
            continue
        for v in vs:
            g = str(v["ticker"])
            if SUFIJO_A_AJUSTE.get(_sufijo_1816(g)) == ajuste:
                out[g] = tk
            suf_den = _sufijo_1816(str(v.get("denominacion") or ""))
            if suf_den and suf_den != _sufijo_1816(g) \
                    and SUFIJO_A_AJUSTE.get(suf_den) == ajuste:
                out[f"{tk} @{suf_den}"] = tk
        out.setdefault(tk, tk)
    return out
