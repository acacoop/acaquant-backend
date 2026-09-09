"""Detectores de DATOS y SEGURIDAD. Doc: `docs/AGENT.md` §5."""
from __future__ import annotations

import logging

from agente import reloj
from agente.tipos import Hallazgo, SinDatos

logger = logging.getLogger(__name__)


# ═══ dato_partido ══════════════════════════════════════════════════════════
def dato_partido(u: dict) -> list[Hallazgo]:
    """Dos copias del mismo dato que dejaron de decir lo mismo.

    Es el detector de una CLASE de bug, no de un caso. Lo que lo hace difícil de
    ver a mano: **cuando dos copias se separan no falla nada**. Cada mitad sigue
    internamente coherente, no hay excepción, no hay log, y el sistema contesta
    con seguridad usando la copia equivocada.

    De los mejores del lote: **no necesita mercado abierto ni precio**, así que
    es cierto un domingo y con la API caída.
    """
    from core import duplicados
    try:
        res = duplicados.divergencias()
    except Exception as e:
        raise SinDatos(f"no pude comparar los duplicados: {e}") from e

    out = []
    for d in res.get("partidos") or []:
        ej = d.get("ejemplos") or []
        muestra = "; ".join(f"{x['sujeto']}: «{x['valor_a']}» ≠ «{x['valor_b']}»"
                            for x in ej[:3])
        # ⚠️ **DOS REGLAS, porque solo una tiene botón (§0.dc).** El duplicado
        # que declara `arreglo_sql` se arbitra desde acá (`arbitrar_copia`);
        # el que declara `arreglo_manual` es un AVISO con la instrucción — un
        # botón que siempre contesta «esto se hace a mano» es un aviso con
        # forma de trabajo.
        con_sql = bool(d.get("tiene_sql"))
        out.append(Hallazgo(
            # `alta` sin dudar: acá no hay «es contexto». Si dos copias
            # difieren, ALGO está leyendo el valor incorrecto ahora mismo — lo
            # único que no sabemos es quién.
            sujeto=str(d["id"]),
            regla="copias_que_no_coinciden" if con_sql else "copias_a_mano",
            severidad="alta",
            nombre=str(d.get("que") or d["id"]),
            problema=f"{d['n']} caso(s) donde {d['que']} dice cosas distintas "
                     f"según dónde se lea: {d['a']} vs {d['b']}."
                     + (f" Ejemplos — {muestra}." if muestra else ""),
            detalle=muestra,
            que_hacer=(f"Manda {d['arbitro']}. Aplicar escribe la copia que pierde "
                       f"con el valor de la que manda, fila por fila. Qué se rompe "
                       f"si no: {d['rompe']}." if con_sql else
                       f"Manda {d['arbitro']}. No se arregla con un UPDATE: "
                       f"{d.get('arreglo_manual') or 'ver el árbitro'}. Qué se "
                       f"rompe si no: {d['rompe']}."),
            evidencia={"n": d["n"], "a": d["a"], "b": d["b"],
                       "arbitro": d["arbitro"], "ejemplos": ej,
                       "tiene_sql": con_sql}))
    # **Lo que no se pudo mirar se canta.** Un duplicado sin chequear se leería
    # igual que uno sano, que es la forma de mentir que este detector persigue.
    for x in res.get("sin_mirar") or []:
        out.append(Hallazgo(
            sujeto=str(x["id"]), regla="no_pude_chequear", severidad="media",
            nombre=str(x.get("que") or x["id"]),
            problema=f"No pude verificar si {x['que']} sigue coincidiendo en sus "
                     f"dos lugares: {x['error']}. **No es que esté bien — es que "
                     f"no se miró.**",
            detalle=str(x["error"]),
            que_hacer="Arreglar la lectura del duplicado para volver a cubrirlo.",
            evidencia={"error": x["error"]}))
    return out


# ═══ permiso_flojo ═════════════════════════════════════════════════════════
def permiso_flojo(u: dict) -> list[Hallazgo]:
    """Endpoints sin gate — y, probando de verdad, los que contestan igual.

    Son DOS mitades con costos distintos: **leer el árbol de rutas es gratis** y
    corre siempre; **probar el borde hace tráfico real contra producción** y por
    eso corre pocas veces por día.

    Con MEMORIA de la foto de ayer, que es lo que permite detectar el endpoint
    que **PERDIÓ** el gate que tenía — algo que un conteo no ve, porque el total
    de abiertos puede no moverse y aun así haber un agujero nuevo.
    """
    from agente import seguridad

    try:
        d = seguridad.declarado()
    except Exception as e:
        raise SinDatos(f"no pude leer la superficie HTTP: {e}") from e

    # ⚠️⚠️ **LA FOTO LA SACA ESTA HABILIDAD, Y ANTES DE COMPARAR.**
    #
    # `comparar()` lee las DOS últimas fechas de `manager.superficie_dia`. Si
    # nadie escribe esa tabla, compara dos fotos viejas **para siempre**: sigue
    # contestando `ok`, sigue sin reportar nada, y un endpoint que pierde el gate
    # mañana no lo ve nunca. Es la falla que no falla.
    #
    # Y eso es exactamente lo que pasó: la foto la sacaba `jobs/db_tamano.py`,
    # que se borró al rehacer el agente (2026-08-24) sin que nadie tomara su
    # lugar. Desde entonces esta habilidad venía comparando el 23 contra el 24 de
    # agosto, en verde.
    #
    # **La memoria es de quien la usa.** Sacarla acá cuesta cero red (el árbol de
    # rutas ya está en proceso) y es idempotente por día: la clave es
    # fecha+path+métodos, así que re-sacarla pisa la del día en vez de duplicar.
    try:
        seguridad.sacar_foto()
    except Exception as e:
        # No es `SinDatos`: sin foto de HOY igual se puede reportar lo que la
        # lectura del árbol ya vio. Lo que se pierde es el delta, no todo.
        logger.warning("agente/permiso_flojo: no pude sacar la foto de hoy (%s)", e)

    cambios = {}
    try:
        c = seguridad.comparar()
        if c.get("ok") and not c.get("primera"):
            cambios = c
    except Exception:
        pass                              # sin foto de ayer se dice menos, no mal

    out = []
    nuevos_hoy = {n["path"] for n in cambios.get("nuevos") or []}
    for f in cambios.get("perdieron_gate") or []:
        out.append(Hallazgo(
            sujeto=f["path"], regla="perdio_el_gate", severidad="alta",
            problema=f"AYER pedía {f['gates_ayer'] or '—'} y HOY no pide nada "
                     f"({f['metodos']})"
                     + (" **Y ADEMÁS ESCRIBE.**" if f.get("escribe") else ""),
            que_hacer="Devolverle el gate en el router. Esto una foto no lo ve: "
                      "el total de abiertos puede no moverse igual.",
            evidencia={"capa": "declarado", "gates_ayer": f.get("gates_ayer"),
                       "metodos": f.get("metodos"), "escribe": f.get("escribe")}))
    for path in d["abiertas_inesperadas"]:
        nuevo = path in nuevos_hoy
        out.append(Hallazgo(
            sujeto=path, regla="sin_gate", severidad="alta",
            problema=("endpoint NUEVO y sin ningún gate" if nuevo else
                      "endpoint sin NINGÚN gate y no declarado como abierto")
                     + f" · {reloj.hhmm()}",
            que_hacer="Si es a propósito, declararlo en `ABIERTOS_OK` con el "
                      "motivo; si el candado vive en Cloudflare, en "
                      "`PROTEGIDOS_EN_EL_BORDE`; si no, le falta el gate.",
            evidencia={"capa": "declarado",
                       "desde": ("hoy" if nuevo else
                                 "ya estaba" if cambios else "sin foto previa")}))

    p = seguridad.probar()
    if not p.get("ok"):
        # **El silencio no es un verde.** Si la mitad efectiva no corrió hay que
        # decirlo: lo declarado no puede afirmar nada sobre el borde, y el
        # incidente que originó esto fue un borde abierto con el código bien.
        out.append(Hallazgo(
            sujeto="(la prueba activa)", regla="prueba_no_corrio", severidad="media",
            problema=f"solo se verificó lo DECLARADO: el borde no se probó "
                     f"({p.get('motivo')})",
            que_hacer="Configurar `AV_AGENT_URL_PUBLICA` para que el agente "
                      "pruebe el borde sin credenciales.",
            evidencia={"motivo": p.get("motivo")}))
    else:
        for r in p.get("filtran") or []:
            out.append(Hallazgo(
                sujeto=str(r.get("path") or r), regla="contesta_sin_credencial",
                severidad="alta",
                problema="probado SIN credenciales contra la URL pública y "
                         "contestó igual: el borde no aplica lo que el código "
                         "declara",
                que_hacer="Cerrarlo en el router o en Cloudflare. Esto no es "
                          "teoría: se midió contra producción.",
                evidencia={"capa": "efectivo", "detalle": r}))
    return out


# ═══ job_reporto ═══════════════════════════════════════════════════════════
def _ultima_corrida(job: str) -> dict | None:
    """`{finished_at, stats}` de la última corrida de ese job, o `None`."""
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT finished_at, data FROM manager.job_runs WHERE tipo = %s "
                    "ORDER BY finished_at DESC NULLS LAST LIMIT 1", (job,))
        r = cur.fetchone()
    if not r:
        return None
    data = dict(r[1] or {})
    return {"finished_at": r[0], "stats": dict(data.get("stats") or {}),
            "status": data.get("status")}


def job_reporto(u: dict) -> list[Hallazgo]:
    """Lo que un job REPORTÓ sin escribir, convertido en aviso con su lista.

    Lee la última corrida de cada job de `agente/reportes.REPORTES` y, si el
    contador declarado es mayor que cero, canta un hallazgo con la lista que el
    job dejó al lado (`<stat>_lista`). Un job que todavía corre código sin la
    lista sale igual, con el número, y lo dice. Ver §0.dd.

    No juzga si el job corrió cuando debía: eso es de `salud`. Acá solo importa
    lo que la última corrida dijo.
    """
    from agente import reloj
    from agente.reportes import REPORTES

    out = []
    corridas: dict[str, dict | None] = {}
    fallas = 0
    for r in REPORTES:
        if r.job not in corridas:
            try:
                corridas[r.job] = _ultima_corrida(r.job)
            except Exception as e:
                fallas += 1
                logger.warning("job_reporto: no pude leer %s (%s)", r.job, e)
                corridas[r.job] = None
        c = corridas[r.job]
        if not c:
            continue
        n = c["stats"].get(r.stat)
        if not isinstance(n, int | float) or n <= 0:
            continue
        lista = c["stats"].get(r.lista)
        tiene_lista = isinstance(lista, list)
        lista = [str(x) for x in (lista or [])][:200]
        cuando = c["finished_at"]
        cuando_txt = cuando.strftime("%d/%m %H:%M") if cuando else "?"
        out.append(Hallazgo(
            sujeto=f"{r.job}·{r.stat}", regla=r.nombre_regla, severidad=r.severidad,
            nombre=f"{r.job}: {r.stat}",
            problema=f"{int(n)} {r.que} · corrida del {cuando_txt} · {reloj.hhmm()}",
            detalle=(" · ".join(lista[:30]) + (f" · y {len(lista) - 30} más"
                                                if len(lista) > 30 else "")
                     if tiene_lista else
                     ("el job solo guarda el número, no la lista" if not r.con_lista else
                      "la lista aparece con la próxima corrida (el job corre código "
                      "que todavía no la guarda)")),
            que_hacer=r.que_hacer,
            evidencia={"job": r.job, "stat": r.stat, "n": n, "lista": lista,
                       "corrida_at": cuando.isoformat() if cuando else None,
                       "status": c.get("status")}))
    if fallas and fallas == len({r.job for r in REPORTES}):
        raise SinDatos("no pude leer manager.job_runs: no sé qué reportaron los jobs")
    return out


# ═══ trajo_poco ════════════════════════════════════════════════════════════
def _valor(corrida: dict, stat: str):
    v = (corrida.get("stats") or {}).get(stat)
    return v if isinstance(v, int | float) and not isinstance(v, bool) else None


def _saltear(corrida: dict) -> bool:
    """Una corrida en seco o parcial a mano (`modo`/`dry` en sus stats) no es
    una medida de cuánto trajo el proveedor."""
    st = corrida.get("stats") or {}
    return bool(st.get("modo")) or bool(st.get("dry"))


def _encogido_diario(hoy: float, historia: list[float], *, corte: float,
                     min_corridas: int, minimo_referencia: float) -> dict | None:
    """PURO. `None` = no opina (poca historia o referencia chica); si no, el
    veredicto con la referencia y el ratio."""
    import statistics
    if len(historia) < min_corridas:
        return None
    ref = float(statistics.median(historia))
    if ref < minimo_referencia:
        return None
    ratio = hoy / ref if ref else 0.0
    return {"referencia": ref, "ratio": round(ratio, 3), "encogido": ratio < corte,
            "corridas": len(historia)}


def _encogido_acumulado(hoy: float, anterior: float | None, *, corte: float,
                        minimo_referencia: float) -> dict | None:
    """PURO. Sin corrida anterior del mismo día → no opina."""
    if anterior is None or anterior < minimo_referencia:
        return None
    ratio = hoy / anterior if anterior else 0.0
    return {"referencia": float(anterior), "ratio": round(ratio, 3),
            "encogido": ratio < corte, "corridas": 1}


def trajo_poco(u: dict) -> list[Hallazgo]:
    """Lo que cada job trae de afuera, contra lo que venía trayendo (§0.dk).

    Un job que trae la mitad de las cuentas sale en verde: corrió, escribió
    algo, `salud` lo ve ok. Medido el 2026-09-02: `interbanking_sync` trajo 0
    movimientos a las 17:00 después de 382 a las 15:01, con estado `ok`. Acá
    cada job declara en `reportes.VOLUMENES` cuál es su número y de qué forma
    crece, y esto lo compara. Dos reglas:

      · `volumen_encogido` (alta): trajo menos que `corte` × su referencia.
      · `volumen_sin_dato` (baja): la última corrida no tiene el contador
        declarado — el job cambió y la declaración quedó vieja.

    Sin arreglo: volver a correr el job, o mirar si el proveedor devuelve
    parcial, lo decide una persona.
    """
    from agente import fuentes
    from agente.reportes import VOLUMENES

    corte = float(u.get("corte", 0.5))
    min_corridas = int(u.get("min_corridas", 5))
    minimo_ref = float(u.get("minimo_referencia", 20))
    ventana = int(u.get("ventana", 10))
    out: list[Hallazgo] = []
    ciegos = 0
    for v in VOLUMENES:
        filas = fuentes.corridas(v.job, ventana + 6)
        if filas is None:
            ciegos += 1
            continue
        validas = [c for c in filas if c.get("status") in ("ok", "partial") and not _saltear(c)]
        if not validas:
            continue
        hoy_c = validas[0]
        hoy = _valor(hoy_c, v.stat)
        cuando = hoy_c["finished_at"]
        cuando_txt = cuando.astimezone(reloj.AR_TZ).strftime("%d/%m %H:%M") if cuando else "?"
        if hoy is None:
            out.append(Hallazgo(
                sujeto=v.job, regla="volumen_sin_dato", severidad="baja",
                problema=(f"la última corrida de «{v.job}» ({cuando_txt}) no guarda el "
                          f"contador «{v.stat}» que declara `reportes.VOLUMENES` · "
                          f"{reloj.hhmm()}"),
                detalle="stats presentes: " + ", ".join(sorted(hoy_c.get("stats") or {})),
                que_hacer=(f"El job cambió o la declaración quedó vieja: corregir el stat en "
                           f"`agente/reportes.py` o volver a guardarlo en `jobs/{v.job}.py`. "
                           f"Hasta entonces «{v.job}» no se vigila por volumen."),
                evidencia={"stat": v.stat, "corrida_at": cuando.isoformat() if cuando else None}))
            continue

        if v.modo == "diario":
            historia = [x for x in (_valor(c, v.stat) for c in validas[1:ventana + 1]
                                    if c.get("status") == "ok") if x is not None]
            r = _encogido_diario(hoy, historia, corte=corte, min_corridas=min_corridas,
                                 minimo_referencia=minimo_ref)
            contra = "la mediana de sus últimas corridas"
        else:
            # La referencia es la última corrida SANA del mismo día, no la
            # inmediata anterior: el 28/08 el banco trajo 245 → 7 → 0, y contra
            # el 7 la corrida del 0 «no opinaba» y cerraba el aviso por ausencia
            # con el job todavía en cero (lo mostró diag_ingesta --simular).
            dia = cuando.astimezone(reloj.AR_TZ).date() if cuando else None
            ant = next((x for x in (_valor(c, v.stat) for c in validas[1:]
                                    if c.get("finished_at")
                                    and c["finished_at"].astimezone(reloj.AR_TZ).date() == dia)
                        if x is not None and x >= minimo_ref), None)
            r = _encogido_acumulado(hoy, ant, corte=corte, minimo_referencia=minimo_ref)
            contra = "la última corrida sana del mismo día"
        if not r or not r["encogido"]:
            continue
        out.append(Hallazgo(
            sujeto=v.job, regla="volumen_encogido", severidad="alta",
            problema=(f"«{v.job}» trajo {int(hoy)} {v.que} a las {cuando_txt}: "
                      f"{int(r['ratio'] * 100)}% de {contra} ({int(r['referencia'])}) "
                      f"· {reloj.hhmm()}"),
            detalle=f"stat `{v.stat}` · modo {v.modo} · corte {int(corte * 100)}%",
            que_hacer=(f"Mirar el log de esa corrida (`manager.job_runs`): si el proveedor "
                       f"contestó parcial o hubo timeouts, volver a correr `jobs.{v.job}` "
                       f"y confirmar que el número vuelve. Si es real (feriado, día corto), "
                       f"«leído» y listo."),
            evidencia={"stat": v.stat, "modo": v.modo, "hoy": hoy, **r,
                       "corrida_at": cuando.isoformat() if cuando else None,
                       "status": hoy_c.get("status")}))
    if ciegos == len(VOLUMENES):
        raise SinDatos("no pude leer manager.job_runs: no sé cuánto trajo ningún job")
    if ciegos:
        logger.warning("trajo_poco: no pude leer %d de %d jobs — esos no cierran nada",
                       ciegos, len(VOLUMENES))
    return out



# ═══ contraparte_faltante ══════════════════════════════════════════════════
#
# ⚠️⚠️ **CUÁNDO UNA CUENTA ES CONTRAPARTE NO LO DECIDE ESTE DETECTOR: LO
# DECIDIÓ LA MESA 394 VECES Y ACÁ SE MIDIÓ CONTRA ESO** (§0.er, medido el
# 2026-09-08 con `scripts/diag_contrapartes`).
#
# El resultado, y es lo único que sostiene el recorte de abajo:
#
#   tipo_cliente = «Fondo Común de Inversión»  →  Fondos en 295 de 295 (100%)
#   tipo_cliente = «Empresa»                   →  13 filas, y de las 615
#                                                 pendientes 332 son Empresa
#
# O sea: **la señal fuerte es el `tipo_cliente` institucional, y nada más.** Sin
# ese recorte la lista son 615 cuentas —332 Empresa, 176 PyMES, 226
# cooperativas—, que no es una lista de pendientes: es la cartera de clientes.
# Una lista que no puede llegar a cero no la mira nadie (la lección de
# `ficha_incompleta`, que se acotó a las carteras justo por esto).
TIPOS_INSTITUCIONALES: tuple[str, ...] = (
    # El único MEDIDO al 100%. Y es regla de negocio escrita en otro lado:
    # `api/services/segmentacion.py` ya trata al FCI como PJ GRANDE siempre.
    "Fondo Común de Inversión",
    # Los otros tres NO están medidos —no hay ninguno en las 394 clasificadas—
    # y entran igual porque **no pueden ser un cliente minorista**: son formas
    # jurídicas institucionales. Son 10 cuentas en total, así que si el criterio
    # está mal se ve en una tarde. El hallazgo los muestra con su tipo al lado
    # para que se pueda distinguir el medido del razonado.
    "Compañía de seguros",
    "Institucional",
    "Fideicomiso",
)

# El sujeto es una FAMILIA: la fila se llena y se vacía a medida que entran
# cuentas nuevas. Por eso `naturaleza` la declara RECURRENTE y no cuenta
# episodios (§0.ep) — sus nacimientos miden cuántas cuentas entraron, no una
# falla.
FAMILIA_CONTRAPARTES = "CONTRAPARTES NUEVAS"


def contraparte_faltante(u: dict) -> list[Hallazgo]:
    """Cuentas institucionales activas que **no están en `clientes.contrapartes`**.

    Es la mitad que el conciliador de Manager no puede ver (§0.eq). Su criterio
    es un regex armado con los nombres de contrapartes que YA tenemos, así que
    sólo encuentra MÁS cuentas de las que ya conocemos: medido el 2026-09-08,
    de las 615 cuentas activas sin decidir encontraba **0**.

    ⚠️ **Y no es una lista de tareas: es plata mal contada.** Una contraparte
    sin registrar cuenta en el AuM como si sus tenencias fueran de un cliente
    —son cuotapartes— y su `nivel_3` queda mal (`jobs/_aum_filters` reglas 3 y
    4, `segmentacion.clasificar_nivel_3`). No falla nada: el AuM sale con
    confianza y de más.

    ⚠️ **LEE SQL Y NUNCA AUNESA.** El conciliador pega la API en vivo porque es
    un botón; un detector que corre cada media hora no puede. Las cuentas nuevas
    ya las trae `jobs/sync_comitentes` (14, 17 y 21 UTC), así que acá se ven
    dentro de la media hora siguiente al sync.

    ⚠️ **LO QUE ESTE DETECTOR NO PUEDE VER, y hay que decirlo:** las ALYC y los
    bancos. Medido: de las 398 contrapartes cargadas, **87 no están en
    `clientes.comitentes`** —el sync sólo pide `tipoCuenta=Comitente`— y son
    justo las que llegan sin `tipo_cliente`. Para esas hace falta una FOTO
    completa de Aunesa (el patrón de `foto_primary`), que todavía no existe.
    """
    from api.services.segmentacion import _TIPOS_PH
    from core.postgres import get_pool

    minimo = int(u.get("min_para_avisar", 1))
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT m.id_cuenta, coalesce(u.denominacion,''), m.tipo_cliente "
                "  FROM clientes.comitentes m "
                "  JOIN clientes.cuentas u ON u.id_cuenta = m.id_cuenta "
                " WHERE m.estado = 'Activa' "
                "   AND m.tipo_cliente = ANY(%s) "
                "   AND NOT EXISTS (SELECT 1 FROM clientes.contrapartes c "
                "                    WHERE c.id_cuenta = m.id_cuenta) "
                # Las que la mesa ya dijo que NO son (§0.es). Se restan acá y no
                # en Python para que el conteo y la lista salgan de la MISMA
                # consulta: dos filtros son dos números que se desincronizan.
                "   AND NOT EXISTS (SELECT 1 FROM clientes.contrapartes_descartadas d "
                "                    WHERE d.id_cuenta = m.id_cuenta) "
                " ORDER BY m.tipo_cliente, u.denominacion",
                (list(TIPOS_INSTITUCIONALES),))
            filas = [{"cuenta": r[0], "denominacion": r[1], "tipo_cliente": r[2]}
                     for r in cur.fetchall()]
            # El denominador: cuántas quedan afuera del recorte. Sin este número
            # «53 cuentas» se lee como «hay 53 sin decidir», y son 615 — el
            # resto son clientes, no contrapartes, pero eso hay que poder verlo.
            cur.execute(
                "SELECT count(*) FROM clientes.comitentes m "
                " WHERE m.estado = 'Activa' "
                # ⚠️ `coalesce` en las DOS, y no es cosmético: `NULL <> ALL(...)`
                # da NULL, así que la fila se cae del conteo. Las 52 cuentas
                # activas sin `tipo_cliente` (las que el sync no clasifica) se
                # habrían perdido y el número de «las que quedan afuera» habría
                # salido 510 donde son 562 — un contador que miente bajo.
                "   AND coalesce(m.tipo_cliente,'') <> ALL(%s) "
                "   AND coalesce(m.tipo_cliente,'') <> ALL(%s) "
                "   AND NOT EXISTS (SELECT 1 FROM clientes.contrapartes c "
                "                    WHERE c.id_cuenta = m.id_cuenta)",
                (list(_TIPOS_PH), list(TIPOS_INSTITUCIONALES)))
            sin_senal = int(cur.fetchone()[0])
    except Exception as e:
        raise SinDatos(f"no pude leer el padrón de clientes: {e}") from e

    if len(filas) < minimo:
        return []

    # La sugerencia SALE DE LA MISMA FUNCIÓN QUE USA EL CONCILIADOR, no de una
    # copia (REGLA #9): el día que allá cambie el criterio, la pantalla y el
    # agente no pueden empezar a sugerir cosas distintas sin que falle nada.
    from api.services.contrapartes_seg import (
        indice_contrapartes,
        inferir_segmento,
        sugerir_contraparte,
    )
    # ⚠️ **LA CONTRAPARTE SE APRENDE DE LAS QUE YA ESTÁN, no se lee del nombre**
    # (§0.es). «FCI Consultatio Estrategia IV» parece Consultatio y en la tabla
    # esas cuentas son ONE618: un sugeridor que mire el nombre se equivoca con
    # seguridad, y el que tilda acepta una propuesta que suena razonable.
    try:
        idx = indice_contrapartes()
    except Exception as e:
        # Sin índice se sigue: las filas salen sin propuesta, que es como
        # estaban. No poder sugerir NO es motivo para ocultar el aviso.
        logger.warning("contraparte_faltante: sin índice de contrapartes (%s)", e)
        idx = {}
    for f in filas:
        f["contraparte_sugerida"], f["porque"] = sugerir_contraparte(
            f["denominacion"], idx)
        f["segmento_sugerido"] = inferir_segmento(f["denominacion"], None) or ""
        # De DÓNDE sale la sugerencia. No es decorado: «lo dice Aunesa» y «lo
        # dice el nombre» son dos actos distintos de confirmar, y el que tilda
        # tiene que poder distinguirlos sin abrir nada.
        if f["tipo_cliente"] == "Fondo Común de Inversión":
            f["segmento_sugerido"] = f["segmento_sugerido"] or "Fondos"
            f["fuente"] = "tipo_cliente"
        else:
            f["fuente"] = "nombre" if f["segmento_sugerido"] else ""

    por_tipo: dict[str, int] = {}
    for f in filas:
        por_tipo[f["tipo_cliente"]] = por_tipo.get(f["tipo_cliente"], 0) + 1
    sugeridas = sum(1 for f in filas if f["contraparte_sugerida"])
    return [Hallazgo(
        sujeto=FAMILIA_CONTRAPARTES, regla="sin_contraparte", severidad="media",
        nombre=FAMILIA_CONTRAPARTES,
        # ⚠️ **CORTO A PROPÓSITO** (pedido del user, §0.es). Acá no hay un
        # problema que explicar: hay un número y un botón. La tarjeta llegó a
        # tener el desglose por tipo, las que quedaban afuera del recorte, ocho
        # denominaciones truncadas y una frase sobre el AuM — y nada de eso se
        # lee, porque la lista entera está a un click en el listado. Lo que
        # sacamos vive igual: el desglose y el denominador, en la evidencia.
        problema=f"{len(filas)} cuenta(s) institucional(es) sin contraparte",
        que_hacer=("Abrir el listado: tildá las que son contraparte y se dan de "
                   "alta; las que no, «no me interesan» y dejan de ofrecerse."),
        evidencia={"cantidad": len(filas), "sin_senal": sin_senal,
                   "por_tipo": por_tipo, "sugeridas": sugeridas,
                   # ⚠️ `_items` es la IDENTIDAD, no la pantalla (§0.cz): con la
                   # lista, una cuenta NUEVA no reincide sobre un alta que
                   # escribió OTRAS. El `_` lo esconde del front.
                   "_items": filas})]


# ═══ hd_1816_al_ccl ════════════════════════════════════════════════════════
#
# El sujeto es la TABLA, no el bono: lo que se decide es «este pipeline está
# pidiéndole a 1816 al dólar equivocado», y son 21 bonos a la vez o ninguno. Los
# tickers van en `evidencia["items"]` para que la reincidencia se cuente por
# bono (§0.cz) sin llenar AHORA de 21 filas que dicen lo mismo.
def hd_1816_al_ccl(u: dict) -> list[Hallazgo]:
    """Datos de 1816 de un bono HARD DOLLAR guardados al CCL en vez del MEP.

    **Por qué existe, y por qué mira las tres tablas juntas** (§0.ez). El
    default de la API de 1816 es `moneda=ars`, y su spec dice que con `ars`
    *«para instrumentos pagaderos en moneda distinta a ARS, para calcular
    indicadores las cotizaciones se dividen por CCL»*. Toda esta plataforma
    divide por MEP. Pedir sin decir la moneda **no falla**: devuelve un número
    plausible al dólar de ellos. Medido el 2026-09-09: BPOB7 daba TEA 7,26% en
    `ars` contra 2,44% en `mep` — 481 bps, y la paridad 3,98 pp arriba.

    Se escapó una vez en RESEARCH y, al buscarlo, estaba también en la tabla que
    alimenta la TEA de RENTA FIJA. Por eso el detector no pregunta por una vista:
    pregunta por **todo lo que 1816 nos dejó escrito**, sea cual sea el job que
    lo trajo. Un pipeline nuevo que se olvide de la moneda entra acá solo, sin
    que nadie se acuerde de agregarlo.

    Es un AVISO a propósito (`arreglos={}` en el catálogo): rehacer la serie
    cuesta créditos de 1816 y es un backfill, o sea REGLA #4 — no sale de un
    botón. Y se cierra solo: arreglado el pedido, el cron reescribe las filas.
    """
    from agente import fuentes
    filas = fuentes.monedas_1816()
    if filas is None:
        raise SinDatos("no pude leer las monedas de lo que trajo 1816: no sé si "
                       "algún bono en dólares está guardado al CCL")

    # Qué moneda le CORRESPONDE a cada uno sale del cliente, la misma función
    # que usan los jobs para pedir. Dos criterios para la misma pregunta
    # terminan siempre con uno de los dos viejo (REGLA #9).
    from core import mercado_1816

    mal: dict[str, list[dict]] = {}
    sin_ficha: dict[str, set[str]] = {}
    for f in filas:
        if not f.get("moneda_pago"):
            # Sin ficha en el catálogo no se puede AFIRMAR que esté mal. Se
            # cuenta aparte: es un agujero de catálogo, no un dato al CCL.
            sin_ficha.setdefault(f["tabla"], set()).add(f["ticker"])
            continue
        debe = mercado_1816.moneda_series(f["moneda_pago"])
        if debe == "mep" and f.get("moneda") != "mep":
            mal.setdefault(f["tabla"], []).append(f)

    hh = reloj.hhmm()
    out = []
    for tabla, malas in sorted(mal.items()):
        tickers = sorted({m["ticker"] for m in malas})
        filas_mal = sum(int(m.get("filas") or 0) for m in malas)
        monedas = sorted({str(m.get("moneda")) for m in malas})
        out.append(Hallazgo(
            sujeto=tabla, regla="al_ccl", severidad="alta",
            nombre=tabla,
            problema=f"{len(tickers)} bonos que PAGAN EN DÓLARES tienen sus "
                     f"números de 1816 guardados en {'/'.join(monedas)} y no en "
                     f"`mep`: son {filas_mal} filas calculadas al CCL de 1816, no "
                     f"al MEP con el que trabaja el resto · {hh}",
            detalle=", ".join(tickers[:40]) + ("…" if len(tickers) > 40 else ""),
            que_hacer=f"1) revisar que el escritor de {tabla} pida "
                      f"`moneda` derivada de `core.mercado_1816.monedas_de`, no "
                      f"el default de la API. 2) rehacer las filas: para "
                      f"`research.mkt_1816_series` es "
                      f"`jobs.mercado_1816_series --backfill --moneda mep` "
                      f"(cuesta créditos, REGLA #4); las otras dos las reescribe "
                      f"su cron en la próxima corrida.",
            evidencia={"tabla": tabla, "items": tickers, "filas": filas_mal,
                       "monedas_guardadas": monedas,
                       "moneda_que_corresponde": "mep"}))

    # Un ticker sin ficha cae en `ars` por default: si además paga en dólares,
    # está al CCL y nadie lo sabe. Es la puerta por la que volvería el problema.
    tope = int(u.get("sin_ficha_max", 0))
    for tabla, tks in sorted(sin_ficha.items()):
        if len(tks) <= tope:
            continue
        out.append(Hallazgo(
            sujeto=f"{tabla} · sin ficha", regla="sin_ficha_1816", severidad="media",
            nombre=tabla,
            problema=f"{len(tks)} tickers de {tabla} no están en el catálogo de "
                     f"1816: de esos no se puede DERIVAR en qué moneda pagan, "
                     f"así que se piden con el default `ars` — y si alguno paga "
                     f"en dólares queda al CCL sin que se note · {hh}",
            detalle=", ".join(sorted(tks)[:40]) + ("…" if len(tks) > 40 else ""),
            que_hacer="correr `python -m jobs.mercado_1816_discovery --apply "
                      "--catalogo` para refrescar el catálogo; si después de eso "
                      "siguen faltando, 1816 no los publica y hay que fijarles la "
                      "moneda a mano en el escritor.",
            evidencia={"tabla": tabla, "items": sorted(tks)}))
    return out
