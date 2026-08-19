"""core/curvas_sql.py — lectura del master de renta fija desde SQL.

El master vive en `mercado.curvas` (Postgres). La columna
`data` jsonb = el doc COMPLETO (mismo shape: fechas como strings ISO, flujos
anidados) → los readers que hacen `.get('campo')` sobre el doc funcionan
sin cambios. Las columnas tipadas (`curva`, `ticker_corto`, `fecha_vencimiento`)
se usan SOLO para filtrar barato.

Capa `core/` → usable por engines (loader de motores) y por api/services.
Master chico (~57 instrumentos) → se traen los docs completos sin problema.
"""
import threading
import time

from core import curvas_ejes as ce
from core.postgres import get_pool

# Cache en memoria del MASTER completo (perf 2026-06-29). El master cambia ~1×/semana
# (alta/baja de instrumento) pero 6 services (renta_fija, fair_value, carry_trade,
# sinteticos, order_book, acreencias) lo leían en CADA request → traían + deserializaban
# los ~223 docs jsonb (con `flujos`) cada vez (cargar_todos medía 56ms). Lo cacheamos
# 300s y servimos por_curva/find_one filtrando en memoria (master chico → instantáneo).
# Tras un alta/baja de bono llamar `invalidar()` para no esperar el TTL.
_TTL = 300.0
_cache: dict = {"data": None, "ts": 0.0}
_lock = threading.Lock()


# Columnas que el blob `data` NO tiene y que hay que MERGEAR encima de cada doc.
#
# El blob es la forma vieja del master: se congeló antes de que existieran los
# ejes, y `jobs/ficha_1816` escribe el emisor estandarizado en la COLUMNA. O sea
# que quien lee por acá —y son ~500 lugares— veía un doc CIEGO a la clasificación
# y con el emisor viejo. Ya se cobró dos incidentes (el editor de Manager borraba
# el emisor; el form de bonos cargaba los ejes vacíos y guardar los borraba).
#
# La COLUMNA SIEMPRE GANA: es la que escriben los procesos nuevos. Un valor del
# blob que sobreviva es, por definición, uno que nadie migró todavía.
_COLS_FUERA_DEL_BLOB = ("emisor_tipo", "moneda_eje", "ajuste", "ajuste_alt",
                        "ley", "emisor", "sector")

# ⚠️ **LOS DOS CAMPOS DE SÍMBOLO, QUE HABÍAN QUEDADO SIN ÁRBITRO** (2026-08-19).
#
# El renombre del 2026-08-15 migró las COLUMNAS y dejó el blob intacto a
# propósito (lo leen ~500 lugares) — pero con el significado **INVERTIDO**:
#
#     COLUMNA   ticker = «AL30»            instrumento  = «MERV - XMEV - AL30 - 24hs»
#     BLOB      ticker = «MERV - XMEV - …» ticker_corto = «AL30»
#
# Mientras los dos digan lo mismo no pasa nada, y por eso durante cuatro días no
# pasó. **Pero nadie los estaba manteniendo iguales**, y el día que divergieron
# el sistema se partió en dos mitades que no se hablan:
#
#     el MOTOR escribe el precio leyendo el BLOB   (`engines/curvas.py`)
#     la VISTA busca el precio por la COLUMNA      (`LEFT JOIN … = c.instrumento`)
#
# Medido en prod: **2 de 229** (AO29 y CO32). En los dos la columna ya tenía la
# pata correcta —la en dólares— y el blob la vieja en pesos, así que el motor
# suscribía una pata y la pantalla buscaba la otra: la fila salía **entera en
# «--»** con el precio existiendo, y ningún detector lo veía porque el agente
# también lee por el blob. Nada fallaba. Simplemente no se encontraban.
#
# Se resuelve con la MISMA regla que el resto —**la columna gana**— pero con
# ALIAS, porque los nombres están cruzados. Sin el alias, `doc["ticker"]` pasaría
# a valer «AL30» y los ~500 lugares que lo usan como símbolo de mercado se
# romperían todos juntos.
_ALIAS_DEL_BLOB = {"instrumento": "ticker",      # símbolo de mercado
                   "ticker": "ticker_corto"}     # el corto, AL30


def _load_all() -> list[dict]:
    try:
        cols = ", ".join(list(_COLS_FUERA_DEL_BLOB) + list(_ALIAS_DEL_BLOB))
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT data, {cols} FROM mercado.curvas")
            nombres = [d[0] for d in cur.description][1:]
            out = []
            for fila in cur.fetchall():
                doc = fila[0]
                if doc is None:
                    continue
                # Solo los NO nulos: una columna vacía no puede borrar lo que el
                # blob sí tenga. Completar sí, pisar con nada no.
                doc.update({_ALIAS_DEL_BLOB.get(k, k): v
                            for k, v in zip(nombres, fila[1:], strict=False)
                            if v is not None})
                out.append(doc)
            return out
    except Exception:
        return []


def _master() -> list[dict]:
    """Lista RAW cacheada del master (uso interno, NO mutar)."""
    now = time.monotonic()
    if _cache["data"] is not None and (now - _cache["ts"]) < _TTL:
        return _cache["data"]
    with _lock:
        if _cache["data"] is None or (time.monotonic() - _cache["ts"]) >= _TTL:
            _cache["data"] = _load_all()
            _cache["ts"] = time.monotonic()
        return _cache["data"]


def invalidar() -> None:
    """Forzar recarga del master en la próxima lectura (tras alta/baja de instrumento)."""
    _cache["data"] = None
    _cache["ts"] = 0.0


# Las públicas devuelven SHALLOW-COPIES de los docs (el cache comparte objetos; copiar
# preserva el contrato "dicts frescos" del path Mongo a costo ~nulo vs re-query/deserializar).
def cargar_todos() -> list[dict]:
    """Todos los docs del master (cacheado 300s, in-process). Equivale a find({})."""
    return [dict(d) for d in _master()]


# ── Pertenencia a una curva: la deciden los EJES, no la columna ──────────────
#
# `mercado.curvas.curva` es UNA PALABRA ESCRITA A MANO por fila. Mientras fue la
# fuente, tres cosas rompían sin dar error:
#
#   · un DUAL (CER+TAMAR) sólo podía tener una palabra → se escondía de una de
#     sus dos tablas;
#   · los 6 corporativos en USD estaban escritos como `soberanos` porque no había
#     otro lugar donde ponerlos;
#   · agregar un bono y olvidarse la palabra lo hacía invisible en silencio.
#
# Ahora la pertenencia se DERIVA de los ejes (`emisor_tipo`/`moneda_eje`/`ajuste`/
# `ajuste_alt`), con la MISMA función que usa la vista (`curvas_ejes.pills`). No
# hay dos criterios que puedan divergir: hay uno.
#
# ⚠️ Un bono SIN ejes no cae en ninguna curva — desaparece de todo lo que llame
# acá. Es a propósito: antes caía en la curva que dijera su palabra aunque nadie
# lo hubiera clasificado. `sin_curva()` los lista para que la mesa los complete.


def esta_en_curva(doc: dict, curva: str) -> bool:
    """¿Este doc del master pertenece a esa curva? Predicado ÚNICO del sistema.

    Existe para que quien ya tiene el doc en la mano (el editor de bonos, el
    validador de pares de breakevens) no vuelva a mirar la columna `curva` y
    contradiga a `por_curva`."""
    return curva in ce.curvas_de(ce.ejes_de_doc(doc))


def por_curva(curva: str) -> list[dict]:
    """Docs de una curva (tasa_fija | cer | soberanos | dolar_linked | tamar).

    Un DUAL sale en las DOS curvas de sus dos patas: es el mismo bono mirado con
    dos lentes, no dos bonos."""
    return [dict(d) for d in _master() if esta_en_curva(d, curva)]


def corporativos() -> list[dict]:
    """Las ONs. Era `por_curva_like('on%')`: el sector del emisor metido dentro del
    nombre de la curva (`on_energia`, `on_finanzas`, `on_otros`). Ser corporativo
    es un EJE del emisor, no una curva — y la curva de una ON en USD a tasa fija
    es `soberanos` (hard dólar), que es donde su rendimiento se compara."""
    return [dict(d) for d in _master() if d.get("emisor_tipo") == "corporativo"]


def no_corporativos() -> list[dict]:
    """El complemento de `corporativos()`. Incluye los que no tienen `emisor_tipo`
    cargado — igual que el viejo `not_like('on%')` incluía los de curva NULL: sin
    dato, un bono se muestra, no se esconde."""
    return [dict(d) for d in _master() if d.get("emisor_tipo") != "corporativo"]


def sin_curva() -> list[dict]:
    """Los que no caen en NINGUNA curva: sin ejes, o con un ajuste que todavía no
    tiene curva (badlar/tpm/caución). Son los que hay que clasificar a mano."""
    return [dict(d) for d in _master() if not ce.curvas_de(ce.ejes_de_doc(d))]


def find_one(ticker_corto: str) -> dict | None:
    """Doc por ticker_corto (PK), o None. Equivale a find_one({'ticker_corto': X})."""
    d = next((x for x in _master() if x.get("ticker_corto") == ticker_corto), None)
    return dict(d) if d is not None else None


def agrupado_por_curva() -> dict[str, list[dict]]:
    """Docs agrupados por curva (ignora los que no caen en ninguna).
    forwards/breakevens. MISMO criterio que `por_curva` — un dual aparece en dos
    grupos, que es lo que hace que su TEA entre a las dos matrices."""
    grupos: dict[str, list[dict]] = {}
    for d in cargar_todos():
        for c in ce.curvas_de(ce.ejes_de_doc(d)):
            grupos.setdefault(c, []).append(d)
    return grupos


def indexado_por_ticker() -> dict[str, dict]:
    """Dict ticker → doc (ignora sin ticker). curvas enriquece TimeSales."""
    return {d["ticker"]: d for d in cargar_todos() if d.get("ticker")}
