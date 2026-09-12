"""`agente/vista.py` — el READ MODEL. Doc: `AGENT.md` §6.

**Las pantallas LEEN. No derivan.** Clase, estado, arreglo y nombre vienen
resueltos de acá; ningún contador se suma en el navegador.

En el agente viejo el badge «AHORA 92» lo sumaba el browser juntando cuatro
cosas de DOS endpoints con frescuras distintas (uno se refrescaba cada 20 s, el
otro se cargaba una sola vez al abrir), contadas sobre listas ya cortadas en 200
filas, leídas de una tabla distinta de la que dibujaba LA LISTA. Nada de eso se
podía verificar del lado del servidor.

Acá cada pantalla es UNA query sobre UNA vista.
"""
from __future__ import annotations

import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)


def _filas(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _serializar(f: dict) -> dict:
    for k, v in list(f.items()):
        if hasattr(v, "isoformat"):
            f[k] = v.isoformat()
        elif isinstance(v, (int, float, str, bool, dict, list)) or v is None:
            pass
        else:
            f[k] = str(v)
    return f


# ── AHORA ──────────────────────────────────────────────────────────────────
def _con_historial(filas: list[dict]) -> list[dict]:
    """Le suma a cada hallazgo **cuántas veces apareció el mismo problema** en
    los últimos 30 días. Doc: `AGENT.md` §6.10.

    ⚠️⚠️ **ES LA PREGUNTA QUE DECIDE QUÉ HACER, y hasta hoy no se hacía.**

    El agente miraba cada hallazgo AISLADO, así que un job que no escribe se ve
    igual la primera vez que la trigésima — y las dos terminaban en «relanzá el
    job». Para la primera está bien. Para la trigésima, relanzar ES el parche:
    lo que hay que revisar es el umbral, el cron, o si el job sigue haciendo
    falta. El user lo dijo así: *«el agente debe poder buscar mejoras, no dejar
    todo como está y parchear»*.

    Un EPISODIO es una vez que el problema **nació**, no una vez que se lo vio:
    un problema que persiste no crea fila nueva (sube `veces`). Tres episodios
    son tres veces que apareció, se fue y volvió — que es justo lo que un
    incidente aislado NO hace.

    **UNA query para toda la lista**, no una por fila: AHORA puede traer
    cuarenta y cuarenta consultas para contestar lo mismo es cómo una pantalla
    se vuelve lenta sin que nadie sepa por qué. Va por el índice
    `hallazgos_problema (habilidad, sujeto, regla, detectado_at DESC)`, que ya
    existía.

    ⚠️ El trío viaja en TRES listas paralelas por `unnest`, no pegado en un
    string: un sujeto puede ser `mercado.market_snapshot` o `/api/x/{id}`, y
    cualquier separador es una apuesta a que no aparezca en los datos.

    Si la consulta falla, cada fila queda con `episodios = None` — **«no sé»,
    que no es lo mismo que «es la primera vez»**. Una pantalla que dice «1ª vez»
    porque no pudo contar es el invariante 1 disfrazado de dato.

    ⚠️ **NO TODA REGLA CUENTA EPISODIOS** (`AGENT.md` §6.10 y §0.ep). El
    número dice algo si el sujeto es UNA COSA FIJA (un job, un ticker); si es
    un GRUPO —«ONs HARD DÓLAR», «CARTERA», el peso de la base— la fila se llena
    y se vacía cada vez que el mundo crece, y contar eso mide el ritmo del
    negocio, no una falla. Cuáles son sale de `catalogo.sin_episodios()`, que
    lee la `naturaleza` declarada en el catálogo — acá no se decide nada.
    """
    from agente import catalogo, tipos

    if not filas:
        return filas
    inf = set(catalogo.sin_episodios())
    for f in filas:
        f["sin_episodios"] = (f["habilidad"], f["regla"]) in inf
    trios = {(f["habilidad"], f["sujeto"], f["regla"]) for f in filas
             if not f["sin_episodios"]}
    cuenta: dict[tuple, tuple] = {}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT d.hab, d.suj, d.reg, count(*)::int, min(h.detectado_at) "
                "  FROM unnest(%s::text[], %s::text[], %s::text[]) "
                "         AS d(hab, suj, reg) "
                "  JOIN agente.hallazgos h ON h.habilidad = d.hab "
                "   AND h.sujeto = d.suj AND h.regla = d.reg "
                " WHERE h.detectado_at > now() - make_interval(days => %s) "
                " GROUP BY d.hab, d.suj, d.reg",
                ([t[0] for t in trios], [t[1] for t in trios],
                 [t[2] for t in trios], tipos.VENTANA_CRONICO_D))
            cuenta = {(r[0], r[1], r[2]): (r[3], r[4]) for r in cur.fetchall()}
    except Exception as e:
        logger.warning("vista: no pude contar los episodios (%s) — las filas van "
                       "sin historial, que NO es «es la primera vez»", e)

    for f in filas:
        n, desde = cuenta.get((f["habilidad"], f["sujeto"], f["regla"]), (None, None))
        f["episodios"] = n
        f["episodios_desde"] = desde.isoformat() if desde is not None else None
        # `None` (no pude contar) NO es crónico: ante la duda, la rama que no
        # afirma nada.
        f["cronico"] = bool(n is not None and n >= tipos.EPISODIOS_CRONICO)
    return filas


def ahora() -> dict:
    """Lo de HOY, sin leer, sin resolver. **Un COUNT sobre tres condiciones.**

    El badge y la lista salen de la misma query, así que no pueden decir cosas
    distintas — que es lo que pasaba cuando el badge se sumaba en el navegador.

    AHORA **se vacía sola**: un hallazgo nace con la fecha del día en que se
    detectó, y al día siguiente sale aunque nadie lo haya leído. Funciona porque
    un problema que persiste NO crea fila nueva (sube `veces`), así que su
    `detectado_at` sigue siendo el del día que apareció.

    ⚠️⚠️ **EL TRABAJO RECURRENTE NO ES UNA NOVEDAD DEL DÍA** (§0.et). User, sobre
    `CONTRAPARTES NUEVAS`: *«esto no es para AHORA, es para ENCONTRÓ»*. Y tiene
    razón, con la misma lógica que ya usa el resto: AHORA contesta «¿qué pasó
    hoy?», y una COLA DE TRABAJO no pasó hoy — está desde siempre y baja cuando
    alguien la trabaja. Su lugar es ENCONTRÓ, que es la lista de lo abierto con
    botón.

    El criterio NO es una lista nueva: son las reglas ya declaradas
    `RECURRENTE` (§0.ep). Y no se puede perder nada por acá, porque una regla
    recurrente sin arreglo no existiría en ninguna de las dos pantallas — hay un
    test que lo exige.
    """
    from agente import catalogo

    recurrentes = set(catalogo.recurrentes())
    filas = _con_historial([_serializar(f) for f in
                            _filas("SELECT * FROM agente.v_ahora")
                            if (f["habilidad"], f["regla"]) not in recurrentes])
    return {"total": len(filas), "filas": filas}


def marcar_leidos(ids: list[int], *, por: str = "") -> dict:
    """«Ya me enteré». **Lo saca de AHORA y de NINGÚN otro lado.**

    LEÍDO ≠ RESUELTO: el hallazgo sigue abierto, sigue en ENCONTRÓ y sigue con
    su botón. Marcar leído baja el ruido del día, no cierra nada.

    Idempotente (`AND leido_at IS NULL`): marcar dos veces no pisa quién fue el
    primero.
    """
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return {"ok": True, "marcados": 0}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE agente.hallazgos SET leido_at = now(), leido_por = %s "
                    "WHERE id = ANY(%s) AND leido_at IS NULL", (por, ids))
        return {"ok": True, "marcados": cur.rowcount or 0}


def desmarcar_leidos(ids: list[int]) -> dict:
    """Reversible desde la misma pantalla: leer no es una decisión importante y
    no tiene por qué ser irreversible."""
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return {"ok": True, "vueltos": 0}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE agente.hallazgos SET leido_at = NULL, leido_por = '' "
                    "WHERE id = ANY(%s)", (ids,))
        return {"ok": True, "vueltos": cur.rowcount or 0}


# ── ENCONTRÓ ───────────────────────────────────────────────────────────────
def encontro() -> dict:
    """Lo abierto que TIENE ARREGLO.

    ⚠️ Un AVISO no entra acá. En el agente viejo `salud` declaraba una acción y
    caía en la lista de trabajo, pero su puerta era de solo lectura: lo único
    que ofrecía era «↻ chequear ahora». **Un aviso con forma de trabajo.**
    """
    from agente import arreglos as arr

    filas = _con_historial([_serializar(f) for f in
                            _filas("SELECT * FROM agente.v_encontro")])
    cat = {a["id"]: a for a in arr.catalogo()}
    for f in filas:
        a = cat.get(f["arreglo"]) or {}
        f["arreglo_titulo"] = a.get("titulo", "")
        f["arreglo_donde"] = a.get("donde", "")
        # ⚠️ **LAS DOS COSAS QUE DECIDEN EL BOTÓN, RESUELTAS ACÁ.**
        #   · `pide_datos` → el botón no escribe: ABRE EL LISTADO. Apretarlo sin
        #     un valor cargado devolvía «no se cargó ningún valor», así que era
        #     un botón que no podía funcionar nunca.
        #   · `repetible`  → el sujeto es una FAMILIA y volver a aplicarlo es lo
        #     normal (un título nuevo llega con la ficha vacía siempre).
        # Van resueltas del backend porque el navegador no deriva (invariante
        # 11): una lista de ids en el front se desincroniza de esta sin fallar.
        f["arreglo_pide_datos"] = bool(a.get("pide_datos"))
        f["arreglo_repetible"] = bool(a.get("repetible"))
    por_habilidad: dict[str, int] = {}
    for f in filas:
        por_habilidad[f["habilidad"]] = por_habilidad.get(f["habilidad"], 0) + 1
    return {"total": len(filas), "filas": filas, "por_habilidad": por_habilidad}


def ignorar(hallazgo_id: int, *, por: str = "", motivo: str = "",
            deshacer: bool = False) -> dict:
    """«No me interesa». Reversible, y **no es un arreglo**: esconde, no resuelve.

    ⚠️⚠️ **SILENCIA EL PROBLEMA, NO LA FILA.** Marcar el hallazgo como
    `ignorado` —lo único que hacía antes— no alcanza y por eso el botón no
    servía: `registro._ver` busca por el TRÍO entre los ABIERTOS, un ignorado no
    está abierto, y en la pasada siguiente nacía una fila nueva en `nuevo`. El
    índice único tampoco chocaba, porque excluye `ignorado`. El bono descartado
    volvía a las dos horas como si fuera la primera vez.

    Van las DOS cosas y en este orden:

      1. la fila se marca `ignorado`, para que salga de la pantalla AHORA;
      2. el TRÍO entra en `agente.silenciados`, para que no vuelva a nacer.

    El botón vive en AHORA y en ENCONTRÓ (AGENT.md §0.dr: los avisos sin arreglo
    son la mayoría de AHORA y no tenían forma de callarse).

    Sin (2) el botón miente; sin (1) el aviso sigue en la lista hasta la próxima
    corrida. **Permanente por defecto** (`hasta` NULL): el vencimiento se carga a
    mano en la base cuando alguien lo elige, no lo pone el código.
    """
    from agente import tipos
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT habilidad, sujeto, regla FROM agente.hallazgos "
                    " WHERE id = %s", (hallazgo_id,))
        if not (f := cur.fetchone()):
            return {"ok": False, "error": "ese hallazgo no existe"}
        trio = (f[0], f[1], f[2])

        if deshacer:
            cur.execute("DELETE FROM agente.silenciados "
                        " WHERE habilidad = %s AND sujeto = %s AND regla = %s", trio)
            cur.execute("UPDATE agente.hallazgos SET estado = %s, cerrado_por = '' "
                        " WHERE id = %s AND estado = %s",
                        (tipos.NUEVO, hallazgo_id, tipos.IGNORADO))
            return {"ok": True, "silenciado": False}

        cur.execute(
            "INSERT INTO agente.silenciados (habilidad, sujeto, regla, por, motivo) "
            "VALUES (%s,%s,%s,%s,%s) "
            # Re-silenciar lo ya silenciado no es un error: actualiza quién y por
            # qué, y **renueva el «para siempre»** limpiando un vencimiento que
            # hubiera quedado de antes.
            "ON CONFLICT (habilidad, sujeto, regla) DO UPDATE SET "
            "  por = EXCLUDED.por, motivo = EXCLUDED.motivo, "
            "  desde = now(), hasta = NULL",
            (*trio, por, motivo))
        cur.execute("UPDATE agente.hallazgos SET estado = %s, cerrado_por = %s "
                    " WHERE id = %s AND estado = ANY(%s)",
                    (tipos.IGNORADO, por, hallazgo_id, list(tipos.ABIERTOS)))
        return {"ok": True, "silenciado": True,
                "habilidad": trio[0], "sujeto": trio[1], "regla": trio[2]}


# ── DESCARTAR ONs POR TICKER ────────────────────────────────────────────────
def _hallazgo_on(hallazgo_id: int) -> dict | None:
    """El hallazgo de familia (id, habilidad, sujeto, regla, estado, evidencia).
    Separada de `no_interesan_ons` para poder probarla sin base."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, habilidad, sujeto, regla, estado, evidencia "
                    "  FROM agente.hallazgos WHERE id = %s", (hallazgo_id,))
        f = cur.fetchone()
    if not f:
        return None
    return {"id": f[0], "habilidad": f[1], "sujeto": f[2], "regla": f[3],
            "estado": f[4], "evidencia": dict(f[5] or {})}


def no_interesan_ons(hallazgo_id: int, tickers: list[str], *,
                      todas: bool = False, por: str = "") -> dict:
    """«No me interesan ESTAS», por ticker (§0.eh).

    A diferencia de `ignorar` —que apaga el aviso ENTERO para siempre— esto
    descarta ONs puntuales de la oferta de `on_faltante`: las descartadas dejan
    de contarse, el aviso desaparece solo cuando no queda ninguna sin descartar
    (cierre por ausencia, lo hace `registro`) y renace únicamente con las que
    1816 publique de acá en más.

    Escribe `mercado.ons_ignoradas` por `ons.ignorar_concil`. ⚠️ Con el borrado
    del tab BONOS se fue el panel de ONs, que era la otra mitad: hoy el agente es
    el ÚNICO que descarta, y **no hay pantalla para restaurar** — des-descartar es
    un DELETE a mano sobre esa tabla. Lo que NO cambió es que la lista sigue
    siendo una sola (REGLA #9: no dos copias de «ONs que la mesa no sigue» sin
    árbitro).
    """
    from agente import fuentes, libro, motor, tipos
    from api.services import ons

    h = _hallazgo_on(hallazgo_id)
    if h is None:
        return {"ok": False, "error": "ese hallazgo no existe"}
    if h["habilidad"] != "on_faltante" or h["regla"] != "no_estan_en_curvas":
        return {"ok": False, "error": "esto solo aplica a la oferta de ONs"}
    if h["estado"] not in tipos.ABIERTOS:
        return {"ok": False, "error": f"ese hallazgo está «{h['estado']}»"}

    permitidos = {str(x.get("ticker") or "").strip().upper()
                  for x in h["evidencia"].get("_items") or []} - {""}
    # ⚠️ NO se escribe lo que manda el navegador: solo lo que el propio
    # detector ofreció en esta fila.
    elegidos = permitidos if todas else ({t.strip().upper() for t in tickers}
                                         & permitidos)
    if not elegidos:
        return {"ok": False, "error": "no elegiste ninguna ON de la lista"}

    escritas, errores = [], []
    for tk in sorted(elegidos):
        try:
            ons.ignorar_concil(tk, actor=por)
            escritas.append(tk)
        except Exception as e:
            errores.append(f"{tk}: {e}"[:160])

    libro.registrar(
        accion="no_interesa_on", objetivo=h["sujeto"], habilidad=h["habilidad"],
        regla=h["regla"], hallazgo_id=hallazgo_id, por=por,
        destino="mercado.ons_ignoradas", campo="ONs descartadas",
        antes=str(len(permitidos)),
        despues=f"{len(permitidos) - len(escritas)} (descartadas {len(escritas)})",
        ok=bool(escritas), error="; ".join(errores)[:300])

    try:
        fuentes.refrescar()
        motor.correr_una("on_faltante")
    except Exception:
        logger.warning("vista: no_interesan_ons descartó, pero no pude "
                       "refrescar on_faltante", exc_info=True)

    return {"ok": True, "descartadas": sorted(escritas),
            "quedan": len(permitidos) - len(escritas),
            "detalle": (f"{len(escritas)} descartada(s) · quedan "
                        f"{len(permitidos) - len(escritas)} · el aviso vuelve "
                        "solo con las nuevas"),
            "errores": errores}


# ── DESCARTAR CUENTAS QUE NO SON CONTRAPARTE ────────────────────────────────
def no_interesan_contrapartes(hallazgo_id: int, cuentas: list[str], *,
                              todas: bool = False, por: str = "") -> dict:
    """«Esta no es contraparte», por cuenta — el gemelo de `no_interesan_ons`
    para la oferta de `contraparte_faltante` (§0.es, misma forma).

    ⚠️⚠️ **ES LA PIEZA QUE HACE QUE LA LISTA PUEDA LLEGAR A CERO.** Sin un lugar
    donde anotar el NO, una cuenta institucional que la mesa ya miró y descartó
    vuelve a ofrecerse para siempre, y una lista que no se vacía no la mira
    nadie.

    ⚠️ Escribe `clientes.contrapartes_descartadas`, **una tabla aparte y no una
    fila vacía en `contrapartes`**: `jobs/_aum_filters` regla 3 excluye del AuM
    por la SOLA PRESENCIA del `id_cuenta` ahí, así que anotar el NO en esa tabla
    le sacaría del AuM la plata de un cliente real, callado.
    """
    from datetime import UTC, datetime

    from agente import libro, motor, tipos

    h = _hallazgo_on(hallazgo_id)
    if h is None:
        return {"ok": False, "error": "ese hallazgo no existe"}
    if h["habilidad"] != "contraparte_faltante" or h["regla"] != "sin_contraparte":
        return {"ok": False, "error": "esto solo aplica a la oferta de contrapartes"}
    if h["estado"] not in tipos.ABIERTOS:
        return {"ok": False, "error": f"ese hallazgo está «{h['estado']}»"}

    # ⚠️ NO se escribe lo que manda el navegador: solo lo que el propio detector
    # ofreció en ESTA fila. Sin esto, la puerta silenciaría cualquier cuenta del
    # padrón — y una cuenta silenciada no vuelve a ofrecerse nunca.
    permitidas = {str(x.get("cuenta") or "").strip()
                  for x in h["evidencia"].get("_items") or []} - {""}
    elegidas = permitidas if todas else ({str(c).strip() for c in cuentas}
                                         & permitidas)
    if not elegidas:
        return {"ok": False, "error": "no elegiste ninguna cuenta de la lista"}

    escritas, errores = [], []
    with get_pool().connection() as conn, conn.cursor() as cur:
        for c in sorted(elegidas):
            try:
                cur.execute(
                    "INSERT INTO clientes.contrapartes_descartadas "
                    "  (id_cuenta, motivo, por, at) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (id_cuenta) DO NOTHING",
                    (c, "no es contraparte", por, datetime.now(UTC)))
                escritas.append(c)
            except Exception as e:
                errores.append(f"{c}: {e}"[:160])
        conn.commit()

    libro.registrar(
        accion="no_es_contraparte", objetivo=h["sujeto"], habilidad=h["habilidad"],
        regla=h["regla"], hallazgo_id=hallazgo_id, por=por,
        destino="clientes.contrapartes_descartadas", campo="cuentas descartadas",
        antes=str(len(permitidas)),
        despues=f"{len(permitidas) - len(escritas)} (descartadas {len(escritas)})",
        ok=bool(escritas), error="; ".join(errores)[:300])

    try:
        motor.correr_una("contraparte_faltante")
    except Exception:
        logger.warning("vista: no_interesan_contrapartes descartó, pero no pude "
                       "refrescar contraparte_faltante", exc_info=True)

    return {"ok": True, "descartadas": sorted(escritas),
            "quedan": len(permitidas) - len(escritas),
            "detalle": (f"{len(escritas)} descartada(s) · quedan "
                        f"{len(permitidas) - len(escritas)} · el aviso vuelve "
                        "solo con las cuentas nuevas"),
            "errores": errores}


# ── DESCARTAR CEDEARs POR TICKER ─────────────────────────────────────────────
def no_interesan_cedears(hallazgo_id: int, tickers: list[str], *,
                         todas: bool = False, por: str = "") -> dict:
    """«No me interesan ESTOS», por ticker — el gemelo de `no_interesan_ons`
    para la oferta de `cedear_faltante` (§0.eh, misma forma).

    A diferencia de `ignorar` —que apaga el aviso ENTERO para siempre— esto
    descarta CEDEARs puntuales de la oferta: los descartados dejan de
    contarse, el aviso desaparece solo cuando no queda ninguno sin descartar
    (cierre por ausencia, lo hace `registro`) y renace únicamente con los que
    Primary liste de acá en más.

    ⚠️ **LOS PERMITIDOS SE RECALCULAN EN VIVO**, igual que `AltaCedear.preview`:
    la evidencia del hallazgo trae solo una `muestra`, así que lo que se puede
    escribir es lo que `alta_cedear.candidatos` ofrece AHORA sobre la foto de
    Primary y el master actuales, no lo que decía la foto cuando se abrió la
    pantalla.

    El descarte va a `mercado.cedears` (`core.cedears_sql.descartar`), la
    MISMA tabla del alta (REGLA #9 B): no hay una segunda lista de «CEDEARs
    que la mesa no sigue». Se restauran desde Manager → RENTA VARIABLE.
    """
    from agente import alta_cedear, fuentes, libro, motor, tipos

    h = _hallazgo_on(hallazgo_id)
    if h is None:
        return {"ok": False, "error": "ese hallazgo no existe"}
    if h["habilidad"] != "cedear_faltante" or h["regla"] != "no_esta_en_master":
        return {"ok": False, "error": "esto solo aplica a la oferta de CEDEARs"}
    if h["estado"] not in tipos.ABIERTOS:
        return {"ok": False, "error": f"ese hallazgo está «{h['estado']}»"}

    master = fuentes.cedears_master()
    fichas = fuentes.fichas_primary()
    if master is None or fichas is None:
        return {"ok": False, "error": "no pude leer el master o la foto de Primary "
                                       "— sin eso no puedo recalcular la oferta"}
    c = alta_cedear.candidatos(master, fichas, min_propios=alta_cedear.MIN_PROPIOS)
    permitidos = {f["unidad"]: f for f in c["filas"]}
    # ⚠️ NO se escribe lo que manda el navegador: solo lo que la oferta EN VIVO
    # todavía ofrece.
    elegidos = (set(permitidos) if todas else
               ({t.strip().upper() for t in tickers} & set(permitidos)))
    if not elegidos:
        return {"ok": False, "error": "no elegiste ningún CEDEAR de la lista"}

    from core import cedears_sql

    escritas, errores = [], []
    for tk in sorted(elegidos):
        fila = permitidos[tk]
        try:
            cedears_sql.descartar(tk, simbolo=fila["simbolo"],
                                  underlying=fila.get("subyacente_primary") or tk,
                                  actor=por)
            escritas.append(tk)
        except Exception as e:
            errores.append(f"{tk}: {e}"[:160])

    libro.registrar(
        accion="no_interesa_cedear", objetivo=h["sujeto"], habilidad=h["habilidad"],
        regla=h["regla"], hallazgo_id=hallazgo_id, por=por,
        destino="mercado.cedears", campo="CEDEARs descartados",
        antes=str(len(permitidos)),
        despues=f"{len(permitidos) - len(escritas)} (descartados {len(escritas)})",
        ok=bool(escritas), error="; ".join(errores)[:300])

    try:
        fuentes.refrescar()
        motor.correr_una("cedear_faltante")
    except Exception:
        logger.warning("vista: no_interesan_cedears descartó, pero no pude "
                       "refrescar cedear_faltante", exc_info=True)

    return {"ok": True, "descartados": sorted(escritas),
            "quedan": len(permitidos) - len(escritas),
            "detalle": (f"{len(escritas)} descartado(s) · quedan "
                        f"{len(permitidos) - len(escritas)} · el aviso vuelve "
                        "solo con los nuevos"),
            "errores": errores}


# ── HISTORIAL ──────────────────────────────────────────────────────────────
LIMITE = 200


def historial(*, limite: int = LIMITE, desde_id: int | None = None,
              q: str = "", solo_malas: bool = False) -> dict:
    """El LIBRO, paginado **del backend**.

    En el agente viejo esto se armaba en el navegador juntando tres fuentes con
    topes distintos (100 acciones, 40 respuestas, 80 votos): cuando el más chico
    se agotaba, la línea de tiempo perdía un tipo de evento y no los otros, sin
    decirlo. Un día aparecía como que «solo tuvo acciones».

    Una fuente, un tope, declarado — y el filtro también es de acá: un buscador
    que solo mira lo que ya bajó no es un buscador.
    """
    limite = max(1, min(int(limite or LIMITE), 500))
    where, params = ["true"], []
    if desde_id:
        where.append("a.id < %s")
        params.append(int(desde_id))
    if solo_malas:
        where.append("a.ok = false")
    if (t := (q or "").strip()):
        where.append("(a.sujeto ILIKE %s OR a.arreglo ILIKE %s OR a.regla ILIKE %s "
                     " OR a.campo ILIKE %s OR a.donde ILIKE %s OR a.por ILIKE %s)")
        params += [f"%{t}%"] * 6

    # ⚠️⚠️ **EL ESTADO DEL HALLAZGO NO SIEMPRE HABLA DE ESTA LÍNEA.**
    #
    # User (2026-08-28), viendo nueve títulos que acababa de completar, cada uno
    # con su ✔ y su `emisor: — → OTROS`, y al lado «el aviso sigue abierto»:
    # *«¿por qué no pone CONFIRMADO? ¿qué tienen que ver los demás?»*.
    #
    # Y no tienen nada que ver. Cuando la acción es sobre UN título y el
    # hallazgo es de TODO el campo, el estado del hallazgo habla de los OTROS
    # —los que faltan— y en ese renglón se lee como una duda sobre la escritura
    # que la propia línea ya está afirmando.
    #
    # Se distingue con el dato, no con una lista: **si el sujeto de la acción es
    # distinto del sujeto del hallazgo, la acción es de un item y el hallazgo de
    # un grupo.** Ahí la línea contesta por sí sola y `estado_hoy` viaja vacío.
    filas = _filas(
        "SELECT a.*, h.severidad, "
        "       CASE WHEN h.id IS NULL OR h.sujeto IS DISTINCT FROM a.sujeto "
        "            THEN NULL ELSE h.estado END AS estado_hoy "
        "  FROM agente.acciones a "
        "  LEFT JOIN agente.hallazgos h ON h.id = a.hallazgo_id "
        f" WHERE {' AND '.join(where)} "
        " ORDER BY a.id DESC LIMIT %s", (*params, limite + 1))
    hay_mas = len(filas) > limite
    filas = [_serializar(f) for f in filas[:limite]]
    from agente import tipos
    # La pantalla no deriva (invariante 11): «lo hizo solo» viaja resuelto.
    for f in filas:
        f["automatico"] = (f.get("por") == tipos.ACTOR_AGENTE)
    return {"filas": filas, "hay_mas": hay_mas,
            "ultimo_id": filas[-1]["id"] if filas else None,
            "limite": limite}


# ── LO CRÓNICO ─────────────────────────────────────────────────────────────
def cronicos(limite: int = 60) -> dict:
    """**Lo que pasa SIEMPRE — donde están las mejoras.** Doc: `AGENT.md` §6.10.

    Un problema que aparece treinta veces en un mes no es un incidente: es una
    configuración mal puesta, y arreglarlo cada vez lo TAPA. Esta lista es la
    única forma de verlo, porque mirando un hallazgo por vez los dos casos se
    ven idénticos.

    ⚠️ **LA CONSULTA VIVE ACÁ Y EN NINGÚN OTRO LADO.** `scripts/diag_agente` la
    LEE de esta función en vez de repetirla: dos definiciones de «crónico» —una
    para la pantalla y otra para la terminal— darían números distintos sobre el
    mismo problema sin que ninguna falle (REGLA #9). Ya pasó con el conteo de
    reincidencias, en tres lugares.

    Devuelve `activos` (pasó en los últimos `DIAS_ACTIVO`) y `historicos`
    aparte. **No es cosmético**: medido la primera vez que se listó, once de
    veinticinco ya no pasaban y competían por atención con los que rompen hoy.

    `mediana_s` es la MEDIANA de cuánto duró cada episodio, no el promedio: uno
    de cuatro horas entre cuarenta de tres minutos mueve el promedio a doce y
    cuenta una historia que no pasó.

    ⚠️ Y la duración mide **cuánto vivió el HALLAZGO**, no cuánto estuvo roto el
    mundo: tiene un piso puesto por la ventana del propio detector. En
    `proveedor_caido` (ventana de 20') una mediana de 20' significa «casi todos
    fueron un solo fallo» — que es justo lo que se quiere saber —, pero no se
    lee como «estuvo caído 20 minutos».
    """
    from agente import catalogo, tipos

    # ⚠️ Lo que NO cuenta episodios queda afuera del ranking entero (§0.ep):
    # un informe («pasa siempre» es su definición) y una regla cuyo sujeto es
    # una FAMILIA que se llena y se vacía. Se excluye por el mismo patrón de
    # listas paralelas que usa `_con_historial` — habilidad+regla nunca se pega
    # en un string (hay un test que lo prohíbe).
    inf = catalogo.sin_episodios()
    habs_inf = [h for h, _ in inf]
    regs_inf = [r for _, r in inf]

    sql = (
        "SELECT h.habilidad, h.sujeto, h.regla, count(*)::int AS episodios, "
        "       (percentile_cont(0.5) WITHIN GROUP ("
        "          ORDER BY extract(epoch FROM "
        "                   coalesce(h.cerrado_at, now()) - h.detectado_at)))::int "
        "          AS mediana_s, "
        "       max(extract(epoch FROM "
        "           coalesce(h.cerrado_at, now()) - h.detectado_at))::int AS peor_s, "
        "       min(h.detectado_at) AS desde, max(h.detectado_at) AS ultima, "
        "       count(*) FILTER (WHERE h.estado = ANY(%s))::int AS abiertos, "
        "       max(h.detectado_at) > now() - make_interval(days => %s) AS activo "
        "  FROM agente.hallazgos h "
        " WHERE h.detectado_at > now() - make_interval(days => %s) "
        "   AND NOT EXISTS (SELECT 1 FROM unnest(%s::text[], %s::text[]) AS i(hab, reg) "
        "                    WHERE i.hab = h.habilidad AND i.reg = h.regla) "
        " GROUP BY h.habilidad, h.sujeto, h.regla "
        "HAVING count(*) >= %s "
        " ORDER BY 10 DESC, 4 DESC LIMIT %s")
    filas = [_serializar(f) for f in _filas(
        sql, (list(tipos.ABIERTOS), tipos.DIAS_ACTIVO, tipos.VENTANA_CRONICO_D,
              habs_inf, regs_inf, tipos.EPISODIOS_CRONICO, int(limite)))]
    activos = [f for f in filas if f.get("activo")]
    return {
        "activos": activos,
        "historicos": [f for f in filas if not f.get("activo")],
        # El badge de la tab cuenta lo que SIGUE pasando: lo que ya se cortó no
        # es trabajo. Y sale de acá, no del navegador (invariante 11).
        "total": len(activos),
        "ventana_dias": tipos.VENTANA_CRONICO_D,
        "dias_activo": tipos.DIAS_ACTIVO,
        "desde_episodios": tipos.EPISODIOS_CRONICO,
    }


# ── LAS REINCIDENCIAS ──────────────────────────────────────────────────────
def reincidencias(limite: int = 100) -> dict:
    """**La que debe estar VACÍA.** Si tiene filas, algo que dimos por arreglado
    se rompió de nuevo. No es una lista de trabajo: es una alarma.

    ⚠️⚠️ **UNA REINCIDENCIA ESTÁ ACTIVA MIENTRAS SU HALLAZGO SIGA ABIERTO.**
    `agente.reincidencias` es una tabla de EVENTOS: sólo se le hace `INSERT` y
    no existe una línea que cierre una fila. Leerla entera hacía que la alarma
    **no pudiera volver a cero nunca**: M31G6 volvió el 28/08, el detector se
    corrigió ese mismo día, el hallazgo se cerró — y el cartel rojo siguió
    arriba de la pantalla una semana, describiendo un bono que ya venció.

    Eso es exactamente el defecto que el propio agente evita en el círculo del
    latido: *«un círculo que está en rojo cuando todo está bien enseña a ignorar
    el círculo»*. Una alarma que no puede apagarse deja de ser una alarma, y la
    fila número 16 —la que importaba— no la mira nadie.

    La fila **no se borra jamás**: «`alta_bono` aguantó 3,6 días sobre M31G6» es
    un hecho, y es la evidencia con la que después se decide qué arreglo es
    confiable. Sólo deja de contar como alarma. `historicas` dice cuántas hay
    apagadas, para que «desapareció» no se confunda con «lo borraron».
    """
    from agente import tipos

    filas = [_serializar(f) for f in _filas(
        "SELECT r.* FROM agente.reincidencias r "
        "  JOIN agente.hallazgos h ON h.id = r.hallazgo_id "
        " WHERE h.estado = ANY(%s) "
        " ORDER BY r.volvio_at DESC LIMIT %s",
        (list(tipos.ABIERTOS), int(limite)))]
    apagadas = _filas(
        "SELECT count(*) AS n FROM agente.reincidencias r "
        "  JOIN agente.hallazgos h ON h.id = r.hallazgo_id "
        " WHERE NOT (h.estado = ANY(%s))", (list(tipos.ABIERTOS),))
    return {"total": len(filas), "filas": filas,
            "historicas": int(apagadas[0]["n"]) if apagadas else 0}


# ── LO QUE EL AGENTE APLICÓ SOLO ───────────────────────────────────────────
def solo() -> dict:
    """Cuánto aplicó SOLO el agente hoy (`agente/autonomo.py`).

    Si la consulta falla: `{"hoy": None, ...}` — «no pude contar» no es cero
    (mismo criterio que `_con_historial`).
    """
    from agente import tipos
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FILTER (WHERE ok), "
                "       count(*) FILTER (WHERE NOT ok), max(at) "
                "  FROM agente.acciones "
                " WHERE por = %s AND at >= date_trunc('day', now())",
                (tipos.ACTOR_AGENTE,))
            hoy, fallidas_hoy, ultima_at = cur.fetchone()
        return {"hoy": int(hoy or 0), "fallidas_hoy": int(fallidas_hoy or 0),
                "ultima_at": ultima_at.isoformat() if ultima_at else None}
    except Exception as e:
        logger.warning("vista: no pude contar lo que el agente aplicó solo (%s)", e)
        return {"hoy": None, "fallidas_hoy": None, "ultima_at": None}


# ── TODO EN UN REQUEST ─────────────────────────────────────────────────────
def vista() -> dict:
    """Todo lo que el modal necesita. **UN request.**

    Y con UNA sola noción de «ahora»: el agente viejo mezclaba un endpoint que
    se refrescaba cada 20 s con otro que se cargaba al abrir, y la pantalla
    sumaba los dos números como si hablaran del mismo instante.
    """
    from agente import catalogo, motor
    return {
        "ok": True,
        "latido": motor.vivo(),
        "ahora": ahora(),
        "encontro": encontro(),
        "reincidencias": reincidencias(20),
        # Lo que el agente aplicó SOLO hoy, contado del lado del backend.
        "solo": solo(),
        # Va en el MISMO request que el resto: el modal se dibuja con UNA sola
        # noción de «ahora». Dos endpoints con frescuras distintas fue cómo el
        # agente viejo llegó a sumar números que hablaban de instantes distintos.
        "cronicos": cronicos(60),
        "habilidades": [_serializar(h) for h in catalogo.estado()],
    }
