"""`agente/emisor.py` — **QUIÉN ES EL EMISOR, PROPUESTO Y CON SU FUENTE.**

Doc: `docs/AGENT.md` §5 → habilidad `ficha_incompleta`, regla `sin_emisor`.
Historia (por qué la cadena es ésta y dónde el modelo no opina): `AGENT.md` §0.dq.

EL PROBLEMA
===========

`jobs/assets_autofill` corre todas las noches y ya completó todo lo que sus
reglas determinísticas saben derivar. Lo que queda sin emisor es, **por
definición, lo que ninguna regla resuelve** — y hasta hoy la única salida era
que alguien fuera título por título a tipearlo.

Medido con `scripts/diag_emisor` (2026-09-05), sobre los que quedaban:

    FINANCIAMIENTO   →  ya lo resuelve una regla (`assets_autofill`)
    en 1816          →  ya lo resuelve `jobs/ficha_1816`, que además ESTANDARIZA
    el resto         →  hay que ENTENDER algo, y de eso se trata este módulo

«Entender algo» es, por ejemplo: *Ciclo Nova Ahorro Plus* es un fondo de **IEB**
—que ya está cargado con otros nueve assets— y ningún substring puede saberlo.

LO QUE ESTE MÓDULO **NO** HACE
==============================

⚠️⚠️ **NO ESCRIBE, Y NO DECIDE.** Propone. La escritura sigue siendo la de
siempre: `agente/arreglos.CompletarFicha` → `assets_sql.set_campos(crear=False)`,
con una línea de libro por título y contra la lista viva de faltantes. Este
módulo devuelve un valor y **de dónde salió**, y una persona confirma.

Esa separación es la que deja subir la autonomía por FUENTE y no de golpe: lo
que viene de Finnhub es un campo de una fuente externa, lo que viene del modelo
es una elección. No son lo mismo y no tienen por qué aprobarse igual.

LAS TRES GUARDAS
================

1. **EL MODELO ELIGE DE UNA LISTA CERRADA.** Recibe los emisores que YA existen
   en el catálogo y tiene que devolver uno de ellos, tal cual. Cualquier otra
   cosa se rechaza. No puede inventar un emisor nuevo ni escribir una variante
   de grafía — que es exactamente cómo nacieron `CREDICUOTAS` y
   `Credicuotas Consumo`, el mismo emisor partido en dos que hace que cualquier
   cosa que agrupe por emisor cuente mal.
2. **«NO SÉ» ES UNA RESPUESTA VÁLIDA Y NO COMPLETA NADA.** Es el mismo
   invariante que `agente/vigencia.py`: sin algo que lo afirme, no se escribe.
   Un emisor inventado es peor que un campo vacío — el vacío se ve, el
   inventado se suma.
3. **TECHO DE PLATA DECLARADO** (`TOPE_MODELO`). Una sola llamada por pantalla,
   con las filas que quedaron sin resolver. Si el gateway no contesta, las filas
   vuelven sin propuesta y el listado queda como estaba: **meter IA acá no puede
   agregar un modo de falla nuevo**, sólo puede ahorrar tipeo.

POR QUÉ NO HAY UN CLASIFICADOR DE ETFs
======================================

Un ETF no tiene emisor y va a `OTROS` (regla del user). La primera versión iba a
detectarlo con una fuente externa —el `quoteType` de Yahoo— porque *«Finnhub no
contestó» NO prueba que sea un ETF*: un símbolo mal escrito o la red caída dan lo
mismo, y `AGRO` (una empresa de verdad) también devuelve vacío.

Se descartó, y la razón es buena: **ese clasificador existía para que el sistema
decidiera solo.** Acá una persona confirma igual, así que alcanza con que el
modelo proponga `OTROS` y que se lo vea. No se agrega una dependencia externa
para un puñado de casos que el que mira resuelve de un vistazo.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

TAREA = "agente_emisor"

# De dónde salió cada propuesta. Viaja hasta la pantalla: confirmar «Finnhub
# dice Chevron Corp» no es el mismo acto que confirmar «el modelo eligió IEB».
REGLA, NOMBRE, FINNHUB, MODELO = "regla", "nombre", "finnhub", "modelo"

# Cuántas filas van al modelo por pantalla. Un tope que no está escrito no es un
# tope — mismo criterio que `redactar.TOPE_POR_PASADA`.
TOPE_MODELO = 80
# ⚠️⚠️ **DÓNDE EL MODELO NO OPINA, Y ESTÁ MEDIDO** (2026-09-05).
#
# En RENTA VARIABLE sin ficha del subyacente el modelo acertó **2 de 6**:
#
#     ✔ TECO2 → TELECOM          ✘ TRAN → TRANSPORTADORA DE GAS DEL NORTE
#     ✔ CGPA2 → Camuzzi          ✘ TXAR → YPF
#                                ✘ OEST → BBVA Argentina
#                                ✘ AGRO → BANCO DE VALORES
#
# Los cuatro errores son el MISMO caso: una acción argentina cuyo emisor no
# está en la lista cerrada, así que el modelo eligió el más parecido —del rubro
# correcto, plausible, y falso—. Y un emisor equivocado en este campo se suma a
# los totales de la casa **sin que nada falle**.
#
# Con ficha, en cambio, Finnhub acertó **7 de 7**. La diferencia no es el
# modelo: es que uno LEE una fuente y el otro RECUERDA.
#
# Así que acá el modelo no opina. La fila queda vacía, que es una respuesta
# honesta, y el camino correcto ya existe: estos tickers están en la foto de
# Primary, `cedear_faltante` los detecta y `alta_cedear` los da de alta CON su
# subyacente — y desde ahí Finnhub contesta.
#
# ⚠️ Un ETF NO cae acá: tiene ficha (`underlying`), lo que pasa es que Finnhub
# no lo cubre. Sigue yendo al modelo, que contesta OTROS — 8 de 8.
SIN_FICHA_NO_OPINA = frozenset({"RENTA VARIABLE"})

# Cuántos caracteres tiene que tener un emisor para buscarlo dentro de un
# nombre. Con menos, un match es casualidad: `MAX` adentro de «Maxinta» no dice
# nada, y emparejar mal es peor que no emparejar (REGLA #9).
MIN_LARGO_NOMBRE = 5


# ── 0. LO QUE EL SISTEMA YA SABE DERIVAR ───────────────────────────────────
def por_regla(fila: dict) -> str:
    """El emisor que `jobs/assets_autofill` derivaría, o "". **PURA.**

    ⚠️⚠️ **NO SE REIMPLEMENTA NINGUNA REGLA: SE LLAMAN LAS DEL JOB.**

    Es la corrección de un error de diseño real (2026-09-05). Las reglas
    deterministas —FINANCIAMIENTO → OTROS, DERIVADOS → OTROS— vivían SOLO en el
    cron nocturno, así que la pantalla mostraba nueve pagarés con el emisor
    vacío mientras el sistema sabía perfectamente qué iba ahí. El user lo dijo
    con todas las letras: *«no entiendo por qué justo el más fácil no lo hace»*.

    Y tenía razón dos veces. La pantalla quedaba peor que el cron, y **la
    pantalla es donde se trabaja**: obligaba a esperar a la noche para ver
    resuelto lo que no requiere pensar.

    Copiar las reglas acá habría sido peor todavía (REGLA #9): dos definiciones
    de «qué emisor le toca a un pagaré», cada una coherente consigo misma, y el
    día que una cambie la otra sigue contestando lo de antes sin fallar. Por eso
    se importan las funciones del job. `agente/alta_cedear.py` ya usa `jobs/` de
    la misma forma, y el test de capas lo permite.

    Va PRIMERO en la cadena porque es lo único gratis, lo único determinista y
    lo único que no puede equivocarse: si hay una regla, no hay nada que
    proponer ni que confirmar.
    """
    try:
        from jobs.assets_autofill import (
            _regla_emisor_derivados,
            _regla_emisor_financiamiento,
        )
    except Exception as e:                       # el job no importa: se sigue
        logger.warning("emisor: no pude leer las reglas del catálogo (%s)", e)
        return ""
    for regla in (_regla_emisor_financiamiento, _regla_emisor_derivados):
        if (v := (regla(fila) or {}).get("emisor")):
            return str(v)
    return ""


# ── 1. EL NOMBRE LO DICE ───────────────────────────────────────────────────
def por_nombre(unidad: str, emisores: list[str]) -> str:
    """El emisor YA CARGADO que aparece dentro de la `unidad`, o "". **PURA.**

    Es el caso `[4207] CAFCI1411-4207 - Allaria Dólar Dinámico` → `ALLARIA`.
    No inventa nada: sólo reconoce un emisor que el catálogo ya tiene.

    ⚠️ **El más largo gana.** Si existieran `ALLARIA` y `ALLARIA FONDOS`, el
    segundo tiene que ganarle al primero: el más específico describe mejor. Sin
    ese orden el resultado dependería de cómo estén ordenados los emisores, que
    es la clase de dependencia que no falla — elige mal, callada.

    ⚠️ **Y se exige un borde de palabra.** `\\bIEB\\b` no matchea dentro de
    «FIEBRE». Un `in` pelado sobre strings cortos empareja por casualidad, que
    es exactamente lo que la REGLA #9(A) prohíbe.
    """
    u = (unidad or "").upper()
    if not u:
        return ""
    for e in sorted({x.strip() for x in emisores if len(x.strip()) >= MIN_LARGO_NOMBRE},
                    key=len, reverse=True):
        if re.search(rf"\b{re.escape(e.upper())}\b", u):
            return e
    return ""


# ── 2. LA FICHA DEL SUBYACENTE ─────────────────────────────────────────────
def por_finnhub(underlying: str) -> str:
    """El nombre de la empresa del subyacente, o "". **Nunca levanta.**

    Medido el 2026-09-05: contesta para acciones y ADRs —incluido el argentino
    (`TGS` → *Transportadora de Gas del Sur SA*)— y **devuelve vacío para los
    ETFs**, porque `profile2` es un perfil de EMPRESA y un ETF no lo es.

    ⚠️ Ese vacío se devuelve como vacío y **no se interpreta**. «Finnhub no
    contestó» puede ser un ETF, un símbolo mal escrito o la red: convertirlo en
    un veredicto sería el invariante 1 al revés. El que sigue en la cadena es el
    modelo, que sí puede decir «es un ETF, va OTROS» — y una persona lo mira.
    """
    und = (underlying or "").strip().upper()
    if not und:
        return ""
    try:
        from core.finnhub import company_profile
        return str((company_profile(und) or {}).get("name") or "").strip()
    except Exception as e:
        logger.info("emisor: Finnhub no contestó por %s (%s)", und, e)
        return ""


# ── 3. EL MODELO, ELIGIENDO ────────────────────────────────────────────────

_SYSTEM = """Sos el que le pone el EMISOR a los títulos del catálogo de una mesa
de capitales argentina. Te doy una lista de títulos sin emisor y la lista CERRADA
de emisores que el catálogo YA usa.

Tu trabajo es elegir, para cada título, cuál de esos emisores le corresponde.

REGLAS DURAS:
- Si contestás algo, tiene que ser **un valor de la lista, copiado TAL CUAL**
  (misma grafía, mismas mayúsculas). No inventes emisores ni escribas variantes.
- ⚠️⚠️ **SI EL EMISOR CORRECTO NO ESTÁ EN LA LISTA, DEVOLVÉ CADENA VACÍA.**
  Nunca elijas «el más parecido», «el del mismo rubro» ni «el que más se
  aproxima». Si el título es de una empresa que no figura, la respuesta es
  vacío — no el competidor de esa empresa, no otro banco, no otra minera.
  Un emisor equivocado se suma a los totales de la casa y NADIE lo nota; un
  campo vacío se ve y alguien lo completa. Vacío es la respuesta correcta y
  no un fracaso tuyo.
- **Si dudás entre dos, devolvé vacío.** No hay premio por contestar.
- Un FCI lleva el emisor de su SOCIEDAD GERENTE, que suele estar en el nombre
  del fondo o ser conocida por el nombre de la familia de fondos.
- Una ACCIÓN lleva la empresa que la emitió, **esa y no otra**. Ojo con los
  tickers argentinos: muchos ya están en la lista porque la misma empresa
  emitió bonos, pero sólo sirve si es LA MISMA empresa.
- **Un ETF, un índice o cualquier cosa que no sea una empresa ni un fondo lleva
  `OTROS`**, que está en la lista.

Contestá SOLO un JSON, sin markdown, con la forma:
{"<unidad tal cual te la di>": "<emisor de la lista, o cadena vacía>"}
"""


def _pedido(filas: list[dict], emisores: list[str]) -> str:
    """Lo que ve el modelo. La lista cerrada va entera: es de donde elige."""
    titulos = [{"unidad": f.get("unidad", ""), "cartera": f.get("cartera", ""),
                "ticker": f.get("ticker", "")} for f in filas]
    return ("EMISORES QUE EXISTEN (elegí de acá, tal cual):\n"
            + json.dumps(sorted(emisores), ensure_ascii=False)
            + f"\n\nTÍTULOS SIN EMISOR ({len(titulos)}):\n"
            + json.dumps(titulos, ensure_ascii=False))


def _json_de(crudo: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (crudo or "").strip(), flags=re.S)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        d = json.loads(t[i:j + 1])
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def por_modelo(filas: list[dict], emisores: list[str]) -> dict[str, str]:
    """`{unidad: emisor}` para los que el modelo supo. **Nunca levanta.**

    ⚠️⚠️ **LA LISTA CERRADA TIENE UN FILO, Y SE VIO EN LA PRIMERA CORRIDA REAL**
    (2026-09-05). Obligar a elegir de una lista impide inventar un emisor nuevo,
    pero **cuando el correcto no está en la lista, empuja a contestar el más
    parecido**: `BHP → RIO TINTO`, `BBD → BANCO DO BRASIL`, `GGB → GEMSA`,
    `MOS → MOLINOS`. Los cuatro son CEDEARs de empresas que el catálogo no
    tiene, y los cuatro salieron mal — plausibles, del rubro correcto, y falsos.

    El prompt se contradecía: pedía «elegí SIEMPRE de la lista» y «si no sabés,
    vacío». Ahora dice explícitamente que **si el emisor correcto no está en la
    lista, la respuesta es vacío** — nunca el competidor, nunca otro del mismo
    rubro. Pero eso ACOTA el daño, no lo cierra: la guarda de verdad para un
    CEDEAR es que su emisor salga de la FICHA del subyacente (Finnhub) y no de
    lo que el modelo recuerde. Los que fallaron son justamente los que no tienen
    `underlying` en `mercado.cedears`, y para eso ya existe `alta_cedear`.

    ⚠️ **LO QUE NO ESTÁ EN LA LISTA SE DESCARTA.** El modelo puede contestar un
    emisor que no existe —una variante de grafía, o uno inventado— y escribirlo
    sería fabricar el duplicado que este campo no puede tener. La comparación es
    insensible a mayúsculas para aceptar la respuesta, pero **lo que se devuelve
    es la grafía del CATÁLOGO**, no la del modelo: si no, `Iam` entraría al lado
    de `IAM` y ninguna de las dos fallaría.
    """
    if not filas or not emisores:
        return {}
    permitidos = {e.strip().upper(): e.strip() for e in emisores if e.strip()}
    try:
        from core import ai
        crudo = ai.completar(TAREA, system=_SYSTEM,
                             user=_pedido(filas[:TOPE_MODELO], emisores),
                             detalle=f"emisor · {len(filas)} título(s)")
    except Exception as e:
        logger.warning("emisor: el gateway falló (%s) — sin propuestas", e)
        return {}
    if not crudo:
        logger.info("emisor: el gateway no contestó (sin key, presupuesto o "
                    "proveedor caído) — las filas van sin propuesta")
        return {}

    validas, rechazadas = {}, []
    for unidad, valor in _json_de(crudo).items():
        elegido = permitidos.get(str(valor or "").strip().upper())
        if elegido:
            validas[str(unidad)] = elegido
        elif str(valor or "").strip():
            rechazadas.append(f"{unidad}→{valor}")
    if rechazadas:
        # Se cuenta y se logea: «el modelo no supo» y «el modelo dijo una macana
        # y la tiré» no se pueden ver iguales. Es la misma razón por la que
        # `redactar.py` guarda el motivo del rechazo.
        logger.info("emisor: %d propuesta(s) fuera de la lista, descartadas: %s",
                    len(rechazadas), "; ".join(rechazadas[:5]))
    return validas


# ── LA CADENA ──────────────────────────────────────────────────────────────
def proponer(filas: list[dict], emisores: list[str], *,
             subyacentes: dict[str, str] | None = None,
             usar_modelo: bool = True) -> list[dict]:
    """Cada fila con `propuesto` y `fuente`. **El primero que contesta gana.**

        0. una REGLA del sistema ya lo sabe        → gratis y determinista
        1. el nombre trae un emisor que ya existe   → barato, sin red
        2. hay subyacente y Finnhub da el nombre    → una fuente externa
        3. el modelo elige de la lista cerrada      → una sola llamada
        4. nada                                     → `propuesto = ""`, y la fila
                                                      queda como está hoy

    El orden **es** el de la confianza, y no es cosmético: lo de arriba se puede
    verificar sin abrir nada, lo de abajo hay que mirarlo. Por eso cada fila se
    lleva su `fuente` hasta la pantalla en vez de quedar todas iguales.

    `subyacentes` es `{ticker_upper: underlying}`; sin él, el paso 2 no corre.
    Se recibe y no se lee acá adentro para que esta función siga siendo probable
    sin base ni red.
    """
    subs = subyacentes or {}
    out, pendientes = [], []
    for f in filas:
        fila = {**f, "propuesto": "", "fuente": ""}
        if (v := por_regla(f)):
            fila["propuesto"], fila["fuente"] = v, REGLA
        elif (v := por_nombre(f.get("unidad", ""), emisores)):
            fila["propuesto"], fila["fuente"] = v, NOMBRE
        elif (und := subs.get((f.get("ticker") or "").strip().upper())):
            if (v := por_finnhub(und)):
                fila["propuesto"], fila["fuente"] = v, FINNHUB
        out.append(fila)
        if fila["propuesto"]:
            continue
        # El modelo no adivina un emisor de renta variable sin ficha: ahí acertó
        # 2 de 6, y todas las que erró fueron acciones locales. Ver el bloque de
        # `SIN_FICHA_NO_OPINA`.
        sin_ficha = not subs.get((f.get("ticker") or "").strip().upper())
        if sin_ficha and (f.get("cartera") or "").strip().upper() in SIN_FICHA_NO_OPINA:
            continue
        pendientes.append(fila)

    if usar_modelo and pendientes:
        elegidos = por_modelo(pendientes, emisores)
        for fila in pendientes:
            if (v := elegidos.get(fila.get("unidad", ""))):
                fila["propuesto"], fila["fuente"] = v, MODELO
    return out


# ── DE DÓNDE SALE EL SUBYACENTE ────────────────────────────────────────────
def subyacentes(tickers: list[str]) -> dict[str, str]:
    """`{ticker_upper: underlying}` desde `mercado.cedears`. **Nunca levanta.**

    Vive acá y no en `agente/fuentes.py` porque esto corre A PEDIDO —cuando
    alguien abre el listado— y no en una pasada del motor: la caché de `fuentes`
    muere con la pasada y acá no hay pasada.

    Se busca por `ticker_corto` Y por `ticker`: el catálogo guarda las dos
    grafías y el asset puede traer cualquiera. **No se recorta ningún sufijo**
    para «conseguir el ticker base» — eso convierte `TXAD` en `TXA` y es la
    REGLA #9(A).

    `{}` si no se pudo leer, y ahí el paso de Finnhub simplemente no corre: no
    poder mirar el master no puede convertirse en una propuesta peor.
    """
    tk = sorted({(t or "").strip().upper() for t in tickers if (t or "").strip()})
    if not tk:
        return {}
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT upper(coalesce(c.ticker_corto, c.ticker)), c.underlying "
                "  FROM mercado.cedears c "
                " WHERE (upper(c.ticker_corto) = ANY(%s) OR upper(c.ticker) = ANY(%s)) "
                "   AND c.underlying IS NOT NULL AND btrim(c.underlying) <> ''",
                (tk, tk))
            return {t: u for t, u in cur.fetchall()}
    except Exception as e:
        logger.warning("emisor: no pude leer mercado.cedears (%s)", e)
        return {}
