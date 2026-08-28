"""jobs/interbanking_sync.py — trae los extractos de Interbanking a `bancos.*`.

ÚNICO writer del esquema `bancos`. Corre cada 2hs de 9 a 19 ART (ver
deploy/crontab.txt) y en cada corrida re-sincroniza **el día hábil anterior y hoy**.

⚠️ **"Ayer" es HÁBIL, no calendario.** Los bancos no operan sábados, domingos ni
feriados: un día no hábil no tiene extracto y no tiene movimientos. Restar un día
de calendario hace que la ventana apunte a un día vacío y que el último día con
actividad real **nunca se vuelva a pedir**.

Pasó el 2026-08-18 (martes): el lunes 17 fue feriado (Paso a la Inmortalidad del
Gral. San Martín), la ventana pidió 17..18 y la vista mostró cero movimientos. El
"ayer" que correspondía era el **viernes 14**. Lo mismo pasaba TODOS los lunes,
donde la ventana caía en domingo y el viernes quedaba sin re-sincronizar.

Por qué se re-pide el día anterior y no solo hoy: un movimiento puede aparecer o
corregirse después del cierre del banco, y re-pedirlo es barato (una llamada por
cuenta — el rango más ancho NO agrega llamadas, solo páginas si hay más de 100
movimientos). La ingesta es idempotente, así que correrla diez veces deja el
mismo resultado que correrla una.

El rango SÍ incluye los días no hábiles que quedan en el medio (el finde entre el
viernes y el lunes): van en la misma llamada, no cuestan nada y vienen vacíos.

Por qué la vista no le pega a Interbanking en vivo: el límite de **100 llamadas
por minuto es del ABONADO**, no del proceso. Si la pantalla consultara en vivo,
unos pocos usuarios refrescando podrían agotar la cuota y romper este job — y
cualquier otro sistema de ACA que use la misma cuota. El job escribe, la vista
lee de Postgres.

FUENTE ÚNICA: la API de **Extractos**. Devuelve, en la misma respuesta, el día
(apertura, cierre, totales) y su detalle de movimientos. Traer además la API de
Movimientos sería la misma data dos veces (medido: los dos endpoints devolvieron
`total_rows=168` para el mismo rango y cuenta), y traer la de Saldos sería una
segunda verdad para el saldo diario.

Uso:
    python -m jobs.interbanking_sync              # hábil anterior + hoy (lo del cron)
    python -m jobs.interbanking_sync --dias 5     # 5 días HÁBILES hacia atrás
    python -m jobs.interbanking_sync --solo-cuentas
    python -m jobs.interbanking_sync --dry        # no escribe, solo reporta

Costo: 4 llamadas para el maestro + ~1 de extracto por cuenta (más páginas si un
día tuvo más de 100 movimientos) + 1 de saldo por cuenta. Con 38 cuentas son ~90
llamadas por corrida, contra un límite de 100 **por minuto** — el cliente
throttlea a 80/min, así que la corrida se espacia sola y no lo agota.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from api.services import bancos  # puro (sin FastAPI), como en otros jobs
from core import interbanking as ib
from core.calendario import restar_habiles
from core.job_runs import JobRunLogger
from core.postgres import get_job_pool
from core.tz import ahora_ar

logger = logging.getLogger("jobs.interbanking_sync")

LIMIT = 100          # el máximo que acepta la API por página
# Cuántas FECHAS distintas se conservan en `bancos.*`. El resto se borra en cada
# corrida. Es un pedido explícito del back office (2026-08-18): esta vista no es
# un archivo histórico — lo relevante es el último día hábil, y guardar meses de
# extractos y movimientos es acumular por acumular.
FECHAS_A_MANTENER = 3
MAX_PAGINAS = 50     # cortafuegos: 50 × 100 = 5.000 movimientos por cuenta y ventana


# --------------------------------------------------------------------------- #
# Parseo
# --------------------------------------------------------------------------- #
def _fecha(v: Any) -> date | None:
    """'2026-07-30' o '2026-07-30T00:00:00' → date."""
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _ts(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _hash_mov(cuenta_id: int, fecha: date, m: dict) -> str:
    """Identidad del movimiento. NO hay id natural: medido contra producción, el
    campo `id` que declara el YAML de Movimientos v1 no viene ni en v1 ni en v2.

    Se incluyen importe, tipo y código de operación además de (extracto,
    correlativo): si el banco corrige un movimiento preferimos una fila NUEVA
    antes que pisar la vieja en silencio. Duplicar es visible y se detecta
    (`incoherentes` en sync_log); perder un movimiento, no.
    """
    partes = [
        str(cuenta_id), fecha.isoformat(),
        str(m.get("statement_number") or ""),
        str(m.get("correlative_number") or ""),
        str(m.get("amount") or ""),
        str(m.get("debit_credit_type") or ""),
        str(m.get("operation_code_ib") or ""),
        str(m.get("voucher_number") or ""),
        str(m.get("movement_date") or ""),
    ]
    return hashlib.sha256("|".join(partes).encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Maestro de cuentas
# --------------------------------------------------------------------------- #
def sincronizar_cuentas(*, dry: bool = False) -> list[dict]:
    """Trae el universo completo (4 llamadas) y lo upsertea. Devuelve las filas
    de `bancos.cuentas` con su `id`."""
    remotas = ib.todas_las_cuentas()
    logger.info("Interbanking: %d cuentas en el maestro", len(remotas))
    if dry:
        return [{**c, "id": None} for c in remotas]

    hoy = ahora_ar().date()   # mismo "hoy" que la ventana; no dos nociones en un archivo
    filas = []
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        for c in remotas:
            cur.execute(
                # ⚠️ `origen` NO se toca en el UPDATE. Si Interbanking empieza a
                # informar una cuenta que se había cargado a mano, se completa
                # con datos reales pero sigue marcada como manual: esa marca es
                # justo la información que hace falta para decidir qué hacer con
                # sus movimientos manuales. Al revés —blanquearla— la perdería en
                # silencio.
                #
                # ⚠️⚠️ **`account_label` TAMPOCO se toca, y esto ya rompió una vez**
                # (2026-08-20): la etiqueta es el nombre OPERATIVO que le pone el
                # back office («PATA ACDI», «VALO CERA ARS»), y estaba en el
                # `DO UPDATE`, así que **cada corrida del job la pisaba** con la
                # denominación del titular que manda Interbanking —la misma para
                # todas las cuentas de ACA—. El trabajo de nombrar 38 cuentas se
                # perdía en la sincronización siguiente, sin ningún error.
                #
                # Peor: como el usuario usaba esas etiquetas para relacionar cada
                # cuenta con su cuenta del mayor, lo que se borraba no era una
                # decoración sino un MAPEO.
                #
                # Se sigue tomando en el INSERT (una cuenta nueva estrena el
                # nombre del proveedor, mejor que nada) y el valor original queda
                # siempre en `raw`. La regla es la de `origen`: **el
                # descubrimiento automático no puede pisar lo que escribió una
                # persona.**
                """INSERT INTO bancos.cuentas
                     (bank_number, bank_name, account_number, account_type, currency,
                      account_cbu, account_cuit, account_label, primera_vez, ultima_vez,
                      raw, origen, actualizado_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'interbanking', now())
                   ON CONFLICT (bank_number, account_number, account_type, currency)
                   DO UPDATE SET bank_name      = EXCLUDED.bank_name,
                                 account_cbu    = EXCLUDED.account_cbu,
                                 account_cuit   = EXCLUDED.account_cuit,
                                 ultima_vez     = EXCLUDED.ultima_vez,
                                 activa         = true,
                                 raw            = EXCLUDED.raw,
                                 actualizado_at = now()
                   RETURNING id""",
                (str(c.get("bank_number") or "").strip(), (c.get("bank_name") or "").strip(),
                 str(c.get("account_number") or "").strip(),
                 (c.get("account_type") or "CC").strip(),
                 (c.get("currency") or "ARS").strip(),
                 c.get("account_cbu"), c.get("account_cuit"), c.get("account_label"),
                 hoy, hoy, json.dumps(c, ensure_ascii=False)),
            )
            filas.append({**c, "id": cur.fetchone()[0]})
        conn.commit()
    return filas


# --------------------------------------------------------------------------- #
# Extractos
# --------------------------------------------------------------------------- #
def _traer_extracto(c: dict, desde: str, hasta: str) -> tuple[dict, list, int, str | None]:
    """Pagina el extracto de una cuenta y devuelve (dias, movimientos, paginas, control_code).

    Un mismo día puede venir partido entre páginas: sus totales se repiten en
    cada página (se queda el primero) y sus movimientos se acumulan. `total_rows`
    cuenta MOVIMIENTOS, no días — por eso la paginación se corta comparando
    contra la cantidad de movimientos acumulados.
    """
    dias: dict[date, dict] = {}
    movs: list[tuple[date, dict]] = []
    total_rows: int | None = None
    control: str | None = None
    pagina = 0

    while pagina < MAX_PAGINAS:
        r = ib.extractos(
            c["account_number"], c["bank_number"], desde, hasta,
            account_type=c.get("account_type") or "CC",
            currency=c.get("currency") or "ARS",
            limit=LIMIT, page=pagina,
        )
        gd = r.get("general_data") or {}
        control = gd.get("control_code") or control
        if total_rows is None:
            total_rows = int(gd.get("total_rows") or 0)

        statements = r.get("statements") or []
        nuevos = 0
        for d in statements:
            f = _fecha(d.get("operation_date"))
            if not f:
                continue
            dias.setdefault(f, d)
            for m in d.get("movement_detail") or []:
                movs.append((f, m))
                nuevos += 1

        pagina += 1
        if not statements or nuevos == 0 or len(movs) >= (total_rows or 0):
            break

    return dias, movs, pagina, control


def _persistir(cuenta_id: int, dias: dict, movs: list) -> tuple[int, int]:
    """Upsert de los días y sus movimientos. Devuelve (días, movimientos guardados)."""
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        for f, d in dias.items():
            ap, ci = _num(d.get("opening_balance")), _num(d.get("ending_balance"))
            cr, de = _num(d.get("credits_total_amount")), _num(d.get("debits_total_amount"))
            dif = None
            cierra = None
            if None not in (ap, ci, cr, de):
                dif = round((ap or 0) + (cr or 0) - (de or 0) - (ci or 0), 2)
                cierra = abs(dif) < 0.01
            cur.execute(
                """INSERT INTO bancos.extracto_dia
                     (cuenta_id, fecha, saldo_apertura, saldo_cierre, total_creditos,
                      total_debitos, total_movimientos, numero_extracto, cierra,
                      diferencia, sincronizado_at, raw)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), %s::jsonb)
                   ON CONFLICT (cuenta_id, fecha) DO UPDATE SET
                     saldo_apertura    = EXCLUDED.saldo_apertura,
                     saldo_cierre      = EXCLUDED.saldo_cierre,
                     total_creditos    = EXCLUDED.total_creditos,
                     total_debitos     = EXCLUDED.total_debitos,
                     total_movimientos = EXCLUDED.total_movimientos,
                     numero_extracto   = EXCLUDED.numero_extracto,
                     cierra            = EXCLUDED.cierra,
                     diferencia        = EXCLUDED.diferencia,
                     sincronizado_at   = now(),
                     raw               = EXCLUDED.raw""",
                (cuenta_id, f, ap, ci, cr, de, d.get("total_movements"),
                 d.get("statement_number"), cierra, dif,
                 json.dumps({k: v for k, v in d.items() if k != "movement_detail"},
                            ensure_ascii=False)),
            )

        if movs:
            cur.executemany(
                """INSERT INTO bancos.movimientos
                     (mov_hash, cuenta_id, fecha, fecha_movimiento, fecha_valor,
                      fecha_proceso, importe, tipo, descripcion_banco, descripcion_ib,
                      codigo_operacion_ib, codigo_operacion_banco, numero_extracto,
                      correlativo, comprobante, sucursal, cuit_contraparte,
                      denominacion_contraparte, raw, sincronizado_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb, now())
                   ON CONFLICT (mov_hash) DO UPDATE SET
                     raw             = EXCLUDED.raw,
                     sincronizado_at = now()""",
                [(
                    _hash_mov(cuenta_id, f, m), cuenta_id, f,
                    _ts(m.get("movement_date")), _ts(m.get("value_date")),
                    _ts(m.get("process_date")), _num(m.get("amount")),
                    m.get("debit_credit_type"), m.get("code_description_bank"),
                    m.get("code_description_ib"), m.get("operation_code_ib"),
                    m.get("operation_code_bank"), m.get("statement_number"),
                    m.get("correlative_number"), m.get("voucher_number"),
                    m.get("branch_office_activity"), m.get("customer_cuit"),
                    m.get("depositor_description"), json.dumps(m, ensure_ascii=False),
                ) for f, m in movs],
            )
        conn.commit()
    return len(dias), len(movs)


def _incoherencias(cuenta_id: int, fechas: list[date]) -> int:
    """Días donde lo que guardamos no coincide con lo que declara el extracto.

    Es el auto-chequeo del hash: si dos movimientos distintos colapsaran en el
    mismo hash (o si uno se duplicara), la cuenta no da y salta acá en vez de
    quedar escondida en la base.
    """
    if not fechas:
        return 0
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM bancos.extracto_dia e
                WHERE e.cuenta_id = %s AND e.fecha = ANY(%s)
                  AND e.total_movimientos IS DISTINCT FROM
                      (SELECT count(*) FROM bancos.movimientos m
                        WHERE m.cuenta_id = e.cuenta_id AND m.fecha = e.fecha)""",
            (cuenta_id, fechas),
        )
        return cur.fetchone()[0] or 0


# --------------------------------------------------------------------------- #
# Saldos
# --------------------------------------------------------------------------- #
def _persistir_saldos(cuenta_id: int, r: dict) -> int:
    """Guarda lo que el banco informa como SALDO. Devuelve cuántos días escribió.

    Por qué esta API además de Extractos, si el extracto ya trae un saldo de
    cierre: **el extracto solo devuelve los días CON movimientos**. Con la
    ventana corta que usa el back office (último día hábil + hoy), una cuenta
    quieta no tiene ninguna fila y el consolidado la muestra con «—». Saldos
    responde igual, se haya movido o no.

    No pisa al extracto ni se mezcla con él: son dos cosas que el banco informa
    por separado y, si difieren, esa diferencia es un hallazgo de conciliación
    (ver el comentario de `bancos.saldos` en sql/schema.sql).

    La respuesta trae dos bloques distintos y se guardan como tales:
    - `historical_balances[]` → una fila por día (saldo del día + totales).
    - `balances` → la foto de HOY (contable, operativo, inicial, proyectados
      24/48hs). NO es una serie: se estampa solo en la fila del `row_date`, que
      es la fecha que declara la propia respuesta. Ponerlo en todos los días
      inventaría un proyectado de ayer que el banco nunca informó.
    """
    gd = r.get("general_data") or {}
    foto = _fecha(gd.get("row_date"))
    b = r.get("balances") or {}
    historicos = r.get("historical_balances") or []

    filas: dict[date, dict] = {}
    for h in historicos:
        f = _fecha(h.get("operation_date"))
        if f:
            filas.setdefault(f, h)
    # El día de la foto puede no estar en el histórico (una cuenta sin
    # movimientos hoy). Igual tiene que existir: es justamente el caso que esta
    # API vino a cubrir.
    if foto:
        filas.setdefault(foto, {})

    if not filas:
        return 0

    with get_job_pool().connection() as conn, conn.cursor() as cur:
        for f, h in filas.items():
            es_foto = f == foto
            cur.execute(
                """INSERT INTO bancos.saldos
                     (cuenta_id, fecha, saldo_dia, creditos_dia, debitos_dia,
                      saldo_contable, saldo_operativo, saldo_operativo_ini,
                      proyectado_24hs, proyectado_48hs, es_foto, raw, sincronizado_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb, now())
                   ON CONFLICT (cuenta_id, fecha) DO UPDATE SET
                     saldo_dia           = EXCLUDED.saldo_dia,
                     creditos_dia        = EXCLUDED.creditos_dia,
                     debitos_dia         = EXCLUDED.debitos_dia,
                     saldo_contable      = EXCLUDED.saldo_contable,
                     saldo_operativo     = EXCLUDED.saldo_operativo,
                     saldo_operativo_ini = EXCLUDED.saldo_operativo_ini,
                     proyectado_24hs     = EXCLUDED.proyectado_24hs,
                     proyectado_48hs     = EXCLUDED.proyectado_48hs,
                     es_foto             = EXCLUDED.es_foto,
                     raw                 = EXCLUDED.raw,
                     sincronizado_at     = now()""",
                (cuenta_id, f, _num(h.get("day_balance")),
                 _num(h.get("total_credits")), _num(h.get("total_debits")),
                 _num(b.get("countable_balance")) if es_foto else None,
                 _num(b.get("current_operating_balance")) if es_foto else None,
                 _num(b.get("initial_operating_balance")) if es_foto else None,
                 _num(b.get("projected_balance_24hs")) if es_foto else None,
                 _num(b.get("projected_balance_48hs")) if es_foto else None,
                 es_foto,
                 json.dumps({"balances": b if es_foto else {}, "dia": h},
                            ensure_ascii=False)),
            )
        conn.commit()
    return len(filas)


# --------------------------------------------------------------------------- #
# Retención
# --------------------------------------------------------------------------- #
def purgar(mantener: int = FECHAS_A_MANTENER) -> dict[str, int]:
    """Deja solo las `mantener` fechas más recientes en `bancos.*`. Devuelve
    cuántas filas borró de cada tabla.

    Por qué existe: esta vista NO es un archivo histórico. Lo que importa es el
    último día hábil; guardar meses de extractos, movimientos y saldos es
    acumular por acumular (user, 2026-08-18).

    El corte es por **FECHA distinta y global**, no por cuenta y no por
    antigüedad en días: "las últimas 3 fechas que tenemos". Así un fin de semana
    largo o un feriado no vacía la tabla — tres fechas son tres fechas con dato,
    no tres días de calendario.

    ⚠️ Cada tabla calcula SU propio top-3. Podrían no coincidir (una cuenta con
    saldo y sin extracto ese día), y está bien: son tablas independientes y
    forzarlas a compartir el corte borraría datos buenos de una porque a la otra
    le faltaban.

    ⚠️ **No se llama nunca si la corrida no trajo nada** (ver `run`). Si
    Interbanking está caído, la ingesta guarda cero y purgar igual dejaría la
    base con menos días de los que había — un borrado silencioso causado por una
    caída del proveedor, que es exactamente lo que no se puede permitir.

    ⚠️ **Solo se purga lo que el job escribe.** `bancos.movimientos_manuales`
    queda afuera a propósito: los movimientos del banco se vuelven a pedir cuando
    hagan falta, pero un movimiento manual lo tipeó una persona y no se puede
    reconstruir. Sumar esa tabla a la lista de abajo sería una pérdida de datos.
    """
    borradas: dict[str, int] = {}
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        # Movimientos primero: son hijos de un día del extracto. Borrar el padre
        # antes dejaría movimientos de un día que ya no existe.
        for tabla in ("bancos.movimientos", "bancos.extracto_dia", "bancos.saldos"):
            cur.execute(
                f"""DELETE FROM {tabla}
                     WHERE fecha NOT IN (
                           SELECT DISTINCT fecha FROM {tabla}
                            ORDER BY fecha DESC LIMIT %s)""",
                (mantener,),
            )
            borradas[tabla.split(".", 1)[1]] = cur.rowcount or 0
        conn.commit()
    return borradas


def _log_sync(cuenta_id: int | None, desde: str, hasta: str, paginas: int,
              dias: int, movs: int, incoh: int, control: str | None,
              ok: bool, error: str | None) -> None:
    try:
        with get_job_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO bancos.sync_log
                     (cuenta_id, fecha_desde, fecha_hasta, paginas, dias, movimientos,
                      incoherentes, control_code, ok, error)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (cuenta_id, desde, hasta, paginas, dias, movs, incoh, control, ok, error),
            )
            conn.commit()
    except Exception as e:  # el log de sync nunca puede tumbar la corrida
        logger.warning("no pude escribir sync_log: %s", e)


# --------------------------------------------------------------------------- #
# Orquestación
# --------------------------------------------------------------------------- #
def ventana(dias_atras: int = 1, hoy: date | None = None) -> tuple[date, date]:
    """(desde, hasta) de la corrida. `dias_atras` cuenta **días HÁBILES**.

    Es una función aparte y pura para poder testearla sin red ni base: la
    ventana es la decisión del job que más fácil se rompe en silencio (devuelve
    cero movimientos, que es indistinguible de "no hubo movimientos").

    Por qué HÁBILES y no calendario: los bancos no operan sábados, domingos ni
    feriados. Restar un día de calendario apunta a un día que no tiene extracto
    y deja el último día CON actividad sin re-sincronizar — todos los lunes, y
    también los martes post-feriado (2026-08-18: el lunes 17 fue feriado y la
    ventana pidió 17..18, dos días sin nada, cuando el "ayer" real era el
    viernes 14).

    `hasta` es HOY aunque hoy no sea hábil: si alguien corre el job un domingo,
    la ventana igual tiene que llegar hasta la fecha de corrida. Los días no
    hábiles que quedan en el medio viajan en la MISMA llamada y vienen vacíos,
    así que ampliar el rango no cuesta llamadas.
    """
    hasta = hoy or ahora_ar().date()
    desde = restar_habiles(hasta, max(dias_atras, 0))
    # Interbanking admite 60 días CALENDARIO por consulta. El tope se aplica
    # sobre el resultado y no sobre `dias_atras`, que ahora cuenta hábiles:
    # 60 hábiles son ~84 días de calendario y la API rechazaría la llamada.
    return max(desde, hasta - timedelta(days=60)), hasta


def run(*, dias_atras: int = 1, solo_cuentas: bool = False, dry: bool = False) -> dict:
    desde, hasta = ventana(dias_atras)
    d1, d2 = desde.isoformat(), hasta.isoformat()

    stats: dict[str, Any] = {
        "ventana": f"{d1}..{d2}", "cuentas": 0, "cuentas_ok": 0, "cuentas_error": 0,
        "dias": 0, "movimientos": 0, "dias_incoherentes": 0, "llamadas": 0,
        "dias_saldo": 0, "cuentas_sin_saldo": 0,
    }

    cuentas = sincronizar_cuentas(dry=dry)
    stats["cuentas"] = len(cuentas)
    stats["llamadas"] += 4
    if solo_cuentas or dry:
        return stats

    for c in cuentas:
        try:
            d, m, pags, control = _traer_extracto(c, d1, d2)
            stats["llamadas"] += pags
            n_dias, n_movs = _persistir(c["id"], d, m)
            incoh = _incoherencias(c["id"], list(d.keys()))
            stats["dias"] += n_dias
            stats["movimientos"] += n_movs
            stats["dias_incoherentes"] += incoh
            stats["cuentas_ok"] += 1
            _log_sync(c["id"], d1, d2, pags, n_dias, n_movs, incoh, control, True, None)

            # SALDOS: una llamada más por cuenta. Va DESPUÉS del extracto y en su
            # propio try — si el saldo falla no se pierde el extracto, que es el
            # dato principal. Al revés también: una cuenta sin extracto (quieta)
            # igual tiene que quedar con su saldo, que es justo para lo que está.
            try:
                stats["llamadas"] += 1
                n_saldos = _persistir_saldos(c["id"], ib.saldos(
                    c["account_number"], c["bank_number"],
                    account_type=c.get("account_type") or "CC",
                    currency=c.get("currency") or "ARS",
                    date_since=d1, date_until=d2,
                ))
                stats["dias_saldo"] += n_saldos
                if not n_saldos:
                    stats["cuentas_sin_saldo"] += 1
            except Exception as e:
                stats["cuentas_sin_saldo"] += 1
                logger.warning("saldos de la cuenta %s (%s): %s: %s",
                               c["id"], c.get("bank_name"), type(e).__name__, e)
        except Exception as e:
            stats["cuentas_error"] += 1
            msg = f"{type(e).__name__}: {e}"
            logger.warning("cuenta %s (%s): %s", c["id"], c.get("bank_name"), msg)
            _log_sync(c.get("id"), d1, d2, 0, 0, 0, 0, None, False, msg)

    # ⚠️ **SELLAR EL CIERRE, ANTES DE PURGAR.** El saldo al cierre de cada día se
    # guarda como un valor con fecha y banco (`bancos.cierres_diarios`) y es lo
    # que se lee como saldo INICIAL del día siguiente — sin recalcular nada.
    #
    # Va antes de la purga a propósito: la purga borra `extracto_dia` y `saldos`
    # de los días viejos, así que si se sellara después ya no habría de dónde.
    if stats["cuentas_ok"]:
        for d in sorted({d1 + timedelta(days=i) for i in range((d2 - d1).days + 1)}):
            try:
                stats["cierres_sellados"] = (stats.get("cierres_sellados", 0)
                                             + len(bancos.sellar_cierre(d)))
            except Exception as e:
                logger.warning("no pude sellar el cierre de %s: %s: %s",
                               d, type(e).__name__, e)

    # La purga va al FINAL y solo si la corrida trajo datos: si Interbanking
    # estuvo caído no se guardó nada, y borrar igual dejaría la base con menos
    # días de los que tenía por culpa de una caída del proveedor.
    if stats["cuentas_ok"]:
        try:
            for tabla, n in purgar().items():
                stats[f"purgadas_{tabla}"] = n
        except Exception as e:
            logger.warning("no pude purgar: %s: %s", type(e).__name__, e)
    else:
        logger.warning("ninguna cuenta trajo datos — NO se purga")

    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Sincroniza extractos de Interbanking")
    ap.add_argument("--dias", type=int, default=1,
                    help="cuántos días HÁBILES hacia atrás "
                         "(default 1 = día hábil anterior + hoy)")
    ap.add_argument("--solo-cuentas", action="store_true", help="solo el maestro de cuentas")
    ap.add_argument("--dry", action="store_true", help="no escribe nada")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    with JobRunLogger("interbanking_sync") as run_log:
        stats = run(dias_atras=args.dias, solo_cuentas=args.solo_cuentas, dry=args.dry)
        run_log.stats.update(stats)
        for k, v in stats.items():
            print(f"  {k:<20} {v}")
        if stats.get("cuentas_error"):
            run_log.errors.append(f"{stats['cuentas_error']} cuentas fallaron")
        if stats.get("dias_incoherentes"):
            run_log.errors.append(
                f"{stats['dias_incoherentes']} días donde lo guardado no coincide "
                f"con el total que declara el extracto")


if __name__ == "__main__":
    main()
