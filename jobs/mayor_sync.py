"""jobs/mayor_sync.py — trae el MAYOR contable de Aunesa a `bancos.mayor_movimientos`.

El otro lado de la conciliación. `jobs/interbanking_sync` trae lo que dice el
BANCO; esto trae lo que dice CONTABILIDAD, para poder compararlos sin que nadie
exporte un .xlsx de HYGIRUS a mano.

Fuente: `GET contabilidad/registrosContables` (Aunesa). Devuelve el día entero de
la contabilidad —~1.850 asientos, ~23.600 movimientos, 9,6 MB— del que acá se
guarda **solo lo bancario**: los movimientos cuya cuenta contable está mapeada en
`bancos.cuentas.codigo_contable`. El resto (comitentes, IVA, aranceles,
regularizadoras) se descarta.

QUÉ DÍA
    El MISMO que muestra la vista de INTERBANKING: el día hábil anterior
    (`restar_habiles(hoy_ar, 1)`), con `fechaDesde == fechaHasta`. Si el job
    apuntara a otro día, la vista nunca encontraría el mayor de lo que muestra.
    Hay un test que fija que coincide con `api.services.bancos.fecha_default()`.

    ⚠️ El rango de la API filtra por **fecha de CONCILIACIÓN**, no por fecha de
    alta (medido 2026-08-20): pidiendo el 19/08 vuelven asientos dados de alta el
    20/08. Usar el alta correría todo un día.

POR QUÉ REEMPLAZA EL DÍA ENTERO Y NO HACE UPSERT
    El día NO está cerrado: el 19/08 pasó de 1.840 a 1.847 asientos entre dos
    consultas del día siguiente. Siguen cargando asientos, y también ANULANDO. Con
    un upsert, un asiento anulado durante la rueda quedaría de fantasma y el saldo
    del mayor saldría inflado sin que nada lo delate. Por eso: `DELETE` del día +
    `INSERT` de lo que vino, en una transacción.

    ⚠️ **Primero se trae, después se borra.** Si la API falla, el día que ya
    estaba guardado queda intacto. Borrar antes de tener el reemplazo es cómo se
    pierde un día entero por un timeout — y los timeouts acá son habituales
    (medido: entre 75 s y 306 s por request).

TRES GUARDAS
    1. **No se vacía un día solo.** Si había movimientos guardados y la respuesta
       trae cero, el job NO aplica y avisa. Puede ser legítimo (anularon todo)
       pero también puede ser la API contestando mal, y las dos se ven igual desde
       acá. Se destraba con `--forzar`.
    2. **Cuentas sin mapear.** Un movimiento bancario de una cuenta sin
       `codigo_contable` se descarta, y esa plata desaparecería del lado del mayor
       apareciendo como diferencia de conciliación. Se cuentan y se nombran.
    3. **`movimiento_id` duplicado.** Es la PK. Si la API repitiera uno, el insert
       moriría a mitad; se deduplica y se reporta.

Uso:
    python -m jobs.mayor_sync                      # el día hábil anterior (cron)
    python -m jobs.mayor_sync --fecha 19/08/2026
    python -m jobs.mayor_sync --dry                # no escribe, solo reporta
    python -m jobs.mayor_sync --forzar             # aplica aunque el día quede vacío
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

from core import aunesa
from core.calendario import restar_habiles
from core.job_runs import JobRunLogger
from core.postgres import get_job_pool
from core.tz import ahora_ar

logger = logging.getLogger(__name__)

ENDPOINT = "contabilidad/registrosContables"

# Los ~9,6 MB tardan entre 75 s y 306 s (medido). El default de `aunesa.get` (180)
# se quedaba corto y disparaba un reintento que duplicaba la espera.
TIMEOUT_S = 360

# Para AVISAR de cuentas bancarias sin mapear. No decide qué se guarda —eso lo
# decide `codigo_contable`—, solo de qué vale la pena avisar: sin este filtro el
# aviso listaría las ~80 cuentas contables que no son bancos y sería ruido.
PISTAS_BANCO = ("banco", "bco", "bank", "cvu")


def fecha_objetivo() -> date:
    """El día hábil anterior en hora ARGENTINA.

    Misma expresión que `api.services.bancos.fecha_default()`, que es la fecha que
    muestra la vista. No se importa de ahí para no cruzar `jobs/` con
    `api/services/`; un test verifica que las dos den lo mismo.
    """
    return restar_habiles(ahora_ar().date(), 1)


def _mapeo() -> dict[str, int]:
    """`codigo_contable` → `bancos.cuentas.id`. Es lo único que define qué se
    guarda: sin mapeo no se guarda nada de esa cuenta."""
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT codigo_contable, id FROM bancos.cuentas
                        WHERE codigo_contable IS NOT NULL AND activa""")
        return {r[0]: r[1] for r in cur.fetchall()}


def _num(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip()
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _fecha_iso(v) -> date | None:
    s = str(v or "").strip()[:10]
    if not s:
        return None
    try:
        d, m, a = s.split("/")
        return date(int(a), int(m), int(d))
    except (ValueError, TypeError):
        return None


def _extraer(registros: list, mapeo: dict[str, int], dia: date) -> dict:
    """Aplana asientos→movimientos y se queda con los bancarios mapeados."""
    filas: dict[str, tuple] = {}
    duplicados = 0
    sin_mapear: dict[str, str] = {}
    vistos = 0

    for asiento in registros:
        if not isinstance(asiento, dict):
            continue
        # La fecha de conciliación de la fila manda sobre la pedida: si la API
        # devolviera algo fuera de rango, quedaría guardado con SU fecha y no
        # disfrazado del día que pedimos.
        f_conc = _fecha_iso(asiento.get("fechaConciliacion")) or dia
        f_alta = _fecha_iso(asiento.get("fechaAlta"))
        # El `[Op. NNNN] Concepto - …` que agrupa `_grupo_mayor` en el consolidado.
        concepto = str(asiento.get("referencia") or "").strip()

        for mov in (asiento.get("movimientos") or []):
            if not isinstance(mov, dict):
                continue
            vistos += 1
            codigo = str(mov.get("codigoCuenta") or "").strip()
            cuenta_id = mapeo.get(codigo)
            if cuenta_id is None:
                nombre = str(mov.get("nombreCuenta") or "")
                if any(p in nombre.casefold() for p in PISTAS_BANCO):
                    sin_mapear[codigo] = nombre
                continue

            mid = str(mov.get("movimientoID") or "").strip()
            if not mid:
                continue
            if mid in filas:
                duplicados += 1
                continue
            filas[mid] = (
                mid, str(asiento.get("asientoID") or ""), str(asiento.get("numero") or ""),
                f_conc, f_alta, codigo, cuenta_id,
                str(mov.get("codigoUnidad") or ""),
                _num(mov.get("valuacion")) or 0.0,
                concepto or str(mov.get("referencia") or "").strip(),
                str(mov.get("comprobante") or ""), str(mov.get("numeroOperacion") or ""),
                str(mov.get("referencia") or ""),
            )
    return {"filas": list(filas.values()), "duplicados": duplicados,
            "sin_mapear": sin_mapear, "movimientos_api": vistos}


def _guardados(dia: date) -> int:
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM bancos.mayor_movimientos "
                    "WHERE fecha_conciliacion = %s", (dia,))
        return cur.fetchone()[0]


def _reemplazar(dia: date, filas: list[tuple]) -> int:
    """DELETE + INSERT del día, en UNA transacción."""
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM bancos.mayor_movimientos WHERE fecha_conciliacion = %s",
                    (dia,))
        if filas:
            cur.executemany(
                """INSERT INTO bancos.mayor_movimientos
                     (movimiento_id, asiento_id, asiento_numero, fecha_conciliacion,
                      fecha_alta, codigo_cuenta, cuenta_id, moneda, importe, concepto,
                      comprobante, numero_operacion, referencia_mov, actualizado_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())""", filas)
        conn.commit()
    return len(filas)


def _anotar(dia: date, stats: dict, ok: bool, error: str | None) -> None:
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO bancos.mayor_sync_log
                 (fecha_conciliacion, asientos_api, movimientos_api, movimientos_banco,
                  cuentas_sin_mapear, segundos, ok, error)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (dia, stats.get("asientos_api"), stats.get("movimientos_api"),
             stats.get("movimientos_banco"), stats.get("cuentas_sin_mapear"),
             stats.get("segundos"), ok, error))
        conn.commit()


def run(fecha: date | None = None, *, dry: bool = False, forzar: bool = False) -> dict:
    import time

    dia = fecha or fecha_objetivo()
    f = dia.strftime("%d/%m/%Y")
    stats: dict = {"fecha": dia.isoformat()}

    mapeo = _mapeo()
    stats["cuentas_mapeadas"] = len(mapeo)
    if not mapeo:
        # Sin mapeo el job guardaría CERO movimientos y el día quedaría vacío,
        # que es indistinguible de "no hubo movimientos". Mejor no correr.
        raise RuntimeError(
            "ninguna cuenta tiene `codigo_contable` cargado en bancos.cuentas: "
            "sin mapeo no hay nada que guardar (ver docs de la conciliación).")

    t0 = time.monotonic()
    logger.info("mayor_sync %s — pidiendo a Aunesa (puede tardar varios minutos)", f)
    resp = aunesa.get(ENDPOINT,
                      {"fechaDesde": f, "fechaHasta": f, "inclNC": "true"},
                      timeout=TIMEOUT_S)
    stats["segundos"] = round(time.monotonic() - t0, 1)

    if resp.status_code == 204:
        registros = []
    elif resp.status_code != 200:
        raise RuntimeError(f"Aunesa devolvió HTTP {resp.status_code}: {resp.text[:200]}")
    else:
        data = resp.json()
        registros = data.get("registros") if isinstance(data, dict) else data
        registros = [r for r in registros or [] if isinstance(r, dict)]

    stats["asientos_api"] = len(registros)
    ext = _extraer(registros, mapeo, dia)
    stats["movimientos_api"] = ext["movimientos_api"]
    stats["movimientos_banco"] = len(ext["filas"])
    stats["duplicados"] = ext["duplicados"]
    stats["cuentas_sin_mapear"] = len(ext["sin_mapear"])
    stats["sin_mapear"] = ext["sin_mapear"]

    previos = _guardados(dia)
    stats["guardados_antes"] = previos
    # Guarda: un día que tenía movimientos y ahora vendría vacío puede ser una
    # anulación real o una respuesta rota, y desde acá se ven igual. Ante la duda
    # se conserva lo que ya estaba: recuperar un día borrado es mucho más caro.
    if previos and not ext["filas"] and not forzar:
        stats["aplicado"] = False
        stats["motivo"] = (f"el día tenía {previos} movimientos y la respuesta trae 0; "
                           "no se aplica sin --forzar")
        return stats

    if dry:
        stats["aplicado"] = False
        stats["motivo"] = "dry-run"
        return stats

    _reemplazar(dia, ext["filas"])
    stats["aplicado"] = True
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Trae el mayor contable de Aunesa")
    ap.add_argument("--fecha", default=None,
                    help="dd/mm/yyyy (default: el día hábil anterior, el de la vista)")
    ap.add_argument("--dry", action="store_true", help="no escribe nada")
    ap.add_argument("--forzar", action="store_true",
                    help="aplicar aunque el día quede vacío")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    dia = _fecha_iso(args.fecha) if args.fecha else None
    if args.fecha and dia is None:
        ap.error(f"--fecha {args.fecha!r} no es dd/mm/yyyy")

    with JobRunLogger("mayor_sync") as run_log:
        try:
            stats = run(dia, dry=args.dry, forzar=args.forzar)
        except Exception as e:
            _anotar(dia or fecha_objetivo(), {}, ok=False, error=str(e)[:500])
            raise

        sin_mapear = stats.pop("sin_mapear", {})
        run_log.stats.update({k: v for k, v in stats.items() if not isinstance(v, dict)})
        for k, v in stats.items():
            print(f"  {k:<20} {v}")

        if sin_mapear:
            print(f"\n  ⚠️  {len(sin_mapear)} cuenta(s) que parecen bancarias SIN "
                  "`codigo_contable` — sus movimientos NO se guardaron:")
            for cod, nombre in sorted(sin_mapear.items()):
                print(f"       {cod}  {nombre}")
            print("     Mapearlas: UPDATE bancos.cuentas SET codigo_contable = '<cod>' "
                  "WHERE id = <id>;")
            run_log.errors.append(
                f"{len(sin_mapear)} cuentas bancarias sin mapear: "
                f"{', '.join(sorted(sin_mapear))}")

        if stats.get("duplicados"):
            run_log.errors.append(f"{stats['duplicados']} movimientoID duplicados")
        if not stats.get("aplicado") and not args.dry:
            run_log.errors.append(stats.get("motivo") or "no se aplicó")

        _anotar(_fecha_iso(args.fecha) or fecha_objetivo(), stats,
                ok=bool(stats.get("aplicado")), error=stats.get("motivo"))


if __name__ == "__main__":
    main()
