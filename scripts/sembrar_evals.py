"""scripts/sembrar_evals.py — el eval set NO tiene por qué arrancar en cero.

Doc madre: `docs/AV_AGENT.md` §0.f. **DRY-RUN por default** (REGLA #4).
Idempotente: cada voto lleva `ref = accion:<id>` y un índice único lo rechaza si
ya estaba, así que re-correrlo no infla ningún número.

EL PROBLEMA
===========

`MIN_VOTOS` son 10 **por causa**. Con ~13 tipos y varias reglas cada uno, llenar
el eval set a mano son más de 100 clicks — y hasta que estén, la capa 1 del
roadmap no habilita la 3. O sea que la medición tarda semanas en servir por una
razón puramente administrativa.

Pero el juicio **ya está guardado**: cada acción que un humano APROBÓ y que salió
bien es alguien diciendo *«la causa que dio el agente era la correcta»*.

QUÉ SE PUEDE DERIVAR Y QUÉ NO — la parte que importa
=====================================================

El libro de acciones registra QUÉ se hizo (`alta_bono`), no QUÉ CAUSA lo motivó.
Así que la causa hay que deducirla del tipo, y **eso solo es honesto donde el
tipo emite UNA sola regla**:

    alta_bono          → falta_en_base   → 1 regla   ✅ `no_esta_en_curvas`
    completar_flujos   → sin_flujo       → 1 regla   ✅ `flujos_vacios`
    crear_curva        → hueco_de_curva  → 1 regla   ✅ `ajuste_sin_curva`
    arreglar_bono      → tasa_sospechosa → 6 reglas  ✖ no se sabe cuál se juzgó
    ignorar_ticker     → —                          ✖ es RELEVANCIA, no corrección

Los dos últimos son los que hacen falta explicar:

  · **`arreglar_bono` no se deriva.** Asignarle una de las seis reglas mediría la
    precisión de una con los votos de otra — peor que no tener el dato, porque
    además lo esconde detrás de un número.
  · **IGNORAR no es un ✖.** «No me interesa este bono» no dice que el agente se
    haya equivocado; dice que el bono no importa. Confundirlas castigaría al
    detector por hacer bien su trabajo sobre un papel irrelevante.

Y todos entran como **`derivado`**, nunca como `humano`: no cuentan para
`candidata_a_auto`. Si contaran, el agente podría habilitarse solo.

Uso:
    python -m scripts.sembrar_evals            # DRY-RUN
    python -m scripts.sembrar_evals --aplicar  # escribe
"""
from __future__ import annotations

import argparse

from api.services import av_agent_evals
from api.services.av_agent_acciones import REGLA_DE_ACCION
from core.postgres import get_pool


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    with get_pool().connection() as conn, conn.cursor() as cur:
        # Solo las APROBADAS por una persona (`por` no nulo) y que SALIERON BIEN.
        # Una que falló no dice nada sobre el diagnóstico: dice que la escritura
        # no anduvo, que es otra cosa.
        cur.execute(
            "SELECT a.id, a.accion, a.objetivo, a.por, a.ts "
            "FROM mercado.av_agent_acciones a "
            "LEFT JOIN mercado.av_agent_evals e ON e.ref = 'accion:' || a.id "
            "WHERE a.ok AND a.por IS NOT NULL AND a.accion = ANY(%s) "
            "  AND e.id IS NULL "
            "ORDER BY a.ts",
            (list(REGLA_DE_ACCION),))
        pendientes = cur.fetchall()

        # Lo que NO se deriva, contado. **Decir cuánto se deja afuera es parte del
        # resultado**: sin esto, «sembré 12» se lee como «había 12».
        cur.execute(
            "SELECT accion, count(*) FROM mercado.av_agent_acciones "
            "WHERE ok AND por IS NOT NULL AND accion <> ALL(%s) "
            "GROUP BY accion ORDER BY count(*) DESC",
            (list(REGLA_DE_ACCION),))
        fuera = cur.fetchall()

    print(f"\n{'=' * 78}\nSEMBRADO DEL EVAL SET desde el libro de acciones\n{'=' * 78}")
    if not pendientes:
        print("\n  No hay nada nuevo que sembrar (o ya se sembró: es idempotente).")
    else:
        print(f"\n  {len(pendientes)} acción(es) aprobada(s) por un humano y "
              f"exitosa(s) → un ✔ derivado cada una:\n")
        for _id, accion, obj, por, ts in pendientes[:30]:
            print(f"    {obj:<12} {REGLA_DE_ACCION[accion]:<20} "
                  f"{por:<28} {ts:%Y-%m-%d}")
        if len(pendientes) > 30:
            print(f"    … y {len(pendientes) - 30} más")

    if fuera:
        print("\n  NO se derivan (y por qué):")
        for accion, n in fuera:
            motivo = ("su tipo emite VARIAS reglas: no se sabe cuál se juzgaba"
                      if accion == "arreglar_bono" else
                      "es un juicio de RELEVANCIA, no de corrección"
                      if accion in ("ignorar_ticker", "designorar") else
                      "es un paso de otra acción, no un diagnóstico propio")
            print(f"    {accion:<20} {n:>4}  — {motivo}")

    if not args.aplicar:
        print(f"\n{'=' * 78}\n  DRY-RUN. Nada se escribió. Para aplicar: --aplicar\n")
        return 0

    n_ok = n_dup = 0
    for _id, accion, obj, por, _ts in pendientes:
        r = av_agent_evals.votar(
            caso=obj, dominio="bono", causa=REGLA_DE_ACCION[accion],
            acierta=True, por=por, origen="derivado", ref=f"accion:{_id}",
            nota=f"derivado: {por} aprobó «{accion}» y salió bien")
        if r.get("duplicado"):
            n_dup += 1
        elif r.get("ok"):
            n_ok += 1
    print(f"\n  ✔ {n_ok} voto(s) derivado(s) sembrado(s)"
          + (f" · {n_dup} ya estaban" if n_dup else ""))

    res = av_agent_evals.resumen()
    if res.get("ok"):
        print(f"\n  El eval set ahora: {res['total']} votos "
              f"({res['humanos']} humanos · {res['derivados']} derivados)")
        print("  ⚠️  `candidata_a_auto` cuenta SOLO los humanos — los derivados "
              "dan\n      contexto, no abren la compuerta.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
