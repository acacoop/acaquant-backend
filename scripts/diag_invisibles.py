"""`scripts/diag_invisibles.py` — ¿CUÁNTO DE LO QUE EL AGENTE TIENE ABIERTO NO
SE VE EN NINGUNA PANTALLA?

READ-ONLY. No escribe una fila.

LA PREGUNTA
===========

El agente tiene tres pantallas donde puede aparecer un hallazgo abierto:

    AHORA      nació HOY (hora argentina), sin leer, y su regla no es RECURRENTE
    ENCONTRÓ   tiene ARREGLO (un botón que escribe)
    PATRONES   su trío nació >= 3 veces en 30 días y cuenta episodios

Un hallazgo que no entra en ninguna de las tres **está abierto, vivo y
confirmado por el detector hace minutos — y no lo ve nadie.** El caso típico:
un AVISO (sin botón) que nació ayer. De AHORA se fue solo al cambiar el día;
a ENCONTRÓ no entra porque no hay nada que apretar.

⚠️⚠️ **ESTE SCRIPT NO DEFINE «VISIBLE»: SE LO PREGUNTA A LAS PANTALLAS.**

Es la regla #9 y es lo único que hace que el número valga. Un `WHERE` propio
acá sería una CUARTA definición de «lo que está abierto» conviviendo con las
tres reales, y ya pasó adentro de este mismo subsistema: `cola.investigables()`
ofrecía 60 casos mientras la pantalla mostraba 3 y 3, porque tenía su propio
criterio. Así que se llama a `vista.ahora()`, `vista.encontro()` y
`vista.cronicos()` —las mismas funciones que dibujan el modal— y se compara
por `id`. Si mañana cambia lo que el modal considera visible, este diag cambia
con él.

COSTO
=====

Cuatro consultas sobre `agente.hallazgos`, todas por índice y acotadas a lo
ABIERTO (cincuenta filas en el orden de magnitud de hoy). No toca la red, no
cuesta créditos, no mira la tenencia ni el master. Se puede correr en rueda.

    python -m scripts.diag_invisibles
    python -m scripts.diag_invisibles --detalle               # el TEXTO de cada uno
    python -m scripts.diag_invisibles --detalle --evidencia   # + la evidencia cruda
"""
from __future__ import annotations

import argparse
import textwrap
from collections import defaultdict

from agente import tipos
from core.postgres import get_pool
from core.tz import AR_TZ


def _abiertos() -> list[dict]:
    """Todo lo ABIERTO, con lo que hace falta para explicar por qué no se ve.

    `ABIERTOS` sale de `agente/tipos.py` —el mismo vocabulario que usan las
    vistas— y no de una lista de estados escrita acá.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT f.id, f.habilidad, f.regla, f.sujeto, f.severidad, f.nombre, "
            "       f.problema, f.que_hacer, f.evidencia, "
            "       f.arreglo, f.detectado_at, f.visto_ultima_vez, f.veces, "
            "       f.leido_at, f.leido_por "
            "  FROM agente.hallazgos f "
            " WHERE f.estado = ANY(%s) "
            " ORDER BY f.detectado_at",
            (list(tipos.ABIERTOS),))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _visibles() -> tuple[set[int], set[int], set[tuple]]:
    """Lo que HOY se ve en cada pantalla, preguntado a las pantallas.

    PATRONES agrupa por trío (no por `id`), así que se devuelve el conjunto de
    tríos activos: un hallazgo cuyo trío está ahí es visible por esa vía
    aunque su fila no se liste una por una.
    """
    from agente import vista

    ahora = {f["id"] for f in vista.ahora()["filas"]}
    encontro = {f["id"] for f in vista.encontro()["filas"]}
    cron = vista.cronicos(200)
    trios = {(c["habilidad"], c["sujeto"], c["regla"]) for c in cron["activos"]}
    return ahora, encontro, trios


def _dias(desde, ahora) -> float:
    return (ahora - desde).total_seconds() / 86400


def _parrafo(rotulo: str, txt: str) -> None:
    """Un párrafo con rótulo que entra en la consola del Droplet, que es angosta."""
    # 13 = 2 de margen + 11 del rótulo: la continuación alinea con el texto.
    sangria = " " * 13
    cuerpo = textwrap.fill(" ".join((txt or "").split()), 76,
                           initial_indent=sangria, subsequent_indent=sangria)
    print(f"  {rotulo + ':':11}{cuerpo.lstrip()}")


def _detalle(invisibles: list[dict], ahora_utc, *, con_evidencia: bool) -> None:
    """El texto de cada problema, **agrupado por (habilidad, regla)**.

    ⚠️ Agrupar no es cosmético: es lo que hace posible contestar «¿esto es
    basura?». `job_reporto` tiene diecisiete hallazgos invisibles, pero son DOS
    reglas — el texto y el `que_hacer` son los mismos para toda la familia y lo
    único que cambia es el sujeto. Imprimirlos de a uno son diecisiete párrafos
    repetidos que nadie lee; imprimir el texto UNA vez y los sujetos abajo entra
    en una pantalla y se juzga de un vistazo.

    Por cada fila se dice **por qué no se ve**, que es la mitad del juicio: no es
    lo mismo «nació ayer y no tiene botón» (el agujero de diseño) que «alguien
    apretó ✓ el 2 de septiembre» (una decisión de una persona, que se revierte
    destildando).
    """
    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for f in invisibles:
        grupos[(f["habilidad"], f["regla"])].append(f)

    for (hab, regla), fs in sorted(grupos.items(), key=lambda x: -len(x[1])):
        uno = fs[0]
        print(f"\n{'─' * 78}")
        # La severidad la fija la regla, pero no se AFIRMA: si el grupo trae
        # más de una, se imprimen las que hay (REGLA #2 en chiquito).
        sev = "/".join(sorted({f["severidad"] for f in fs})).upper()
        print(f" {hab} · {regla} · {len(fs)} invisible(s) · severidad {sev}")
        print(f"{'─' * 78}")
        if uno["nombre"]:
            print(f"  {uno['nombre']}")
        _parrafo("QUÉ DICE", uno["problema"])
        _parrafo("QUÉ HACER", uno["que_hacer"])
        print()
        print(f"    {'SUJETO':40} {'DÍAS':>5} {'VECES':>6}  {'ÚLT. OK':>11}  POR QUÉ NO SE VE")
        for f in sorted(fs, key=lambda x: x["detectado_at"]):
            if f["arreglo"]:
                porque = "TIENE BOTÓN y no está en ENCONTRÓ ← mirar"
            elif f["leido_at"] is not None:
                quien = f["leido_por"] or "alguien"
                porque = f"✓ leído por {quien} el {f['leido_at'].astimezone(AR_TZ):%d/%m}"
            else:
                porque = "aviso sin botón, nació antes de hoy"
            print(f"    {f['sujeto'][:40]:40} "
                  f"{_dias(f['detectado_at'], ahora_utc):>5.1f} {f['veces']:>6}  "
                  f"{f['visto_ultima_vez'].astimezone(AR_TZ):%d/%m %H:%M}  {porque}")
            if con_evidencia:
                print(f"        evidencia: {f['evidencia']}")


def main() -> int:
    from datetime import UTC, datetime

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detalle", action="store_true",
                    help="el TEXTO de cada problema invisible, agrupado por regla")
    ap.add_argument("--evidencia", action="store_true",
                    help="con --detalle: agrega la evidencia cruda de cada uno")
    a = ap.parse_args()

    ahora_utc = datetime.now(UTC)
    filas = _abiertos()
    if not filas:
        print("No hay ningún hallazgo abierto. Nada que medir.")
        return 0
    en_ahora, en_encontro, trios_cronicos = _visibles()

    invisibles: list[dict] = []
    for f in filas:
        f["en_ahora"] = f["id"] in en_ahora
        f["en_encontro"] = f["id"] in en_encontro
        f["en_patrones"] = (f["habilidad"], f["sujeto"], f["regla"]) in trios_cronicos
        if not (f["en_ahora"] or f["en_encontro"] or f["en_patrones"]):
            invisibles.append(f)

    print(f"\n{'═' * 78}")
    print(f" LO ABIERTO DEL AGENTE — {ahora_utc.astimezone(AR_TZ):%d/%m/%Y %H:%M} (hora de la mesa)")
    print(f"{'═' * 78}")
    print(f"  abiertos            {len(filas):>4}")
    print(f"  se ven en AHORA     {sum(1 for f in filas if f['en_ahora']):>4}"
          "   (nacieron hoy, sin leer)")
    print(f"  se ven en ENCONTRÓ  {sum(1 for f in filas if f['en_encontro']):>4}"
          "   (tienen botón)")
    print(f"  se ven en PATRONES  {sum(1 for f in filas if f['en_patrones']):>4}"
          "   (crónicos activos)")
    print(f"  ── INVISIBLES ──    {len(invisibles):>4}"
          f"   ({len(invisibles) * 100 // len(filas)}% de lo abierto)")

    if not invisibles:
        print("\n  Nada invisible: todo lo abierto se ve en alguna pantalla.\n")
        return 0

    # POR QUÉ no se ve cada uno. No es lo mismo «no tiene botón y nació ayer»
    # —el caso estructural— que «alguien lo marcó leído hoy», que es una
    # decisión de una persona y se arregla destildando.
    por_causa: dict[str, int] = defaultdict(int)
    for f in invisibles:
        if f["arreglo"]:
            por_causa["tiene botón pero NO está en ENCONTRÓ (mirar: es un bug)"] += 1
        elif f["leido_at"] is not None:
            por_causa["aviso marcado LEÍDO (lo sacó una persona)"] += 1
        else:
            por_causa["AVISO sin botón que nació antes de hoy"] += 1
    print("\n  POR QUÉ NO SE VEN")
    for causa, n in sorted(por_causa.items(), key=lambda x: -x[1]):
        print(f"    {n:>4}  {causa}")

    # POR HABILIDAD: es el corte que dice dónde está el problema de diseño.
    por_hab: dict[str, list[dict]] = defaultdict(list)
    for f in invisibles:
        por_hab[f["habilidad"]].append(f)
    print("\n  POR HABILIDAD")
    print(f"    {'HABILIDAD':22} {'N':>3}  {'MÁS VIEJO':>10}  {'ÚLT. CONFIRMADO':>16}  REGLAS")
    for hab, fs in sorted(por_hab.items(), key=lambda x: -len(x[1])):
        viejo = max(_dias(f["detectado_at"], ahora_utc) for f in fs)
        ultimo = max(f["visto_ultima_vez"] for f in fs)
        reglas = ", ".join(sorted({f["regla"] for f in fs}))
        print(f"    {hab:22} {len(fs):>3}  {viejo:>7.1f} d  "
              f"{ultimo.astimezone(AR_TZ):%d/%m %H:%M}  {reglas[:34]}")

    if a.detalle:
        _detalle(invisibles, ahora_utc, con_evidencia=a.evidencia)

    altas = sum(1 for f in invisibles if f["severidad"] == "alta")
    print(f"\n  De los {len(invisibles)} invisibles, {altas} son severidad ALTA.")
    print("  El detector los sigue confirmando en cada pasada: están abiertos,")
    print("  vivos, y no aparecen en ninguna de las tres pantallas.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
