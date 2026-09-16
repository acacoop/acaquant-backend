"""core/duplicados.py — DÓNDE VIVE EL MISMO DATO DOS VECES, Y QUIÉN MANDA.

Doc oficial: `docs/ACAQUANT.md` · `docs/AGENT.md` §0.aa.

LA LECCIÓN, PAGADA TRES VECES EN CUATRO DÍAS
=============================================

> *Dos representaciones del mismo dato sin un árbitro declarado no conviven: se
> separan. Y cuando se separan **no falla nada** — cada mitad sigue siendo
> internamente coherente y el sistema miente en silencio.*

    2026-08-19  el símbolo del bono vivía en la COLUMNA y en el BLOB. El motor
                escribía el precio leyendo el blob y la vista lo buscaba por la
                columna. Divergieron en 2 de 229 y esos dos bonos salían enteros
                en `--` **con el precio existiendo**. Nada falló: cada mitad
                tenía razón sobre lo que miraba.

    2026-08-19  `preferencia` (MEP antes que cable) estaba escrita TRES veces.
                Las tres eligieron distinto y el diag mostraba una pata mientras
                el agente iba a pedir otra.

    2026-08-15  el renombre de columnas dejó el blob con el significado
                invertido. Durante cuatro días no pasó nada — hasta que pasó.

El problema no es tener el dato dos veces: a veces hace falta (un blob que leen
500 lugares no se migra de un día para el otro). **El problema es no declarar
quién manda, y que nadie mire si siguen diciendo lo mismo.**

QUÉ HACE ESTE MÓDULO
====================

Un registro **declarado** de los datos que viven en más de un lugar. Cada entrada
dice qué dato es, dónde vive, **quién es el árbitro** y qué se rompe si divergen.
Agregar uno nuevo son cinco líneas y una query.

`divergencias()` los corre a todos y devuelve solo lo que NO coincide. Lo lee el
AV Agent (`detectar_dato_partido`) y lo canta como cualquier otro hallazgo — así
la próxima vez que dos copias se separen, **alguien se entera el mismo día**.

⚠️ **Cada query en su propio `try`.** Una que se rompa no puede dejar sin mirar a
las demás, y —sobre todo— no puede devolver «no hay divergencias»: sin correr, la
respuesta correcta es *no sé*, y por eso `divergencias()` devuelve también qué no
pudo mirar. *El silencio no es un verde.*
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from core.postgres import get_pool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Duplicado:
    """Un dato que vive en dos lugares, con su árbitro declarado.

    `sql` devuelve UNA fila por sujeto que NO coincide: `(sujeto, valor_a,
    valor_b)`. Los que coinciden no se devuelven — la lista es de problemas, no
    un inventario.
    """
    id: str
    que: str          # qué dato es, en criollo
    a: str            # dónde vive la copia A
    b: str            # dónde vive la copia B
    arbitro: str      # cuál de las dos manda cuando difieren
    rompe: str        # qué se rompe si divergen (lo que hace que importe)
    sql: str
    # ⚠️ **NO TODOS SE ARREGLAN CON UN UPDATE, y eso se DECLARA.** Sincronizar dos
    # copias parece siempre lo mismo y no lo es: a veces la copia mala se corrige
    # escribiéndole el valor del árbitro (un blob desactualizado), y a veces el
    # árbitro es un JOB —o el cambio no surte efecto hasta reiniciar un motor— y
    # un UPDATE dejaría el dato «coincidiendo» sin que nada haya mejorado.
    #
    # Vacío = no hay arreglo mecánico, y entonces `arreglo_manual` dice qué hacer.
    # Declararlo separado es lo que impide que alguien escriba el UPDATE «obvio».
    arreglo_sql: str = ""
    arreglo_manual: str = ""
    # Cuál de las dos copias es la que MANDA cuando `arreglo_sql` existe: la
    # que se escribe es la otra. Es lo que deja anotar en el libro «antes →
    # después» por fila sin adivinar leyendo la prosa de `arbitro`.
    gana: str = ""


# ⚠️ **SE DECLARA, NO SE DESCUBRE.** No hay forma de deducir del esquema que dos
# columnas guardan «lo mismo» — eso lo sabe quien modeló. Lo que sí se consigue
# declarándolo es que el chequeo exista y que la próxima divergencia dure horas y
# no cuatro días.
DUPLICADOS: tuple[Duplicado, ...] = (
    Duplicado(
        id="mesa_observacion_nombre_vs_email",
        que="a QUÉ OPERADOR se le imputa el resultado de intermediación de una "
            "op de Mesa de Dinero",
        a="operaciones.mesa_dinero.observacion_email (el email del operador)",
        b="operaciones.mesa_dinero.observacion (el nombre con el que se cargó)",
        arbitro="el EMAIL — es la PK de `clientes.operadores`. El nombre no tiene "
                "UNIQUE y se puede editar: sirve para mostrar, no para repartir "
                "plata (REGLA #9 · ver sql/schema.sql → mesa_dinero)",
        rompe="ese email es el que decide de quién es el 50% del resultado cuando "
              "la vista OPERADORES arma la producción del comercial (ARS 45,4M "
              "medidos el 2026-09-16, +21,8% sobre el arancel). Si el nombre se "
              "corrige y el email queda viejo, **la plata le sigue sumando al "
              "operador anterior y el total de la mesa da exactamente igual**: no "
              "falla nada, solo está en la persona equivocada",
        # Filas donde el email guardado NO es el del operador que hoy se llama así.
        # Las que tienen email NULL no entran: «todavía sin imputar» es un estado
        # legítimo (ambiguos del backfill), no una divergencia.
        sql="""
            SELECT o.id::text,
                   coalesce(o.observacion_email, ''),
                   coalesce(o.observacion, '')
            FROM operaciones.mesa_dinero o
            WHERE o.observacion_email IS NOT NULL
              AND o.observacion IS NOT NULL
              AND btrim(o.observacion) <> 'Mesa'
              AND NOT EXISTS (
                    SELECT 1 FROM clientes.operadores c
                     WHERE c.email = o.observacion_email
                       AND upper(btrim(c.nombre)) = upper(btrim(o.observacion)))
            ORDER BY o.id
        """,
        # Se re-sincroniza el NOMBRE desde el email (gana el email): la fila sigue
        # imputándole a quien se eligió, y el texto pasa a decir cómo se llama hoy.
        # Scopeado a las filas que difieren y solo cuando el email es un operador
        # real — si no lo fuera, el UPDATE no tiene de dónde sacar el nombre.
        arreglo_sql="""
            UPDATE operaciones.mesa_dinero o
            SET observacion = c.nombre
            FROM clientes.operadores c
            WHERE c.email = o.observacion_email
              AND o.observacion_email IS NOT NULL
              AND o.observacion IS NOT NULL
              AND btrim(o.observacion) <> 'Mesa'
              AND c.nombre IS NOT NULL
              AND upper(btrim(c.nombre)) <> upper(btrim(o.observacion))
        """,
        gana="a"),
    Duplicado(
        id="simbolo_columna_vs_blob",
        que="el símbolo de mercado del bono (con el que se pide el precio)",
        a="mercado.curvas.instrumento (columna)",
        b="mercado.curvas.data->>'ticker' (blob)",
        arbitro="la COLUMNA — es la que migró el renombre del 2026-08-15",
        rompe="el motor escribe el precio leyendo el BLOB y la vista lo busca por "
              "la COLUMNA: si difieren, la fila sale entera en «--» teniendo el "
              "precio cargado, y ningún detector lo ve porque cada mitad es "
              "coherente consigo misma (el caso AO29/CO32)",
        sql="""
            SELECT ticker,
                   coalesce(instrumento, ''),
                   coalesce(data->>'ticker', '')
            FROM mercado.curvas
            WHERE instrumento IS NOT NULL
              AND coalesce(data->>'ticker', '') <> ''
              AND btrim(instrumento) <> btrim(data->>'ticker')
            ORDER BY ticker
        """,
        # Se le escribe al BLOB el valor de la COLUMNA. **No es un renombre**: la
        # clave del blob sigue llamándose `ticker` (la leen ~500 lugares), solo se
        # le pone el valor correcto. Scopeado a las filas que difieren.
        arreglo_sql="""
            UPDATE mercado.curvas
            SET data = jsonb_set(coalesce(data, '{}'::jsonb), '{ticker}',
                                 to_jsonb(btrim(instrumento)))
            WHERE instrumento IS NOT NULL
              AND coalesce(data->>'ticker', '') <> ''
              AND btrim(instrumento) <> btrim(data->>'ticker')
        """,
        gana="a"),
    Duplicado(
        id="ticker_curva_vs_assets",
        que="el TICKER del título — la clave que une la tenencia con su curva",
        a="mercado.curvas.ticker (la PK de la curva)",
        b="portafolio.assets.ticker (el catálogo que carga la mesa)",
        arbitro="la UNIDAD de `portafolio.assets`, que es su PK y la escribe "
                "Aunesa: trae el código adentro (`[84857] PLC5O - ON …`). No "
                "gana ninguna de las dos copias — gana el dato que ninguna de "
                "las dos tipeó",
        rompe="el join `curvas.ticker → assets.ticker → unidad` es el que atribuye "
              "una tenencia a su CURVA. Si difieren, el bono **sigue sumando al "
              "AuM** (eso va por `unidad`) pero desaparece de flujos, acreencias y "
              "renta fija — y no falla nada: las dos tablas son coherentes consigo "
              "mismas. Medido el 2026-08-22: 2 casos, los dos por UN carácter "
              "tipeado a mano (`PLC5O`→`PLC50`, la O es un cero; `S13N6`→`S13B6`)",
        # ⚠️ El WHERE compara contra el código de la UNIDAD, no contra la curva:
        # así se listan también los assets cuyo ticker está mal aunque su curva no
        # exista todavía. El `substring` espeja `acreencias._RE_CODIGO`.
        # ⚠️⚠️ **ACOTADO A LOS QUE SON UN BONO, y la primera versión no lo
        # estaba: devolvía 303 divergencias de las cuales 2 eran reales.**
        #
        # El código de la unidad solo ES un ticker cuando el asset es un bono.
        # En un FCI la unidad dice `[2598] cafc1103- 2598 - IEB Renta Fija`
        # → «cafc1103», que no es el ticker de nada; en un OTC dice
        # `[OTC - DLR052027]`. Compararlos contra `assets.ticker` da 301
        # «divergencias» que no significan nada — y un chequeo que grita 303
        # veces por 2 problemas reales enseña a ignorarlo, que es el daño que
        # este módulo existe para evitar.
        #
        # El `JOIN` con `mercado.curvas` es la misma guarda 1 de la acción
        # `assets.ticker`: si el código de la unidad no es una curva, no
        # sabemos cuál de los dos nombres es el bueno, así que no hay
        # divergencia que declarar.
        sql=r"""
            SELECT a.unidad, cod.c, coalesce(a.ticker, '')
            FROM portafolio.assets a
            CROSS JOIN LATERAL (
                SELECT upper(substring(a.unidad
                       from '^\s*(?:\[\d+\]\s*)?([A-Za-z0-9]+)')) AS c
            ) cod
            JOIN mercado.curvas k ON upper(btrim(k.ticker)) = cod.c
            WHERE cod.c IS NOT NULL
              AND coalesce(a.ticker, '') <> ''
              AND upper(btrim(a.ticker)) <> cod.c
            ORDER BY a.unidad
        """,
        # **SIN `arreglo_sql` a propósito.** El UPDATE «obvio» —pisar
        # `assets.ticker` con el código de la unidad— es correcto en los dos casos
        # medidos y peligroso en general: si ese ticker equivocado es el ticker
        # REAL de otro papel, corregirlo acá le rompe el join a ESE otro. Va por
        # la acción del agente (`assets.ticker`), que lo verifica caso por caso
        # antes de escribir y pide OK.
        arreglo_manual="AV Agent → la fila `sin_espejo_en_assets` → CORREGIR EL "
                       "TICKER (acción `assets.ticker`), o a mano en Manager → "
                       "TÍTULOS · ASSETS."),
    Duplicado(
        id="ticker_corto_columna_vs_blob",
        que="el ticker corto del bono (el que une con `portafolio.assets`)",
        a="mercado.curvas.ticker (columna, la PK)",
        b="mercado.curvas.data->>'ticker_corto' (blob)",
        arbitro="la COLUMNA — es la PK",
        rompe="es la clave del join con assets y con la tenencia: si el blob dice "
              "otra cosa, lo que lea por el blob valúa contra otro papel",
        sql="""
            SELECT ticker,
                   ticker,
                   coalesce(data->>'ticker_corto', '')
            FROM mercado.curvas
            WHERE coalesce(data->>'ticker_corto', '') <> ''
              AND upper(btrim(ticker)) <> upper(btrim(data->>'ticker_corto'))
            ORDER BY ticker
        """,
        arreglo_sql="""
            UPDATE mercado.curvas
            SET data = jsonb_set(coalesce(data, '{}'::jsonb), '{ticker_corto}',
                                 to_jsonb(btrim(ticker)))
            WHERE coalesce(data->>'ticker_corto', '') <> ''
              AND upper(btrim(ticker)) <> upper(btrim(data->>'ticker_corto'))
        """,
        gana="a"),
    Duplicado(
        id="simbolo_master_vs_especies",
        que="qué pata del bono se suscribe",
        a="mercado.curvas.instrumento (se carga a mano)",
        b="mercado.especies.es_default (se deriva de Primary)",
        arbitro="ESPECIES — sale del catálogo real; el master se tipea",
        rompe="el motor pide una pata y la mesa mira otra: la grilla muestra "
              "pesos en una curva en dólares (AO29/GD46/CO32)",
        sql="""
            SELECT c.ticker, coalesce(c.instrumento, ''), e.simbolo
            FROM mercado.curvas c
            JOIN mercado.especies e
                 ON upper(e.ticker) = upper(c.ticker) AND e.es_default
            WHERE c.instrumento IS NOT NULL
              AND btrim(c.instrumento) <> btrim(e.simbolo)
            ORDER BY c.ticker
        """,
        arreglo_manual="NO se arregla con un UPDATE. El motor arma su universo AL "
                       "ARRANCAR, así que cambiar `curvas.instrumento` no surte "
                       "efecto hasta reiniciar `motor_rofex` FUERA DE RUEDA — y "
                       "una acción que se aplica, se verifica en verde y no cambia "
                       "nada en pantalla destruye la confianza en todas las demás. "
                       "Primero se pide la pata (adhoc, se ve en 5s) para saber si "
                       "cotiza; con ese dato se decide el reinicio."),
    Duplicado(
        id="vencimiento_master_vs_assets",
        que="CUÁNDO VENCE el título — la fecha que decide si todavía existe",
        a="mercado.curvas.fecha_vencimiento (el master, date)",
        b="portafolio.assets.vencimiento (el catálogo, text, carga la mesa)",
        arbitro="el MASTER. Confirmado por la mesa el 2026-09-04 sobre GMCGO: "
                "`curvas` decía 2028-01-28 y `assets` 2026-06-28, y la fecha "
                "buena es la del master. Es coherente con el origen de cada "
                "copia — la del master entra con el alta de la curva (1816), la "
                "del catálogo se tipea a mano",
        rompe="`jobs.validar_instrumentos` apagaba el título (`vigente=false`, "
              "motivo `vencido`) con la fecha del CATÁLOGO, que le ganaba al "
              "master. Con eso un bono con DOS AÑOS de vida por delante quedaba "
              "marcado como vencido, el AV AGENT lo daba por muerto a partir de "
              "esa marca, y `cleanup_curvas` —que mira sólo el master— no lo "
              "borraba nunca: el título quedaba MUERTO en una mitad del sistema "
              "y VIVO en la otra, con un hallazgo abierto que nadie podía "
              "cerrar. Y `assets.vencimiento` además se muestra en /aca y en los "
              "flujos, así que la fecha equivocada se lee en pantalla",
        # ⚠️ Sólo se comparan los `vencimiento` que parsean como ISO — el MISMO
        # criterio que `validar_instrumentos._a_fecha`, que hace
        # `date.fromisoformat(str(v)[:10])`. Lo que no parsea no es una fecha
        # distinta: es un dato sin cargar, y no hay divergencia que declarar.
        # Reimplementar el parseo acá con otro criterio sería exactamente el
        # defecto que este módulo persigue.
        sql=r"""
            SELECT a.unidad,
                   to_char(c.fecha_vencimiento, 'YYYY-MM-DD'),
                   left(btrim(a.vencimiento), 10)
            FROM portafolio.assets a
            JOIN mercado.curvas c ON c.ticker = a.ticker
            WHERE c.fecha_vencimiento IS NOT NULL
              AND btrim(coalesce(a.vencimiento, '')) ~ '^\d{4}-\d{2}-\d{2}'
              AND left(btrim(a.vencimiento), 10)
                  <> to_char(c.fecha_vencimiento, 'YYYY-MM-DD')
            ORDER BY a.unidad
        """,
        # **SIN `arreglo_sql` a propósito, y no por prudencia genérica.** El
        # árbitro está confirmado para UN caso, no medido para todos, y
        # `assets.vencimiento` no es un campo interno: lo leen `titulos_flujos` y
        # la vista `/aca`. Un UPDATE masivo cambiaría lo que se muestra en
        # pantalla apoyado en una sola confirmación — REGLA #4. Primero se mira
        # la lista; si son todos la misma forma, ahí se agrega el arreglo con la
        # evidencia al lado.
        arreglo_manual="Manager → TÍTULOS · ASSETS → corregir VENCIMIENTO con la "
                       "fecha del master. `validar_instrumentos` vuelve a "
                       "encender solo lo que él mismo había apagado.",
        gana="a"),
    Duplicado(
        id="emisor_curvas_vs_assets",
        que="el emisor del papel",
        a="mercado.curvas.emisor",
        b="portafolio.assets.emisor",
        arbitro="1816 (`jobs.ficha_1816` escribe LOS DOS, justamente por esto)",
        rompe="cualquier cosa que AGRUPE por emisor cuenta distinto según de dónde "
              "lea, y no se nota: las dos filas existen y suman bien por separado",
        sql="""
            SELECT c.ticker, coalesce(c.emisor, ''), coalesce(a.emisor, '')
            FROM mercado.curvas c
            JOIN portafolio.assets a ON upper(a.ticker) = upper(c.ticker)
            WHERE coalesce(btrim(c.emisor), '') <> ''
              AND coalesce(btrim(a.emisor), '') <> ''
              AND upper(btrim(c.emisor)) <> upper(btrim(a.emisor))
            ORDER BY c.ticker
        """,
        arreglo_manual="NO se arregla con un UPDATE: el árbitro es 1816 y el que "
                       "escribe las DOS tablas es `jobs.ficha_1816`. Escribir a "
                       "mano dejaría las copias coincidiendo en un valor que "
                       "ninguna fuente respalda — que es peor que la divergencia, "
                       "porque además la esconde. Correr el job."),
)

# Cuántos sujetos divergentes se listan por duplicado. Si son más, se dice el
# total igual: **truncar en silencio se lee como «solo hay estos»**.
TOPE_EJEMPLOS = 8


def declarado(duplicado_id: str) -> Duplicado | None:
    return next((x for x in DUPLICADOS if x.id == duplicado_id), None)


def arbitrar(duplicado_id: str) -> dict:
    """Ejecuta el `arreglo_sql` del duplicado (scopeado por su propio WHERE a
    las filas que difieren). `{"ok", "filas"}` o `{"ok": False, "error"}`.
    Sin `arreglo_sql` no hace nada y lo dice: esos se arreglan a mano."""
    d = declarado(duplicado_id)
    if d is None:
        return {"ok": False, "error": f"«{duplicado_id}» no está declarado"}
    if not d.arreglo_sql.strip():
        return {"ok": False, "error": f"«{d.id}» no se arregla con un UPDATE: "
                                      f"{d.arreglo_manual or 'ver el árbitro'}"}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(d.arreglo_sql)
            return {"ok": True, "id": d.id, "filas": cur.rowcount or 0}
    except Exception as e:
        logger.warning("duplicados: no pude arbitrar %s (%s)", d.id, e)
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}


def una(duplicado_id: str) -> dict:
    """UN duplicado, con **todas** sus filas divergentes.

    `divergencias()` es el barrido: corre los N y trunca los ejemplos en
    `TOPE_EJEMPLOS`, porque su salida es un resumen para la pantalla. Esto es lo
    contrario — un solo par, sin truncar — y existe para que un control pueda
    trabajar sobre la lista completa **sin reimplementar el predicado**.

    Devuelve `{"ok": True, "filas": [(sujeto, valor_a, valor_b), …]}` o
    `{"ok": False, "error": …}`. ⚠️ Nunca una lista vacía cuando falló: «no pude
    mirar» y «no hay ninguno» tienen que poder distinguirse, que es la regla que
    sostiene todo este módulo.
    """
    d = next((x for x in DUPLICADOS if x.id == duplicado_id), None)
    if d is None:
        return {"ok": False, "error": f"«{duplicado_id}» no está declarado"}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(d.sql)
            return {"ok": True, "id": d.id, "filas": cur.fetchall()}
    except Exception as e:
        logger.warning("duplicados: no pude chequear %s (%s)", d.id, e)
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}


def divergencias() -> dict:
    """Los duplicados que NO coinciden hoy, y los que no se pudieron mirar.

    `{"partidos": [...], "sin_mirar": [...], "revisados": n}`. Nunca levanta: es
    un chequeo, y un chequeo que se cae no puede tumbar al job que lo llama.
    """
    partidos: list[dict] = []
    sin_mirar: list[dict] = []
    for d in DUPLICADOS:
        try:
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(d.sql)
                filas = cur.fetchall()
        except Exception as e:
            # **«No pude mirar» se DECLARA.** Si esto se tragara la excepción y
            # siguiera, un duplicado sin chequear se leería igual que uno sano.
            logger.warning("duplicados: no pude chequear %s (%s)", d.id, e)
            sin_mirar.append({"id": d.id, "que": d.que,
                              "error": f"{type(e).__name__}: {str(e)[:160]}"})
            continue
        if not filas:
            continue
        partidos.append({
            "id": d.id, "que": d.que, "a": d.a, "b": d.b,
            "arbitro": d.arbitro, "rompe": d.rompe,
            "tiene_sql": bool(d.arreglo_sql.strip()),
            "arreglo_manual": d.arreglo_manual,
            "n": len(filas),
            "ejemplos": [{"sujeto": r[0], "valor_a": r[1], "valor_b": r[2]}
                         for r in filas[:TOPE_EJEMPLOS]],
        })
    return {"partidos": partidos, "sin_mirar": sin_mirar,
            "revisados": len(DUPLICADOS) - len(sin_mirar)}
