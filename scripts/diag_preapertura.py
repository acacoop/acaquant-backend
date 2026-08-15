"""scripts/diag_preapertura.py — ¿queda todo sano para cuando abra el mercado? READ-ONLY.

El 2026-08-15 se tocaron, en un día, las cuatro cosas que deciden de dónde sale un
precio: se renombraron las columnas del master, se sembró `mercado.especies`, se
derivaron los símbolos de `portafolio.assets`, y se metió un filtro de validación
en el ÚNICO punto por el que pasan todas las suscripciones.

Cada cambio se verificó por separado. Lo que este script contesta es la pregunta
que ninguno contesta solo: **cuando el motor arranque, ¿a qué se va a suscribir, y
va a poder?**

La respuesta importa porque el modo de fallar es SILENCIOSO. Un símbolo que el
filtro descarta no tira error: el papel simplemente se queda sin `last_price`, la
vista abre igual y el número queda viejo. Si algo se rompió, hay que verlo ANTES
de la rueda y no cuando alguien pregunte por qué un bono no se mueve.

Los cinco bloques, en el orden en que las cosas se romperían:

  1) el CATÁLOGO de Primary — es el insumo del filtro. Vacío o corto = no filtra.
  2) el UNIVERSO del motor de portfolio — los símbolos de la tenencia de hoy, y
     cuántos sobreviven al filtro. Acá se ve el daño real.
  3) el MASTER de curvas — cuántos bonos tienen símbolo y si existe.
  4) las ESPECIES — que la tabla no haya quedado vacía o a medias.
  5) los PRECIOS de la última rueda — el contrafáctico: qué tenía precio ayer y
     hoy no lo tendría.

READ-ONLY. No escribe, no suscribe, no toca los motores.

Uso:
    python -m scripts.diag_preapertura
"""
from __future__ import annotations

from core.instrumentos_validos import validos
from core.postgres import get_pool

_SEP = "=" * 92


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _sep(t: str) -> None:
    print(f"\n{_SEP}\n{t}\n{_SEP}")


def _vigencia_por_simbolo(simbolos: list[str]) -> dict[str, str]:
    """`símbolo → 'sí' / 'no (motivo)'`. Separa el ruido del problema real.

    Un símbolo que no existe en Primary NO es un error si el título venció: es
    exactamente lo que tiene que pasar. Sin este cruce, el reporte mezcla los
    papeles muertos —que son la mayoría y no hay nada que hacerles— con los
    vivos, que son los únicos que importan.
    """
    if not simbolos:
        return {}
    filas = _q("""
        SELECT instrumento, bool_or(vigente IS NOT false) AS alguna_vigente,
               min(vigencia_motivo) AS motivo
        FROM portafolio.assets
        WHERE instrumento = ANY(%s)
        GROUP BY instrumento
    """, (simbolos,))
    return {f["instrumento"]: ("sí" if f["alguna_vigente"]
                               else f"no ({f['motivo'] or 'sin motivo'})")
            for f in filas}


def main() -> None:
    print(_SEP)
    print("PRE-APERTURA — ¿a qué se van a suscribir los motores?")
    print(_SEP)

    # ── 1) El catálogo, que es el insumo del filtro ──────────────────────────
    _sep("1) CATÁLOGO DE PRIMARY (lo que usa el filtro del WS)")
    univ = validos(forzar=True)
    if univ is None:
        print("  ⚠ `validos()` devolvió None → el filtro NO va a descartar nada.")
        print("    Es la degradación elegida a propósito: sin catálogo confiable se")
        print("    prefiere suscribir de más antes que dejar la mesa sin precios.")
        print("    Los motores arrancan como siempre. Para tener el filtro activo:")
        print("    python -m scripts.discovery_pyrofex")
        univ = set()
    else:
        print(f"  ✅ {len(univ)} símbolos — el filtro está ACTIVO")

    # ── 2) El universo REAL del motor de portfolio ───────────────────────────
    _sep("2) MOTOR DE PORTFOLIO — los símbolos de la tenencia de hoy")
    print("  Es de acá que sale el `last_price` de TODA la tenencia. Un símbolo")
    print("  descartado = ese papel sin precio en PORTFOLIOS y en el PnL.")
    filas = _q("""
        WITH ult AS (SELECT max(fecha) AS f FROM portafolio.tenencia WHERE aum = 'si')
        SELECT a.instrumento, count(DISTINCT t.unidad) AS unidades,
               count(*) AS filas_tenencia
        FROM portafolio.tenencia t
        JOIN ult ON t.fecha = ult.f
        JOIN portafolio.assets a ON a.unidad = t.unidad
        WHERE t.aum = 'si' AND COALESCE(t.cantidad, 0) <> 0
          AND a.instrumento IS NOT NULL AND trim(a.instrumento) <> ''
          AND a.instrumento <> 'NO APLICA'
        GROUP BY a.instrumento ORDER BY count(*) DESC
    """)
    fuera = [f for f in filas if univ and f["instrumento"] not in univ]
    print(f"\n  símbolos a suscribir: {len(filas)}")
    print(f"  que el filtro DESCARTA: {len(fuera)}")
    if fuera:
        print("\n  El filtro está haciendo su trabajo: NO se los pide a Primary. Lo que")
        print("  sigue mal es el DATO — el símbolo malo está guardado en assets y nadie")
        print("  lo limpia. Y la mitad de las veces ni siquiera es un error: si el")
        print("  título VENCIÓ, es normal que Primary no lo liste.")
        print(f"\n  {'SÍMBOLO':<34}{'UNIDADES':>9}{'FILAS':>7}  ¿VIGENTE?")
        print("  " + "-" * 68)
        vig = _vigencia_por_simbolo([f["instrumento"] for f in fuera])
        vencidos = 0
        for f in fuera[:30]:
            v = vig.get(f["instrumento"], "?")
            vencidos += v.startswith("no")
            print(f"  {str(f['instrumento'])[:33]:<34}{f['unidades']:>9}"
                  f"{f['filas_tenencia']:>7}  {v}")
        if len(fuera) > 30:
            print(f"  … y {len(fuera) - 30} más")
        print(f"\n  de los mostrados: {vencidos} son títulos DADOS DE BAJA (esperado) · "
              f"{min(len(fuera), 30) - vencidos} siguen vigentes (esos SÍ hay que mirar)")
    else:
        print("  ✅ todos los símbolos de la tenencia existen en Primary")

    sin_simbolo = _q("""
        WITH ult AS (SELECT max(fecha) AS f FROM portafolio.tenencia WHERE aum = 'si')
        SELECT count(DISTINCT t.unidad) AS n
        FROM portafolio.tenencia t
        JOIN ult ON t.fecha = ult.f
        LEFT JOIN portafolio.assets a ON a.unidad = t.unidad
        WHERE t.aum = 'si' AND COALESCE(t.cantidad, 0) <> 0
          AND (a.instrumento IS NULL OR trim(a.instrumento) = ''
               OR a.instrumento = 'NO APLICA')
    """)[0]["n"]
    print(f"\n  unidades con tenencia y SIN símbolo cargado: {sin_simbolo}")
    print("  (no es una regresión: cash, FCI y pagarés no cotizan en Primary)")

    # ── 3) El master de curvas ───────────────────────────────────────────────
    _sep("3) MASTER DE CURVAS — el símbolo con el que se dibuja cada bono")
    m = _q("""
        SELECT count(*) AS bonos,
               count(instrumento) FILTER (WHERE trim(COALESCE(instrumento,'')) <> '')
                   AS con_simbolo
        FROM mercado.curvas
    """)[0]
    print(f"  bonos: {m['bonos']} · con símbolo: {m['con_simbolo']} · "
          f"SIN símbolo: {m['bonos'] - m['con_simbolo']}")
    if univ:
        simbolos = _q("SELECT DISTINCT instrumento FROM mercado.curvas "
                      "WHERE instrumento IS NOT NULL AND trim(instrumento) <> ''")
        malos = [s["instrumento"] for s in simbolos if s["instrumento"] not in univ]
        print(f"  con símbolo que NO existe en Primary: {len(malos)}")
        for s in malos[:15]:
            print(f"      ✗ {s}")

    # ── 4) Especies ──────────────────────────────────────────────────────────
    _sep("4) ESPECIES — el catálogo de patas")
    e = _q("""
        SELECT count(*) AS filas, count(DISTINCT ticker) AS tickers,
               count(*) FILTER (WHERE validado IS true)  AS validadas,
               count(*) FILTER (WHERE validado IS false) AS invalidas,
               count(*) FILTER (WHERE validado IS NULL)  AS sin_marcar,
               count(*) FILTER (WHERE es_default)        AS defaults
        FROM mercado.especies
    """)[0]
    print(f"  filas: {e['filas']} · tickers: {e['tickers']} · default marcados: {e['defaults']}")
    print(f"  validadas: {e['validadas']} · inválidas: {e['invalidas']} · "
          f"sin marcar: {e['sin_marcar']}")
    if e["invalidas"]:
        print("  ⚠ quedan inválidas: `jobs.validar_instrumentos` las borra al correr")

    # ── 5) El contrafáctico ──────────────────────────────────────────────────
    _sep("5) LO QUE TENÍA PRECIO AYER Y HOY NO LO TENDRÍA")
    print("  La prueba más directa de que no se rompió nada: si un símbolo tuvo")
    print("  precio en la última rueda y ahora el filtro lo descartaría, ESO es una")
    print("  regresión de hoy. Si la lista está vacía, la rueda abre como ayer.")
    if not univ:
        print("\n  (sin catálogo no se filtra nada → no puede haber regresión)")
    else:
        snap = _q("SELECT ticker FROM valuaciones.portfolio_snapshot "
                  "WHERE last_price IS NOT NULL")
        perdidos = [s["ticker"] for s in snap if s["ticker"] not in univ]
        print(f"\n  símbolos con precio en el último snapshot: {len(snap)}")
        print(f"  que el filtro descartaría: {len(perdidos)}")
        print("\n  OJO con leer esto como regresión: `portfolio_snapshot` NO se limpia")
        print("  cuando un papel vence — el último precio queda pegado para siempre.")
        print("  Así que un título que amortizó en mayo sigue teniendo precio ahí y")
        print("  Primary ya no lo lista. Lo que importa es la columna de vigencia.")
        vig = _vigencia_por_simbolo(perdidos)
        vencidos = [s for s in perdidos if vig.get(s, "?").startswith("no")]
        vivos = [s for s in perdidos if s not in vencidos]
        print(f"\n  · dados de baja (esperado, nada que hacer): {len(vencidos)}")
        print(f"  · VIGENTES sin símbolo válido → hay que mirarlos: {len(vivos)}")
        for s in vivos[:30]:
            print(f"      ⚠ {s}  ({vig.get(s, 'no está en assets')})")
        if not vivos:
            print("  ✅ ninguno vigente se queda sin precio. La rueda abre como ayer.")

    print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")


if __name__ == "__main__":
    main()
