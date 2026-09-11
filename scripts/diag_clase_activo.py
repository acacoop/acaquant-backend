"""`scripts/diag_clase_activo.py` — **POR QUÉ NO SE PROPONE LA CLASE DE ACTIVO.**

Read-only. Corre **la misma cadena que la pantalla** (`agente.clase.proponer`
con las mismas fuentes que le pasa `arreglos.CompletarFicha.preview`) sobre los
mismos faltantes, y cuenta el resultado por MOTIVO.

⚠️ **NO CLASIFICA NADA: AGRUPA LO QUE LA FILA YA DICE.** Desde §0.fg el motivo
viaja en la `nota` de cada fila —la misma que se dibuja en el listado—, así que
acá se cuenta por `nota` y nada más. La primera versión tenía su PROPIO
clasificador de causas: funcionaba, y era una segunda definición del mismo
criterio esperando a divergir (REGLA #9). Lo que se mide acá es exactamente lo
que va a leer la mesa en pantalla.

    python -m scripts.diag_clase_activo
    python -m scripts.diag_clase_activo --detalle            # fila por fila
    python -m scripts.diag_clase_activo --cartera FCI        # solo esa cartera
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict

from agente import clase, fuentes
from agente.detectores import catalogo as det
from core.fci_match import normalizar as normalizar_fondo


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def _quien(f: dict, ancho: int = 22) -> str:
    return (f.get("ticker") or f.get("unidad") or "?")[:ancho]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detalle", action="store_true", help="fila por fila")
    ap.add_argument("--cartera", default="", help="filtrar por cartera")
    a = ap.parse_args()

    campo = next(c for c in det.CAMPOS if c["campo"] == "clase_activo")
    filas = det.faltantes(campo)
    if a.cartera:
        filas = [f for f in filas
                 if (f.get("cartera") or "").strip().upper() == a.cartera.strip().upper()]
    usadas = det.valores_usados("clase_activo")
    fichas = fuentes.fichas_primary()
    master = fuentes.master()
    fci = fuentes.fci_por_unidad()

    _titulo("EL UNIVERSO")
    print(f"  {len(filas)} título(s) en cartera de cliente sin clase_activo"
          + (f" (filtrado por cartera = {a.cartera})" if a.cartera else ""))
    print(f"  lista cerrada: {len(usadas)} valor(es) ya cargados en clase_activo")
    print(f"  Primary: {'—' if fichas is None else len(fichas)} ficha(s)   ·   "
          f"master de curvas: {'—' if master is None else len(master)} bono(s)   ·   "
          f"mercado.fci linkeados: {'—' if fci is None else len(fci)}")
    for fuente_, valor in (("fichas_primary", fichas), ("master", master),
                           ("fci_por_unidad", fci)):
        if valor is None:
            print(f"  ⚠ {fuente_}() = None → la regla que depende de esa fuente "
                  f"no puede proponer NADA en esta corrida")
    print("\n  POR CARTERA:")
    for c, n in Counter((f.get("cartera") or "(vacía)").strip().upper()
                        for f in filas).most_common():
        print(f"    {c:28} {n:>4}")

    # La MISMA llamada que hace la pantalla.
    propuestas = clase.proponer(filas, fichas, usadas, master=master, fci=fci)
    con = [f for f in propuestas if f["propuesto"]]
    sin: dict[str, list[dict]] = defaultdict(list)
    for f in propuestas:
        if not f["propuesto"]:
            sin[f["nota"] or "(sin nota — ES UN BUG: toda fila sin propuesta "
                            "tiene que decir por qué)"].append(f)

    _titulo("EL VEREDICTO")
    print(f"  ✔ SE PROPONEN   {len(con):>4}   ← el ejecutor las escribe solo")
    print(f"  ✘ SIN PROPUESTA {sum(len(v) for v in sin.values()):>4}")
    if con:
        print("\n  ── LO QUE SE PROPONE, por fuente ──")
        for (fu, v), n in Counter((f["fuente"], f["propuesto"])
                                  for f in con).most_common():
            print(f"    {fu:8} {v:24} {n:>4}")

    _titulo("SIN PROPUESTA — el motivo que la fila le muestra a la mesa")
    for nota, fs in sorted(sin.items(), key=lambda kv: -len(kv[1])):
        print(f"\n  {len(fs):>4}  {nota}")
        print(f"        {', '.join(_quien(f) for f in fs[:8])}"
              + (" …" if len(fs) > 8 else ""))

    # ── FCI: ¿de dónde salió (o no) la propuesta? ──────────────────────────
    #
    # El link `mercado.fci.unidad` es el que confirmó la mesa en Manager y es la
    # clave con la que se escribe; el match por nombre es el respaldo. Este
    # bloque mide cuánto aporta cada uno — si el link cubre todo, el respaldo
    # por nombre se puede discutir.
    fci_filas = [f for f in propuestas
                 if (f.get("cartera") or "").strip().upper() in clase._CARTERAS_FCI]
    if fci_filas and fci is not None:
        _titulo("FCI — el LINK vs. el NOMBRE")
        linkeados = sum(1 for f in fci_filas if f["unidad"] in fci)
        con_tipo = sum(1 for f in fci_filas
                       if (fci.get(f["unidad"], {}).get("tipo_renta") or "").strip())
        indice = clase._indice_primary(fichas or [])
        matchean = sum(1 for f in fci_filas
                       if indice.get(normalizar_fondo(f.get("ticker") or "")))
        print(f"  {len(fci_filas)} FCI sin clase_activo")
        print(f"  {linkeados} linkeados en mercado.fci por `unidad`   ·   "
              f"{con_tipo} de ésos con `tipo_renta` de Primary")
        print(f"  {matchean} matchean una ficha de Primary por NOMBRE "
              f"(con el normalizador del dominio FCI)")
        for fu, n in Counter(f["fuente"] for f in fci_filas if f["propuesto"]).items():
            print(f"  → {n} resueltos por «{fu}»")

    if a.detalle:
        _titulo("FILA POR FILA")
        for f in propuestas:
            estado = (f"✔ {f['fuente']}:{f['propuesto']}" if f["propuesto"] else "✘")
            print(f"  {estado:30} {(f.get('cartera') or '—')[:14]:14} "
                  f"{(f.get('ticker') or '')[:20]:20} {f['unidad'][:44]}")
            if not f["propuesto"]:
                print(f"      ↳ {f['nota']}")

    print("\n  Read-only: no escribió nada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
