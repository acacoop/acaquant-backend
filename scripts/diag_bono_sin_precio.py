"""scripts/diag_bono_sin_precio.py — ¿POR QUÉ ESTE BONO NO TIENE PRECIO?

**Read-only.** Recorre la cadena ENTERA de un ticker y dice en qué eslabón se
corta. Es la pregunta del user sobre AO29:

    *«Ahora es relevante entender por qué uno de los bonos que aparece en la
    tabla no tiene precio durante la rueda. Si es por liquidez, o porque no
    suscribe porque hay algo mal — como es el caso del AO29, que claramente hay
    algo mal y ni lo está detectando.»*

LA CADENA, y qué significa que se corte en cada punto:

    1. está en `mercado.curvas`          si no, la vista ni lo muestra
    2. tiene SÍMBOLO de mercado          sin esto el motor no puede pedir nada
    3. el símbolo está en `especies`     la pata del ticker, la fuente única
    4. Primary lo LISTA                  si no existe, no se puede suscribir
    5. está en `market_snapshot`         el motor lo pidió y algo volvió
    6. tiene `last_price` y es de hoy    operó

    python -m scripts.diag_bono_sin_precio AO29
    python -m scripts.diag_bono_sin_precio            # todos los cortados
"""
from __future__ import annotations

import sys

from core.postgres import get_pool


def _fila(ok: bool | None, texto: str) -> str:
    return f"  {'✔' if ok else '—' if ok is None else '✖'}  {texto}"


def revisar(tk: str) -> list[str]:
    from api.services import av_agent
    from core import curvas_sql, market_snapshot

    out = [f"\n{tk}\n" + "─" * 62]
    doc = next((d for d in (curvas_sql.cargar_todos() or [])
                if (d.get("ticker_corto") or "").strip().upper() == tk), None)
    if not doc:
        out.append(_fila(False, "NO está en `mercado.curvas` — la vista no lo "
                                "muestra y el agente no lo mira"))
        return out
    out.append(_fila(True, f"está en `mercado.curvas` (curva {doc.get('curva')})"))

    simbolo = (doc.get("ticker") or "").strip()   # el blob usa el nombre viejo
    if not simbolo:
        out.append(_fila(False, "**SIN SÍMBOLO DE MERCADO** (`curvas.instrumento` "
                                "vacío) → el motor no puede pedir un precio que "
                                "nadie nombró. Se completa desde `mercado.especies`."))
    else:
        out.append(_fila(True, f"símbolo: «{simbolo}»"))

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT simbolo, moneda, plazo, es_default, validado_at "
                    "FROM mercado.especies WHERE upper(ticker) = %s "
                    "ORDER BY es_default DESC, simbolo", (tk,))
        patas = cur.fetchall()
    if not patas:
        out.append(_fila(False, "no tiene NINGUNA pata en `mercado.especies` → "
                                "de ahí sale el símbolo; sin patas no hay de dónde"))
    else:
        out.append(_fila(True, f"{len(patas)} pata(s) en `especies`:"))
        for p in patas:
            marca = " (default)" if p[3] else ""
            val = f"validada {p[4]:%Y-%m-%d}" if p[4] else "**sin validar** contra Primary"
            out.append(f"        {p[0]:44} {p[1]}/{p[2]}{marca} · {val}")

    try:
        universo = av_agent.simbolos_primary() or set()
        if simbolo:
            out.append(_fila(simbolo in universo,
                             f"Primary {'lista' if simbolo in universo else 'NO lista'} "
                             f"«{simbolo}» — si no lo lista, suscribirlo es imposible"))
        fuera = [p[0] for p in patas if p[0] not in universo]
        if fuera:
            out.append(f"        patas que Primary NO lista: {fuera}")
    except Exception as e:
        out.append(_fila(None, f"no pude leer el universo de Primary ({e})"))

    # ── LOS EJES DEL MASTER ────────────────────────────────────────────────
    # Sin los TRES el bono no se ubica en ninguna curva (`curvas_vista` línea
    # 141) y el motor puede no calcularle nada: la fila sale entera en «--».
    ejes = {k: doc.get(k) for k in ("emisor_tipo", "moneda_eje", "ajuste")}
    faltan = [k for k, v in ejes.items() if not v]
    out.append(_fila(not faltan,
                     f"ejes: emisor_tipo={ejes['emisor_tipo']!r} · "
                     f"moneda_eje={ejes['moneda_eje']!r} · ajuste={ejes['ajuste']!r}"
                     + (f"  ← **FALTA {', '.join(faltan)}**" if faltan else "")))
    out.append(_fila(None, f"valor_nominal={doc.get('valor_nominal')} · "
                           f"tipo={doc.get('tipo')} · "
                           f"flujos={len(doc.get('flujos') or [])} filas"))

    # ── EL SNAPSHOT, con TODAS las métricas ────────────────────────────────
    _METRICAS = ["last_price", "tea", "tem", "paridad", "duration",
                 "mod_duration", "updated_at"]
    if simbolo:
        snap = market_snapshot.cols_map([simbolo], _METRICAS) or {}
        d = snap.get(simbolo)
        if d is None:
            out.append(_fila(False, "NO está en `market_snapshot` → nadie lo "
                                    "suscribió (o el motor nunca recibió nada)"))
        else:
            px = d.get("last_price")
            out.append(_fila(bool(px), f"snapshot: last_price={px} · "
                                       f"updated_at={d.get('updated_at')}"))
            metricas = {k: d.get(k) for k in _METRICAS
                        if k not in ("last_price", "updated_at")}
            vacias = [k for k, v in metricas.items() if v in (None, 0)]
            # **El caso que ningún detector mira hoy**: el motor RECIBIÓ el precio
            # y no pudo valuar. Eso no es liquidez — es configuración.
            out.append(_fila(len(vacias) < len(metricas),
                             "métricas: "
                             + " · ".join(f"{k}={v}" for k, v in metricas.items())
                             + (" ← **TIENE PRECIO Y NINGUNA MÉTRICA**: el motor "
                                "recibió el precio y no pudo valuarlo. Eso NO es "
                                "falta de liquidez."
                                if px and len(vacias) == len(metricas) else "")))

    # ── LAS OTRAS PATAS: ¿alguna tiene precio? ─────────────────────────────
    if patas:
        otras = market_snapshot.cols_map([p[0] for p in patas],
                                         ["last_price", "updated_at"]) or {}
        out.append("")
        out.append("  precio POR PATA (la vista lee la del master, no la default):")
        for p in patas:
            e = otras.get(p[0]) or {}
            out.append(f"        {p[0]:44} {e.get('last_price') or '—'!s:>14}"
                       f"  {e.get('updated_at') or ''}")

    # Y lo que DICE EL DETECTOR sobre este bono, ahora mismo.
    from core import market_snapshot as ms
    simbolos = [(b.get("ticker") or "").strip()
                for b in (curvas_sql.cargar_todos() or []) if b.get("ticker")]
    hall = av_agent.detectar_sin_precio(
        [doc], ms.cols_map(simbolos, ["last_price", "updated_at"]) or {})
    out.append("")
    out.append(f"  EN RUEDA: {'SÍ' if av_agent.en_rueda() else 'NO'} "
               f"(fuera de rueda, «precio viejo» no se reporta)")
    if hall:
        for h in hall:
            out.append(f"  → EL AGENTE DICE [{h['regla']}] {h['motivo']}")
    else:
        out.append("  → el agente NO reporta nada de este bono")
    return out


def main() -> int:
    from core import curvas_sql, market_snapshot

    if len(sys.argv) > 1:
        for tk in sys.argv[1:]:
            print("\n".join(revisar(tk.strip().upper())))
        print()
        return 0

    # Sin argumento: los que el motor no está pudiendo pricear.
    bonos = curvas_sql.cargar_todos() or []
    simbolos = [(b.get("ticker") or "").strip() for b in bonos if b.get("ticker")]
    snap = market_snapshot.cols_map(simbolos, ["last_price", "updated_at"]) or {}
    sin_simbolo = [b for b in bonos if not (b.get("ticker") or "").strip()]
    sin_snap = [b for b in bonos
                if (b.get("ticker") or "").strip()
                and (b.get("ticker") or "").strip() not in snap]
    print(f"\n{len(bonos)} bonos en el master\n" + "=" * 62)
    print(f"  SIN SÍMBOLO de mercado : {len(sin_simbolo)}")
    for b in sin_simbolo[:40]:
        print(f"      {(b.get('ticker_corto') or '?'):10} {b.get('curva')}")
    print(f"  con símbolo pero SIN SNAPSHOT : {len(sin_snap)}")
    for b in sin_snap[:40]:
        print(f"      {(b.get('ticker_corto') or '?'):10} {b.get('ticker')}")

    # **CON PRECIO Y SIN NINGUNA MÉTRICA.** El motor recibió el precio y no pudo
    # valuar: la fila sale entera en «--» y hoy no lo mira ningún detector.
    # Se cuenta ANTES de escribir la regla (REGLA #2): si son cinco es un bug
    # puntual, si son ochenta es una decisión de arquitectura mal leída.
    full = market_snapshot.cols_map(
        simbolos, ["last_price", "tea", "paridad", "duration"]) or {}
    mudos = []
    for b in bonos:
        sim = (b.get("ticker") or "").strip()
        d = full.get(sim) or {}
        if not d.get("last_price"):
            continue
        if any(d.get(k) for k in ("tea", "paridad", "duration")):
            continue
        mudos.append(b)
    print(f"  CON PRECIO y sin NINGUNA métrica : {len(mudos)}")
    for b in mudos[:40]:
        print(f"      {(b.get('ticker_corto') or '?'):10} "
              f"px={full[(b.get('ticker') or '').strip()].get('last_price')!s:>12}  "
              f"eje={b.get('moneda_eje')}/{b.get('ajuste')} "
              f"tipo={b.get('tipo')} curva={b.get('curva')}")
    print("\n  detalle de uno: python -m scripts.diag_bono_sin_precio <TICKER>\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
