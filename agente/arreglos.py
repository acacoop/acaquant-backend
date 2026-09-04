"""`agente/arreglos.py` — LO QUE ESCRIBE. Doc: `docs/AGENT.md` §6.4.

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


def _preview_de_simulacion(r: dict, *, donde: str, que: str) -> dict:
    """La simulación de la cadena de alta, con la forma que dibuja la pantalla.

    ⚠️ **`alta.simular` devuelve SU forma** —`ejes`, `rama`, `cupones`,
    `pasos`, `veredicto`— y la pantalla lee `que_escribe` / `porque`. Devolverla
    cruda dejaba el preview VACÍO (`escribe: —`) con toda la información
    calculada adentro del dict.

    Un botón que dice «ver qué haría» y muestra un guión es peor que no tenerlo:
    el que lo aprieta concluye que el agente no sabe, cuando en realidad sabe y
    no se lo tradujo.
    """
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error") or "la simulación no cerró"}
    ver = r.get("veredicto") or {}
    ejes = r.get("ejes") or {}
    resumen = " · ".join(x for x in (
        f"rama {r.get('rama')}" if r.get("rama") else "",
        f"{r.get('cupones')} cupones" if r.get("cupones") else "",
        f"vence {r.get('vencimiento')}" if r.get("vencimiento") else "",
        f"símbolo {r.get('simbolo')}" if r.get("simbolo") else "",
        f"TEA {float(r['tea']):.2%}" if r.get("tea") is not None else "",
    ) if x)
    return {
        "ok": True,
        "que_escribe": f"{que}: {resumen}" if resumen else que,
        "donde": donde,
        "porque": (ver.get("detalle") or ver.get("titulo") or "")
                  or (f"ejes {ejes.get('emisor_tipo')} · {ejes.get('moneda_eje')} "
                      f"· {ejes.get('ajuste')}" if ejes else ""),
        # ⚠️ La cadena viaja en `chequeos`, no en `pasos`. Leerla del nombre
        # equivocado no fallaba: devolvía una lista vacía, o sea el mismo guión
        # de antes con otra cara.
        "pasos": [{"titulo": c.get("titulo"), "estado": c.get("estado"),
                   "detalle": (c.get("detalle") or "")[:600],
                   "tabla": c.get("tabla"), "aviso": c.get("aviso") or ""}
                  for c in (r.get("chequeos") or [])],
        "puede_aplicar": ver.get("puede_aplicar", True),
        # El texto del veredicto ya viene armado y dice CUÁLES pasos bloquean:
        # «NO se puede aplicar — 2 paso/s lo bloquean: …».
        "veredicto": ver.get("texto") or "",
        # EL CUADRO. Es lo que el bono va a pagar, y es la mitad del valor de
        # simular aunque la cadena frene: verlo contesta «¿es este el bono?».
        "flujos": [{"fecha": f.get("fecha"),
                    "amortizacion": f.get("amortizacion_pct"),
                    "cupon": f.get("cupon_sobre_residual"),
                    "residual": f.get("residual_previo_pct")}
                   for f in ((r.get("cuadro") or {}).get("flujos") or [])],
        "escala": (r.get("cuadro") or {}).get("escala"),
        "rama": r.get("rama"),
        "vencimiento": r.get("vencimiento"),
        "simbolo": r.get("simbolo"),
        "tea": r.get("tea"),
        "precio": r.get("precio"),
        "ejes": ejes,
    }


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

    # ⚠️ **¿EL ARREGLO NECESITA QUE UNA PERSONA ELIJA ALGO?** Se declara.
    #
    # Casi todos CALCULAN el valor que van a escribir (qué pata suscribir, qué
    # día rehacer) y por eso el botón es un botón: no hay nada que preguntar.
    # `completar_ficha` no puede — la clase de activo de un título no se deduce
    # de ningún lado, la sabe la mesa—, así que su pantalla es un listado
    # editable y `aplicar` recibe lo que se cargó.
    #
    # Sin este flag el front tendría que adivinar cuál de los dos es cada uno, y
    # adivinar significa una lista de ids en el navegador que nadie mantiene
    # igual a esta (REGLA #9).
    pide_datos = False

    def preview(self, sujeto: str, ev: dict) -> dict:
        raise NotImplementedError

    def aplicar(self, sujeto: str, ev: dict, por: str = "",
                datos: list | None = None) -> Resultado:
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

    def aplicar(self, sujeto: str, ev: dict, por: str = "",
                datos: list | None = None) -> Resultado:
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

    def aplicar(self, sujeto: str, ev: dict, por: str = "",
                datos: list | None = None) -> Resultado:
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

    def aplicar(self, sujeto: str, ev: dict, por: str = "",
                datos: list | None = None) -> Resultado:
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
        return _preview_de_simulacion(alta.simular_flujos(sujeto),
                                      donde=self.donde,
                                      que=f"el cronograma de {sujeto}")

    def aplicar(self, sujeto: str, ev: dict, por: str = "",
                datos: list | None = None) -> Resultado:
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
        return _preview_de_simulacion(alta.simular(sujeto, curva_1816=c),
                                      donde=self.donde,
                                      que=f"{sujeto} (curva 1816: {c})")

    def aplicar(self, sujeto: str, ev: dict, por: str = "",
                datos: list | None = None) -> Resultado:
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
        """El nombre con el que el job figura en `REHACIBLES`.

        ⚠️ **Traduce `rehacer.cual_job`, y nadie más.** El detector pregunta por
        la MISMA puerta: si cada uno tuviera su regla, el detector ofrecería el
        botón y el botón contestaría «ese job no se puede rehacer» — la clase de
        contradicción que hace que la gente deje de creerle a la pantalla.

        Y no alcanzaba con que las dos reglas fueran idénticas: las dos hacían
        `split(":")` + `removeprefix("jobs.")`, iguales, y las dos estaban mal —
        el nombre real del job no se obtiene de ninguna transformación, se
        DECLARA (`conocido_como`).
        """
        from agente import rehacer
        job = rehacer.cual_job(ev.get("job") or sujeto or "")
        return job, rehacer.fecha_objetivo(job)

    def preview(self, sujeto: str, ev: dict) -> dict:
        from agente import rehacer
        job, fecha = self._job_fecha(sujeto, ev)
        cfg = rehacer.rehacibles().get(job) or {}
        hay = rehacer.hay_dato(job, fecha)
        prueba = cfg.get("prueba", rehacer.PRUEBA_DIA)
        if prueba == rehacer.PRUEBA_TABLA:
            verif = (f"el contrato de SALUD sobre {cfg.get('tabla')}: MAX("
                     f"{cfg.get('columna')}) con no más de "
                     f"{(cfg.get('contrato') or {}).get('max_dias_habiles', '?')} "
                     "días hábiles de atraso")
        elif prueba == rehacer.PRUEBA_CORRIDA:
            verif = (f"una corrida en manager.job_runs (tipo "
                     f"{', '.join(cfg.get('tipos') or ['?'])}) empezada después de "
                     f"{fecha} y que no haya fallado — es una prueba sobre la "
                     "CORRIDA, no sobre el dato: este job no tiene contrato de tabla")
        else:
            verif = (f'SELECT 1 FROM {cfg.get("tabla", "?")} '
                     f'WHERE {cfg.get("columna", "?")}::date = \'{fecha}\'')
        return {"ok": True, "que_escribe": f"correr `{job}` para {fecha}",
                "donde": self.donde, "ya_hay_dato": hay,
                # ⚠️ **EL COMANDO EXACTO, no una descripción de él.** «Ver qué
                # haría» tiene que mostrar lo que se va a ejecutar: sale de la
                # declaración (`REHACIBLES`), que es la misma que corre — no
                # puede quedar desactualizado respecto de lo que pasa.
                "pasos": [{"titulo": "lo que se va a ejecutar",
                           "detalle": f'{rehacer.RUN_JOB} {job} '
                                      f'{cfg.get("timeout", "")} '
                                      f'{cfg.get("comando", "")}',
                           "estado": "ok"},
                          {"titulo": "y después se verifica",
                           "detalle": verif,
                           "estado": "ok"}],
                "porque": ("el job no dejó el dato de ese día. Se relanza por el "
                           "mismo lanzador del cron y se verifica mirando la "
                           "tabla, no el código de salida. Qué le pide a la "
                           "fuente lo imprime el propio job en su log, y eso "
                           "vuelve acá cuando termina.")}

    def aplicar(self, sujeto: str, ev: dict, por: str = "",
                datos: list | None = None) -> Resultado:
        from agente import rehacer
        job, fecha = self._job_fecha(sujeto, ev)
        r = rehacer.rehacer(job, fecha, por=por)
        if r.get("lanzado"):
            # ⚠️ **SE LARGÓ Y TODAVÍA NO TERMINÓ, y eso NO es un fracaso.**
            # `inmediato=False` deja el hallazgo en `en_curso`; cuando el
            # detector deje de verlo se cierra POR ACCIÓN, que es exactamente
            # lo que hay que anotar. Contestar «no escribió» a los 20 segundos
            # de un trabajo de ocho minutos era medir antes de tiempo.
            return Resultado(True, str(r.get("detalle") or "lo largué"),
                             campo=self.campo, despues=fecha, donde=self.donde,
                             inmediato=False)
        if not r.get("ok"):
            # ⚠️ **LO QUE DIJO EL JOB VIAJA CON EL ERROR.** «Corrió sin error y
            # la tabla sigue sin el día» es un diagnóstico incompleto: dice qué
            # NO fue el problema. La última línea del job («OK=0 vacía=1040 ·
            # filas insertadas=0») dice qué SÍ pasó, y hasta hoy se descartaba
            # a dos niveles.
            return Resultado(False, " → ".join(
                x for x in (str(r.get("error") or "no se pudo rehacer"),
                            str(r.get("salida") or "")) if x))
        hay = rehacer.hay_dato(job, fecha)
        if hay is None:
            return Resultado(True, "corrió, pero no pude releer la tabla",
                             campo=self.campo, despues=fecha, donde=self.donde,
                             inmediato=False)
        if not hay:
            return Resultado(False, f"corrió y la tabla SIGUE sin {fecha}")
        return Resultado(True, f"la tabla ya tiene {fecha}", campo=self.campo,
                         antes="sin dato", despues=fecha, donde=self.donde)


# ── ARBITRAR DOS COPIAS DEL MISMO DATO ─────────────────────────────────────
class ArbitrarCopia(Arreglo):
    """Escribe la copia que PIERDE con el valor de la que MANDA, fila por fila,
    para un duplicado de `core/duplicados.DUPLICADOS` que declare `arreglo_sql`.

    Es UNA clase para todos los duplicados, manejada por el registro: el SQL
    que corre es el declarado al lado del chequeo, así que lo que se arregla es
    exactamente lo que el detector midió (§0.dc). `preview` recalcula la lista
    de filas que difieren cuando se mira y `aplicar` la recalcula de nuevo
    antes de escribir: lo que se aplica es lo cierto AHORA.
    """

    id = "arbitrar_copia"
    titulo = "Escribir la copia que pierde con el valor de la que manda"
    donde = "core/duplicados (la tabla del duplicado)"
    campo = "copia"

    @staticmethod
    def _tabla(d) -> str:
        import re
        m = re.search(r"UPDATE\s+([\w.]+)", d.arreglo_sql or "", re.I)
        return m.group(1) if m else "?"

    def preview(self, sujeto: str, ev: dict) -> dict:
        from core import duplicados
        d = duplicados.declarado(sujeto)
        if d is None:
            return {"ok": False, "error": f"«{sujeto}» no es un duplicado declarado"}
        if not d.arreglo_sql.strip():
            return {"ok": True, "que_escribe": "nada: este duplicado no se arregla con "
                                              "un UPDATE",
                    "donde": "—", "porque": d.arreglo_manual or d.arbitro,
                    "puede_aplicar": False, "pasos": []}
        r = duplicados.una(sujeto)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error") or "no pude leer el duplicado"}
        filas = r.get("filas") or []
        gana_a = d.gana != "b"
        tabla = self._tabla(d)
        return {
            "ok": True,
            "que_escribe": (f"{len(filas)} fila/s de {tabla}: "
                            f"{d.b if gana_a else d.a} ← {d.a if gana_a else d.b}"),
            "donde": tabla,
            "porque": f"manda {d.arbitro}. {d.rompe}",
            "puede_aplicar": bool(filas),
            "pasos": [{"titulo": str(f[0]),
                       "detalle": (f"«{f[2] if gana_a else f[1]}» → "
                                   f"«{f[1] if gana_a else f[2]}»"),
                       "estado": "ok"} for f in filas[:50]]
                     + ([{"titulo": f"… y {len(filas) - 50} más", "detalle": "",
                          "estado": "info"}] if len(filas) > 50 else []),
        }

    def aplicar(self, sujeto: str, ev: dict, por: str = "",
                datos: list | None = None) -> Resultado:
        from core import duplicados
        d = duplicados.declarado(sujeto)
        if d is None:
            return Resultado(False, f"«{sujeto}» no es un duplicado declarado")
        if not d.arreglo_sql.strip():
            return Resultado(False, f"no se arregla con un UPDATE: {d.arreglo_manual}")
        # Se relee ANTES de escribir: es la lista de lo que va a cambiar, y es la
        # que se anota en el libro fila por fila.
        antes = duplicados.una(sujeto)
        if not antes.get("ok"):
            return Resultado(False, f"no pude leer el duplicado: {antes.get('error')}")
        filas = antes.get("filas") or []
        if not filas:
            return Resultado(True, "ya coinciden: no había nada que arbitrar",
                             campo=self.campo, donde=self._tabla(d))
        r = duplicados.arbitrar(sujeto)
        if not r.get("ok"):
            return Resultado(False, str(r.get("error") or "no se pudo arbitrar"))
        gana_a = d.gana != "b"
        tabla = self._tabla(d)
        for f in filas:
            libro.registrar(accion=self.id, objetivo=str(f[0]), destino=tabla,
                            campo=d.que[:40], antes=str(f[2] if gana_a else f[1]),
                            despues=str(f[1] if gana_a else f[2]), por=por, ok=True)
        return Resultado(True, f"{r.get('filas', len(filas))} fila/s de {tabla} "
                               f"escritas con el valor de {'A' if gana_a else 'B'}",
                         campo=self.campo, antes=f"{len(filas)} distintas",
                         despues="coinciden", donde=tabla)


# ── COMPLETAR LA FICHA DE UN TÍTULO ────────────────────────────────────────
class CompletarFicha(Arreglo):
    """**El único arreglo que no calcula el valor: lo carga una persona.**

    La clase de activo de un título, o su emisor, no se deducen de ningún lado
    —`jobs/assets_autofill` corre todas las noches y ya completó todo lo que sus
    reglas saben derivar, así que lo que queda es, por definición, lo que
    ninguna regla puede resolver—. Medido el 2026-08-27: **0 derivables** en las
    cuatro reglas. Un botón «completar automáticamente» sería un botón que
    siempre dice «no pude», que es justo lo que se sacó del agente viejo.

    Lo que sí se puede es sacarle a la mesa el viaje a Manager: el listado de lo
    que falta se despliega **adentro del aviso**, se carga ahí, y **se escribe
    de verdad**. Lo completado desaparece de la lista porque `preview` la
    recalcula: no hay una foto guardada que pueda quedar vieja.

    ⚠️ **ESCRIBE POR LA PUERTA ÚNICA** (`assets_sql.set_campos`), con
    `crear=False`. No es un detalle: ese `set_campos` hace el `.strip()` de los
    valores —un `'HD  '` rompe los filtros que comparan exacto y no falla en el
    momento—, deja `actualizado_por`, invalida el cache, y con `crear=False` una
    unidad que no existe EXACTA levanta en vez de fabricar un asset fantasma
    trimmeado (el incidente OTC del 2026-08-22, REGLA #9).
    """

    id = "completar_ficha"
    titulo = "Completar la ficha de los títulos que están en cartera"
    donde = "portafolio.assets"
    campo = "ficha"
    pide_datos = True

    def _campo(self, sujeto: str, ev: dict) -> dict | None:
        """La definición del campo. **Sale del catálogo de detectores, no del
        sujeto**: así el arreglo no puede escribir una columna que el detector
        no mira, ni que no exista."""
        from agente.detectores.catalogo import CAMPOS
        objetivo = (ev.get("campo") or sujeto or "").strip()
        return next((c for c in CAMPOS if c["campo"] == objetivo), None)

    def preview(self, sujeto: str, ev: dict) -> dict:
        from agente.detectores import catalogo as det

        c = self._campo(sujeto, ev)
        if c is None:
            return {"ok": False,
                    "error": f"«{sujeto}» no es un campo de la ficha"}
        filas = det.faltantes(c)
        return {
            "ok": True,
            "que_escribe": (f"{c['campo'].upper()} en {len(filas)} título(s) de "
                            f"carteras de clientes"),
            "donde": self.donde,
            "porque": c["rompe"],
            "puede_aplicar": bool(filas),
            # LO QUE LA PANTALLA DESPLIEGA. Cada fila trae el resto de la ficha
            # para que se pueda decidir sin salir de acá: la cartera y el ticker
            # son lo que dice qué es ese título.
            "campo": c["campo"],
            "filas": filas,
            # Los valores que ese campo YA tiene, para elegir en vez de tipear.
            # Tipear es como nacen `HD ` y `hd`, que no fallan y rompen filtros.
            "opciones": det.valores_usados(c["campo"]),
        }

    def aplicar(self, sujeto: str, ev: dict, por: str = "",
                datos: list | None = None) -> Resultado:
        from api.services.assets_sql import set_campos

        c = self._campo(sujeto, ev)
        if c is None:
            return Resultado(False, f"«{sujeto}» no es un campo de la ficha")
        if not datos:
            return Resultado(False, "no se cargó ningún valor")

        # ⚠️ **SE VERIFICA CONTRA LA LISTA VIVA, NO CONTRA LO QUE MANDÓ EL
        # NAVEGADOR.** El front manda `unidad` y `valor`; si se escribiera
        # directo, esta puerta aceptaría escribir CUALQUIER unidad del catálogo
        # —incluida una que el detector no está mirando— porque el pedido viene
        # de afuera. Se recalcula el conjunto permitido acá.
        from agente.detectores import catalogo as det
        permitidas = {f["unidad"] for f in det.faltantes(c)}

        escritos, saltados, errores = [], [], []
        for d in datos:
            unidad = str((d or {}).get("unidad") or "")
            valor = str((d or {}).get("valor") or "").strip()
            if not unidad or not valor:
                continue                       # fila que no se cargó: no es error
            if unidad not in permitidas:
                # Ya no le falta —lo completó otro, o esta pantalla estaba
                # vieja—. No es un error y **no se pisa**: el valor que ya está
                # lo puso alguien, y este arreglo COMPLETA, no corrige.
                saltados.append(unidad)
                continue
            try:
                set_campos(unidad, {c["campo"]: valor}, actor=por, crear=False)
            except Exception as e:
                errores.append(f"{unidad[:30]}: {type(e).__name__}")
                continue
            escritos.append((unidad, valor))
            # UNA LÍNEA DE LIBRO POR TÍTULO. Son escrituras distintas sobre
            # filas distintas: resumirlas en una sola dejaría el libro sin poder
            # contestar «¿quién le puso HD a este?», que es para lo que existe.
            libro.registrar(
                accion=self.id, objetivo=unidad, destino=self.donde,
                campo=c["campo"], antes="", despues=valor, por=por, ok=True)

        if not escritos:
            return Resultado(
                False,
                (f"no se escribió nada — {len(saltados)} ya estaban completos, "
                 f"{len(errores)} fallaron"
                 + (f": {'; '.join(errores[:3])}" if errores else "")))

        quedan = len(permitidas) - len(escritos)
        return Resultado(
            True,
            campo=c["campo"], donde=self.donde,
            antes="(vacío)",
            despues=f"{len(escritos)} título(s)",
            detalle=(f"{len(escritos)} completado(s) · quedan {quedan}"
                     + (f" · {len(saltados)} ya estaban" if saltados else "")
                     + (f" · {len(errores)} con error: {'; '.join(errores[:3])}"
                        if errores else "")),
            # ⚠️ **`inmediato` acá NO habla de la escritura: habla del AVISO.**
            #
            # La escritura ya pasó y es segura —hay una línea de libro por
            # título—, pero el hallazgo es de TODO el campo: completar 5 de 379
            # no lo resuelve, el detector lo va a seguir viendo con 374 y tiene
            # que quedar abierto.
            #
            # **Salvo que hayas completado el último.** Ahí no queda nada que
            # esperar y decir «falta confirmar» sería mentir sobre un trabajo
            # terminado — que es exactamente lo que hacía hasta el 2026-08-28,
            # cuando el user preguntó *«¿confirmación de qué?? si yo ya lo
            # apliqué»*. El cierre igual lo hace el detector y sigue siendo POR
            # ACCIÓN; lo que cambia es lo que se le dice al que apretó.
            inmediato=(quedan <= 0))


# ── DAR DE ALTA CEDEARs ────────────────────────────────────────────────────
class AltaCedear(Arreglo):
    """CEDEARs que Primary lista (con la FICHA de un CEDEAR) y no tenemos →
    alta ENTERA de los que una persona tilde. Doc: §0.dl.

    Pide datos, como `completar_ficha`, pero por otra razón: el sistema SABE
    escribirlo todo (símbolo, subyacente, historia, ADR, suscripción) — lo que
    no puede decidir es CUÁLES. Primary lista muchos más CEDEARs de los que la
    mesa mira, y un botón que los sume a todos convertiría el scanner en la
    guía telefónica.

    ⚠️ **NO se escribe lo que manda el navegador**: cada ticker se vuelve a
    verificar contra Primary (foto y EN VIVO) y contra la ficha calibrada al
    aplicar. Uno que no pasa no frena a los demás.
    """

    id = "alta_cedear"
    titulo = "Dar de alta CEDEARs que cotizan en Primary"
    donde = "mercado.cedears"
    campo = "cedear"
    pide_datos = True
    # El efecto lo confirma el detector: el hallazgo es de la FAMILIA, y sumar
    # 3 de 300 no lo cierra.
    inmediato = False

    def preview(self, sujeto: str, ev: dict) -> dict:
        from agente import alta_cedear
        lst = alta_cedear.listado()
        if not lst.get("ok"):
            return {"ok": False, "error": lst.get("error")}
        return {
            "ok": True,
            "que_escribe": (f"los CEDEARs que tildes, de {len(lst['filas'])} que "
                            "Primary lista y no tenemos"),
            "donde": self.donde,
            "porque": (f"ficha calibrada con {lst['reconocidos']} de nuestros "
                       f"{lst['propios']}: cficode {lst['cficodes']} · plazo "
                       f"{lst['plazos']} · moneda {lst['monedas']}"),
            "puede_aplicar": bool(lst["filas"]),
            # LO QUE LA PANTALLA DESPLIEGA: un listado para TILDAR, no para
            # completar. `listado` le dice al front cuál de los dos dibujar —
            # la forma la decide el backend, no una lista de ids en el navegador.
            "listado": "cedears",
            "cedears": lst["filas"],
            "motor": alta_cedear._motor(),
        }

    def aplicar(self, sujeto: str, ev: dict, por: str = "",
                datos: list | None = None) -> Resultado:
        from agente import alta_cedear
        if not datos:
            return Resultado(False, "no se eligió ningún CEDEAR")
        r = alta_cedear.aplicar_varios(datos, actor=por)
        esc, err = r["escritos"], r["errores"]
        if not esc:
            return Resultado(False, "no se dio de alta ninguno — " + "; ".join(err[:4]))
        sin_hist = [e["ticker_corto"] for e in esc if e.get("velas_error")]
        sin_adr = [e["ticker_corto"] for e in esc if not e.get("adr_ok")]
        foto_vieja = [e["ticker_corto"] for e in esc if e.get("foto_vieja")]
        detalle = (f"{len(esc)} dado(s) de alta: "
                   + ", ".join(e["ticker_corto"] for e in esc)
                   + (f" · {len(err)} con error: {'; '.join(err[:3])}" if err else "")
                   + (f" · sin historia EOD (Yahoo no contestó): {', '.join(sin_hist)}"
                      if sin_hist else "")
                   + (f" · sin ADR (Finnhub no contestó): {', '.join(sin_adr)}"
                      if sin_adr else "")
                   + (f" · foto de Primary vieja, el precio llega tras el discovery: "
                      f"{', '.join(foto_vieja)}" if foto_vieja else ""))
        return Resultado(True, detalle, campo=self.campo, donde=self.donde,
                         antes="no estaban", despues=f"{len(esc)} CEDEAR(s)",
                         inmediato=False)


ARREGLOS: dict[str, Arreglo] = {
    a.id: a for a in (PedirPata(), PataDolar(), ApuntarPata(), AltaFlujos(),
                      AltaBono(), RehacerJob(), CompletarFicha(), ArbitrarCopia(),
                      AltaCedear())
}


def catalogo() -> list[dict]:
    return [{"id": a.id, "titulo": a.titulo, "donde": a.donde,
             "campo": a.campo, "inmediato": a.inmediato,
             "pide_datos": a.pide_datos}
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


def aplicar(hallazgo_id: int, *, por: str = "",
            datos: list | None = None) -> dict:
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

    # Todo lo que la acción anote adentro hereda el trío del hallazgo, así que
    # las líneas internas de la cadena de alta dejan de salir con «? · ?».
    with libro.contexto(habilidad=h["habilidad"], sujeto=h["sujeto"],
                        regla=h["regla"], hallazgo_id=hallazgo_id,
                        por=por) as cuantas:
        try:
            r = a.aplicar(h["sujeto"], h["evidencia"], por=por, datos=datos)
        except Exception as e:
            logger.exception("arreglos: %s falló sobre %s", a.id, h["sujeto"])
            r = Resultado(False, str(e)[:300])
        # ⚠️ Solo se anota si la acción NO anotó nada. Las que llevan su propio
        # libro (la cadena de alta escribe una línea por paso) ya dijeron qué
        # hicieron: agregar una segunda deja DOS filas para UNA acción, y la
        # segunda es la menos informativa de las dos.
        if not cuantas():
            libro.registrar(
                accion=a.id, objetivo=h["sujeto"], habilidad=h["habilidad"],
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
