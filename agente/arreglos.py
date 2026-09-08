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
from dataclasses import dataclass, field

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
        "flujos": _flujos_para_ver((r.get("cuadro") or {}).get("flujos") or []),
        "escala": (r.get("cuadro") or {}).get("escala"),
        "rama": r.get("rama"),
        "vencimiento": r.get("vencimiento"),
        "simbolo": r.get("simbolo"),
        "tea": r.get("tea"),
        # La TNA es la que mira la mesa, y el DÓLAR es la mitad que faltaba: una
        # tasa en dólares sin decir cuál no es un número (§0.ec).
        "tna": r.get("tna"),
        "dolar": r.get("dolar"), "dolar_valor": r.get("dolar_valor"),
        "precio": r.get("precio"),
        "ejes": ejes,
    }


def _flujos_para_ver(flujos: list[dict]) -> list[dict]:
    """El cuadro convertido → la tabla de la pantalla. **SOLO para mostrar.**

    ⚠️⚠️ **LA SHAPE DEL CUADRO NO ES UNA SOLA, y esto mostraba «—» en todo.**
    `convertir_flujos` devuelve tres formas distintas, a propósito, porque el
    motor consume tres cosas distintas:

        cer / soberanos / dolar_linked  →  amortizacion_pct · cupon_sobre_residual
        tasa_fija / **on**              →  amortizacion · interes  (montos absolutos)

    El mapeo de acá conocía solo la primera, así que para una ON —y para una
    LECAP— las tres columnas salían vacías: la fila existía, la fecha estaba, y
    los números no. **No fallaba nada**: `f.get("amortizacion_pct")` sobre un
    dict que no la tiene devuelve None, y None se dibuja como un guión.

    El RESIDUAL se deriva acá cuando la shape no lo trae (§0.dw), con la MISMA cuenta que
    hace el motor en su rama `on` (línea 746 de `engines/curvas.py`): el residual
    vivo es la suma de las amortizaciones que faltan, no el campo
    `valor_residual` —que puede venir en otra escala—. Se deriva en el BACKEND y
    para la vista; el cuadro que se escribe no se toca.
    """
    out, residual = [], sum(float(f.get("amortizacion") or 0.0) for f in flujos)
    for f in flujos:
        amort = f.get("amortizacion_pct")
        if amort is None:
            amort = f.get("amortizacion")
        res = f.get("residual_previo_pct")
        if res is None and f.get("amortizacion") is not None:
            res, residual = residual, residual - float(f.get("amortizacion") or 0.0)
        out.append({"fecha": f.get("fecha"), "amortizacion": amort,
                    "cupon": (f.get("cupon_sobre_residual")
                              if f.get("cupon_sobre_residual") is not None
                              else f.get("interes")),
                    "residual": res})
    return out


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
    # ⚠️ **QUÉ HIZO, PASO POR PASO** (§0.dx). Un arreglo que hace SIETE cosas y
    # devuelve una frase al final obliga a confiar. Cada paso viaja con lo que
    # de verdad pasó —incluido el que salió mal sin tumbar a los demás— y la
    # pantalla lo dibuja como una lista, no como un párrafo. Vacío = el arreglo
    # todavía no lo cuenta; la pantalla cae al detalle de siempre.
    pasos: list = field(default_factory=list)


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

    # ⚠️⚠️ **¿EL DETECTOR PUEDE CONFIRMARLO YA, Y GRATIS?**
    #
    # Un hallazgo lleva su `problema`, su `detalle` y su evidencia ESCRITOS en
    # la fila, y el único que los reescribe es el detector cuando vuelve a
    # correr. `ficha_incompleta` corre cada SEIS HORAS — así que completabas 28
    # títulos, la lista de abajo pasaba a 10 (se recalcula al mirar) y la
    # tarjeta de arriba seguía diciendo 44 hasta la noche.
    #
    # Dos números sobre lo mismo, de instantes distintos, uno al lado del otro:
    # exactamente lo que este subsistema existe para no hacer. El user lo vio en
    # un minuto: *«dice 44 pero son 6, está 100% desactualizado»*.
    #
    # Con esto, el arreglo que puede probarse solo vuelve a correr su detector
    # apenas escribe. La tarjeta queda con el número de verdad, el texto se
    # regenera, y si no quedaba nada el hallazgo se cierra POR ACCIÓN.
    #
    # **Es `False` por default y cada arreglo lo declara**, porque volver a
    # correr NO es gratis para todos: el job de `rehacer_job` tarda ocho minutos
    # y ahí la respuesta todavía NO EXISTE — preguntarla sería medir antes de
    # tiempo, y el «no» que devolvería sería falso.
    #
    # ⚠️ El costo, en cambio, casi nunca alcanza para decir que no (2026-09-06).
    # Acá decía que las altas no confirmaban porque el censo de 1816 cuesta
    # créditos — y es cierto, pero **el alta ya gastó un crédito POR CUPÓN** para
    # bajar el cuadro: son 7 a 39. Un censo más para que la tarjeta quede con el
    # número de verdad es marginal contra eso, y la alternativa es lo que el user
    # vio: «APLICADO · ESPERANDO QUE EL DETECTOR CONFIRME» durante dos horas,
    # con el bono ya escrito.
    confirma_ya = False

    # ⚠️ **¿EL SUJETO ES UN CAMPO ENTERO —UNA FAMILIA— Y NO UN TÍTULO?**
    #
    # `completar_ficha` no arregla UN asset: arregla la lista de los que le
    # faltan a `clase_activo` hoy. Aplicarlo dos veces no duplica nada —la
    # segunda vez completa lo que sigue faltando— y hay que poder aplicarlo
    # de nuevo cada vez que aparece un título nuevo sin ese campo.
    #
    # Es `False` por default: un arreglo sobre UN título (`alta_bono`,
    # `pedir_pata`) no es repetible sobre el MISMO hallazgo — ya lo resolvió.
    repetible = False

    def preview(self, sujeto: str, ev: dict) -> dict:
        raise NotImplementedError

    def aplicar(self, sujeto: str, ev: dict, por: str = "",
                datos: list | None = None) -> Resultado:
        raise NotImplementedError

    def solo(self, sujeto: str, ev: dict) -> list[dict] | None:
        """Lo que el agente escribiría SOLO, sin persona: `datos` listos para
        `aplicar`, o `None` si este arreglo no sabe. Solo lo determinístico:
        lo del modelo nunca va por acá (§0.ei)."""
        return None


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
    # El sujeto ES el bono, así que el detector puede probarlo en el acto: si ya
    # está en el master, deja de encontrarlo y el hallazgo cierra POR ACCIÓN.
    # Sin esto quedaba en «esperando que el detector confirme» hasta dos horas
    # (§0.dy),
    # con el bono ya escrito — el mismo número viejo al lado del hecho nuevo.
    confirma_ya = True

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
            return Resultado(False, str(r.get("error") or "la cadena no cerró"),
                             pasos=r.get("pasos") or [])
        return Resultado(True, "bono dado de alta", campo=self.campo,
                         antes="no estaba", despues=f"cargado ({c})",
                         donde=self.donde, pasos=r.get("pasos") or [])


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
    # `dato_partido` vuelve a comparar las dos copias con SQL y nada más — y
    # después de arbitrar, la respuesta correcta es «ya coinciden». Que lo diga
    # en el momento es la mitad de la confianza en el botón.
    confirma_ya = True

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
    # Su detector son cinco consultas contra `portafolio.assets`: no toca la
    # red, no cuesta créditos y contesta en el acto. No hay razón para que la
    # tarjeta espere seis horas a decir la verdad.
    confirma_ya = True
    # El sujeto es un CAMPO —`clase_activo`, `emisor`—, no un título: aplicarlo
    # de nuevo no duplica nada, y hay que poder aplicarlo cada vez que aparece
    # un título nuevo al que le falta ese campo.
    repetible = True

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
        # ⚠️ **EL VALOR VIENE PROPUESTO, PERO NO ESCRITO** (2026-09-05).
        #
        # Por default este listado sale vacío y hay que tipear los N valores. Para
        # el EMISOR eso ya no hace falta: `agente/emisor.proponer` arma una
        # propuesta por fila y le cuelga DE DÓNDE SALIÓ, así el que mira destilda
        # en vez de escribir.
        #
        # Lo que NO cambia es quién decide: `aplicar` sigue recibiendo lo que la
        # pantalla mandó, sigue verificándolo contra la lista viva y sigue
        # escribiendo por `set_campos`. Una propuesta que nadie confirma no toca
        # la base — y si el gateway no contesta, las filas vuelven sin propuesta
        # y esto queda exactamente como estaba.
        #
        # Hoy la CLASE también se deriva, para CUATRO casos determinísticos:
        # derivados con C/P, copia de cartera (RENTA VARIABLE/HD/DL), FCI por
        # Primary y ARS por la curva del bono en el master (§0.ei). Y la
        # CARTERA de un bono se deriva por las reglas del job (financiamiento,
        # FCI, OTC/agro) o, si ninguna aplica, por sus EJES —moneda, ajuste—
        # en el master o en el catálogo de 1816 (§0.ej). El resto sigue siendo
        # criterio de la mesa: no hay de dónde derivarlo, y proponerlo sería
        # inventar.
        propuestas = 0
        if c["campo"] == "emisor" and filas:
            from agente import emisor as em
            try:
                filas = em.proponer(
                    filas, det.valores_usados("emisor"),
                    subyacentes=em.subyacentes([f.get("ticker", "") for f in filas]))
                propuestas = sum(1 for f in filas if f.get("propuesto"))
            except Exception as e:
                logger.warning("completar_ficha: sin propuestas de emisor (%s)", e)
        if c["campo"] == "clase_activo" and filas:
            from agente import clase, fuentes
            try:
                filas = clase.proponer(filas, fuentes.fichas_primary(),
                                       det.valores_usados("clase_activo"),
                                       master=fuentes.master())
                propuestas = sum(1 for f in filas if f.get("propuesto"))
            except Exception as e:
                logger.warning("completar_ficha: sin propuestas de clase (%s)", e)
        if c["campo"] == "cartera" and filas:
            from agente import cartera, fuentes
            try:
                filas = cartera.proponer(filas, fuentes.master(), fuentes.universo_1816(),
                                         det.valores_usados("cartera"))
                propuestas = sum(1 for f in filas if f.get("propuesto"))
            except Exception as e:
                logger.warning("completar_ficha: sin propuestas de cartera (%s)", e)
        return {
            "ok": True,
            "que_escribe": (f"{c['campo'].upper()} en {len(filas)} título(s) de "
                            f"carteras de clientes"
                            + (f" · {propuestas} con valor propuesto"
                               if propuestas else "")),
            "donde": self.donde,
            "porque": c["rompe"],
            "puede_aplicar": bool(filas),
            # LO QUE LA PANTALLA DESPLIEGA. Cada fila trae el resto de la ficha
            # para que se pueda decidir sin salir de acá: la cartera y el ticker
            # son lo que dice qué es ese título. Y, si es el emisor, `propuesto`
            # + `fuente`.
            "campo": c["campo"],
            "filas": filas,
            # Los valores que ese campo YA tiene, para elegir en vez de tipear.
            # Tipear es como nacen `HD ` y `hd`, que no fallan y rompen filtros.
            "opciones": det.valores_usados(c["campo"]),
        }

    def solo(self, sujeto: str, ev: dict) -> list[dict] | None:
        """Lo que el agente completaría SOLO: hoy, `clase_activo` y `cartera`,
        y sólo lo que una regla determinística resuelve Y ya existe en la
        lista cerrada (`clase.deterministas` / `cartera.deterministas`). El
        emisor y el resto de la clase siguen sin dueño automático: eso lo
        decide una persona."""
        from agente.detectores import catalogo as det

        c = self._campo(sujeto, ev)
        if c is None:
            return None
        from agente import fuentes
        if c["campo"] == "clase_activo":
            from agente import clase
            return clase.deterministas(
                clase.proponer(det.faltantes(c), fuentes.fichas_primary(),
                               det.valores_usados("clase_activo"),
                               master=fuentes.master()))
        if c["campo"] == "cartera":
            from agente import cartera
            return cartera.deterministas(
                cartera.proponer(det.faltantes(c), fuentes.master(),
                                 fuentes.universo_1816(),
                                 det.valores_usados("cartera")))
        return None

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
    # Mismo caso que `alta_on`, y acá ni siquiera cuesta créditos: el detector
    # lee la foto de Primary y el master, nada más.
    confirma_ya = True
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


# ── DAR DE ALTA LAS ONs QUE UNA PERSONA ELIJA ──────────────────────────────
class AltaON(Arreglo):
    """Las ONs hard dólar que 1816 publica y no tenemos → alta de las que se
    tilden. Doc: §0.dv.

    **Mismo problema que `alta_cedear` y misma forma.** 1816 publica muchas más
    ONs de las que la mesa quiere seguir (193 el 2026-09-06), así que el sistema
    sabe escribirlas todas y **no puede decidir cuáles**. Por eso el hallazgo es
    UNA fila de familia y el arreglo despliega la lista para tildar.

    ⚠️ **No se escribe lo que manda el navegador.** Cada ticker tildado vuelve a
    pasar por `alta.aplicar`, o sea por el pre-flight entero: baja el cuadro de
    1816, lo convierte, calcula la TEA y **coteja el cronograma contra el de
    ellos**. Una que no cierra no se escribe y no frena a las demás.
    """

    id = "alta_on"
    titulo = "Dar de alta ONs que 1816 publica"
    donde = "mercado.curvas"
    campo = "bono"
    pide_datos = True
    # Una sola corrida al final, no una por ON: el detector recuenta y la fila
    # queda con las que SIGUEN faltando. No cierra (quedan las demás), pero deja
    # de mentir el número.
    confirma_ya = True
    # El hallazgo es de la FAMILIA: sumar 3 de 193 no lo cierra.
    inmediato = False

    def _filas(self, ev: dict) -> list[dict]:
        return [x for x in (ev.get("_items") or []) if isinstance(x, dict)]

    def preview(self, sujeto: str, ev: dict) -> dict:
        filas = self._filas(ev)
        return {
            "ok": True,
            "que_escribe": (f"las ONs que tildes, de {len(filas)} que 1816 "
                            "publica y no tenemos"),
            "donde": self.donde,
            "porque": ("cada una se da de alta con su cronograma de 1816 y se "
                       "coteja contra el de ellos antes de escribir: la que no "
                       "cierra no se escribe"),
            "puede_aplicar": bool(filas),
            "listado": "ons",
            "ons": filas,
        }

    def aplicar(self, sujeto: str, ev: dict, por: str = "",
                datos: list | None = None) -> Resultado:
        from agente import alta
        if not datos:
            return Resultado(False, "no se eligió ninguna ON")
        # La curva de 1816 sale de lo que el DETECTOR guardó, no de lo que mandó
        # el navegador: es la clasificación con la que se decidió que faltaba.
        curvas = {x.get("ticker"): x.get("curva_1816") for x in self._filas(ev)}
        escritos, errores, pasos = [], [], []
        for d in datos:
            tk = str((d or {}).get("unidad") or "").strip().upper()
            curva = curvas.get(tk) or str((d or {}).get("valor") or "").strip()
            if not tk or not curva:
                errores.append(f"{tk or '?'}: no sé en qué curva de 1816 está")
                continue
            try:
                r = alta.aplicar(tk, curva_1816=curva, actor=por)
            except Exception as e:                                # pragma: no cover
                errores.append(f"{tk}: {type(e).__name__}: {e}"[:160])
                continue
            hecho = bool(r.get("ok") and r.get("aplicado"))
            (escritos if hecho else errores).append(
                tk if hecho else f"{tk}: {r.get('error') or 'la cadena no cerró'}"[:160])
            # El rastro de CADA una, para que «3 sí y 1 no» diga cuál y por qué.
            pasos.append({"titulo": tk, "estado": "ok" if hecho else "falló",
                          "detalle": ((r.get("error") or "")[:300] if not hecho else
                                      "; ".join(x["titulo"] for x in (r.get("pasos") or [])
                                                if x.get("estado") == "ok"))})
        if not escritos:
            return Resultado(False, "no se dio de alta ninguna — "
                             + "; ".join(errores[:4]), pasos=pasos)
        return Resultado(
            True,
            f"{len(escritos)} dada(s) de alta: " + ", ".join(escritos)
            + (f" · {len(errores)} no cerraron: {'; '.join(errores[:3])}"
               if errores else "")
            + " · la TEA aparece al reiniciar motor_rofex + motor_curvas",
            campo=self.campo, donde=self.donde, pasos=pasos,
            antes="no estaban", despues=f"{len(escritos)} ON(s)", inmediato=False)


ARREGLOS: dict[str, Arreglo] = {
    a.id: a for a in (PedirPata(), PataDolar(), ApuntarPata(), AltaFlujos(),
                      AltaBono(), RehacerJob(), CompletarFicha(), ArbitrarCopia(),
                      AltaCedear(), AltaON())
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
        return {"ok": False, "error": r.detalle, "pasos": r.pasos}

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE agente.hallazgos SET estado = %s, "
                    "  arreglo_aplicado = %s WHERE id = %s",
                    (tipos.EN_CURSO, a.id, hallazgo_id))

    # ⚠️ **EL DETECTOR VUELVE A MIRAR AHORA, si su arreglo declaró que puede.**
    #
    # Va DESPUÉS del `UPDATE` a `en_curso` a propósito: si el problema ya no
    # está, `registro._cerrar_ausentes` lo encuentra en ese estado y lo cierra
    # **POR ACCIÓN** —que es lo que hay que anotar, y lo único que después
    # habilita una reincidencia—. Corriéndolo antes, el cierre caería en
    # AUSENCIA y se perdería que fue este botón el que lo resolvió.
    #
    # **Nunca levanta.** Que el refresco falle no puede tirar abajo una
    # escritura que ya pasó: lo peor que puede ocurrir es que la tarjeta siga
    # con el número viejo hasta la próxima pasada, o sea exactamente lo que
    # había antes de esto.
    if a.confirma_ya:
        try:
            from agente import fuentes, motor
            fuentes.refrescar()
            motor.correr_una(h["habilidad"])
        except Exception:
            logger.warning("arreglos: %s escribió, pero no pude refrescar %s",
                           a.id, h["habilidad"], exc_info=True)

    return {"ok": True, "detalle": r.detalle, "estado": tipos.EN_CURSO,
            "inmediato": r.inmediato, "pasos": r.pasos,
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
