"""`scripts/diag_quien_escribe.py` — **CUÁNTAS TABLAS SE EXIGEN SIN SABER QUIÉN LAS ESCRIBE.**

Read-only. No escribe una sola fila, no toca la API de ningún proveedor, y hace
UNA sola query (el perfil que el agente ya tiene guardado). Todo lo demás sale
de leer el código del repo.

## La pregunta que contesta

`agente/detectores/sistema.py::tabla_quieta` le exige frescura a ~190 tablas, y
antes de cantar consulta a `core/escribe.py`:

    reloj   → la dispara un cron o un motor   → se le EXIGE
    evento  → la dispara una persona/request  → se SALTEA
    no_se   → no se pudo averiguar            → ⚠️ se le SIGUE EXIGIENDO

El tercer caso es a propósito (*«dejar de mirar algo que no entendimos es cómo
se pierde una señal de verdad»*), pero tiene un costo que nadie midió: **cada
tabla en `no_se` que en realidad es de EVENTO produce un aviso falso**, con un
`que_hacer` que dice «relanzar el job que la escribe» sin nombrar ninguno —
porque no hay.

Pasó de verdad y es lo que motivó este diag: `operaciones.tesoreria_cheques`
—que se llena con carga MANUAL— cantó como «dejó de escribir · es tiempo real
(cada 10 s)». Su ritmo fue MEDIDO durante un rato de actividad y después se le
exigió como si fuera un motor.

## Por qué hay que medirlo ANTES de construir nada

Según el tamaño del balde `no_se`, la respuesta es distinta y son dos trabajos
que no se parecen en nada:

    pocas   → se declaran a mano en `core.escribe.POR_OCASION`. Cero IA.
    muchas  → averiguar quién escribe cada una es trabajo de LEER CÓDIGO
              siguiendo un rastro (el INSERT usa una variable, o un helper, o
              `core.pg_mirror`), y eso se hace a mano, tabla por tabla.

Sin este número, elegir una de las dos sería adivinar (REGLA #2).

## De dónde saca el universo

⚠️ **NO reimplementa la selección del detector.** Aplica las MISMAS tres
guardas, llamando a las mismas funciones: sólo las que tienen ritmo medible
(`tablas.perfiles(solo_con_ritmo=True)`), sólo nuestros schemas
(`peso.schemas_nuestros()`) y sin las que ya tienen contrato en SALUD
(`tablas._ya_tienen_contrato()`). Dos definiciones del mismo universo darían
números distintos sin que ninguna falle (REGLA #9), y este diag existe para
decidir sobre esos números.

## Costo

Una query a `manager.tabla_perfil` (~190 filas) más el escaneo de los `INSERT`
del repo, que `core.escribe` cachea. Segundos. **Se puede correr en rueda.**

    python -m scripts.diag_quien_escribe
    python -m scripts.diag_quien_escribe --atraso    # + quiénes cantan HOY
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import sys

from core import escribe

# ⚠️ `agente.tablas` y `agente.peso` se importan DENTRO de las funciones que los
# usan: arrastran `core.postgres`, y la clasificación de `core.escribe` —que es
# la mitad interesante de este diag— sale de leer el repo y no necesita base.


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def _tabla(filas: list[tuple], cols: tuple[str, ...], anchos: tuple[int, ...]) -> None:
    print("  " + " ".join(f"{c:<{a}}" for c, a in zip(cols, anchos, strict=True)))
    print("  " + " ".join("─" * a for a in anchos))
    for f in filas:
        print("  " + " ".join(f"{(v if v is not None else '—')!s:<{a}}"[:a]
                              for v, a in zip(f, anchos, strict=True)))


def _universo() -> list[dict]:
    """Las mismas tablas que mira `tabla_quieta`, con sus mismas tres guardas."""
    from agente import peso, tablas
    con_contrato = tablas._ya_tienen_contrato()
    nuestros = peso.schemas_nuestros()
    out = []
    for p in tablas.perfiles(solo_con_ritmo=True):
        if nuestros and p["schema"] not in nuestros:
            continue
        nombre = f"{p['schema']}.{p['tabla']}"
        if nombre in con_contrato:
            continue
        out.append({**p, "nombre": nombre})
    return out


# Las carpetas que `core.escribe._mapa()` escanea. Lo que se escribe desde
# `agente/` o `scripts/` es invisible para el mapa, y eso NO es un bug:
# esas carpetas no tienen una clase única (`agente/` escribe `hallazgos` en cada
# pasada —reloj— y `acciones` cuando alguien aprieta —evento—). Se declaran.
_ESCANEADAS = ("jobs", "engines", "core", "api")


def _donde_se_nombra(nombre: str) -> list[str]:
    """Las carpetas de primer nivel cuyos `.py` mencionan esta tabla.

    ⚠️ **Menciona ≠ escribe.** Un `SELECT` cuenta igual que un `INSERT`, así que
    esto NO prueba quién la escribe: acota DÓNDE buscar. Es a propósito — si
    supiéramos detectar el INSERT, la tabla no estaría en `no_se`.
    """
    raiz = pathlib.Path(__file__).resolve().parents[1]
    fuera = {".git", "node_modules", "__pycache__", ".venv"}
    carpetas = set()
    for f in raiz.rglob("*.py"):
        rel = f.relative_to(raiz).parts
        if len(rel) < 2 or rel[0] in fuera:
            continue
        try:
            if nombre in f.read_text(encoding="utf-8", errors="ignore"):
                carpetas.add(rel[0])
        except OSError:
            continue
    return sorted(carpetas)


def _porque_no_se(nombre: str) -> tuple[str, str]:
    """(en qué balde cae, dónde se la nombra) — y cada balde se arregla distinto.

    `declararla`   la tabla sólo se nombra en carpetas que el mapa no escanea
                   (`agente/`, `scripts/`). No hay nada que buscar: el escritor
                   está ahí y su clase es mixta o no corresponde. Va a
                   `POR_OCASION`, o a `_QUIEN_DISPARA` si toda la carpeta tiene
                   una sola clase. **Determinístico, sin modelo.**
    `buscarla`     se nombra en una carpeta escaneada y aun así el INSERT no se
                   detectó. ⚠️ **La causa dominante ya está medida y es UNA**:
                   el nombre vive en una constante del módulo y se interpola —
                   `_TABLA_CHEQUES = "operaciones.tesoreria_cheques"` y después
                   `f"INSERT INTO {_TABLA_CHEQUES} (…)"`. Ninguno de los tres
                   regex de `core.escribe` ve eso, y son ~37 tablas, casi todas
                   de `api/` (o sea: EVENTO, o sea: no había que exigirles nada).
                   Antes de salir a buscar nada, resolver las constantes.
    `nadie la usa` no aparece en ningún `.py`: o la siembra un script que ya se
                   borró, o la escribe algo que no es este repo (Manager por la
                   UI, Supabase). Ninguna de las dos se arregla acá.
    """
    carpetas = _donde_se_nombra(nombre)
    if not carpetas:
        return "nadie la usa", "—"
    if not set(carpetas) & set(_ESCANEADAS):
        return "declararla", ",".join(carpetas)
    return "buscarla", ",".join(carpetas)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--atraso", action="store_true",
                    help="además, cuáles de las `no_se` están atrasadas AHORA "
                         "(según el perfil guardado, sin consultar cada tabla)")
    args = ap.parse_args()

    universo = _universo()
    if not universo:
        print("\n⚠️ El perfil de tablas está vacío: `manager.tabla_perfil` no tiene "
              "filas con ritmo.\n   El barrido lo hace `tabla_quieta` fuera de rueda. "
              "Sin perfil, este diag no mide nada.\n")
        return 1

    baldes: dict[str, list[dict]] = {escribe.RELOJ: [], escribe.EVENTO: [], escribe.NO_SE: []}
    for p in universo:
        baldes[escribe.la_dispara(p["nombre"])].append(p)

    total = len(universo)
    _titulo(f"EL UNIVERSO DE `tabla_quieta` — {total} tablas con ritmo medible")
    _tabla([("reloj", len(baldes[escribe.RELOJ]), "se le EXIGE frescura — correcto"),
            ("evento", len(baldes[escribe.EVENTO]), "se saltea — correcto"),
            ("no_se", len(baldes[escribe.NO_SE]), "⚠️ se le exige SIN SABER si corresponde")],
           ("CLASE", "CUÁNTAS", "QUÉ LE PASA HOY"), (8, 8, 56))

    ns = sorted(baldes[escribe.NO_SE], key=lambda p: p["nombre"])
    if not ns:
        print("\n  ✅ Ninguna tabla cae en `no_se`: a todas se les sabe el origen.\n"
              "     No hay ruido de esta clase que arreglar.\n")
        return 0

    _titulo(f"LAS {len(ns)} QUE SE EXIGEN A CIEGAS")
    print("  Cada una es un aviso potencial cuyo `que_hacer` va a decir «relanzar el\n"
          "  job que la escribe» sin nombrar ninguno — porque no se sabe cuál es.\n")
    clasificadas = [(p, *_porque_no_se(p["nombre"])) for p in ns]
    _tabla([(p["nombre"], p["cadencia"], f"{p['filas']:,}", balde, donde)
            for p, balde, donde in clasificadas],
           ("TABLA", "CADENCIA", "FILAS", "QUÉ CORRESPONDE", "SE LA NOMBRA EN"),
           (42, 12, 10, 14, 24))

    por_balde = collections.Counter(b for _, b, _ in clasificadas)
    _titulo("Y ESTO ES LO QUE DECIDE EL CAMINO")
    _tabla([("declararla", por_balde["declararla"],
             "sólo vive en carpetas que el mapa no escanea → POR_OCASION"),
            ("buscarla", por_balde["buscarla"],
             "el INSERT existe y está escondido → hay que seguir un rastro"),
            ("nadie la usa", por_balde["nadie la usa"],
             "ningún .py la nombra → no se escribe desde este repo")],
           ("BALDE", "CUÁNTAS", "QUÉ SIGNIFICA"), (14, 8, 52))

    if args.atraso:
        from agente import tablas
        declarado = tablas.declarados()
        cantan = [p for p in ns
                  if tablas.frescura(p, declarado=declarado.get(p["nombre"]))["estado"]
                  == "atrasada"]
        _titulo(f"DE ESAS, {len(cantan)} ESTÁN ATRASADAS AHORA MISMO")
        if cantan:
            print("  Éstas son las que están generando (o van a generar) un hallazgo hoy.\n"
                  "  ⚠️ El atraso sale del perfil GUARDADO, no de consultar cada tabla: "
                  "puede\n     estar viejo si el último barrido no corrió.\n")
            _tabla([(p["nombre"],
                     tablas.frescura(p, declarado=declarado.get(p["nombre"]))["motivo"])
                    for p in cantan], ("TABLA", "QUÉ DICE EL DETECTOR"), (40, 50))
        else:
            print("  Ninguna está atrasada en este momento — pero se les sigue exigiendo,\n"
                  "  así que cualquiera puede cantar el día que se quede quieta.\n")

    _titulo("QUÉ HACER CON ESTO")
    print(f"  · Son {len(ns)} de {total} tablas del universo de `tabla_quieta`. Pero el número\n"
          "    que importa NO es ése: es cómo se reparten en los tres baldes de arriba,\n"
          "    porque cada uno es un trabajo distinto y sólo UNO justifica un modelo.\n"
          "\n"
          "      declararla    → una fila en `core.escribe.POR_OCASION` con el motivo en\n"
          "                      castellano, o sumar la carpeta a `_QUIEN_DISPARA` si toda\n"
          "                      ella tiene una sola clase. Determinístico y barato.\n"
          "\n"
          "      buscarla      → el INSERT existe en una carpeta que SÍ se escanea y aun\n"
          "                      así no se detectó: está detrás de un helper, de\n"
          "                      `core.pg_mirror`, o el nombre viaja en una variable.\n"
          "                      Encontrarlo es seguir un rastro cuyo próximo paso depende\n"
          "                      del anterior: se lee el código a mano y se declara la fila.\n"
          "\n"
          "      nadie la usa  → no se escribe desde este repo (la carga Manager por la UI,\n"
          "                      o la sembró un script que ya se borró). Va a POR_OCASION\n"
          "                      igual, pero por otro motivo: no hay job que relanzar.\n"
          "\n"
          "  · Y lo que NO hay que hacer: mandar `no_se` al mismo balde que `evento`.\n"
          "    Dejaría de mirar tablas que sí tienen un job atrás, y esa señal se\n"
          "    perdería en silencio — el peor modo de falla de un monitor.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
