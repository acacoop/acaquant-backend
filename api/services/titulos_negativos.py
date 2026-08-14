"""api/services/titulos_negativos.py — control de nominales NEGATIVOS en T0 y T1.

Back Office → CONTROL TÍTULOS NEGATIVOS. Un nominal negativo significa que se
comprometió un título que no se tiene: o hay una venta en descubierto, o falta que
entre una compra, o hay un error de carga. Sea cual sea, el back office tiene que
verlo el mismo día, no en la conciliación de mañana.

Se devuelven los DOS horizontes, porque contestan preguntas distintas y las dos
importan:

  · **T0** — liquidada A HOY. Es lo que está en custodia AHORA y se puede
    entregar, garantizar o caucionar. Un negativo acá es un descubierto REAL:
    hoy no se puede cumplir.
  · **T1** — liquidada a MAÑANA, con lo concertado hoy adentro. Un negativo acá
    y no en T0 es un descubierto que se VIENE: todavía hay tiempo de resolverlo.

Fuente: `portafolio.tenencia_live`, que refresca el daemon `jobs/tenencia_live.py`
durante la rueda. **No se persiste nada** — esto es una lectura de esa tabla, y la
frescura la dice `actualizado_at`.

**Se excluyen por default MONEDAS y DERIVADOS**, porque en los dos el negativo es
NORMAL y no un problema:
  · efectivo negativo = descubierto bancario — otro problema, otro dueño;
  · derivados negativos = posición vendida — así se representa un short.
Mezclarlos llena la pantalla de filas que no son el tema, y una pantalla llena de
ruido se deja de mirar.
"""
from __future__ import annotations

from api.cache import cached
from api.services._sql import _f, _q
from core.postgres import get_pool


def _exec(sql: str, params: dict) -> int:
    """INSERT/UPDATE/DELETE con commit. `_sql.py` solo expone lectura."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.rowcount
        conn.commit()
        return n

# Carteras donde un nominal negativo NO es un descubierto. La `cartera` sale de
# `portafolio.assets`, así que un asset sin clasificar la tiene NULL — por eso hay
# un segundo criterio en el predicado.
_CARTERAS_EXCLUIDAS = ("MONEDAS", "MONEDA", "DERIVADOS", "DERIVADO")

_HORIZONTES = ("t0", "t1")

# Cuentas que NO entran al control de saldos, por `clientes.comitentes.nivel_5`.
# CDC y OTC no son clientes a los que haya que perseguirles un descubierto, y el
# criterio ya está cargado en la segmentación — usarlo en vez de hardcodear un
# patrón sobre el nombre significa que reclasificar una cuenta en Manager la saca
# (o la trae) sin tocar código. Es el mismo valor que ya filtra el selector
# NIVEL 5 del Tablero Comercial.
NIVEL5_EXCLUIDOS: tuple[str, ...] = ("CDC", "OTC")

# Cuándo TIENE que estar corriendo el daemon de saldos, y cada cuánto late. Sale
# de las constantes del job (`jobs/control_saldos.py`: cron 11:00 UTC = 8 ART,
# HORA_CIERRE_ART = 18, DETECTOR_S = 180) y viaja al front en la respuesta.
#
# Va acá y NO copiado en el frontend porque es la única forma de que la pantalla
# no mienta el día que cambie el horario del cron: con dos copias, una queda
# vieja y nadie se entera hasta que la alarma suena cuando no debe (o peor, no
# suena cuando debe).
VENTANA = {
    "hora_desde": 8,            # ART
    "hora_hasta": 18,           # ART — el daemon termina solo a esta hora
    "dias": [1, 2, 3, 4, 5],    # lunes a viernes (ISO)
    "latido_cada_min": 3,       # DETECTOR_S
    "tolerancia_min": 10,       # margen antes de gritar (3 ciclos perdidos)
}


# ── Cuentas OCULTAS a mano ────────────────────────────────────────────────────
# Lista que el back office maneja desde la propia vista (mismo patrón que el
# catálogo de agentes de SENEBIS). Hay cuentas que van a aparecer siempre y que
# no son un problema que nadie tenga que mirar; en vez de que cada uno las
# saltee con el ojo todos los días, se ocultan una vez y quedan ocultas.
#
# **OCULTAR NO ES EXCLUIR.** La fila se sigue persistiendo igual en
# `portafolio.control_saldos` — el saldo existe y el daemon lo escribe. Lo único
# que pasa es que esta pantalla no lo muestra. Por eso el corte vive ACÁ (en la
# lectura) y no en el job: es una preferencia de visualización, no una regla
# sobre qué es un saldo válido.
#
# Cada fila guarda QUIÉN la ocultó y CUÁNDO: ocultar es esconderle información a
# los demás, así que tiene que tener nombre y fecha.
def _ensure_ocultas() -> None:
    """Crea la tabla si falta. Se llama en cada escritura, no en la lectura.

    La vista es de solo lectura y se sirve aunque la tabla no exista (degrada a
    lista vacía), así que hacer DDL en cada poll sería pagar un viaje a la base
    cada 20 segundos por usuario para nada.
    """
    _exec("""CREATE TABLE IF NOT EXISTS portafolio.control_saldos_ocultas (
            id_cuenta  text PRIMARY KEY,
            cuenta     text,
            motivo     text,
            creado_por text,
            creado_at  timestamptz NOT NULL DEFAULT now())""", {})


def listar_ocultas() -> list[dict]:
    try:
        filas = _q("SELECT id_cuenta, cuenta, motivo, creado_por, creado_at "
                   "FROM portafolio.control_saldos_ocultas ORDER BY creado_at DESC")
    except Exception:
        return []
    return [{"id_cuenta": f["id_cuenta"], "cuenta": f["cuenta"] or "",
             "motivo": f["motivo"] or "", "creado_por": f["creado_por"] or "",
             "creado_at": f["creado_at"].isoformat() if f["creado_at"] else None}
            for f in filas]


def ocultar_cuenta(id_cuenta: str, actor: str, motivo: str = "") -> dict:
    """Agrega una cuenta a la lista de ocultas. Idempotente.

    La denominación se resuelve contra `clientes.cuentas` y se guarda junto a la
    fila: sin eso la pantalla mostraría una lista de números pelados y nadie
    sabría qué está ocultando.
    """
    idc = str(id_cuenta or "").strip()
    if not idc:
        raise ValueError("falta 'id_cuenta'")
    _ensure_ocultas()
    denom = _q("SELECT denominacion FROM clientes.cuentas WHERE id_cuenta = %(c)s",
               {"c": idc})
    cuenta = (denom[0]["denominacion"] if denom else "") or ""
    _exec(
        "INSERT INTO portafolio.control_saldos_ocultas "
        "(id_cuenta, cuenta, motivo, creado_por, creado_at) "
        "VALUES (%(c)s, %(d)s, %(m)s, %(por)s, now()) "
        # Re-ocultar una cuenta ya oculta REFRESCA quién y cuándo: el último que
        # tomó la decisión es el que tiene que figurar.
        "ON CONFLICT (id_cuenta) DO UPDATE SET cuenta = EXCLUDED.cuenta, "
        "motivo = EXCLUDED.motivo, creado_por = EXCLUDED.creado_por, "
        "creado_at = EXCLUDED.creado_at",
        {"c": idc, "d": cuenta, "m": (motivo or "").strip() or None,
         "por": (actor or "").lower() or None})
    return {"id_cuenta": idc, "cuenta": cuenta}


def mostrar_cuenta(id_cuenta: str, _actor: str = "") -> dict:
    """Saca una cuenta de la lista de ocultas — vuelve a verse."""
    idc = str(id_cuenta or "").strip()
    if not idc:
        raise ValueError("falta 'id_cuenta'")
    _ensure_ocultas()
    n = _exec("DELETE FROM portafolio.control_saldos_ocultas WHERE id_cuenta = %(c)s",
              {"c": idc})
    return {"id_cuenta": idc, "borradas": n}

# ⚠️ La CARTERA se lee de `portafolio.assets` EN VIVO, no de la copia congelada en
# `tenencia_live`. Motivo (2026-08-12): el daemon carga el catálogo de assets UNA
# vez al arrancar y lo reusa las 10 horas que corre, así que un título
# reclasificado a mediodía sigue guardándose con la cartera vieja hasta el día
# siguiente. La cartera es una CLASIFICACIÓN (metadato mutable), no un hecho del
# día: tiene que resolverse al momento de mirar. Con el join, corregir un asset en
# Manager se refleja en el próximo poll.
#
# `tl.cartera` queda de respaldo por si la unidad no está en `assets`.
_CARTERA = "upper(coalesce(nullif(a.cartera, ''), tl.cartera, ''))"

# Predicado que deja afuera esas carteras. Dos criterios en OR, y el segundo es la
# red del primero:
#
#   1. `cartera` ∈ excluidas — el dato bueno, cuando está.
#   2. la `unidad` NO empieza con `[` — las unidades de título traen el código de
#      especie entre corchetes (`[8032] MSFT`); el efectivo es la moneda pelada
#      (`ARS`, `USD`, `USDC`). Cubre el asset sin cartera cargada, que si no se
#      colaría como si fuera un título.
#
# El patrón del LIKE va como PARÁMETRO y no interpolado: con `%` literal dentro
# del SQL, psycopg lo toma como placeholder y la query revienta en runtime.
_EXCLUIR = (f"AND NOT ({_CARTERA} = ANY(%(excl)s) "
            "OR tl.unidad NOT LIKE %(pfx)s)")

_COLS = ("tl.id_cuenta, tl.cuenta, tl.unidad, "
         "coalesce(nullif(a.ticker, ''), tl.ticker) AS ticker, "
         f"{_CARTERA} AS cartera, tl.cantidad, tl.actualizado_at")


def _negativos_de(horizonte: str, incluir_todo: bool) -> list[dict]:
    params: dict = {"h": horizonte}
    extra = ""
    if not incluir_todo:
        extra = _EXCLUIR
        params |= {"excl": list(_CARTERAS_EXCLUIDAS), "pfx": "[%"}

    filas = _q(
        f"SELECT {_COLS} "
        "FROM portafolio.tenencia_live tl "
        "LEFT JOIN portafolio.assets a ON a.unidad = tl.unidad "
        "WHERE tl.horizonte = %(h)s "
        "  AND tl.fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live) "
        "  AND tl.cantidad < 0 "
        f"  {extra} "
        "ORDER BY tl.cantidad ASC",
        params,
    )
    return [{
        "id_cuenta": f["id_cuenta"],
        "cuenta": f["cuenta"] or f"[{f['id_cuenta']}]",
        "unidad": f["unidad"],
        "ticker": f["ticker"] or f["unidad"],
        "cartera": f["cartera"] or "",
        "cantidad": _f(f["cantidad"]),
        "actualizado_at": (f["actualizado_at"].isoformat()
                           if f["actualizado_at"] else None),
    } for f in filas]


def _saldos() -> dict:
    """Saldos de EFECTIVO del día, desde `portafolio.control_saldos`.

    Es otra fuente y otra pregunta que la de arriba. `tenencia_live` es una
    posición PROYECTADA (mete adentro lo que todavía no liquidó), así que una
    caución que vence mañana deja la cuenta en falso negativo hoy — y por eso el
    efectivo estaba excluido de esta pantalla: era ruido. `control_saldos` guarda
    lo **liquidado** (endpoint `cuentas/{id}/posiciones`), donde un negativo es un
    descubierto REAL. Verificado contra la cuenta 805 el 2026-08-13.

    El signo ya viene corregido por el daemon: acá `cantidad < 0` es, sin
    interpretación, plata que falta.

    Se devuelven los saldos **POSITIVOS Y NEGATIVOS** (2026-08-13): la pantalla es
    de 50/50, con lo que está a favor de un lado y el descubierto del otro. El
    corte por signo y por moneda lo hace el front sobre estas mismas filas — así
    cambiar de moneda es instantáneo y los totales de las dos mitades no pueden
    contradecirse entre sí por venir de dos consultas distintas.

    ⚠️ Degrada en silencio a `disponible: false` si la tabla todavía no existe.
    La escribe un daemon nuevo y el `apply_schema` puede no haber corrido aún:
    sin esto, una tabla faltante tumbaría con un 500 la pantalla ENTERA de
    control de negativos, que hoy funciona y no depende de esto.
    """
    try:
        # Una sola query trae el saldo, QUIÉN atiende la cuenta y su nivel_5. El
        # operador sale del mismo viaje que el saldo: pedirlo aparte serían 1.900
        # roundtrips a Supabase por nada (el peaje es ~8.5ms cada uno, y lo que
        # cuesta es la CANTIDAD de queries, no su plan).
        filas = _q(
            "SELECT cs.id_cuenta, cs.cuenta, cs.ticker, cs.cantidad, "
            "       cs.actualizado_at, c.nivel_5, c.operador_email, "
            "       o.nombre AS operador_nombre "
            "FROM portafolio.control_saldos cs "
            "LEFT JOIN clientes.comitentes c ON c.id_cuenta = cs.id_cuenta "
            "LEFT JOIN clientes.operadores o ON o.email = c.operador_email "
            "WHERE cs.fecha = (SELECT MAX(fecha) FROM portafolio.control_saldos) "
            "ORDER BY cs.cantidad ASC")
        # El LATIDO viaja como subconsulta escalar en la MISMA query del meta: es
        # otra tabla, pero pedirlo aparte sería un roundtrip más a Supabase por
        # cada poll de cada usuario, y lo que se paga acá es la cantidad de
        # queries, no su plan.
        meta = _q("SELECT MAX(fecha) AS fecha, MAX(actualizado_at) AS ult, "
                  "       count(DISTINCT id_cuenta) AS cuentas, "
                  "       (SELECT updated_at FROM operaciones.motor_heartbeat "
                  "        WHERE id = 'control_saldos') AS latido "
                  "FROM portafolio.control_saldos "
                  "WHERE fecha = (SELECT MAX(fecha) FROM portafolio.control_saldos)")[0]
    except Exception:
        return {"disponible": False, "n": 0, "n_negativos": 0, "filas": [],
                "fecha": None, "actualizado_at": None, "cuentas_en_control": 0,
                "ocultas": 0, "excluidos": list(NIVEL5_EXCLUIDOS),
                "ocultas_manual": 0, "lista_ocultas": [], "latido_at": None,
                "ventana": VENTANA}

    # Los dos cortes se hacen en PYTHON y no en el WHERE a propósito: filtrando en
    # SQL las filas ocultas desaparecen sin dejar rastro y la pantalla no puede
    # decir «además hay 4 que no te muestro». Son pocas filas, así que el filtro en
    # memoria no cuesta nada y devuelve el número.
    #
    # Se cuentan POR SEPARADO (`ocultas` = por nivel_5 / `ocultas_manual` = las que
    # alguien escondió a mano) porque son dos decisiones distintas y con dueños
    # distintos: una la fija la segmentación y la otra una persona con nombre.
    lista_ocultas = listar_ocultas()
    ids_ocultas = {o["id_cuenta"] for o in lista_ocultas}
    visibles, ocultas, ocultas_manual = [], 0, 0
    for f in filas:
        if (f["nivel_5"] or "").strip().upper() in NIVEL5_EXCLUIDOS:
            ocultas += 1
            continue
        if f["id_cuenta"] in ids_ocultas:
            ocultas_manual += 1
            continue
        visibles.append(f)

    return {
        "disponible": True,
        "fecha": meta["fecha"].isoformat() if meta["fecha"] else None,
        "actualizado_at": meta["ult"].isoformat() if meta["ult"] else None,
        # LATIDO ≠ ACTUALIZADO, y la diferencia es todo el punto: `actualizado_at`
        # es la última vez que una cuenta CAMBIÓ, y el latido la última vez que el
        # daemon MIRÓ. Sin los dos no se puede distinguir «no se movió nadie» de
        # «el daemon está muerto».
        "latido_at": meta["latido"].isoformat() if meta["latido"] else None,
        # La ventana en la que el daemon TIENE que estar vivo, servida por el
        # backend y no hardcodeada en el front: los horarios son del job, y dos
        # copias del mismo horario se desincronizan el día que uno cambia.
        "ventana": VENTANA,
        "cuentas_en_control": meta["cuentas"],
        "ocultas": ocultas,
        "excluidos": list(NIVEL5_EXCLUIDOS),
        "ocultas_manual": ocultas_manual,
        # La lista viaja completa: es el ABM de la pantalla, son pocas filas y
        # traerla acá evita un segundo request para abrir el panel.
        "lista_ocultas": lista_ocultas,
        "n": len(visibles),
        # El contador de la solapa: lo que hay que mirar son los descubiertos, no
        # el total de saldos. Se cuenta acá y no en el front para que el número de
        # la solapa no dependa de qué moneda esté elegida.
        "n_negativos": sum(1 for f in visibles if (f["cantidad"] or 0) < 0),
        "filas": [{
            "id_cuenta": f["id_cuenta"],
            "cuenta": f["cuenta"] or f"[{f['id_cuenta']}]",
            "ticker": f["ticker"],
            "cantidad": _f(f["cantidad"]),
            # Sin operador cargado la fila igual se muestra: un descubierto no se
            # oculta porque falte la segmentación — al revés, que no tenga dueño
            # es información.
            "operador": f["operador_nombre"] or f["operador_email"] or "",
            "nivel_5": f["nivel_5"] or "",
            "actualizado_at": (f["actualizado_at"].isoformat()
                               if f["actualizado_at"] else None),
        } for f in visibles],
    }


@cached(ttl=10)
def titulos_negativos(incluir_todo: bool = False) -> dict:
    """Nominales negativos en los DOS horizontes, cada uno ordenado del peor al menos.

    `incluir_todo=True` trae también MONEDAS y DERIVADOS — para el caso en que se
    quiera mirar todo junto, no para el uso diario.

    TTL de 10s: el daemon reescribe una cuenta como mucho cada 60s (debounce) y la
    vista pollea cada 20s — 10s colapsa las ráfagas de varios usuarios sin que
    nadie vea un dato viejo.
    """
    horizontes = {h: _negativos_de(h, incluir_todo) for h in _HORIZONTES}

    # Frescura y fecha salen de una query aparte y NO de las filas devueltas: si no
    # hay ningún negativo (el caso bueno) igual hay que poder decir "mirado a las
    # 14:32", o la pantalla vacía se lee como "no cargó" — o peor, como "está todo
    # bien" con el daemon caído.
    meta = _q(
        "SELECT MAX(fecha) AS fecha, MAX(actualizado_at) AS ult, "
        "       count(DISTINCT id_cuenta) AS cuentas "
        "FROM portafolio.tenencia_live "
        "WHERE fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live)"
    )[0]

    return {
        "fecha": meta["fecha"].isoformat() if meta["fecha"] else None,
        "actualizado_at": (meta["ult"].isoformat() if meta["ult"] else None),
        "cuentas_en_posicion": meta["cuentas"],
        "incluir_todo": incluir_todo,
        "t0": {"n": len(horizontes["t0"]), "filas": horizontes["t0"]},
        "t1": {"n": len(horizontes["t1"]), "filas": horizontes["t1"]},
        # Bloque NUEVO y ADITIVO: el front lo puede ignorar sin romperse. Va en la
        # misma respuesta y no en un endpoint aparte porque es la misma pantalla y
        # un segundo request para dos tablas que se miran juntas es un viaje de más.
        "saldos": _saldos(),
    }
