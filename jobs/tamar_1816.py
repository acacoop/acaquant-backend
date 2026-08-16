"""jobs/tamar_1816.py — la TEA y el MARGEN de los TAMAR (y de cada pata de un dual).

Doc madre: `docs/RENTA_FIJA.md` §0. Escribe `mercado.tamar_1816`.

**Qué resuelve.** Los bonos con pata TAMAR no tenían NINGÚN cálculo: caen en el
`else` de `engines/curvas.py`, que solo computa duration. No estaban mal valuados
—estaban sin valuar— y no se notaba porque una celda vacía no rompe nada. Y de un
TAMAR lo que la mesa mira es el **MARGEN sobre la TAMAR**, que directamente no
existía en el sistema.

**Por qué se trae en vez de calcularse.** Un TAMAR es una nota de tasa PROMEDIO
(el cupón promedia la TAMAR de bancos privados entre T−10 hábiles de emisión y
T−10 del vencimiento, más un margen de licitación): la parte ya observada está
congelada y la futura hay que proyectarla. Medido el 2026-08-16 contra la planilla
de la mesa, 1816 da TXMD9 TEA 38,62% / margen 9,73% y la planilla 38,55% / 9,71%
al MISMO precio — la mesa ya valida contra 1816. Decisión del user: traerlo.

**Los duales salen gratis con la misma llamada.** 1816 publica cada pata como un
TICKER APARTE (`TXMD9 @CER`, `TXMD9 @TAMAR`) con su propio cashflow y su propia
tasa. Verificado: entre patas hay ~2.900 bps de diferencia. Por eso la tabla
guarda UNA FILA POR PATA — `market_snapshot` no puede, tiene una sola TEA por
símbolo, y por eso la vista venía mostrando el mismo número en las dos tablas.

**Lo que NO hace: tocar `mercado.market_snapshot`.** Esa tabla es del motor (live,
Primary, cada 5s) y esto es 1816 (BYMA, con delay). Mezclarlos dejaría una TEA sin
forma de saber de dónde salió. La vista los junta EN LA LECTURA y marca la
procedencia (`curvas_vista`). Además el motor NO pisa estas tasas aunque quisiera:
para la rama `otros` `dep_tasa_disponible` devuelve False, así que el
anti-TEA-fantasma no las limpia.

**Costo**: ~25 tickers × 6 campos ≈ 150 créditos por corrida × 15 corridas/día
≈ 2.250 de los 100.000 diarios.

Cron: cada 30' de 10 a 17 ART en días hábiles (ver `deploy/crontab.txt`).

Uso:
    python -m jobs.tamar_1816                    # normal (cron)
    python -m jobs.tamar_1816 --dry-run          # universo + costo, sin pegar ni escribir
    python -m jobs.tamar_1816 --fecha 2026-08-14 # forzar una rueda concreta
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging

from core import mercado_1816
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Los campos que la API ACEPTA (verificado 2026-08-16 probándolos de a uno:
# `margen`, `margin`, `spreadTamar` y `margenTamar` devuelven HTTP 400).
# `spread` ES el «Margen» del header de 1816: para TXMD9 @TAMAR devolvió 0.0973
# contra el 9,73% de la pantalla.
_CAMPOS = ["tea", "tna", "spread", "precioClean", "duration", "paridad"]

# Sufijo de 1816 → nuestro `ajuste`. Tabla explícita y no un parser, por la misma
# razón que `core.curvas_ejes.EJES_1816`: un sufijo nuevo tiene que REPORTARSE,
# no clasificarse mal en silencio. `BONCAP` es un bono a tasa fija (así lo llama
# 1816 en la denominación de la pata fija de los duales TTD26/TTS26).
_SUFIJO_A_AJUSTE = {
    "TAMAR": "tamar",
    "CER": "cer",
    "TASA FIJA": "fija",
    "BONCAP": "fija",
    "USD-L": "dolar_linked",
}

# Cuántos días hábiles retroceder si la rueda pedida vuelve sin datos (feriados).
_MAX_RETROCESO = 4


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _habil_anterior(d: dt.date) -> dt.date:
    """Día hábil anterior (solo fines de semana). Los feriados los resuelve el
    retroceso por respuesta vacía — no hace falta un calendario para eso."""
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def _sufijo(tk: str) -> str:
    return tk.split("@", 1)[1].strip().upper() if "@" in tk else ""


def universo() -> tuple[dict[str, tuple[str, str]], list[str]]:
    """`{ticker_1816: (nuestro_ticker, pata)}` + los sufijos que no sabemos leer.

    **La grafía la manda el CATÁLOGO de 1816, no nosotros.** Armar `f"{tk} @TAMAR"`
    a mano parece obvio y es frágil: basta que cambien el espacio para que el job
    devuelva vacío sin error. Se leen las variantes que 1816 YA publica en
    `research.mkt_1816_instrumentos` (la llena `mercado_1816_discovery --catalogo`).

    Un TAMAR puro no tiene variantes: se pide el ticker pelado, que es correcto
    porque no hay dos patas que separar.
    """
    bonos = _q("""
        SELECT ticker, ajuste, ajuste_alt FROM mercado.curvas
        WHERE ajuste = 'tamar' OR ajuste_alt = 'tamar'
    """)
    variantes = _q("""
        SELECT ticker, denominacion FROM research.mkt_1816_instrumentos
        WHERE ticker ILIKE '%%@%%'
    """)
    por_base: dict[str, list[dict]] = {}
    for v in variantes:
        base = str(v["ticker"]).split("@", 1)[0].strip().upper()
        por_base.setdefault(base, []).append(v)

    pedidos: dict[str, tuple[str, str]] = {}
    desconocidos: list[str] = []
    for b in bonos:
        tk = str(b["ticker"]).upper()
        vs = por_base.get(tk)
        if not vs:
            pedidos[tk] = (tk, b["ajuste"])          # TAMAR puro: una sola pata
            continue
        for v in vs:
            tk1816 = str(v["ticker"])
            suf = _sufijo(tk1816)
            pata = _SUFIJO_A_AJUSTE.get(suf)
            if not pata:
                desconocidos.append(f"{tk1816} (sufijo {suf!r})")
                continue
            pedidos[tk1816] = (tk, pata)
            # ALIAS: en TTD26/TTS26 el catálogo guarda el ticker como
            # `@TASA FIJA` pero la denominación dice `@BONCAP`, y la corrida del
            # 2026-08-16 mostró que con `@TASA FIJA` 1816 no devuelve NADA. No se
            # adivina cuál es el bueno: se piden LOS DOS en la misma llamada (6
            # créditos más) y gana el que traiga datos. Si algún día unifican la
            # grafía, el alias simplemente vuelve vacío y no molesta.
            suf_den = _sufijo(str(v.get("denominacion") or ""))
            if suf_den and suf_den != suf and suf_den in _SUFIJO_A_AJUSTE:
                pedidos[f"{tk} @{suf_den}"] = (tk, _SUFIJO_A_AJUSTE[suf_den])
    return pedidos, desconocidos


def _mejor_por_pata(pedidos: dict[str, tuple[str, str]],
                    inst: dict) -> dict[tuple[str, str], dict]:
    """(ticker, pata) → la mejor respuesta para esa pata.

    Hace falta porque el alias hace que una misma pata se pida con DOS grafías.
    Gana la que trae TEA; si ninguna trae nada la pata no se escribe — dejar la
    fila anterior es más honesto que pisarla con nulls, y `actualizado_en` delata
    que quedó vieja.
    """
    out: dict[tuple[str, str], dict] = {}
    for tk1816, (nuestro, pata) in pedidos.items():
        v = inst.get(tk1816) or {}
        if v.get("tea") is None:
            continue
        clave = (nuestro, pata)
        if clave not in out:
            out[clave] = {"ticker_1816": tk1816, **v}
    return out


def _upsert(filas: list[dict]) -> int:
    if not filas:
        return 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO mercado.tamar_1816 (ticker,pata,ticker_1816,tea,tna,spread,"
            "precio_clean,duration,paridad,fecha_operacion) VALUES "
            "(%(ticker)s,%(pata)s,%(ticker_1816)s,%(tea)s,%(tna)s,%(spread)s,"
            "%(precio_clean)s,%(duration)s,%(paridad)s,%(fecha_operacion)s) "
            "ON CONFLICT (ticker,pata) DO UPDATE SET "
            "ticker_1816 = EXCLUDED.ticker_1816, tea = EXCLUDED.tea, "
            "tna = EXCLUDED.tna, spread = EXCLUDED.spread, "
            "precio_clean = EXCLUDED.precio_clean, duration = EXCLUDED.duration, "
            "paridad = EXCLUDED.paridad, fecha_operacion = EXCLUDED.fecha_operacion, "
            "actualizado_en = now()",
            filas,
        )
    return len(filas)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha", help="forzar una rueda YYYY-MM-DD (default: hoy, "
                                    "retrocediendo si no hay datos)")
    ap.add_argument("--dry-run", action="store_true",
                    help="universo + costo; NO pega a la API ni escribe")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    pedidos, desconocidos = universo()
    if not pedidos:
        print("✗ ningún bono con pata TAMAR en mercado.curvas — nada que hacer.")
        return
    costo = len(pedidos) * len(_CAMPOS)
    print(f"Universo: {len(pedidos)} tickers × {len(_CAMPOS)} campos ≈ {costo} créditos")
    if desconocidos:
        print(f"⚠️ {len(desconocidos)} variantes con sufijo desconocido (NO se piden; "
              f"sumar a _SUFIJO_A_AJUSTE): {', '.join(desconocidos)}")

    if args.dry_run:
        for tk1816, (nuestro, pata) in sorted(pedidos.items()):
            print(f"   {tk1816:<22} → {nuestro:<8} pata={pata}")
        print("(DRY-RUN — no se pegó a la API ni se escribió)")
        return

    if not mercado_1816.disponible():
        print("✗ falta MERCADO_1816_API_KEY en el .env")
        return

    from core.job_runs import JobRunLogger
    with JobRunLogger("tamar_1816") as jr:
        # ⚠️ SIEMPRE con fechaOperacion explícita. Sin ella la API usa HOY y un
        # día sin rueda devuelve los 6 campos en null — un domingo eso hizo
        # parecer que el campo `spread` no existía (incidente 2026-08-16).
        d = dt.date.fromisoformat(args.fecha) if args.fecha else dt.date.today()
        if d.weekday() >= 5:
            d = _habil_anterior(d)
        tickers = sorted(pedidos)
        inst: dict = {}
        resp: dict = {}
        for _ in range(_MAX_RETROCESO + 1):
            resp = mercado_1816.indicadores(tickers, _CAMPOS, fecha_operacion=d.isoformat())
            inst = resp.get("instrumentos") or {}
            if any(v.get("tea") is not None for v in inst.values() if v):
                break
            # Antes de las 11 ART todavía no hay rueda de hoy, y un feriado no
            # la va a tener nunca: en los dos casos el número bueno es el del
            # último día con datos, no un vacío.
            jr.log(f"{d}: sin datos, retrocedo un hábil")
            d = _habil_anterior(d)
        else:
            jr.set_stat("filas", 0)
            jr.set_stat("motivo", "sin datos en 5 ruedas")
            print(f"✗ 5 ruedas seguidas sin datos (última probada {d}). No se escribió nada.")
            return

        fecha_op = resp.get("fechaOperacion") or d.isoformat()
        mejores = _mejor_por_pata(pedidos, inst)
        filas = [{
            "ticker": tk, "pata": pata, "ticker_1816": v["ticker_1816"],
            "tea": v.get("tea"), "tna": v.get("tna"), "spread": v.get("spread"),
            "precio_clean": v.get("precioClean"), "duration": v.get("duration"),
            "paridad": v.get("paridad"), "fecha_operacion": fecha_op,
        } for (tk, pata), v in mejores.items()]
        n = _upsert(filas)

        # Lo que 1816 NO trajo se CUENTA, no se silencia: es el número que dice si
        # mañana la vista tiene menos tasas de las que debería.
        sin_dato = sorted({f"{tk}/{pata}" for tk, pata in pedidos.values()}
                          - {f"{tk}/{pata}" for tk, pata in mejores})
        con_margen = sum(1 for f in filas if f["spread"] is not None)
        jr.set_stat("filas", n)
        jr.set_stat("con_margen", con_margen)
        jr.set_stat("sin_dato", len(sin_dato))
        jr.set_stat("fecha_operacion", fecha_op)
        if sin_dato:
            jr.log(f"sin dato en 1816: {', '.join(sin_dato)}")

    print(f"✅ {n} patas actualizadas en mercado.tamar_1816 (rueda {fecha_op}) · "
          f"{con_margen} con margen · {len(sin_dato)} sin dato")
    if sin_dato:
        print(f"   sin dato: {', '.join(sin_dato)}")


if __name__ == "__main__":
    main()
