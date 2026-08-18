"""diag_ia_informe — por qué «la IA no devolvió nada». Read-only.

El analista del informe masivo (`av_agent_analista`) devolvió vacío. Eso puede
ser cinco cosas muy distintas y desde afuera se ven iguales:

  · el proveedor no está configurado (sin key);
  · el presupuesto diario de tokens está agotado;
  · el modelo tardó más que el timeout;
  · el modelo contestó pero TODO su output se fue en el razonamiento
    (`thinking`) y no quedó texto — la lección del P2, que ya pasó una vez;
  · el prompt es más grande de lo que el proveedor acepta.

`ia.trazas` guarda una fila POR LLAMADA, ok o no, con el error y los tokens.
Ahí está la respuesta; esto la lee y además hace un smoke chico para separar
«el proveedor no anda» de «este pedido en particular no entra».

    python -m scripts.diag_ia_informe
"""
from __future__ import annotations

import traceback

SEP = "─" * 72


def _seccion(t: str) -> None:
    print(f"\n{SEP}\n{t}\n{SEP}")


def main() -> None:
    _seccion("1) EL PROVEEDOR: ¿está configurado?")
    try:
        from core import ai, llm
        for p in llm.estado_proveedores():
            print(f"   {'✅' if p.get('configurado') else '❌'} {p.get('nombre') or p.get('proveedor')}"
                  f"  modelos={p.get('modelos')}  no_entrena={p.get('no_entrena')}")
            if p.get("saldo"):
                print(f"      saldo: {p['saldo'].get('disponible')}")
        print(f"\n   ai.disponible('av_agent_informe') → {ai.disponible('av_agent_informe')}")
        print(f"   presupuesto global/día: {ai.presupuesto_dia_global():,} tokens")
        if (m := ai.motivo_presupuesto(None)):
            print(f"   ⚠️  {m}")
    except Exception:
        traceback.print_exc()

    _seccion("2) LAS TRAZAS de la tarea (ia.trazas) — acá está el error real")
    try:
        from core.postgres import get_pool
        with get_pool().connection() as cx, cx.cursor() as cur:
            cur.execute(
                "SELECT ts, modelo, tokens_in, tokens_out, latencia_ms, ok, error, "
                "       length(coalesce(respuesta,'')), length(coalesce(razonamiento,'')) "
                "FROM ia.trazas WHERE tarea = 'av_agent_informe' "
                "ORDER BY ts DESC LIMIT 5")
            filas = cur.fetchall()
        if not filas:
            print("   (ninguna traza — la llamada NO llegó a salir: mirá la sección 1)")
        for ts, mod, ti, to, ms, ok, err, lr, lz in filas:
            print(f"   {'✅' if ok else '❌'} {ts:%d/%m %H:%M:%S}  {mod}")
            print(f"      tokens in={ti} out={to} · {ms} ms · "
                  f"respuesta {lr} chars · razonamiento {lz} chars")
            if err:
                print(f"      error: {err[:300]}")
            if ok and not lr and lz:
                print("      ⚠️  CONTESTÓ PERO SIN TEXTO: todo el output se fue en el"
                      " razonamiento → subir max_tokens (es la lección del P2).")
    except Exception:
        traceback.print_exc()

    _seccion("3) EL TAMAÑO del pedido: ¿cuánto pesa el informe?")
    try:
        from api.services import av_agent_analista as an
        from api.services import av_agent_masivo
        run = av_agent_masivo.estado(None)
        if not run.get("ok"):
            print(f"   {run.get('error')}")
        else:
            sistema = len(an._DOMINIO) + len(an._causas()) + len(an._lecciones()) \
                + len(an._INSTRUCCIONES)
            usuario = len(run.get("texto") or "")
            # ~4 chars por token es la regla gruesa habitual; alcanza para saber
            # si estamos cerca de un límite o lejísimos.
            print(f"   corrida #{run.get('id')} · {run.get('hechos')} casos")
            print(f"   system: {sistema:>7,} chars  (~{sistema // 4:,} tokens)")
            print(f"   user:   {usuario:>7,} chars  (~{usuario // 4:,} tokens)")
            print(f"   TOTAL:  {sistema + usuario:>7,} chars  "
                  f"(~{(sistema + usuario) // 4:,} tokens)")
            if (sistema + usuario) // 4 > 60000:
                print("   ⚠️  Es un prompt MUY grande — probable causa del vacío.")
    except Exception:
        traceback.print_exc()

    _seccion("4) SMOKE: ¿el proveedor contesta algo, lo que sea?")
    try:
        from core import ai
        r = ai.completar("smoke", system="Respondé solo: ok", user="ok?")
        print(f"   respuesta: {r!r}")
        if r:
            print("   ✅ el proveedor ANDA → el problema es de ESTE pedido"
                  " (tamaño, timeout o max_tokens), no de la conexión.")
        else:
            print("   ❌ ni el smoke contesta → el problema es el proveedor"
                  " o el presupuesto, no el informe.")
    except Exception:
        traceback.print_exc()

    print(f"\n{SEP}")


if __name__ == "__main__":
    main()
