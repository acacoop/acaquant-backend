"""agente/tablas.py — LO QUE EL AGENTE SABE DE LA BASE, SIN QUE NADIE SE LO ESCRIBA.

Doc madre: **`docs/AGENT.md`** §0.r.

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
    CUÁL ES SU FECHA        →  pg_catalog: su SELLO DE ESCRITURA (`_elegir_col`).
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
#
# ⚠️ **ESTABA ESCRITA SÓLO EN INGLÉS, Y LA MITAD DEL SCHEMA NOMBRA EN
# CASTELLANO.** Medido sobre `sql/schema.sql`: `actualizado_at` aparece **38
# veces** — más que `updated_at` (31) — y no estaba acá. Las tablas que nombran
# en castellano no tenían NINGÚN sello en esta lista, así que `inventario()`
# caía al fallback `candidatas[0]`: la primera columna temporal **por orden de
# columna en el DDL**, que no es una elección, es el orden en que alguien
# escribió el `CREATE TABLE`. Y en este repo ese orden no es neutro: las
# fechas de negocio se declaran arriba y el sello de auditoría al final, así
# que el fallback agarraba casi siempre una fecha de negocio disfrazada de
# sello de escritura.
#
# El nombre NO alcanza para separar un sello de una fecha de negocio (`ts` y
# `fecha` son ambiguos por nombre): eso lo resuelve `_elegir_col()` por TIPO de
# columna. Estas tres tuplas sólo DESEMPATAN dentro de cada grupo.
#
# ⚠️ **UN SELLO DE ALTA NO SIRVE PARA MEDIR FRESCURA: NO SE MUEVE.**
# `creado_at` se escribe una vez y no vuelve a cambiar nunca. En una tabla que
# se upsertea, `max(creado_at)` queda CONGELADO en el día que entró la última
# fila nueva, aunque el job la esté reescribiendo entera cada quince minutos.
# Por eso los tres grupos no son decorativos: los de ESCRITURA ganan, los de
# ALTA pierden **incluso contra un sello que no está en ninguna lista**, y en el
# medio queda cualquier otro `timestamptz` — que al menos se mueve.
# (Lo cazó `agente.habilidades`: con la lista plana elegía `creada_at` en vez de
# `ultima_corrida_at`, o sea el día que nació la habilidad en lugar de la última
# vez que corrió.)
_SELLO_ESCRITURA = ("updated_at", "actualizado_at", "actualizado_en",
                    "sincronizado_at", "sellado_at", "tomado_at", "corrida_at",
                    "computed_at", "agregado_at")
_SELLO_ALTA = ("ingestado_en", "ingestado_at", "importado_en", "generado_at",
               "creado_at", "created_at", "creada_at")
# Fecha de NEGOCIO: de qué día son los datos, no cuándo se escribieron. Sólo se
# usan cuando la tabla no tiene NINGÚN sello (ver `_elegir_col`).
_FECHA_NEGOCIO = ("ts", "fecha", "concertacion", "hora")

COLS_FECHA = _SELLO_ESCRITURA + _SELLO_ALTA + _FECHA_NEGOCIO

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


def _elegir_col(cols: list[str], tipos: list[str]) -> tuple[str | None, bool]:
    """Entre las columnas temporales candidatas, cuál usar como frescura.

    **La regla es de TIPO, no de lista** (este módulo entero está construido
    sobre no tener listas de tablas): una columna `date` NUNCA puede ser un
    instante de escritura. Un `date` no tiene hora — por construcción es una
    fecha de negocio ("de qué día son los datos"); un sello de escritura es
    siempre `timestamptz`/`timestamp`.

    Caso real, `bancos.mayor_movimientos`: tenía `fecha_conciliacion` (date) y
    `actualizado_at` (timestamptz), y por orden de columna en el DDL el viejo
    fallback elegía `fecha_conciliacion`. El job `mayor_sync` corre cada 15
    minutos pero trae SIEMPRE el día hábil anterior, así que esa columna
    **nunca puede estar más fresca que T-1 hábil** — el agente lo cantaba como
    caído estando perfecto. Con esta regla elige `actualizado_at`.

    Entonces:

    1. Entre las columnas que NO son `date` (los sellos), gana la convención
       de nombre (`COLS_FECHA`, en orden); si ninguna matchea, la primera.
    2. Sólo si la tabla no tiene NINGÚN sello, se cae a una `date`: gana la
       convención, si no la primera. Y se marca `es_fecha_negocio=True`: la
       frescura se está juzgando contra una fecha de negocio, y eso hay que
       poder decirlo, no esconderlo.

    `tipos` puede venir más corto, más largo o vacío que `cols` (si el
    agregado paralelo del SQL quedara desalineado por lo que sea): lo que no
    se puede clasificar se trata como SELLO, nunca como fecha de negocio —
    ante la duda se sigue midiendo, que es la doctrina del módulo.

    `(None, False)` si no hay ninguna columna temporal.
    """
    if not cols:
        return None, False
    tipos = list(tipos)
    if len(tipos) < len(cols):
        tipos = tipos + [None] * (len(cols) - len(tipos))
    sellos = [c for c, t in zip(cols, tipos, strict=False) if t != "date"]
    if sellos:
        elegida = (next((c for c in _SELLO_ESCRITURA if c in sellos), None)
                   # Un sello que no está en ninguna lista le gana a uno de
                   # ALTA: no sabemos qué es, pero al menos puede moverse, y
                   # `creado_at` seguro que no.
                   or next((c for c in sellos if c not in _SELLO_ALTA), None)
                   or next((c for c in _SELLO_ALTA if c in sellos), None)
                   or sellos[0])
        return elegida, False
    # Ninguna columna sobrevivió al filtro: todas son `date`. Se juzga por
    # fecha de negocio, y se dice.
    return next((c for c in COLS_FECHA if c in cols), cols[0]), True


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
                 AS cols_fecha,
               array_agg(t.typname ORDER BY a.attnum)
                 FILTER (WHERE t.typname IN ('timestamptz','timestamp','date'))
                 AS tipos_fecha
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
        tipos = list(f.get("tipos_fecha") or [])
        # Mejor una imperfecta que ninguna: sin columna de fecha la tabla
        # queda como punto ciego, que es lo que esto viene a eliminar.
        col, es_fecha_negocio = _elegir_col(candidatas, tipos)
        out.append({"schema": f["schema"], "tabla": f["tabla"],
                    "filas": int(f["filas"] or 0), "col_fecha": col,
                    "cols_fecha": candidatas, "es_fecha_negocio": es_fecha_negocio})
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


def frescura(perfil: dict, *, ahora: datetime | None = None,
             declarado: dict | None = None) -> dict:
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
    # ⚠️⚠️ **UNA FECHA DE NEGOCIO NO ES UN TIMESTAMP DE ESCRITURA.**
    #
    # `COLS_FECHA` mezcla dos cosas distintas: `updated_at`/`ingestado_en` dicen
    # CUÁNDO SE ESCRIBIÓ la fila, y `fecha`/`ts_cierre` dicen **de qué día son
    # los datos**. Medir el atraso contra la segunda suma hasta 24 h de retraso
    # que no existe: el cierre del 27 se escribe el 27 a las 20:35, pero su
    # `fecha` dice `2026-08-27 00:00`.
    #
    # Medido el 2026-08-28 a las 17:06 UTC: siete tablas de cierre
    # (`bonos_ohlc_daily`, `cedears_ohlc_daily`, `day_trading_stats`,
    # `eikon_cierres`, `fair_value_residuos`, `snapshots_cierre_hist`) salieron
    # todas juntas con «hace 1,7 días» **teniendo el dato correcto**: el cierre
    # del 28 todavía no había pasado.
    #
    # No hace falta declarar qué tabla es de negocio: **el dato se delata solo.**
    # Un valor a medianoche EXACTA no es un instante de escritura —ningún job
    # escribe a las 00:00:00.000000— es un día. Y entonces la pregunta correcta
    # no es «¿hace cuánto de ese instante?» sino «¿hace cuánto que TERMINÓ ese
    # día?».
    #
    # ⚠️ Y esto tapa el CIERRE del día, no el DESFASE de la fuente. Un job que
    # pide siempre el día hábil anterior (`mayor_sync`) tiene una tabla cuya
    # fecha de negocio **nunca puede estar más fresca que T-1 hábil**, y contra
    # el reloj eso son hasta 3 días un martes a la mañana. Cuando la tabla tiene
    # un sello de escritura la pregunta ni se hace —`_elegir_col` lo elige—;
    # cuando no lo tiene, esto es lo mejor que se puede hacer, y por eso el
    # veredicto sale MARCADO (`fecha_de_negocio`): quien lee la tarjeta tiene
    # que saber que el número es una cota, no una medición. AGENT.md §0.em.
    fecha_de_negocio = (ult.hour, ult.minute, ult.second, ult.microsecond) == (0, 0, 0, 0)
    if fecha_de_negocio:
        ult_efectivo = ult + timedelta(days=1)   # el día cierra a las 24:00
    else:
        ult_efectivo = ult

    atraso = (ahora - ult_efectivo).total_seconds()
    tope = TOLERANCIA_S.get(cad, 3 * 86400)
    unidad = "de reloj"

    # ⚠️⚠️ **SI EL RITMO ESTÁ DECLARADO, LE GANA AL MEDIDO. SIEMPRE.**
    #
    # Medir la mediana entre filas funciona para un motor y **falla feo para un
    # job que corre una vez al día y appendea un lote**: adentro del lote las
    # filas están separadas por milisegundos, así que la mediana dice «tiempo
    # real» y este detector le empieza a exigir el ritmo de un feed live.
    #
    # Medido el 2026-08-28: **7 de los 10 hallazgos abiertos** eran eso.
    # `research.mkt_1816_series` —un append diario de las 22:00 UTC— figuraba
    # como «tiempo real, cada 2 s», y `mercado.canje_cierre`, post-cierre, como
    # «cada 0 s». Ninguna estaba rota.
    #
    # La guarda que ya existía (`_dias_con_escritura`) pregunta *«¿escribió en
    # muchos días distintos?»* y un job diario contesta que **sí**: escribe todos
    # los días… una vez. Distingue «escribe seguido» de «escribió mucho una vez»,
    # pero no **«escribe todo el día»** de **«escribe todos los días»**.
    #
    # Y el dato bueno estaba al lado todo el tiempo: `deploy/crontab.txt`. No se
    # sube ninguna tolerancia —eso taparía las tablas que sí importan—: se deja
    # de adivinar algo que está escrito.
    if declarado and declarado.get("hueco_s"):
        # Margen sobre el hueco: un job que corre 22:00 y tarda 15 min está al
        # filo de las 24 h exactas todos los días. 1,5× es la misma proporción
        # que usa el árbol de diagnóstico entre «lento» y «crítico».
        tope = int(declarado["hueco_s"] * MARGEN_DECLARADO)
        # Un job de reloj NO se mide en tiempo de mercado: corre a la hora que
        # corre, y muchos corren de noche.
        unidad = "de reloj"
        if declarado.get("solo_habiles"):
            tope += _segundos_de_finde(ult_efectivo, ahora)
        ok = atraso <= tope
        return {"estado": "ok" if ok else "atrasada", "atraso_s": int(atraso),
                "tope_s": int(tope), "ultimo_dato": ult.isoformat(),
                "unidad": unidad, "declarado": True,
                "fecha_de_negocio": fecha_de_negocio,
                "motivo": ("al día" if ok else
                           f"no escribe hace {_humano(atraso)} y su cron dice "
                           f"cada {_humano(declarado['hueco_s'])} como mucho"
                           f" · {hora_ar(ahora)}")}

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
        atraso = _segundos_de_rueda(ult_efectivo, ahora)
        unidad = "de rueda"
    elif cad == "diaria_habil":
        # En una tabla de días hábiles, el fin de semana NO cuenta como atraso.
        tope += _segundos_de_finde(ult_efectivo, ahora)
    ok = atraso <= tope
    return {"estado": "ok" if ok else "atrasada", "atraso_s": int(atraso),
            "tope_s": int(tope), "ultimo_dato": ult.isoformat(),
            "unidad": unidad, "fecha_de_negocio": fecha_de_negocio,
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

# Cuánto más que el hueco declarado se tolera antes de decir «atrasada».
MARGEN_DECLARADO = 1.5


def _segundos_de_rueda(desde: datetime, hasta: datetime) -> int:
    """Cuántos segundos de MERCADO ABIERTO hubo entre los dos momentos.

    Cuenta solo lunes a viernes dentro de la ventana de rueda — la misma que usa
    `av_agent.en_rueda`, importada y no copiada: dos definiciones del horario del
    mercado se separan el día que cambia una.
    """
    from agente.reloj import RUEDA_UTC

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
    # ⚠️⚠️ **SE MIRA EL DÍA Y DESPUÉS SE AVANZA, NO AL REVÉS.** La versión
    # anterior sumaba primero, así que el día de `desde` NUNCA se evaluaba y de
    # cada fin de semana descontaba UNO SOLO.
    #
    # Pasó el 2026-08-31: el cierre del viernes 28 deja `ult_efectivo` en sábado
    # 29, el bucle arrancaba mirando el domingo 30, y el sábado quedaba contado
    # como día hábil. Seis tablas de cierre salieron «no escribe hace 2,6 días»
    # un lunes a la mañana, teniendo el dato correcto del viernes.
    #
    # Y casi no se ve, que es lo peor: con un día de descuento el tope queda en
    # 3,00 d contra un atraso de 2,98 d a las 23:26 — **zafa por media hora**.
    # Un viernes que termine tarde, o un feriado pegado al finde, lo tumban.
    while d < fin:
        if d.weekday() >= 5:
            seg += 86400
        d += timedelta(days=1)
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
        # `es_fecha_negocio` viaja en memoria nomás: `manager.tabla_perfil` no
        # tiene esa columna y el INSERT de abajo no la toca.
        p = {"schema": t["schema"], "tabla": t["tabla"], "filas": t["filas"],
             "col_fecha": t["col_fecha"], "es_fecha_negocio": t["es_fecha_negocio"]}
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


def declarados() -> dict[str, dict]:
    """`schema.tabla` → el ritmo que su job DECLARA en el crontab.

    Junta las dos mitades que el sistema ya tenía sueltas:

        `core.escribe.que_relanzar(tabla)`  → QUÉ job la escribe
        `core.crontab.ritmo_declarado()`    → CADA CUÁNTO corre ese job

    Ninguna de las dos es nueva: el agente ya las usaba por separado —una para
    escribir el `que_hacer`, la otra para `cron_desalineado`— y nunca se
    preguntó una a la otra. Por eso adivinaba un ritmo que estaba escrito.

    Se resuelve UNA vez por corrida y no por tabla: son ~190 tablas y leer el
    crontab 190 veces sería el mismo trabajo repetido.
    """
    from core import crontab, escribe
    try:
        ritmos = crontab.ritmo_declarado()
    except Exception as e:
        logger.warning("tablas: no pude leer el ritmo del crontab (%s)", e)
        return {}
    out: dict[str, dict] = {}
    for nombre in escribe.tablas_con_escritor():
        job = escribe.que_relanzar(nombre)
        r = ritmos.get((job or "").strip())
        if r:
            out[nombre] = {**r, "job": job}
    return out
