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
       Y sobre todo: ¿las dos fechas de vencimiento COINCIDEN? (ver abajo).

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

⚠️ **LA PRIMERA HIPÓTESIS ERA QUE FALTABA LA FECHA EN EL MASTER. ERA FALSA**, y
medirla es lo único que lo demostró. Lo que hay es peor:

    mercado.curvas.fecha_vencimiento    2028-01-28   → afirma que está VIVO
    portafolio.assets.vencimiento       2026-06-28   → afirma que MURIÓ

**Las dos copias están cargadas y se contradicen por diecinueve meses**, sin
nadie arbitrando. Es la REGLA #9 en su forma más pura, y falla como siempre: no
falla nada, cada mitad es coherente consigo misma, y los dos jobs actúan en
consecuencia sin enterarse el uno del otro:

    validar_instrumentos  usa `assets.vencimiento` O `curvas.fecha_vencimiento`
                          — le alcanza cualquiera, así que apagó el asset
    cleanup_curvas        usa SOLO `curvas.fecha_vencimiento`, que dice 2028
                          — así que no lo borra, y no lo va a borrar hasta 2028

Y de yapa destapó un bug en `agente/vigencia.py`: tomaba el primer «está muerto»
sin mirar si otra fuente afirmaba lo contrario, así que daba por muerto un título
que el master declara vivo. **Corregido**: contradicción = «no sé», y no se
cierra nada. Ver `test_dos_fuentes_que_se_contradicen_no_caducan_nada`.

**RESUELTO el 2026-09-04: manda el MASTER.** La mesa confirmó que GMCGO vence el
2028-01-28. De ahí salieron tres cosas:

  · el árbitro está DECLARADO en `core/duplicados.DUPLICADOS`
    (`vencimiento_master_vs_assets`) → el agente lo mira todas las noches
  · `validar_instrumentos` ya NO apaga cuando las dos fechas se contradicen, y
    **deshace su propio apagado** → GMCGO vuelve a `vigente` solo
  · la fecha equivocada sigue en `assets` y hay que corregirla a mano (Manager →
    TÍTULOS · ASSETS): NO hay arreglo automático, porque ese campo se muestra en
    /aca y en los flujos y un UPDATE masivo apoyado en un solo caso es REGLA #4.

Cuando el tema cierre, este archivo se borra (REGLA #5).
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date

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
                  f"{'motivo':<10} {'abiertos':<9} diagnóstico")
            sin_fecha = contradicen = pendientes = 0
            for tk, venc_c, motivo, venc_a, abiertos in zombis:
                # El master dice que VIVE si su fecha es futura. Que `assets` lo
                # dé de baja al mismo tiempo NO es un detalle: son dos copias del
                # mismo hecho diciendo lo contrario, y hasta que alguien decida
                # cuál manda, el sistema no sabe si el título existe.
                # ⚠️ **SE COMPARAN LAS DOS FECHAS ENTRE SÍ, no sólo el master
                # contra hoy.** La primera versión miraba únicamente si el master
                # decía «vivo» y de ahí concluía «se contradicen» — así que
                # DESPUÉS de corregir la fecha en `assets` seguía gritando
                # contradicción sobre dos fechas idénticas (visto el 2026-09-04,
                # GMCGO con 2028-01-28 de los dos lados).
                #
                # Es el mismo pecado que este diag denuncia: un texto que sigue
                # afirmando algo que dejó de ser cierto. Y acá era peor que
                # inútil — le decía al que lo corrió que su corrección no había
                # servido.
                vive_el_master = venc_c is not None and venc_c >= date.today()
                coinciden = (venc_c is not None and venc_a
                             and str(venc_c) == f"{venc_a}"[:10])
                if venc_c is None:
                    sin_fecha += 1
                    dx = "master SIN fecha → cleanup_curvas no lo borra nunca"
                elif vive_el_master and not coinciden:
                    contradicen += 1
                    dx = "⚠ LAS FECHAS NO COINCIDEN — manda el MASTER: está VIVO"
                elif vive_el_master:
                    # Las fechas ya están de acuerdo: lo único viejo es el tilde,
                    # y eso lo deshace el job solo. No es un problema abierto.
                    pendientes += 1
                    dx = ("✔ fechas OK — sólo quedó viejo el tilde `vigente`: "
                          "lo enciende validar_instrumentos (23:00 UTC L-V)")
                else:
                    dx = "el master ya lo da por vencido: sale en el próximo cleanup"
                vc = f"{venc_c or '— NULL —'}"
                va = f"{venc_a or '—'}"[:10]
                print(f"  {tk:<10} {vc:<13} {va:<13} "
                      f"{(motivo or '—'):<10} {abiertos:<9} {dx}")
            print(f"\n  {len(zombis)} título(s) que `portafolio.assets` da de baja y "
                  f"siguen en `mercado.curvas`.")
            if pendientes:
                print(f"\n  ✔ {pendientes} YA CORREGIDO(S): las dos fechas coinciden y "
                      "el título está vivo.\n    Lo único pendiente es el tilde "
                      "`vigente`, que quedó de antes —\n    `validar_instrumentos` "
                      "deshace su propio apagado en su próxima corrida.\n    No hay "
                      "nada que hacer: si mañana sigue acá, ahí sí avisá.")
            if contradicen:
                print(f"  ⚠⚠ {contradicen} con las DOS fechas cargadas y en desacuerdo. "
                      "MANDA EL MASTER\n    (declarado en `core/duplicados` →"
                      " `vencimiento_master_vs_assets`).\n"
                      "    · el agente NO los caduca: una contradicción es «no sé»\n"
                      "    · `validar_instrumentos` ya no los apaga, y DESHACE el "
                      "apagado que hizo él\n    · falta corregir la fecha en "
                      "`assets` a mano (Manager → TÍTULOS · ASSETS):\n      no hay "
                      "arreglo automático porque ese campo se lee en /aca y en flujos")
            if sin_fecha:
                print(f"  ⚠ {sin_fecha} SIN `fecha_vencimiento` en el master: "
                      "`cleanup_curvas` sólo borra\n    con esa columna, y sin fecha "
                      "devuelve False — no los va a soltar nunca.")
            print("\n  Mientras sigan en el master, sus hallazgos NO caducan: el "
                  "detector\n  los sigue viendo, y la caducidad sólo alcanza a lo que "
                  "dejó de verse.\n  Arreglar la fecha en el master los saca solo, y "
                  "ahí sí cierran — con motivo.")

    print("\n✅ Read-only: no se escribió nada.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
