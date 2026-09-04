"""`scripts/diag_caducidad.py` — QUÉ CADUCARÍA, sin escribir una sola fila.

**Read-only. No hace ni un `UPDATE`.** Existe por la REGLA #2: la caducidad
cierra hallazgos sola, y antes de dejarla suelta hay que poder mirar con datos
REALES a quién le pegaría — no confiar en que los tests de estructura pasan.

    python -m scripts.diag_caducidad            # el resumen
    python -m scripts.diag_caducidad --detalle  # fila por fila, con el motivo

QUÉ CONTESTA
============

    1. ¿Cuántos hallazgos ABIERTOS hay sobre sujetos que ya no existen?
    2. ¿Cuáles, y con qué fundamento y de qué fuente?
    3. ¿Alguna habilidad se pasaría del tope por corrida?
    4. ¿Cuántas REINCIDENCIAS quedan activas con el criterio nuevo?
    5. ¿Qué títulos están MUERTOS en `portafolio.assets` y SIGUEN en el master?
       — la pregunta que apareció al correr esto la primera vez (ver abajo).

⚠️ **Mira los ABIERTOS y el motor caduca los que NO VINIERON en la corrida.**
Son conjuntos distintos a propósito: acá se ve el universo COMPLETO de sujetos
muertos con hallazgo vivo —que es la pregunta «¿a quién le pegaría esto?»—,
mientras que el motor sólo toca los que además dejó de ver. O sea: **lo que
liste este diag es el TECHO, nunca menos que lo que va a pasar de verdad.**

LA PREGUNTA 5 SALIÓ DE LA PRIMERA CORRIDA REAL (2026-09-04)
===========================================================

El diag encontró UN caso —`GMCGO`, marcado `vigente=false` motivo `vencido`— con
un hallazgo `sin_punta` ABIERTO. Y no va a caducar, porque el detector lo SIGUE
VIENDO: `bono_sin_precio` itera `mercado.curvas`, y ahí el bono sigue.

**Los dos jobs miran fechas distintas** (verificado en el código):

    validar_instrumentos  apaga el asset con `assets.vencimiento` O
                          `curvas.fecha_vencimiento` — le alcanza cualquiera
    cleanup_curvas        borra del master SOLO con `curvas.fecha_vencimiento`,
                          y sin fecha devuelve False: **no borra nunca**

Entonces un título con vencimiento cargado en `assets` y NULL en `curvas` queda
**muerto en una mitad del sistema y vivo en la otra, para siempre** — y genera
un hallazgo que nadie va a poder cerrar nunca. Es la REGLA #9 exacta: dos copias
del mismo dato («cuándo vence») y cada job consulta la suya.

⚠️ **Hipótesis, no hecho**: que el `fecha_vencimiento` de `mercado.curvas` esté
vacío en esos títulos es lo que explicaría el caso, pero hay que MEDIRLO — para
eso está la sección 5. La columna `venc_master` de esa tabla es la respuesta.

Cuando el tema cierre, este archivo se borra (REGLA #5).
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from agente import catalogo, tipos, vigencia
from core.postgres import get_pool


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detalle", action="store_true",
                    help="lista cada hallazgo con su motivo y su fuente")
    args = ap.parse_args()

    con_tipo = {n: h.sujeto_es for n, h in catalogo.HABILIDADES.items()
                if h.sujeto_es}
    _titulo("1. QUIÉN PUEDE CADUCAR — las habilidades que declaran `sujeto_es`")
    if not con_tipo:
        print("  ninguna. Sin declaración no se caduca nada: es el default seguro.")
        return 0
    for nombre, tipo in sorted(con_tipo.items()):
        print(f"  {nombre:<24} sujeto_es={tipo}")
    print(f"\n  Las otras {len(catalogo.HABILIDADES) - len(con_tipo)} no declaran "
          "tipo de sujeto y NO caducan nada.")

    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT habilidad, sujeto, regla, estado, id "
                "  FROM agente.hallazgos "
                " WHERE estado = ANY(%s) AND habilidad = ANY(%s) "
                " ORDER BY habilidad, sujeto",
                (list(tipos.ABIERTOS), sorted(con_tipo)))
            abiertos = cur.fetchall()

        por_hab: dict[str, list] = defaultdict(list)
        for hab, suj, reg, est, hid in abiertos:
            por_hab[hab].append((suj, reg, est, hid))

        _titulo("2. QUÉ CADUCARÍA — sujetos verificados como MUERTOS")
        total_muertos = pasadas_de_tope = 0
        for hab, filas in sorted(por_hab.items()):
            # ⚠️ Se usa la MISMA función que el motor. Reimplementar el criterio
            # acá sería tener dos definiciones de «muerto» (REGLA #9): el diag
            # diría una cosa y el agente haría otra, y nadie se enteraría.
            muertos = vigencia.muertos(conn, con_tipo[hab], [f[0] for f in filas])
            n = sum(1 for f in filas if f[0] in muertos)
            total_muertos += n
            marca = ""
            if n > vigencia.TOPE_POR_CORRIDA:
                marca = f"  ⚠ SE PASA DEL TOPE ({vigencia.TOPE_POR_CORRIDA})"
                pasadas_de_tope += 1
            print(f"  {hab:<24} {n:>4} de {len(filas):>4} abiertos{marca}")
            if args.detalle:
                for suj, reg, est, hid in filas:
                    if (v := muertos.get(suj)):
                        print(f"      #{hid} {suj:<12} {reg:<22} [{est}] "
                              f"→ {v.motivo}  ({v.fuente})")

        print(f"\n  TOTAL que caducaría: {total_muertos}")
        if pasadas_de_tope:
            print(f"  ⚠ {pasadas_de_tope} habilidad(es) se pasan del tope. Eso NO "
                  "frena la corrida:\n    los que sobran cierran por AUSENCIA, "
                  "que dice menos pero no miente.\n    Si se repite, mirá la "
                  "FUENTE antes que al agente.")

        _titulo("3. LAS REINCIDENCIAS — cuántas quedan activas")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FILTER (WHERE h.estado = ANY(%s)) AS activas, "
                "       count(*) AS totales "
                "  FROM agente.reincidencias r "
                "  JOIN agente.hallazgos h ON h.id = r.hallazgo_id",
                (list(tipos.ABIERTOS),))
            activas, totales = cur.fetchone()
        print(f"  activas (su hallazgo sigue abierto): {activas}")
        print(f"  en la tabla, históricas incluidas:   {totales}")
        print("\n  La fila nunca se borra: «el arreglo X aguantó N días» es la "
              "evidencia\n  con la que después se decide qué arreglo es confiable. "
              "Lo que se apaga\n  es la ALARMA, no el hecho.")

        _titulo("4. MUERTOS EN UN LADO, VIVOS EN EL OTRO — los que el master no soltó")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT c.ticker, c.fecha_vencimiento, a.motivo, a.venc_assets, "
                "       coalesce(h.n, 0) AS abiertos "
                "  FROM mercado.curvas c "
                "  JOIN LATERAL ("
                "     SELECT bool_and(NOT coalesce(x.vigente, true)) AS de_baja, "
                "            min(x.vigencia_motivo) AS motivo, "
                "            max(x.vencimiento) AS venc_assets "
                "       FROM portafolio.assets x WHERE x.ticker = c.ticker"
                "  ) a ON a.de_baja "
                "  LEFT JOIN LATERAL ("
                "     SELECT count(*) AS n FROM agente.hallazgos y "
                "      WHERE y.sujeto = c.ticker AND y.estado = ANY(%s)"
                "  ) h ON true "
                " ORDER BY (c.fecha_vencimiento IS NULL) DESC, c.ticker",
                (list(tipos.ABIERTOS),))
            zombis = cur.fetchall()

        if not zombis:
            print("  ninguno. El master y el catálogo dicen lo mismo.")
        else:
            print(f"  {'TICKER':<10} {'venc_master':<13} {'venc_assets':<13} "
                  f"{'motivo':<10} hallazgos_abiertos")
            sin_fecha = 0
            for tk, venc_c, motivo, venc_a, abiertos in zombis:
                sin_fecha += venc_c is None
                vc = f"{venc_c or '— NULL —'}"
                va = f"{venc_a or '—'}"[:10]
                print(f"  {tk:<10} {vc:<13} {va:<13} "
                      f"{(motivo or '—'):<10} {abiertos}")
            print(f"\n  {len(zombis)} título(s) que `portafolio.assets` da de baja y "
                  f"siguen en `mercado.curvas`.")
            if sin_fecha:
                print(f"  ⚠ {sin_fecha} de ellos SIN `fecha_vencimiento` en el master "
                      "— y esa es la causa:\n    `cleanup_curvas` sólo borra con esa "
                      "columna, y sin fecha devuelve False.\n    `validar_instrumentos` "
                      "en cambio se conforma con la de `assets`.\n    Dos copias del "
                      "mismo dato, cada job consultando la suya (REGLA #9).")
            print("\n  Mientras sigan en el master, sus hallazgos NO caducan: el "
                  "detector\n  los sigue viendo, y la caducidad sólo alcanza a lo que "
                  "dejó de verse.\n  Arreglar la fecha en el master los saca solo, y "
                  "ahí sí cierran — con motivo.")

    print("\n✅ Read-only: no se escribió nada.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
