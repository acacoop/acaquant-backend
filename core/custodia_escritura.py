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
from datetime import date
from typing import Any

from core.byma_custodia import id_cuenta
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
        cta = id_cuenta(f.get("accountNumber", ""))
        if not cta:
            # Un accountNumber con otra forma no se adivina: se cuenta y se mira.
            sin_cuenta += 1
            continue
        cvsa_id = (f.get("cvsaIdentifier") or "").strip()
        unidad = mapa.get(cvsa_id)
        if not unidad:
            sin_unidad.add(cvsa_id)
        registros.append((
            fecha, cta, cvsa_id,
            (f.get("subBalanceType") or "").strip() or "SIN_ESTADO",
            _cantidad(f.get("holding")), unidad,
            f.get("accountNumber"), f.get("identAccountComposite"),
        ))

    stats["sin_cuenta_reconocible"] = sin_cuenta
    stats["codigos_sin_asset"] = len(sin_unidad)
    stats["cuentas"] = len({r[1] for r in registros})

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
            "(fecha, id_cuenta, cvsa_id, sub_balance_type, cantidad, unidad, "
            " account_number, ident_composite) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (fecha, id_cuenta, cvsa_id, sub_balance_type) DO UPDATE "
            "SET cantidad = EXCLUDED.cantidad, unidad = EXCLUDED.unidad, "
            "    actualizado_at = now()",
            registros)
        conn.commit()

    stats["escrito"] = len(registros)
    logger.info("custodia_cvsa %s: %d filas, %d cuentas, %d códigos sin asset",
                fecha, len(registros), stats["cuentas"], len(sin_unidad))
    return stats
