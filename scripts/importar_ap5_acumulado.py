"""scripts/importar_ap5_acumulado.py — cargar los ACUMULADOS desde un archivo.

El problema: el archivo trae **la DENOMINACIÓN**, no el número de cuenta. Y
`ap5.acumulado` se indexa por `account`.

⚠️ **PAREAR POR NOMBRE ES EXACTAMENTE LO QUE LA REGLA #9(A) PROHÍBE HACER A LA
LIGERA.** El nombre es una convención de quien lo escribió: la misma cooperativa
es `COOP. AGRÍCOLA X LTDA`, `Coop Agricola X`, `COOPERATIVA AGRICOLA X LIMITADA`.
Un pareo flojo asigna la plata de una cuenta a otra — y **no falla nada**: la
fila existe, el número es plausible, y el error aparece meses después en un
ranking que nadie puede explicar.

Por eso este importador tiene cuatro guardas, y ninguna es opcional:

1. **DRY por defecto.** Escribe sólo con `--aplicar`. Primero se mira.
2. **Match en NIVELES, del más estricto al más flojo**, y cada fila queda
   marcada con el nivel que la resolvió. Un match `exacto` y uno `sin sufijos`
   no son la misma evidencia y no se pueden leer igual.
3. **Ambiguo NUNCA se escribe.** Si un nombre matchea dos cuentas, se reporta
   con las dos candidatas y esa fila no entra. Elegir una es adivinar.
4. **Lo que no matchea se EXPORTA** a un CSV con las mejores sugerencias, para
   completar el `account` a mano y re-importar por número — que es el camino
   sin ambigüedad posible.

Se parea contra las DOS columnas de nombre de `ap5.cuentas`: `denominacion` (la
que publica la cámara) y `name` (el override que carga la mesa). Son columnas
distintas a propósito y el archivo puede venir con cualquiera de las dos.

⚠️ **`fecha` es EXCLUSIVA** (mismo contrato que la carga a mano): los importes ya
contienen todo hasta ese día inclusive y el sistema suma los días POSTERIORES.
Ponerle el primer día de la serie hace que ese día deje de contar y el acumulado
baja sin que nada falle.

Uso:
    # 1) mirar qué parearía (no escribe)
    python -m scripts.importar_ap5_acumulado archivo.xlsx --fecha 2026-08-22

    # 2) si el reporte convence, escribir
    python -m scripts.importar_ap5_acumulado archivo.xlsx --fecha 2026-08-22 --aplicar

    # 3) el CSV de los que no parearon queda en <archivo>.sin_parear.csv:
    #    completás la columna `account` y lo re-importás por número
    python -m scripts.importar_ap5_acumulado sin_parear.csv --fecha 2026-08-22 --aplicar

Columnas: se detectan solas por el encabezado. Si no las encuentra, lo dice y se
pasan a mano con --col-nombre / --col-pesos / --col-mtr / --col-account.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import re
import unicodedata
from pathlib import Path
from typing import Any

from core.postgres import get_pool

# Palabras que NO distinguen a una cooperativa de otra: son la forma jurídica.
# Se sacan SÓLO en el nivel 2 del pareo, nunca en el nivel 1 — porque sacarlas
# puede fusionar dos entidades distintas ("X SA" y "X SRL" existen).
SUFIJOS = {
    "SA", "SAU", "SRL", "SAS", "SACI", "SACIF", "SACIFA", "SAICA", "SAIC",
    "SCA", "LTDA", "LIMITADA", "SOCIEDAD", "ANONIMA", "COOP", "COOPERATIVA",
    "DE", "DEL", "LA", "EL", "LOS", "LAS", "Y",
}
# ⚠️ **`AGRÍCOLA`, `GANADERA` y `AGROPECUARIA` NO están en la lista, a propósito.**
# Son palabras que DISTINGUEN: «Cooperativa Agrícola de X» y «Cooperativa
# Ganadera de X» son dos entidades y sacarlas las vuelve el mismo nombre. Una
# primera versión las sacaba y reducía toda una cooperativa a `PIGUE` — el
# apellido del pueblo. La guarda de ambigüedad lo atajaría **sólo si las dos
# están cargadas**; si una sola está, el pareo daría un match confiado y
# equivocado.

# Encabezados que reconoce, por CONTENIDO del texto (no por igualdad): un
# encabezado que CONTENGA la palabra alcanza, así `Acumulado Pesos` y `Pesos`
# caen las dos en el mismo lugar. Se comparan ya normalizados (sin acentos, sin
# puntuación), por eso las variantes con tilde no hacen falta acá.
#
# ⚠️ El orden de este dict IMPORTA: se detecta de arriba hacia abajo y una
# columna ya asignada no vuelve a ofrecerse. `account` va PRIMERO porque
# «cuenta» es ambigua —puede ser el número o el nombre del cliente— y si gana
# `nombre`, el número se leería como si fuera una denominación.
CLAVES = {
    "account": ("account", "nro", "numero", "comitente", "id cuenta", "codigo"),
    "nombre": ("denominacion", "nombre", "cliente", "razon", "titular",
               "cuenta"),
    "pesos": ("pesos", "peso", "ars"),
    "mtr": ("mtr", "usd", "dolar"),
}


def normalizar(s: str) -> str:
    """Mayúsculas, sin acentos, sin puntuación, un solo espacio.

    ⚠️ **Las abreviaturas con puntos se re-arman.** `S.A.` queda como `S A` al
    convertir la puntuación en espacios, y eso NO parea con `SA` — que es la
    misma sociedad escrita de la otra forma. Se juntan las corridas de letras
    sueltas (`S A` → `SA`, `S A C I F` → `SACIF`) para que las dos grafías
    caigan en el mismo lugar.

    La corrida tiene que ser de DOS o más: una letra suelta aislada es la `Y` de
    «AGRÍCOLA Y GANADERA», y pegarla al vecino cambiaría el nombre.
    """
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^0-9A-Za-z ]+", " ", s.upper())
    partes = s.split()

    salida: list[str] = []
    corrida: list[str] = []
    for p in partes + [""]:
        if len(p) == 1 and p.isalpha():
            corrida.append(p)
            continue
        if len(corrida) >= 2:
            salida.append("".join(corrida))
        else:
            salida.extend(corrida)
        corrida = []
        if p:
            salida.append(p)
    return " ".join(salida)


def sin_sufijos(s: str) -> str:
    """La misma normalización, sacando además la forma jurídica."""
    return " ".join(p for p in normalizar(s).split() if p not in SUFIJOS)


def _num(v: Any) -> float | None:
    """Texto de planilla → número. `1.234.567,89` y `1,234,567.89` conviven."""
    if v is None or (isinstance(v, float) and v != v):  # NaN
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace("$", "").replace(" ", "")
    if not t or t in {"-", "--"}:
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    if "," in t and "." in t:            # el ÚLTIMO separador es el decimal
        t = (t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".")
             else t.replace(",", ""))
    elif "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        n = float(t)
    except ValueError:
        return None
    return -n if neg else n


def leer_archivo(ruta: Path) -> list[dict]:
    """CSV o XLSX → lista de dicts. Sin pandas para el CSV: una dependencia
    menos en el camino más común."""
    if ruta.suffix.lower() in {".xlsx", ".xlsm"}:
        import pandas as pd
        df = pd.read_excel(ruta, dtype=object)
        return [{str(k): v for k, v in r.items()} for r in df.to_dict("records")]
    with ruta.open(newline="", encoding="utf-8-sig") as f:
        muestra = f.read(4096)
        f.seek(0)
        try:
            dial = csv.Sniffer().sniff(muestra, delimiters=",;\t|")
        except csv.Error:
            dial = csv.excel
        return list(csv.DictReader(f, dialect=dial))


def detectar(cols: list[str], que: str, explicito: str | None,
             tomadas: set[str]) -> str | None:
    """La columna para `que`, sin repetir una ya asignada.

    `tomadas` evita que una misma columna sirva para dos roles: sin eso,
    `Acumulado Pesos` puede quedar como pesos Y como nombre (contiene «cuenta»
    en algunos archivos), y el importe se leería como denominación.
    """
    if explicito:
        if explicito not in cols:
            raise SystemExit(f"--col-{que}={explicito!r} no está. Hay: {cols}")
        return explicito
    for c in cols:
        if c in tomadas:
            continue
        n = normalizar(c).lower()
        if any(k in n for k in CLAVES[que]):
            return c
    return None


def _pinta_numero(filas: list[dict], col: str | None) -> float:
    """Qué proporción de la columna se lee como número. Sirve para desconfiar."""
    if not col:
        return 0.0
    vals = [f.get(col) for f in filas if str(f.get(col) or "").strip()]
    if not vals:
        return 0.0
    return sum(1 for v in vals if _num(v) is not None) / len(vals)


def cuentas_indexadas() -> tuple[dict, dict, dict]:
    """Los índices de pareo. Un nombre que apunta a DOS cuentas se guarda con
    las dos: es lo que después permite reportarlo como ambiguo en vez de
    quedarse callado con la primera."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT account, denominacion, name FROM ap5.cuentas")
        filas = cur.fetchall()
    exacto: dict[str, set[str]] = {}
    flojo: dict[str, set[str]] = {}
    nombres: dict[str, str] = {}
    for account, deno, name in filas:
        for txt in (deno, name):
            if not txt:
                continue
            nombres.setdefault(normalizar(txt), str(txt))
            exacto.setdefault(normalizar(txt), set()).add(str(account))
            flojo.setdefault(sin_sufijos(txt), set()).add(str(account))
    return exacto, flojo, nombres


def parear(nombre: str, exacto: dict, flojo: dict) -> tuple[str, list[str]]:
    """(nivel, cuentas). `nivel` ∈ exacto | sin_sufijos | ninguno."""
    n = normalizar(nombre)
    if n in exacto:
        return "exacto", sorted(exacto[n])
    f = sin_sufijos(nombre)
    if f and f in flojo:
        return "sin_sufijos", sorted(flojo[f])
    return "ninguno", []


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Cargar ap5.acumulado desde un archivo con DENOMINACIONES.")
    ap.add_argument("archivo")
    ap.add_argument("--fecha", required=True,
                    help="AAAA-MM-DD. EXCLUSIVA: los importes ya contienen todo "
                         "hasta ese día inclusive.")
    ap.add_argument("--aplicar", action="store_true",
                    help="escribir de verdad (sin esto sólo muestra)")
    ap.add_argument("--por", default="importar_ap5_acumulado")
    ap.add_argument("--crear-cuentas", action="store_true",
                    help="dar de alta en `ap5.cuentas` las que traen ACCOUNT y "
                         "no existen. Sin número de cuenta no se puede: por eso "
                         "sólo aplica a las filas que ya lo tienen.")
    for c in ("nombre", "pesos", "mtr", "account"):
        ap.add_argument(f"--col-{c}")
    args = ap.parse_args()

    ruta = Path(args.archivo)
    if not ruta.exists():
        raise SystemExit(f"no existe: {ruta}")
    filas = leer_archivo(ruta)
    if not filas:
        raise SystemExit("el archivo no tiene filas")
    cols = list(filas[0])

    # El orden es el de CLAVES: `account` primero, y cada columna asignada sale
    # del pozo para las siguientes.
    tomadas: set[str] = set()
    elegidas: dict[str, str | None] = {}
    for que, explicito in (("account", args.col_account),
                           ("nombre", args.col_nombre),
                           ("pesos", args.col_pesos),
                           ("mtr", args.col_mtr)):
        col = detectar(cols, que, explicito, tomadas)
        elegidas[que] = col
        if col:
            tomadas.add(col)
    c_acc, c_nom = elegidas["account"], elegidas["nombre"]
    c_pes, c_mtr = elegidas["pesos"], elegidas["mtr"]

    print("=" * 78)
    print(f"{ruta.name} · {len(filas)} filas · fecha {args.fecha} "
          f"({'APLICA' if args.aplicar else 'DRY — no escribe'})")
    print("=" * 78)
    print(f"  columnas del archivo : {cols}")
    print(f"  nombre   → {c_nom!r}")
    print(f"  account  → {c_acc!r}  (si está, MANDA: no se parea por nombre)")
    print(f"  pesos    → {c_pes!r}")
    print(f"  MtR      → {c_mtr!r}")
    if not c_acc and not c_nom:
        raise SystemExit("\nNo encontré ni nombre ni account. Pasalos con "
                         "--col-nombre / --col-account.")
    if not c_pes and not c_mtr:
        raise SystemExit("\nNo encontré ninguna columna de importe. Pasala con "
                         "--col-pesos / --col-mtr.")

    # ⚠️ Detectar por el ENCABEZADO puede errarle, y errarle en silencio: si la
    # columna «Cuenta» trae el número y se leyó como nombre, no parea NADA y el
    # reporte dice «0 pareos» sin explicar por qué. Mirar el CONTENIDO lo delata
    # antes de que pierdas media hora.
    avisos = []
    if c_nom and _pinta_numero(filas, c_nom) > 0.8:
        avisos.append(f"{c_nom!r} está tomada como NOMBRE y casi todo su "
                      f"contenido son números. ¿Es el número de cuenta? "
                      f"→ --col-account {c_nom!r}")
    if c_acc and _pinta_numero(filas, c_acc) < 0.5:
        avisos.append(f"{c_acc!r} está tomada como ACCOUNT y su contenido no "
                      f"parece numérico. ¿Es la denominación? "
                      f"→ --col-nombre {c_acc!r}")
    for col, rol in ((c_pes, "pesos"), (c_mtr, "MtR")):
        if col and _pinta_numero(filas, col) < 0.5:
            avisos.append(f"{col!r} está tomada como {rol} y casi nada de su "
                          f"contenido se lee como número.")
    for a in avisos:
        print(f"\n  ⚠️ {a}")

    # ⚠️ En un Excel con filas de título arriba del encabezado, pandas toma la
    # PRIMERA fila como encabezado y las columnas quedan `Unnamed: 0`, `Unnamed:
    # 1`… El síntoma es «no encontré ninguna columna», que no dice por qué.
    if sum(1 for c in cols if normalizar(c).startswith("UNNAMED")) >= len(cols) / 2:
        print("\n  ⚠️ La mayoría de las columnas se llaman `Unnamed: N`. Eso pasa")
        print("     cuando el encabezado NO está en la primera fila del Excel")
        print("     (hay un título o filas en blanco arriba). Borrá esas filas y")
        print("     dejá los encabezados arriba de todo, o guardá como CSV.")

    exacto, flojo, nombres = cuentas_indexadas()
    print(f"  ap5.cuentas: {len(exacto)} nombres indexados")

    ok, ambiguas, sin_parear = [], [], []
    for f in filas:
        pesos = _num(f.get(c_pes)) if c_pes else None
        mtr = _num(f.get(c_mtr)) if c_mtr else None
        nombre = str(f.get(c_nom) or "").strip() if c_nom else ""
        acc = str(f.get(c_acc) or "").strip() if c_acc else ""

        if acc:
            # ⚠️ El número MANDA sobre el nombre. Es la razón de ser del CSV de
            # re-importación: un account escrito a mano no puede ser ambiguo.
            ok.append({"account": acc, "nombre": nombre, "nivel": "account",
                       "pesos": pesos or 0.0, "mtr": mtr or 0.0})
            continue
        nivel, cuentas = parear(nombre, exacto, flojo)
        if len(cuentas) == 1:
            ok.append({"account": cuentas[0], "nombre": nombre, "nivel": nivel,
                       "pesos": pesos or 0.0, "mtr": mtr or 0.0})
        elif len(cuentas) > 1:
            ambiguas.append({"nombre": nombre, "cuentas": cuentas,
                             "pesos": pesos, "mtr": mtr})
        else:
            cerca = difflib.get_close_matches(normalizar(nombre), nombres, 3, 0.6)
            sin_parear.append({"nombre": nombre, "pesos": pesos, "mtr": mtr,
                               "sugerencias": [nombres[c] for c in cerca]})

    print(f"\n  ✔ parean 1-a-1 : {len(ok)}")
    for nivel in ("account", "exacto", "sin_sufijos"):
        n = sum(1 for x in ok if x["nivel"] == nivel)
        if n:
            print(f"      {nivel:<12} {n}")
    print(f"  ⚠ AMBIGUAS     : {len(ambiguas)}  (no se escriben)")
    print(f"  ✗ sin parear   : {len(sin_parear)}")

    if ok:
        print("\n  ── las que se escribirían " + "─" * 46)
        print(f"     {'account':<10} {'nivel':<12} {'pesos':>16} {'MtR':>14}  nombre")
        for x in ok[:40]:
            print(f"     {x['account']:<10} {x['nivel']:<12} {x['pesos']:>16,.2f} "
                  f"{x['mtr']:>14,.2f}  {x['nombre'][:30]}")
        if len(ok) > 40:
            print(f"     … y {len(ok)-40} más")

    if ambiguas:
        print("\n  ── AMBIGUAS: el mismo nombre en más de una cuenta " + "─" * 22)
        print("     No se elige una: elegir sería adivinar de quién es la plata.")
        for x in ambiguas:
            print(f"     {x['nombre'][:44]:<44} → {x['cuentas']}")

    # ⚠️ **EL NÚMERO QUE IMPORTA ES EL DE ACÁ, no el de «sin parear».**
    # El archivo puede traer 218 filas y nosotros tener 98 cuentas: las 120 de
    # más NO son un problema —son clientes que no operan futuros con la cámara—
    # y mirar «139 sin parear» asusta sin motivo. Lo que sí importa es al revés:
    # **de NUESTRAS cuentas, ¿cuáles se quedaron sin valor?** Ésas son las filas
    # que la vista va a mostrar vacías.
    escritas_acc = {x["account"] for x in ok}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT account, COALESCE(NULLIF(name,''), denominacion, account) "
                    "FROM ap5.cuentas ORDER BY 2")
        nuestras = [(str(a), str(n or a)) for a, n in cur.fetchall()]
    huerfanas = [(a, n) for a, n in nuestras if a not in escritas_acc]

    print("\n  ── LO QUE DE VERDAD FALTA: cuentas NUESTRAS sin valor " + "─" * 18)
    print(f"     {len(nuestras) - len(huerfanas)} de {len(nuestras)} cuentas de "
          f"`ap5.cuentas` reciben acumulado")
    if huerfanas:
        print(f"     {len(huerfanas)} se quedan sin valor — la vista las muestra "
              f"en cero:")
        for a, n in huerfanas[:30]:
            cerca = difflib.get_close_matches(normalizar(n),
                                              [normalizar(x["nombre"])
                                               for x in sin_parear], 2, 0.55)
            pista = f"   ¿será «{cerca[0]}»?" if cerca else ""
            print(f"       {a:<10} {n[:44]:<44}{pista}")
        if len(huerfanas) > 30:
            print(f"       … y {len(huerfanas)-30} más")
        print("\n     Estas son las que vale la pena resolver a mano. Las del")
        print("     archivo que no parean, en cambio, pueden ser simplemente")
        print("     clientes que no operan futuros — no hay nada que arreglar.")

    # El CSV de los que faltan, con sugerencias: completás `account` y lo
    # re-importás. Ese camino no puede parear mal — ya viene el número.
    if sin_parear or ambiguas:
        salida = ruta.with_suffix(".sin_parear.csv")
        with salida.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["account", "denominacion", "acumulado_pesos",
                        "acumulado_mtr", "por_que", "sugerencias"])
            for x in sin_parear:
                w.writerow(["", x["nombre"], x["pesos"] or "", x["mtr"] or "",
                            "sin match", " | ".join(x["sugerencias"])])
            for x in ambiguas:
                w.writerow(["", x["nombre"], x["pesos"] or "", x["mtr"] or "",
                            "ambigua", " | ".join(x["cuentas"])])
        print(f"\n  → escribí {salida}")
        print("     Completá la columna `account` y volvé a correr el script")
        print("     apuntando a ESE archivo: con el número no hay ambigüedad.")

    if not args.aplicar:
        print("\n  DRY: no se escribió nada. Agregá --aplicar cuando el reporte")
        print("  de arriba te convenza.")
        return

    if args.crear_cuentas:
        # ⚠️ El nombre del archivo va a `name` (la columna MANUAL), nunca a
        # `denominacion`: esa la publica la cámara y la escribe el job desde
        # `AccountDetails`. Pisarla con un nombre de planilla rompería el
        # arbitraje entre las dos — y dejándola NULL, el job la resuelve solo en
        # la próxima corrida.
        nuevas = [(x["account"], x["nombre"]) for x in ok
                  if x["nivel"] == "account" and x["nombre"]]
        if nuevas:
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO ap5.cuentas (account, name, visto_at) "
                    "VALUES (%s, %s, now()) ON CONFLICT (account) DO NOTHING",
                    nuevas)
                altas = cur.rowcount
            print(f"\n  ✓ {altas} cuenta(s) nuevas en ap5.cuentas "
                  f"(de {len(nuevas)} con account); la denominación de la cámara "
                  f"la completa el job")

    from api.services.ap5_posiciones import guardar_acumulado
    escritas, fallidas = 0, []
    for x in ok:
        r = guardar_acumulado(x["account"], x["pesos"], x["mtr"], args.fecha,
                              por=args.por)
        if r.get("ok"):
            escritas += 1
        else:
            fallidas.append((x["account"], r.get("motivo")))
    print(f"\n  ✓ {escritas} cuentas escritas en ap5.acumulado")
    for a, m in fallidas[:10]:
        print(f"    ✗ {a}: {m}")


if __name__ == "__main__":
    main()
