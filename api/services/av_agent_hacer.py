"""api/services/av_agent_hacer.py — LO QUE EL AGENTE SABE HACER.

Doc madre: **`docs/AV_AGENT.md`** §0.n.

Pedido del user (2026-08-19): *«que el agente aprenda a sugerir, y que si le das
OK actualice en el momento y luego controle que lo hizo bien en el mismo
proceso… que ya tenga la feature armada pero que se haga con mi permiso»*.

EL CICLO, Y ES UNO SOLO PARA TODO
=================================

    PROPONER  →  (el humano da OK)  →  APLICAR  →  VERIFICAR

Es el MISMO ciclo del alta de un bono (simular → aplicar → verificar). Que sea
uno solo no es prolijidad: el que aprueba un cambio de cartera no tiene que
aprender otro modelo mental que el que aprueba un alta, y el día que se le dé
autonomía a una acción se le da con el mismo interruptor.

CÓMO SE AGREGA UNA ACCIÓN NUEVA
================================

Una clase con tres métodos y una línea en `ACCIONES`. Nada más — ni endpoint, ni
tabla, ni UI:

    class MiAccion:
        id, titulo, sobre, campo, donde   # metadatos
        def proponer(self, casos) -> list[Propuesta]
        def aplicar(self, p) -> None            # escribe por la puerta de la app
        def verificar(self, p) -> tuple[bool, str]

**La escritura va SIEMPRE por la misma puerta que usa la pantalla** (ej.
`assets_sql.set_campos`, la que usa Manager). Un segundo camino de escritura
termina con dos criterios distintos para el mismo dato — es como se llega a que
la mitad de las carteras tengan un espacio al final.

DÓNDE VA LA REGLA Y DÓNDE VA EL LLM
====================================

La regla determinista SIEMPRE primero. El modelo se llama **solo para lo que la
regla no pudo**, y su respuesta entra por el mismo `Propuesta` con
`fuente="ia"` — el humano ve de dónde salió cada una, porque **cambia cuánto hay
que mirarla**: una regla se audita leyendo el código una vez; una sugerencia del
modelo hay que mirarla caso por caso.

Eso no es desconfianza del modelo: es que el 80% de estos casos los resuelve una
regla de tres líneas, y gastar tokens (y atención humana) en eso es tirar los dos
recursos que escasean.
"""
from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Protocol

from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Apaga el modelo para TODA la tanda (ver `_solo_regla`). Es un ContextVar y
# no un global para que dos requests en paralelo no se pisen el interruptor.
_SIN_IA: ContextVar[bool] = ContextVar("av_agent_hacer_sin_ia", default=False)


@dataclass
class Propuesta:
    """UN cambio concreto sobre UN sujeto. Es lo que el humano aprueba.

    `porque` es obligatorio y no decorativo: una propuesta sin motivo no se
    puede aprobar con criterio, solo con fe — y aprobar con fe a escala es
    exactamente lo que este ciclo viene a evitar.
    """
    sujeto: str
    campo: str
    propuesto: str
    porque: str
    antes: str = ""
    fuente: str = "regla"        # regla | ia
    confianza: float = 1.0
    extra: dict = field(default_factory=dict)


class Accion(Protocol):
    id: str
    titulo: str
    sobre: str                   # qué control/caso resuelve
    campo: str
    donde: str                   # la pantalla donde se hace a mano

    def proponer(self, casos: list[dict]) -> list[Propuesta]: ...
    def aplicar(self, p: Propuesta) -> None: ...
    def verificar(self, p: Propuesta) -> tuple[bool, str]: ...


# ── ACCIÓN 1: la CARTERA de un asset ────────────────────────────────────────
#
# El user marcó el patrón sin que nadie se lo dijera: *«OTC TRI DLR… son siempre
# derivados. El X29E7 tiene la palabra CER en el nombre»*. Eso es exactamente
# una regla determinista, y es gratis.

# El vocabulario de CARTERA (CLAUDE.md, «divisor de la valuación»). No es una
# lista de strings: cada valor decide CÓMO se valúa el papel, así que proponer
# uno que no esté acá rompería el AuM en silencio.
CARTERAS = ("HD", "DL", "ARS", "MONEDAS", "FCI", "RENTA VARIABLE",
            "DERIVADOS", "FINANCIAMIENTO")

# Patrones → cartera, en ORDEN: el primero que matchea gana. El orden es la
# regla, no un detalle — «LETRA TESORO … CER» matchea CER y TESORO, y el ajuste
# manda sobre el emisor.
_PATRONES: tuple[tuple[str, str, str], ...] = (
    # OTC / TRI. / MAI. / SOJ. / GIR. abren el NOMBRE (por eso el `^` sobre el
    # nombre ya limpio, no sobre la unidad): son prefijos de contrato, y
    # buscarlos en cualquier posición haría que un "GIR." adentro de una razón
    # social convierta un bono en derivado.
    (r"^(OTC\b|TRI\.|MAI\.|SOJ\.|GIR\.)", "DERIVADOS",
     "los contratos de cámara (OTC/TRI/MAI/SOJ/GIR) son derivados"),
    (r"\bDLR\s*\d", "DERIVADOS", "DLR+vencimiento es un futuro de dólar"),
    # ⚠️ `CAFCI1910` NO tiene borde de palabra después de «CAFCI» (le sigue un
    # dígito, los dos son caracteres de palabra), así que `\bCAFCI\b` no matchea
    # NINGUNA unidad real. El borde va adelante y un dígito atrás.
    (r"\bCAFCI?\d", "FCI", "el código CAFCI lo identifica como FCI"),
    (r"\bCER\b", "ARS", "el nombre dice CER: es un ajustable por CER, en pesos"),
    (r"\bTAMAR\b|\bBADLAR\b|\bTASA\s+FIJA\b", "ARS",
     "TAMAR/BADLAR/tasa fija son instrumentos en pesos"),
    (r"\bLETRA\s+TESORO\b|\bBONCAP\b|\bLECAP\b", "ARS",
     "letras y BONCAP del Tesoro cotizan en pesos"),
)


def _nombre(unidad: str) -> str:
    """La unidad SIN el id de especie de Aunesa (`[42932] OTC SOJ.` → `OTC SOJ.`).

    Sacarlo no es cosmético: los patrones que valen solo al principio del nombre
    (los contratos de cámara) nunca matchearían con `[42932] ` adelante, y el
    número de especie no dice nada sobre qué es el papel — de hecho **cambia**
    cuando Aunesa rebautiza el instrumento."""
    return re.sub(r"^\s*\[\s*\d+\s*\]\s*", "", unidad or "").strip().upper()


class AccionCartera:
    id = "assets.cartera"
    titulo = "Poner la CARTERA que falta"
    sobre = "assets_sin_cartera"
    campo = "CARTERA"
    donde = "Manager → TÍTULOS · ASSETS"

    def proponer(self, casos: list[dict]) -> list[Propuesta]:
        props, sin_regla = [], []
        for c in casos:
            unidad = _sujeto(c)
            if not unidad:
                continue
            u = _nombre(unidad)
            for patron, cartera, porque in _PATRONES:
                if re.search(patron, u):
                    props.append(Propuesta(sujeto=unidad, campo=self.campo,
                                           propuesto=cartera, porque=porque))
                    break
            else:
                sin_regla.append(unidad)
        # Lo que la regla no supo, al modelo — y SOLO eso.
        if sin_regla:
            props.extend(_proponer_con_ia(
                sin_regla, campo=self.campo, opciones=CARTERAS,
                que=("la CARTERA de un título. La cartera decide CÓMO se valúa: "
                     "HD/DL/ARS son renta fija que cotiza en paridad (÷100), "
                     "MONEDAS es efectivo, FCI son fondos, RENTA VARIABLE son "
                     "acciones/CEDEARs, DERIVADOS son futuros y contratos de "
                     "cámara, FINANCIAMIENTO son pagarés y cheques.")))
        return props

    def aplicar(self, p: Propuesta) -> None:
        if p.propuesto not in CARTERAS:
            raise ValueError(f"«{p.propuesto}» no es una cartera válida")
        from api.services import assets_sql
        assets_sql.set_campos(p.sujeto, {"CARTERA": p.propuesto},
                              actor="av-agent")

    def verificar(self, p: Propuesta) -> tuple[bool, str]:
        """**Se relee de la base, no se confía en que el UPDATE salió bien.**
        Escribir y asumir es como se acumulan arreglos que no arreglaron nada."""
        from api.services.assets_sql import asset_one_panel
        row = asset_one_panel(p.sujeto) or {}
        val = str(row.get("CARTERA") or row.get("cartera") or "").strip()
        return (val == p.propuesto,
                f"quedó «{val or '(vacío)'}»" if val != p.propuesto
                else f"CARTERA = {val}")


# ── ACCIÓN 2: el EMISOR / TICKER de un FCI, por parecido ────────────────────

class AccionFci:
    """El user: *«otra funcionalidad que tranquilamente por similitudes el LLM
    puede detectar con otros y sugerir»*. Pero antes que el LLM hay algo más
    barato y más seguro: **el mismo fondo ya está cargado en otra clase**.

    `CAFCI1781-6039 - BAVSA Deuda Privada Argentina USD - Clase B` sin emisor,
    y `…Clase A` del mismo fondo con emisor cargado. El código CAFCI es la
    identidad y la clase es la variante: copiar de un hermano no es una
    inferencia, es un hecho. Es la misma idea de la regla `herencia` de
    `assets_autofill`, aplicada acá.
    """
    id = "assets.fci"
    titulo = "Completar el FCI desde su hermano"
    sobre = "fci_incompletos"
    campo = "EMISOR"
    donde = "Manager → TÍTULOS · ASSETS"

    def proponer(self, casos: list[dict]) -> list[Propuesta]:
        from api.services.assets_sql import assets_rows
        filas = assets_rows(["unidad", "cartera", "emisor", "ticker"]) or []
        # Índice por código CAFCI → los emisores que YA tiene cargados.
        por_cafci: dict[str, set[str]] = {}
        for f in filas:
            cod = _cafci(str(f.get("unidad") or ""))
            em = str(f.get("emisor") or "").strip()
            if cod and em:
                por_cafci.setdefault(cod, set()).add(em)

        props = []
        for c in casos:
            unidad = _sujeto(c)
            cod = _cafci(unidad)
            if not cod:
                continue
            candidatos = por_cafci.get(cod) or set()
            # **Si los hermanos no están de acuerdo, no se propone.** Dos
            # emisores distintos para el mismo CAFCI es un dato roto, no una
            # ambigüedad que se resuelve eligiendo uno.
            if len(candidatos) != 1:
                continue
            emisor = next(iter(candidatos))
            props.append(Propuesta(
                sujeto=unidad, campo=self.campo, propuesto=emisor,
                porque=f"otra clase del MISMO fondo (CAFCI {cod}) ya tiene "
                       f"emisor «{emisor}»"))
        return props

    def aplicar(self, p: Propuesta) -> None:
        from api.services import assets_sql
        assets_sql.set_campos(p.sujeto, {"EMISOR": p.propuesto}, actor="av-agent")

    def verificar(self, p: Propuesta) -> tuple[bool, str]:
        from api.services.assets_sql import asset_one_panel
        row = asset_one_panel(p.sujeto) or {}
        val = str(row.get("EMISOR") or row.get("emisor") or "").strip()
        return val == p.propuesto, f"EMISOR = {val or '(vacío)'}"


def _cafci(unidad: str) -> str:
    """El código CAFCI de la unidad. Es lo que el rebautizo de Aunesa NO toca,
    así que es la identidad estable del fondo (ver la regla `herencia`)."""
    m = re.search(r"(CAFCI\d+|cafc\d+)", unidad or "", re.I)
    return m.group(1).upper().replace("CAFC", "CAFCI").replace("CAFCII", "CAFCI") if m else ""


# ── ACCIÓN 3: dar de alta una CONTRAPARTE que el conciliador ya identificó ──

class AccionContraparte:
    """El control `contrapartes_pendientes` **ya trae la respuesta**: dice
    «cuenta 4351 'BANCO CREDICOOP…' → sugerida: Credicoop (Bancos)». El user:
    *«mismo caso que el de carteras… que se pueda hacer desde acá»*.

    **La sugerencia NO se lee del texto del control, se vuelve a pedir.** El
    detalle guardado en `manager.controles_datos` es de la última corrida del
    cron; parsear ese string sería dar de alta una contraparte con datos de
    ayer, y peor, quedaría atado al formato de una frase. Se llama de nuevo a
    `reconciliar()` —la MISMA función que usa el control y el botón de
    Manager— y se propone sobre lo que dice HOY.
    """
    id = "contrapartes.alta"
    titulo = "Dar de alta la contraparte sugerida"
    sobre = "contrapartes_pendientes"
    campo = "contraparte"
    donde = "Manager → CONTRAPARTES"

    def proponer(self, casos: list[dict]) -> list[Propuesta]:
        from api.services.contrapartes_seg import reconciliar
        vivos = {str(c.get("cuenta")): c for c in
                 (reconciliar(limit=500).get("candidatos") or [])}
        # Los casos del control acotan QUÉ mirar; el conciliador dice QUÉ decir.
        # Un caso que ya no está entre los candidatos es uno que se resolvió
        # solo: no se propone nada sobre él.
        pedidos = {_sujeto(c) for c in casos} or set(vivos)
        props = []
        for cuenta in sorted(pedidos & set(vivos)):
            c = vivos[cuenta]
            cp = str(c.get("contraparte_sugerida") or "").strip()
            seg = str(c.get("segmento_sugerido") or "").strip()
            if not cp:
                continue
            props.append(Propuesta(
                sujeto=cuenta, campo=self.campo, propuesto=cp,
                porque=f"la denominación «{c.get('denominacion', '')}» contiene "
                       f"el nombre de la contraparte {cp}",
                extra={"denominacion": c.get("denominacion") or "",
                       "segmento": seg}))
        return props

    def aplicar(self, p: Propuesta) -> None:
        from api.services.contrapartes_seg import add_contraparte
        r = add_contraparte(cuenta=p.sujeto,
                            denominacion=p.extra.get("denominacion"),
                            contraparte=p.propuesto,
                            segmento=p.extra.get("segmento") or None,
                            actor="av-agent")
        if not r.get("added"):
            raise RuntimeError(r.get("reason") or "el alta no se hizo")

    def verificar(self, p: Propuesta) -> tuple[bool, str]:
        from api.services.contrapartes_seg import listar_contrapartes
        # Se relee del padrón: la contraparte está de alta o no está.
        for r in (listar_contrapartes(q=p.sujeto) or {}).get("contrapartes", []):
            if str(r.get("cuenta")) == p.sujeto:
                val = str(r.get("contraparte") or "").strip()
                return val == p.propuesto, f"contraparte = {val or '(vacía)'}"
        return False, "la cuenta no quedó en el padrón de contrapartes"


# ── ACCIÓN 4: AVISARLE A UNA PERSONA ────────────────────────────────────────

class AccionAvisar:
    """*«Sería tremendo que sepa mandar un ping, una notificación interna a un
    usuario elegido de la plataforma que es el que se encarga de esto»* (user,
    sobre los comitentes sin nivel_1).

    **No se hace una tabla de notificaciones nueva.** Ya existe la lista de
    pendientes del agente (`mercado.av_agent_avisos`): tiene alta, cierre por
    una persona, y ya se dibuja. Lo único que le faltaba era **a quién** —
    columna `para`. Inventar un segundo buzón habría dado dos lugares donde
    mirar lo que hay para hacer, que es exactamente el problema que SALUD vino
    a resolver cuando la observabilidad estaba en seis pantallas.

    Y es UN aviso por control, no uno por caso: 200 pings de «esta cuenta no
    tiene nivel_1» no son 200 avisos, son un aviso ignorado.
    """
    id = "avisar.responsable"
    titulo = "Avisarle a la persona que se encarga"
    sobre = "comitentes_sin_nivel1"
    campo = "para"
    donde = "AV Agent → AVISOS"

    def proponer(self, casos: list[dict]) -> list[Propuesta]:
        """El destinatario **lo elige el humano** — por eso la propuesta nace
        sin valor y la pantalla la completa. Adivinar a quién le toca un tema
        interno no es algo que el nombre del caso pueda decir."""
        if not casos:
            return []
        n = len(casos)
        muestra = ", ".join(_sujeto(c) for c in casos[:5])
        return [Propuesta(
            sujeto=self.sobre, campo=self.campo, propuesto="",
            porque=f"{n} caso/s sin resolver (ej. {muestra})",
            extra={"n": n, "elige_destinatario": True,
                   "que_hacer": f"completar el nivel_1 de {n} comitente/s activo/s",
                   "por_que": "sin nivel_1 quedan fuera de la segmentación y de "
                              "los filtros madre"})]

    def aplicar(self, p: Propuesta) -> None:
        from api.services import av_agent_vista
        email = (p.propuesto or "").strip().lower()
        if "@" not in email:
            raise ValueError("hay que elegir a quién avisarle")
        if not _usuario_existe(email):
            raise ValueError(f"«{email}» no es un usuario de la plataforma")
        # ⚠️ **SE MIRA EL RESULTADO.** `avisar_a` devuelve 0 cuando no creó nada, y
        # acá se ignoraba: la acción decía «listo» con el mensaje sin mandar. Un
        # 0 legítimo existe —ya hay uno abierto para ESA persona— y por eso se
        # distingue releyendo, en vez de tratar todo 0 como error.
        n = av_agent_vista.avisar_a(
            para=email, clave="ping", ticker=f"control:{p.sujeto}",
            que_hacer=str(p.extra.get("que_hacer") or p.porque),
            por_que=str(p.extra.get("por_que") or ""),
            donde=CONTROL_DONDE.get(p.sujeto, ""), por="av-agent")
        if not n and not any(
                a.get("ticker") == f"control:{p.sujeto}"
                for a in av_agent_vista.avisos_de(email)):
            raise RuntimeError(
                f"el aviso no quedó en la bandeja de «{email}» y no hay uno "
                f"abierto suyo sobre esto — no se mandó nada")

    def verificar(self, p: Propuesta) -> tuple[bool, str]:
        from api.services import av_agent_vista
        abiertos = av_agent_vista.avisos_de(str(p.propuesto or "").strip().lower())
        clave = f"control:{p.sujeto}"
        hay = any(a.get("ticker") == clave for a in abiertos)
        return hay, (f"el aviso está en la lista de {p.propuesto}" if hay
                     else "el aviso no quedó registrado")


# Dónde se corrige cada control. Se lee de la ficha que ya tiene SALUD para no
# escribir dos veces la misma frase (y que digan cosas distintas al mes).
def _control_donde() -> dict[str, str]:
    try:
        from api.services.av_agent_salud import CONTROLES
        return {k: v.get("donde", "") for k, v in CONTROLES.items()}
    except Exception:
        return {}


CONTROL_DONDE = _control_donde()


def _sujeto(caso: dict) -> str:
    """La identidad del caso, venga del control (`item`) o de una propuesta."""
    return str(caso.get("item") or caso.get("sujeto") or caso.get("key") or "").strip()


def _usuario_existe(email: str) -> bool:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM manager.manager_users WHERE lower(email) = %s",
                    (email,))
        return cur.fetchone() is not None


class AccionPedirPata:
    """**PEDIRLE A PRIMARY UN SÍMBOLO QUE NADIE ESTÁ ESCUCHANDO** (2026-08-19).

    Nace del incidente del AO29, y es la acción que mejor cierra el ciclo de todo
    este módulo — por un motivo concreto: **es la única cuyo efecto se puede
    verificar en segundos**.

    EL PROBLEMA QUE RESUELVE, QUE NO ERA EL QUE PARECÍA
    ====================================================

    El AO29 mostraba la fila vacía. Se buscó el bug en el bono, en los ejes, en
    los flujos, en la valuación — y estaba todo bien. Lo que pasaba es que
    **nadie le estaba pidiendo el precio a su pata en dólares**, y como
    `market_snapshot` y `timesales` las escribe el motor *solo para lo que
    suscribe*, todas nuestras tablas decían «no cotiza» **por construcción**.

    Preguntarle a esas tablas si un símbolo opera es preguntarle al que no estaba
    escuchando si sonó el teléfono. **La única forma de saberlo es pedirlo.** Se
    pidió: AO29D cotizaba a USD 90,76, y coincidía al centavo con lo que el motor
    venía calculando dividiendo el precio en pesos por el MEP.

    POR QUÉ ESTA ACCIÓN SE PUEDE AUTOMATIZAR Y OTRAS NO
    ===================================================

    La acción hermana —cambiar el símbolo del master— **se dejó sin automatizar a
    propósito**: el motor arma su universo al arrancar, así que se aplicaría, se
    verificaría en verde releyendo la fila, y en la pantalla no cambiaría nada
    hasta la noche. *Una acción que se aplica y no se ve destruye la confianza en
    todas las demás.*

    Esta es lo contrario: el `adhoc_watcher` de `motor_rofex` pollea cada 5
    segundos y las suscripciones de pyRofex son **aditivas** — no rompen nada, no
    hace falta reiniciar, y funciona **en plena rueda**. Se aplica y se ve.

    QUÉ SIGNIFICA «VERIFICADA» ACÁ, QUE NO ES OBVIO
    ================================================

    Verificar que el precio LLEGÓ no siempre es posible en el acto: puede que el
    símbolo no opere hasta las 15. Así que se verifica lo que la acción sí
    controla —**que quedó pedido**— y el detalle dice si el precio ya entró o
    todavía no. **Y eso no es una excusa**: si nunca llega, el hallazgo sigue
    ahí, a la vista, hasta que alguien decida. Lo que cambió es que ahora la
    ausencia de precio *significa algo*, porque estamos escuchando.
    """

    id = "mercado.pedir_pata"
    titulo = "Pedirle el precio a un símbolo que nadie escucha"
    sobre = "patas_sin_precio"
    campo = "suscripción"
    donde = "mercado.adhoc_subscriptions (el motor la levanta en 5s, sin reiniciar)"

    def proponer(self, casos: list[dict]) -> list[Propuesta]:
        props = []
        for c in casos:
            simbolo = (c.get("simbolo") or "").strip()
            ticker = (c.get("ticker") or c.get("key") or "").strip()
            if not simbolo:
                continue
            props.append(Propuesta(
                sujeto=simbolo, campo=self.campo, propuesto="pedir",
                antes="nadie la pide",
                porque=(f"«{simbolo}» es el símbolo con el que el master pide el "
                        f"precio de {ticker} y no tiene ninguno. Eso NO prueba "
                        f"que no cotice: nuestras tablas solo guardan lo que el "
                        f"motor suscribe. Pedirlo es lo único que convierte esa "
                        f"ausencia en un dato — y no hay que reiniciar nada."),
                extra={"ticker": ticker, "curva": c.get("curva")}))
        return props

    def aplicar(self, p: Propuesta) -> None:
        """Por la MISMA puerta que usa la app: `adhoc_subscriptions.subscribe`,
        la que ya pollea el motor. Un segundo camino de suscripción terminaría
        con dos universos que no se hablan — que es justo el bug de origen."""
        from core import adhoc_subscriptions

        # No se inventa un símbolo: tiene que existir como pata conocida.
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM mercado.especies WHERE simbolo = %s",
                        (p.sujeto,))
            if cur.fetchone() is None:
                raise ValueError(f"«{p.sujeto}» no existe en `mercado.especies`")
        r = adhoc_subscriptions.subscribe(p.sujeto)
        if not r.get("ok"):
            raise RuntimeError(f"no se pudo pedir: {r.get('reason')}")

    def verificar(self, p: Propuesta) -> tuple[bool, str]:
        """Se relee de la base. Lo que se exige es que **quede pedido** — que es
        lo que esta acción controla; que el precio llegue lo decide el mercado."""
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM mercado.adhoc_subscriptions "
                        "WHERE ticker = %s AND expires_at > now()", (p.sujeto,))
            pedida = cur.fetchone() is not None
            cur.execute("SELECT last_price FROM mercado.market_snapshot "
                        "WHERE ticker = %s", (p.sujeto,))
            r = cur.fetchone()
            px = r[0] if r else None
        if not pedida:
            return False, "no quedó pedida"
        if px:
            return True, f"pedida y YA llegó precio: {px}"
        return True, ("pedida; todavía sin precio — si pasa una rueda entera y no "
                      "llega, ESA pata no cotiza (antes no se podía afirmar)")


class AccionPataDolar:
    """PEDIR LA PATA EN DÓLARES de un bono que cotiza en pesos.

    Hermana de `AccionPedirPata` y **no la misma**: aquélla pide el símbolo que el
    master ya usa y que no tiene precio; ésta pide **la OTRA pata**, la que está en
    dólares, de un bono cuyo símbolo actual sí cotiza —solo que en pesos—.

    La escritura NO vive acá: delega en `av_agent_pata.pedir`, que es la misma
    función que usa la puerta de la pantalla. Dos caminos a la misma escritura
    terminan siempre con dos criterios que se contradicen — es el bug del blob y
    la columna (§0.u), y no se repite.

    ⚠️ **No cambia el master**, a propósito. Eso exige reiniciar `motor_rofex` y
    no se vería hasta la noche. Esta acción consigue lo anterior y lo verificable:
    que empecemos a escuchar esa pata, para poder decidir con el precio a la vista.
    """

    id = "mercado.pata_dolar"
    titulo = "Pedir la pata en DÓLARES de un bono que cotiza en pesos"
    sobre = "patas_dolar_sin_pedir"
    campo = "suscripción"
    donde = "mercado.adhoc_subscriptions (el motor la levanta en 5s, sin reiniciar)"

    def proponer(self, casos: list[dict]) -> list[Propuesta]:
        props = []
        for c in casos:
            ticker = (c.get("ticker") or c.get("key") or "").strip().upper()
            simbolo = (c.get("simbolo") or "").strip()
            if not ticker:
                continue
            props.append(Propuesta(
                sujeto=ticker, campo=self.campo, propuesto="pedir",
                antes="nadie la pide",
                porque=(f"«{ticker}» es de curva USD y la grilla lo muestra en "
                        f"pesos. Su pata en dólares"
                        + (f" («{simbolo}»)" if simbolo else "")
                        + " no la escucha nadie, así que **no sabemos si opera** "
                          "— y sin ese dato no se puede decidir si vale apuntar "
                          "el master ahí. Pedirla no reinicia nada."),
                extra={"simbolo": simbolo, "curva": c.get("curva")}))
        return props

    def aplicar(self, p: Propuesta) -> None:
        from api.services import av_agent_pata
        r = av_agent_pata.pedir(p.sujeto)
        if not r.get("ok"):
            raise RuntimeError(r.get("error") or "no se pudo pedir la pata")

    def verificar(self, p: Propuesta) -> tuple[bool, str]:
        """Se exige que quede PEDIDA —lo que la acción controla—; que el precio
        llegue lo decide el mercado, y el detalle lo dice sin disfrazarlo."""
        from api.services import av_agent_pata
        d = av_agent_pata.explicar(p.sujeto)
        if not d.get("ok"):
            return False, str(d.get("error") or "no pude releer")
        if not d.get("pedida"):
            return False, "no quedó pedida"
        px = d.get("precio")
        if px:
            return True, f"pedida y YA llegó precio: {px:,.2f}"
        return True, ("pedida; todavía sin precio — si pasa una rueda entera y no "
                      "llega, ESA pata no cotiza (antes no se podía afirmar)")


ACCIONES: dict[str, Accion] = {a.id: a for a in (
    AccionCartera(), AccionFci(), AccionContraparte(), AccionAvisar(),
    AccionPedirPata(), AccionPataDolar())}
# Qué acción resuelve cada control. Sin esto la pantalla tendría que saberlo, y
# el día que se agregue una acción habría que tocar el front.
POR_CONTROL: dict[str, str] = {a.sobre: a.id for a in ACCIONES.values()}


# ── El LLM, solo para lo que la regla no supo ───────────────────────────────

def _proponer_con_ia(sujetos: list[str], *, campo: str, opciones: tuple[str, ...],
                     que: str) -> list[Propuesta]:
    """Le pide al modelo UNA opción de una lista cerrada, por sujeto.

    **La lista cerrada es la mitad del diseño.** Un valor libre puede ser
    plausible y estar mal escrito («Derivados», «DERIVADO»), y eso rompe los
    filtros que comparan exacto sin dar ningún error. Lo que no está en la
    lista se descarta acá — el modelo propone, la lista decide qué es válido.
    """
    if _SIN_IA.get():
        return []
    from core import ai
    tarea = "av_agent_accion"
    if not sujetos or not ai.disponible(tarea):
        return []
    system = (
        f"Sos un analista de datos de una mesa de renta fija argentina. Te paso "
        f"nombres de instrumentos y tenés que decidir {que}\n\n"
        f"RESPONDÉ SOLO un JSON: una lista de objetos "
        f'{{"sujeto": "...", "valor": "...", "porque": "..."}}.\n'
        f"`valor` tiene que ser EXACTAMENTE uno de: {', '.join(opciones)}.\n"
        f"`porque` es una frase corta con la evidencia del NOMBRE que te llevó "
        f"a esa opción.\n\n"
        f"**Si un nombre no te alcanza para decidir, NO lo incluyas en la "
        f"lista.** Es preferible que quede sin propuesta a que alguien apruebe "
        f"una adivinanza: del otro lado hay una persona que confía en que si "
        f"proponés algo es porque el nombre lo dice.")
    txt = ai.completar(tarea, system=system,
                       user="\n".join(f"- {s}" for s in sujetos[:60]),
                       detalle=f"{campo} de {len(sujetos)} sujetos")
    if not txt:
        return []
    try:
        crudo = txt[txt.index("["):txt.rindex("]") + 1]
        datos = json.loads(crudo)
    except (ValueError, json.JSONDecodeError):
        logger.warning("av_agent_hacer: el modelo no devolvió JSON usable")
        return []
    validos = {s.upper(): s for s in sujetos}
    out = []
    for d in datos if isinstance(datos, list) else []:
        suj, val = str(d.get("sujeto") or ""), str(d.get("valor") or "").strip()
        # Doble guarda: el sujeto tiene que ser uno de los que mandamos (el
        # modelo puede inventar uno) y el valor uno de la lista cerrada.
        real = validos.get(suj.upper())
        if not real or val not in opciones:
            continue
        out.append(Propuesta(sujeto=real, campo=campo, propuesto=val,
                             porque=str(d.get("porque") or "").strip()[:300],
                             fuente="ia", confianza=0.6))
    return out


# ═══════════════════════════════════════════════════════════════════════════
# LA PERSISTENCIA — por qué la propuesta se guarda en vez de aplicarse de una
# ═══════════════════════════════════════════════════════════════════════════
#
# Podría no haber tabla: proponer, mostrar, y que el botón APLICAR mande el
# valor de vuelta. Se guarda por cuatro razones concretas:
#
#   · el OK del humano es el único gate real, y sin fila no hay a qué darle OK
#     sin que el front se convierta en la fuente de verdad de lo que se propuso;
#   · `antes` congela el valor previo → «revertir» puede ser una función y no
#     una promesa;
#   · `verificado` separa «lo escribí» de «quedó bien» — son cosas distintas y
#     confundirlas es como se acumulan arreglos que no arreglaron nada;
#   · **cada propuesta con su resultado ES el eval set de las acciones.** Sin
#     esto no hay forma de saber si el agente sugiere bien, y sin eso no se le
#     puede dar más autonomía sin fe. Es el mismo criterio que gobierna el resto
#     del programa: la autonomía se gana con evidencia medida, no se declara.

_COLS = ("id", "creado_at", "accion", "sujeto", "campo", "antes", "propuesto",
         "fuente", "confianza", "porque", "estado", "por", "aplicado_at",
         "verificado", "verificado_detalle", "error")


def _filas(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _serializar(f: dict) -> dict:
    """La fila, lista para el front. `creado_at` a ISO y `confianza` a float —
    un `Decimal` no es serializable a JSON y revienta el endpoint entero."""
    out = dict(f)
    for k in ("creado_at", "aplicado_at"):
        v = out.get(k)
        out[k] = v.isoformat() if hasattr(v, "isoformat") else (v or None)
    c = out.get("confianza")
    out["confianza"] = float(c) if c is not None else None
    return out


def catalogo() -> list[dict]:
    """Qué sabe hacer el agente. Lo consume la pantalla para saber si un control
    tiene acción — sin esto el front tendría que llevar su propia lista y
    quedaría vieja el día que se agregue una acción."""
    return [{"id": a.id, "titulo": a.titulo, "sobre": a.sobre, "campo": a.campo,
             "donde": a.donde} for a in ACCIONES.values()]


def proponer(accion_id: str, *, casos: list[dict] | None = None,
             con_ia: bool = True) -> dict:
    """Corre la acción y **persiste lo que propone**, sin tocar nada más.

    Si `casos` no viene, los saca del control **volviendo a correrlo**, no de la
    última corrida guardada: proponer un cambio sobre un caso que ya se
    resolvió es exactamente el «cementerio de avisos viejos» que el user
    rechazó. Cuesta una corrida de un invariante — barato al lado de aprobar
    algo que no existe.
    """
    a = ACCIONES.get(accion_id)
    if a is None:
        return {"ok": False, "error": f"no existe la acción «{accion_id}»"}
    if casos is None:
        casos, err = _casos_frescos(a.sobre)
        if err:
            return {"ok": False, "error": err}
    try:
        props = a.proponer(casos) if con_ia else _solo_regla(a, casos)
    except Exception as e:
        logger.warning("av_agent_hacer: %s no pudo proponer: %s", accion_id, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    n = _guardar(accion_id, props)
    con_ia_n = sum(1 for p in props if p.fuente == "ia")
    return {"ok": True, "accion": accion_id, "propuestas": n,
            "casos": len(casos), "por_regla": n - con_ia_n, "por_ia": con_ia_n,
            "pendientes": pendientes(accion_id)}


def _solo_regla(a: Accion, casos: list[dict]) -> list[Propuesta]:
    """Propone SIN gastar un token.

    El interruptor está en `_proponer_con_ia` y no adentro de cada acción a
    propósito: si cada una tuviera que acordarse de mirar el flag, la que se
    olvide gasta tokens con el modelo apagado y nadie se entera hasta ver la
    factura. Acá el olvido es imposible — el único camino al modelo pasa por
    esa función."""
    tok = _SIN_IA.set(True)
    try:
        return a.proponer(casos)
    finally:
        _SIN_IA.reset(tok)


def _casos_frescos(control_id: str) -> tuple[list[dict], str]:
    """Vuelve a correr el control y devuelve lo que sigue mal AHORA."""
    try:
        from jobs.controles_datos import CONTROLES as REGISTRO
    except Exception as e:
        return [], f"no se pudo cargar el control: {e}"
    ctl = next((c for c in REGISTRO if c.id == control_id), None)
    if ctl is None:
        return [], f"no existe el control «{control_id}»"
    try:
        return list(ctl.fn() or []), ""
    except Exception as e:
        return [], f"el control falló: {type(e).__name__}: {e}"


def _guardar(accion_id: str, props: list[Propuesta]) -> int:
    """Upsert por (accion, sujeto, campo). **Re-proponer sobre el mismo sujeto
    ACTUALIZA la propuesta en vez de acumular diez para el mismo asset** — y
    resetea el estado a `propuesta`, porque una propuesta nueva sobre algo ya
    aplicado significa que el problema volvió y hay que volver a aprobarlo."""
    if not props:
        return 0
    filas = [(accion_id, p.sujeto, p.campo, p.antes or None, p.propuesto,
              p.fuente, p.confianza, p.porque[:1000],
              json.dumps(p.extra or {}, ensure_ascii=False))
             for p in props]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO mercado.av_agent_propuestas "
            "(accion, sujeto, campo, antes, propuesto, fuente, confianza, "
            " porque, extra) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
            "ON CONFLICT (accion, sujeto, campo) DO UPDATE SET "
            "  propuesto = EXCLUDED.propuesto, antes = EXCLUDED.antes, "
            "  fuente = EXCLUDED.fuente, confianza = EXCLUDED.confianza, "
            "  porque = EXCLUDED.porque, extra = EXCLUDED.extra, "
            "  creado_at = now(), estado = 'propuesta', "
            "  verificado = NULL, verificado_detalle = NULL, error = NULL",
            filas)
    return len(filas)


def pendientes(accion_id: str = "", limite: int = 300) -> list[dict]:
    """Lo que espera OK. Es la lista que se dibuja."""
    where = "estado = 'propuesta'" + (" AND accion = %s" if accion_id else "")
    params = (accion_id, limite) if accion_id else (limite,)
    return [_serializar(f) for f in _filas(
        f"SELECT {', '.join(_COLS)}, extra FROM mercado.av_agent_propuestas "
        f"WHERE {where} ORDER BY fuente, sujeto LIMIT %s", params)]


def historial(accion_id: str = "", limite: int = 100) -> list[dict]:
    """Lo ya decidido — **incluido lo que salió mal.** Un libro que solo guarda
    los aciertos no sirve para medir nada."""
    where = "estado <> 'propuesta'" + (" AND accion = %s" if accion_id else "")
    params = (accion_id, limite) if accion_id else (limite,)
    return [_serializar(f) for f in _filas(
        f"SELECT {', '.join(_COLS)} FROM mercado.av_agent_propuestas "
        f"WHERE {where} ORDER BY aplicado_at DESC NULLS LAST, id DESC LIMIT %s",
        params)]


def aplicar(ids: list[int], *, por: str = "", valores: dict | None = None) -> dict:
    """**El OK.** Escribe y verifica EN EL MISMO PASO, propuesta por propuesta.

    *«Que si le das OK actualice en el momento y luego controle que lo hizo bien
    en el mismo proceso»* (user). No son dos botones ni dos jobs: verificar
    después, o en otra corrida, es lo mismo que no verificar — nadie vuelve.

    Cada propuesta se aplica **sola**: una que falla no arrastra a las otras.
    Aprobar diez y que se caigan las diez porque la séptima tenía un dato roto
    es la forma más rápida de que nadie vuelva a apretar el botón.

    `valores` permite pisar el valor propuesto por propuesta (`{id: valor}`) —
    es lo que hace que el humano pueda **corregir** la sugerencia en vez de
    tener que elegir entre aceptarla tal cual o descartarla.
    """
    if not ids:
        return {"ok": False, "error": "no se pasó ninguna propuesta"}
    filas = _filas(
        f"SELECT {', '.join(_COLS)}, extra FROM mercado.av_agent_propuestas "
        "WHERE id = ANY(%s)", (list(ids),))
    if not filas:
        return {"ok": False, "error": "esas propuestas ya no existen"}
    res = []
    for f in filas:
        res.append(_aplicar_una(f, por=por, valores=valores or {}))
    hechas = sum(1 for r in res if r["ok"])
    return {"ok": True, "aplicadas": hechas, "fallidas": len(res) - hechas,
            "resultados": res,
            "texto": _texto_resultado(hechas, len(res) - hechas)}


def _texto_resultado(hechas: int, fallidas: int) -> str:
    if not fallidas:
        return f"{hechas} aplicada/s y verificada/s."
    if not hechas:
        return f"ninguna se pudo aplicar ({fallidas} con error)."
    return f"{hechas} aplicada/s y verificada/s · {fallidas} con error."


def _aplicar_una(f: dict, *, por: str, valores: dict) -> dict:
    """Aplicar → RELEER → sellar. El orden importa y el releer no es opcional.

    Si la verificación dice que NO quedó, el estado es **`fallida`**, no
    `aplicada`. Marcar aplicado algo que no se puede comprobar es cómo un
    tablero termina diciendo que todo está bien mientras el dato sigue roto —
    el mismo incidente que dio origen a SALUD.
    """
    a = ACCIONES.get(f["accion"])
    if a is None:
        return {"id": f["id"], "ok": False, "error": "acción desconocida"}
    p = Propuesta(sujeto=f["sujeto"], campo=f["campo"],
                  propuesto=str(valores.get(f["id"], valores.get(str(f["id"]),
                                f["propuesto"])) or ""),
                  porque=f["porque"] or "", antes=f["antes"] or "",
                  fuente=f["fuente"], extra=f.get("extra") or {})
    try:
        a.aplicar(p)
    except Exception as e:
        _sellar(f["id"], estado="fallida", por=por, error=f"{type(e).__name__}: {e}")
        return {"id": f["id"], "sujeto": p.sujeto, "ok": False,
                "error": f"{type(e).__name__}: {e}"}
    try:
        quedo, detalle = a.verificar(p)
    except Exception as e:
        quedo, detalle = False, f"no se pudo verificar: {type(e).__name__}: {e}"
    _sellar(f["id"], estado="aplicada" if quedo else "fallida", por=por,
            verificado=quedo, detalle=detalle,
            propuesto=p.propuesto if p.propuesto != f["propuesto"] else None,
            error=None if quedo else "aplicado pero la verificación no lo confirma")
    return {"id": f["id"], "sujeto": p.sujeto, "campo": p.campo,
            "valor": p.propuesto, "ok": quedo, "verificado": quedo,
            "detalle": detalle}


def _sellar(pid: int, *, estado: str, por: str = "", verificado=None,
            detalle: str = "", error: str | None = None,
            propuesto: str | None = None) -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mercado.av_agent_propuestas SET estado = %s, por = %s, "
            "aplicado_at = now(), verificado = %s, verificado_detalle = %s, "
            "error = %s, propuesto = COALESCE(%s, propuesto) WHERE id = %s",
            (estado, por or None, verificado, (detalle or "")[:500], error,
             propuesto, pid))


def rechazar(ids: list[int], *, por: str = "") -> dict:
    """**Decir que no también es información.** Una propuesta rechazada mide
    tanto como una aplicada: es lo que dice que la regla (o el modelo) sugirió
    algo que un humano no compró. Sin registrarlo, el agente parecería tener
    100% de acierto para siempre."""
    if not ids:
        return {"ok": False, "error": "no se pasó ninguna propuesta"}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mercado.av_agent_propuestas SET estado = 'rechazada', "
            "por = %s, aplicado_at = now() WHERE id = ANY(%s) "
            "AND estado = 'propuesta'", (por or None, list(ids)))
        n = cur.rowcount
    return {"ok": True, "rechazadas": n, "texto": f"{n} descartada/s."}


def resumen(accion_id: str = "") -> dict:
    """Cómo viene la acción: cuántas propuso, cuántas se aprobaron y **cuántas
    de las aprobadas quedaron verificadas**. Es la métrica que habilita (o no)
    darle más autonomía a esta acción — la misma vara del eval set."""
    where = ("WHERE accion = %s", (accion_id,)) if accion_id else ("", ())
    f = _filas(
        "SELECT count(*) FILTER (WHERE estado = 'propuesta')  AS pendientes, "
        "       count(*) FILTER (WHERE estado = 'aplicada')   AS aplicadas, "
        "       count(*) FILTER (WHERE estado = 'rechazada')  AS rechazadas, "
        "       count(*) FILTER (WHERE estado = 'fallida')    AS fallidas, "
        "       count(*) FILTER (WHERE fuente = 'ia')         AS de_ia "
        f"FROM mercado.av_agent_propuestas {where[0]}", where[1])
    r = {k: int(v or 0) for k, v in (f[0] if f else {}).items()}
    decididas = r.get("aplicadas", 0) + r.get("rechazadas", 0)
    r["acierto"] = (round(r.get("aplicadas", 0) / decididas, 3)
                    if decididas else None)
    return r
