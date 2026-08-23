"""api/services/av_agent_masivo.py — EL DIAGNÓSTICO MASIVO del AV Agent.

Doc madre: **`docs/AV_AGENT.md`** §0.i.

**Qué contesta.** *«De los 68 hallazgos, cuáles se explican, cuáles no, y por
qué»* — en un solo informe, agrupado por causa y listo para copiar.

Pedido del user (2026-08-18): *«un botón que haga un estado de situación con los
que dieron error, los que dieron bien, etc., bien completo, que quede para copiar
y pegar así te paso las respuestas»*.

**Por qué importa más de lo que parece.** Hoy los diagnósticos se miran de a uno,
y de a uno no se ven los patrones. Un informe de 68 muestra cosas que ninguna
fila individual puede mostrar: que 68 casos son 4 causas (el trabajo real es más
chico de lo que parece), que la verificación de una causa NO vuelve al rango en
12 de ellos (entonces esa causa está mal), o que 9 fallan con la misma excepción
(eso es UN bug de código disfrazado de 9 hallazgos). El bug de `moneda_flujo` se
encontró justo así, comparando 8 hallazgos contra 30 bonos.

**Por qué corre en BACKGROUND.** El plan de 1816 permite **1 petición por
segundo** y el throttle es global entre procesos (`core/mercado_1816._throttle`).
68 bonos con `cashflow` son ~82 segundos de piso y 2-3 minutos reales; ningún
request HTTP sobrevive a eso — el propio agente aborta a los 45s por
`_PRESUPUESTO_S`. Así que se arranca, se devuelve un id y el modal pollea.

⚠️ **La corrida vive en un thread del proceso de la API** (uvicorn corre sin
workers). Si se reinicia la API a mitad de camino, el thread muere y la fila
queda en `corriendo` para siempre — por eso hay `latido_at`: un `corriendo` sin
latido reciente se LEE como `interrumpido`. La corrida no puede escribir su
propia lápida.

**No se re-implementa ningún diagnóstico.** Cada caso pasa por la MISMA puerta
que usa el modal (`simular_arreglo`, `simular_flujos`, `simular`,
`salud.diagnosticar`) — dos caminos al mismo diagnóstico terminan
contradiciéndose, que es el bug que este agente ya se comió tres veces.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime

from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Cuánto puede pasar sin latido antes de leer un `corriendo` como muerto. Con el
# throttle de 1,2s y algún reintento, un caso lento tarda decenas de segundos:
# 3 minutos deja margen de sobra sin dejar un run zombie a la vista todo el día.
_LATIDO_MUERTO_S = 180

# Tope de créditos por corrida. **No es para ahorrar** —el plan da 100.000
# diarios y usamos ~4.000—: es para que un bug que pida `cashflow` en loop se
# corte en 5.000 y no en 100.000. Un tope que nunca se toca no molesta a nadie;
# el día que se toca, avisó de un bug.
TOPE_CREDITOS_DEFAULT = 5000

# Cada cuántos casos se relee el saldo. Cuesta 1 crédito y una llamada (o sea
# 1,2s de la cola, que es el recurso escaso de verdad), así que no se hace por
# caso. Con 10 el tope se detecta con un sobrepaso acotado.
_CADA_CUANTO_SALDO = 10

# Los runs vivos de ESTE proceso, para poder frenarlos. La fila de la base dice
# `frenado`; esto es lo que hace que el thread se entere sin pollear la base en
# cada vuelta.
_frenar: set[int] = set()


# ── La base ─────────────────────────────────────────────────────────────────

def _crear(*, por: str, filtro: dict, total: int, sin_red: bool,
           tope: int | None) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agente.av_agent_runs (por, filtro, total, sin_red, "
            " tope_creditos, latido_at) VALUES (%s, %s::jsonb, %s, %s, %s, now()) "
            "RETURNING id",
            (por or None, json.dumps(filtro, ensure_ascii=False, default=str),
             total, sin_red, tope))
        rid = cur.fetchone()[0]
        conn.commit()
    return int(rid)


def _latir(run_id: int, hechos: int, informe: list[dict]) -> None:
    """Progreso + informe parcial. Se escribe el informe ENTERO en cada vuelta
    (no un append) porque así el que pollea ve resultados desde el primer caso —
    un informe que aparece solo al final no deja abortar una corrida que ya se ve
    mal, que es justo cuando uno quiere abortarla."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE agente.av_agent_runs SET hechos = %s, "
                        "latido_at = now(), informe = %s::jsonb WHERE id = %s",
                        (hechos, json.dumps(informe, ensure_ascii=False, default=str),
                         run_id))
            conn.commit()
    except Exception as e:
        logger.warning("av_agent_masivo: no se pudo latir el run %s: %s", run_id, e)


def _cerrar(run_id: int, estado: str, *, creditos: int | None = None,
            error: str = "") -> None:
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE agente.av_agent_runs SET estado = %s, fin_at = now(), "
                        "latido_at = now(), creditos = %s, error = %s WHERE id = %s",
                        (estado, creditos, (error or None)[:2000] if error else None,
                         run_id))
            conn.commit()
    except Exception as e:
        logger.warning("av_agent_masivo: no se pudo cerrar el run %s: %s", run_id, e)


# ── La corrida ──────────────────────────────────────────────────────────────

def _saldo() -> int | None:
    """Créditos usados hoy, según 1816. `None` si no se pudo leer — y entonces el
    tope no se puede aplicar, cosa que el informe DICE en vez de simular que sí."""
    try:
        from core import mercado_1816
        return (mercado_1816.balance() or {}).get("daily", {}).get("used")
    except Exception:
        return None


# ── LAS PUERTAS: qué función diagnostica cada modo ──────────────────────────
#
# ⚠️⚠️ **ERA LA CUARTA COPIA DE LA MISMA TABLA DE RUTEO, y se había separado.**
# Acá vivía un `if/elif` con CUATRO modos (`salud · flujos · alta · arreglo`)
# mientras `av_agent.accion_de()` ya devolvía OCHO. Los otros cuatro caían al
# `else` y el informe los declaraba **«SIN PUERTA — el agente los ve y todavía
# no sabe tocarlos»**.
#
# Medido en el masivo #14 (2026-08-22): de 26 «sin puerta», **13 tenían acción
# desde hacía días** — 11 `pata_equivocada` (que se arreglan con
# `mercado.apuntar_pata`, escrita justamente porque el user se hartó de verlos
# 17 veces) y 2 `sin_espejo_en_assets`. El agente decía que no sabía hacer algo
# que sabía hacer, y por eso el informe pedía construir lo que ya estaba
# construido.
#
# Es el MISMO bug que los BOPREALes, en su tercera reencarnación: una tabla de
# ruteo copiada. Ahora hay UNA sola y un test exige que todo modo que
# `accion_de()` pueda devolver esté acá — un modo nuevo no puede volver a
# aparecer como deuda inexistente.
def _p_salud(sujeto: str, _caso: dict) -> dict:
    # ⚠️ Sin `con_ia`: ese parámetro se fue cuando se dio de baja la lente con
    # IA (2026-08-19) y esta llamada quedó pasándolo. Resultado: **14 casos
    # EXPLOTARON con TypeError** en el masivo #6 — todos los chequeos de SALUD y
    # todos los controles, o sea la categoría entera. El masivo los separa bien
    # («esto es un bug, no un bono mal cargado») pero nadie los miraba: la
    # corrida terminaba «OK».
    from api.services import av_agent_salud
    return av_agent_salud.diagnosticar(sujeto)


def _p_flujos(sujeto: str, _caso: dict) -> dict:
    from api.services import av_agent_alta
    return av_agent_alta.simular_flujos(sujeto)


def _p_alta(sujeto: str, caso: dict) -> dict:
    from api.services import av_agent_alta
    return av_agent_alta.simular(
        sujeto, curva_1816=str((caso.get("evidencia") or {}).get("curva_1816") or ""))


def _p_arreglo(sujeto: str, _caso: dict) -> dict:
    from api.services import av_agent_alta
    return av_agent_alta.simular_arreglo(sujeto)


def _p_sin_precio(sujeto: str, _caso: dict) -> dict:
    from api.services import av_agent_sin_precio
    return av_agent_sin_precio.diagnosticar(sujeto)


def _p_espejo(sujeto: str, _caso: dict) -> dict:
    from api.services import av_agent_espejo
    return av_agent_espejo.diagnosticar(sujeto)


def _p_pata(sujeto: str, _caso: dict) -> dict:
    from api.services import av_agent_pata
    return av_agent_pata.explicar(sujeto)


def _p_apuntar(sujeto: str, _caso: dict) -> dict:
    # ⚠️ **SIMULA, no aplica.** `uno(..., aplicar_ya=False)` arma el caso
    # volviendo a correr el control y devuelve QUÉ HARÍA. El masivo diagnostica
    # 92 bonos de una: si alguna puerta escribiera, una corrida de rutina se
    # convertiría en 92 escrituras que nadie aprobó.
    from api.services import av_agent_hacer
    return av_agent_hacer.uno("mercado.apuntar_pata", sujeto, aplicar_ya=False)


PUERTAS = {
    "salud": _p_salud, "flujos": _p_flujos, "alta": _p_alta,
    "arreglo": _p_arreglo, "sin_precio": _p_sin_precio, "espejo": _p_espejo,
    "pata": _p_pata, "apuntar": _p_apuntar,
}


def _cerrar_viejo(caso: dict, sujeto: str, causa: str) -> str:
    """El diagnóstico probó que ya no aplica → el item pasa a `en_curso`.

    ⚠️⚠️ **`en_curso` y NO `resuelto` (caso GD46, 2026-08-22).** Marcar
    `resuelto` generaba VOLVIÓ espurio: el diagnóstico prueba «sano» sobre
    `mercado.curvas`, pero el DETECTOR puede seguir viendo el síntoma en
    producción (la TEA vive en `market_snapshot` y recién cambia cuando el
    motor se reinicia) → resuelto + re-avistaje = «volvió», ensuciando la
    señal más valiosa del modelo. `en_curso` saca la fila de la lista IGUAL
    (cuenta como atendida) y deja que el DETECTOR sea quien cierre de verdad
    cuando deje de verlo — el agente no califica su propio trabajo.
    Nunca levanta, y si no pudo moverlo **lo dice**.
    """
    from api.services import av_agent_items
    from core import ciclo
    clave = av_agent_items.clave_de_problema(
        sujeto, str(caso.get("regla") or ""), str(caso.get("origen") or ""))
    r = av_agent_items.marcar(clave, ciclo.EN_CURSO, por="av-agent:masivo")
    if r.get("ok") or r.get("sin_cambio"):
        return (f"se comprobó que ya no aplica ({causa or 'sin causa'}) → "
                f"queda ATENDIDO; lo cierra el detector cuando deje de verlo. "
                f"Si en cambio lo re-ve tras un cierre, entra como «volvió».")
    return (f"la cadena probó que ya no aplica, pero **no pude moverlo**: "
            f"{r.get('error') or 'sin motivo'}. Va a seguir en la lista.")


# Por qué NO hay botón, dicho por TIPO — la frase genérica («no tiene qué
# hacer») se leía como un defecto en filas donde la respuesta correcta es otra.
_SIN_PUERTA_POR_TIPO = {
    "motor_caido": "es infraestructura: se mira y se decide afuera — reiniciar "
                   "un motor no es un botón del agente",
    "motor_ruidoso": "es infraestructura: el agente la vigila y te la muestra; "
                     "arreglarla es una decisión de la mesa, no un botón",
    "proveedor_caido": "es un proveedor externo: el agente lo vigila y avisa "
                       "cuando vuelve — no hay nada que aplicar de este lado",
    "tabla_quieta": "es una observación de la base: se contesta en la fila con "
                    "¿TE SIRVE VERLO? — «✖ es ruido» la esconde",
    "db_cambio": "es una observación de la base (tabla nueva/crecida): se "
                 "contesta en la fila con ¿TE SIRVE VERLO? — «✖ es ruido» la "
                 "esconde",
}


def _diagnosticar_uno(caso: dict, sin_red: bool) -> dict:
    """UN caso, por la misma puerta que el modal. Devuelve la fila del informe.

    Nunca levanta: una excepción en el caso 7 no puede tumbar los 61 restantes —
    se anota como `error` y la corrida sigue. Un informe que se corta en el primer
    problema no sirve justamente para el día que hay problemas.
    """
    sujeto = caso.get("ticker") or "?"
    accion = caso.get("accion") or ""
    fila = {"sujeto": sujeto, "tipo": caso.get("tipo"), "regla": caso.get("regla"),
            "accion": accion, "motivo_hallazgo": caso.get("motivo")}
    t0 = time.perf_counter()
    try:
        puerta = PUERTAS.get(accion)
        if puerta is not None:
            r = puerta(sujeto, caso)
        else:
            # Un hallazgo sin acción no siempre es «falta construir la puerta»
            # (user, 2026-08-22: «no termino de entender por qué no tiene que
            # hacer nada»). Hay TRES casos y decían la misma frase:
            #   · infraestructura (motores, proveedores): se MIRA y se decide
            #     afuera — reiniciar un motor en rueda no es un botón del agente;
            #   · observaciones de la base (tabla nueva, tabla quieta): la
            #     respuesta es el voto ¿TE SIRVE VERLO? de la fila — «✖ es
            #     ruido» la esconde; no hay nada que aplicar;
            #   · el resto: la puerta de verdad falta, y nombrarlo es la lista
            #     de lo que hay que construir.
            detalle = _SIN_PUERTA_POR_TIPO.get(
                caso.get("tipo") or "",
                "el agente lo ve pero todavía no tiene qué hacer con esto")
            return {**fila, "estado": "sin_puerta",
                    "detalle": detalle, "segundos": 0.0}
    except Exception as e:
        return {**fila, "estado": "error", "detalle": f"{type(e).__name__}: {e}",
                "segundos": round(time.perf_counter() - t0, 2)}

    seg = round(time.perf_counter() - t0, 2)
    if not r.get("ok"):
        return {**fila, "estado": "no_pudo", "detalle": str(r.get("error") or "")[:400],
                "segundos": seg}

    ver = r.get("veredicto") or {}
    pasos = r.get("chequeos") or []
    # La CAUSA es lo que agrupa el informe: 68 casos se vuelven 4 líneas.
    causa = r.get("causa") or (r.get("diagnostico") or {}).get("causa") or ""

    # ── SE COMPROBÓ QUE YA NO APLICA → SE CIERRA ────────────────────────────
    #
    # ⚠️⚠️ El user (2026-08-22): *«si dio error y no pudo encontrar el error que
    # dio, CHAU DESAPARECE… ya no sé cómo explicar que no quiero basura acá»*.
    #
    # Tiene razón y el caso es concreto: SFD34 y BPOD7 salían como
    # `sin_tea_con_precio` y la cadena terminaba en **«se comprobó que el bono
    # está bien, este hallazgo quedó viejo»** — con la TEA coincidiendo con 1816
    # a 0 bps. El agente lo PROBÓ y la fila se quedaba igual, para siempre,
    # obligando a leerla de nuevo cada mañana.
    #
    # **Por qué acá y no al abrir el modal**: mirar no puede escribir. El masivo
    # es una pasada deliberada que ya diagnosticó todo, así que cerrar es su
    # conclusión, no un efecto secundario de haber abierto una pantalla. Y se
    # CUENTA en el informe: cerrar en silencio sería otra forma de esconder.
    #
    # El estado `resuelto` NO es destructivo — el objeto guarda su historia y si
    # el problema vuelve reaparece como `volvio`, que informa más que uno nuevo.
    desenlace = (ver.get("desenlace") or {}).get("clase") or ""
    if desenlace == "viejo":
        cerrado = _cerrar_viejo(caso, sujeto, causa)
        return {**fila, "estado": "cerrado", "causa": causa,
                "veredicto": ver.get("texto") or "",
                "detalle": cerrado, "segundos": seg}

    return {
        **fila,
        # `listo` = el agente sabe qué hacer y la cadena lo habilita. Es la
        # pregunta que uno le hace al informe, y por eso es un estado y no algo
        # que haya que deducir mirando dos campos.
        "estado": "listo" if ver.get("puede_aplicar") else "bloqueado",
        "causa": causa,
        "veredicto": ver.get("texto") or "",
        "sin_red": bool(r.get("sin_red")),
        "tea": r.get("tea"),
        "antes": r.get("antes"),
        "propuesta": r.get("parche") or r.get("propuesta"),
        # Solo los pasos que NO están en verde: el informe es para leer, y 10
        # pasos × 68 casos son 680 líneas donde lo que importa son las que fallan.
        # ⚠️ **SIN REPETIR, Y ACÁ — no solo en la versión para el modelo.** El
        # user (2026-08-21): *«necesito respuestas más claras, menos texto y más
        # claro cuál es el problema»*. Un caso salía con la MISMA frase tres
        # veces bajo tres títulos distintos («La escala del cuadro que YA está
        # cargado», «El valor técnico: ¿en qué escala está el cronograma?»,
        # «⇒ LA CONCLUSIÓN»), y esto decía que para una persona esa redundancia
        # ayudaba. No ayuda: hace dudar de si son tres problemas o uno.
        "trabas": _sin_repetir(
            [{"paso": p.get("titulo"), "estado": p.get("estado"),
              "detalle": str(p.get("detalle") or "")[:300]}
             for p in pasos if p.get("estado") in ("bloquea", "revisar")])[:3],
        "segundos": seg,
    }


def _correr(run_id: int, casos: list[dict], sin_red: bool, tope: int | None) -> None:
    """El bucle. Corre en su propio thread; **no levanta nunca hacia afuera**."""
    informe: list[dict] = []
    saldo_ini = None if sin_red else _saldo()
    creditos = None
    estado_final, err = "terminado", ""
    try:
        for i, caso in enumerate(casos, 1):
            if run_id in _frenar:
                estado_final = "frenado"
                break
            informe.append(_diagnosticar_uno(caso, sin_red))
            _latir(run_id, i, informe)

            # El TOPE, cada N casos. Se mide contra 1816, no se estima: una
            # estimación de créditos que se equivoca es peor que no tener tope.
            if (tope and saldo_ini is not None and not sin_red
                    and i % _CADA_CUANTO_SALDO == 0):
                s = _saldo()
                if s is not None:
                    creditos = s - saldo_ini
                    if creditos >= tope:
                        estado_final = "frenado"
                        err = (f"se alcanzó el tope de {tope} créditos en el caso "
                               f"{i} de {len(casos)} — el resto quedó sin mirar")
                        break
    except Exception as e:                       # defensa de última línea
        estado_final, err = "error", f"{type(e).__name__}: {e}"
        logger.exception("av_agent_masivo: el run %s murió", run_id)
    finally:
        _frenar.discard(run_id)
        if saldo_ini is not None:
            s = _saldo()
            if s is not None:
                creditos = s - saldo_ini
        _latir(run_id, len(informe), informe)
        _cerrar(run_id, estado_final, creditos=creditos, error=err)


# ── La API del service ──────────────────────────────────────────────────────

def arrancar(casos: list[dict], *, por: str = "", filtro: dict | None = None,
             sin_red: bool = False,
             tope_creditos: int | None = TOPE_CREDITOS_DEFAULT) -> dict:
    """Arranca la corrida y devuelve el id **al instante**. El trabajo sigue en
    background: con 1 petición por segundo, esperar el resultado no es una
    opción."""
    casos = [c for c in (casos or []) if c.get("ticker")]
    # ⚠️⚠️ **EL MASIVO CONSULTA EL DNI ANTES DE DIAGNOSTICAR** (2026-08-22).
    # Los casos llegan de la FOTO de la última corrida, que no sabe qué pasó
    # después: GD46 se arregló a las 19:10 y a las 20:05 la corrida lo volvió
    # a diagnosticar (y el lote lo volvió a APLICAR — dos `arreglar_bono` en el
    # libro para el mismo arreglo). El user: *«no se pueden repetir las cosas
    # ni arreglar cosas que funcionan — puede generar un problemón»*. El objeto
    # (`av_agent_items`) ya sabe que ese problema está atendido: se saltea y
    # se DICE cuántos (saltear en silencio se lee como que se perdieron).
    saltados = 0
    try:
        from api.services import av_agent_items
        from core import ciclo
        atendidos = {ciclo.EN_CURSO, ciclo.RESUELTO, ciclo.IGNORADO}
        claves = [av_agent_items.clave_de_problema(
            str(c.get("ticker") or ""), str(c.get("regla") or ""),
            str(c.get("origen") or "")) for c in casos]
        estados = av_agent_items.estados_de(claves)
        vivos = [c for c, k in zip(casos, claves, strict=True)
                 if estados.get(k) not in atendidos]
        saltados = len(casos) - len(vivos)
        casos = vivos
    except Exception as e:
        logger.warning("av_agent_masivo: no pude filtrar atendidos (%s)", e)
    # ⚠️ **UN BONO, UN DIAGNÓSTICO** (informe #18, 2026-08-23): VSCYO, OLC3O,
    # PECKO y CO3D7 salían DOS veces cada uno — dos reglas de detección sobre
    # el mismo papel son dos filas de la foto, pero el diagnóstico corre POR
    # BONO (las mismas ocho lentes, la misma llamada a 1816): correrlo dos
    # veces duplica créditos y llena el informe con párrafos idénticos. Se
    # dedupea por (sujeto, acción) quedándose el primero.
    vistos_dedup: set[tuple[str, str]] = set()
    unicos = []
    for c in casos:
        k = (str(c.get("ticker") or ""), str(c.get("accion") or ""))
        if k in vistos_dedup:
            continue
        vistos_dedup.add(k)
        unicos.append(c)
    casos = unicos
    if not casos:
        return {"ok": False,
                "error": ("no hay casos para diagnosticar"
                          if not saltados else
                          f"los {saltados} casos ya están atendidos "
                          "(aplicados o cerrados) — nada para re-diagnosticar")}
    if len(casos) > 500:
        return {"ok": False, "error": f"son {len(casos)} casos: filtrá primero. "
                                      f"A 1,2s por llamada eso es más de 10 minutos."}
    rid = _crear(por=por, filtro=filtro or {}, total=len(casos), sin_red=sin_red,
                 tope=tope_creditos)
    t = threading.Thread(target=_correr, args=(rid, casos, sin_red, tope_creditos),
                         name=f"av-masivo-{rid}", daemon=True)
    t.start()
    return {"ok": True, "run_id": rid, "total": len(casos),
            # Lo que se salteó por ya estar atendido — se dice, no se esconde.
            "saltados_atendidos": saltados,
            # La ESPERA estimada, dicha de entrada. Sin esto el que arranca una
            # corrida de 3 minutos cree que se colgó y la vuelve a arrancar.
            "segundos_estimados": 0 if sin_red else int(len(casos) * 1.4)}


def frenar(run_id: int) -> dict:
    """Corta la corrida en el próximo caso. Lo ya diagnosticado queda."""
    _frenar.add(int(run_id))
    return {"ok": True, "run_id": int(run_id)}


def marcar_visto(run_id: int) -> dict:
    """CIERRA el informe en la pantalla — sin borrar nada (2026-08-22).

    El run quedaba pegado para siempre: un «interrumpido · sin señales hace
    107 min» de ayer sin ninguna forma de sacarlo de la vista («el informe no
    se puede cerrar, está 100% estático» — user). Cerrar es una marca, no un
    borrado: la corrida es historia y `GET /masivo` la sigue devolviendo; el
    front la muestra plegada en una línea, reabrible. Una corrida EN CURSO no
    se puede cerrar — primero se frena.

    ⚠️ **«En curso» es el latido, no la columna** (user, 2026-08-22: *«da
    error y no deja cerrar tampoco»*). Un run que murió con la API (reinicio a
    mitad de corrida) queda `corriendo` en la base PARA SIEMPRE — la pantalla
    ya lo deduce como «interrumpido» por el latido viejo, pero este UPDATE
    miraba solo la columna y rechazaba cerrarlo: el único informe imposible de
    cerrar era justamente el que quedó pegado. El criterio es EL MISMO
    `_LATIDO_MUERTO_S` que usa `estado()` — dos umbrales para «está vivo» es
    cómo la pantalla dice una cosa y el botón otra.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE agente.av_agent_runs SET visto_at = now() "
                    " WHERE id = %s AND (estado <> 'corriendo' "
                    "    OR latido_at IS NULL "
                    "    OR latido_at < now() - make_interval(secs => %s)) "
                    "RETURNING id",
                    (int(run_id), _LATIDO_MUERTO_S))
        fila = cur.fetchone()
        conn.commit()
    if not fila:
        return {"ok": False,
                "error": "no existe, o sigue corriendo (frenala primero)"}
    return {"ok": True, "run_id": int(run_id)}


def _fila(run_id: int | None) -> dict | None:
    sql = ("SELECT id, creado_at, fin_at, estado, por, filtro, total, hechos, "
           "latido_at, sin_red, tope_creditos, creditos, error, informe, visto_at "
           "FROM agente.av_agent_runs ")
    with get_pool().connection() as conn, conn.cursor() as cur:
        if run_id:
            cur.execute(sql + "WHERE id = %s", (run_id,))
        else:
            cur.execute(sql + "ORDER BY creado_at DESC LIMIT 1")
        r = cur.fetchone()
    if not r:
        return None
    cols = ["id", "creado_at", "fin_at", "estado", "por", "filtro", "total", "hechos",
            "latido_at", "sin_red", "tope_creditos", "creditos", "error", "informe",
            "visto_at"]
    d = dict(zip(cols, r, strict=True))
    for k in ("creado_at", "fin_at", "latido_at", "visto_at"):
        d[k] = d[k].isoformat() if d[k] else None
    return d


def estado(run_id: int | None = None) -> dict:
    """El run pedido (o el último). Trae el informe parcial: se puede mirar
    mientras corre."""
    d = _fila(run_id)
    if not d:
        return {"ok": False, "error": "no hay ninguna corrida"}

    # **`interrumpido` se DEDUCE, no se escribe.** Si el proceso murió no pudo
    # dejar constancia; un `corriendo` sin latido reciente es la única evidencia
    # que queda, y mostrarlo como «corriendo» para siempre sería mentir.
    if d["estado"] == "corriendo" and d["latido_at"]:
        edad = (datetime.now(UTC)
                - datetime.fromisoformat(d["latido_at"])).total_seconds()
        if edad > _LATIDO_MUERTO_S:
            d["estado"] = "interrumpido"
            d["error"] = (f"sin señales hace {edad / 60:.0f} min — probablemente se "
                          f"reinició la API. Lo diagnosticado hasta ahí queda.")
    d["ok"] = True
    d["resumen"] = _resumen(d["informe"] or [])
    d["texto"] = informe_texto(d)
    return d


def _resumen(informe: list[dict]) -> dict:
    """El agregado se DERIVA del detalle, nunca se persiste: un resumen guardado
    se contradice con sus propios casos en cuanto cambia la forma de agrupar."""
    por_estado: dict[str, int] = {}
    por_causa: dict[str, int] = {}
    for f in informe:
        por_estado[f.get("estado") or "?"] = por_estado.get(f.get("estado") or "?", 0) + 1
        if f.get("causa"):
            por_causa[f["causa"]] = por_causa.get(f["causa"], 0) + 1
    return {"por_estado": por_estado,
            "por_causa": dict(sorted(por_causa.items(), key=lambda kv: -kv[1])),
            "segundos": round(sum(float(f.get("segundos") or 0) for f in informe), 1)}


# ── EL INFORME, en texto plano ──────────────────────────────────────────────
#
# Es el punto del pedido: *«que quede para copiar y pegar así te paso las
# respuestas»*. Se arma en el BACKEND y no en el front porque el que lo lee (una
# persona o un modelo) tiene que ver exactamente lo mismo que la pantalla — dos
# renderizados del mismo informe se desincronizan al primer cambio.

_ORDEN_ESTADO = ["error", "no_pudo", "bloqueado", "sin_puerta", "listo", "cerrado"]

_QUE_ES = {
    "error": "EXPLOTARON (excepción — esto es un bug, no un bono mal cargado)",
    "no_pudo": "NO SE PUDIERON DIAGNOSTICAR (falta un insumo)",
    "bloqueado": "DIAGNOSTICADOS pero la cadena NO habilita aplicar",
    "sin_puerta": "SIN PUERTA (el agente los ve y todavía no sabe tocarlos)",
    "listo": "LISTOS PARA APLICAR",
    # Va ÚLTIMO en `_ORDEN_ESTADO` a propósito: es la única categoría donde no
    # queda nada por hacer, así que no puede competir por la atención con las
    # que sí piden algo.
    "cerrado": "CERRADOS: se comprobó que ya no aplican (salieron de la lista)",
}


def _sin_repetir(trabas: list[dict]) -> list[dict]:
    """Las trabas de UN caso, sin las que dicen lo mismo con otro título.

    **Medido (informe #4): el user pesa 26.525 chars para 16 casos** y buena
    parte es la misma frase tres veces — «Dónde está el problema, por división»,
    «La paridad, con la división a la vista» y «⇒ LA CONCLUSIÓN» repiten el mismo
    cálculo. Para una persona esa redundancia ayuda (cada lente se lee sola);
    para el modelo es ruido que compite por la ventana de contexto y esconde el
    patrón, que es justo lo único que se le pide.

    Se compara por el arranque del detalle y no por el título: los títulos son
    distintos a propósito, el contenido es el que se repite.
    """
    out, vistos = [], set()
    for t in trabas:
        # Se compara por el ARRANQUE del detalle, no por el título: los títulos
        # son distintos a propósito y el contenido es el que se repite. 80 chars
        # alcanzan — las tres versiones de la escala empiezan idénticas y recién
        # se separan en el paréntesis.
        clave = (str(t.get("detalle") or "")[:80]).strip().lower()
        if clave and clave in vistos:
            continue
        vistos.add(clave)
        out.append(t)
    return out


def informe_texto(run: dict, *, compacto: bool = False) -> str:
    """El bloque para copiar. Ordenado por lo que hay que mirar primero: los que
    explotaron arriba, los que están listos abajo.

    `compacto=True` es la versión para el LLM: saca las trabas repetidas y acorta
    los detalles. **Es el MISMO informe**, no un segundo formato — si divergieran,
    el análisis hablaría de un texto que el humano nunca vio.
    """
    inf = run.get("informe") or []
    res = _resumen(inf)
    L: list[str] = []
    L.append(f"AV AGENT — DIAGNÓSTICO MASIVO #{run.get('id')}")
    L.append(f"{run.get('creado_at') or ''} · estado: {run.get('estado')} · "
             f"{run.get('hechos')}/{run.get('total')} casos")
    if run.get("filtro"):
        L.append(f"filtro: {json.dumps(run['filtro'], ensure_ascii=False)}")
    L.append(f"modo: {'SIN RED (cero créditos)' if run.get('sin_red') else '1816 habilitado'}"
             + (f" · créditos usados: {run['creditos']}" if run.get("creditos") is not None
                else "")
             + f" · {res['segundos']}s de cálculo")
    if run.get("error"):
        L.append(f"⚠ {run['error']}")
    L.append("")

    L.append("── RESUMEN ─────────────────────────────────────────────")
    for e in _ORDEN_ESTADO:
        if res["por_estado"].get(e):
            L.append(f"  {res['por_estado'][e]:>3}  {_QUE_ES[e]}")
    for e, n in res["por_estado"].items():          # cualquier estado nuevo
        if e not in _QUE_ES:
            L.append(f"  {n:>3}  {e}")
    if res["por_causa"]:
        L.append("")
        L.append("  POR CAUSA (esto es lo que dice cuánto trabajo hay de verdad):")
        for c, n in res["por_causa"].items():
            L.append(f"    {n:>3}  {c}")
    L.append("")

    for e in _ORDEN_ESTADO:
        filas = [f for f in inf if f.get("estado") == e]
        if not filas:
            continue
        L.append(f"── {_QUE_ES[e]} ({len(filas)}) ──")
        for f in filas:
            L.append(f"  {f['sujeto']}  [{f.get('regla') or f.get('tipo') or ''}]")
            if f.get("causa"):
                L.append(f"      causa:   {f['causa']}")
            if f.get("veredicto"):
                L.append(f"      cadena:  {f['veredicto']}")
            if f.get("detalle"):
                L.append(f"      detalle: {f['detalle']}")
            # Ya vienen sin repetir y topeadas en 3 desde el origen, así que
            # acá no se vuelve a filtrar: dos criterios de recorte sobre la misma
            # lista terminan mostrando cosas distintas según por dónde se lea.
            for t in (f.get("trabas") or []):
                det = str(t["detalle"])
                L.append(f"      · [{t['estado']}] {t['paso']}: "
                         f"{det[:180] if compacto else det}")
        L.append("")
    return "\n".join(L)


def historial(limite: int = 15) -> list[dict]:
    """Las corridas anteriores, para poder comparar: *«esto mejoró, esto
    empeoró»* es la pregunta que un informe suelto no contesta."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, creado_at, estado, total, hechos, sin_red, creditos "
                    "FROM agente.av_agent_runs ORDER BY creado_at DESC LIMIT %s",
                    (max(1, min(50, limite)),))
        rows = cur.fetchall()
    return [{"id": r[0], "creado_at": r[1].isoformat() if r[1] else None,
             "estado": r[2], "total": r[3], "hechos": r[4], "sin_red": r[5],
             "creditos": r[6]} for r in rows]
