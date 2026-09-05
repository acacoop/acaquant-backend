"""`agente/emisor.py` — **QUIÉN ES EL EMISOR, PROPUESTO Y CON SU FUENTE.**

Doc: `docs/AGENT.md` §5 → habilidad `ficha_incompleta`, regla `sin_emisor`.

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
NOMBRE, FINNHUB, MODELO = "nombre", "finnhub", "modelo"

# Cuántas filas van al modelo por pantalla. Un tope que no está escrito no es un
# tope — mismo criterio que `redactar.TOPE_POR_PASADA` y `triage.TOPE_DIARIO`.
TOPE_MODELO = 80
# Cuántos caracteres tiene que tener un emisor para buscarlo dentro de un
# nombre. Con menos, un match es casualidad: `MAX` adentro de «Maxinta» no dice
# nada, y emparejar mal es peor que no emparejar (REGLA #9).
MIN_LARGO_NOMBRE = 5


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
- **Elegí SIEMPRE un valor de la lista, copiado TAL CUAL** (misma grafía, mismas
  mayúsculas). No inventes emisores nuevos ni escribas variantes.
- **Si no sabés, devolvé cadena vacía.** No adivines: un emisor equivocado se
  suma a los totales y nadie lo nota, un campo vacío se ve.
- Un FCI lleva el emisor de su SOCIEDAD GERENTE, que suele estar en el nombre
  del fondo o ser conocida por el nombre de la familia de fondos.
- Una ACCIÓN lleva la empresa. Ojo: muchas ya están en la lista porque la misma
  empresa emitió bonos.
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
        if (v := por_nombre(f.get("unidad", ""), emisores)):
            fila["propuesto"], fila["fuente"] = v, NOMBRE
        elif (und := subs.get((f.get("ticker") or "").strip().upper())):
            if (v := por_finnhub(und)):
                fila["propuesto"], fila["fuente"] = v, FINNHUB
        out.append(fila)
        if not fila["propuesto"]:
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
