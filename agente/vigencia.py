"""`agente/vigencia.py` — **¿EL SUJETO DE UN HALLAZGO TODAVÍA EXISTE?**

Doc: `docs/AGENT.md` §6.8. Este módulo **no escribe nada** y no decide nada
sobre un hallazgo: contesta una pregunta y `agente/registro.py` —la puerta
única— hace con eso lo que corresponde.

EL PROBLEMA QUE RESUELVE
========================

Un hallazgo es **una afirmación sobre el mundo en un momento**: «M31G6 falta en
el master». Debajo hay una premisa que nadie escribió: «M31G6 debería estar en
el master». Cuando el bono vence, la premisa se vuelve falsa y el hallazgo no
está resuelto ni sin resolver: **dejó de aplicar**.

Hasta acá el agente no tenía cómo decir eso. Un hallazgo sólo moría de dos
formas —el detector no lo vio más, o una persona apretó «no me interesa»— y la
segunda es falsa (nadie está desinteresado) y además **pierde el motivo para
siempre**. Con lo cual el sistema quedaba igual de tonto para el próximo bono
que venciera.

LA REGLA QUE HACE QUE ESTO NO SEA PELIGROSO
===========================================

⚠️⚠️ **NO ENCONTRAR EL SUJETO NO ES PRUEBA DE QUE NO EXISTA.**

Es la única regla que importa y está en las tres funciones. Para decir «esto ya
no existe» hace falta una **partida de defunción**: una fuente que lo afirme,
con fecha. Que el ticker no aparezca en ninguna tabla significa *no sé*, y no sé
**no cierra nada** — es el invariante 1 (`docs/AGENT.md` §8) aplicado un nivel
más abajo.

Sin esa regla, esto sería el peor bug que puede tener una herramienta de
integridad: el día que una fuente devuelva vacío, el agente «caduca» los
hallazgos abiertos de golpe y deja el tablero en verde justo cuando está ciego.
Por eso `Veredicto.existe` es **de tres valores y no un bool**: `False` («está
muerto, y esta es la fuente») y `None` («no pude saberlo») son respuestas
distintas y no se pueden confundir en un `if`.

POR QUÉ ES UN REGISTRO POR TIPO Y NO UN `if` POR DETECTOR
=========================================================

Porque la caducidad tiene que servir para todo lo que puede dejar de existir —un
bono que venció, un job que se borró, una tabla que se dropeó— y no para el caso
que la motivó. El motor **no sabe qué es un bono**: sabe leer `sujeto_es` de la
fila del catálogo y preguntarle al verificador de ese tipo. Sumar un tipo nuevo
es una función acá y una palabra en `agente/tipos.SUJETOS`.

⚠️ **Hoy hay UN tipo: `bono`.** No es un recorte de ambición, es la regla de
arriba: es el único cuyo certificado de defunción existe y es verificable
(`fecha_vencimiento` en dos catálogos + la marca `vigente` que escribe
`jobs.validar_instrumentos`). `job`, `tabla`, `endpoint` y `motor` son
candidatos REALES y ninguno entra hasta tener una fuente que afirme la baja: un
verificador que caduque «porque no lo encontré» es exactamente lo que este
módulo existe para impedir.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

logger = logging.getLogger(__name__)

BONO = "bono"

# Cuántos puede caducar UNA corrida de UNA habilidad. No es una limitación
# técnica: es que **caducar 40 hallazgos de una es más probablemente una fuente
# rota que 40 bonos venciendo el mismo día**. Si se pasa, los que sobran se
# cierran por la vía de siempre (ausencia, que no miente) y queda en el log.
TOPE_POR_CORRIDA = 10


@dataclass(frozen=True)
class Veredicto:
    """TRES respuestas, no dos.

    `existe=True`  → está vivo.
    `existe=False` → **murió, y `motivo` + `fuente` dicen por qué y según quién**.
    `existe=None`  → no pude saberlo. No habilita nada.
    """

    existe: bool | None
    motivo: str = ""
    fuente: str = ""

    @property
    def muerto(self) -> bool:
        """Lo único que habilita a caducar. `None` NO entra acá — y por eso se
        pregunta con esta propiedad y no con `not v.existe`, que daría `True`
        para «no sé» y convertiría la guarda en su contrario."""
        return self.existe is False


NO_SE = Veredicto(None)


def _bonos(conn, sujetos: list[str]) -> dict[str, Veredicto]:
    """¿Estos tickers todavía existen? **UNA query para todos.**

    Tres fuentes, y alcanza con que UNA firme la defunción:

      1. `mercado.curvas.fecha_vencimiento`  — nuestro master.
      2. `portafolio.assets.vigente = false` — la baja explícita, que escribe
         `jobs.validar_instrumentos` (motivo `vencido`) o carga la mesa a mano
         (motivo `manual`). Sólo cuenta si **NINGUNA** unidad con ese ticker
         sigue vigente: un ticker con cuatro assets y uno vivo está vivo.
      3. `research.mkt_1816_instrumentos.fecha_vencimiento` — el catálogo de
         1816. Es la que resuelve el caso que motivó todo esto: un bono que
         `soberanos_faltantes` reporta **porque NO está en nuestro master** no
         puede verificarse contra nuestro master. La única que sabe de él es la
         fuente que lo nombró.

    ⚠️ **El vencimiento tiene que estar ESTRICTAMENTE en el pasado.** Un bono
    que vence hoy existe hoy. Se podría haber usado la ventana de
    `jobs/cleanup_curvas` (dos hábiles antes), pero esa ventana contesta «¿lo
    saco del master?», que es otra pregunta: acá se afirma que **el instrumento
    dejó de existir**, y para eso la única fecha honesta es la que ya pasó.

    ⚠️ **`date.today()` — el reloj del Droplet**, el mismo que usan
    `cleanup_curvas` y `detectores/mercado.soberanos_faltantes`. Con la fecha
    argentina, entre las 21 y las 00 la ventana daría distinto y volveríamos a
    tener dos criterios sobre el mismo bono (REGLA #9).
    """
    hoy = date.today()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.tk, c.fecha_vencimiento, i.fecha_vencimiento, "
            "       a.todos_de_baja, a.motivo "
            "  FROM unnest(%s::text[]) AS t(tk) "
            "  LEFT JOIN mercado.curvas c ON c.ticker = t.tk "
            "  LEFT JOIN research.mkt_1816_instrumentos i ON i.ticker = t.tk "
            "  LEFT JOIN LATERAL ("
            "     SELECT bool_and(NOT coalesce(x.vigente, true)) AS todos_de_baja, "
            "            min(x.vigencia_motivo) AS motivo "
            "       FROM portafolio.assets x WHERE x.ticker = t.tk"
            "  ) a ON true",
            (list(sujetos),))
        filas = cur.fetchall()

    out: dict[str, Veredicto] = {}
    for tk, vto_master, vto_1816, de_baja, motivo in filas:
        if vto_master and vto_master < hoy:
            out[tk] = Veredicto(False, f"venció el {vto_master.isoformat()}",
                                "mercado.curvas.fecha_vencimiento")
        elif de_baja is True:
            out[tk] = Veredicto(False, f"dado de baja ({motivo or 'sin motivo'})",
                                "portafolio.assets.vigente")
        elif vto_1816 and vto_1816 < hoy:
            out[tk] = Veredicto(False, f"venció el {vto_1816.isoformat()} según 1816",
                                "research.mkt_1816_instrumentos.fecha_vencimiento")
        elif vto_master or vto_1816 or de_baja is False:
            # Lo encontré y está vivo. Es una afirmación, no un default.
            out[tk] = Veredicto(True)
        else:
            # No está en ninguna de las tres. **NO ES PRUEBA DE NADA.**
            out[tk] = NO_SE
    return out


# El registro. Sumar un tipo es una fila acá + una palabra en `tipos.SUJETOS`.
VERIFICADORES = {BONO: _bonos}


def muertos(conn, tipo: str, sujetos: list[str]) -> dict[str, Veredicto]:
    """Los sujetos **verificablemente muertos** de esa lista, con su motivo.

    Devuelve SOLO los muertos: los vivos y los «no sé» no entran, así quien
    llama no puede confundirlos con un `.get()` distraído.

    ⚠️ **VA EN UN SAVEPOINT.** Corre adentro de la transacción de
    `registro.guardar`, que es la que escribe los hallazgos de la corrida. En
    psycopg un error deja la transacción entera abortada, así que sin el
    savepoint una tabla que no existe todavía —`apply_schema` va detrás del
    deploy— haría fallar **la escritura de la corrida completa**. Una función
    que sólo agrega información no puede tirar abajo la que ya andaba.

    Y si falla, la respuesta es NINGUNO. No «todos vivos», no «todos muertos»:
    ninguno caduca, y todo sigue funcionando como antes de que este módulo
    existiera.
    """
    verificador = VERIFICADORES.get(tipo)
    if not verificador or not sujetos:
        return {}
    try:
        with conn.transaction():          # SAVEPOINT
            veredictos = verificador(conn, sorted(set(sujetos)))
    except Exception as e:
        logger.warning("vigencia/%s: no pude verificar (%s) — no caduca nada, "
                       "que es lo mismo que hacía el agente antes", tipo, e)
        return {}
    return {s: v for s, v in veredictos.items() if v.muerto}
