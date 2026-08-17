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


def patas_de(ticker: str, simbolos: list[str], *, moneda_bono: str = "") -> list[dict]:
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
    for sim in simbolos:
        s = segs(sim)
        if not s:
            continue
        b, _, _ = clasificar(s[2].upper())
        if b == tk:
            p = pata(sim, tk, clasificar)
            if p:
                patas.append(p)
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
    patas = patas_de(ticker, sims, moneda_bono=moneda_bono)
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
