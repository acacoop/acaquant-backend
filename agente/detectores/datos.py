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


# ═══ cierre_sano ═══════════════════════════════════════════════════════════
def _salta(hoy: float, historia: list[float], veces: float) -> tuple[bool, float]:
    """¿El movimiento de hoy es más grande que `veces` × el mayor movimiento
    de la historia? Devuelve `(salta, maximo_historico)`. PURO.

    Sin umbral en porcentaje: la normalidad de cada serie es SU historia. Un
    bono que se mueve 3% por día y un AuM que se mueve 0,5% no pueden compartir
    un número, y el número que compartían en `guardrails` era `None`."""
    maximo = max((abs(x) for x in historia), default=0.0)
    if maximo <= 0:
        return False, maximo
    return abs(hoy) > veces * maximo, maximo


def _deltas(serie: list[float]) -> list[float]:
    """Variaciones % entre valores consecutivos (la más nueva primero)."""
    from itertools import pairwise
    out = []
    for a, b in pairwise(serie):
        if a and b and b > 0:
            out.append((a / b - 1) * 100)
    return out


def cierre_sano(u: dict) -> list[Hallazgo]:
    """El cierre del día contra su propia historia, y los emisores que se
    contradicen. Reemplaza a `jobs/guardrails.py` (§0.dj).

    Cuatro reglas, ninguna con umbral inventado:

      · `aum_salto`: el AuM total se movió más que `veces_maximo` × el mayor
        movimiento diario de las últimas fechas. Una carga parcial del writer
        se ve como una caída que no pasó nunca.
      · `cierre_invalido`: filas del cierre de hoy con precio ≤ 0, null o sin
        ticker. Invariante absoluto.
      · `cierre_salto`: un bono cuyo cierre se movió más que `veces_maximo` ×
        su propio máximo. Un print malo o un evento real: en los dos casos
        alguien tiene que mirarlo antes de que entre al histórico.
      · `emisor_contradictorio` y `emisor_sin_industria`: lo que el catálogo
        de emisores deja agrupando como «otros» sin que se note.
    """
    from agente import fuentes

    veces = float(u.get("veces_maximo", 1.5))
    minimo = int(u.get("min_historia", 10))
    out: list[Hallazgo] = []
    ciegos = []

    # ── AuM ──
    serie = fuentes.aum_serie()
    if serie is None:
        ciegos.append("portafolio.tenencia")
    elif len(serie) >= 2:
        deltas = _deltas([t for _, t in serie])
        hoy, historia = deltas[0], deltas[1:]
        if len(historia) >= minimo:
            salta, maximo = _salta(hoy, historia, veces)
            if salta:
                f_hoy, f_ayer = serie[0][0], serie[1][0]
                out.append(Hallazgo(
                    sujeto="AuM", regla="aum_salto", severidad="alta",
                    problema=(f"el AuM total del {f_hoy} se movió {hoy:+.1f}% contra el "
                              f"{f_ayer}: nunca se movió más de {maximo:.1f}% en las "
                              f"últimas {len(historia)} fechas · {reloj.hhmm()}"),
                    detalle=f"hoy {serie[0][1]:,.0f} · ayer {serie[1][1]:,.0f}",
                    que_hacer=("Antes de que alguien decida con ese número: comparar la "
                               "tenencia de hoy contra ayer por cuenta. Si el writer trajo "
                               "parcial, rehacer el día con `jobs.portafolio_backfill "
                               "--diario`; si es real, no hay nada que arreglar."),
                    evidencia={"fecha": str(f_hoy), "delta_pct": round(hoy, 2),
                               "maximo_historico_pct": round(maximo, 2),
                               "fechas_en_historia": len(historia), "veces_maximo": veces}))

    # ── el cierre ──
    c = fuentes.cierres()
    if c is None:
        ciegos.append("mercado.snapshots_cierre_hist")
    elif c["fechas"]:
        f_hoy = c["fechas"][0]
        malos = sorted({str(f.get("ticker_corto") or "SIN_TICKER") for f in c["filas_hoy"]
                        if not f.get("ticker_corto") or f.get("ultimo_precio") is None
                        or f["ultimo_precio"] <= 0})
        if malos:
            out.append(Hallazgo(
                sujeto="cierre", regla="cierre_invalido", severidad="alta",
                problema=(f"{len(malos)} fila(s) del cierre del {f_hoy} con precio ≤ 0, "
                          f"nulo o sin ticker · {reloj.hhmm()}"),
                detalle=" · ".join(malos[:30]),
                que_hacer=("Rehacer el cierre con `jobs.snapshot_cierre`. Si vuelve "
                           "inválido, el motor escribió un precio ≤ 0 para ese símbolo: "
                           "mirar el bono en Manager → TÍTULOS · BONOS."),
                evidencia={"fecha": str(f_hoy), "items": malos}))
        hoy_px = c["precios"].get(f_hoy) or {}
        for tk, px in sorted(hoy_px.items()):
            serie_tk = [px] + [c["precios"].get(f, {}).get(tk) for f in c["fechas"][1:]]
            serie_tk = [x for x in serie_tk if x]
            deltas = _deltas(serie_tk)
            if len(deltas) < minimo + 1:
                continue
            salta, maximo = _salta(deltas[0], deltas[1:], veces)
            if salta:
                out.append(Hallazgo(
                    sujeto=tk, regla="cierre_salto", severidad="media",
                    problema=(f"«{tk}» cerró {deltas[0]:+.1f}% el {f_hoy}: nunca se movió "
                              f"más de {maximo:.1f}% en sus últimas {len(deltas) - 1} "
                              f"ruedas · {reloj.hhmm()}"),
                    detalle=f"cierre {px} · anterior {serie_tk[1]}",
                    que_hacer=("Confirmar el precio contra 1816/BYMA. Si es un print malo, "
                               "corregir el cierre de ese bono en "
                               "`mercado.snapshots_cierre_hist`: ese cierre alimenta el "
                               "histórico de la curva."),
                    evidencia={"fecha": str(f_hoy), "delta_pct": round(deltas[0], 2),
                               "maximo_historico_pct": round(maximo, 2),
                               "ruedas_en_historia": len(deltas) - 1}))

    # ── los emisores ──
    em = fuentes.emisores_estado()
    if em is None:
        ciegos.append("mercado.emisores")
    else:
        for e in em["contradicciones"]:
            out.append(Hallazgo(
                sujeto=str(e.get("emisor")), regla="emisor_contradictorio", severidad="media",
                problema=(f"«{e.get('emisor')}» tiene {e.get('bonos')} bonos con sectores "
                          f"distintos entre sí: {e.get('sectores')} · {reloj.hhmm()}"),
                detalle=str(e.get("sectores") or ""),
                que_hacer=("Elegir UNA industria para el emisor en Manager → EMISORES; "
                           "hasta entonces sus bonos agrupan en dos lados."),
                evidencia={"bonos": e.get("bonos"), "sectores": e.get("sectores")}))
        sin = [str(e.get("emisor")) for e in em["sin_industria"]]
        if sin:
            out.append(Hallazgo(
                sujeto="industria", regla="emisor_sin_industria", severidad="baja",
                problema=(f"{len(sin)} emisor(es) corporativos sin industria: al agrupar "
                          f"caen en «otros» sin que se note · {reloj.hhmm()}"),
                detalle=" · ".join(sin[:30]),
                que_hacer=("Cargar la industria de cada uno en Manager → EMISORES. Es "
                           "carga a mano: el job de 1816 trae el emisor, no el sector."),
                evidencia={"items": sin,
                           "bonos": sum(int(e.get("bonos") or 0) for e in em["sin_industria"])}))

    if ciegos and len(ciegos) == 3:
        raise SinDatos("no pude leer tenencia, cierre ni emisores: no sé cómo cerró el día")
    if ciegos:
        # Miré una parte. Lo que no pude ver no se cierra: lo digo en el log y
        # sigo, porque cerrar por ausencia con media mirada es el invariante #1.
        logger.warning("cierre_sano: no pude leer %s — esas reglas no cierran nada",
                       ", ".join(ciegos))
    return out

