"""core/custodia_escritura.py — persistir la tenencia de CVSA. UNA sola implementación.

La escritura vive acá y no en el job porque tiene DOS llamadores, y tienen que
comportarse idéntico:

  · `jobs/custodia_cvsa.py`            — cuando el Droplet pueda llegar a BYMA.
  · `POST /api/ingest/custodia/holdings` — la PC con Okta, que HOY es el único
                                           camino (BYMA está detrás de AppGate y
                                           el server no entra).

Si cada uno tuviera su copia, el día que se habilite la IP tendríamos dos
escrituras que pueden divergir sin que falle nada — que es exactamente el modo
de falla de la REGLA #9. Con una sola función, cambiar de camino es cambiar
quién la llama.

Doc: `docs/BYMA_CUSTODIA.md`. Regla de capas: `core/` solo usa `core/` y `config`.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from core.byma_custodia import moneda
from core.custodia_cuentas import partir
from core.postgres import get_pool

logger = logging.getLogger(__name__)


def mapa_codigo_a_unidad() -> dict[str, str]:
    """`assets.codigo_cnv` → `assets.unidad`.

    ⚠️ `codigo_cnv` guarda el código de CVSA (pese al nombre). NO es único: el
    código es por INSTRUMENTO y varios assets lo comparten (AL30 y AL30D son el
    mismo 5921). Con `ORDER BY unidad` la elección es estable corrida a corrida —
    que el mapeo no cambie solo importa más que cuál de los dos gane.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT btrim(codigo_cnv), unidad FROM portafolio.assets "
                    "WHERE codigo_cnv IS NOT NULL AND btrim(codigo_cnv) <> '' "
                    "ORDER BY unidad")
        mapa: dict[str, str] = {}
        for codigo, unidad in cur.fetchall():
            mapa.setdefault(codigo, unidad)
        return mapa


def _cantidad(valor: Any):
    """`holding` viene como string. Una cantidad ilegible es None, no 0.

    Cero y «no sé» son cosas distintas: un 0 se suma y desaparece dentro de un
    total, un None se ve.
    """
    try:
        return float(str(valor).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def guardar(fecha: date, filas: list[dict]) -> dict:
    """Reemplaza la foto de `fecha` con `filas` (las filas CRUDAS de BYMA).

    ⚠️ **GUARDA — una respuesta vacía NO borra la foto.** Si vienen cero filas
    (pasa temprano, antes de que CVSA arme el día), no se escribe nada: queda la
    foto anterior envejeciendo a la vista por `actualizado_at`. Sin esto, el
    primer barrido de la mañana borraría el día entero y la pantalla diría «sin
    tenencia» con total seguridad — que es peor que mostrarla vieja.

    `DELETE` + `INSERT` en UNA transacción, no upsert: con upsert, un papel que
    la cuenta tenía a las 10 y ya no tiene a las 15 quedaría como fila FANTASMA
    —presente acá, ausente en la Caja— sin que nada falle.
    """
    stats: dict = {"fecha": fecha.isoformat(), "filas_origen": len(filas)}

    if not filas:
        stats["escrito"] = 0
        stats["motivo"] = "vinieron 0 filas: no se toca la foto anterior"
        logger.warning("custodia_cvsa %s: 0 filas, no se escribe nada", fecha)
        return stats

    mapa = mapa_codigo_a_unidad()
    stats["assets_con_codigo"] = len(mapa)

    registros = []
    sin_cuenta = 0
    sin_unidad: set[str] = set()
    for f in filas:
        # ⚠️ La cuenta son las DOS mitades. Guardar solo la derecha hacía que
        # `80074/555555555` (garantías) y `74/555555555` (un comitente) fueran la
        # misma fila: una pisaba a la otra sin que nada fallara.
        partido = partir(f.get("accountNumber", ""))
        if partido is None:
            # Un accountNumber con otra forma no se adivina: se cuenta y se mira.
            sin_cuenta += 1
            continue
        part, cta = partido
        cvsa_id = (f.get("cvsaIdentifier") or "").strip()
        unidad = mapa.get(cvsa_id)
        if not unidad:
            sin_unidad.add(cvsa_id)
        registros.append((
            fecha, part, cta, cvsa_id,
            (f.get("subBalanceType") or "").strip() or "SIN_ESTADO",
            _cantidad(f.get("holding")), unidad,
            f.get("accountNumber"), f.get("identAccountComposite"),
        ))

    stats["sin_cuenta_reconocible"] = sin_cuenta
    stats["codigos_sin_asset"] = len(sin_unidad)
    stats["cuentas"] = len({(r[1], r[2]) for r in registros})
    # Cuántas filas trajo cada espacio de numeración. Es LA pregunta que no se
    # podía contestar antes: si `70074`/`80074` no aparecen acá, es que
    # `/holdings` de nuestro participante no los devuelve y hay que pedirlos.
    por_participante: dict[str, int] = {}
    for r in registros:
        por_participante[r[1]] = por_participante.get(r[1], 0) + 1
    stats["por_participante"] = por_participante

    if not registros:
        # Llegaron filas pero ninguna se pudo interpretar. Tampoco se borra:
        # no saber leer la respuesta no es lo mismo que no haya tenencia.
        stats["escrito"] = 0
        stats["motivo"] = (f"{len(filas)} filas y ninguna con accountNumber "
                           "reconocible: no se toca la foto anterior")
        logger.error("custodia_cvsa %s: %s", fecha, stats["motivo"])
        return stats

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM portafolio.custodia_cvsa WHERE fecha = %s", (fecha,))
        cur.executemany(
            "INSERT INTO portafolio.custodia_cvsa "
            "(fecha, participante, id_cuenta, cvsa_id, sub_balance_type, cantidad, "
            " unidad, account_number, ident_composite) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (fecha, participante, id_cuenta, cvsa_id, sub_balance_type) DO UPDATE "
            "SET cantidad = EXCLUDED.cantidad, unidad = EXCLUDED.unidad, "
            "    actualizado_at = now()",
            registros)
        conn.commit()

    stats["escrito"] = len(registros)
    logger.info("custodia_cvsa %s: %d filas, %d cuentas, %d códigos sin asset",
                fecha, len(registros), stats["cuentas"], len(sin_unidad))
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# MOVIMIENTOS — hechos, no foto. Mismo patrón de un solo writer, otra semántica.
# ─────────────────────────────────────────────────────────────────────────────
# Candidatas a PK, de la más angosta a la más ancha. El ingest las mide sobre
# cada lote real para que la clave definitiva salga de un número y no de una
# suposición (REGLA #2). Se angosta cuando una candidata iguale a las filas.
CLAVES_CANDIDATAS: tuple[tuple[str, ...], ...] = (
    ("referencia",),
    ("referencia", "id_cuenta"),
    ("referencia", "participante", "id_cuenta"),
    ("referencia", "participante", "id_cuenta", "cvsa_id"),
    ("referencia", "participante", "id_cuenta", "cvsa_id", "sub_balance_type"),
)


def contar_claves(registros: list[dict]) -> dict[str, int]:
    """Cuántas combinaciones distintas hay para cada PK candidata.

    La primera que iguale a `filas` es la clave real. Hoy la PK de la tabla es la
    más ancha a propósito: de más a menos se puede angostar mirando este número;
    al revés ya perdiste filas y no te enteraste.
    """
    out = {"filas": len(registros)}
    for campos in CLAVES_CANDIDATAS:
        out["+".join(campos)] = len({tuple(r.get(c) for c in campos) for r in registros})
    return out


def _normalizar_movimientos(filas: list[dict], *, fuente: str) -> tuple[list[dict], dict]:
    """Filas crudas de BYMA → registros listos para escribir. Por NOMBRE, nunca
    posicional: `/transactions` trae 9 columnas, `today` 11 y el POST 13.
    """

    mapa = mapa_codigo_a_unidad()
    stats: dict = {"filas_origen": len(filas), "fuente": fuente,
                   "sin_cuenta_reconocible": 0, "sin_fecha": 0}
    sin_unidad: set[str] = set()
    registros: list[dict] = []

    for f in filas:
        partido = partir(f.get("accountNumber", ""))
        if partido is None:
            stats["sin_cuenta_reconocible"] += 1
            continue
        part, cta = partido
        fecha_liq = _fecha_liq(f.get("settlementDate"))
        if fecha_liq is None:
            # Sin fecha de liquidación no hay dónde guardarlo: es parte de la PK.
            stats["sin_fecha"] += 1
            continue
        cvsa_id = (f.get("cvsaIdentifier") or "").strip()
        unidad = mapa.get(cvsa_id)
        if not unidad:
            sin_unidad.add(cvsa_id)
        codigo_moneda = (f.get("currency") or "").strip() or None
        registros.append({
            "fecha_liq": fecha_liq,
            "participante": part,
            "id_cuenta": cta,
            "cvsa_id": cvsa_id,
            "sub_balance_type": (f.get("securitiesSubBalanceType") or "").strip(),
            "referencia": (f.get("instructionReference") or "").strip(),
            # CON SIGNO: el signo ES el dato (de qué lado de la partida está).
            "volumen": _cantidad(f.get("volume")),
            "monto": _cantidad(f.get("amount")),
            "moneda": moneda(codigo_moneda),
            "moneda_codigo": codigo_moneda,
            "unidad": unidad,
            "account_number": f.get("accountNumber"),
            "contraparte": (f.get("counterparty") or "").strip() or None,
            "contraparte_cta": (f.get("counterpartySecuritiesAcc") or "").strip() or None,
            "estado": (f.get("settlementStatus") or "").strip() or None,
            "estado_motivo": (f.get("settlementStatusReason") or "").strip() or None,
            "fuente": fuente,
        })

    stats["codigos_sin_asset"] = len(sin_unidad)
    stats["sin_referencia"] = sum(1 for r in registros if not r["referencia"])
    return registros, stats


def _fecha_liq(valor: Any) -> date | None:
    """`settlementDate` viene como string y no siempre en el mismo formato.

    Se aceptan los dos que BYMA usó (`YYYY-MM-DD` e `YYYYMMDD`) y nada más: una
    fecha mal interpretada escribe el movimiento en el día equivocado, y eso no
    falla — solo queda mal.
    """
    if isinstance(valor, date):
        return valor
    s = str(valor or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def guardar_movimientos(filas: list[dict], *, fuente: str) -> dict:
    """UPSERT de movimientos. **Nunca borra nada.**

    Al revés que `guardar()`: acá no hay foto que reemplazar. Un movimiento que
    no vino en esta corrida no dejó de existir — `today` solo trae hoy, y
    `byreference` trae una referencia. Borrar por ausencia perdería historia que
    CVSA purga a los 7 días y no se puede reconstruir.

    Lo que un método no trae NO PISA lo que otro ya escribió (`COALESCE`): el
    POST agrega `estado` sin borrar la `contraparte` que trajo `today`.
    """
    registros, stats = _normalizar_movimientos(filas, fuente=fuente)
    stats["claves"] = contar_claves(registros)

    if not registros:
        stats["escrito"] = 0
        stats["motivo"] = (f"{len(filas)} filas y ninguna interpretable"
                           if filas else "vinieron 0 filas")
        logger.warning("custodia_movimientos (%s): %s", fuente, stats["motivo"])
        return stats

    campos = ("fecha_liq", "participante", "id_cuenta", "cvsa_id",
              "sub_balance_type", "referencia",
              "volumen", "monto", "moneda", "moneda_codigo", "unidad",
              "account_number", "contraparte", "contraparte_cta",
              "estado", "estado_motivo", "fuente")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO portafolio.custodia_movimientos ({', '.join(campos)}) "
            f"VALUES ({', '.join(['%s'] * len(campos))}) "
            "ON CONFLICT (fecha_liq, referencia, participante, id_cuenta, cvsa_id, "
            "             sub_balance_type) "
            "DO UPDATE SET "
            "  volumen        = EXCLUDED.volumen, "
            "  monto          = EXCLUDED.monto, "
            "  moneda         = COALESCE(EXCLUDED.moneda, custodia_movimientos.moneda), "
            "  moneda_codigo  = COALESCE(EXCLUDED.moneda_codigo, custodia_movimientos.moneda_codigo), "
            "  unidad         = COALESCE(EXCLUDED.unidad, custodia_movimientos.unidad), "
            "  contraparte    = COALESCE(EXCLUDED.contraparte, custodia_movimientos.contraparte), "
            "  contraparte_cta= COALESCE(EXCLUDED.contraparte_cta, custodia_movimientos.contraparte_cta), "
            "  estado         = COALESCE(EXCLUDED.estado, custodia_movimientos.estado), "
            "  estado_motivo  = COALESCE(EXCLUDED.estado_motivo, custodia_movimientos.estado_motivo), "
            "  fuente         = EXCLUDED.fuente, "
            "  actualizado_at = now()",
            [tuple(r[c] for c in campos) for r in registros])
        conn.commit()

    stats["escrito"] = len(registros)
    stats["referencias"] = len({r["referencia"] for r in registros})
    logger.info("custodia_movimientos (%s): %d filas, %d referencias, %d sin asset",
                fuente, len(registros), stats["referencias"], stats["codigos_sin_asset"])
    return stats
