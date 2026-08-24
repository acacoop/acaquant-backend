"""`agente/arreglos.py` — LO QUE ESCRIBE. Doc: `docs/AGENT_2.0.md` §6.4.

**Un arreglo ESCRIBE en algún lado.** Después de apretarlo, el mundo es
distinto. No son arreglos, y en el agente viejo estaban mezclados como si lo
fueran: «↻ chequear ahora» (mirar no arregla), «✔ entendido» (es marcar leído),
«✖ es ruido» (era un voto, y los votos se dieron de baja), «ignorar» (esconde),
explicar y simular (calculan, no mutan).

Cada arreglo tiene DOS verbos y la diferencia es la del front:

    preview(sujeto, evidencia)          CALCULA qué va a escribir. No muta.
    aplicar(sujeto, evidencia, por)     ESCRIBE. Deja la línea en el LIBRO.

⚠️ **No hay tabla de propuestas.** El agente viejo persistía la propuesta, la
mostraba, y después la aplicaba desde la fila guardada — con lo cual el mundo
podía haber cambiado entre las dos. Acá `preview` se recalcula cuando se mira y
`aplicar` vuelve a calcular antes de escribir: lo que se aplica es lo que es
cierto en ese momento, no lo que era cierto cuando alguien abrió la pantalla.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from agente import libro
from core.postgres import get_pool

logger = logging.getLogger(__name__)


@dataclass
class Resultado:
    ok: bool
    detalle: str = ""
    campo: str = ""
    antes: str = ""
    despues: str = ""
    donde: str = ""
    # ¿El efecto se ve YA, o hay que esperar a que el mundo conteste?
    # `False` deja el hallazgo `en_curso`: el detector confirma después.
    inmediato: bool = True


class Arreglo:
    id = ""
    titulo = ""
    donde = ""            # en qué tabla escribe
    campo = ""
    # ¿El efecto se ve en el acto? Una acción que se aplica y NO se ve destruye
    # la confianza en todas las demás — así que cuando no se ve, se dice.
    inmediato = True

    def preview(self, sujeto: str, ev: dict) -> dict:
        raise NotImplementedError

    def aplicar(self, sujeto: str, ev: dict, por: str = "") -> Resultado:
        raise NotImplementedError


# ── PEDIR UN SÍMBOLO QUE NADIE ESCUCHA ─────────────────────────────────────
class PedirPata(Arreglo):
    """**El caso AO29.** El bono mostraba la fila vacía; se buscó el bug en el
    bono, en los ejes, en los flujos y en la valuación, y estaba todo bien: nadie
    le estaba pidiendo el precio a su pata.

    Como `market_snapshot` y `timesales` las escribe el motor **solo para lo que
    suscribe**, todas nuestras tablas decían «no cotiza» POR CONSTRUCCIÓN.
    Preguntarle a esas tablas si un símbolo opera es preguntarle al que no estaba
    escuchando si sonó el teléfono. Se pidió: cotizaba.

    Es la única cuyo efecto se ve en SEGUNDOS: el `adhoc_watcher` de
    `motor_rofex` pollea cada 5 s y las suscripciones de pyRofex son aditivas —
    no rompen nada, no hace falta reiniciar, y funciona en plena rueda.
    """

    id = "pedir_pata"
    titulo = "Pedirle el precio a un símbolo que nadie escucha"
    donde = "mercado.adhoc_subscriptions"
    campo = "suscripción"

    def _simbolo(self, sujeto: str, ev: dict) -> str:
        return (ev.get("simbolo") or "").strip()

    def preview(self, sujeto: str, ev: dict) -> dict:
        s = self._simbolo(sujeto, ev)
        if not s:
            return {"ok": False, "error": "el hallazgo no trae el símbolo"}
        return {"ok": True, "que_escribe": f"suscribir «{s}»", "donde": self.donde,
                "porque": ("nuestras tablas solo guardan lo que el motor pide: "
                           "la ausencia de precio no prueba que no cotice. El "
                           "motor la levanta en 5 s, sin reiniciar.")}

    def aplicar(self, sujeto: str, ev: dict, por: str = "") -> Resultado:
        from core import adhoc_subscriptions
        s = self._simbolo(sujeto, ev)
        if not s:
            return Resultado(False, "el hallazgo no trae el símbolo")
        # No se inventa un símbolo: tiene que existir como pata conocida.
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM mercado.especies WHERE simbolo = %s", (s,))
            if cur.fetchone() is None:
                return Resultado(False, f"«{s}» no existe en `mercado.especies`")
        r = adhoc_subscriptions.subscribe(s)
        if not r.get("ok"):
            return Resultado(False, f"no se pudo pedir: {r.get('reason')}")
        return Resultado(True, "pedida — el motor la levanta en 5 s",
                         campo=self.campo, antes="nadie la pide", despues=s,
                         donde=self.donde, inmediato=False)


# ── PEDIR LA PATA EN DÓLARES ───────────────────────────────────────────────
class PataDolar(Arreglo):
    """Pide **la OTRA pata** —la que está en dólares— de un bono cuyo símbolo
    actual sí cotiza, solo que en pesos.

    La escritura NO vive acá: delega en `agente.pata.pedir`, que es la misma
    función que usa la puerta de la pantalla. **Dos caminos a la misma escritura
    terminan siempre con dos criterios que se contradicen.**
    """

    id = "pata_dolar"
    titulo = "Pedir la pata en DÓLARES de un bono que cotiza en pesos"
    donde = "mercado.adhoc_subscriptions"
    campo = "suscripción"
    inmediato = False

    def preview(self, sujeto: str, ev: dict) -> dict:
        from agente import pata
        return pata.explicar(sujeto)

    def aplicar(self, sujeto: str, ev: dict, por: str = "") -> Resultado:
        from agente import pata
        r = pata.pedir(sujeto, por=por)
        if not r.get("ok"):
            return Resultado(False, str(r.get("error") or "no se pudo pedir"))
        return Resultado(True, str(r.get("detalle") or "pedida"),
                         campo=self.campo, despues=str(r.get("simbolo") or ""),
                         donde=self.donde, inmediato=False)


# ── APUNTAR EL MASTER A LA PATA CORRECTA ───────────────────────────────────
class ApuntarPata(Arreglo):
    """Cambia el SÍMBOLO del master **y** pide la pata nueva.

    ⚠️ **El motor arma su universo al arrancar**, así que el cambio del master no
    se ve hasta reiniciarlo fuera de rueda. Por eso se hacen las dos cosas: el
    master queda bien para siempre, y la suscripción ad-hoc hace que se vea hoy.
    Sin la segunda mitad, esta acción se aplicaría, se verificaría en verde, y en
    la pantalla no cambiaría nada — *una acción que se aplica y no se ve destruye
    la confianza en todas las demás*.
    """

    id = "apuntar_pata"
    titulo = "Apuntar el master a la pata correcta (y pedirla)"
    donde = "mercado.curvas.instrumento + adhoc_subscriptions"
    campo = "instrumento"
    inmediato = False

    def _destino(self, ev: dict) -> str:
        return (ev.get("pata_dolar") or "").strip()

    def preview(self, sujeto: str, ev: dict) -> dict:
        d = self._destino(ev)
        if not d:
            return {"ok": False, "error": "el hallazgo no trae la pata destino"}
        return {"ok": True, "que_escribe": f"instrumento → «{d}»",
                "donde": self.donde, "antes": ev.get("simbolo"),
                "porque": ("el master suscribe la pata en pesos teniendo la pata "
                           "en dólares. Se cambia el master Y se pide la pata, "
                           "porque el motor arma su universo al arrancar.")}

    def aplicar(self, sujeto: str, ev: dict, por: str = "") -> Resultado:
        from core import adhoc_subscriptions
        d = self._destino(ev)
        if not d:
            return Resultado(False, "el hallazgo no trae la pata destino")
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM mercado.especies WHERE simbolo = %s", (d,))
            if cur.fetchone() is None:
                return Resultado(False, f"«{d}» no existe en `mercado.especies`")
            cur.execute("SELECT instrumento FROM mercado.curvas WHERE ticker = %s",
                        (sujeto,))
            f = cur.fetchone()
            antes = (f[0] if f else "") or ""
            cur.execute("UPDATE mercado.curvas SET instrumento = %s "
                        "WHERE ticker = %s", (d, sujeto))
            if not cur.rowcount:
                return Resultado(False, f"«{sujeto}» no está en mercado.curvas")
        adhoc_subscriptions.subscribe(d)
        return Resultado(True, f"el master apunta a «{d}» y la pata quedó pedida",
                         campo=self.campo, antes=antes, despues=d,
                         donde=self.donde, inmediato=False)


# ── DAR DE ALTA EL CRONOGRAMA DESDE 1816 ───────────────────────────────────
class AltaFlujos(Arreglo):
    """El bono sin cronograma → su ticker va a 1816.

    Delega en la cadena de alta (`agente.alta`), que baja el cuadro, simula la
    TEA, la coteja contra 1816 y **solo escribe si la cadena cierra**. Si 1816 no
    lo tiene, ESE es el hallazgo: se modela a mano.
    """

    id = "alta_flujos"
    titulo = "Dar de alta el cronograma desde 1816"
    donde = "mercado.curvas"
    campo = "FLUJOS"

    def preview(self, sujeto: str, ev: dict) -> dict:
        from agente import alta
        return alta.simular_flujos(sujeto)

    def aplicar(self, sujeto: str, ev: dict, por: str = "") -> Resultado:
        from agente import alta
        r = alta.aplicar_flujos(sujeto, actor=por)
        if not r.get("ok") or not r.get("aplicado"):
            return Resultado(False, str(r.get("error") or "la cadena no cerró"))
        return Resultado(True, "cronograma cargado", campo=self.campo,
                         antes="sin flujos", despues="cargado desde 1816",
                         donde=self.donde)


# ── DAR DE ALTA EL BONO ────────────────────────────────────────────────────
class AltaBono(Arreglo):
    """Un bono que 1816 lista y nosotros no tenemos → alta completa."""

    id = "alta_bono"
    titulo = "Dar de alta el bono desde 1816"
    donde = "mercado.curvas"
    campo = "bono"

    def _curva(self, ev: dict) -> str:
        return str(ev.get("curva_1816") or "").strip()

    def preview(self, sujeto: str, ev: dict) -> dict:
        from agente import alta
        if not (c := self._curva(ev)):
            return {"ok": False, "error": "el hallazgo no trae la curva de 1816"}
        return alta.simular(sujeto, curva_1816=c)

    def aplicar(self, sujeto: str, ev: dict, por: str = "") -> Resultado:
        from agente import alta
        if not (c := self._curva(ev)):
            return Resultado(False, "el hallazgo no trae la curva de 1816")
        r = alta.aplicar(sujeto, curva_1816=c, actor=por)
        if not r.get("ok") or not r.get("aplicado"):
            return Resultado(False, str(r.get("error") or "la cadena no cerró"))
        return Resultado(True, "bono dado de alta", campo=self.campo,
                         antes="no estaba", despues=f"cargado ({c})",
                         donde=self.donde)


# ── REHACER EL DÍA DE UN JOB ───────────────────────────────────────────────
class RehacerJob(Arreglo):
    """Vuelve a correr un job cuyo dato falta, por el MISMO lanzador que usa el
    cron (`run_job.sh`: lock + timeout).

    ⚠️ **Se verifica mirando la TABLA, no el código de salida del proceso.** Un
    job puede terminar en 0 y no haber escrito nada.
    """

    id = "rehacer_job"
    titulo = "Rehacer el día de un job cuyo dato falta"
    donde = "run_job.sh"
    campo = "corrida"

    def _job_fecha(self, sujeto: str, ev: dict):
        from agente import rehacer
        job = (ev.get("job") or sujeto or "").strip()
        job = job.split(":")[-1]
        return job, rehacer.fecha_objetivo(job)

    def preview(self, sujeto: str, ev: dict) -> dict:
        from agente import rehacer
        job, fecha = self._job_fecha(sujeto, ev)
        hay = rehacer.hay_dato(job, fecha)
        return {"ok": True, "que_escribe": f"correr `{job}` para {fecha}",
                "donde": self.donde, "ya_hay_dato": hay,
                "porque": ("el job no dejó el dato de ese día. Se relanza por el "
                           "mismo lanzador del cron y se verifica mirando la "
                           "tabla, no el código de salida.")}

    def aplicar(self, sujeto: str, ev: dict, por: str = "") -> Resultado:
        from agente import rehacer
        job, fecha = self._job_fecha(sujeto, ev)
        r = rehacer.rehacer(job, fecha, por=por)
        if not r.get("ok"):
            return Resultado(False, str(r.get("error") or "no se pudo rehacer"))
        hay = rehacer.hay_dato(job, fecha)
        if hay is None:
            return Resultado(True, "corrió, pero no pude releer la tabla",
                             campo=self.campo, despues=fecha, donde=self.donde,
                             inmediato=False)
        if not hay:
            return Resultado(False, f"corrió y la tabla SIGUE sin {fecha}")
        return Resultado(True, f"la tabla ya tiene {fecha}", campo=self.campo,
                         antes="sin dato", despues=fecha, donde=self.donde)


ARREGLOS: dict[str, Arreglo] = {
    a.id: a for a in (PedirPata(), PataDolar(), ApuntarPata(), AltaFlujos(),
                      AltaBono(), RehacerJob())
}


def catalogo() -> list[dict]:
    return [{"id": a.id, "titulo": a.titulo, "donde": a.donde,
             "campo": a.campo, "inmediato": a.inmediato}
            for a in ARREGLOS.values()]


def preview(hallazgo_id: int) -> dict:
    h = _hallazgo(hallazgo_id)
    if h is None:
        return {"ok": False, "error": "ese hallazgo no existe"}
    a = ARREGLOS.get(h["arreglo"])
    if a is None:
        return {"ok": False, "error": f"«{h['arreglo']}» no es un arreglo"}
    try:
        return {"ok": True, "arreglo": a.id, "titulo": a.titulo,
                **(a.preview(h["sujeto"], h["evidencia"]) or {})}
    except Exception as e:
        logger.exception("arreglos: preview de %s falló", a.id)
        return {"ok": False, "error": str(e)[:300]}


def aplicar(hallazgo_id: int, *, por: str = "") -> dict:
    """Aplica el arreglo de UN hallazgo y mueve su estado.

    ⚠️ **`en_curso` y no `resuelto`.** Que la escritura saliera bien no prueba
    que el problema se fue: eso lo dice el DETECTOR la próxima vez que mire. El
    cierre por acción lo hace `registro._cerrar_ausentes` cuando el detector
    deja de verlo — y solo ese cierre habilita la reincidencia.
    """
    from agente import tipos

    h = _hallazgo(hallazgo_id)
    if h is None:
        return {"ok": False, "error": "ese hallazgo no existe"}
    if h["estado"] not in tipos.ABIERTOS:
        return {"ok": False, "error": f"ese hallazgo está «{h['estado']}»"}
    a = ARREGLOS.get(h["arreglo"])
    if a is None:
        return {"ok": False, "error": f"«{h['arreglo']}» no es un arreglo"}

    try:
        r = a.aplicar(h["sujeto"], h["evidencia"], por=por)
    except Exception as e:
        logger.exception("arreglos: %s falló sobre %s", a.id, h["sujeto"])
        r = Resultado(False, str(e)[:300])

    libro.registrar(accion=a.id, objetivo=h["sujeto"], habilidad=h["habilidad"],
                    regla=h["regla"], hallazgo_id=hallazgo_id, por=por,
                    destino=r.donde or a.donde, campo=r.campo or a.campo,
                    antes=r.antes, despues=r.despues, ok=r.ok,
                    error="" if r.ok else r.detalle)
    if not r.ok:
        return {"ok": False, "error": r.detalle}

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE agente.hallazgos SET estado = %s, "
                    "  arreglo_aplicado = %s WHERE id = %s",
                    (tipos.EN_CURSO, a.id, hallazgo_id))
    return {"ok": True, "detalle": r.detalle, "estado": tipos.EN_CURSO,
            "inmediato": r.inmediato,
            "aviso": ("" if r.inmediato else
                      "aplicado — el efecto lo confirma el detector en su "
                      "próxima pasada")}


def _hallazgo(hid: int) -> dict | None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, habilidad, sujeto, regla, arreglo, evidencia, "
                    "       estado FROM agente.hallazgos WHERE id = %s", (hid,))
        f = cur.fetchone()
    if not f:
        return None
    return {"id": f[0], "habilidad": f[1], "sujeto": f[2], "regla": f[3],
            "arreglo": f[4], "evidencia": dict(f[5] or {}), "estado": f[6]}
