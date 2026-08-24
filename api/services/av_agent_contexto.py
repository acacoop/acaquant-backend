"""api/services/av_agent_contexto.py — LO QUE EL AGENTE SABE DE LA BASE, SIN QUE NADIE SE LO ESCRIBA.

Doc madre: **`docs/AV_AGENT.md`** §0.r.

Pedido del user (2026-08-19), y son dos cosas que van juntas:

    *«Que el agente sepa exactamente cada tabla que hay, y exactamente cómo
    funciona esa tabla en cuanto a los datos. Es decir: si portfolio en AuM
    actualiza con fecha T-1, ok, que el agent diga qué día es hoy, cuándo es T-1,
    y vaya a buscar: ¿hay datos? sí, no. Bueno, pasa algo o no pasa nada.»*

    *«Que no dependa de un git pull, que no dependa de cosas estáticas. Que
    siempre sepa qué hay en las bases, de schema y eso, o de tablas posta.»*

POR QUÉ NO HAY NINGUNA LISTA ACÁ
=================================

La respuesta obvia sería escribir el contrato de cada tabla: *«`portafolio.tenencia`
es diaria, `mercado.market_snapshot` es live, …»*. **Es la respuesta equivocada, y
por dos razones distintas:**

1. **Nadie mantiene 200 contratos.** La lista quedaría vieja el primer mes — y una
   lista vieja es PEOR que no tener ninguna, porque afirma cosas falsas con la
   misma cara que las verdaderas. Este proyecto ya lo pagó y por eso `MAPA_APP.md`
   §0, `SISTEMA.md` y el catálogo de SKILLS se autogeneran.
2. **Depender de un `git pull` para que el agente sepa qué existe es exactamente
   lo contrario de un agente.** Una tabla creada el martes tiene que estar en su
   cabeza el martes, no cuando alguien se acuerde de anotarla.

Así que **todo se DERIVA de la base misma**:

    QUÉ TABLAS HAY          →  pg_catalog. Completo siempre, sin mantenimiento.
    CUÁL ES SU FECHA        →  information_schema, primera columna temporal.
    CADA CUÁNTO SE ESCRIBE  →  **se MIDE** mirando la distribución de esa columna.

Lo tercero es la parte no obvia y es la que hace que esto escale: **la cadencia de
una tabla no hay que declararla, la tabla la dice**. Si el intervalo típico entre
escrituras es de segundos, es tiempo real; si es de un día hábil, es diaria. Se
mide con la MEDIANA de las diferencias (no el promedio: un hueco de fin de semana
o un backfill viejo desplazarían la media y no la mediana).

DÓNDE SÍ MANDA UN CONTRATO DECLARADO, Y POR QUÉ
================================================

⚠️ **La cadencia aprendida tiene un punto ciego, y es importante entenderlo:** si
el job de tenencias lleva tres días roto, la «normalidad observada» de la tabla se
corre sola y el detector deja de avisar — se acostumbra al problema. Por eso los
`CONTRATOS` declarados de `salud.py` (8, las tablas donde una demora cuesta plata)
**siguen mandando donde existen**: ahí el «debería» es una decisión de negocio, no
un promedio. La derivación cubre las otras ~190 que hoy son un punto ciego total.

Es la misma lógica que en la latencia: comparar contra uno mismo funciona para
detectar un cambio, y NO funciona para detectar algo que está mal desde siempre.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from core.postgres import get_pool
from core.tz import hora_ar

logger = logging.getLogger(__name__)

# Las columnas que pueden marcar "cuándo se escribió esta fila", en orden de
# preferencia. La primera que exista gana. No es una lista de tablas: es una
# lista de CONVENCIONES de nombre del propio repo, y por eso no envejece igual.
COLS_FECHA = ("updated_at", "ingestado_en", "generado_at", "creado_at",
              "created_at", "ts", "fecha", "concertacion", "hora")

# Los tramos que separan una cadencia de otra, medidos sobre el intervalo típico
# entre escrituras. Los bordes son generosos a propósito: lo que se busca es
# CLASIFICAR para saber qué esperar, no medir con precisión.
_TRAMOS: tuple[tuple[int, str], ...] = (
    (5 * 60,        "tiempo_real"),    # escribe cada pocos minutos o menos
    (6 * 3600,      "intradiaria"),    # varias veces por día
    (36 * 3600,     "diaria"),         # una por día (hábil o corrido — ver abajo)
    (10 * 86400,    "semanal"),
    (45 * 86400,    "mensual"),
)

# Cuántas escrituras hacen falta para animarse a decir una cadencia. Con 3 puntos
# cualquier cosa parece un patrón.
MIN_MUESTRAS = 8
# Cuántas filas se miran para medir. No se escanea la tabla entera: se toman las
# últimas N por su columna de fecha, que es justo lo que describe el comportamiento
# ACTUAL — una tabla que hace un año era diaria y hoy es live tiene que decir live.
MUESTRA = 500


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def inventario() -> list[dict]:
    """**TODAS las tablas que existen AHORA**, con su columna de fecha si la tiene.

    UNA query contra el catálogo de Postgres. No hay lista, no hay `git pull`, no
    hay nada que se pueda quedar viejo: una tabla creada hoy sale acá hoy.
    """
    filas = _q("""
        SELECT n.nspname AS schema, c.relname AS tabla,
               COALESCE(s.n_live_tup, 0)::bigint AS filas,
               array_agg(a.attname ORDER BY a.attnum)
                 FILTER (WHERE t.typname IN ('timestamptz','timestamp','date'))
                 AS cols_fecha
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
        LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0
                                AND NOT a.attisdropped
        LEFT JOIN pg_type t ON t.oid = a.atttypid
        WHERE c.relkind = 'r'
          AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
        GROUP BY n.nspname, c.relname, s.n_live_tup
        ORDER BY n.nspname, c.relname
    """)
    out = []
    for f in filas:
        candidatas = list(f.get("cols_fecha") or [])
        # La convención del repo gana; si la tabla no usa ninguna, se toma la
        # primera columna temporal que tenga. Mejor una imperfecta que ninguna:
        # sin columna de fecha la tabla queda como punto ciego, que es lo que
        # esto viene a eliminar.
        col = next((c for c in COLS_FECHA if c in candidatas),
                   candidatas[0] if candidatas else None)
        out.append({"schema": f["schema"], "tabla": f["tabla"],
                    "filas": int(f["filas"] or 0), "col_fecha": col,
                    "cols_fecha": candidatas})
    return out


def medir(schema: str, tabla: str, col: str) -> dict:
    """**Cada cuánto se escribe esta tabla**, medido — no declarado.

    Se toman las últimas `MUESTRA` escrituras y se calcula la MEDIANA de las
    diferencias. Mediana y no promedio: un fin de semana, un feriado o un
    backfill viejo desplazan la media y dejarían a una tabla diaria pareciendo
    semanal.
    """
    from statistics import median

    # schema/tabla/col salen del catálogo de Postgres, no de entrada de usuario.
    try:
        filas = _q(f'SELECT "{col}" AS t FROM "{schema}"."{tabla}" '
                   f'WHERE "{col}" IS NOT NULL ORDER BY "{col}" DESC LIMIT {MUESTRA}')
    except Exception as e:
        logger.debug("contexto: no pude medir %s.%s: %s", schema, tabla, e)
        return {"cadencia": None, "intervalo_p50_s": None, "ultimo_dato": None}
    if not filas:
        return {"cadencia": "vacia", "intervalo_p50_s": None, "ultimo_dato": None}

    momentos = [_a_dt(f["t"]) for f in filas]
    momentos = [m for m in momentos if m is not None]
    ultimo = momentos[0] if momentos else None
    if len(momentos) < MIN_MUESTRAS:
        # Con pocas escrituras no se afirma un patrón. `eventual` NO es "no sé":
        # es "esta tabla no tiene ritmo", que es un dato real (carga manual, un
        # catálogo que se toca cada tanto) y significa que no se le puede exigir
        # frescura.
        return {"cadencia": "eventual", "intervalo_p50_s": None,
                "ultimo_dato": ultimo}

    difs = [(momentos[i] - momentos[i + 1]).total_seconds()
            for i in range(len(momentos) - 1)]
    difs = [d for d in difs if d > 0]      # las escrituras simultáneas no son ritmo
    if not difs:
        # Todas al mismo instante: es una carga de una sola vez (una siembra, un
        # import). No tiene cadencia.
        return {"cadencia": "estatica", "intervalo_p50_s": 0, "ultimo_dato": ultimo}

    p50 = median(difs)
    cadencia = next((n for tope, n in _TRAMOS if p50 <= tope), "eventual")

    # ⚠️ **UNA RÁFAGA NO ES UN RITMO** — el error que produjo 47 falsos positivos
    # en la primera corrida (2026-08-19). Una tabla de AUDITORÍA o un catálogo se
    # escribe A LOS SALTOS: alguien edita y entran 15 filas con dos segundos de
    # diferencia, y después nada por tres semanas. La mediana de los intervalos
    # mira ADENTRO de la ráfaga y dice «tiempo real», así que `clientes.aca_valores`
    # —sin escribir hace 43 días— salía clasificada como live y atrasada.
    #
    # El arreglo NO es subir la tolerancia (taparía las tablas que sí importan):
    # es preguntar otra cosa. Una tabla con ritmo rápido escribe **CASI TODOS LOS
    # DÍAS**; una a ráfagas, no. Se mide con la misma vara de siempre —observando,
    # sin declarar nada— y la que no llega se degrada a `eventual`, que ya
    # significa «no se le puede exigir frescura».
    if cadencia in _EXIGEN_REGULARIDAD:
        dias = _dias_con_escritura(schema, tabla, col)
        if dias is not None and dias < DIAS_MINIMOS:
            return {"cadencia": "eventual", "intervalo_p50_s": int(p50),
                    "ultimo_dato": ultimo, "dias_con_escritura": dias,
                    "degradada_de": cadencia}
    # Una "diaria" que solo escribe de lunes a viernes es DISTINTA de una que
    # escribe los siete días: exigirle el sábado a la primera es un falso positivo
    # garantizado todos los fines de semana.
    if cadencia == "diaria" and _solo_habiles(momentos):
        cadencia = "diaria_habil"
    return {"cadencia": cadencia, "intervalo_p50_s": int(p50), "ultimo_dato": ultimo}


# Las cadencias que AFIRMAN que la tabla escribe seguido. Son las únicas que hay
# que verificar contra el calendario: a una `semanal` o una `mensual` no se le
# puede pedir regularidad diaria sin contradecir su propia definición.
_EXIGEN_REGULARIDAD = frozenset({"tiempo_real", "intradiaria", "diaria"})
DIAS_VENTANA = 30       # sobre cuántos días corridos se mira la regularidad
DIAS_MINIMOS = 10       # ~la mitad de los hábiles de ese mes


def _dias_con_escritura(schema: str, tabla: str, col: str) -> int | None:
    """En cuántos DÍAS DISTINTOS escribió en el último mes.

    Es la pregunta que la mediana no puede contestar: distingue *«escribe seguido»*
    de *«escribió mucho una vez»*. `None` si la query falla — y ahí NO se degrada
    nada: no poder medir jamás puede convertirse en un veredicto.
    """
    try:
        sql = (f'SELECT count(DISTINCT "{col}"::date) AS d '
               f'FROM "{schema}"."{tabla}" '
               f'WHERE "{col}" > now() - interval \'{DIAS_VENTANA} days\'')
        r = _q(sql)
        return int(r[0]["d"]) if r else None
    except Exception as e:
        logger.debug("contexto: no pude contar días de %s.%s: %s", schema, tabla, e)
        return None


def _solo_habiles(momentos: list[datetime]) -> bool:
    """¿Escribe solo de lunes a viernes? Se mira sobre una muestra reciente: con
    pocos puntos, no tener un sábado puede ser casualidad."""
    recientes = momentos[:60]
    if len(recientes) < 15:
        return False
    return not any(m.weekday() >= 5 for m in recientes)


def _a_dt(v) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if hasattr(v, "year"):        # date
        return datetime(v.year, v.month, v.day, tzinfo=UTC)
    return None


# ── Lo que se espera de cada cadencia ───────────────────────────────────────
#
# Cuánto puede tardar una tabla en escribir antes de que sea raro. Es el ÚNICO
# juicio de valor del módulo, y va acá suelto para poder discutirlo: el resto es
# medición.
#
# Los topes son GENEROSOS (3-4× el intervalo típico) a propósito. Un detector que
# avisa al primer atraso es un detector que avisa todos los días — y de uno así no
# se desconfía: se lo ignora.
TOLERANCIA_S: dict[str, int] = {
    "tiempo_real":  30 * 60,        # media hora sin escribir en rueda ya es raro
    "intradiaria":  24 * 3600,
    "diaria":       3 * 86400,
    "diaria_habil": 3 * 86400,      # los días hábiles se cuentan aparte, ver abajo
    "semanal":      21 * 86400,
    "mensual":      75 * 86400,
}


def frescura(perfil: dict, *, ahora: datetime | None = None) -> dict:
    """**¿Está al día según lo que se espera de ELLA?** — la pregunta del user.

    «Si portfolio en AuM actualiza con fecha T-1, ok: qué día es hoy, cuándo es
    T-1, ¿hay datos? sí o no.» Eso es literalmente esto, con la diferencia de que
    el «debería» no está escrito: sale de cómo se comporta la tabla.

    Las cadencias sin ritmo (`eventual`, `estatica`, `vacia`) devuelven
    `no_se_puede_saber` y **no** `atrasada`: a una tabla de carga manual no se le
    puede exigir frescura, y marcarla en rojo todos los días es cómo se entrena a
    alguien para ignorar una pantalla.
    """
    ahora = ahora or datetime.now(UTC)
    cad = perfil.get("cadencia")
    ult = _a_dt(perfil.get("ultimo_dato"))
    if cad in (None, "eventual", "estatica", "vacia") or ult is None:
        return {"estado": "no_se_puede_saber", "atraso_s": None,
                "motivo": {"vacia": "la tabla está vacía",
                           "estatica": "se cargó de una sola vez",
                           "eventual": "no tiene un ritmo: no se le puede exigir "
                                       "frescura"}.get(cad, "sin columna de fecha")}
    atraso = (ahora - ult).total_seconds()
    tope = TOLERANCIA_S.get(cad, 3 * 86400)
    unidad = "de reloj"

    # ⚠️ **UNA TABLA DE RUEDA SE MIDE EN TIEMPO DE MERCADO, NO DE RELOJ.**
    #
    # *«El agente tiene que entender el horario de mercado. No puede decir solo
    # desde cuándo no actualiza algo, porque eso es mentiroso: si el precio cierra
    # a las 17 y abre a las 10:30, es obvio que no va a actualizar.»* (user)
    #
    # `mercado.timesales` a las 20:30 ART lleva 3½ h sin escribir y eso **no es un
    # atraso**: el mercado está cerrado. Con tiempo de reloj, el job de las 23:30
    # marcaría todas las tablas de rueda **todas las noches, para siempre** — y un
    # aviso que aparece siempre a la misma hora se deja de leer en una semana.
    #
    # Es el mismo principio que ya aplica el detector de motores («fuera de rueda
    # no está caído, está apagado») y la MISMA idea que `_segundos_de_finde` para
    # las diarias hábiles, llevada a su forma general: **el reloj que corre es el
    # del mercado**. De yapa resuelve el arranque: a las 10:10 ART hace diez
    # minutos que abrió, así que una tabla que escribió ayer al cierre recién
    # acumula diez minutos de atraso, no diecisiete horas.
    if cad in _MIDEN_EN_RUEDA:
        atraso = _segundos_de_rueda(ult, ahora)
        unidad = "de rueda"
    elif cad == "diaria_habil":
        # En una tabla de días hábiles, el fin de semana NO cuenta como atraso.
        tope += _segundos_de_finde(ult, ahora)
    ok = atraso <= tope
    return {"estado": "ok" if ok else "atrasada", "atraso_s": int(atraso),
            "tope_s": int(tope), "ultimo_dato": ult.isoformat(),
            "unidad": unidad,
            # El motivo lleva la PRUEBA, porque el voto ¿ACERTÓ? está en la
            # fila y la fila muestra solo esto (§0.ai): cuánto hace, cada cuánto
            # se esperaba, y la hora. Sin los tres, no se puede votar.
            "motivo": ("al día" if ok else
                       f"no escribe hace {_humano(atraso)} {unidad} · es "
                       f"{cad.replace('_', ' ')}"
                       + (f" (cada {_humano(p50)})" if (p50 := perfil.get(
                           "intervalo_p50_s")) else "")
                       # hora_ar y no strftime pelado: `ahora` viene en UTC
                       # y el sello decía 23:31 cuando en la mesa eran las
                       # 20:31 — y sin fecha, un hallazgo persistido de ayer
                       # se leía como de hoy (2026-08-22).
                       + f" · {hora_ar(ahora)}")}


# Las cadencias que solo tienen sentido MIENTRAS el mercado opera. Una `diaria`
# no entra: un job diario corre a la hora que corre, y muchos corren de noche.
_MIDEN_EN_RUEDA = frozenset({"tiempo_real", "intradiaria"})


def _segundos_de_rueda(desde: datetime, hasta: datetime) -> int:
    """Cuántos segundos de MERCADO ABIERTO hubo entre los dos momentos.

    Cuenta solo lunes a viernes dentro de la ventana de rueda — la misma que usa
    `av_agent.en_rueda`, importada y no copiada: dos definiciones del horario del
    mercado se separan el día que cambia una.
    """
    from api.services.av_agent import RUEDA_UTC

    abre, cierra = RUEDA_UTC
    if hasta <= desde:
        return 0
    total, d = 0.0, desde
    while d < hasta:
        # El final del día de `d`, o el corte, lo que llegue primero.
        fin_dia = (d + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                  microsecond=0)
        tramo_fin = min(fin_dia, hasta)
        if d.weekday() < 5:
            ini = max(d, d.replace(hour=abre, minute=0, second=0, microsecond=0))
            fin = min(tramo_fin,
                      d.replace(hour=cierra, minute=0, second=0, microsecond=0))
            if fin > ini:
                total += (fin - ini).total_seconds()
        d = tramo_fin
    return int(total)


def _segundos_de_finde(desde: datetime, hasta: datetime) -> int:
    """Los sábados y domingos que hubo en el medio, en segundos. Sin esto, toda
    tabla de días hábiles aparece atrasada cada lunes a la mañana."""
    d, fin, seg = desde.date(), hasta.date(), 0
    while d < fin:
        d += timedelta(days=1)
        if d.weekday() >= 5:
            seg += 86400
    return seg


def _humano(seg: float) -> str:
    # Los segundos importan: una tabla de tiempo real escribe cada 30 s y
    # redondear a minutos la mostraba «cada 0 min», que no dice nada — y es
    # justo el número que hace votable el hallazgo (§0.ai).
    if seg < 60:
        return f"{int(seg)} s"
    if seg < 3600:
        return f"{int(seg // 60)} min"
    if seg < 86400:
        return f"{seg / 3600:.1f} h".replace(".", ",")
    return f"{seg / 86400:.1f} días".replace(".", ",")


def barrer(*, guardar: bool = True) -> dict:
    """Mide TODAS las tablas y persiste el perfil. Es lo que corre en el job.

    Guardar no es cache por performance: es lo que le da MEMORIA al agente. Sin
    esto tendría que re-medir 200 tablas cada vez que alguien pregunta algo, y
    por lo tanto nunca preguntaría.
    """
    perfiles = []
    for t in inventario():
        p = {"schema": t["schema"], "tabla": t["tabla"], "filas": t["filas"],
             "col_fecha": t["col_fecha"]}
        p.update(medir(t["schema"], t["tabla"], t["col_fecha"])
                 if t["col_fecha"] else
                 {"cadencia": None, "intervalo_p50_s": None, "ultimo_dato": None})
        perfiles.append(p)

    if guardar and perfiles:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO manager.tabla_perfil (schema, tabla, col_fecha, "
                "  cadencia, intervalo_p50_s, filas, ultimo_dato, medido_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, now()) "
                "ON CONFLICT (schema, tabla) DO UPDATE SET "
                "  col_fecha = EXCLUDED.col_fecha, cadencia = EXCLUDED.cadencia, "
                "  intervalo_p50_s = EXCLUDED.intervalo_p50_s, "
                "  filas = EXCLUDED.filas, ultimo_dato = EXCLUDED.ultimo_dato, "
                "  medido_at = now()",
                [(p["schema"], p["tabla"], p["col_fecha"], p["cadencia"],
                  p["intervalo_p50_s"], p["filas"], p["ultimo_dato"])
                 for p in perfiles])
    return {"tablas": len(perfiles),
            "con_ritmo": sum(1 for p in perfiles
                             if p["cadencia"] in TOLERANCIA_S)}


def perfiles(solo_con_ritmo: bool = False) -> list[dict]:
    """Lo medido en el último barrido. Es la memoria del agente sobre la base."""
    filas = _q("SELECT schema, tabla, col_fecha, cadencia, intervalo_p50_s, "
               "filas, ultimo_dato, medido_at FROM manager.tabla_perfil "
               "ORDER BY schema, tabla")
    if solo_con_ritmo:
        filas = [f for f in filas if f.get("cadencia") in TOLERANCIA_S]
    return filas


# ── El detector ─────────────────────────────────────────────────────────────

# Las tablas que YA tienen contrato declarado en `salud.CONTRATOS` no se
# reportan acá: ahí el «debería» es una decisión de negocio (`portafolio.tenencia`
# DEBE tener el último día hábil) y no un promedio observado. Duplicar el aviso
# haría que el mismo problema aparezca dos veces con dos textos distintos.
def _ya_tienen_contrato() -> set[str]:
    try:
        from api.services.salud import CONTRATOS
        return {c["tabla"] for c in CONTRATOS if c.get("tabla")}
    except Exception:
        return set()


# ── EL ÚLTIMO DATO SE LEE AHORA, NO SE RECUERDA ─────────────────────────────
#
# ⚠️⚠️ **EL VEREDICTO ERA DE ANOCHE Y LA PREGUNTA ES DE AHORA** (2026-08-24).
#
# El user, mirando NOTICIAS DE LA BASE con cinco filas todas fechadas «23/08
# 20:31»: *«lo de los jobs de noche no tiene sentido, esto necesito que sea
# prácticamente real time o cada 10 minutos, y que cada corrida no superponga
# cosas ya arregladas»*.
#
# La causa: este detector leía `perfiles()`, o sea `manager.tabla_perfil`, y
# sacaba el veredicto de frescura contra el `ultimo_dato` **que congeló el
# barrido de las 23:30 UTC**. O sea que a las 11 de la mañana el agente seguía
# contestando con la foto de anoche: una tabla que volvió a escribir a las 9 AM
# seguía anunciada como quieta, y así hasta la noche siguiente. El aviso no
# envejecía — se quedaba clavado hasta que otro job de 24 horas lo levantara.
#
# **Y las dos mitades del perfil no envejecen igual**, que es lo que permite
# arreglarlo sin correr el barrido entero cada diez minutos:
#
#     LA CADENCIA   «esta tabla escribe cada 4 s» — es una propiedad del
#                   sistema, medida sobre sus últimas 500 escrituras. Cambia
#                   cuando cambia un job, o sea casi nunca. Cuesta UNA query
#                   POR TABLA (~200 viajes) → sigue siendo del barrido nocturno.
#     EL ATRASO     «hace 6 h que no escribe» — cambia minuto a minuto y es LA
#                   pregunta. Cuesta un `max(col)`, y los ~N de las tablas con
#                   ritmo entran en UNA sola query.
#
# Es el mismo HOT/COLD de `ops_agregado_diario`: lo caro y lento se precomputa,
# lo barato y vivo se calcula al leer.
#
# **Si la lectura viva falla, se usa la guardada y se DICE** (`ultimo_vivo` en
# la evidencia): un veredicto viejo sirve, uno viejo disfrazado de nuevo no.
def _ultimo_dato_vivo(perfiles_: list[dict]) -> dict[int, object]:
    """`max(col_fecha)` de cada tabla, AHORA. Una query para todas.

    El peaje de Supabase se paga por VIAJE (~8,5 ms), no por fila: 120 `max()`
    en un `UNION ALL` cuestan un viaje. Nunca levanta — si no se puede leer, el
    detector se queda con lo que guardó el barrido y lo declara.
    """
    if not perfiles_:
        return {}
    partes = [f'SELECT {i} AS i, max("{p["col_fecha"]}") AS t '
              f'FROM "{p["schema"]}"."{p["tabla"]}"'
              for i, p in enumerate(perfiles_) if p.get("col_fecha")]
    if not partes:
        return {}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(" UNION ALL ".join(partes))
            return {r[0]: r[1] for r in cur.fetchall()}
    except Exception as e:
        logger.warning("contexto: no pude leer el último dato en vivo (%s)", e)
        return {}


def detectar_tablas() -> list[dict]:
    """Tablas que **dejaron de escribir cuando deberían estar escribiendo**.

    Cubre las ~190 que hoy son un punto ciego total: las 8 críticas las sigue
    mirando `salud.CONTRATOS`, que es más estricto y no se acostumbra al problema.

    El RITMO sale del barrido nocturno (caro, cambia poco); el ATRASO se mide
    **en esta llamada** (barato, cambia siempre) — ver el bloque de arriba.
    """
    from core import escribe

    try:
        con_contrato = _ya_tienen_contrato()
        out = []
        con_ritmo = perfiles(solo_con_ritmo=True)
        vivo = _ultimo_dato_vivo(con_ritmo)
        for i, p in enumerate(con_ritmo):
            # El último dato de AHORA gana sobre el que congeló el barrido. Si
            # la lectura viva no trajo esta tabla, se usa el guardado y la
            # evidencia lo dice — nunca se presenta lo viejo como si fuera nuevo.
            es_vivo = i in vivo
            if es_vivo:
                p = {**p, "ultimo_dato": vivo[i]}
            nombre = f"{p['schema']}.{p['tabla']}"
            if nombre in con_contrato:
                continue
            f = frescura(p)
            if f["estado"] != "atrasada":
                continue

            # ⚠️ **UNA TABLA DE EVENTOS NO TIENE CADENCIA: TIENE OCASIONES**
            # (§0.aq). La primera medición de cobertura puso a `sin_escribir`
            # como la pared más cara —8 casos— y sus tres ejemplos fueron
            # `ia.trazas`, `manager.role_audit` y `manager.salud_eventos`:
            # ninguna tiene un job atrás. Están quietas porque **no pasó nada**,
            # no porque algo esté roto, y no hay nada que relanzar.
            #
            # Es la otra mitad de §0.u: allá una ráfaga se leía como ritmo, acá
            # un ritmo REAL (los eventos vienen seguido) se leía como una
            # obligación. `ia.trazas` escribe casi todos los días porque se usa
            # IA casi todos los días — hasta el día que no.
            #
            # `no_se` NO se saltea: ante la duda se sigue exigiendo, porque
            # dejar de mirar algo que no entendimos es cómo se pierde una señal.
            if escribe.la_dispara(nombre) == escribe.EVENTO:
                continue
            # Y lo que la puerta va a necesitar el día que exista: QUÉ relanzar,
            # derivado del código y no adivinado.
            relanzar = escribe.que_relanzar(nombre)
            out.append({
                "tipo": "tabla_quieta", "ticker": nombre, "regla": "sin_escribir",
                "severidad": "alta" if p["cadencia"] == "tiempo_real" else "media",
                "motivo": f["motivo"],
                "evidencia": {
                    "texto": (f"venía escribiendo cada "
                              f"{_humano(p['intervalo_p50_s'] or 0)} "
                              f"({p['cadencia'].replace('_', ' ')}, medido sobre sus "
                              f"últimas escrituras) y el último dato en "
                              f"«{p['col_fecha']}» es de hace "
                              f"{_humano(f['atraso_s'])}. Nadie declaró esta "
                              f"cadencia: sale de cómo se comporta la tabla."),
                    "cadencia": p["cadencia"], "col_fecha": p["col_fecha"],
                    "atraso_s": f["atraso_s"], "tope_s": f["tope_s"],
                    "ultimo_dato": f["ultimo_dato"], "filas": p["filas"],
                    # ¿El atraso se midió AHORA o salió de la foto del barrido?
                    # Sin esto, un veredicto viejo se lee igual que uno fresco.
                    "ultimo_vivo": es_vivo,
                    "la_escribe": escribe.quien_escribe(nombre),
                    "relanzar": relanzar}})
        return out
    except Exception as e:
        logger.warning("av_agent_contexto: no pude evaluar las tablas: %s", e)
        return []
