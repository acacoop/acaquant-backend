"""core/especies.py — LAS PATAS de un bono. Una sola lógica, dos usuarios.

Doc madre: `docs/RENTA_FIJA.md` §0 paso 8 · `docs/AV_AGENT.md`.

**Qué es una especie.** Un mismo bono cotiza con varios símbolos según en qué
moneda se liquide: `AL30` en pesos, `AL30D` en dólar MEP, `AL30C` en cable. Cada
uno es una PATA, y `mercado.especies` es el único lugar donde vive esa relación
— de ahí salen `portafolio.assets.instrumento` / `instrumento_usd` (vía
`jobs/assets_autofill`) y de ahí sale el símbolo que el AV Agent escribe al dar
de alta un bono.

**Por qué esto vive en `core/` y no en el script.** La lógica la escribió
`scripts/sembrar_especies` para sembrar TODO el master de una. Cuando el AV
Agent tuvo que sembrar UN bono —el que acaba de dar de alta— la alternativa era
reescribirla, y una segunda implementación de "cómo se clasifica una pata"
termina siempre igual: las dos se separan y nadie sabe cuál manda. Acá está una
vez; el script siembra en lote y el agente siembra de a uno, con el MISMO
criterio.

## Las dos convenciones que conviven (y que fueron el bug de la primera corrida)

- **Soberanos y letras** — la especie es un SUFIJO sobre el ticker:
  `AL30` pesos · `AL30D` MEP · `AL30C` cable. La base es `AL30`.
- **ONs** — la especie es la ÚLTIMA LETRA del propio ticker: `AERBO` pesos ·
  `AERBD` dólares. La base es `AERBO`.

La segunda se **RECONOCE, no se adivina**: aplica solo cuando el par `stem+O` /
`stem+D` existe DE VERDAD en el universo. Sin esa condición, `AERBD` no matchea
la regex de sufijo (no tiene dígitos antes de la D) y caía a PESOS **siendo la
pata en dólares** — el precio quedaba de otra escala y el bono figuraba con dos
patas "pesos/24hs" duplicadas.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

RE_ESPECIE = re.compile(r"^([A-Z]+\d+)([DC])$")
# El sufijo del SÍMBOLO manda sobre el label: `engines/curvas.py:223` ya decide la
# moneda así ("el ticker_corto es un label humano y puede no reflejar la moneda").
ESPECIE = {"": ("pesos", "ARS"), "D": ("mep", "USD"), "C": ("cable", "USD")}


def segs(simbolo: str) -> list[str] | None:
    """`MERV - XMEV - AL30D - 24hs` → `[MERV, XMEV, AL30D, 24hs]`."""
    s = [x.strip() for x in (simbolo or "").split(" - ")]
    return s if len(s) >= 4 else None


def base(tk: str) -> tuple[str, str]:
    m = RE_ESPECIE.match((tk or "").strip().upper())
    return (m.group(1), m.group(2)) if m else ((tk or "").strip().upper(), "")


def clasificador(universo: set[str]):
    """→ `clasificar(ticker_especie) = (base, especie, moneda)`. PURO.

    `universo` son los `ticker_especie` conocidos — hace falta para reconocer el
    par O/D de las ONs sin adivinarlo (ver el docstring del módulo).
    """
    def clasificar(t: str) -> tuple[str, str, str]:
        t = (t or "").strip().upper()
        if len(t) >= 2 and t[-1] in ("O", "D"):
            stem = t[:-1]
            if f"{stem}O" in universo and f"{stem}D" in universo:
                esp, mon = ("pesos", "ARS") if t[-1] == "O" else ("mep", "USD")
                return f"{stem}O", esp, mon     # la pata en pesos nombra al bono
        m = RE_ESPECIE.match(t)
        if m:
            esp, mon = ESPECIE[m.group(2)]
            return m.group(1), esp, mon
        return t, "pesos", "ARS"
    return clasificar


def pata(simbolo: str, ticker: str, clasificar) -> dict | None:
    s = segs(simbolo)
    if not s:
        return None
    _, esp, mon = clasificar(s[2])
    return {"simbolo": simbolo, "ticker": ticker, "ticker_especie": s[2].upper(),
            "especie": esp, "moneda": mon, "plazo": s[3]}


# ── LAS PATAS POR FICHA, cuando el STRING no alcanza (2026-08-19) ───────────
#
# **El caso BOPREAL, que rompe las dos convenciones de arriba.** El user lo cazó
# mirando Manager → Títulos · Instrumentos:
#
#     MERV - XMEV - BPOA7 - CI     ARS   BOPREAL S. 1 A VTO31/10/27 U$S CG
#     MERV - XMEV - BPA7D - 24hs   USD   BOPREAL S. 1 A VTO31/10/27 U$S CG
#     MERV - XMEV - BPA7C - CI     USD   BOPREAL S. 1 A VTO31/10/27 U$S CG
#
# La pata en pesos se llama **BPOA7** y la de dólares **BPA7D**: no es `stem + D`
# — **se cae la O del medio**. `RE_ESPECIE` clasifica bien a `BPA7D` (base `BPA7`,
# especie mep) pero lo agrupa con un bono que no existe: el par nunca se arma, y
# los 6 BOPREALes salían como «no tiene pata en dólares» siendo que la tienen.
#
# La salida NO es agregarle un caso especial a la regex. Sería la tercera
# convención escrita a mano, y la cuarta la vamos a descubrir igual que ésta:
# tarde y por un bono que se veía raro en la pantalla.
#
# **Primary ya dice de qué bono es cada símbolo** — `underlying` + `maturity`
# vienen en el discovery y no hay que adivinarlos:
#
#     el ticker en pesos → su ficha (underlying, maturity)
#     esa misma ficha    → ¿qué otros símbolos la tienen, en otra moneda?
#
# Eso es un JOIN EXACTO sobre dos campos, no una heurística: o la ficha coincide
# o no. Funciona para BOPREAL, para AL30 y para el que venga con la convención
# que se les ocurra, porque **no mira el nombre**.
#
# ⚠️ **SOLO los símbolos `MERV - XMEV - …`**, y no es un detalle de formato.
# Primary publica los mismos papeles dos veces, y la forma corta trae un
# `underlying` GENÉRICO:
#
#     MERV - XMEV - BPOA7 - CI   →  "BOPREAL S. 1 A VTO31/10/27 U$S CG"   ← sirve
#     BPOA7/CI                   →  "Bopreales - Bonos BCRA"              ← NO
#
# Con la genérica, los 6 BOPREALes tendrían la MISMA ficha y cada uno heredaría
# las patas de los otros cinco. Un emparejamiento silencioso y equivocado es peor
# que no emparejar: el motor pediría el precio de otro bono y la fila se llenaría
# con un número creíble.

# Tope de símbolos que puede tener una ficha antes de dejar de creerle. Un bono
# tiene a lo sumo 3 especies × 2 plazos = 6; se deja el doble de aire. Si una
# ficha agrupa más, es genérica —o Primary cambió algo— y **no se usa**: es la
# misma degradación que el resto del módulo, ante la duda no se empareja.
MAX_POR_FICHA = 12


def _es_merv(simbolo: str) -> bool:
    return (simbolo or "").strip().upper().startswith("MERV - XMEV - ")


def fichas(filas: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """`(underlying, maturity)` → los símbolos de Primary que la comparten. PURA.

    `filas` son los `instruments` del discovery: `{ticker, underlying, maturity,
    currency}`. Se descartan las que no son `MERV - XMEV - …` (ver arriba) y las
    que no traen ficha completa — media ficha no identifica a nadie.
    """
    out: dict[tuple[str, str], list[dict]] = {}
    for f in filas or []:
        sim = (f.get("ticker") or "").strip()
        und = (f.get("underlying") or "").strip()
        mat = str(f.get("maturity") or "").strip()
        if not sim or not und or not mat or not _es_merv(sim):
            continue
        pedazos = segs(sim) or ["", "", "", ""]
        te = pedazos[2].upper()
        out.setdefault((und.upper(), mat), []).append({
            "simbolo": sim, "ticker_especie": te,
            "moneda": (f.get("currency") or "").strip().upper(),
            # La ESPECIE sale del sufijo, igual que en todo el módulo. Hace falta
            # para poder ordenar con `preferencia` — sin ella, MEP y cable quedan
            # a merced del alfabeto y **cable gana siempre** (`BPA7C` < `BPA7D`).
            # Fue exactamente el bug de la primera corrida: los 8 BOPREALes
            # emparejaron bien y eligieron la pata equivocada.
            "especie": ESPECIE.get(te[-1:] if te[-1:] in ("D", "C") else "",
                                   ("pesos", "ARS"))[0],
            "plazo": pedazos[3],
            "underlying": und, "maturity": mat})
    return out


def mejor(simbolos: list[str]) -> str:
    """El símbolo que la mesa mira, entre varios del mismo bono: **MEP antes que
    cable**, 24hs antes que CI.

    EL criterio, uno solo. Lo escribían por su cuenta la puerta del agente y el
    diag —los dos con un `sorted()` alfabético— y por eso los dos elegían cable:
    el orden del abecedario no es un criterio de mercado. Cable y MEP son cosas
    distintas y mezclarlas es de lo que ya advierte `CLAUDE.md`.
    """
    def clave(sim: str) -> tuple:
        te = ((segs(sim) or ["", "", "", ""])[2] or "").upper()
        suf = te[-1:] if te[-1:] in ("D", "C") else ""
        return preferencia({"especie": ESPECIE.get(suf, ("pesos", "ARS"))[0],
                            "plazo": (segs(sim) or ["", "", "", ""])[3],
                            "simbolo": sim})
    return sorted([s for s in (simbolos or []) if s], key=clave)[0] if simbolos else ""


def hermanas_por_ficha(ticker_especie: str, filas: list[dict],
                       *, moneda: str = "USD") -> list[dict]:
    """Las patas de OTRA moneda del mismo bono, encontradas por FICHA. PURA.

    `ticker_especie` es el corto tal como cotiza (`BPOA7`), no el símbolo entero.
    Devuelve los símbolos de Primary que comparten `underlying` + `maturity` y
    están en `moneda`, ordenados por preferencia (24hs antes que CI).

    Vacío si no se encuentra, si la ficha está incompleta o si agrupa demasiados
    símbolos: **«no pude emparejar» nunca se convierte en un emparejamiento.**
    """
    tk = (ticker_especie or "").strip().upper()
    mon = (moneda or "").strip().upper()
    if not tk:
        return []
    idx = fichas(filas)
    # La ficha del ticker que nos dieron. Puede aparecer en varios plazos: todos
    # comparten ficha, así que alcanza con encontrarlo una vez.
    clave = next((k for k, v in idx.items()
                  if any(x["ticker_especie"] == tk for x in v)), None)
    if clave is None:
        return []
    grupo = idx[clave]
    if len(grupo) > MAX_POR_FICHA:
        logger.warning("especies: la ficha %s agrupa %d símbolos (> %d) — no se "
                       "empareja por ficha", clave, len(grupo), MAX_POR_FICHA)
        return []
    hs = [x for x in grupo if x["moneda"] == mon and x["ticker_especie"] != tk]
    # `preferencia`, EL criterio del módulo: MEP antes que cable y 24hs antes que
    # CI. Acá había un orden propio que solo miraba el plazo, y por eso los 8
    # BOPREALes emparejaron bien y devolvieron la pata en CABLE.
    return sorted(hs, key=preferencia)


def preferencia(p: dict) -> tuple:
    """Orden de preferencia dentro de una moneda: MEP antes que cable (es la que
    mira la mesa) y 24hs antes que CI (ahí está la liquidez, y por lo tanto el
    precio)."""
    return (p["especie"] != "mep", p["plazo"] != "24hs", p["simbolo"])


def simbolos_primary() -> list[str]:
    """Los símbolos crudos de Primary (`manager.pyrofex_instruments`).

    Es la fuente que NO depende de lo que el master eligió — a diferencia de
    `market_snapshot`, que solo tiene lo que el motor suscribe y el motor
    suscribe desde el master (circular).
    """
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT i->>'ticker' "
                    "FROM manager.pyrofex_instruments p, "
                    "     jsonb_array_elements(p.instruments) i "
                    "WHERE i->>'ticker' IS NOT NULL")
        return [t for (t,) in cur.fetchall() if t]


def instrumentos_primary() -> list[dict]:
    """Los instruments CRUDOS de Primary, con su ficha completa.

    `simbolos_primary()` devuelve solo los nombres, que alcanza para preguntar
    «¿existe?». Para preguntar «¿de qué bono es?» hace falta la ficha —
    `underlying`, `maturity`, `currency`— y eso es lo que habilita
    `hermanas_por_ficha` (ver el caso BOPREAL arriba).
    """
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT i->>'ticker', i->>'underlying', i->>'maturity', "
                    "       i->>'currency' "
                    "FROM manager.pyrofex_instruments p, "
                    "     jsonb_array_elements(p.instruments) i "
                    "WHERE i->>'ticker' IS NOT NULL")
        return [{"ticker": a, "underlying": b, "maturity": c, "currency": d}
                for a, b, c, d in cur.fetchall()]


def patas_de(ticker: str, simbolos: list[str], *, moneda_bono: str = "",
             extra: tuple[str, ...] = ()) -> list[dict]:
    """Todas las patas de UN ticker, con `es_default` resuelto. PURA.

    **Devuelve las DOS monedas cuando existen** (pedido del user, 2026-08-17): un
    global tiene su pata en pesos y su pata en dólares, y las dos tienen que
    quedar — `assets.instrumento` sale de una e `instrumento_usd` de la otra.

    `es_default` es UNA por bono (la que dibuja la curva): se elige dentro de la
    moneda del bono, porque valuar un bono en dólares con su pata en pesos es
    exactamente la "cruzada" que el seeder reporta como error.
    """
    tk = (ticker or "").strip().upper()
    if not tk:
        return []
    universo = {s[2].upper() for x in simbolos if (s := segs(x))}
    clasificar = clasificador(universo)

    patas = []
    vistos: set[str] = set()
    for sim in simbolos:
        s = segs(sim)
        if not s:
            continue
        b, _, _ = clasificar(s[2].upper())
        if b == tk:
            p = pata(sim, tk, clasificar)
            if p:
                patas.append(p)
                vistos.add(sim)
    # **`extra` son símbolos que YA sabemos de este bono por otra vía** — hoy, la
    # ficha de Primary (`hermanas_por_ficha`), que es la única que caza al BOPREAL
    # porque su pata en dólares NO se parece a la de pesos (BPOA7 ↔ BPA7D).
    #
    # Entran forzados al grupo, pero **la moneda y la especie se siguen sacando
    # del sufijo** y no se inventan: `BPA7D` termina en D, así que el clasificador
    # acierta — lo único que estaba roto era a QUÉ bono pertenece, no qué es.
    for sim in extra:
        if sim in vistos:
            continue
        p = pata(sim, tk, clasificar)
        if p:
            patas.append(p)
            vistos.add(sim)
    if not patas:
        return []

    mon = (moneda_bono or "").strip().upper()
    candidatas = [p for p in patas if p["moneda"] == mon] or patas
    elegida = sorted(candidatas, key=preferencia)[0]
    for p in patas:
        p["es_default"] = p["simbolo"] == elegida["simbolo"]
    return sorted(patas, key=lambda p: p["simbolo"])


def escribir(filas: list[dict]) -> int:
    """Upsert por `simbolo` (la PK). Idempotente: re-sembrar no duplica."""
    if not filas:
        return 0
    from core.postgres import get_pool
    ahora = datetime.now(UTC)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO mercado.especies (simbolo, ticker, ticker_especie, especie, "
            "moneda, plazo, es_default, activa, actualizado_at) "
            "VALUES (%(simbolo)s, %(ticker)s, %(ticker_especie)s, %(especie)s, "
            "%(moneda)s, %(plazo)s, %(es_default)s, true, %(ts)s) "
            "ON CONFLICT (simbolo) DO UPDATE SET ticker = EXCLUDED.ticker, "
            "ticker_especie = EXCLUDED.ticker_especie, especie = EXCLUDED.especie, "
            "moneda = EXCLUDED.moneda, plazo = EXCLUDED.plazo, "
            "es_default = EXCLUDED.es_default, actualizado_at = EXCLUDED.actualizado_at",
            [{**f, "ts": ahora} for f in filas])
        return cur.rowcount or len(filas)


def sembrar_ticker(ticker: str, *, moneda_bono: str = "") -> dict:
    """Siembra las patas de UN bono. **Nunca levanta** — devuelve el resultado.

    Lo llama el AV Agent como PASO FINAL del alta: sin especie el bono queda
    escrito pero su símbolo es una convención sin verificar, y el catálogo de
    Primary es el único que puede decir con qué símbolo cotiza de verdad.
    """
    try:
        sims = simbolos_primary()
    except Exception as e:
        return {"ok": False, "error": f"no se pudo leer el catálogo de Primary: "
                                      f"{type(e).__name__}", "patas": []}
    # LA FICHA, para los que el nombre no empareja (BOPREAL). Es un intento
    # ADICIONAL y nunca reemplaza al de siempre: si falla, se siembra igual lo que
    # el string sí encontró — degradar es distinto de fallar.
    extra: tuple[str, ...] = ()
    try:
        insts = instrumentos_primary()
        extra = tuple(h["simbolo"] for m in ("USD", "ARS")
                      for h in hermanas_por_ficha(ticker, insts, moneda=m))
    except Exception as e:
        logger.warning("especies: no pude emparejar %s por ficha (%s)", ticker, e)
    patas = patas_de(ticker, sims, moneda_bono=moneda_bono, extra=extra)
    if not patas:
        return {"ok": False, "patas": [],
                "error": f"Primary no lista ninguna especie de {ticker} — no hay "
                         "nada que sembrar. Puede que todavía no haya listado, o "
                         "que el catálogo esté viejo (`scripts.discovery_pyrofex`)."}
    try:
        n = escribir(patas)
    except Exception as e:
        return {"ok": False, "patas": patas,
                "error": f"no se pudo escribir mercado.especies: {type(e).__name__}"}
    return {"ok": True, "patas": patas, "escritas": n,
            "simbolos": [p["simbolo"] for p in patas],
            "monedas": sorted({p["moneda"] for p in patas})}
