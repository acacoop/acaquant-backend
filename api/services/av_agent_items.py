"""api/services/av_agent_items.py — EL STORE DE LOS OBJETOS DEL AGENTE.

Doc madre: **`docs/AV_AGENT.md`** §0.bd. El modelo (estados, transiciones,
hitos, `Item`) vive en `core/ciclo.py`; acá está la persistencia y nada más.

QUÉ RESUELVE, Y POR QUÉ ES UNA TABLA Y NO VEINTIDÓS
===================================================

El user (2026-08-21): *«que todo lo del AV Agent esté como objeto; va a ser
siempre el mismo estilo, solo que va a cambiar el TIPO de problema —log, aviso,
etc.— pero cómo van a estar es lo mismo»*.

Y la medición le daba la razón: **22 tablas, 8 formas de decir «resuelto»**.

⚠️ **LO QUE ESTO ARREGLA DE FONDO ES LA MEMORIA.** `av_agent_hallazgos` guarda
una FOTO por corrida: el mismo problema se reescribe entero cada vez, sin
identidad. Por eso aparecía «nuevo» todas las ruedas, por eso perdía que ya lo
habías votado, y por eso el agente parecía no acordarse de nada.

Acá la PK es la `clave` y **no lleva fecha**. Ver el mismo problema mañana no
crea una fila: actualiza la que hay. De ahí salen tres cosas que antes no
existían:

    · `abierto_at` mide antigüedad DE VERDAD (no «desde la última corrida»)
    · `veces` cuenta cuántas ruedas lleva sin resolverse
    · si estaba RESUELTO y reaparece → `volvio`, que **no es lo mismo que nuevo**

CONVIVE CON LO VIEJO
====================

No se migró ninguna tabla: las 22 siguen ahí y `core.ciclo.sin_migrar()` cuenta
las 11 que tienen ciclo propio. Esto es el destino, y cada superficie se mueve
cuando le toca. Migrar de un saque es cómo se rompe un sistema que funciona.
"""
from __future__ import annotations

import functools
import json
import logging
from datetime import UTC, datetime

from core import ciclo
from core.postgres import get_pool

logger = logging.getLogger(__name__)

_COLS = ("clave", "tipo", "origen", "sujeto", "regla", "estado", "severidad",
         "veces", "abierto_at", "ultimo_at", "visto_at", "resuelto_at",
         "vuelto_at", "titulo", "afecta", "datos")


def _fila(r) -> ciclo.Item:
    d = dict(zip(_COLS, r, strict=False))
    d["datos"] = d.get("datos") or {}
    return ciclo.Item(**d)


@functools.lru_cache(maxsize=1)
def _equivalencias() -> dict[str, str]:
    """`patas_equivocadas` (el control) → `pata_equivocada` (el detector).

    **DERIVADO de `ACCIONES`, no una segunda lista.** Cada acción ya declara las
    dos puntas: `sobre` es el control del que saca los casos y `causa` es la
    regla del detector con la que vota al eval set (§0.av). Que el mapa salga de
    ahí es lo que garantiza que no pueda contradecir al voto — si fuera una
    tabla aparte, un día diría una cosa y el eval set otra, sin fallar nunca.
    """
    try:
        from api.services.av_agent_hacer import ACCIONES, _causa_de
    except Exception:                       # nunca rompe por esto
        return {}
    out = {}
    for a in ACCIONES.values():
        causa = _causa_de(a)
        if causa and a.sobre and causa != a.sobre:
            out[a.sobre.strip().lower()] = causa.strip().lower()
    return out


def causa_canonica(regla: str) -> str:
    """La causa con UN solo nombre, sea quien sea el que la vio.

    El control se llama `patas_equivocadas` y el detector emite
    `pata_equivocada`: son **la misma causa** y sin normalizar producirían dos
    objetos para el mismo bono roto.
    """
    r = (regla or "").strip().lower()
    return _equivalencias().get(r, r)


def clave_de_problema(sujeto: str, regla: str, origen: str = "") -> str:
    """La identidad canónica: **(qué cosa, qué le pasa)**, con la causa
    normalizada. Es el ÚNICO lugar donde se arma — el detector, el control, la
    acción y el backfill la piden acá."""
    return ciclo.identidad(sujeto, causa_canonica(regla), origen)


def ver(*, tipo: str, origen: str, sujeto: str, regla: str = "",
        titulo: str = "", afecta: str = "", severidad: str = "media",
        datos: dict | None = None) -> dict:
    """**«Vi esto».** Lo crea si es nuevo, lo actualiza si ya estaba.

    Es el ÚNICO camino de entrada, y es idempotente: un detector puede llamarlo
    en cada corrida sin pensar. Lo que hace de más —y es todo el punto— es
    distinguir tres situaciones que hoy se veían iguales:

        no estaba          → nace `nuevo`
        estaba abierto     → suma `veces`, refresca el título, NO pisa `abierto_at`
        estaba RESUELTO    → **`volvio`**, con `vuelto_at`

    ⚠️ **`abierto_at` no se pisa nunca.** Es lo que convierte «apareció hoy» en
    «lleva 11 días»: sin eso, un problema de hace dos semanas se ve igual de
    urgente que uno de recién, y por eso nada acumulaba antigüedad.

    ⚠️ **El TÍTULO sí se refresca.** El problema es el mismo pero su explicación
    puede mejorar (una firma nueva, una traducción del modelo): guardar la
    primera para siempre sería congelar el peor texto.
    """
    clave = clave_de_problema(sujeto, regla, origen)
    if not clave:
        return {"ok": False, "error": "un item sin causa no es nada"}
    ahora = datetime.now(UTC)
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mercado.av_agent_items "
                "(clave, tipo, origen, sujeto, regla, estado, severidad, "
                " titulo, afecta, datos, abierto_at, ultimo_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) "
                "ON CONFLICT (clave) DO UPDATE SET "
                # El estado: si estaba resuelto y volvió a aparecer, VOLVIÓ.
                # Se resuelve en SQL —y no leyendo primero— para que dos
                # detectores corriendo a la vez no se pisen.
                "  estado = CASE WHEN mercado.av_agent_items.estado = %s "
                "                THEN %s ELSE mercado.av_agent_items.estado END, "
                "  vuelto_at = CASE WHEN mercado.av_agent_items.estado = %s "
                "                   THEN %s ELSE mercado.av_agent_items.vuelto_at END, "
                # Y si volvió, deja de estar resuelto: si no, el seguimiento
                # seguiría contándole hitos a un arreglo que ya falló.
                "  resuelto_at = CASE WHEN mercado.av_agent_items.estado = %s "
                "                     THEN NULL ELSE mercado.av_agent_items.resuelto_at END, "
                "  veces = mercado.av_agent_items.veces + 1, "
                "  ultimo_at = EXCLUDED.ultimo_at, "
                "  severidad = EXCLUDED.severidad, "
                "  titulo = EXCLUDED.titulo, "
                "  afecta = EXCLUDED.afecta, "
                "  datos = EXCLUDED.datos "
                "RETURNING (xmax = 0) AS nacio, estado, veces, abierto_at",
                (clave, tipo, origen, sujeto, regla, ciclo.NUEVO, severidad,
                 titulo, afecta, json.dumps(datos or {}, ensure_ascii=False,
                                            default=str), ahora, ahora,
                 ciclo.RESUELTO, ciclo.VOLVIO,
                 ciclo.RESUELTO, ahora,
                 ciclo.RESUELTO))
            nacio, estado, veces, abierto = cur.fetchone()
        return {"ok": True, "clave": clave, "nuevo": bool(nacio),
                "estado": estado, "veces": veces,
                "dias_abierto": round(ciclo._dias(abierto), 2)}
    except Exception as e:
        logger.warning("av_agent_items: no pude registrar %s (%s)", clave, e)
        return {"ok": False, "error": str(e)[:200]}


def marcar(clave: str, estado: str, *, por: str = "") -> dict:
    """Mueve un item de estado, **validando la transición**.

    Un salto imposible (`resuelto → en_curso`) se rechaza acá y no se descubre
    tres pantallas después. La marca de tiempo la pone el estado al que va: no
    hay dos fuentes para «cuándo se resolvió».
    """
    estado = (estado or "").strip()
    if estado not in ciclo.ESTADOS:
        return {"ok": False, "error": f"«{estado}» no es un estado"}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT estado FROM mercado.av_agent_items "
                        "WHERE clave = %s", (clave,))
            f = cur.fetchone()
            if not f:
                return {"ok": False, "error": "ese item no existe"}
            actual = f[0]
            if actual == estado:
                return {"ok": True, "sin_cambio": True, "estado": estado}
            if not ciclo.puede_pasar(actual, estado):
                return {"ok": False, "error": (
                    f"de «{actual}» no se puede pasar a «{estado}»")}
            ahora = datetime.now(UTC)
            cur.execute(
                "UPDATE mercado.av_agent_items SET estado = %s, "
                "  visto_at    = COALESCE(visto_at, %s), "
                "  resuelto_at = CASE WHEN %s = 'resuelto' THEN %s ELSE resuelto_at END, "
                "  vuelto_at   = CASE WHEN %s = 'volvio'   THEN %s ELSE vuelto_at END, "
                "  datos = datos || %s::jsonb "
                "WHERE clave = %s",
                (estado, ahora, estado, ahora, estado, ahora,
                 json.dumps({"por": por} if por else {}), clave))
        return {"ok": True, "de": actual, "a": estado}
    except Exception as e:
        logger.warning("av_agent_items: no pude marcar %s (%s)", clave, e)
        return {"ok": False, "error": str(e)[:200]}


def sincronizar(origen: str, vistos: list[dict], *,
                evaluados: set[str] | tuple[str, ...] = ()) -> dict:
    """Una corrida entera: **registra lo que está y CIERRA lo que ya no está.**

    Acá vive la memoria de verdad, y es la mitad que faltaba. `ver()` sabe que
    algo sigue; lo que nadie sabía es que algo **dejó de estar**, porque la foto
    por corrida no tiene forma de decir «esto ya no aparece»: simplemente sale
    una lista más corta. Por eso el agente nunca podía afirmar que algo se
    arregló, y por eso el seguimiento no tenía de dónde arrancar.

        lo que está      → `ver()`: nace, o suma `veces`, o pasa a `volvio`
        lo que YA NO     → `resuelto`, con `resuelto_at` = ahora
                           y ahí arranca el conteo de hitos (1·2·3·7·14·30)

    ⚠️⚠️ **`evaluados` NO ES OPCIONAL EN LA PRÁCTICA, Y ES LA GUARDA MÁS
    IMPORTANTE DE ESTE MÓDULO.** Una corrida puede mirar MENOS de lo que mira
    siempre: si 1816 no contesta, `falta_en_base` no se evaluó — y su lista
    vacía **no significa que no falte ningún bono**, significa que no se miró.

    Cerrar por ausencia sin saber qué se miró convertiría cada caída de un
    proveedor en «se arreglaron 40 problemas», que es la mentira más cara que
    puede decir una herramienta de integridad: deja el tablero en verde
    exactamente el día que está más ciego. El propio job ya distingue los dos
    casos (imprime «los FALTANTES no se evaluaron en esta corrida»); lo que
    faltaba era que la persistencia también los distinguiera.

    **Solo se cierran los tipos que la corrida declara haber evaluado.** Un tipo
    fuera de `evaluados` se registra si aparece, y **jamás** se cierra.
    """
    origen = (origen or "").strip()
    if not origen:
        return {"ok": False, "error": "sin origen no se puede cerrar nada: "
                                      "cerraría hallazgos de otro detector"}
    evaluados = {str(x).strip() for x in (evaluados or ()) if str(x).strip()}

    claves_vistas: set[str] = set()
    nuevos = vueltos = 0
    for h in vistos or []:
        tipo = str(h.get("tipo") or "").strip()
        if not tipo:
            continue
        r = ver(tipo=tipo, origen=origen,
                sujeto=str(h.get("ticker") or h.get("sujeto") or ""),
                regla=str(h.get("regla") or ""),
                titulo=str(h.get("motivo") or h.get("titulo") or ""),
                afecta=str(h.get("afecta") or ""),
                severidad=str(h.get("severidad") or "media"),
                datos=h.get("evidencia") or h.get("datos") or {})
        if not r.get("ok"):
            continue
        claves_vistas.add(r["clave"])
        nuevos += 1 if r.get("nuevo") else 0
        vueltos += 1 if r.get("estado") == ciclo.VOLVIO else 0

    cerrados = _cerrar_ausentes(origen, claves_vistas, evaluados)
    return {"ok": True, "origen": origen, "vistos": len(claves_vistas),
            "nuevos": nuevos, "vueltos": vueltos, "resueltos": cerrados,
            "no_evaluados": sorted(_tipos_abiertos(origen) - evaluados)}


def _cerrar_ausentes(origen: str, vistas: set[str], evaluados: set[str]) -> int:
    """Cierra lo que este detector dejó de ver. **Solo de los tipos evaluados.**"""
    if not evaluados:
        # Sin saber qué se miró no se cierra NADA. Es la degradación correcta:
        # dejar un problema resuelto en la lista molesta; borrar uno que sigue
        # roto no se ve nunca.
        return 0
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE mercado.av_agent_items "
                "   SET estado = %s, resuelto_at = now() "
                " WHERE origen = %s "
                "   AND tipo = ANY(%s) "
                "   AND estado NOT IN (%s, %s) "
                "   AND NOT (clave = ANY(%s))",
                (ciclo.RESUELTO, origen, sorted(evaluados),
                 ciclo.RESUELTO, ciclo.IGNORADO, sorted(vistas) or [""]))
            return cur.rowcount or 0
    except Exception as e:
        logger.warning("av_agent_items: no pude cerrar los ausentes de %s (%s)",
                       origen, e)
        return 0


def _tipos_abiertos(origen: str) -> set[str]:
    """Qué tipos tiene abiertos este detector. Sirve para DECIR cuáles quedaron
    sin evaluar — el silencio se lee igual que un verde."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT tipo FROM mercado.av_agent_items "
                        "WHERE origen = %s AND estado NOT IN (%s, %s)",
                        (origen, ciclo.RESUELTO, ciclo.IGNORADO))
            return {r[0] for r in cur.fetchall()}
    except Exception:
        return set()


# ⚠️ Acá vivía `marcar_por(sujeto, regla, estado)`: el PUENTE entre los dos
# objetos que el mismo problema generaba (uno del detector y otro del control).
# **Sobra desde que la identidad es (sujeto, causa)** (§0.bj): ahora los dos
# escriben en el mismo objeto y no hay nada que puentear.
#
# Se BORRA en vez de dejarse "por las dudas" (REGLA #5): un puente a ninguna
# parte que sigue exportado es una función que alguien va a usar creyendo que
# hace falta, y ahí vuelven los dos objetos por otro camino.


def abiertos(tipo: str = "", limite: int = 400) -> list[ciclo.Item]:
    """Lo que sigue vivo. **Incluye `volvio`**: un problema que reapareció está
    abierto, y además merece más atención que uno nuevo."""
    where = "estado NOT IN ('resuelto', 'ignorado')"
    params: list = []
    if tipo:
        where += " AND tipo = %s"
        params.append(tipo)
    params.append(max(1, min(int(limite), 2000)))
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(_COLS)} FROM mercado.av_agent_items "
                        f"WHERE {where} ORDER BY ultimo_at DESC LIMIT %s",
                        tuple(params))
            return [_fila(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("av_agent_items: no pude listar (%s)", e)
        return []


def en_seguimiento() -> list[dict]:
    """Los arreglos que se están mirando, **con cuántos hitos llevan**.

    El user: *«5 días es mucho, es el día siguiente para ver si vuelve. Pero a
    su vez tiene que tener memoria y recursos para que siga con el paso del
    tiempo: puede ser 2 días, 3 días…»*.

    Por eso no es un plazo: son HITOS (1·2·3·7·14·30). El primero da la señal
    rápida —si vuelve mañana, el arreglo no sirvió— y los siguientes acumulan
    confianza, que es lo que después habilita autonomía. **Volver una vez borra
    todo lo acumulado**: un arreglo que falla al día 8 no es «7 días bueno».
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_COLS)} FROM mercado.av_agent_items "
                "WHERE estado = 'resuelto' AND resuelto_at IS NOT NULL "
                "ORDER BY resuelto_at DESC LIMIT 500")
            items = [_fila(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("av_agent_items: no pude leer el seguimiento (%s)", e)
        return []
    out = []
    for it in items:
        d = it.dias_resuelto()
        out.append({
            "clave": it.clave, "sujeto": it.sujeto, "regla": it.regla,
            "titulo": it.titulo, "dias": round(d, 2),
            "hitos": ciclo.hitos_cumplidos(d), "de": len(ciclo.HITOS_DIAS),
            "confianza": ciclo.confianza(d),
            "proximo_hito_en_dias": ciclo.proximo_hito(d),
            "aguanto": ciclo.hitos_cumplidos(d) >= len(ciclo.HITOS_DIAS)})
    return out


def cerrar_hitos() -> dict:
    """**El tiempo, convertido en evidencia.** Corre una vez por día.

    Es la pasada que le pone número a *«¿el arreglo sirvió?»*, y es la señal más
    fuerte que tiene el sistema porque **no es la opinión de nadie**: el problema
    volvió o no volvió, y el agente no controla eso.

        pasó TODOS los hitos (30 días sin volver)  → ✔ `verificado`
        VOLVIÓ                                     → ✖ `verificado`, con la fecha

    Los dos votan al eval set con `origen='verificado'`, que la compuerta de
    autonomía cuenta a la par de un voto humano (§0.f) — a diferencia de
    `derivado`, que es alguien diciendo «dale» y no el mundo diciendo «funcionó».

    ⚠️ **Idempotente por `ref`.** El job corre todos los días y los que
    aguantaron siguen aguantando: sin el `ref`, el mismo arreglo votaría una vez
    por día y en un mes tendría 30 votos que son uno solo. El índice único de
    `av_agent_evals` lo rechaza y acá se cuenta como duplicado.

    ⚠️ **Y «todavía no volvió» NO es «aguantó».** Solo vota el que pasó el
    ÚLTIMO hito. Los del medio siguen en prueba — premiar a los tres días sería
    justo lo que el escalonado vino a evitar.
    """
    from api.services import av_agent_evals

    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(_COLS)} FROM mercado.av_agent_items "
                " WHERE (estado = %s AND resuelto_at IS NOT NULL) OR estado = %s "
                " LIMIT 1000", (ciclo.RESUELTO, ciclo.VOLVIO))
            items = [_fila(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("av_agent_items: no pude leer el seguimiento (%s)", e)
        return {"ok": False, "error": str(e)[:200]}

    aguantaron: list[dict] = []
    volvieron: list[dict] = []
    votos = 0
    tope = max(ciclo.HITOS_DIAS)
    for it in items:
        if it.estado == ciclo.VOLVIO:
            r = av_agent_evals.votar(
                caso=it.sujeto or it.clave, dominio="bono", causa=it.regla,
                acierta=False, origen="verificado", ref=f"volvio:{it.clave}",
                nota="el arreglo no aguantó: el problema volvió a aparecer")
            volvieron.append({"sujeto": it.sujeto, "regla": it.regla,
                              "titulo": it.titulo})
            votos += 1 if r.get("ok") and not r.get("duplicado") else 0
            continue
        d = it.dias_resuelto()
        if d < tope:
            continue                       # sigue en prueba: no se premia
        r = av_agent_evals.votar(
            caso=it.sujeto or it.clave, dominio="bono", causa=it.regla,
            acierta=True, origen="verificado", ref=f"aguanto:{it.clave}",
            nota=f"aguantó {tope} días sin volver")
        aguantaron.append({"sujeto": it.sujeto, "regla": it.regla,
                           "dias": round(d, 1)})
        votos += 1 if r.get("ok") and not r.get("duplicado") else 0

    return {"ok": True, "mirados": len(items), "aguantaron": aguantaron,
            "volvieron": volvieron, "votos": votos,
            "en_prueba": [x for x in en_seguimiento() if not x["aguanto"]]}


def resumen() -> dict:
    """Cuántos hay de cada estado y de cada tipo. Una query."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT tipo, estado, count(*) "
                        "FROM mercado.av_agent_items GROUP BY tipo, estado")
            filas = cur.fetchall()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    por_estado: dict[str, int] = {}
    por_tipo: dict[str, int] = {}
    for tipo, estado, n in filas:
        por_estado[estado] = por_estado.get(estado, 0) + n
        por_tipo[tipo] = por_tipo.get(tipo, 0) + n
    return {"ok": True, "por_estado": por_estado, "por_tipo": por_tipo,
            "total": sum(por_estado.values())}
