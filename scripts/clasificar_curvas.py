"""scripts/clasificar_curvas.py — PASO 2 del rediseño de renta fija: pone los EJES.

Doc madre: `docs/RENTA_FIJA.md` §0. Deriva `emisor_tipo` / `moneda` / `ajuste` /
`ley` / `instrumento` para los instrumentos de `mercado.curvas`, cruzándolos
contra el catálogo de 1816 (que ya los tiene bien agrupados) y traduciendo con la
tabla de `core/curvas_ejes.py`.

**Es invisible para la vista**: escribe columnas NUEVAS al lado de `curva`, que
sigue mandando en la pantalla hasta el paso 3. Nada las lee todavía.

**Lo más importante que hace no es escribir: es el TEST DE EQUIVALENCIA.** Antes
de tocar el front hay que saber si las pills nuevas muestran EXACTAMENTE lo mismo
que hoy. El script compara bono por bono y parte el resultado en tres:

  · **IGUAL**   — mismo bono, misma pill. Es lo que tiene que ser casi todo.
  · **CAMBIA**  — hoy está en una pill y pasaría a otra. **Cada uno hay que poder
                  explicarlo**; si alguno no se explica, NO se toca el front.
  · **ENTRA**   — hoy no está en ninguna pill y pasaría a estar. Son las ONs, que
                  el user decidió absorber al eliminar la vista de ONs. Esperado.

Los CAMBIA esperados (medidos el 2026-08-15) son los duales: 5 viven hoy en la
curva `cer` y 3 en `tamar`, y con el eje `ajuste=dual` se van a la pill DUALES.
Eso es justamente lo que se quería arreglar.

⚠ CRÉDITOS: el censo de 1816 son ~29 (o 0 con `--desde`, reusando el que guarda
`diag_1816_cashflow --json`). No se piden precios ni series.

Uso:
    python -m scripts.clasificar_curvas                      # DRY-RUN (default)
    python -m scripts.clasificar_curvas --desde /tmp/1816.json
    python -m scripts.clasificar_curvas --aplicar            # escribe los ejes
    python -m scripts.clasificar_curvas --equivalencia       # solo el test
"""
from __future__ import annotations

import argparse
import json

from core import curvas_ejes as ce
from core import mercado_1816
from scripts.diag_1816_cashflow import censar

_norm = mercado_1816.normalizar_ticker

# Columnas que escribe el `--aplicar`. Se crean con `python -m scripts.apply_schema`
# (están en sql/schema.sql). Todas nullable: "sin clasificar" es un estado válido.
# El eje bono/letra (`tipo_instrumento`) se ELIMINÓ el 2026-08-15: 1816 solo lo
# afirma en 3 de sus 28 curvas y quedó vacío en los 221 bonos.
_COLS = ("emisor_tipo", "moneda_eje", "ajuste", "ley")


def _master() -> list[dict]:
    from core import curvas_sql
    return curvas_sql.cargar_todos()


def _fijados() -> set[str]:
    """Tickers CER con el CER de liquidación ya publicado — se comportan como
    tasa fija. MISMA fuente que usa la vista hoy (`api/routers/titulos.py`), para
    que el test de equivalencia no mida contra una regla distinta."""
    try:
        from api.routers.titulos import _bonos_cer_fijados_set_corto
        return {_norm(t) for t in _bonos_cer_fijados_set_corto()}
    except Exception as e:
        print(f"  ⚠ no se pudo leer los CER fijados ({str(e)[:70]}) — el test de "
              "equivalencia va a marcar como CAMBIA a los CER ya fijados.")
        return set()


def _pill_actual(curva: str | None, fijado: bool) -> str | None:
    """La pill que la vista muestra HOY. El front filtra por `curva_efectiva`
    (= 'tasa_fija' si el CER está fijado, sino la curva) contra 4 botones
    hardcodeados; `soberanos` se muestra con la etiqueta HARD DOLAR."""
    c = "tasa_fija" if fijado else (curva or "")
    return {"tasa_fija": "tasa_fija", "cer": "cer", "soberanos": "hard_dolar",
            "dolar_linked": "dolar_linked"}.get(c)


def _clasificar(master: list[dict], univ: dict) -> list[dict]:
    """Un dict por instrumento con sus ejes derivados (o el motivo de que falten)."""
    fijados = _fijados()
    out = []
    for d in master:
        tk = _norm(d.get("ticker_corto"))
        inst = univ.get(tk) or {}
        curva_1816 = inst.get("_curva")
        ejes = ce.desde_1816(curva_1816)
        fijado = tk in fijados
        out.append({
            "ticker": d.get("ticker_corto"), "tk": tk, "curva": d.get("curva"),
            "tipo": d.get("tipo"),
            "curva_1816": curva_1816, "ejes": ejes, "cer_fijado": fijado,
            "pill_nueva": ce.pill(ejes, fijado),
            "pill_actual": _pill_actual(d.get("curva"), fijado),
            "motivo": ("" if ejes else
                       ("sin match en 1816" if not curva_1816 else
                        f"curva 1816 desconocida: {curva_1816}")),
        })
    return out


def _reporte_ejes(filas: list[dict]) -> None:
    print(f"\n{'=' * 96}\nEJES DERIVADOS ({len(filas)} instrumentos de mercado.curvas)\n{'=' * 96}")
    con = [f for f in filas if f["ejes"]]
    sin = [f for f in filas if not f["ejes"]]
    print(f"  ✔ clasificados: {len(con)}   ✘ sin clasificar: {len(sin)}")

    # cómo queda repartido el universo por cada eje
    for eje in ("emisor_tipo", "moneda", "ajuste"):
        cuenta: dict[str, int] = {}
        for f in con:
            v = getattr(f["ejes"], eje)
            cuenta[v] = cuenta.get(v, 0) + 1
        print(f"    {eje:<12} " + " · ".join(f"{k}={v}" for k, v in
                                             sorted(cuenta.items(), key=lambda x: -x[1])))

    # el mapa mi_curva → ejes: acá se ve el desorden que el rediseño arregla
    print(f"\n{'MI CURVA':<16}{'N':>4}   EJES A LOS QUE SE ABRE")
    print("─" * 96)
    porcurva: dict[str, dict[str, int]] = {}
    for f in con:
        e = f["ejes"]
        clave = f"{e.emisor_tipo}/{e.moneda}/{e.ajuste}"
        porcurva.setdefault(f["curva"] or "?", {}).setdefault(clave, 0)
        porcurva[f["curva"] or "?"][clave] += 1
    for curva, dest in sorted(porcurva.items(), key=lambda x: -sum(x[1].values())):
        d = sorted(dest.items(), key=lambda x: -x[1])
        txt = " · ".join(f"{k} ({v})" for k, v in d[:4])
        if len(d) > 4:
            txt += f" · +{len(d) - 4}"
        print(f"{curva[:16]:<16}{sum(dest.values()):>4}   {txt}")
    print("─" * 96)

    # `tipo` es un campo VIEJO de la tabla (Bono | Lecap | Boncap | ON …) que
    # mezcla la FORMA del título con el tipo de emisor. Se lo cruza contra el eje
    # de EMISOR para decidir con datos si sobra — afirmarlo sin medir fue justo el
    # error que esta línea viene a no repetir. (Antes se cruzaba contra el eje
    # bono/letra, que se eliminó por vacío.)
    cruce: dict[str, dict[str, int]] = {}
    for f in filas:
        t = (f.get("tipo") or "(vacío)").strip() or "(vacío)"
        i = (f["ejes"].emisor_tipo if f["ejes"] else None) or "(sin dato)"
        cruce.setdefault(t, {}).setdefault(i, 0)
        cruce[t][i] += 1
    print(f"\n{'CAMPO `tipo` (viejo)':<24}{'N':>4}   EJE `emisor_tipo`")
    print("─" * 96)
    for t, dest in sorted(cruce.items(), key=lambda x: -sum(x[1].values())):
        print(f"{t[:24]:<24}{sum(dest.values()):>4}   "
              + " · ".join(f"{k}={v}" for k, v in sorted(dest.items(), key=lambda x: -x[1])))
    print("─" * 96)
    print("  Si `tipo` distingue lo mismo que `emisor_tipo`, uno de los dos sobra.")

    if sin:
        print(f"\n⚠ SIN CLASIFICAR ({len(sin)}) — quedan a mano (paso aparte):")
        motivos: dict[str, list[str]] = {}
        for f in sin:
            motivos.setdefault(f["motivo"], []).append(f["ticker"] or "?")
        for m, tks in sorted(motivos.items()):
            print(f"    {m}: {', '.join(sorted(tks)[:25])}"
                  + (f" … (+{len(tks) - 25})" if len(tks) > 25 else ""))


def _test_equivalencia(filas: list[dict]) -> bool:
    """→ True si NINGÚN bono que hoy se ve cambia de pill. Ese es el semáforo
    para tocar el front."""
    print(f"\n{'=' * 96}\nTEST DE EQUIVALENCIA — ¿las pills nuevas muestran lo mismo?"
          f"\n{'=' * 96}")
    igual = [f for f in filas if f["pill_actual"] and f["pill_actual"] == f["pill_nueva"]]
    cambia = [f for f in filas if f["pill_actual"] and f["pill_actual"] != f["pill_nueva"]]
    entra = [f for f in filas if not f["pill_actual"] and f["pill_nueva"]]
    fuera = [f for f in filas if not f["pill_actual"] and not f["pill_nueva"]]

    print(f"  IGUAL  {len(igual):>4}  (misma pill que hoy)")
    print(f"  CAMBIA {len(cambia):>4}  ← lo único que puede romper la vista")
    print(f"  ENTRA  {len(entra):>4}  (hoy no se ven; son las ONs que se absorben)")
    print(f"  FUERA  {len(fuera):>4}  (no entran a ninguna pill ni antes ni ahora)")

    if cambia:
        print(f"\n{'TICKER':<10}{'HOY':<14}{'PASARÍA A':<14}{'CURVA 1816':<30}MOTIVO")
        print("─" * 96)
        for f in sorted(cambia, key=lambda x: (x["pill_actual"] or "", x["ticker"] or "")):
            nueva = f["pill_nueva"] or "(ninguna)"
            motivo = ("dual: hoy repartido en cer/tamar"
                      if (f["ejes"] and f["ejes"].ajuste == "dual") else
                      f["motivo"] or "revisar")
            print(f"{(f['ticker'] or '')[:10]:<10}{f['pill_actual']:<14}{nueva:<14}"
                  f"{(f['curva_1816'] or '—')[:30]:<30}{motivo}")
        print("─" * 96)

    inexplicados = [f for f in cambia
                    if not (f["ejes"] and f["ejes"].ajuste == "dual")]
    if not cambia:
        print("\n✅ VERDE: ningún bono cambia de pill. El front se puede tocar sin "
              "que la vista cambie.")
    elif not inexplicados:
        n = len(cambia)
        print(f"\n✅ VERDE CON NOTA: {'el que cambia es un dual yéndose' if n == 1 else f'los {n} que cambian son TODOS duales yéndose'} "
              "a su pill propia — que es justo lo que el rediseño venía a "
              "arreglar. Ningún cambio inesperado.")
    else:
        print(f"\n🛑 ROJO: {len(inexplicados)} bono(s) cambian de pill sin explicación "
              "(no son duales). NO tocar el front hasta entender cada uno:")
        for f in inexplicados[:20]:
            print(f"     {f['ticker']}: {f['pill_actual']} → "
                  f"{f['pill_nueva'] or '(ninguna)'} · curva mía '{f['curva']}' · "
                  f"1816 '{f['curva_1816'] or '—'}'")

    if entra:
        porpill: dict[str, int] = {}
        for f in entra:
            porpill[f["pill_nueva"]] = porpill.get(f["pill_nueva"], 0) + 1
        print("\n  Los que ENTRAN, por pill: "
              + " · ".join(f"{ce.DISPLAY[k]}={v}" for k, v in
                           sorted(porpill.items(), key=lambda x: -x[1])))
        print("  (esperado: es la absorción de la vista de ONs que pediste)")
    if fuera:
        print(f"\n  Los {len(fuera)} de FUERA son ajustes sin pill acordada "
              "(badlar/tpm/caución) o sin clasificar.")
    return not inexplicados


def _faltan_columnas() -> list[str]:
    """Las columnas de `_COLS` que NO existen en la base. Chequeo PREVIO al
    UPDATE: sin esto el `--aplicar` moría con un traceback de psycopg en medio
    de la escritura, que no le dice a nadie qué hacer. Pasó el 2026-08-15 —
    `CREATE TABLE IF NOT EXISTS` es un no-op sobre una tabla que ya existe, así
    que las columnas nuevas SOLO entran por `ALTER TABLE ADD COLUMN`."""
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'mercado' AND table_name = 'curvas'",
        )
        hay = {r[0] for r in cur.fetchall()}
    return [c for c in _COLS if c not in hay]


def _aplicar(filas: list[dict]) -> None:
    """Escribe los ejes. Idempotente: re-correr no cambia nada si nada cambió."""
    from core.postgres import get_pool
    faltan = _faltan_columnas()
    if faltan:
        print(f"\n🛑 NO se escribió nada: faltan columnas en mercado.curvas "
              f"({', '.join(faltan)}).")
        print("   Corré primero:  python -m scripts.apply_schema")
        print("   Si ya lo corriste y sigue faltando, el schema.sql del Droplet "
              "está viejo → `git pull` (el contador de statements te lo dice: "
              "si no cambió, el archivo tampoco).")
        return
    con = [f for f in filas if f["ejes"]]
    with get_pool().connection() as conn, conn.cursor() as cur:
        for f in con:
            e = f["ejes"]
            cur.execute(
                "UPDATE mercado.curvas SET emisor_tipo=%s, moneda_eje=%s, ajuste=%s, "
                "ley=%s WHERE ticker=%s",
                (e.emisor_tipo, e.moneda, e.ajuste, e.ley, f["ticker"]),
            )
    print(f"\n✅ ejes escritos en {len(con)} instrumentos de mercado.curvas.")
    print("   La vista NO cambia: sigue leyendo `curva`. Los ejes quedan al lado, "
          "listos para el paso 3.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Paso 2: ejes de mercado.curvas")
    ap.add_argument("--aplicar", action="store_true", help="escribe (default: dry-run)")
    ap.add_argument("--desde", help="censo guardado por diag_1816_cashflow --json")
    ap.add_argument("--equivalencia", action="store_true", help="solo el test")
    args = ap.parse_args()

    if not mercado_1816.disponible():
        print("✗ falta MERCADO_1816_API_KEY en el .env")
        return

    print("=" * 96)
    print("PASO 2 — EJES DE mercado.curvas (emisor · moneda · ajuste)")
    print("=" * 96)

    if args.desde:
        with open(args.desde, encoding="utf-8") as fh:
            censo = json.load(fh)
        print(f"Censo 1816 leído de {args.desde} (0 créditos).")
    else:
        censo = censar()
    univ = censo["instrumentos"]

    master = _master()
    if not master:
        print("✗ mercado.curvas vino vacío")
        return

    desconocidas = ce.desconocidas(i.get("_curva") for i in univ.values())
    if desconocidas:
        print(f"\n⚠ curvas de 1816 que NO están en la tabla de ejes ({len(desconocidas)}): "
              + ", ".join(desconocidas))
        print("  → sus instrumentos quedan SIN clasificar. Sumarlas a mano en "
              "core/curvas_ejes.py::EJES_1816 (no se adivinan a propósito).")

    filas = _clasificar(master, univ)
    if not args.equivalencia:
        _reporte_ejes(filas)
    verde = _test_equivalencia(filas)

    if args.aplicar:
        _aplicar(filas)
    else:
        print("\n(DRY-RUN — no se escribió nada. Con --aplicar se guardan los ejes.)")
    if not verde:
        print("\n⚠ El test de equivalencia dio ROJO: los ejes se pueden escribir "
              "igual (son invisibles), pero el paso 3 queda BLOQUEADO hasta "
              "resolverlo.")


if __name__ == "__main__":
    main()
