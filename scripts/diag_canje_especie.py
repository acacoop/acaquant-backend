"""diag_canje_especie.py — busca boletos de "Cambio de especie" en
CashFlow.NegocioMovimientos para entender la shape antes de codear el
parser de canjes (FCI clase B → clase C, etc.).

Anonimiza id_cuenta (hash 4 chars) y NO imprime importes/precios. Sirve
para que el user pueda compartir el output sin filtrar nombres ni plata.

Lo que el script responde:
  1. ¿Cuántos boletos de "Cambio de especie" hay en NegocioMovimientos?
  2. ¿Cómo los está categorizando hoy `aunesa_negocio.py`? (probablemente
     "otro" — ese es justo el bug a fixar).
  3. ¿Aparecen los dos lados? Para cada (cuenta_hash, fecha) cuento si
     hay CEV (entrega) + CRV (recepción) — eso confirma que el parser
     puede linkar ambos lados.
  4. ¿Qué texto exacto usa Aunesa? Imprime los strings distintos del
     campo `informacion` (deduplicados, top 20).
  5. ¿Qué `ticker` carga el boleto? Importante para saber si el ticker
     ya distingue B vs C (ej "SCHRODER B" vs "SCHRODER C") o si hay que
     parsear "clase X" del texto.

Uso:
    python -m scripts.diag_canje_especie
    python -m scripts.diag_canje_especie --desde 2026-01-01

Output: solo a stdout, no escribe a Mongo.
"""
from __future__ import annotations

import argparse
import hashlib
import re
from collections import Counter, defaultdict

from core.mongo import get_mongo_client

DB_NAME = "CashFlow"
COL_NAME = "NegocioMovimientos"

# Detecta cualquier variante (con/sin tilde, comillas, etc.)
PATRON_TEXTO = re.compile(r"cambio\s+de\s+especie", re.IGNORECASE)

# Parsea "Recepción de valores - Cambio de especie de clase B a C"
PATRON_RECEPCION = re.compile(
    r"recepci[oó]n\s+de\s+valores.*cambio\s+de\s+especie\s+de\s+clase\s+"
    r'["\']?(?P<origen>[\w./-]+)["\']?\s+a\s+["\']?(?P<destino>[\w./-]+)',
    re.IGNORECASE,
)
# Parsea "Entrega de valores - Cambio de especie a clase C"
PATRON_ENTREGA = re.compile(
    r"entrega\s+de\s+valores.*cambio\s+de\s+especie\s+a\s+clase\s+"
    r'["\']?(?P<destino>[\w./-]+)',
    re.IGNORECASE,
)


def _hash_cuenta(c: str | None) -> str:
    """4 chars del SHA1 de la cuenta — anonimiza pero permite agrupar."""
    if not c:
        return "----"
    return hashlib.sha1(str(c).encode("utf-8")).hexdigest()[:4]


def _clasificar_lado(info: str) -> str:
    """Devuelve 'recepcion', 'entrega' o 'otro' según texto."""
    low = (info or "").lower()
    if "recepci" in low and "cambio de especie" in low:
        return "recepcion"
    if "entrega" in low and "cambio de especie" in low:
        return "entrega"
    return "otro"


def run(desde: str | None = None) -> None:
    print("=" * 110)
    print("DIAG CashFlow.NegocioMovimientos — boletos de 'Cambio de especie'")
    print("=" * 110)

    coll = get_mongo_client()[DB_NAME][COL_NAME]

    query: dict = {"informacion": {"$regex": PATRON_TEXTO}}
    if desde:
        query["fecha"] = {"$gte": desde}

    docs = list(coll.find(
        query,
        {
            "_id":         0,
            "fecha":       1,
            "comprobante": 1,
            "cuenta":      1,
            "categoria":   1,
            "op":          1,
            "ticker":      1,
            "cantidad":    1,
            "moneda":      1,
            "informacion": 1,
        },
    ).sort("fecha", 1))

    total = len(docs)
    print(f"\nTotal boletos con 'cambio de especie': {total}")
    if total == 0:
        print("\n∅ No hay ningún boleto en NegocioMovimientos con ese texto.")
        print("   → puede ser que el cron `negocio_movimientos.py` no esté capturando")
        print("     todavía las fechas del canje, o que Aunesa use otro string.")
        return

    # Rango de fechas
    fechas = sorted({d.get("fecha", "") for d in docs if d.get("fecha")})
    print(f"Rango de fechas: {fechas[0]}  →  {fechas[-1]}")

    # ── 1. Categoría actual (lo que está guardando aunesa_negocio.py) ──
    print("\n── 1. Categoría actual (cómo aunesa_negocio.py los clasifica hoy) ──")
    cats = Counter(d.get("categoria") for d in docs)
    for c, n in cats.most_common():
        print(f"   {n:5d}  {c!r}")

    # ── 2. Op parseado del texto ──
    print("\n── 2. Campo `op` parseado ──")
    ops = Counter(d.get("op") for d in docs)
    for o, n in ops.most_common():
        print(f"   {n:5d}  {o!r}")

    # ── 3. Lado (entrega/recepción) ──
    print("\n── 3. Lado del canje (parseado del texto `informacion`) ──")
    lados = Counter(_clasificar_lado(d.get("informacion", "")) for d in docs)
    for l, n in lados.most_common():
        print(f"   {n:5d}  {l}")

    # ── 4. Tickers ──
    print("\n── 4. Tickers distintos (ver si distingue clase B vs C) ──")
    tickers = Counter(d.get("ticker") for d in docs)
    for t, n in tickers.most_common(30):
        print(f"   {n:5d}  {t!r}")

    # ── 5. Signo de cantidad ──
    print("\n── 5. Signo de `cantidad` por lado ──")
    signo_por_lado: dict[str, Counter] = defaultdict(Counter)
    for d in docs:
        lado = _clasificar_lado(d.get("informacion", ""))
        cant = d.get("cantidad")
        if cant is None:
            signo = "null"
        elif cant > 0:
            signo = "positivo (+)"
        elif cant < 0:
            signo = "negativo (-)"
        else:
            signo = "cero"
        signo_por_lado[lado][signo] += 1
    for lado, counter in signo_por_lado.items():
        print(f"   [{lado}]")
        for s, n in counter.most_common():
            print(f"      {n:5d}  {s}")

    # ── 6. Strings distintos del texto `informacion` (dedup, top 20) ──
    print("\n── 6. Strings distintos del campo `informacion` (top 20) ──")
    # Tomo prefijo de 110 chars para no romper línea pero mostrar el patrón.
    strings = Counter()
    for d in docs:
        s = (d.get("informacion") or "").strip()
        # Si tiene cantidad/precio embebido, lo truncamos a la parte
        # textual. Aunesa suele formatear:
        #   "CEV X - Entrega de valores - Cambio de especie a clase \"C\""
        # NO siempre tiene cantidad/precio inline (canjes raras veces sí).
        strings[s[:110]] += 1
    for s, n in strings.most_common(20):
        print(f"   {n:5d}  {s}")

    # ── 7. Pares CEV (entrega) + CRV (recepción) por (cuenta_hash, fecha) ──
    print("\n── 7. Pares entrega+recepción por (cuenta_hash, fecha) ──")
    print("       (si hay ambos lados en una cuenta/fecha → el parser los puede linkar)")
    por_cuenta_fecha: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"entrega": 0, "recepcion": 0})
    for d in docs:
        cta_hash = _hash_cuenta(d.get("cuenta"))
        fecha = d.get("fecha", "")
        lado = _clasificar_lado(d.get("informacion", ""))
        if lado in ("entrega", "recepcion"):
            por_cuenta_fecha[(cta_hash, fecha)][lado] += 1

    con_ambos = 0
    solo_entrega = 0
    solo_recepcion = 0
    for _, lados in por_cuenta_fecha.items():
        if lados["entrega"] > 0 and lados["recepcion"] > 0:
            con_ambos += 1
        elif lados["entrega"] > 0:
            solo_entrega += 1
        elif lados["recepcion"] > 0:
            solo_recepcion += 1
    print(f"   Pares completos (entrega + recepción)   : {con_ambos}")
    print(f"   Solo entrega (sin recepción matcheable) : {solo_entrega}")
    print(f"   Solo recepción (sin entrega matcheable) : {solo_recepcion}")

    # ── 8. Detalle de boletos (anonimizado) ──
    print("\n── 8. Detalle (hasta 30 boletos, anonimizado) ──")
    print(f"  {'FECHA':<12} {'CTA':<6} {'LADO':<10} {'CATEG':<22} "
          f"{'TICKER':<22} {'CANT_SIGNO':<11} {'MONEDA':<8} TEXTO")
    print("  " + "-" * 200)
    docs_show = docs[:30]
    for d in docs_show:
        fecha = str(d.get("fecha", ""))[:10]
        cta = _hash_cuenta(d.get("cuenta"))
        lado = _clasificar_lado(d.get("informacion", ""))
        categ = str(d.get("categoria") or "")[:22]
        ticker = str(d.get("ticker") or "")[:22]
        cant = d.get("cantidad")
        signo = "+" if (cant or 0) > 0 else "-" if (cant or 0) < 0 else "0"
        moneda = str(d.get("moneda") or "")[:8]
        texto = (d.get("informacion") or "").strip()[:120]
        print(f"  {fecha:<12} {cta:<6} {lado:<10} {categ:<22} "
              f"{ticker:<22} {signo:<11} {moneda:<8} {texto}")

    # ── 9. Si parseamos "de clase X a Y": qué pares de clases aparecen ──
    print("\n── 9. Pares 'clase origen → clase destino' (de recepciones) ──")
    pares = Counter()
    for d in docs:
        info = d.get("informacion", "")
        m = PATRON_RECEPCION.search(info)
        if m:
            pares[(m.group("origen"), m.group("destino"))] += 1
    if pares:
        for (o, dst), n in pares.most_common(10):
            print(f"   {n:5d}  '{o}' → '{dst}'")
    else:
        print("   (ninguna recepción matchea el patrón regex 'de clase X a Y')")

    # ── 10. Si parseamos "a clase X" desde la entrega ──
    print("\n── 10. Clase destino (de entregas, sin origen explícito) ──")
    destinos = Counter()
    for d in docs:
        info = d.get("informacion", "")
        m = PATRON_ENTREGA.search(info)
        if m:
            destinos[m.group("destino")] += 1
    if destinos:
        for dst, n in destinos.most_common(10):
            print(f"   {n:5d}  → clase '{dst}'")
    else:
        print("   (ninguna entrega matchea el patrón regex 'a clase X')")

    print("\n" + "=" * 110)
    print("FIN diag_canje_especie")
    print("=" * 110)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--desde", help="Fecha mínima YYYY-MM-DD (default: todas)")
    args = p.parse_args()
    run(desde=args.desde)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
