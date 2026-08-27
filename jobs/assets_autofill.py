"""jobs/assets_autofill.py — autocompletado del catálogo `portafolio.assets`.

El panel Manager → TÍTULOS → ASSETS se llena a mano, pero una parte de los campos
NO es una decisión: sale de la propia `unidad` que manda Aunesa. Este job aplica
esas reglas determinísticas sobre los campos que están VACÍOS y deja para la mesa
lo que sí es criterio humano (EMISOR, CALIFICACIÓN).

El invariante que NO se negocia:
  * NUNCA pisa un valor cargado. Si la regla propone algo distinto de lo que ya
    hay, no escribe: lo reporta como CONFLICTO — así una etiqueta mal escrita en
    el catálogo se ve en el run en vez de duplicarse en silencio.

Eso es lo que hace seguro tener acá una regla HEURÍSTICA (ver más abajo): la
máquina propone para no clasificar miles de assets a mano, el humano corrige en
Manager, y a partir de ahí el job no vuelve a opinar sobre ese valor.

Cada regla es una función `fila → {columna: valor}` sobre la fila que le llega.
La mayoría deriva de la propia `unidad` (misma unidad → mismo resultado, para
siempre); `financiamiento_clase` y `herencia` son las excepciones y necesitan
contexto que NO está en la unidad (el nominal de la última tenencia y los otros
assets del catálogo) — ese contexto se les inyecta en la fila antes de correr,
así la regla sigue siendo una función pura y testeable sin base.

Reglas v1:
  * financiamiento — pagarés/cheques del negocio de financiamiento. La unidad
    tiene firma propia (`[TICKER] TICKER Nro. <nro> Vto. <dd/mm/aaaa>`, el ticker
    repetido dentro y fuera del corchete) → CARTERA, TICKER y VENCIMIENTO.
    INSTRUMENTO y CODIGO_CNV no existen para estos papeles: no se tocan.
  * financiamiento_clase — CLASE_ACTIVO `HD`/`DL` de esos mismos papeles, según
    el nominal (≤ 5.000.000 → HD, > → DL). ÚNICA regla heurística del job:
    existe porque la vista FINANCIAMIENTO no puede graficar juntas dos escalas
    tan distintas y clasificar el catálogo a mano no era viable. Backfill y
    mantenimiento son el mismo comando (ver "Uso").
  * fci — `[<id>] CAFCI<n>-<m> - <nombre>` → CARTERA, TICKER (nombre del fondo) y
    CAFCI (código). Es la misma derivación que hace el auto-alta del writer diario
    (`core.cafci`), acá backfilleada sobre lo que ya está en el catálogo.
  * ticker — TICKER para el resto del catálogo, sea cual sea la cartera: sale del
    `[<id>] <descripción>` de Aunesa.
  * especies — los DOS símbolos de mercado (INSTRUMENTO en pesos e INSTRUMENTO_USD
    en dólares) desde `mercado.especies`, relacionando por TICKER. Es lo que hace
    que el catálogo deje de ser una segunda verdad sobre market data: los símbolos
    se cargan en UN solo lugar y acá se bajan derivados.
  * herencia — REBAUTIZO de Aunesa (ver abajo): copia entre unidades que son el
    MISMO instrumento los campos que NO se derivan de la unidad (EMISOR,
    CALIFICACIÓN, INSTRUMENTO, CLASE_ACTIVO, CÓDIGO CNV, FEE ADMIN).

Sumar una regla = una función `fila → {columna: valor}` + una entrada en REGLAS;
el motor se ocupa del "solo si está vacío", del reporte y de la escritura.

## El rebautizo de Aunesa (2026-08-12) — por qué existe `herencia`

Por un cambio normativo Aunesa reemitió los instrumentos con OTRO id de especie.
El corchete cambia y el resto de la unidad queda igual:

    [6461]  CAFCI1910-6461 - DXA Multicobertura - Clase B   ← la vieja, con EMISOR
    [28902] CAFCI1910-6461 - DXA Multicobertura - Clase B   ← la nueva, sin nada

Como `unidad` es la PK de `portafolio.assets`, la renombrada entra como asset
NUEVO: el writer diario le pone CARTERA y TICKER (se derivan solos) y el resto
—EMISOR sobre todo— nace VACÍO. La carga manual de la mesa se quedó pegada a la
unidad vieja, que además NO se puede borrar: la tenencia histórica la referencia.

La herencia no adivina nada: se apoya en que el rebautizo cambia la ETIQUETA y no
la IDENTIDAD. Para un fondo la identidad es el **código CAFCI** (`CAFCI1910-6461`,
intacto arriba); si el rebautizo llegara a tocar también el código, queda el
**nombre del fondo** como segunda clave. Dos unidades que comparten identidad son
el mismo instrumento → lo que una tiene cargado vale para la otra, en las dos
direcciones (si mañana la mesa carga el EMISOR en la nueva, la vieja lo recibe).

Las tres cosas que la hacen segura:
  * **Los donantes tienen que estar de acuerdo.** Si en un grupo hay DOS valores
    distintos para el mismo campo no se escribe nada: se reporta la divergencia.
    Eso es lo que sostiene la clave por nombre — si dos fondos distintos llegaran
    a llamarse igual, sus EMISORES no coinciden y el desacuerdo frena la copia.
  * **Nunca pisa** (invariante del job): solo llena lo VACÍO.
  * **Solo FCI.** Para el resto del catálogo la identidad sería el TICKER, que
    también se deriva solo — pero eso todavía NO está medido, así que se CUENTA
    y se reporta sin escribir (`HEREDAR_NO_FCI`).

Cron: L-V 11:40 UTC, después del writer diario (11:00) que da de alta las unidades
nuevas. La API relee el catálogo por TTL (`assets_sql`, 300s).

Uso:
    python -m jobs.assets_autofill                              # todas las reglas (cron)
    python -m jobs.assets_autofill --dry                        # qué completaría, sin escribir
    python -m jobs.assets_autofill --regla financiamiento_clase # backfill de HD/DL
    python -m jobs.assets_autofill --regla herencia --dry       # qué copiaría el rebautizo

El BACKFILL y el mantenimiento diario son EL MISMO comando: el job siempre mira
lo que está vacío, así que la primera corrida completa el histórico y las
siguientes sólo tocan lo que entró nuevo. Es idempotente por construcción.
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from core.cafci import es_fci_unidad, extract_cafci, nombre_fci
from core.job_runs import JobRunLogger
from core.postgres import get_pool

# Columnas que el job puede escribir. Es una allowlist de verdad: los nombres se
# interpolan en el UPDATE, así que una regla no puede inventar una columna.
# EMISOR / CALIFICACION / FEE_ADMIN entraron con la regla `herencia`: no se
# derivan de nada, se COPIAN de otra unidad que ya las tiene cargadas a mano.
_ESCRIBIBLES = frozenset({"cartera", "clase_activo", "ticker", "instrumento",
                          "instrumento_usd", "cafci", "vencimiento", "codigo_cnv",
                          "emisor", "calificacion", "fee_admin"})
_LEIBLES = ("unidad", "cartera", "clase_activo", "emisor", "ticker",
            "instrumento", "instrumento_usd", "calificacion", "cafci", "vencimiento",
            "codigo_cnv", "fee_admin")
_ACTOR = "job:assets_autofill"


def _norm(v) -> str:
    """Forma canónica para COMPARAR valores de columnas de distinto tipo.

    `fee_admin` es `numeric` → vuelve como Decimal, no como str. Sin esta
    normalización el motor compararía Decimal contra str, nunca daría igual y
    marcaría como conflicto un valor idéntico al que ya está guardado.
    """
    return "" if v is None else str(v).strip()


def _vacio(v) -> bool:
    """Mismo criterio de "vacío" que el panel (`assets_sql._EMPTY`)."""
    s = _norm(v)
    return not s or s.upper() == "NO APLICA"


# ── Reglas ───────────────────────────────────────────────────────────────────

# `[*BIN031000050] *BIN031000050 Nro. 29805263 Vto. 03/10/2026`
# El ticker aparece DOS veces (dentro y fuera del corchete) y eso es lo que hace
# segura la firma: en el resto del catálogo el corchete lleva un id de especie que
# NO se repite (`[9131] YPFD - CEDEAR YPF`).
_RE_FINANCIAMIENTO = re.compile(
    r"^\[(?P<tk>[^\]]+)\]\s+(?P<tk2>\S+)\s+Nro\.?\s*\S+\s+"
    r"Vto\.?\s*(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})$")

CARTERA_FINANCIAMIENTO = "FINANCIAMIENTO"


def _regla_financiamiento(row: dict) -> dict[str, str]:
    m = _RE_FINANCIAMIENTO.match((row.get("unidad") or "").strip())
    if not m or m["tk"] != m["tk2"]:
        return {}
    try:
        vto = date(int(m["y"]), int(m["m"]), int(m["d"]))
    except ValueError:      # 31/02: la unidad miente, mejor no completar nada
        return {}
    return {"cartera": CARTERA_FINANCIAMIENTO, "ticker": m["tk"],
            "vencimiento": vto.isoformat()}


# CLASE_ACTIVO de financiamiento: HD (hard dollar) o DL (dólar linked).
#
# POR QUÉ EXISTE: son ESCALAS distintas. Un HD de 5.000 y un DL de 27.000.000 en
# el mismo gráfico dejan al HD invisible — la vista FINANCIAMIENTO no puede
# sumarlos ni graficarlos juntos, y necesita saber cuál es cuál.
#
# CÓMO SE DECIDE (regla del user, 2026-08-11): por el NOMINAL. Nominal chico =
# el papel está expresado en dólares de verdad (HD); nominal grande = está en la
# escala del dólar linked (DL).
#
# ⚠️ ES UNA HEURÍSTICA DE ARRANQUE, no una verdad. Existe para no clasificar
# 2.000 assets a mano. Se corrige en Manager → ASSETS y el job NUNCA pisa lo
# corregido (invariante del módulo): cada valor que un humano toca queda fijo y
# la heurística no vuelve a opinar sobre él.
# <= 5.000.000 → HD · > 5.000.000 → DL
#
# POR QUÉ 5.000.000 (corregido 2026-08-11, el primer intento fue 5.000 y clasificó
# 260 papeles como DL cuando no lo eran). La evidencia son los nominales y sus
# tasas, que vienen del texto real de los boletos (ver `core/mav_tasa.py`):
#
#     30.000 @ 7%        100.000 @ 6%        500.000 @ -0,5%     → tasas de DÓLAR
#     27.000.000 @ 39,5%                                          → tasa de PESOS
#
# La frontera real está entre 500.000 y 27.000.000: un papel que rinde 6% anual
# no puede estar expresado en pesos. Con el corte en 5.000 caían del lado DL
# instrumentos que pagan tasa de dólar, y CASI TODO el catálogo terminaba en DL
# — que un lado se lleve todo es la señal de que el umbral está mal puesto.
_UMBRAL_HD = 5_000_000.0

# De dónde sale el nominal del asset: la SUMA de lo que hay en la última
# tenencia de esa unidad. Es lo más cercano al valor nominal del papel que se
# puede armar sin un campo propio (el asset no tiene "monto"): si el pagaré está
# repartido entre varios comitentes, las partes suman el total emitido.
_NOMINAL_COL = "nominal"


def _regla_financiamiento_clase(row: dict) -> dict[str, str]:
    if not _RE_FINANCIAMIENTO.match((row.get("unidad") or "").strip()):
        return {}
    nominal = row.get(_NOMINAL_COL)
    if nominal is None:
        return {}                       # sin tenencia hoy → no hay de qué inferir
    return {"clase_activo": "HD" if float(nominal) <= _UMBRAL_HD else "DL"}


def _regla_fci(row: dict) -> dict[str, str]:
    unidad = row.get("unidad") or ""
    nombre = nombre_fci(unidad)
    if not nombre:
        return {}
    out = {"cartera": "FCI", "ticker": nombre}
    codigo = extract_cafci(unidad)
    if codigo:
        out["cafci"] = codigo
    return out


def _es_fci(row: dict) -> bool:
    cartera = str(row.get("cartera") or "").strip().upper()
    return cartera in {"FCI", "CARTERA FCI"} or es_fci_unidad(row.get("unidad"))


# `[<id de especie>] <descripción opcional>` — la forma general de Aunesa. Las
# unidades de cash (`ARS`, `USDL`) no la cumplen y quedan afuera solas.
_RE_UNIDAD = re.compile(r"^\[(?P<id>[^\]]*)\]\s*(?P<resto>.*)$", re.DOTALL)


def _regla_ticker(row: dict) -> dict[str, str]:
    """TICKER para cualquier cartera, derivado de la unidad:

        `[DLR012026]`                          → DLR012026   (sin descripción: el id)
        `[43070] NZC6O - NZC6O - T.DEUDA BNA`  → NZC6O       (hasta el primer guion)
        `[10390] Depósito U$S Ext`             → Depósito U$S Ext  (sin guion: todo)

    Se autoexcluye de las dos carteras que ya tienen su propia derivación: en FCI
    el ticker es el NOMBRE del fondo, que va DESPUÉS del código CAFCI (cortar en el
    primer guion daría 'CAFCI518'); en financiamiento la descripción es el propio
    ticker seguido de Nro./Vto.
    """
    unidad = (row.get("unidad") or "").strip()
    if _es_fci(row) or _RE_FINANCIAMIENTO.match(unidad):
        return {}
    m = _RE_UNIDAD.match(unidad)
    if not m:
        return {}
    resto = m["resto"].strip()
    # `or resto` cubre la descripción que ARRANCA con guion: mejor el texto
    # completo que un ticker vacío.
    tk = (resto.split("-", 1)[0].strip() or resto) if resto else (m["id"] or "").strip()
    return {"ticker": tk} if tk else {}


# ── Regla `especies` — los DOS símbolos de mercado, desde mercado.especies ───
#
# QUÉ RESUELVE. `assets.instrumento` es el símbolo que el motor de portfolio le
# SUSCRIBE a Primary (`engines/_universo_portfolio.py`) — o sea, de dónde sale el
# last_price de la tenencia. Se venía cargando A MANO en Manager → ASSETS, con lo
# cual el catálogo de mercado terminó desparramado en tres lugares que se
# contradicen entre sí (assets, mercado.curvas, y el universo real de Primary).
#
# `mercado.especies` es el ÚNICO lugar donde vive esa relación (ticker → sus
# patas), sembrada desde el catálogo real de Primary. Esta regla la baja al
# catálogo: el humano deja de tipear símbolos y `assets` pasa a ser un DERIVADO
# de especies, no una segunda verdad.
#
# LAS DOS PATAS. Un bono no tiene un símbolo, tiene N: AL30 cotiza en pesos
# (`…AL30 - 24hs`), en MEP (`…AL30D…`) y en cable (`…AL30C…`). El catálogo tenía
# UNA sola columna, así que cada quien guardó la que le servía. Ahora son dos
# explícitas: `instrumento` = la pata en PESOS, `instrumento_usd` = la pata en
# DÓLARES (MEP; cable NO — es otra cosa y mezclarlas volvería a esconder cuál es
# cuál). Cada consumidor pide la que necesita en vez de adivinar.
#
# POR QUÉ ES SEGURO. El invariante del job: NUNCA pisa. Lo que hoy está cargado
# queda como está y el motor sigue suscribiendo exactamente lo mismo — o sea,
# esto NO puede cambiar una valuación. Si lo cargado no coincide con especies, se
# reporta como CONFLICTO y se decide mirándolo, que es justo el listado que hoy
# no existe.
_ESPECIES_COL = "_especies"

# Preferencia dentro de una misma pata. `es_default` primero (es la que la mesa
# ya eligió para dibujar la curva) y después 24hs sobre CI: el plazo estándar del
# mercado local, que es donde hay liquidez y por lo tanto precio.
_PLAZO_PREF = {"24HS": 0, "CI": 1}


def _prioridad_especie(e: dict) -> tuple:
    return (0 if e.get("es_default") else 1,
            _PLAZO_PREF.get(_norm(e.get("plazo")).upper(), 9),
            _norm(e.get("simbolo")))


def anotar_especies(rows: Iterable[dict], especies: Iterable[dict]) -> dict:
    """Inyecta `_especies` = {instrumento, instrumento_usd} en cada fila. PURA.

    Se agrupa por TICKER porque es la bisagra del modelo: `assets.ticker` y
    `especies.ticker` son el mismo AL30 (y el mismo que `mercado.curvas.ticker`).
    Un asset sin ticker todavía no es relacionable — lo completa la regla
    `ticker` en esta misma corrida y entra mañana.
    """
    rows = list(rows)
    por_ticker: dict[str, list[dict]] = defaultdict(list)
    for e in especies:
        if (tk := _norm(e.get("ticker")).upper()) and _norm(e.get("simbolo")):
            por_ticker[tk].append(e)

    propuesto: dict[str, dict[str, str]] = {}
    for tk, patas in por_ticker.items():
        elegido: dict[str, str] = {}
        for col, especies_ok in (("instrumento", {"pesos"}), ("instrumento_usd", {"mep"})):
            cands = sorted((e for e in patas if _norm(e.get("especie")).lower() in especies_ok),
                           key=_prioridad_especie)
            if cands:
                elegido[col] = _norm(cands[0]["simbolo"])
        if elegido:
            propuesto[tk] = elegido

    con_ars = con_usd = sin_ticker = 0
    for row in rows:
        row.pop(_ESPECIES_COL, None)
        # Mismo criterio que `_claves_identidad`: si la columna está vacía se
        # deriva de la unidad, así el asset que el writer dio de alta hace 40
        # minutos entra HOY y no mañana.
        tk = _norm(row.get("ticker")) or _norm(_regla_ticker(row).get("ticker"))
        if not tk:
            sin_ticker += 1
            continue
        prop = propuesto.get(tk.upper())
        if not prop:
            continue
        row[_ESPECIES_COL] = prop
        con_ars += "instrumento" in prop
        con_usd += "instrumento_usd" in prop
    return {"tickers_en_especies": len(por_ticker), "con_pata_ars": con_ars,
            "con_pata_usd": con_usd, "sin_ticker": sin_ticker}


def _regla_especies(row: dict) -> dict[str, object]:
    """Lo que `anotar_especies` dejó preparado. Sin anotar, no opina."""
    return dict(row.get(_ESPECIES_COL) or {})


# ── Regla `herencia` — el rebautizo de Aunesa ────────────────────────────────
#
# Campos que se COPIAN entre unidades del mismo instrumento. Son exactamente los
# que NO se derivan de la unidad: si la mesa no los carga, no los sabe nadie.
# CARTERA / TICKER / CAFCI / VENCIMIENTO quedan afuera a propósito — ya los
# resuelven las otras reglas desde la unidad, copiarlos sería redundante.
_HEREDABLES: tuple[str, ...] = ("emisor", "calificacion", "instrumento",
                                "clase_activo", "codigo_cnv", "fee_admin")

# Campo inyectado en la fila por `anotar_herencia` (mismo patrón que `nominal`).
_HERENCIA_COL = "_herencia"

# ¿Heredar también fuera de FCI, usando el TICKER como identidad? HOY NO: el
# rebautizo se vio en fondos y el resto del catálogo no está medido. El job
# igual CUENTA cuántos assets completaría (stat `no_fci_receptoras`) — cuando
# ese número y sus divergencias se hayan mirado, esto pasa a True y ya está.
HEREDAR_NO_FCI = False


def _claves_identidad(row: dict) -> list[tuple[str, str]]:
    """Claves por las que dos unidades son EL MISMO instrumento, más fuerte primero.

    FCI → código CAFCI (el id del regulador, lo que el rebautizo no toca) y, de
    respaldo, el nombre del fondo. Resto del catálogo → el ticker.

    El ticker se deriva de la unidad si la columna todavía está vacía: el asset
    que el writer dio de alta hace 40 minutos no lo tiene escrito (lo completa la
    regla `ticker` en esta misma corrida) y sin esto no se lo podría agrupar
    hasta mañana.
    """
    unidad = (row.get("unidad") or "").strip()
    if codigo := extract_cafci(unidad):
        claves = [("cafci", codigo)]
        if nombre := nombre_fci(unidad):
            claves.append(("nombre", nombre.upper()))
        return claves
    tk = _norm(row.get("ticker")) or _norm(_regla_ticker(row).get("ticker"))
    return [("ticker", tk.upper())] if tk else []


def anotar_herencia(rows: Iterable[dict]) -> dict:
    """Inyecta `_herencia` en cada fila que pueda recibir campos de su gemela.

    Es PURA (no toca la base) y devuelve el reporte del grupo — que es lo que se
    mira para entender el rebautizo: cuántos instrumentos aparecen duplicados,
    qué se copia, qué quedó frenado por desacuerdo entre donantes.

    El motor (`planificar`) hace el resto: solo escribe lo vacío y no pisa nada.
    """
    rows = list(rows)
    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        for clave in _claves_identidad(row):
            grupos[clave].append(row)

    # clave → campo → {valor normalizado: (valor crudo, unidad donante)}
    donantes: dict[tuple[str, str], dict[str, dict]] = {}
    divergencias: list[str] = []
    # Un mismo desacuerdo aparece bajo las DOS claves del fondo (código y nombre).
    # Es un solo problema para arreglar en Manager → se reporta una sola vez.
    ya_reportadas: set[tuple[str, frozenset]] = set()
    for clave, miembros in grupos.items():
        if len(miembros) < 2:            # sin gemela no hay de quién heredar
            continue
        por_campo: dict[str, dict] = {}
        for campo in _HEREDABLES:
            vistos: dict[str, tuple] = {}
            for m in miembros:
                if not _vacio(m.get(campo)):
                    vistos.setdefault(_norm(m[campo]), (m[campo], m["unidad"]))
            if len(vistos) > 1:
                # Dos valores distintos para el mismo instrumento: uno de los dos
                # está mal cargado. No se elige por el humano — se avisa.
                huella = (campo, frozenset(u for _, u in vistos.values()))
                if huella not in ya_reportadas:
                    ya_reportadas.add(huella)
                    divergencias.append(
                        f"{clave[0]}={clave[1]} · {campo}: "
                        + " vs ".join(f"{v!r} ({u})" for v, u in vistos.values()))
            elif vistos:
                por_campo[campo] = vistos
        if por_campo:
            donantes[clave] = por_campo

    campos = Counter()
    campos_no_fci = Counter()
    receptoras = no_fci_receptoras = 0
    detalle: list[str] = []
    for row in rows:
        row.pop(_HERENCIA_COL, None)     # re-anotar no arrastra lo de la pasada anterior
        herencia: dict[str, object] = {}
        pendiente: dict[str, object] = {}
        for clave in _claves_identidad(row):
            aplica = clave[0] != "ticker" or HEREDAR_NO_FCI
            destino = herencia if aplica else pendiente
            for campo, vistos in (donantes.get(clave) or {}).items():
                if campo in herencia or campo in pendiente or not _vacio(row.get(campo)):
                    continue
                valor, donante = next(iter(vistos.values()))
                destino[campo] = valor
                if aplica:
                    detalle.append(f"{row['unidad']} ← {campo}={valor!r} "
                                   f"(de {donante}, por {clave[0]})")
        if herencia:
            row[_HERENCIA_COL] = herencia
            receptoras += 1
            campos.update(herencia.keys())      # cuenta CAMPOS, no valores
        if pendiente:
            no_fci_receptoras += 1
            campos_no_fci.update(pendiente.keys())

    # Un mismo fondo cae en DOS grupos (su código y su nombre) con los mismos
    # miembros: contar claves diría "2 instrumentos" donde hay uno. Se cuentan
    # los CONJUNTOS de unidades distintos.
    instrumentos = {frozenset(m["unidad"] for m in grupos[c]) for c in donantes}

    return {"grupos": len(instrumentos), "receptoras": receptoras,
            "campos": dict(campos), "divergencias": divergencias,
            "no_fci_receptoras": no_fci_receptoras,
            "no_fci_campos": dict(campos_no_fci), "detalle": detalle}


def _regla_herencia(row: dict) -> dict[str, object]:
    """Lo que `anotar_herencia` dejó preparado para esta fila. Sin anotar, no opina."""
    return dict(row.get(_HERENCIA_COL) or {})


@dataclass(frozen=True)
class Regla:
    id: str
    titulo: str
    fn: Callable[[dict], dict[str, object]]



# ── DERIVADOS: los OTC y los futuros de agro ───────────────────────────────
#
# Pedido del user (2026-08-27): *«OTC MAI SOJ o TRI son siempre cartera
# DERIVADOS»*. Salió de mirar el listado de `ficha_incompleta`: de los 29
# títulos en cartera de cliente sin CARTERA, la mayoría eran estos.
#
# ⚠️⚠️ **VA ACÁ Y NO EN EL AGENTE, y la diferencia es de modelo.** El agente
# DETECTA; `assets_autofill` DERIVA. Si esta regla viviera adentro del detector
# habría dos lugares sabiendo cómo se completa una ficha —el job de las 11:40 y
# el botón— y el día que difieran el botón escribiría algo distinto de lo que el
# job escribe todas las noches, sin que nada falle (REGLA #9).
#
# Puesta acá, el circuito se cierra solo: el job la completa, y en su próxima
# pasada `ficha_incompleta` ya no la ve y baja el contador. Nadie aprieta nada.
#
# ⚠️ Los prefijos NO se inventan acá: son los que el sistema ya usa para los
# futuros de agro (`api/services/derivados_agro.DISPO_LABELS` → `TRI.` / `MAI.`
# / `SOJ.`) y el marcador OTC que ya reconocen `jobs/_aum_filters` y
# `api/services/sin_operador`. Escribir una segunda lista sería exactamente la
# REGLA #9: dos criterios para la misma pregunta, coherentes cada uno consigo
# mismo.
CARTERA_DERIVADOS = "DERIVADOS"

# El TOKEN del commodity, no un `startswith` suelto: `MAI.ROS/ABR27` y
# `MAI.MIN/JUL27` empiezan con `MAI.`, pero un ticker que casualmente arranque
# con esas tres letras y NO tenga el punto no es un futuro. El punto es parte
# del nombre del contrato, así que se exige.
_PREFIJOS_AGRO = ("MAI.", "SOJ.", "TRI.")


def _regla_derivados_otc(row: dict) -> dict[str, str]:
    """CARTERA = DERIVADOS para los OTC y los futuros de agro.

    Mira la `unidad` Y el `ticker` ya derivado: la unidad de un futuro puede
    venir como `[MAI.ROS/ABR27 192 P]` (el id ES el contrato) o con descripción,
    y en el segundo caso el prefijo queda en el ticker y no en el id.
    """
    unidad = (row.get("unidad") or "").upper()
    # El ticker propio si ya está cargado; si no, el que derivaría `_regla_ticker`
    # en esta misma pasada. Sin esto la regla dependería del ORDEN en que corren
    # las reglas, que es la clase de acoplamiento que no se ve hasta que falla.
    tk = (_norm(row.get("ticker")) or _norm(_regla_ticker(row).get("ticker"))).upper()

    es_otc = "OTC" in unidad
    es_agro = any(x in unidad or tk.startswith(x) for x in _PREFIJOS_AGRO)
    return {"cartera": CARTERA_DERIVADOS} if (es_otc or es_agro) else {}


REGLAS: list[Regla] = [
    Regla("financiamiento", "Pagarés/cheques de FINANCIAMIENTO", _regla_financiamiento),
    Regla("financiamiento_clase", "HD/DL de financiamiento (por nominal)",
          _regla_financiamiento_clase),
    Regla("fci", "Fondos comunes (código CAFCI + nombre)", _regla_fci),
    Regla("derivados_otc", "CARTERA de los OTC y los futuros de agro",
          _regla_derivados_otc),
    Regla("ticker", "Ticker derivado de la unidad (resto del catálogo)", _regla_ticker),
    Regla("especies", "Símbolos de mercado ARS/USD desde mercado.especies",
          _regla_especies),
    Regla("herencia", "Campos manuales heredados de la unidad rebautizada",
          _regla_herencia),
]


# ── Motor ────────────────────────────────────────────────────────────────────

def leer_catalogo() -> list[dict]:
    """Catálogo + el NOMINAL de cada unidad en la última tenencia.

    El nominal no es una columna de `assets` — se agrega acá para que la regla
    `financiamiento_clase` siga siendo una función pura `fila → {columna: valor}`
    como todas las demás, en vez de tener que ir a la base por su cuenta.

    Es UNA query extra y sólo sobre el último día (`ix_tenencia_fecha_unidad`),
    no sobre la tenencia histórica. Las unidades sin tenencia hoy quedan con
    `nominal=None` y la regla no opina sobre ellas.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(_LEIBLES)} FROM portafolio.assets")
        rows = [dict(zip(_LEIBLES, r, strict=True)) for r in cur.fetchall()]
        cur.execute(
            "SELECT unidad, sum(cantidad) FROM portafolio.tenencia "
            "WHERE fecha = (SELECT max(fecha) FROM portafolio.tenencia) "
            "  AND cantidad IS NOT NULL "
            "GROUP BY unidad")
        nominal = {u: (float(c) if c is not None else None) for u, c in cur.fetchall()}
    for r in rows:
        r[_NOMINAL_COL] = nominal.get(r["unidad"])
    return rows


def leer_especies() -> list[dict]:
    """Patas ACTIVAS de `mercado.especies` — el único catálogo de símbolos.

    Tabla chica (cientos de filas) y sin filtro por ticker: traerla entera y
    agrupar en memoria es UNA query, contra una por asset. La lectura y el
    criterio de elección viven separados a propósito (`anotar_especies` es pura
    y testeable sin base).
    """
    cols = ("simbolo", "ticker", "especie", "plazo", "es_default")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(cols)} FROM mercado.especies "
                    "WHERE activa IS NOT false")
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def planificar(rows: Iterable[dict], reglas: Iterable[Regla]) -> tuple[dict, dict]:
    """`({unidad: {columna: valor}}, {regla_id: reporte})`. No toca la base."""
    rows = list(rows)
    cambios: dict[str, dict[str, object]] = defaultdict(dict)
    reporte: dict[str, dict] = {}
    for regla in reglas:
        campos: Counter[str] = Counter()
        conflictos: list[str] = []
        matcheadas = 0
        for row in rows:
            propuesta = regla.fn(row)
            if not propuesta:
                continue
            matcheadas += 1
            unidad = row["unidad"]
            for col, val in propuesta.items():
                if col not in _ESCRIBIBLES:
                    raise ValueError(f"regla {regla.id}: columna no escribible {col!r}")
                planeado = cambios[unidad].get(col)
                if planeado is not None and _norm(planeado) != _norm(val):
                    conflictos.append(f"{unidad} · {col}: otra regla ya propuso "
                                      f"{planeado!r}, esta propone {val!r}")
                    continue
                actual = row.get(col)
                if _vacio(actual):
                    cambios[unidad][col] = val
                    campos[col] += 1
                elif _norm(actual) != _norm(val):
                    conflictos.append(f"{unidad} · {col}: catálogo={_norm(actual)!r} "
                                      f"regla={val!r}")
        reporte[regla.id] = {"matcheadas": matcheadas, "campos": dict(campos),
                             "conflictos": conflictos}
    return {u: s for u, s in cambios.items() if s}, reporte


def aplicar(cambios: dict[str, dict[str, object]]) -> int:
    """UPDATE de los campos planificados. Agrupa por firma de columnas para mandar
    un `executemany` por combinación en vez de un UPDATE armado por fila."""
    if not cambios:
        return 0
    ts = datetime.now(UTC)
    por_firma: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for unidad, sets in cambios.items():
        por_firma[tuple(sorted(sets))].append(
            {**sets, "_unidad": unidad, "_actor": _ACTOR, "_ts": ts})
    n = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for cols, params in por_firma.items():
            asigna = ", ".join(f"{c} = %({c})s" for c in cols)
            cur.executemany(
                f"UPDATE portafolio.assets SET {asigna}, "
                "actualizado_por = %(_actor)s, actualizado_at = %(_ts)s "
                "WHERE unidad = %(_unidad)s", params)
            n += len(params)
        conn.commit()
    return n


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Autocompletado de portafolio.assets")
    ap.add_argument("--dry", action="store_true", help="reporta qué completaría, sin escribir")
    ap.add_argument("--regla", action="append", metavar="ID",
                    help=f"correr solo esta regla (repetible). Ids: {[r.id for r in REGLAS]}")
    args = ap.parse_args()

    pedidas = set(args.regla or ())
    if desconocidas := pedidas - {r.id for r in REGLAS}:
        raise SystemExit(f"regla(s) inexistente(s): {sorted(desconocidas)}")
    reglas = [r for r in REGLAS if not pedidas or r.id in pedidas]

    with JobRunLogger("assets_autofill") as jr:
        rows = leer_catalogo()
        # El contexto entre assets se arma UNA vez, antes de planificar: la regla
        # `herencia` lo lee de la fila igual que `financiamiento_clase` lee el nominal.
        her = anotar_herencia(rows)
        esp = anotar_especies(rows, leer_especies())
        cambios, reporte = planificar(rows, reglas)
        jr.log(f"catálogo: {len(rows)} assets · reglas: {[r.id for r in reglas]}")
        for r in reglas:
            rep = reporte[r.id]
            detalle = ", ".join(f"{c}={n}" for c, n in sorted(rep["campos"].items()))
            jr.log(f"  · {r.id}: {rep['matcheadas']} unidades matchean → "
                   f"{detalle or 'nada vacío que completar'}")
            # En cron los conflictos se capan (son estables: repetirlos enteros todos
            # los días inunda el log). Con --dry se listan todos: es el modo en el que
            # uno los va a revisar y corregir.
            tope = len(rep["conflictos"]) if args.dry else 10
            for c in rep["conflictos"][:tope]:
                jr.log(f"      ⚠ {c}")
            if len(rep["conflictos"]) > tope:
                jr.log(f"      … +{len(rep['conflictos']) - tope} conflicto(s) más "
                       f"(verlos: --dry)")
            jr.set_stat(f"{r.id}_matcheadas", rep["matcheadas"])
            jr.set_stat(f"{r.id}_campos", rep["campos"])
            jr.set_stat(f"{r.id}_conflictos", len(rep["conflictos"]))

        if any(r.id == "especies" for r in reglas):
            jr.log(f"  · especies: {esp['tickers_en_especies']} ticker(s) en el catálogo "
                   f"de símbolos → {esp['con_pata_ars']} asset(s) con pata ARS y "
                   f"{esp['con_pata_usd']} con pata USD disponibles")
            if esp["sin_ticker"]:
                jr.log(f"      ℹ {esp['sin_ticker']} asset(s) sin TICKER: no son "
                       f"relacionables con especies todavía")
            for k, v in esp.items():
                jr.set_stat(f"especies_{k}", v)

        if any(r.id == "herencia" for r in reglas):
            jr.log(f"  · herencia: {her['grupos']} instrumento(s) con más de una "
                   f"unidad → {her['receptoras']} asset(s) reciben campos")
            # Las divergencias son el ÚNICO caso que pide mano humana: dos unidades
            # del mismo instrumento con datos distintos. Van completas (son pocas y
            # cada una es un dato mal cargado que hay que arreglar en Manager).
            for d in her["divergencias"]:
                jr.log(f"      ⚠ desacuerdo entre donantes → {d}")
            tope = len(her["detalle"]) if args.dry else 20
            for linea in her["detalle"][:tope]:
                jr.log(f"      · {linea}")
            if len(her["detalle"]) > tope:
                jr.log(f"      … +{len(her['detalle']) - tope} más (verlas: --dry)")
            if her["no_fci_receptoras"]:
                jr.log(f"      ℹ fuera de FCI habría {her['no_fci_receptoras']} asset(s) "
                       f"para completar por ticker ({her['no_fci_campos']}) — "
                       f"NO se escriben (HEREDAR_NO_FCI=False)")
            jr.set_stat("herencia_grupos", her["grupos"])
            jr.set_stat("herencia_divergencias", len(her["divergencias"]))
            jr.set_stat("herencia_no_fci_receptoras", her["no_fci_receptoras"])
            jr.set_stat("herencia_no_fci_campos", her["no_fci_campos"])

        if args.dry:
            for unidad, sets in list(cambios.items())[:20]:
                jr.log(f"    [dry] {unidad} → {sets}")
            jr.log(f"[dry] {len(cambios)} assets se completarían — no se escribió nada")
            jr.set_stat("dry", True)
            return 0

        jr.set_stat("assets_actualizados", aplicar(cambios))
        jr.log(f"✅ {len(cambios)} assets completados (la API los relee en ≤5 min, TTL assets_sql)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
