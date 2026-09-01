"""`scripts/diag_primary_catalogo.py` — ¿EL CATÁLOGO DE PRIMARY QUE USA EL AGENTE ESTÁ VIEJO?

Read-only. Contesta la pregunta que el 2026-09-01 se contestó MAL en pantalla:
el arreglo de `S29E7` dijo «⚠ Primary NO lista este símbolo» y el buscador de
OPERAR lo encontraba en el acto. No se contradecían: miran DOS fuentes.

    · El agente (`core/instrumentos_validos`), el WS de los motores y el job
      `validar_instrumentos` leen **`manager.pyrofex_instruments`**, una FOTO
      que escribe UN solo script, `scripts/discovery_pyrofex`, a mano. No está
      en el crontab. Todo bono licitado después de la última corrida es
      «inexistente» para esa mitad del sistema.
    · OPERAR pregunta a Primary EN VIVO (`pyRofex.get_detailed_instruments`).

Este diag pone las dos fotos una al lado de la otra y mide:

    1. ¿De cuándo es la foto?  — `manager.pyrofex_discovery.generated_at`
    2. ¿Cuántos símbolos ve el filtro? — lo mismo que `instrumentos_validos.validos()`
    3. ¿Están los tickers pedidos?     — en la foto y en Primary EN VIVO
    4. ¿Qué hay en vivo que la foto no tiene? — los que hoy el agente descarta
    5. ¿A quién descartó `soberanos_faltantes` por «no cotiza»? — y si en vivo SÍ cotiza

Uso:
    python -m scripts.diag_primary_catalogo                      # S29E7 por default
    python -m scripts.diag_primary_catalogo S29E7 X29E7 T30E7    # los que quieras
    python -m scripts.diag_primary_catalogo --sin-live           # solo la base (sin sesión pyRofex)

No gasta créditos de 1816: el paso 5 lee `research.mkt_1816_instrumentos`
(el catálogo persistido) y los hallazgos ya guardados, no vuelve a censar.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from core.postgres import get_pool

PLAZOS = ("24hs", "CI", "48hs")


def _filas(sql: str, params: tuple = ()) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def _simbolos_de(tk: str) -> list[str]:
    return [f"MERV - XMEV - {tk} - {p}" for p in PLAZOS]


def _ticker_base(simbolo: str) -> str:
    partes = simbolo.split(" - ")
    return (partes[2] if len(partes) >= 3 else simbolo).strip().upper()


def _foto() -> tuple[set[str] | None, str]:
    """Lo que ve el filtro, por el MISMO lector que usan el WS y el agente."""
    from core import instrumentos_validos
    vs = instrumentos_validos.validos(forzar=True)
    if vs is None:
        return None, ("`validos()` devolvió None: la tabla no se pudo leer o tiene "
                      f"menos de {instrumentos_validos._MINIMO_CREIBLE} símbolos → "
                      "el filtro NO filtra (deja pasar todo)")
    return vs, f"{len(vs)} símbolos — el filtro ESTÁ activo"


def _live() -> tuple[set[str] | None, str]:
    """Lo que Primary publica AHORA. Misma llamada que el buscador de OPERAR."""
    try:
        import pyRofex

        from core.rofex_session import inicializar_sesion
    except Exception as e:  # pragma: no cover - entorno sin pyRofex
        return None, f"pyRofex no importable ({e})"
    if not inicializar_sesion():
        return None, "no pude iniciar sesión pyRofex (credenciales/red)"
    try:
        res = pyRofex.get_detailed_instruments()
    except Exception as e:
        return None, f"get_detailed_instruments falló: {e}"
    if not res or res.get("status") != "OK":
        return None, f"get_detailed_instruments status != OK: {(res or {}).get('status')}"
    out = set()
    for inst in res.get("instruments") or []:
        sym = inst.get("symbol") or (inst.get("instrumentId") or {}).get("symbol")
        if isinstance(sym, str) and sym.strip():
            out.add(sym.strip())
    return out, f"{len(out)} símbolos en vivo"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", default=["S29E7"], help="tickers base (S29E7)")
    ap.add_argument("--sin-live", action="store_true",
                    help="no abrir sesión pyRofex: solo la foto de la base")
    a = ap.parse_args()
    tickers = [t.strip().upper() for t in a.tickers if t.strip()]

    # ── 1. ¿DE CUÁNDO ES LA FOTO? ───────────────────────────────────────────
    _titulo("1 · LA FOTO — manager.pyrofex_discovery / pyrofex_instruments")
    try:
        d = _filas("SELECT generated_at, total_instruments FROM manager.pyrofex_discovery "
                   "WHERE id = 'current'")
    except Exception as e:
        d, err = [], e
        print(f"  ✗ no pude leer pyrofex_discovery: {err}")
    if d and d[0][0]:
        gen, total = d[0]
        edad = datetime.now(UTC) - gen
        print(f"  última corrida de scripts/discovery_pyrofex: {gen:%Y-%m-%d %H:%M} UTC "
              f"— hace {edad.days} día/s ({edad.total_seconds() / 3600:.0f} h)")
        print(f"  total_instruments declarados: {total}")
        if edad.days >= 1:
            print("  ⚠ La foto tiene más de un día. NADA la refresca sola: el discovery "
                  "no está en deploy/crontab.txt.")
    elif d:
        print("  ✗ hay fila 'current' pero sin generated_at")
    else:
        print("  ✗ NUNCA corrió el discovery (no hay fila 'current')")

    n_cfi = _filas("SELECT count(*), coalesce(sum(count),0) FROM manager.pyrofex_instruments")
    print(f"  pyrofex_instruments: {n_cfi[0][0]} CFI · {n_cfi[0][1]} instrumentos sumados")

    # ── 2. LO QUE VE EL FILTRO ──────────────────────────────────────────────
    _titulo("2 · LO QUE VE core/instrumentos_validos.validos() (el filtro del WS y del agente)")
    foto, nota = _foto()
    print(f"  {nota}")

    # ── 3. LO QUE HAY EN VIVO ───────────────────────────────────────────────
    _titulo("3 · PRIMARY EN VIVO — pyRofex.get_detailed_instruments (lo que mira OPERAR)")
    live: set[str] | None = None
    if a.sin_live:
        print("  (salteado por --sin-live)")
    else:
        live, nota = _live()
        print(f"  {nota}")

    # ── 4. LOS TICKERS PEDIDOS, EN LAS DOS FOTOS ────────────────────────────
    _titulo("4 · LOS TICKERS PEDIDOS: ¿en la foto? ¿en vivo?")
    print(f"  {'símbolo':<36} {'foto (tabla)':<14} {'en vivo':<10}")
    for tk in tickers:
        for sim in _simbolos_de(tk):
            en_foto = "—" if foto is None else ("SÍ" if sim in foto else "no")
            en_live = "—" if live is None else ("SÍ" if sim in live else "no")
            marca = " ◀ ACÁ está la contradicción" if (
                en_foto == "no" and en_live == "SÍ") else ""
            print(f"  {sim:<36} {en_foto:<14} {en_live:<10}{marca}")
        # otras grafías en vivo (sufijo D/C, otro segmento)
        if live is not None:
            otras = sorted(s for s in live if f" - {tk}" in s
                           and s not in _simbolos_de(tk))
            if otras:
                print(f"    otras grafías en vivo para {tk}: {', '.join(otras[:8])}")

    # ── 5. QUÉ HAY EN VIVO QUE LA FOTO NO TIENE ─────────────────────────────
    if foto is not None and live is not None:
        _titulo("5 · EN VIVO Y NO EN LA FOTO — lo que hoy el filtro descarta sin decirlo")
        nuevos = sorted(live - foto)
        muertos = sorted(foto - live)
        merv_nuevos = [s for s in nuevos if s.startswith("MERV - XMEV - ")]
        print(f"  en vivo y no en la foto: {len(nuevos)} (de ellos MERV: {len(merv_nuevos)})")
        print(f"  en la foto y ya no en vivo: {len(muertos)}")
        if merv_nuevos:
            bases = sorted({_ticker_base(s) for s in merv_nuevos})
            print(f"  tickers MERV nuevos ({len(bases)}): {', '.join(bases[:60])}"
                  + (" …" if len(bases) > 60 else ""))
        print("  ⚠ Cada uno de esos: el WS NO lo suscribe aunque esté en mercado.curvas, "
              "el alta del agente lo BLOQUEA, y `soberanos_faltantes` lo descarta "
              "si no está en cartera.")

    # ── 6. A QUIÉN DESCARTÓ soberanos_faltantes ─────────────────────────────
    _titulo("6 · soberanos_faltantes — lo que guardó y lo que descartó por «no cotiza»")
    hab = _filas("SELECT ultima_corrida_at, ultimo_resultado, ultimo_error, corridas_hoy, "
                 "corridas_dia FROM agente.habilidades WHERE nombre = 'soberanos_faltantes'")
    if hab:
        u, r, e, ch, cd = hab[0]
        print(f"  última corrida: {u} · resultado: {r} · corridas hoy ({cd}): {ch}"
              + (f" · error: {e}" if e else ""))
    abiertos = _filas("SELECT sujeto, severidad, detectado_at, veces, "
                      "evidencia->>'fuente_universo', evidencia->>'en_cartera' "
                      "FROM agente.hallazgos WHERE habilidad = 'soberanos_faltantes' "
                      "AND estado NOT IN ('resuelto','ignorado') ORDER BY sujeto")
    print(f"  hallazgos abiertos: {len(abiertos)}")
    for s, sev, det, veces, fu, ec in abiertos:
        print(f"    {s:<8} {sev:<6} desde {det:%Y-%m-%d %H:%M} · visto {veces}× · "
              f"universo={fu} · en_cartera={ec}")

    # Reconstruye el descarte del detector con el catálogo persistido de 1816
    # (sin gastar créditos): soberanos ARS/USD que no están en el master y cuyo
    # símbolo armado NO está en la foto. Ese `continue` solo deja un log.info.
    try:
        from core import curvas_ejes
        mios = {t for (t,) in _filas("SELECT upper(btrim(ticker)) FROM mercado.curvas") if t}
        cat = _filas("SELECT ticker, curva, fecha_vencimiento FROM research.mkt_1816_instrumentos")
    except Exception as ex:
        print(f"  ✗ no pude reconstruir el descarte: {ex}")
        cat, mios = [], set()
    descartados = []
    for tk, curva, fv in cat:
        tk = (tk or "").strip().upper()
        if not tk or "@" in tk or tk in mios:
            continue
        ejes = curvas_ejes.desde_1816(curva)
        if ejes is None or ejes.emisor_tipo not in ("soberano", "bcra"):
            continue
        if ejes.moneda not in ("ARS", "USD"):
            continue
        if fv and fv < datetime.now(UTC).date():
            continue
        en_foto = foto is not None and any(s in foto for s in _simbolos_de(tk))
        if not en_foto:
            en_live = None if live is None else any(s in live for s in _simbolos_de(tk))
            descartados.append((tk, curva, fv, en_live))
    if cat:
        print(f"\n  soberanos de 1816 (catálogo persistido) que NO están en el master y "
              f"cuyo símbolo NO está en la foto → el detector los DESCARTA salvo que "
              f"estén en cartera: {len(descartados)}")
        for tk, curva, fv, en_live in sorted(descartados):
            liv = "—" if en_live is None else ("SÍ cotiza en vivo ◀" if en_live else "no en vivo")
            print(f"    {tk:<8} vence {fv} · «{curva}» · {liv}")
        print("  (el catálogo persistido lo llena `mercado_1816_discovery --apply --catalogo`; "
              "si está viejo, los licitados después tampoco aparecen acá)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
