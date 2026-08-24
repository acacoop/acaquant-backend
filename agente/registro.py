"""`agente/registro.py` — **LA ÚNICA PUERTA POR LA QUE EL AGENTE ESCRIBE.**

Doc: `docs/AGENT_2.0.md` §1.

El agente viejo tenía SEIS puertas escribiendo estado, cada una con su criterio
sobre qué guardar y qué significaba «resuelto». No era un mal diseño: era una
migración a medias. El costo no se paga en los bugs que ya salieron sino en que
**nada obligaba a una funcionalidad nueva a usar el pipeline que ya existía**.

Acá hay una función que escribe y un test que prohíbe el resto.

QUÉ HACE UNA CORRIDA, EN ORDEN
==============================

    1. sella la corrida en el CATÁLOGO — corra bien o mal, encuentre o no
    2. si el resultado NO es `ok`, TERMINA: no toca un solo hallazgo
    3. abre o refresca los hallazgos que vinieron
    4. cierra POR AUSENCIA los que estaban abiertos y no vinieron
    5. anota las REINCIDENCIAS de los que ya se habían cerrado POR ACCIÓN
"""
from __future__ import annotations

import json
import logging

from agente import tipos
from core.postgres import get_pool

logger = logging.getLogger(__name__)


def sellar_corrida(habilidad: str, *, resultado: str, error: str = "",
                   duracion_ms: int = 0) -> None:
    """La fila del catálogo. **Va SIEMPRE**, y es lo que separa «corrí y no
    encontré nada» de «no corrí» — que en el agente viejo se veían iguales."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE agente.habilidades SET "
            "  ultima_corrida_at = now(), ultimo_resultado = %s, "
            "  ultimo_error = %s, ultima_duracion_ms = %s, "
            # El contador se resetea solo cuando cambia el día: sin la fecha al
            # lado, un contador miente en el primer cambio de día. Sin cron.
            "  corridas_hoy = CASE WHEN corridas_dia = current_date "
            "                      THEN corridas_hoy + 1 ELSE 1 END, "
            "  corridas_dia = current_date "
            "WHERE nombre = %s",
            (resultado, (error or "")[:500], int(duracion_ms), habilidad))


def guardar(habilidad: str, hallazgos, *, resultado: str = tipos.OK,
            error: str = "", duracion_ms: int = 0) -> dict:
    """Escribe lo que una corrida vio. **La única puerta.**

    ⚠️ **`resultado` es la guarda más importante del subsistema.** Solo una
    corrida `ok` puede cerrar por ausencia. Una que miró menos de lo habitual
    —la fuente no contestó, el detector levantó— no puede convertir su lista más
    corta en «se arreglaron 40 problemas»: es la mentira más cara que puede
    decir una herramienta de integridad, porque deja el tablero en verde justo
    el día que está más ciega.
    """
    sellar_corrida(habilidad, resultado=resultado, error=error,
                   duracion_ms=duracion_ms)
    if resultado != tipos.OK:
        return {"ok": False, "resultado": resultado, "abiertos": 0,
                "nuevos": 0, "cerrados": 0, "reincidencias": 0}

    filas = list(hallazgos or [])
    nuevos = reincidencias = 0
    vistos: list[tuple[str, str]] = []
    with get_pool().connection() as conn:
        for h in filas:
            r = _ver(conn, habilidad, h)
            vistos.append((h.sujeto, h.regla))
            nuevos += 1 if r["nacio"] else 0
            reincidencias += 1 if r["reincidio"] else 0
        cerrados = _cerrar_ausentes(conn, habilidad, vistos)
    return {"ok": True, "resultado": resultado, "abiertos": len(filas),
            "nuevos": nuevos, "cerrados": cerrados,
            "reincidencias": reincidencias}


def _ver(conn, habilidad: str, h) -> dict:
    """Un hallazgo visto: lo abre si no estaba, o le suma una vuelta si estaba.

    El índice único parcial de `hallazgos` es el que garantiza que haya UNO
    abierto por problema. Es lo que reemplaza al «modo reemplazo» del agente
    viejo: sin él, un monitor de 30 segundos deja 2.880 filas del mismo problema
    por día.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE agente.hallazgos SET visto_ultima_vez = now(), "
            "  veces = veces + 1, "
            # La evidencia se REFRESCA: son los números de ahora, no los de la
            # primera vez. `detectado_at` NO se toca — es la fecha de nacimiento
            # y es lo que hace que AHORA se vacíe sola al día siguiente.
            "  evidencia = %s, problema = %s, severidad = %s, arreglo = %s "
            "WHERE habilidad = %s AND sujeto = %s AND regla = %s "
            "  AND estado = ANY(%s) RETURNING id",
            (json.dumps(h.evidencia or {}, default=str), h.problema,
             h.severidad, _arreglo_de(habilidad, h.regla), habilidad,
             h.sujeto, h.regla, list(tipos.ABIERTOS)))
        if (f := cur.fetchone()):
            return {"id": f[0], "nacio": False, "reincidio": False}

        # No estaba abierto. ¿Estuvo cerrado POR ACCIÓN alguna vez?
        cur.execute(
            "SELECT id, cerrado_at, arreglo_aplicado FROM agente.hallazgos "
            " WHERE habilidad = %s AND sujeto = %s AND regla = %s "
            "   AND estado = %s AND cerrado_como = %s "
            " ORDER BY cerrado_at DESC LIMIT 1",
            (habilidad, h.sujeto, h.regla, tipos.RESUELTO, tipos.POR_ACCION))
        previo = cur.fetchone()

        cur.execute(
            "INSERT INTO agente.hallazgos "
            " (habilidad, sujeto, regla, nombre, severidad, problema, "
            "  que_hacer, arreglo, evidencia, estado) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id, detectado_at",
            (habilidad, h.sujeto, h.regla, h.nombre or h.sujeto, h.severidad,
             h.problema, h.que_hacer, _arreglo_de(habilidad, h.regla),
             json.dumps(h.evidencia or {}, default=str), tipos.NUEVO))
        nid, nacido = cur.fetchone()

        if previo is None:
            return {"id": nid, "nacio": True, "reincidio": False}

        # ⚠️ **SOLO LO CERRADO POR ACCIÓN REINCIDE.** La guarda vive acá, en la
        # única función que inserta, porque la base no puede expresarla sin un
        # trigger. Si lo cerrado por AUSENCIA entrara, la tabla que debería
        # estar vacía se llenaría de bonos que no operaron esa noche.
        pid, cerrado_at, arreglo = previo
        cur.execute(
            "INSERT INTO agente.reincidencias "
            " (hallazgo_id, hallazgo_previo_id, habilidad, sujeto, regla, "
            "  arreglo_aplicado, resuelto_at, volvio_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (nid, pid, habilidad, h.sujeto, h.regla, arreglo or "",
             cerrado_at, nacido))
        cur.execute("UPDATE agente.hallazgos SET estado = %s WHERE id = %s",
                    (tipos.REINCIDIO, nid))
        logger.warning("REINCIDIÓ %s/%s/%s — el arreglo «%s» no sirvió",
                       habilidad, h.sujeto, h.regla, arreglo or "?")
        return {"id": nid, "nacio": True, "reincidio": True}


def _cerrar_ausentes(conn, habilidad: str, vistos: list[tuple[str, str]]) -> int:
    """Lo que estaba abierto y esta corrida NO volvió a ver.

    Cierra POR ACCIÓN si el arreglo se había aplicado (estaba `en_curso`) y por
    AUSENCIA si no. Esa diferencia es la que después decide si puede reincidir.
    """
    pares = [f"{s}\x00{r}" for s, r in vistos]
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE agente.hallazgos SET "
            "  estado = %s, cerrado_at = now(), "
            "  cerrado_como = CASE WHEN estado = %s THEN %s ELSE %s END "
            "WHERE habilidad = %s AND estado = ANY(%s) "
            "  AND (sujeto || chr(0) || regla) <> ALL(%s)",
            (tipos.RESUELTO, tipos.EN_CURSO, tipos.POR_ACCION,
             tipos.POR_AUSENCIA, habilidad, list(tipos.ABIERTOS), pares))
        return cur.rowcount or 0


def _arreglo_de(habilidad: str, regla: str) -> str:
    from agente import catalogo
    h = catalogo.HABILIDADES.get(habilidad)
    return h.arreglo_de(regla) if h else ""


def anotar_accion(*, arreglo: str, habilidad: str, sujeto: str, regla: str,
                  hallazgo_id: int | None = None, por: str = "",
                  donde: str = "", campo: str = "", antes: str = "",
                  despues: str = "", ok: bool = True, error: str = "") -> None:
    """EL LIBRO. Lo que dibuja HISTORIAL.

    ⚠️ **El trío va SIEMPRE.** En el agente viejo la mayoría de las acciones no
    guardaban la regla que las motivó, y por eso la única columna que contestaba
    «¿quedó arreglado?» no podía contestarlo. Acá es obligatorio.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agente.acciones (arreglo, habilidad, sujeto, regla, "
            " hallazgo_id, por, donde, campo, antes, despues, ok, error) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (arreglo, habilidad, sujeto, regla, hallazgo_id, por, donde,
             campo, str(antes)[:200], str(despues)[:200], ok, (error or "")[:500]))
