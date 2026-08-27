"""Detectores de DATOS y SEGURIDAD. Doc: `docs/AGENT_2.0.md` §5."""
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
        out.append(Hallazgo(
            # `alta` sin dudar: acá no hay «es contexto». Si dos copias
            # difieren, ALGO está leyendo el valor incorrecto ahora mismo — lo
            # único que no sabemos es quién.
            sujeto=str(d["id"]), regla="copias_que_no_coinciden", severidad="alta",
            nombre=str(d.get("que") or d["id"]),
            problema=f"{d['n']} caso(s) donde {d['que']} dice cosas distintas "
                     f"según dónde se lea: {d['a']} vs {d['b']}."
                     + (f" Ejemplos — {muestra}." if muestra else ""),
            detalle=muestra,
            que_hacer=f"Manda {d['arbitro']}. Qué se rompe si no: {d['rompe']}.",
            evidencia={"n": d["n"], "a": d["a"], "b": d["b"],
                       "arbitro": d["arbitro"], "ejemplos": ej}))
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
