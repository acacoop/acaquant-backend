"""`scripts/diag_emisor.py` — **DE DÓNDE PUEDE SALIR CADA EMISOR QUE FALTA.**

Read-only. No escribe una sola fila. Doc del hallazgo: `docs/AGENT.md` §5 →
habilidad `ficha_incompleta`, regla `sin_emisor`.

## Qué contesta, y por qué esas preguntas

El pedido es que el agente complete el `emisor` solo, con el valor **ya
resuelto**, para que una persona confirme. Antes de diseñar eso hay que separar
tres cosas que hoy se ven iguales en la pantalla —una fila sin emisor— y se
arreglan en lugares opuestos:

    PLOMERÍA    el dato YA EXISTE en el sistema y no llegó. `jobs/ficha_1816`
                escribe el emisor en `portafolio.assets` todas las noches
                joineando por ticker contra el catálogo de 1816. Si una fila de
                renta fija está vacía, o el ticker no matchea, o 1816 no lo
                lista, o el job no corrió. **Ninguna se arregla con un modelo.**
    REGLA       se deduce sin ambigüedad (FINANCIAMIENTO → OTROS). Es una línea
                en `jobs/assets_autofill.py`, gratis y sin confirmación.
    MODELO      hay que ENTENDER algo (el nombre del fondo dice el emisor; el
                subyacente de un CEDEAR dice la empresa). Es lo único que hay
                que pagar y lo único que necesita que alguien confirme.

**Poner un LLM encima sin medir esto sería pagarle a un modelo para tapar un
join roto.** Este diag mide cuál es cuál, con números, antes de escribir nada.

## De dónde saca el universo

⚠️ **NO reimplementa la query del detector.** Llama a
`agente.detectores.catalogo.faltantes()`, que es la MISMA que usa el hallazgo
para contar y el arreglo para armar el listado. Dos definiciones de «a quién le
falta el emisor» darían números distintos sin que ninguna falle (REGLA #9), y
este diag existe justamente para decidir sobre esos números.

## Costo

Todo sale de Postgres y son consultas acotadas al universo del detector (los
títulos EN CARTERA DE CLIENTE, no el catálogo entero). **Nada toca la API de
1816** — se lee `research.mkt_1816_instrumentos`, que ya está persistida.

`--finnhub` es la ÚNICA parte que sale a la red, está apagada por default y
tiene tope: prueba una muestra de subyacentes contra `core.finnhub` (que ya
limita a 40 req/min) para ver **qué devuelve de verdad** con una acción y con
un ETF. Es la diferencia entre suponer que Finnhub trae el nombre y saberlo.

    python -m scripts.diag_emisor
    python -m scripts.diag_emisor --detalle          # fila por fila
    python -m scripts.diag_emisor --finnhub 12       # + probar 12 subyacentes
"""
from __future__ import annotations

import argparse
import sys

from core.postgres import get_pool

# El campo que se está diagnosticando. Sale del catálogo de detectores para que
# el diag no pueda mirar una definición de «falta el emisor» distinta de la que
# mira el agente.
CAMPO = "emisor"


def _filas(sql: str, params: tuple = ()) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def _tabla(filas: list[tuple], cols: tuple[str, ...], anchos: tuple[int, ...]) -> None:
    print("  " + " ".join(f"{c:<{a}}" for c, a in zip(cols, anchos, strict=True)))
    print("  " + " ".join("─" * a for a in anchos))
    for f in filas:
        print("  " + " ".join(f"{(v if v is not None else '—')!s:<{a}}"[:a]
                              for v, a in zip(f, anchos, strict=True)))


def _definicion():
    """La fila de `CAMPOS` que define «sin emisor». Se lee, no se copia."""
    from agente.detectores.catalogo import CAMPOS
    d = next((c for c in CAMPOS if c["campo"] == CAMPO), None)
    if d is None:
        raise SystemExit(f"«{CAMPO}» no está en agente.detectores.catalogo.CAMPOS")
    return d


# ── 1. EL UNIVERSO ─────────────────────────────────────────────────────────
def universo() -> list[dict]:
    """A quién le falta el emisor, según el detector y no según este script."""
    from agente.detectores import catalogo as det
    return det.faltantes(_definicion())


def por_cartera(filas: list[dict]) -> list[tuple]:
    """El corte que decide el diseño: cada cartera se resuelve por otra vía."""
    cuenta: dict[str, int] = {}
    sin_ticker: dict[str, int] = {}
    for f in filas:
        c = (f.get("cartera") or "(vacía)").strip().upper() or "(vacía)"
        cuenta[c] = cuenta.get(c, 0) + 1
        if not (f.get("ticker") or "").strip():
            sin_ticker[c] = sin_ticker.get(c, 0) + 1
    return sorted(((c, n, sin_ticker.get(c, 0)) for c, n in cuenta.items()),
                  key=lambda x: -x[1])


# ── 2. LO QUE 1816 YA TIENE (y por qué no llegó) ───────────────────────────
def contra_1816(tickers: list[str]) -> dict[str, str]:
    """`{ticker_upper: emisor}` de los que 1816 SÍ tiene con emisor cargado.

    Es exactamente el join que hace `jobs/ficha_1816.leer()`, sobre los tickers
    que faltan. Si un ticker sale acá con emisor, ese emisor **tendría que estar
    escrito** en `portafolio.assets`: no es trabajo para un modelo, es una
    corrida que no pasó o un ticker que no matchea.
    """
    tk = sorted({t.strip().upper() for t in tickers if (t or "").strip()})
    if not tk:
        return {}
    filas = _filas(
        "SELECT upper(i.ticker), i.emisor "
        "  FROM research.mkt_1816_instrumentos i "
        " WHERE upper(i.ticker) = ANY(%s) "
        "   AND i.emisor IS NOT NULL AND btrim(i.emisor) <> ''", (tk,))
    return {t: e for t, e in filas}


def frescura_1816() -> tuple:
    """Cuántos instrumentos tiene el catálogo y de cuándo es. Un catálogo viejo
    explica un faltante mejor que cualquier hipótesis sobre el dato."""
    f = _filas("SELECT count(*), max(actualizado_en) "
               "  FROM research.mkt_1816_instrumentos")
    return f[0] if f else (0, None)


def ultima_corrida_ficha_1816() -> tuple:
    """Cuándo corrió por última vez el job que escribe el emisor, y cómo salió.
    Sin esto, «el dato está en 1816 y no en assets» no se puede explicar."""
    f = _filas("SELECT finished_at, status, data->'stats' "
               "  FROM manager.job_runs WHERE tipo = 'ficha_1816' "
               " ORDER BY finished_at DESC NULLS LAST LIMIT 1")
    return f[0] if f else (None, None, None)


# ── 3. LOS QUE SON CEDEAR: el subyacente ───────────────────────────────────
def subyacentes(tickers: list[str]) -> dict[str, str]:
    """`{ticker_upper: underlying}` desde `mercado.cedears`.

    Se busca por `ticker_corto` Y por `ticker` porque el catálogo guarda las dos
    grafías y el asset puede traer cualquiera. **No se recorta ningún sufijo**:
    adivinar el ticker base por el string es la REGLA #9(A).
    """
    tk = sorted({t.strip().upper() for t in tickers if (t or "").strip()})
    if not tk:
        return {}
    filas = _filas(
        "SELECT upper(coalesce(c.ticker_corto, c.ticker)), c.underlying "
        "  FROM mercado.cedears c "
        " WHERE (upper(c.ticker_corto) = ANY(%s) OR upper(c.ticker) = ANY(%s)) "
        "   AND c.underlying IS NOT NULL AND btrim(c.underlying) <> ''",
        (tk, tk))
    return {t: u for t, u in filas}


# ── 4. LA LISTA CERRADA: los emisores que YA existen ───────────────────────
def emisores_por_cartera() -> list[tuple]:
    """Cada emisor cargado, con en qué carteras aparece y cuántos assets tiene.

    ⚠️ **Es la respuesta a «¿qué ES el emisor en RENTA VARIABLE?»**, y sale del
    dato y no de una opinión: si los que ya están cargados dicen el nombre de la
    empresa, esa es la convención de la casa y el modelo tiene que copiarla.

    Y es la lista de la que el modelo va a poder ELEGIR en vez de inventar —
    la misma idea que `agente/arreglos.CompletarFicha` ya usa para el desplegable
    (`valores_usados`): tipear es como nacen `HD ` y `hd`.
    """
    return _filas("""
        SELECT btrim(a.emisor) AS emisor,
               count(*)::int AS assets,
               string_agg(DISTINCT upper(btrim(coalesce(a.cartera, '—'))), ', '
                          ORDER BY upper(btrim(coalesce(a.cartera, '—')))) AS carteras
          FROM portafolio.assets a
         WHERE a.emisor IS NOT NULL AND btrim(a.emisor) <> ''
           AND upper(btrim(a.emisor)) <> 'NO APLICA'
         GROUP BY btrim(a.emisor)
         ORDER BY 2 DESC, 1
    """)


# ── 5. EL CASO FCI: el emisor está DENTRO del nombre ───────────────────────
def match_por_nombre(filas: list[dict], emisores: list[str]) -> list[tuple]:
    """Cuántos faltantes contienen, en su `unidad`, un emisor que YA existe.

    Es el caso `Allaria Dólar Dinámico` → ALLARIA y `IVW - CEDEAR ISHARES…` →
    ISHARES. **Se mide acá, en Python y sin modelo**, para saber cuánto del
    trabajo se lleva un `ILIKE` antes de pagarle a nadie: lo que un substring
    resuelve no necesita un LLM, y lo que no, lo necesita de verdad.

    ⚠️ Es una MEDICIÓN, no la regla final. Un substring empareja por string, que
    es exactamente lo que la REGLA #9 desaconseja para decidir identidad — por
    eso el resultado se muestra con el candidato al lado, para poder mirar si
    acertó. Acá informa el diseño; no escribe nada.
    """
    # Los más largos primero: «Allaria Fondos» tiene que ganarle a «Allaria».
    orden = sorted({e.strip() for e in emisores if len(e.strip()) >= 4},
                   key=len, reverse=True)
    out = []
    for f in filas:
        u = (f.get("unidad") or "").upper()
        hit = next((e for e in orden if e.upper() in u), "")
        if hit:
            out.append((f.get("cartera") or "—", f.get("unidad") or "", hit))
    return out


# ── 6. LO QUE DEVUELVE FINNHUB DE VERDAD ───────────────────────────────────
def probar_finnhub(pares: list[tuple[str, str]], tope: int) -> list[tuple]:
    """Para una muestra de `(ticker, underlying)`, qué contesta Finnhub.

    ⚠️ **Es la única parte que sale a la red, y por eso está apagada por
    default.** El cliente ya throttlea a 40 req/min; acá se le pone además un
    tope de llamadas, declarado por el que corre el diag.

    Lo que se quiere ver con datos y no suponer: si `company_profile` devuelve
    `name` para una ACCIÓN (QCOM → Qualcomm) y qué devuelve para un **ETF**
    (XLK), que es el caso donde el «emisor» no es una empresa operativa. Si
    para un ETF viene vacío, esa rama necesita otra fuente y hay que saberlo
    antes de prometer que el valor viene resuelto.
    """
    from core.finnhub import FinnhubError, company_profile

    out = []
    for tk, und in pares[:tope]:
        try:
            p = company_profile(und) or {}
            out.append((tk, und, p.get("name") or "(sin name)",
                        p.get("finnhubIndustry") or "—", p.get("country") or "—"))
        except FinnhubError as e:
            out.append((tk, und, f"ERROR: {str(e)[:40]}", "—", "—"))
        except Exception as e:
            out.append((tk, und, f"{type(e).__name__}: {str(e)[:30]}", "—", "—"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="De dónde puede salir cada emisor que falta (read-only)")
    ap.add_argument("--detalle", action="store_true",
                    help="lista fila por fila, no solo los totales")
    ap.add_argument("--finnhub", type=int, default=0, metavar="N",
                    help="probar N subyacentes contra Finnhub (sale a la red)")
    ap.add_argument("--emisores", type=int, default=25, metavar="N",
                    help="cuántos emisores existentes listar (default 25)")
    a = ap.parse_args()

    d = _definicion()
    print(f"\nDIAG EMISOR — regla «{d['regla']}» de la habilidad "
          f"`ficha_incompleta`\nQué rompe cuando falta: {d['rompe']}")

    # ── 1 ──────────────────────────────────────────────────────────────────
    _titulo("1. EL UNIVERSO — a quién le falta el emisor, por CARTERA")
    filas = universo()
    if not filas:
        print("  No falta ninguno. La regla `sin_emisor` está en cero.")
        return 0
    print(f"  {len(filas)} título(s) en carteras de clientes sin emisor.\n")
    _tabla(por_cartera(filas), ("CARTERA", "SIN EMISOR", "…Y SIN TICKER"),
           (28, 12, 14))
    print("\n  «SIN TICKER» es la columna que decide: un asset sin ticker no "
          "matchea\n  con 1816 ni con el catálogo de CEDEARs. Ahí no hay nada "
          "que buscar —\n  falta el dato con el que se busca.")

    # ── 2 ──────────────────────────────────────────────────────────────────
    _titulo("2. LO QUE 1816 YA TIENE — y que `jobs/ficha_1816` debería haber escrito")
    n_cat, cuando = frescura_1816()
    fin, status, stats = ultima_corrida_ficha_1816()
    print(f"  catálogo `research.mkt_1816_instrumentos`: {n_cat} instrumentos, "
          f"actualizado {cuando or 'NUNCA'}")
    print(f"  última corrida de `ficha_1816`: {fin or 'NUNCA'} · estado "
          f"{status or '—'} · stats {dict(stats or {}) or '—'}\n")
    tickers = [f.get("ticker") or "" for f in filas]
    tiene = contra_1816(tickers)
    con_1816 = [f for f in filas
                if (f.get("ticker") or "").strip().upper() in tiene]
    print(f"  ⚠ {len(con_1816)} de {len(filas)} tienen emisor EN 1816 y vacío "
          f"en `portafolio.assets`.")
    if con_1816:
        print("     Eso NO es trabajo para un modelo: el dato existe y no "
              "llegó. Mirá\n     la corrida de arriba antes que cualquier otra "
              "cosa.\n")
        _tabla([(f["cartera"], f["ticker"],
                 tiene[(f["ticker"] or "").strip().upper()])
                for f in (con_1816 if a.detalle else con_1816[:15])],
               ("CARTERA", "TICKER", "EMISOR SEGÚN 1816"), (18, 14, 40))
        if not a.detalle and len(con_1816) > 15:
            print(f"     … y {len(con_1816) - 15} más (--detalle)")

    # ── 3 ──────────────────────────────────────────────────────────────────
    _titulo("3. LOS QUE SON CEDEAR — ¿tienen subyacente para preguntarle a Finnhub?")
    subs = subyacentes(tickers)
    # La foto de Primary: si el ticker está ahí, ES un instrumento que cotiza y
    # `alta_cedear` puede darlo de alta con su subyacente.
    try:
        from agente import fuentes
        prim = fuentes.tickers_en_primary() or set()
    except Exception as e:                       # no bloquea el resto del diag
        print(f"  (no pude leer la foto de Primary: {e})")
        prim = set()
    rv = [f for f in filas
          if (f.get("cartera") or "").strip().upper() == "RENTA VARIABLE"]
    con_und = [(f["ticker"], subs[(f["ticker"] or "").strip().upper()])
               for f in rv if (f.get("ticker") or "").strip().upper() in subs]
    print(f"  {len(rv)} de RENTA VARIABLE · {len(con_und)} con `underlying` en "
          f"`mercado.cedears`\n  {len(rv) - len(con_und)} sin subyacente: son "
          f"acciones locales o no están en el master de CEDEARs,\n  y para esos "
          f"Finnhub no es la fuente.")
    if con_und:
        print()
        _tabla(con_und if a.detalle else con_und[:15],
               ("TICKER", "SUBYACENTE"), (14, 20))
        if not a.detalle and len(con_und) > 15:
            print(f"     … y {len(con_und) - 15} más (--detalle)")

    # ── 4 ──────────────────────────────────────────────────────────────────
    _titulo("4. LA LISTA CERRADA — los emisores que YA existen, y en qué carteras")
    ex = emisores_por_cartera()
    print(f"  {len(ex)} emisor(es) distintos cargados hoy.")
    print("  Mirá la columna CARTERAS: dice qué ES un emisor para cada tipo de\n"
          "  activo, y esa es la convención que el modelo tiene que copiar.\n")
    _tabla(ex[:a.emisores], ("EMISOR", "ASSETS", "CARTERAS"), (38, 7, 28))
    if len(ex) > a.emisores:
        print(f"     … y {len(ex) - a.emisores} más (--emisores N)")

    # ── 5 ──────────────────────────────────────────────────────────────────
    _titulo("5. EL CASO FCI — cuántos traen un emisor EXISTENTE en su propio nombre")
    m = match_por_nombre(filas, [e[0] for e in ex])
    print(f"  {len(m)} de {len(filas)} contienen un emisor ya cargado dentro de "
          f"la `unidad`.\n  Eso es lo que un `ILIKE` resuelve sin pagarle a "
          f"nadie — el resto es lo que\n  de verdad hay que entender.\n")
    if m:
        _tabla(m if a.detalle else m[:15],
               ("CARTERA", "UNIDAD", "EMISOR QUE APARECE"), (16, 40, 20))
        if not a.detalle and len(m) > 15:
            print(f"     … y {len(m) - 15} más (--detalle)")

    # ── 6 ──────────────────────────────────────────────────────────────────
    if a.finnhub:
        _titulo(f"6. FINNHUB — qué devuelve DE VERDAD (muestra de {a.finnhub})")
        print("  Sale a la red. Lo que importa mirar: si un ETF (XLK, URA, SMH)\n"
              "  trae `name` igual que una acción, o si viene vacío — porque de\n"
              "  eso depende que esa rama pueda prometer un valor resuelto.\n")
        r = probar_finnhub(con_und, a.finnhub)
        _tabla(r, ("TICKER", "SUBY.", "NAME DE FINNHUB", "INDUSTRIA", "PAÍS"),
               (10, 8, 34, 20, 6))
    else:
        _titulo("6. FINNHUB — no se probó")
        print("  Corré con `--finnhub 12` para ver qué contesta de verdad con "
              "una acción\n  y con un ETF. Es la única parte que sale a la red.")

    # ── 7 ──────────────────────────────────────────────────────────────────
    _titulo("7. LO QUE YA SE ESCRIBIÓ — para revisar UNO POR UNO")
    print("  ⚠️ El modelo elige de una lista CERRADA. Si el emisor correcto no\n"
          "  está en esa lista, no puede proponerlo — y el prompt le pide elegir\n"
          "  igual. Ahí es donde contesta el más PARECIDO en vez de nada.\n"
          "  Sospechosos: un CEDEAR cuyo emisor es una empresa extranjera que el\n"
          "  catálogo todavía no tiene.\n")
    esc = _filas("""
        SELECT a.at, a.sujeto, a.despues, a.por
          FROM agente.acciones a
         WHERE a.arreglo = 'completar_ficha' AND a.campo = 'emisor' AND a.ok
         ORDER BY a.at DESC LIMIT 60
    """)
    if not esc:
        print("  todavía no se escribió ningún emisor desde el agente.")
    else:
        _tabla([(str(x[0])[:16], x[1][:34], x[2], (x[3] or "")[:18]) for x in esc],
               ("CUÁNDO", "UNIDAD", "EMISOR ESCRITO", "QUIÉN"), (17, 36, 32, 18))

    # ── 8 ──────────────────────────────────────────────────────────────────
    _titulo("8. LOS QUE QUEDARON SIN PROPUESTA — por qué, uno por uno")
    sin = [f for f in filas if not subs.get((f.get("ticker") or "").strip().upper())
           and (f.get("cartera") or "").strip().upper() == "RENTA VARIABLE"]
    print(f"  {len(sin)} de RENTA VARIABLE sin `underlying` en `mercado.cedears`.\n"
          "  Sin subyacente no se le puede preguntar a Finnhub, así que caen al\n"
          "  modelo — y el modelo sólo puede elegir de la lista.\n"
          "  La columna que decide es la última: si el emisor correcto NO está en\n"
          "  la lista, el problema no es el modelo, es el alcance que le dimos.\n")
    existentes = {e[0].upper() for e in ex}
    _tabla([(f["ticker"], "no" if f["ticker"].upper() not in prim else "sí",
             "sí" if f["ticker"].upper() in existentes else "NO")
            for f in sin],
           ("TICKER", "¿EN LA FOTO DE PRIMARY?", "¿SU TICKER ES UN EMISOR YA?"),
           (12, 24, 28))
    print("\n  ⚠️ Estos son CEDEARs que `mercado.cedears` no tiene. El sistema ya\n"
          "  sabe resolverlo: la habilidad `cedear_faltante` los detecta y el\n"
          "  arreglo `alta_cedear` los da de alta CON su subyacente. Con eso,\n"
          "  Finnhub contesta y el modelo deja de tener que adivinar.")

    # ── 9 ──────────────────────────────────────────────────────────────────
    _titulo("9. ¿ESE CAMINO EXISTE DE VERDAD? — contra `alta_cedear.candidatos`")
    print("  El bloque 8 termina diciendo «los da de alta `alta_cedear`». Eso hay\n"
          "  que VERIFICARLO, no afirmarlo (REGLA #2): el arreglo sólo ofrece lo\n"
          "  que Primary marca con la FICHA de un CEDEAR (`cficode` calibrado con\n"
          "  los que ya tenemos), y estar en la foto NO alcanza.\n")
    from agente import alta_cedear
    li = alta_cedear.listado()
    if not li.get("ok"):
        print(f"  ⚠️ no se pudo mirar: {li.get('error')}")
    else:
        ofrecidos = {f["unidad"].upper() for f in li["filas"]}
        print(f"  `alta_cedear` ofrece {len(li['filas'])} altas · ficha "
              f"cficode {li['cficodes']} plazo {li['plazos']} moneda {li['monedas']}\n"
              f"  (calibrada con {li['reconocidos']} de nuestros {li['propios']})\n")
        _tabla([(f["ticker"], "SÍ" if f["ticker"].upper() in ofrecidos else "no")
                for f in sin],
               ("TICKER", "¿LO OFRECE `alta_cedear`?"), (12, 26))
        faltan = [f["ticker"] for f in sin if f["ticker"].upper() not in ofrecidos]
        if faltan:
            print(f"\n  ⚠️ {len(faltan)} NO los ofrece: {', '.join(faltan)}\n"
                  "  Para ésos el alta de CEDEAR no es el camino — o no son CEDEARs,\n"
                  "  o Primary les pone otra ficha. Se cargan a mano en Manager →\n"
                  "  TÍTULOS · ASSETS, y ahí el emisor se tipea una vez y listo.")


    _titulo("QUÉ HACER CON ESTO")
    print("  · Lo del bloque 2 es PLOMERÍA: el dato existe y no llegó.")
    print("  · FINANCIAMIENTO es una REGLA de dos líneas en `assets_autofill`.")
    print("  · Lo del bloque 5 lo resuelve un substring, sin modelo.")
    print("  · Lo que queda después de esos tres es lo ÚNICO que justifica un LLM.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
