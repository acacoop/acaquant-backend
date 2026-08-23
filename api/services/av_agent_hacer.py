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
    # ⚠️ **LA CAUSA DEL EVAL SET**, que NO siempre es `sobre`. `sobre` es el id
    # del CONTROL (`patas_equivocadas`, plural) y la causa es la REGLA que emite
    # el detector y sobre la que vota un humano en ENCONTRÓ (`pata_equivocada`,
    # singular). Si el voto derivado y el humano usaran claves distintas,
    # medirían la misma cosa por separado y ninguna de las dos llegaría nunca al
    # mínimo de votos — la divergencia silenciosa de REGLA #9, aplicada a la
    # única compuerta que habilita autonomía.
    #
    # Vacío = se usa `sobre`. Es lo correcto para las acciones que nacen de un
    # control y NO tienen un detector espejo: ahí el control ES la causa.
    causa: str



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
    # ⚠️ r-string: el docstring CITA una regex (`\bDLR\s*\d`) para explicar por
    # qué ese patrón matcheaba de casualidad. Sin la `r`, Python 3.12 lo canta
    # como `SyntaxWarning: invalid escape sequence` en CADA arranque de la API
    # y en cada job — ruido permanente en los logs por un comentario.
    r"""El nombre del papel, sin los corchetes de Aunesa. **Hay DOS formas.**

        [42932] OTC SOJ.        el corchete es un PREFIJO: el id de especie
        [OTC - MAI.ROS/ENE27]   el corchete ENVUELVE al nombre entero

    ⚠️ **Esto solo sacaba la primera, y por eso el agente clasificaba UN caso de
    19.** El user, mirando la propuesta (2026-08-21): *«no entiendo cómo
    clasifica a uno solo como OTC si hay un montón así, y las commodities
    también, que eran derivados»*.

    La causa, medida: los patrones de contrato de cámara están anclados con `^`
    —a propósito, para que un «GIR.» adentro de una razón social no convierta un
    bono en derivado— y con el `[` adelante **ninguno puede matchear**. El único
    que salió fue `[OTC - DLR052027]`, y **de casualidad**: pegó con
    `\bDLR\s*\d`, que es el único patrón sin ancla.

    Medido sobre los 19 casos reales: 1 clasificado, 8 contratos de cámara
    perdidos. No fallaba nada — simplemente el `^` nunca llegaba a la letra.
    """
    u = (unidad or "").strip()
    # (1) el corchete que envuelve TODO
    if u.startswith("[") and u.endswith("]") and u.count("[") == 1:
        u = u[1:-1]
    # (2) el corchete de PREFIJO con el id de especie
    u = re.sub(r"^\s*\[\s*\d+\s*\]\s*", "", u)
    # (3) y el corchete de prefijo con el nombre adentro
    #     (`[OTC - MAI.ROS/ENE27] algo más`)
    u = re.sub(r"^\s*\[\s*([^\]]{1,60})\s*\]\s*", r"\1 ", u)
    return u.strip().upper()


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
                              actor="av-agent", crear=False)

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
        # ⚠️ **EN MAYÚSCULA.** `assets_rows` proyecta con claves UPPERCASE (shape
        # heredado de Mongo) y acá se pedían en minúscula: `KeyError: 'cartera'`
        # adentro de una comprehension, o sea que **la acción entera explotaba**
        # y el informe decía «no pude proponer» sin más. Se descubrió recién al
        # correrla desde `scripts/agente_aplicar` — por la pantalla nadie la
        # había apretado nunca. `unidad` va sola, no se pide.
        filas = assets_rows(["EMISOR"]) or []
        # Índice por código CAFCI → los emisores que YA tiene cargados.
        por_cafci: dict[str, set[str]] = {}
        for f in filas:
            cod = _cafci(str(f.get("unidad") or ""))
            em = str(f.get("EMISOR") or "").strip()
            if cod and em:
                por_cafci.setdefault(cod, set()).add(em)

        props = []
        for c in casos:
            unidad = _sujeto(c)
            # ⚠️ **EL CONTROL YA DICE QUÉ FALTA, y este gate lo ignoraba**
            # (incidente 2026-08-23): a un caso «FCI sin ticker» le proponía
            # EMISOR — un campo que esa fila ya tenía cargado. El detalle del
            # control nombra el campo faltante; este gate solo sabe completar
            # EMISOR, así que a los casos que no piden emisor no les ofrece
            # nada (mentirles un arreglo es peor que decir «esto no lo sé»).
            if "emisor" not in str(c.get("detalle") or "").lower():
                continue
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
        assets_sql.set_campos(p.sujeto, {"EMISOR": p.propuesto}, actor="av-agent",
                              crear=False)

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
            # ⚠️ **SIN SEGMENTO NO SE DA DE ALTA SOLA.** El user (2026-08-21):
            # *«no toma en cuenta todos los casilleros de clasificar una
            # contraparte, no pidió si es fondo, ALYC o qué — le falta
            # contexto»*. Y tenía razón: `BCO CREDICOOP TERCEROS` vino con
            # `segmento=None` y se dio de alta igual, **sin clasificar**.
            #
            # Una contraparte sin segmento no está dada de alta: está a medias.
            # No rompe nada hoy y es exactamente el hallazgo de mañana — o peor,
            # cuenta mal en cualquier reporte que agrupe por segmento y nadie se
            # entera, que es REGLA #9 otra vez.
            #
            # El caso NO desaparece: se propone **con el valor vacío**, así sale
            # en la lista pidiendo que una persona lo complete. Esconderlo sería
            # peor que darlo de alta mal.
            props.append(Propuesta(
                sujeto=cuenta, campo=self.campo,
                propuesto=f"{cp} · {seg}" if seg else "",
                porque=(f"la denominación «{c.get('denominacion', '')}» dice que "
                        f"es **{cp}**"
                        + (f", del segmento **{seg}**." if seg else
                           ". ⚠️ **Falta el SEGMENTO** (Fondos · ALYC · Bancos · "
                           "Aseguradoras): el conciliador no lo pudo deducir y "
                           "sin eso la contraparte queda sin clasificar. "
                           "Completalo y aplicá.")
                        + " El código MAE no se puede adivinar —lo asigna el "
                          "MAE— y se carga después en Manager → CONTRAPARTES."),
                extra={"denominacion": c.get("denominacion") or "",
                       "contraparte": cp, "segmento": seg}))
        return props

    def aplicar(self, p: Propuesta) -> None:
        from api.services.contrapartes_seg import add_contraparte

        # El valor viaja como `Nombre · Segmento` — un solo campo editable, para
        # que el humano pueda corregir LOS DOS antes de aplicar. Si lo dejó
        # vacío o sin segmento, no se escribe: dar de alta a medias es crear el
        # hallazgo de mañana.
        cp, seg = self._partir(p.propuesto)
        if not cp:
            raise RuntimeError("falta el nombre de la contraparte")
        if not seg:
            raise RuntimeError(
                "falta el SEGMENTO (Fondos · ALYC · Bancos · Aseguradoras): "
                "escribilo como «Credicoop · Bancos» y aplicá de nuevo")
        r = add_contraparte(cuenta=p.sujeto,
                            denominacion=p.extra.get("denominacion"),
                            contraparte=cp, segmento=seg, actor="av-agent")
        if not r.get("added"):
            raise RuntimeError(r.get("reason") or "el alta no se hizo")

    @staticmethod
    def _partir(valor: str) -> tuple[str, str]:
        """`«Credicoop · Bancos»` → `("Credicoop", "Bancos")`. Acepta `·`, `|`
        y `-` porque el que corrige a mano no tiene por qué acertar el separador."""
        for sep in ("·", "|", " - "):
            if sep in (valor or ""):
                a, _, b = (valor or "").partition(sep)
                return a.strip(), b.strip()
        return (valor or "").strip(), ""

    def verificar(self, p: Propuesta) -> tuple[bool, str]:
        from api.services.contrapartes_seg import listar_contrapartes

        # ⚠️ **Se verifica el NOMBRE Y EL SEGMENTO.** Antes solo miraba el
        # nombre, así que un alta sin clasificar salía «verificado» — el libro
        # decía «contraparte = Credicoop» y la contraparte estaba a medias.
        cp, seg = self._partir(p.propuesto)
        for r in (listar_contrapartes(q=p.sujeto) or {}).get("contrapartes", []):
            if str(r.get("cuenta")) == p.sujeto:
                val = str(r.get("contraparte") or "").strip()
                vseg = str(r.get("segmento") or "").strip()
                if not vseg:
                    return False, (f"quedó «{val}» pero SIN SEGMENTO: la "
                                   f"contraparte está a medias")
                return (val == cp and vseg == seg), f"{val} · {vseg}"
        return False, "la cuenta no quedó en el padrón de contrapartes"


# ── ACCIÓN 4: AVISARLE A UNA PERSONA ────────────────────────────────────────

class AccionAvisar:
    """*«Sería tremendo que sepa mandar un ping, una notificación interna a un
    usuario elegido de la plataforma que es el que se encarga de esto»* (user,
    sobre los comitentes sin nivel_1).

    **No se hace una tabla de notificaciones nueva.** Ya existe la lista de
    pendientes del agente (`agente.av_agent_avisos`): tiene alta, cierre por
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
    """La identidad del caso, venga del control (`item`) o de una propuesta.

    ⚠️⚠️ **SIN `.strip()` — la identidad es el string EXACTO** (incidente
    2026-08-22, REGLA #9). El control canta la `unidad` tal cual está en la PK
    de `portafolio.assets`, y hay unidades reales con espacio al final.
    Stripear acá hacía que la propuesta naciera con OTRA identidad: el UPSERT
    de `set_campos` creaba una fila FANTASMA trimmeada, la verificación la
    releía en verde, y el control seguía cantando la fila real — para siempre.
    Para MOSTRAR un nombre limpio está `_nombre()`; la identidad no se toca.
    """
    return str(caso.get("item") or caso.get("sujeto") or caso.get("key") or "")


def _usuario_existe(email: str) -> bool:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM manager.manager_users WHERE lower(email) = %s",
                    (email,))
        return cur.fetchone() is not None


class Seguible:
    """Acciones cuyo EFECTO no se puede medir en el acto.

    ⚠️ **El bug que esto arregla, y que vale más que el arreglo** (user,
    2026-08-20): *«eso es SUPER INMEDIATO, no es ni 1 seg… me resulta raro»*.
    `verificar()` corría cero segundos después de `aplicar()` y el motor levanta
    la suscripción a los 5 s, así que «todavía sin precio» era la única frase que
    la función podía devolver — siempre, para todos los casos. **Un chequeo con
    una sola respuesta posible no es un chequeo: es un cartel**, y engaña más que
    no verificar porque parece que verificó.

    Son DOS preguntas y se contestaban como una:

        ¿la acción hizo lo suyo?   ¿quedó pedida?     → al instante (`verificar`)
        ¿y lo que abría?           ¿esa pata cotiza?  → lo dice el mercado (`veredicto`)

    `espera_s` es cuánto **mercado abierto** hace falta para que un «no» sea una
    respuesta y no una impaciencia. Lo relee `av_agent_respuesta` desde el
    monitor de rueda.
    """

    # Una rueda arranca 13 UTC y la mesa mira estos bonos todo el día. 45 minutos
    # de mercado abierto sin una sola punta, estando suscriptos, ya es iliquidez
    # y no un hueco: son nueve pasadas del monitor.
    espera_s = 45 * 60

    def veredicto(self, p: Propuesta) -> tuple[bool | None, str]:
        """`True` cotiza · `False` no puede cotizar · `None` todavía no se sabe.

        **`None` no es un error**: es la respuesta honesta mientras el mercado no
        contestó. Quien decide cuándo se agotó la espera es el seguimiento, no la
        acción — la acción dice lo que ve, no cuánto hay que aguantar.
        """
        raise NotImplementedError


class AccionPedirPata(Seguible):
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
    causa = "sin_punta"
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
        """SOLO lo que la acción controla: que quede pedida. **El precio no se
        mira acá** — se lo pregunta `veredicto()` cuando haya pasado tiempo."""
        if not self._pedida(p.sujeto):
            return False, "no quedó pedida"
        return True, "pedida — el motor la levanta en 5 s"

    def veredicto(self, p: Propuesta) -> tuple[bool | None, str]:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT last_price, updated_at FROM mercado.market_snapshot "
                        "WHERE ticker = %s", (p.sujeto,))
            r = cur.fetchone()
        px = r[0] if r else None
        if px:
            return True, f"llegó precio {float(px):,.2f}"
        return None, "todavía sin punta"

    @staticmethod
    def _pedida(simbolo: str) -> bool:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM mercado.adhoc_subscriptions "
                        "WHERE ticker = %s AND expires_at > now()", (simbolo,))
            return cur.fetchone() is not None


class AccionPataDolar(Seguible):
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
    causa = "cotiza_en_pesos"
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
        """SOLO que quede PEDIDA, que es lo que la acción controla. Si esa pata
        cotiza o no lo contesta `veredicto()`, con el mercado abierto y tiempo."""
        from api.services import av_agent_pata
        d = av_agent_pata.explicar(p.sujeto)
        if not d.get("ok"):
            return False, str(d.get("error") or "no pude releer")
        if not d.get("pedida"):
            return False, "no quedó pedida"
        return True, "pedida — el motor la levanta en 5 s"

    def veredicto(self, p: Propuesta) -> tuple[bool | None, str]:
        from api.services import av_agent_pata
        d = av_agent_pata.explicar(p.sujeto)
        if not d.get("ok"):
            return None, "no pude releer"
        px = d.get("precio")
        if px:
            return True, f"llegó precio {float(px):,.2f}"
        # ⚠️ Si dejamos de escucharla, el «no vino punta» no prueba nada: es el
        # error del AO29 otra vez (§0.v). Mejor seguir esperando que contar una
        # ausencia que nadie estaba midiendo.
        if not d.get("escuchando"):
            return None, "dejamos de escucharla"
        return None, "todavía sin punta"


class AccionApuntarPata(Seguible):
    """**APUNTAR EL MASTER A LA PATA CORRECTA.** El arreglo de verdad.

    ⚠️ **POR QUÉ EXISTE, y por qué antes no** (user, 2026-08-20, sobre los
    BOPREALes: *«estos siguen apareciendo, es algo de no creer; necesito de una
    vez por todas que esto se solucione»*).

    Tenía razón y el problema era estructural: **`pata_equivocada` no tenía
    ninguna acción que lo arreglara.** La única puerta era `mercado.pata_dolar`,
    que PIDE la pata en dólares — cosa útil, pero que no toca
    `mercado.curvas.instrumento`. O sea que el master seguía apuntando a la pata
    en pesos, el detector lo volvía a ver en la pasada siguiente, y el hallazgo
    reaparecía **todas las ruedas, para siempre**. Marcar «acertó» no lo cerraba:
    el agente había acertado, y aun así nadie podía hacer nada.

    > **Un hallazgo sin arreglo posible no es un aviso: es una pared.** Y una
    > pared que aparece todos los días enseña a ignorar la lista entera — el
    > mismo daño que hacían los 46 falsos positivos de §0.u, por el camino
    > contrario.

    LO QUE FRENABA AUTOMATIZARLO, Y CÓMO SE RESUELVE
    ================================================

    La objeción original (§0.u) era buena: **el motor arma su universo al
    arrancar**, así que cambiar el campo no se ve hasta reiniciarlo fuera de
    rueda, y *una acción que se aplica y no se ve destruye la confianza en todas
    las demás*.

    Se resuelve haciendo **las dos cosas en el mismo paso**:

        1. se corrige el master  → `mercado.curvas.instrumento` = la pata buena
        2. se PIDE esa pata      → `adhoc_subscriptions`, que el `adhoc_watcher`
                                    levanta en 5 s, sin reiniciar, en plena rueda

    Con las dos: el precio aparece en el acto (por el adhoc), la grilla lo
    muestra en dólares (la vista joinea por la columna) y el master ya quedó bien
    para el próximo arranque. **El hallazgo desaparece en la pasada siguiente**,
    que es lo único que el user pidió.

    ⚠️ **Se escriben LAS DOS COPIAS del símbolo** (la columna y la clave del blob)
    en el MISMO `UPDATE`. `curvas_sql` hace ganar a la columna al leer, así que
    con una alcanzaría — pero dejar el blob diciendo otra cosa es volver a crear
    la divergencia que costó cuatro días (REGLA #9 B). Se arregla el duplicado,
    no se confía en el árbitro.
    """

    id = "mercado.apuntar_pata"
    # El control se llama `patas_equivocadas` y el detector emite
    # `pata_equivocada`. Son los MISMOS bonos: sin esta línea, los 17 votos
    # humanos de los BOPREALes y los votos derivados de sus arreglos irían a dos
    # causas distintas y ninguna juntaría evidencia.
    causa = "pata_equivocada"
    titulo = "Apuntar el master a la pata correcta (y pedirla)"
    sobre = "patas_equivocadas"
    campo = "mercado.curvas.instrumento"
    donde = "mercado.curvas + adhoc_subscriptions (se ve en 5s, sin reiniciar)"

    def proponer(self, casos: list[dict]) -> list[Propuesta]:
        props = []
        for c in casos:
            ticker = (c.get("ticker") or c.get("key") or "").strip().upper()
            actual = (c.get("simbolo") or "").strip()
            sugerido = (c.get("sugerido") or "").strip()
            # **Sin la pata sugerida no se propone nada.** Adivinarla por sufijo
            # es justo lo que REGLA #9(A) prohíbe: `BPOA7 → BPA7D` se come una
            # letra del medio y ninguna regla de string la saca.
            if not ticker or not sugerido or sugerido == actual:
                continue
            props.append(Propuesta(
                sujeto=ticker, campo=self.campo, propuesto=sugerido,
                antes=actual or "—",
                porque=(f"El master de «{ticker}» suscribe «{_sym(actual)}», que "
                        f"cotiza en PESOS, y la pata correcta es "
                        f"«{_sym(sugerido)}» (la que `mercado.especies` marca "
                        f"por default). Por eso la grilla lo muestra en pesos al "
                        f"lado de bonos en dólares. Se corrige el campo Y se pide "
                        f"la pata en el mismo paso: el precio entra en 5 s sin "
                        f"reiniciar nada."),
                extra={"actual": actual, "curva": c.get("curva")}))
        return props

    def aplicar(self, p: Propuesta) -> None:
        from core import adhoc_subscriptions, curvas_sql
        from core.postgres import get_pool

        # No se inventa un símbolo: tiene que existir como pata conocida.
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM mercado.especies WHERE simbolo = %s",
                        (p.propuesto,))
            if cur.fetchone() is None:
                raise ValueError(f"«{p.propuesto}» no existe en `mercado.especies`")
            # LAS DOS COPIAS, en un solo UPDATE. La clave del blob se llama
            # `ticker` y guarda el SÍMBOLO (el nombre viejo, cruzado) — ver el
            # renombre de 2026-08-15 en CLAUDE.md.
            cur.execute(
                "UPDATE mercado.curvas "
                "   SET instrumento = %(s)s, "
                "       data = jsonb_set(COALESCE(data, '{}'::jsonb), "
                "                        '{ticker}', to_jsonb(%(s)s::text)) "
                " WHERE upper(ticker) = %(t)s",
                {"s": p.propuesto, "t": p.sujeto})
            if cur.rowcount != 1:
                raise ValueError(f"«{p.sujeto}» no está en `mercado.curvas` "
                                 f"(filas tocadas: {cur.rowcount})")
            conn.commit()
        # El master está cacheado 
        curvas_sql.invalidar()

        # Y ahora lo que hace que se VEA: el motor no relee su universo, pero el
        # `adhoc_watcher` sí pollea esta tabla cada 5 s.
        r = adhoc_subscriptions.subscribe(p.propuesto)
        if not r.get("ok"):
            raise RuntimeError(
                f"el master quedó apuntado a «{_sym(p.propuesto)}» pero no se "
                f"pudo pedir: {r.get('reason')}. Sin la suscripción el precio no "
                f"llega hasta el próximo reinicio del motor.")

    def verificar(self, p: Propuesta) -> tuple[bool, str]:
        """Lo que la acción controla: **el campo quedó escrito y la pata pedida**.
        Si esa pata da precio lo contesta el mercado — `veredicto()`."""
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT instrumento, data->>'ticker' FROM mercado.curvas "
                        "WHERE upper(ticker) = %s", (p.sujeto,))
            r = cur.fetchone()
            cur.execute("SELECT 1 FROM mercado.adhoc_subscriptions "
                        "WHERE ticker = %s AND expires_at > now()", (p.propuesto,))
            pedida = cur.fetchone() is not None
        if not r or r[0] != p.propuesto:
            return False, "el master no quedó apuntado a la pata nueva"
        if r[1] != p.propuesto:
            # Las dos copias tienen que decir lo mismo o vuelve la divergencia.
            return False, "la columna quedó bien pero el blob no — quedaron partidos"
        if not pedida:
            return False, "el master quedó apuntado pero la pata no quedó pedida"
        return True, f"master → «{_sym(p.propuesto)}» y pedida"

    def veredicto(self, p: Propuesta) -> tuple[bool | None, str]:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT last_price FROM mercado.market_snapshot "
                        "WHERE ticker = %s", (p.propuesto,))
            r = cur.fetchone()
        px = r[0] if r else None
        if px:
            return True, f"la grilla ya muestra USD {float(px):,.2f}"
        return None, "todavía sin punta"


def _sym(simbolo: str) -> str:
    """`MERV - XMEV - BPA7D - 24hs` → `BPA7D`. En un aviso entra el corto."""
    partes = (simbolo or "").split(" - ")
    return partes[2].strip() if len(partes) >= 3 else (simbolo or "")


class AccionRehacerDia(Seguible):
    """**REHACER EL DÍA DE UN JOB — con la prueba mirada, no con el error.**

    Pedido del user (2026-08-20), después de que el AuM del día no se escribiera
    por un HTTP 500 de Aunesa: *«que lo pueda hacer, o sea ejecutar fecha de hoy
    por haber detectado un error Y haber verificado 100% en la base que no hay
    fecha realmente con lo que iba de hoy»*.

    **La segunda mitad es el diseño.** No se relanza porque el job falló — se
    relanza porque **el dato no está en la tabla**. Un job puede salir con error
    después de escribir todo, y puede salir en verde sin escribir nada: lo único
    que importa es el resultado. Misma ley que los CONTRATOS de SALUD.

    Es la PRIMERA acción con efecto fuera de `mercado.curvas`, y por eso lleva
    las cuatro guardas de `av_agent_rehacer`: sin evidencia no corre, va por
    `run_job.sh` (lock + timeout), solo jobs declarados, y **se verifica
    releyendo la tabla**.

    ⚠️ **No relanza motores.** Un motor en rueda le corta el feed a la mesa y eso
    no se decide desde un botón.
    """

    id = "sistema.rehacer_dia"
    titulo = "Rehacer el día de un job cuyo dato falta"
    sobre = "dia_sin_dato"
    campo = "corrida"
    donde = "run_job.sh (lock + timeout, el mismo que usa el cron)"

    # El dato tarda: el job pega a Aunesa cuenta por cuenta. Se verifica que la
    # fila esté, y eso es inmediato — pero la CONSECUENCIA (que las vistas se
    # actualicen) la mira el seguimiento.
    espera_s = 45 * 60

    def proponer(self, casos: list[dict]) -> list[Propuesta]:
        from api.services import av_agent_rehacer as reh

        props = []
        for c in casos:
            job = (c.get("job") or c.get("key") or "").strip()
            fecha = (c.get("fecha") or "").strip()
            cfg = reh.REHACIBLES.get(job)
            if not cfg or not fecha:
                continue
            props.append(Propuesta(
                sujeto=job, campo=self.campo, propuesto=fecha,
                antes=f"{cfg['tabla']} sin {fecha}",
                porque=(f"**{cfg['titulo']}**: se miró `{cfg['tabla']}` y el "
                        f"{fecha} NO ESTÁ. {cfg['rompe'].capitalize()}. El job es "
                        f"idempotente y corre por `run_job.sh`, con el mismo lock "
                        f"que el cron: si ya está corriendo, esto se saltea solo."),
                extra={"fecha": fecha, "tabla": cfg["tabla"]}))
        return props

    def aplicar(self, p: Propuesta) -> None:
        from api.services import av_agent_rehacer as reh

        r = reh.rehacer(p.sujeto, p.propuesto)
        if not r.get("ok"):
            raise RuntimeError(r.get("error") or "no se pudo rehacer")
        # `ya_estaba` NO es un fallo: es la guarda haciendo su trabajo. Que el
        # dato apareciera solo entre la propuesta y el OK es el mejor final.

    def verificar(self, p: Propuesta) -> tuple[bool, str]:
        """**Se mira la TABLA**, no el código de salida del proceso."""
        from api.services import av_agent_rehacer as reh

        hay = reh.hay_dato(p.sujeto, p.propuesto)
        if hay is None:
            return False, "no pude releer la tabla para verificar"
        if not hay:
            return False, f"la tabla SIGUE sin {p.propuesto}"
        return True, f"la tabla ya tiene {p.propuesto}"

    def veredicto(self, p: Propuesta) -> tuple[bool | None, str]:
        """Acá `verificar` ya es concluyente —el dato está o no está— así que el
        veredicto no espera nada: se contesta con lo mismo."""
        ok, detalle = self.verificar(p)
        return (True, detalle) if ok else (False, detalle)


# ── ACCIÓN: el TICKER del asset, cuando es un TYPEO ─────────────────────────

class AccionTickerAsset:
    """**CORREGIR EL TICKER DE LA FICHA.** Un carácter, y rompe un join entero.

    ⚠️ **QUÉ SE MIDIÓ** (2026-08-22, `scripts/diag_espejo_assets`). El user, sobre
    PLC5O y S13N6 marcados con `sin_espejo_en_assets`: *«claramente está bugueada
    esta feature porque los bonos SÍ están»*. Los bonos estaban, y el hallazgo
    también era cierto — las dos cosas a la vez:

        curva  PLC5O   ·  assets  PLC50   ← la «O» es un CERO
        curva  S13N6   ·  assets  S13B6   ← la «N» es una «B»

    Dos de dos, **tipeados a mano**. No falta la ficha ni falta el campo: el campo
    está mal escrito por un carácter. Y como `portafolio.assets` es catálogo de la
    mesa (se carga a mano) esto va a volver a pasar.

    POR QUÉ SE PUEDE PROPONER CON CERTEZA
    =====================================

    Porque **el valor correcto no lo tipea nadie**: viene adentro de la propia
    `unidad`, que es la PK de la fila y la escribe Aunesa —
    `'[84857] PLC5O - ON PLUSPETROL…'` → `PLC5O`. No se adivina por parecido ni
    por distancia de edición (REGLA #9 A): se lee de la fuente que ninguna de las
    dos copias escribió.

    LAS TRES GUARDAS, que son lo que separa esto de un UPDATE peligroso
    ===================================================================

    1. **El código de la unidad tiene que coincidir con una curva.** Si la unidad
       dice algo que no es ninguna curva, no sabemos cuál es el bueno → no se
       propone.
    2. **El ticker actual no puede ser el de OTRA curva real.** Si `PLC50` fuera
       un papel de verdad, pisarlo acá le rompería el join a ESE — se reporta y
       no se toca.
    3. **Una sola ficha por unidad.** Con dos, cuál es la buena es una decisión,
       no una derivación.
    """

    id = "assets.ticker"
    causa = "sin_espejo_en_assets"
    titulo = "Corregir el TICKER de la ficha"
    sobre = "assets_ticker_partido"
    campo = "TICKER"
    donde = "Manager → TÍTULOS · ASSETS"

    def proponer(self, casos: list[dict]) -> list[Propuesta]:
        from core.postgres import get_pool
        sujetos = [(_sujeto(c) or "").strip() for c in casos]
        sujetos = [x for x in sujetos if x]
        if not sujetos:
            return []
        arriba = [x.upper() for x in sujetos]
        with get_pool().connection() as conn, conn.cursor() as cur:
            # ⚠️ **EL SUJETO PUEDE VENIR DE DOS FORMAS Y LAS DOS TIENEN QUE
            # ANDAR.** El control `assets_ticker_partido` emite la UNIDAD (que es
            # la PK de la ficha); la fila de ENCONTRÓ, en cambio, habla del
            # TICKER del bono. Escrito para una sola, la otra propone CERO — sin
            # error, sin log: el botón aparece, no hace nada y no explica por
            # qué. Es exactamente la pared que esta acción vino a sacar.
            cur.execute(
                "SELECT unidad, coalesce(ticker, '') FROM portafolio.assets "
                " WHERE upper(unidad) = ANY(%(s)s) "
                r"    OR upper(substring(unidad "
                r"         from '^\s*(?:\[\d+\]\s*)?([A-Za-z0-9]+)')) "
                "       = ANY(%(s)s)", {"s": arriba})
            fichas = cur.fetchall()
            cur.execute("SELECT upper(btrim(ticker)) FROM mercado.curvas "
                        "WHERE ticker IS NOT NULL AND ticker <> ''")
            curvas = {r[0] for r in cur.fetchall()}
        return [Propuesta(sujeto=u, campo=self.campo, propuesto=nuevo,
                          antes=actual or "(vacío)",
                          porque=self._porque(actual, nuevo),
                          extra={"unidad": u, "actual": actual})
                for u, actual, nuevo in _elegir_ticker(fichas, curvas)]

    @staticmethod
    def _porque(actual: str, nuevo: str) -> str:
        return (f"La ficha dice TICKER «{actual or '(vacío)'}» y su propia unidad "
                f"dice «{nuevo}», que además es una curva existente. Mientras no "
                f"coincidan, el bono **suma al AuM** (ese join va por unidad) pero "
                f"no se puede unir a su curva: queda afuera de flujos, acreencias "
                f"y renta fija.")

    def aplicar(self, p: Propuesta) -> None:
        from api.services.acreencias import codigo_de_unidad
        from api.services.assets_sql import set_campos
        from core.postgres import get_pool

        nuevo = (p.propuesto or "").strip().upper()
        # ⚠️ **LAS GUARDAS SE VUELVEN A CORRER ACÁ.** Entre proponer y aplicar
        # pueden pasar horas y alguien pudo tocar el catálogo a mano; aplicar
        # confiando en la foto vieja es cómo se pisa un dato que ya estaba bien.
        if codigo_de_unidad(p.sujeto).upper() != nuevo:
            raise ValueError(
                f"la unidad «{p.sujeto}» no dice «{nuevo}» — no se escribe un "
                f"ticker que no salga de la propia unidad")
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT coalesce(ticker, '') FROM portafolio.assets "
                        "WHERE unidad = %s", (p.sujeto,))
            r = cur.fetchone()
            if r is None:
                raise ValueError(f"la ficha «{p.sujeto}» ya no existe")
            actual = (r[0] or "").strip().upper()
            if actual == nuevo:
                return                       # alguien lo arregló: no es un error
            cur.execute("SELECT 1 FROM mercado.curvas "
                        "WHERE upper(btrim(ticker)) = %s", (actual,))
            if actual and cur.fetchone():
                raise ValueError(
                    f"«{actual}» es el ticker de una curva REAL: pisarlo acá le "
                    f"rompería el join a ese papel. Hay que mirarlo a mano.")
        # Por la MISMA puerta que usa Manager: un segundo camino de escritura
        # termina con dos criterios para el mismo dato.
        set_campos(p.sujeto, {"TICKER": nuevo}, actor="av-agent", crear=False)

    def verificar(self, p: Propuesta) -> tuple[bool, str]:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT coalesce(ticker, '') FROM portafolio.assets "
                        "WHERE unidad = %s", (p.sujeto,))
            r = cur.fetchone()
        if not r:
            return False, "la ficha desapareció"
        quedo = (r[0] or "").strip().upper()
        if quedo != (p.propuesto or "").strip().upper():
            return False, f"quedó «{quedo}», no «{p.propuesto}»"
        return True, f"la ficha ya dice «{quedo}» — el join con la curva cierra"


def _elegir_ticker(fichas: list[tuple[str, str]],
                   curvas: set[str]) -> list[tuple[str, str, str]]:
    """Las tres guardas de `assets.ticker`, **puras y testeables**.

    `fichas` = `[(unidad, ticker_actual)]`, `curvas` = los tickers que existen.
    Devuelve `[(unidad, actual, propuesto)]` solo para lo que se puede corregir
    con certeza.

    Vive afuera de la clase a propósito: son las tres decisiones que separan
    esto de un UPDATE peligroso, y una decisión que no se puede testear sin la
    base es una decisión que nadie va a testear.
    """
    from api.services.acreencias import codigo_de_unidad

    # GUARDA 3 — una sola ficha por código. Con dos, cuál es la buena es una
    # decisión y no una derivación.
    por_codigo: dict[str, list[tuple[str, str]]] = {}
    for unidad, tk in fichas:
        por_codigo.setdefault(codigo_de_unidad(unidad).upper(), []).append(
            (unidad, (tk or "").strip()))

    out: list[tuple[str, str, str]] = []
    for cod, filas in sorted(por_codigo.items()):
        if len(filas) != 1 or not cod:
            continue
        unidad, actual = filas[0]
        if actual.upper() == cod:
            continue                     # ya coincide: no hay nada que proponer
        if cod not in curvas:
            # GUARDA 1 — si el código de la unidad no es una curva, no sabemos
            # cuál de los dos es el bueno. No se propone.
            continue
        if actual.upper() in curvas:
            # GUARDA 2 — el ticker actual es de OTRO papel real. Pisarlo acá le
            # rompería el join a ESE. Se mira a mano.
            continue
        out.append((unidad, actual, cod))
    return out


ACCIONES: dict[str, Accion] = {a.id: a for a in (
    AccionCartera(), AccionFci(), AccionContraparte(), AccionAvisar(),
    AccionPedirPata(), AccionPataDolar(), AccionApuntarPata(),
    AccionTickerAsset(), AccionRehacerDia())}
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
    # ── LA GUARDA UNIVERSAL: NO SE PROPONE LO QUE LA BASE YA TIENE ──────────
    #
    # ⚠️⚠️ REGLA #10.4, y nació de un incidente (2026-08-23): el user aplicó
    # EMISOR=IEB y EMISOR=BALANZ a las 19:06 (verificado ✔, en el libro) y dos
    # horas después QUÉ PROPONÉS le ofrecía EXACTAMENTE eso otra vez — el gate
    # del FCI proponía sin mirar el valor VIVO. La guarda va ACÁ y no en cada
    # gate a propósito: el gate que se olvide de chequear ya no puede repetir
    # la oferta, porque el único camino a la pantalla pasa por esta función.
    # `verificar(p)` es el MISMO juez que corre después de aplicar: si ya da
    # verde, no hay nada que proponer. Best-effort: si verificar explota, la
    # propuesta pasa — ofrecer de más se ve y se descarta; filtrar de más no.
    vivas, ya_estaban = [], 0
    for p in props:
        try:
            listo, _detalle = a.verificar(p)
        except Exception:
            listo = False
        if listo:
            ya_estaban += 1
            continue
        vivas.append(p)
    props = vivas
    n = _guardar(accion_id, props)
    con_ia_n = sum(1 for p in props if p.fuente == "ia")
    return {"ok": True, "accion": accion_id, "propuestas": n,
            "casos": len(casos), "por_regla": n - con_ia_n, "por_ia": con_ia_n,
            # Lo filtrado SE DICE: «2 ya estaban» explica por qué hay menos
            # propuestas que casos sin que parezca que la acción falló.
            "ya_estaban": ya_estaban,
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
            "INSERT INTO agente.av_agent_propuestas "
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
        f"SELECT {', '.join(_COLS)}, extra FROM agente.av_agent_propuestas "
        f"WHERE {where} ORDER BY fuente, sujeto LIMIT %s", params)]


def historial(accion_id: str = "", limite: int = 100) -> list[dict]:
    """Lo ya decidido — **incluido lo que salió mal.** Un libro que solo guarda
    los aciertos no sirve para medir nada."""
    where = "estado <> 'propuesta'" + (" AND accion = %s" if accion_id else "")
    params = (accion_id, limite) if accion_id else (limite,)
    return [_serializar(f) for f in _filas(
        f"SELECT {', '.join(_COLS)} FROM agente.av_agent_propuestas "
        f"WHERE {where} ORDER BY aplicado_at DESC NULLS LAST, id DESC LIMIT %s",
        params)]


def uno(accion_id: str, sujeto: str, *, aplicar_ya: bool = False,
        por: str = "") -> dict:
    """UNA acción sobre UN sujeto, desde la fila de ENCONTRÓ.

    ⚠️ **POR QUÉ EXISTE, y es el bug que hizo que los BOPREALes volvieran 17
    veces.** El botón de la fila salía de `ACCION_POR_TIPO`, o sea del TIPO del
    hallazgo — y todo `precio_moneda` mostraba «BUSCAR LA PATA USD». Para
    `pata_equivocada` **ese botón no arregla nada**: pide la pata en dólares
    (que ya cotizaba) y deja el master apuntando a la de pesos. El user apretaba,
    decía «✔ pedida», y a la rueda siguiente estaban los 17 de nuevo.

        Un botón que no arregla el problema de esa fila es peor que no tenerlo:
        promete, no cumple, y no da ningún error.

    La acción que SÍ lo arregla (`mercado.apuntar_pata`) existía desde el mismo
    día, pero solo se llegaba a ella por la tab de propuestas — tres pantallas
    más allá de donde está el problema.

    **No es una segunda implementación**: arma el caso volviendo a correr el
    control (lo que sigue mal AHORA, no la foto del cron), propone con la MISMA
    acción y aplica por la MISMA `aplicar()` — así el libro, la verificación y el
    «esperando respuesta del mercado» funcionan igual que por el otro camino.

    Sin `aplicar_ya` **no toca nada**: devuelve qué haría. Ese es el paso
    SIMULAR de la fila, y es lo que hace que aprobar no sea a ciegas.
    """
    a = ACCIONES.get(accion_id)
    if a is None:
        return {"ok": False, "error": f"no existe la acción «{accion_id}»"}
    sujeto = (sujeto or "").strip()
    if not sujeto:
        return {"ok": False, "error": "falta el sujeto"}

    casos, err = _casos_frescos(a.sobre)
    if err:
        return {"ok": False, "error": err}
    # El control devuelve TODOS los casos; acá interesa uno. Se filtra por las
    # dos claves que usan los controles (`key` y `ticker`) porque no todos traen
    # las dos, y quedarse con una sola dejaría acciones sin poder dispararse.
    mios = [c for c in casos
            if sujeto in {str(c.get("key") or ""), str(c.get("ticker") or "")}]
    if not mios:
        # **«Ya no está» NO es un error**: entre la corrida y el click el
        # problema pudo resolverse solo. Decirlo así evita que el usuario crea
        # que el botón falló.
        return {"ok": True, "ya_no_esta": True, "aplicadas": 0,
                "texto": f"«{sujeto}» ya no aparece en el control: no hay nada que hacer"}

    props = _solo_regla(a, mios)          # una fila no espera a que piense un LLM
    if not props:
        return {"ok": True, "sin_propuesta": True, "aplicadas": 0,
                "texto": ("el agente ve el problema pero no puede proponer un "
                          "valor seguro para este caso")}
    p = props[0]
    vista = {"sujeto": p.sujeto, "campo": p.campo, "antes": p.antes,
             "propuesto": p.propuesto, "porque": p.porque, "fuente": p.fuente}
    if not aplicar_ya:
        return {"ok": True, "simulado": True, "propuesta": vista}

    _guardar(accion_id, [p])
    filas = _filas("SELECT id FROM agente.av_agent_propuestas "
                   "WHERE accion = %s AND sujeto = %s AND campo = %s "
                   "  AND estado = 'propuesta' ORDER BY id DESC LIMIT 1",
                   (accion_id, p.sujeto, p.campo))
    if not filas:
        return {"ok": False, "error": "no pude guardar la propuesta"}
    r = aplicar([filas[0]["id"]], por=por)
    return {**r, "propuesta": vista}


def _recontrolar_despues(accion_ids: set[str]) -> dict[str, str]:
    """**Vuelve a correr el control de cada acción aplicada.** Nunca levanta.

    ⚠️⚠️ **POR QUÉ, y es lo que hacía que aplicar «no hiciera nada».** El user
    (2026-08-22) aplicó 5 emisores de FCI, volvió a mirar y ENCONTRÓ mostraba
    **los mismos 98 hallazgos, número por número**. Y no era un bug del arreglo
    —los 5 se escribieron y se verificaron— era que entre el dato y la pantalla
    hay CUATRO capas de foto:

        jobs.controles_datos (16:30)  →  manager.controles_datos
        salud.evaluar()               →  lee esa tabla
        jobs.av_agent (de noche)      →  escribe av_agent_hallazgos
        la pantalla                   →  lee esa foto

    Arreglar el dato no tocaba **ninguna**. Así que el arreglo funcionaba y la
    pantalla seguía diciendo lo mismo hasta la noche siguiente — que para quien
    mira es idéntico a que el botón no haga nada.

    Se re-corre el control por su `sobre`, **derivado de la acción**: no hay
    lista que mantener y una acción nueva lo hereda sola. Y por la MISMA puerta
    que el cron (`_diff_y_persistir`), así que un re-chequeo a mano y la corrida
    nocturna dejan exactamente el mismo estado.
    """
    out: dict[str, str] = {}
    for aid in sorted(accion_ids):
        a = ACCIONES.get(aid)
        sobre = getattr(a, "sobre", "") if a else ""
        if not sobre:
            continue
        try:
            from api.services.av_agent_salud import recontrolar
            r = recontrolar(sobre)
            out[sobre] = (r.get("texto") or "recontrolado") if r.get("ok") \
                else f"no pude recontrolar: {r.get('error')}"
        except Exception as e:                      # el arreglo ya se escribió
            logger.warning("hacer: no pude recontrolar %s (%s)", sobre, e)
            out[sobre] = f"no pude recontrolar: {type(e).__name__}"
    return out


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
        f"SELECT {', '.join(_COLS)}, extra FROM agente.av_agent_propuestas "
        "WHERE id = ANY(%s)", (list(ids),))
    if not filas:
        return {"ok": False, "error": "esas propuestas ya no existen"}
    res = []
    for f in filas:
        res.append(_aplicar_una(f, por=por, valores=valores or {}))
    hechas = sum(1 for r in res if r["ok"])
    esperando = sum(1 for r in res if r.get("esperando"))
    # ⚠️ **Y AHORA SE VUELVE A MIRAR.** Sin esto el arreglo se escribía, se
    # verificaba… y la pantalla seguía mostrando el problema hasta la corrida de
    # la noche siguiente. Ver `_recontrolar_despues`.
    recontrol = _recontrolar_despues(
        {str(f["accion"]) for f in filas if hechas}) if hechas else {}
    return {"ok": True, "aplicadas": hechas, "fallidas": len(res) - hechas,
            "esperando": esperando, "resultados": res,
            "recontrol": recontrol,
            "texto": _texto_resultado(hechas, len(res) - hechas, esperando)}


def _texto_resultado(hechas: int, fallidas: int, esperando: int = 0) -> str:
    # **«Esperando» se dice**, no se esconde adentro de «aplicada». Si el que
    # aprieta el botón no sabe que falta una respuesta, no la va a ir a buscar —
    # y ahí vuelve el problema que esto vino a resolver.
    cola = (f" · {esperando} esperando respuesta del mercado" if esperando else "")
    if not fallidas:
        return f"{hechas} aplicada/s y verificada/s.{cola}"
    if not hechas:
        return f"ninguna se pudo aplicar ({fallidas} con error).{cola}"
    return f"{hechas} aplicada/s y verificada/s · {fallidas} con error.{cola}"


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

    # ⚠️ **SI EL EFECTO TARDA, NO SE SELLA COMO SI SE SUPIERA** (2026-08-20). Una
    # acción `Seguible` hizo lo suyo —quedó pedida— pero la pregunta que abre la
    # contesta el mercado, y acá pasaron cero segundos. Queda `esperando` y la
    # cierra `av_agent_respuesta` desde el monitor de rueda. Antes se sellaba
    # `aplicada` con un detalle que era la única frase posible, y nadie volvía a
    # mirar: el user lo leyó como «está bien, sí, pero no te quedás tranquilo».
    espera = False
    if quedo and isinstance(a, Seguible):
        try:
            v, det = a.veredicto(p)
        except Exception as e:
            v, det = None, f"no se pudo mirar: {type(e).__name__}"
        if v is True:
            detalle = det
        elif v is None:
            espera, detalle = True, f"{detalle}; la respuesta llega en la rueda"

    from api.services.av_agent_respuesta import ESPERANDO
    _sellar(f["id"],
            estado=ESPERANDO if espera else ("aplicada" if quedo else "fallida"),
            por=por, verificado=None if espera else quedo, detalle=detalle,
            propuesto=p.propuesto if p.propuesto != f["propuesto"] else None,
            error=None if quedo else "aplicado pero la verificación no lo confirma")
    # ⚠️⚠️ **EL LIBRO, Y EL SEGUIMIENTO — que estaban desconectados de acá.**
    #
    # `av_agent_seguimiento` (§0.ac) existe desde el 2026-08-19 para contestar la
    # pregunta que el user pidió: *«que el agente entienda cuándo hizo algo bien,
    # no porque yo le puse "acertó", sino porque a los días puede detectar que el
    # cambio tuvo consistencia»*. Y **nunca recibió un caso de las 8 acciones de
    # este módulo**: se alimenta desde `av_agent_acciones.registrar`, y este
    # camino —el que usan TODAS las acciones, incluidos los botones de fila— no
    # lo llamaba nunca. Medido: cero apariciones de `registrar` en este archivo.
    #
    # O sea que el sistema sabía verificar que la escritura ENTRÓ (releer, mismo
    # segundo) y tenía la máquina para verificar que el arreglo FUNCIONÓ (que el
    # problema no vuelva en 5 días), y las dos estaban en el mismo repo sin
    # tocarse. Nada fallaba: simplemente el libro no registraba estas acciones y
    # el seguimiento se quedaba vacío.
    #
    #     Verificar que la escritura entró no dice si el arreglo era el correcto:
    #     un símbolo mal puesto se escribe igual de bien que uno bien puesto.
    #
    # **Va DESPUÉS de sellar y en su propio try**: la escritura real ya pasó y no
    # se deshace por un problema de auditoría o de medición.
    #
    # ⚠️ **Solo si QUEDÓ.** Poner en seguimiento algo que falló mediría un arreglo
    # que no existe, y a los 5 días lo cantaría como «volvió» — culpando al
    # diagnóstico de un problema que fue de la escritura.
    if quedo:
        try:
            from api.services import av_agent_acciones as acc
            acc.registrar(accion=a.id, objetivo=p.sujeto, por=por, ok=True,
                          origen="hacer", regla=_causa_de(a),
                          antes={"valor": f["antes"]} if f.get("antes") else None,
                          detalle={"campo": p.campo, "valor": p.propuesto,
                                   "esperando": espera, "verificado": detalle})
        except Exception as e:
            logger.warning("av_agent_hacer: no pude anotar %s en el libro: %s",
                           p.sujeto, e)

        # ── Y EL OBJETO PASA A «EN CURSO» ───────────────────────────────────
        #
        # Éste es el eslabón que faltaba, y es literalmente la queja del user:
        # *«ya lo marqué como hecho y sigue figurando»*. Apretaba el arreglo, se
        # escribía, se verificaba… y el hallazgo seguía exactamente igual,
        # porque **nada conectaba la acción con el objeto**.
        #
        # `en_curso` y no `resuelto`, y la diferencia importa: escribir el dato
        # no es lo mismo que el problema haya desaparecido. Quien lo declara
        # resuelto es el DETECTOR, cuando vuelve a mirar y ya no lo encuentra
        # (`sincronizar`) — y recién ahí arrancan los hitos. Si lo cerráramos
        # nosotros, estaríamos calificando nuestro propio trabajo.
        try:
            _mover_item(a, p)
        except Exception as e:
            logger.warning("av_agent_hacer: no pude mover el item de %s (%s)",
                           p.sujeto, e)

    return {"id": f["id"], "sujeto": p.sujeto, "campo": p.campo,
            "valor": p.propuesto, "ok": quedo, "verificado": quedo,
            "esperando": espera, "detalle": detalle}


def _mover_item(a, p: Propuesta) -> None:
    """El objeto que esta acción arregla, a `en_curso`.

    ⚠️ **Antes había que mover DOS** — el del control y el del detector — porque
    el mismo bono roto producía dos objetos con claves distintas. Ya no:
    la identidad es **(sujeto, causa)** con la causa normalizada (§0.bj), así
    que el detector de rueda y el control nocturno escriben en el MISMO objeto.
    El puente que hacía falta ayer sobra hoy, y eso es lo que tenía que pasar.

    `en_curso` y no `resuelto`: escribir el dato no es lo mismo que el problema
    haya desaparecido. Quien lo cierra es el DETECTOR, cuando vuelve a mirar y
    ya no lo encuentra — si lo cerrara la acción, el agente estaría calificando
    su propio trabajo.
    """
    from api.services import av_agent_items
    from core import ciclo

    clave = av_agent_items.clave_de_problema(p.sujeto, _causa_de(a), a.sobre)
    av_agent_items.marcar(clave, ciclo.EN_CURSO, por="accion")


def _causa_de(a) -> str:
    """La causa del eval set que resuelve esta acción. `sobre` es el default."""
    return (getattr(a, "causa", "") or a.sobre or "").strip()


def _sellar(pid: int, *, estado: str, por: str = "", verificado=None,
            detalle: str = "", error: str | None = None,
            propuesto: str | None = None) -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE agente.av_agent_propuestas SET estado = %s, por = %s, "
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
            "UPDATE agente.av_agent_propuestas SET estado = 'rechazada', "
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
        f"FROM agente.av_agent_propuestas {where[0]}", where[1])
    r = {k: int(v or 0) for k, v in (f[0] if f else {}).items()}
    decididas = r.get("aplicadas", 0) + r.get("rechazadas", 0)
    r["acierto"] = (round(r.get("aplicadas", 0) / decididas, 3)
                    if decididas else None)
    return r
