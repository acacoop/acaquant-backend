"""scripts/diag_patas_master.py — ¿por qué los 11 `pata_equivocada` no caducan?

Read-only. El síntoma (2026-08-22): 11 bonos (CP36O, HJCLO, LOC6O, …) con la
acción BLOQUEADA («ya no aparece en el control: se resolvió solo») **siguen en
LA LISTA**, y el monitor en vivo los seguía emitiendo. Eso solo pasa si dos
lectores ven cosas distintas — REGLA #9. Este diag imprime, por ticker, TODAS
las copias del símbolo para ver cuál quedó vieja:

    columna  mercado.curvas.instrumento   (la que gana, la que mira _caduco)
    blob     data->>'ticker'              (la que lee el MOTOR y el detector live)
    especies la pata default en USD       (lo que el control considera correcto)
    foto     evidencia.sugerido del último hallazgo pata_equivocada
    lote     las últimas acciones mercado.apuntar_pata sobre ese sujeto

Con eso se decide QUÉ copia corregir — sin adivinar (REGLA #2).

Uso: python -m scripts.diag_patas_master
"""
from __future__ import annotations

import json

from core.postgres import get_pool


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        # Los sujetos con pata_equivocada en la última foto.
        cur.execute(
            "SELECT DISTINCT ticker, evidencia->>'sugerido' "
            "  FROM agente.av_agent_hallazgos "
            " WHERE regla = 'pata_equivocada' "
            "   AND corrida_at = (SELECT max(corrida_at) "
            "                       FROM agente.av_agent_hallazgos "
            "                      WHERE regla = 'pata_equivocada')")
        casos = {t: (s or "") for t, s in cur.fetchall()}
        print(f"pata_equivocada en la última foto: {len(casos)}\n")

        for tk in sorted(casos):
            cur.execute(
                "SELECT instrumento, data->>'ticker' "
                "  FROM mercado.curvas WHERE upper(ticker) = upper(%s)", (tk,))
            f = cur.fetchone() or (None, None)
            col, blob = (f[0] or ""), (f[1] or "")
            cur.execute(
                "SELECT ticker_especie, moneda, es_default "
                "  FROM mercado.especies WHERE upper(ticker) = upper(%s) "
                " ORDER BY es_default DESC", (tk,))
            patas = [f"{r[0]}({r[1]}{'*' if r[2] else ''})" for r in cur.fetchall()]
            sug = casos[tk]
            caduca = bool(sug) and bool(col) and col.strip() == sug.strip()
            print(f"{tk}")
            print(f"  columna : {col!r}")
            print(f"  blob    : {blob!r}"
                  + ("   ⚠ DIVERGE de la columna" if blob.strip() != col.strip()
                     else ""))
            print(f"  especies: {', '.join(patas) or '—'}")
            print(f"  sugerido: {sug!r}")
            print(f"  _caduco daría: {'SÍ (no debería verse)' if caduca else 'NO (por eso se ve)'}")
            cur.execute(
                "SELECT ts, detalle FROM agente.av_agent_acciones "
                " WHERE accion LIKE '%%apuntar_pata%%' "
                "   AND upper(objetivo) = upper(%s) "
                " ORDER BY ts DESC LIMIT 2", (tk,))
            for at, det in cur.fetchall():
                d = det if isinstance(det, str) else json.dumps(det, default=str)
                print(f"  lote    : {at:%d/%m %H:%M} {str(d)[:140]}")
            print()


if __name__ == "__main__":
    main()
