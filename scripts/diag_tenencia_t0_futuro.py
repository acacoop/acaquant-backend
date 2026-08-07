"""scripts/diag_tenencia_t0_futuro.py — ¿Aunesa nos puede dar tenencia a HOY y a FUTURO?

READ-ONLY. No escribe una sola fila: hace GETs a Aunesa y SELECTs a Postgres.

CONTEXTO
--------
El writer de tenencias (`jobs/portafolio_backfill.py --diario`, 11:00 UTC L-V)
snapshotea el día hábil ANTERIOR. Para el control en T0 y para la posición real
(la que incluye lo que liquida mañana) hacen falta dos cosas que hoy no tenemos,
y antes de diseñar nada hay que MEDIR si Aunesa ya las da.

QUÉ RESPONDE (las dos preguntas, en orden)
------------------------------------------
1) T0 — ¿pidiendo `desde = próximo hábil` sale la posición de HOY, con los
   boletos de hoy ya adentro? (El endpoint está corrido un día: `desde=X`
   devuelve la posición al día hábil anterior a X.)

2) FUTURO — ¿pidiendo `desde` cada vez más adelante, Aunesa PROYECTA las
   liquidaciones pendientes, o devuelve siempre lo mismo?
     · Si las cantidades CAMBIAN al avanzar la escalera → Aunesa proyecta y la
       posición futura sale gratis: mismo endpoint, otro parámetro.
     · Si NO cambian → hay que construirla nosotros (tenencia + boletos
       pendientes por fecha de liquidación).

3) BONUS — el parser de producción se queda SOLO con las filas
   `informacion == "Acumulado"` y descarta el resto. Este diag lista qué otros
   valores vienen: si ahí hay un desglose por plazo/liquidación, la respuesta
   ya estaba en la respuesta y no hace falta ninguna escalera.

USO (en el Droplet)
-------------------
    python -m scripts.diag_tenencia_t0_futuro --auto     # elige una cuenta que operó hoy
    python -m scripts.diag_tenencia_t0_futuro --cuenta 1839 --pasos 4
    python -m scripts.diag_tenencia_t0_futuro --json   # payload crudo de 1 corrida
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from itertools import pairwise

from core.calendario import es_habil, proximo_habil
from core.postgres import get_pool
from jobs.aum import _SESSION, LISTADO_URL, POSICION_URL, autenticar

CUENTA_DEFAULT = "1839"
PASOS_DEFAULT = 4          # escalones de la escalera después de T0
TIMEOUT = 120
# Mismos params que el writer de producción (`portafolio_backfill._PARAMS_BASE`):
# si el diag preguntara distinto, mediría otra cosa.
_PARAMS_BASE = {"hasta": "", "tipoCuenta": "Comitentes y propias",
                "nivel": "Especie x cuenta", "ocultarCerradas": "true"}


def _aunesa(d: date) -> str:
    """Fecha como la quiere Aunesa: dd/mm/yyyy. Con ISO devuelve 400 ("Error en
    formato de fechas"). Es lo que usa el writer (`_prox_habil(D).strftime`)."""
    return d.strftime("%d/%m/%Y")


def _pedir(cuenta: str, desde: date, headers: dict,
           intentos: int = 3) -> tuple[int, list | None, float, str]:
    """GET crudo → (status, data, segundos, nota).

    NO se usa `jobs.aum.consultar_posicion` a propósito: esa colapsa 204/401/404/
    500 en un único `None` y el diag necesita justamente distinguirlos. El 204 no
    es un error — es "cuenta sin posición" (así lo trata producción).

    REINTENTA igual que el writer (3 intentos con backoff): sin esto, un 500
    intermitente de Aunesa se lee como "el endpoint no sirve" cuando producción
    lo absorbe todos los días sin enterarse.
    """
    t0 = time.time()
    ultimo: tuple[int, list | None, float, str] = (-1, None, 0.0, "sin intentos")
    for i in range(1, intentos + 1):
        try:
            resp = _SESSION.get(POSICION_URL.format(cuenta),
                                params={"desde": _aunesa(desde), **_PARAMS_BASE},
                                headers=headers, timeout=TIMEOUT)
        except Exception as e:
            ultimo = (-1, None, time.time() - t0, f"{type(e).__name__}: {e}")
            if i < intentos:
                time.sleep(2 ** i)
                continue
            return ultimo
        seg = time.time() - t0
        if resp.status_code == 204:
            return 204, [], seg, f"sin posición (intento {i})"
        if resp.status_code == 200:
            try:
                return 200, resp.json(), seg, (f"OK en intento {i}" if i > 1 else "")
            except Exception as e:
                return 200, None, seg, f"body no-JSON: {type(e).__name__}"
        cuerpo = (resp.text or "")[:130].replace("\n", " ")
        ultimo = (resp.status_code, None, seg, f"[{i}/{intentos}] {cuerpo}")
        if resp.status_code in (401, 400, 404) or i == intentos:
            return ultimo      # no tiene sentido reintentar estos
        time.sleep(2 ** i)
    return ultimo


def _ficha_aunesa(cuenta: str, headers: dict) -> tuple[dict | None, int]:
    """La fila de `listadoCuentas` de esa cuenta + el total de cuentas.

    Clave para leer un 500: el writer diario SOLO consulta cuentas con
    `tipo` ∈ (Comitente, Propia) y `estado == 'Activa'` (ver
    `jobs/aum.obtener_cuentas`). Si la cuenta no está o no cumple, producción
    NUNCA la pide — y que Aunesa reviente con ella no dice nada del endpoint.
    """
    try:
        resp = _SESSION.get(LISTADO_URL, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        filas = resp.json()
    except Exception as e:
        print(f"  ⚠ no se pudo traer listadoCuentas: {type(e).__name__}: {e}")
        return None, 0
    if not isinstance(filas, list):
        return None, 0
    rec = next((r for r in filas if isinstance(r, dict) and str(r.get("id")) == str(cuenta)), None)
    return rec, len(filas)


def _candidatas(ayer: date, limite: int = 8) -> list[tuple[str, int, int]]:
    """Cuentas que operaron desde `ayer` Y tienen tenencia en el último snapshot.

    Son las únicas donde el test es CONCLUYENTE: si la cuenta no operó, T0 tiene
    que dar igual que el cierre de ayer y no se prueba nada. Query scopeada (los
    boletos del día son pocos), no un scan de la tabla entera.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id_cuenta, count(*) AS n FROM operaciones "
            "WHERE concertacion >= %s AND anulado_en IS NULL AND id_cuenta IS NOT NULL "
            "GROUP BY id_cuenta ORDER BY n DESC LIMIT 40", (ayer,))
        ops = {r[0]: int(r[1]) for r in cur.fetchall()}
        if not ops:
            return []
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia")
        ult = cur.fetchone()[0]
        if ult is None:
            return []
        cur.execute("SELECT id_cuenta, count(*) FROM portafolio.tenencia "
                    "WHERE fecha = %s AND id_cuenta = ANY(%s) GROUP BY id_cuenta",
                    (ult, list(ops)))
        ten = {r[0]: int(r[1]) for r in cur.fetchall()}
    out = [(idc, ops[idc], ten.get(idc, 0)) for idc in ops if ten.get(idc)]
    out.sort(key=lambda x: (-x[1], -x[2]))
    return out[:limite]


def _opt(flag: str, default: str) -> str:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def _hoy() -> date:
    return datetime.now().date()


def _habil_anterior(d: date) -> date:
    while not es_habil(d):
        d = d - timedelta(days=1)
    return d


def _acumulado(data) -> dict[str, dict]:
    """{unidad: {cantidad, precio, tipo}} de las filas 'Acumulado' — MISMO criterio
    que `jobs/portafolio_backfill._parse`, para comparar peras con peras."""
    out: dict[str, dict] = defaultdict(lambda: {"cantidad": 0.0, "precio": 0.0, "tipo": ""})
    for r in data or []:
        if not isinstance(r, dict) or r.get("informacion") != "Acumulado":
            continue
        unidad = r.get("unidad")
        if not unidad:
            continue
        try:
            cant = float(r.get("cantidad") or 0) * -1
        except (TypeError, ValueError):
            cant = 0.0
        try:
            prec = float(r.get("precio") or 0)
        except (TypeError, ValueError):
            prec = 0.0
        g = out[unidad]
        g["cantidad"] += cant
        g["precio"] = max(g["precio"], prec)
        g["tipo"] = r.get("tipoTitulo") or g["tipo"]
    return {u: g for u, g in out.items() if g["cantidad"] != 0}


def _fmt(x: float) -> str:
    return f"{x:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def main() -> None:
    cuenta = _opt("--cuenta", CUENTA_DEFAULT)
    pasos = int(_opt("--pasos", str(PASOS_DEFAULT)))
    hoy = _hoy()

    print("=" * 78)
    print(f"DIAG TENENCIA T0 / FUTURO — cuenta {cuenta}")
    print(f"Hoy: {hoy.isoformat()} ({'HÁBIL' if es_habil(hoy) else 'NO hábil'})")
    print("=" * 78)

    headers = autenticar()
    ayer = _habil_anterior(hoy - timedelta(days=1))

    # ── BLOQUE 0 · elegir una cuenta donde el test SIRVA ─────────────────────
    # Si la cuenta no operó, T0 tiene que dar igual que el cierre de ayer por
    # definición y no se prueba nada. Con --auto se toma la que más operó hoy.
    cands = _candidatas(ayer)
    print("\n── BLOQUE 0 · cuentas donde el test es concluyente ──────────────────")
    if cands:
        print(f"  {'cuenta':<10} {'boletos desde ' + ayer.isoformat():<26} especies en tenencia")
        for idc, n_ops, n_ten in cands:
            marca = " ←" if idc == cuenta else ""
            print(f"  {idc:<10} {n_ops:<26} {n_ten}{marca}")
    else:
        print("  (ninguna cuenta operó desde " + ayer.isoformat() + ")")
    if "--auto" in sys.argv and cands:
        cuenta = cands[0][0]
        print(f"  --auto → se usa la cuenta {cuenta}")
    else:
        # Cuántos boletos tiene LA CUENTA PEDIDA (no alcanza con mirar el top:
        # puede haber operado poco y no aparecer arriba).
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM operaciones WHERE id_cuenta = %s "
                        "AND concertacion >= %s AND anulado_en IS NULL", (cuenta, ayer))
            n_prop = int(cur.fetchone()[0])
        print(f"\n  cuenta pedida {cuenta}: {n_prop} boletos desde {ayer.isoformat()}")
        if n_prop == 0:
            print("  ⚠ no operó → el test de T0 NO puede probar nada con esta cuenta")
            print("    (sin movimientos, T0 tiene que dar igual al cierre de ayer).")
            print("    El test de FUTURO sí sirve igual si tiene liquidaciones pendientes.")

    # ── BLOQUE 0.5 · ¿producción consulta esta cuenta? ───────────────────────
    # Un 500 en una cuenta que el writer NUNCA pide no dice nada del endpoint.
    print("\n── BLOQUE 0.5 · la cuenta según Aunesa (listadoCuentas) ─────────────")
    rec, total = _ficha_aunesa(cuenta, headers)
    apta = False
    if rec is None:
        print(f"  ✗ la cuenta {cuenta} NO figura en listadoCuentas ({total} cuentas)")
        print("    → el 500 es esperable: le estamos pidiendo una cuenta inexistente")
    else:
        tipo, estado = rec.get("tipo"), rec.get("estado")
        apta = tipo in ("Comitente", "Propia") and estado == "Activa"
        print(f"  denominación: {rec.get('denominacion')}")
        print(f"  tipo={tipo!r}  estado={estado!r}  →  "
              f"{'la consulta el cron diario' if apta else 'el cron diario la SALTEA'}")
        print(f"  fila cruda: {json.dumps(rec, ensure_ascii=False, default=str)[:300]}")
        if not apta:
            print("    ⚠ producción solo pide tipo ∈ (Comitente, Propia) y estado='Activa'")
            print("      → un error acá NO prueba nada sobre el endpoint")

    # ── Escalera de `desde`. Regla del endpoint: desde=X → posición al hábil
    #    anterior a X. Así que para la posición AL día D se pide desde=D+1 hábil.
    escalera: list[tuple[str, date, date]] = [
        ("cierre de ayer (lo que guarda el cron)", hoy, ayer),
        ("T0 — HOY", proximo_habil(hoy), hoy),
    ]
    d_pos = hoy
    d_desde = proximo_habil(hoy)
    for i in range(1, pasos + 1):
        d_pos = proximo_habil(d_pos)
        d_desde = proximo_habil(d_desde)
        escalera.append((f"futuro +{i} hábil", d_desde, d_pos))

    print(f"\n── BLOQUE 1 · qué devuelve cada `desde` (cuenta {cuenta}) ───────────")
    print(f"{'etiqueta':<40} {'desde':<12} {'pos.esperada':<13} {'HTTP':>5} "
          f"{'filas':>6} {'acum':>6} {'esp':>5} {'seg':>6}  nota")
    resultados: list[tuple[str, date, dict]] = []
    crudos: dict[str, list] = {}
    for etiqueta, desde, pos in escalera:
        status, data, seg, nota = _pedir(cuenta, desde, headers)
        if status == 401:                     # token vencido: re-auth y un reintento
            headers = autenticar()
            status, data, seg, nota = _pedir(cuenta, desde, headers)
            nota = (nota + " (tras re-auth)").strip()
        if data is None:
            print(f"{etiqueta:<40} {_aunesa(desde):<12} {pos.isoformat():<13} "
                  f"{status:>5} {'—':>6} {'—':>6} {'—':>5} {seg:>6.1f}  {nota}")
            continue
        acum = _acumulado(data)
        n_acum = sum(1 for r in data if isinstance(r, dict) and r.get("informacion") == "Acumulado")
        print(f"{etiqueta:<40} {_aunesa(desde):<12} {pos.isoformat():<13} "
              f"{status:>5} {len(data):>6} {n_acum:>6} {len(acum):>5} {seg:>6.1f}  {nota}")
        resultados.append((etiqueta, pos, acum))
        crudos[etiqueta] = data

    if len(resultados) < 2:
        print("\n⚠ No se pudo comparar. Qué significa cada código:")
        print("   204 → la cuenta NO tiene posición (no es un error).")
        print("   400 → parámetro mal formado; la nota trae el detalle de Aunesa.")
        print("   401 → token rechazado incluso tras re-auth (credenciales/permisos).")
        print("   404 → ese id de cuenta no existe en Aunesa.")
        print("   500 → Aunesa reventó. Ver el CONTROL de acá abajo para saber si")
        print("         es la cuenta o es el endpoint.")
        print("   -1  → no hubo respuesta (red/timeout); la nota dice la excepción.")

        # ── CONTROL · misma llamada contra una cuenta que el cron SÍ procesa.
        #    Es lo que separa "el endpoint no sirve" de "esta cuenta es especial".
        if cands:
            ctrl = cands[0][0]
            print(f"\n── CONTROL · misma request contra la cuenta {ctrl} ──────────────")
            st, dt, sg, nt = _pedir(ctrl, hoy, headers)
            print(f"  desde={_aunesa(hoy)}  HTTP {st}  {sg:.1f}s  "
                  f"filas={len(dt) if dt is not None else '—'}  {nt}")
            if st == 200:
                print(f"  → el endpoint ANDA. El problema es la cuenta {cuenta}.")
                print(f"    Re-corré con: --cuenta {ctrl}")
            else:
                print("  → también falla en una cuenta sana: el problema es la request,")
                print("    no la cuenta. Pasame este output.")
        return

    # ── BLOQUE 2: ¿cambian las cantidades al avanzar? ES LA PREGUNTA DEL DISEÑO.
    print("\n── BLOQUE 2 · cantidad por especie en cada escalón ──────────────────")
    especies = sorted({u for _e, _p, a in resultados for u in a})
    cabecera = "".join(f"{e.split('—')[0].strip()[:14]:>16}" for e, _p, _a in resultados)
    print(f"{'ESPECIE':<38}{cabecera}")
    for u in especies:
        fila = "".join(f"{_fmt(a.get(u, {}).get('cantidad', 0.0)):>16}" for _e, _p, a in resultados)
        print(f"{u[:38]:<38}{fila}")

    print("\n── BLOQUE 3 · diferencias entre escalones ───────────────────────────")
    for (e1, _p1, a1), (e2, _p2, a2) in pairwise(resultados):
        difs = []
        for u in sorted(set(a1) | set(a2)):
            c1 = a1.get(u, {}).get("cantidad", 0.0)
            c2 = a2.get(u, {}).get("cantidad", 0.0)
            if abs(c2 - c1) > 1e-6:
                difs.append((u, c1, c2))
        if not difs:
            print(f"  «{e1}» → «{e2}»: IDÉNTICOS (0 diferencias)")
        else:
            print(f"  «{e1}» → «{e2}»: {len(difs)} especies cambian")
            for u, c1, c2 in difs[:15]:
                print(f"      {u[:44]:<44} {_fmt(c1):>16} → {_fmt(c2):>16}")

    # ── BLOQUE 4: lo que el parser de producción TIRA A LA BASURA.
    print("\n── BLOQUE 4 · campos que `_parse` descarta (posible desglose) ───────")
    muestra = crudos.get("T0 — HOY") or next(iter(crudos.values()))
    infos = Counter(str(r.get("informacion")) for r in muestra if isinstance(r, dict))
    estados = Counter(str(r.get("estado")) for r in muestra if isinstance(r, dict))
    print(f"  informacion: {dict(infos)}")
    print(f"  estado:      {dict(estados)}")
    claves = sorted({k for r in muestra if isinstance(r, dict) for k in r})
    print(f"  claves de fila ({len(claves)}): {claves}")
    otros = [r for r in muestra if isinstance(r, dict) and r.get("informacion") != "Acumulado"]
    if otros:
        print(f"\n  ── {len(otros)} filas NO-'Acumulado'. Primeras 5 crudas:")
        for r in otros[:5]:
            print(f"      {json.dumps(r, ensure_ascii=False, default=str)[:400]}")
    else:
        print("  (no hay filas fuera de 'Acumulado')")

    # ── BLOQUE 5: boletos de HOY de la cuenta. Si T0 los refleja, tienen que
    #    aparecer como diferencia entre «cierre de ayer» y «T0».
    print("\n── BLOQUE 5 · boletos de HOY de la cuenta (operaciones.operaciones) ─")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT boleto, concertacion, tipo_operacion, instrumento, cantidad, bruto, "
            "moneda, condiciones FROM operaciones "
            "WHERE id_cuenta = %s AND concertacion >= %s AND anulado_en IS NULL "
            "ORDER BY concertacion DESC, boleto DESC LIMIT 30",
            (cuenta, ayer.isoformat()))
        filas = cur.fetchall()
    if not filas:
        print(f"  sin boletos desde {ayer.isoformat()} — la cuenta no operó, T0 debería")
        print("  dar IGUAL que el cierre de ayer (probá con otra cuenta que sí opere)")
    for f in filas:
        conc, bol, tipo, instr, cond = f[1], f[0], f[2] or "", f[3] or "", f[7] or ""
        print(f"  {conc!s:<12} {bol!s:<14} {str(tipo)[:26]:<26} "
              f"{str(instr)[:22]:<22} {cond!s:<10}")

    # ── BLOQUE 6: qué hay guardado hoy en SQL, para contrastar.
    print("\n── BLOQUE 6 · último snapshot en portafolio.tenencia ────────────────")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE id_cuenta = %s", (cuenta,))
        ult = cur.fetchone()[0]
        print(f"  última fecha guardada: {ult}")
        if ult:
            cur.execute("SELECT count(*), sum(valuacion) FROM portafolio.tenencia "
                        "WHERE id_cuenta = %s AND fecha = %s", (cuenta, ult))
            n, val = cur.fetchone()
            print(f"  {n} especies · valuación {_fmt(float(val or 0))}")

    # ── VEREDICTO ────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("VEREDICTO")
    print("=" * 78)
    t0_row = next((r for r in resultados if r[0] == "T0 — HOY"), None)
    ayer_row = next((r for r in resultados if r[0].startswith("cierre de ayer")), None)
    futuros = [r for r in resultados if r[0].startswith("futuro")]

    if t0_row and ayer_row:
        cambia = any(
            abs(t0_row[2].get(u, {}).get("cantidad", 0.0)
                - ayer_row[2].get(u, {}).get("cantidad", 0.0)) > 1e-6
            for u in set(t0_row[2]) | set(ayer_row[2]))
        print(f"1) T0: pedir desde={proximo_habil(hoy).isoformat()} "
              f"{'SÍ' if cambia else 'NO'} cambia contra el cierre de ayer.")
        if not cambia and filas:
            print("   ⚠ La cuenta operó hoy pero la posición no se movió → o Aunesa")
            print("     todavía no impactó el boleto, o T0 no se puede leer así.")
        if not cambia and not filas:
            print("   (no concluyente: la cuenta no operó, no había nada que cambiar)")

    if t0_row and futuros:
        proyecta = any(
            abs(f[2].get(u, {}).get("cantidad", 0.0)
                - t0_row[2].get(u, {}).get("cantidad", 0.0)) > 1e-6
            for f in futuros for u in set(f[2]) | set(t0_row[2]))
        print(f"2) FUTURO: {'SÍ' if proyecta else 'NO'} proyecta liquidaciones.")
        print("   → " + ("sale gratis con el mismo endpoint (otro `desde`)."
                         if proyecta else
                         "hay que construirla: tenencia + boletos por fecha de liquidación."))

    if "--json" in sys.argv and crudos:
        print("\n── PAYLOAD CRUDO (T0, primeras 20 filas) ────────────────────────")
        print(json.dumps((crudos.get("T0 — HOY") or [])[:20], ensure_ascii=False,
                         indent=2, default=str))


if __name__ == "__main__":
    main()
