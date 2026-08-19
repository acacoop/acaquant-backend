"""api/services/av_agent_db.py — EL AGENTE ENTIENDE LA BASE DE DATOS.

Doc madre: **`docs/AV_AGENT.md`** §0.q.

Pedido del user (2026-08-19):

    *«Necesito que el AV AGENT entienda de la base de datos: qué tablas hay,
    cuánto pesa cada una (al menos una vez por día), y sepa distinguir al día
    siguiente si se agregó algo nuevo, cuáles aumentaron su tamaño y por cuánto,
    y en general cuánto pesa. Esto me parece fundamental para tener control de la
    base. Tendrá que persistir para tener contexto, pero a su vez no crecer todo
    el tiempo: con que tenga registro de hoy y ayer constantemente alcanza.»*

LO QUE HACE ÚTIL A ESTO (y no es el tamaño)
============================================

`pg_total_relation_size` ya dice cuánto pesa cada tabla, y mirarlo una vez no
sirve de nada: son doscientos números sin escala. **La información es el DELTA**:

  · una tabla **NUEVA** → alguien creó algo, o un job está escribiendo donde no
    debería. Es lo que uno se entera último y explica lo demás;
  · una tabla que **CRECIÓ de golpe** → un job en loop, un backfill que se fue de
    mano, o una tabla sin purga. Eso es lo que se come el plan;
  · una tabla que **DESAPARECIÓ** → alguien dropeó algo.

Por eso hay foto diaria y no una consulta en vivo: sin el de ayer no hay
comparación, y sin comparación esto es otro tablero que nadie abre.

LOS UMBRALES SE AUTO-CALIBRAN
==============================

⚠️ No tengo acceso a prod, así que **cualquier umbral en MB que ponga a mano es
una adivinanza** (REGLA #2). Entonces no se pone: el corte absoluto se calcula
como una fracción del tamaño REAL de la base, con un piso para que una base chica
no dispare por cualquier cosa. Así el aviso significa lo mismo con 500 MB que con
50 GB, y nadie tiene que recalibrar nada cuando la base crezca.
"""
from __future__ import annotations

import logging
from datetime import date

from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Cuántas fechas se guardan. DOS: hoy y ayer, que es lo que pidió el user y lo
# único que hace falta para el delta diario.
FECHAS_QUE_SE_GUARDAN = 2

# Para que un crecimiento sea NOTICIA tiene que pasar los DOS filtros. Uno solo
# no alcanza: el relativo dispara con tablas de 8 KB (crecer 50% son 4 KB) y el
# absoluto solo, con tablas grandes que crecen lo normal todos los días.
CRECIO_PCT = 0.25                 # +25% sobre su propio tamaño de ayer
CRECIO_FRACCION_BASE = 0.005      # …y al menos 0,5% del tamaño total de la base
CRECIO_PISO_BYTES = 10 * 1024**2  # …con un piso de 10 MB para bases chicas


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def mb(b) -> str:
    """Bytes en algo que se lee. Un número de 11 dígitos no informa nada."""
    try:
        f = float(b)
    except (TypeError, ValueError):
        return "—"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(f) < 1024 or u == "TB":
            return f"{f:,.1f} {u}".replace(",", "X").replace(".", ",").replace("X", ".")
        f /= 1024
    return "—"


def sacar_foto(*, hoy: date | None = None) -> dict:
    """Congela el tamaño de TODAS las tablas + el total de la base, y **purga en
    el mismo INSERT** todo lo que no sean las últimas `FECHAS_QUE_SE_GUARDAN`.

    La purga va acá y no en una limpieza aparte a propósito: una tabla que vigila
    el tamaño de la base y crece sin techo es un chiste que se cuenta solo, y
    depender de que alguien se acuerde de correr algo es cómo empieza.

    Re-sacarla el mismo día PISA la del día (PK por fecha), así correrla dos
    veces no duplica ni rompe el delta.
    """
    f = hoy or date.today()
    # La MISMA query que usa el panel de Manager → BASE. No se reescribe: dos
    # formas de medir lo mismo terminan dando dos tamaños distintos.
    filas = _q("""
        SELECT n.nspname AS schema, c.relname AS tabla,
               pg_total_relation_size(c.oid)::bigint AS bytes,
               s.n_live_tup AS filas
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
        WHERE c.relkind = 'r'
          AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
    """)
    total = int(_q("SELECT pg_database_size(current_database()) AS b")[0]["b"])

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO manager.db_tamano (fecha, schema, tabla, bytes, filas) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (fecha, schema, tabla) DO UPDATE SET "
            "  bytes = EXCLUDED.bytes, filas = EXCLUDED.filas",
            [(f, r["schema"], r["tabla"], r["bytes"], r["filas"]) for r in filas])
        cur.execute(
            "INSERT INTO manager.db_tamano_dia (fecha, bytes_total, n_tablas) "
            "VALUES (%s, %s, %s) ON CONFLICT (fecha) DO UPDATE SET "
            "  bytes_total = EXCLUDED.bytes_total, n_tablas = EXCLUDED.n_tablas",
            (f, total, len(filas)))
        # LA PURGA, en la misma transacción que la escritura.
        cur.execute(
            "DELETE FROM manager.db_tamano WHERE fecha NOT IN "
            "(SELECT fecha FROM manager.db_tamano_dia ORDER BY fecha DESC LIMIT %s)",
            (FECHAS_QUE_SE_GUARDAN,))
        cur.execute(
            "DELETE FROM manager.db_tamano_dia WHERE fecha NOT IN "
            "(SELECT fecha FROM manager.db_tamano_dia ORDER BY fecha DESC LIMIT %s)",
            (FECHAS_QUE_SE_GUARDAN,))
    return {"fecha": str(f), "tablas": len(filas), "bytes_total": total}


def comparar() -> dict:
    """Hoy contra la foto anterior. Es TODO lo que este módulo tiene para decir.

    Devuelve las tres cosas que importan —lo nuevo, lo que creció, lo que
    desapareció— más el total. Si hay una sola foto lo dice explícito en vez de
    inventar un delta contra cero: **una tabla "nueva" porque es la primera
    corrida no es una tabla nueva**, y arrancar con 200 falsos positivos es la
    forma más rápida de que nadie vuelva a mirar esto.
    """
    dias = _q("SELECT fecha, bytes_total, n_tablas FROM manager.db_tamano_dia "
              "ORDER BY fecha DESC LIMIT 2")
    if not dias:
        return {"ok": False, "motivo": "todavía no saqué ninguna foto de la base"}
    hoy = dias[0]
    if len(dias) < 2:
        return {"ok": True, "primera": True, "fecha": str(hoy["fecha"]),
                "bytes_total": int(hoy["bytes_total"]),
                "n_tablas": int(hoy["n_tablas"]),
                "motivo": "es la primera foto: mañana puedo decirte qué cambió"}
    ayer = dias[1]

    filas = _q("SELECT fecha, schema, tabla, bytes, filas FROM manager.db_tamano "
               "WHERE fecha = ANY(%s)", ([hoy["fecha"], ayer["fecha"]],))
    h = {(r["schema"], r["tabla"]): r for r in filas if r["fecha"] == hoy["fecha"]}
    a = {(r["schema"], r["tabla"]): r for r in filas if r["fecha"] == ayer["fecha"]}

    total_hoy = int(hoy["bytes_total"])
    corte = max(CRECIO_PISO_BYTES, int(total_hoy * CRECIO_FRACCION_BASE))

    nuevas, crecieron, desaparecidas = [], [], []
    for k, r in h.items():
        prev = a.get(k)
        nombre = f"{k[0]}.{k[1]}"
        if prev is None:
            nuevas.append({"tabla": nombre, "bytes": int(r["bytes"]),
                           "filas": int(r["filas"] or 0)})
            continue
        delta = int(r["bytes"]) - int(prev["bytes"])
        base = int(prev["bytes"]) or 1
        if delta >= corte and delta / base >= CRECIO_PCT:
            crecieron.append({
                "tabla": nombre, "bytes": int(r["bytes"]), "delta": delta,
                "pct": round(100 * delta / base, 1),
                "filas": int(r["filas"] or 0),
                "filas_delta": int(r["filas"] or 0) - int(prev["filas"] or 0)})
    for k in a.keys() - h.keys():
        desaparecidas.append({"tabla": f"{k[0]}.{k[1]}",
                              "bytes": int(a[k]["bytes"])})

    crecieron.sort(key=lambda x: -x["delta"])
    nuevas.sort(key=lambda x: -x["bytes"])
    return {
        "ok": True, "primera": False,
        "fecha": str(hoy["fecha"]), "fecha_previa": str(ayer["fecha"]),
        "bytes_total": total_hoy,
        "delta_total": total_hoy - int(ayer["bytes_total"]),
        "n_tablas": int(hoy["n_tablas"]),
        "corte_bytes": corte,
        "nuevas": nuevas, "crecieron": crecieron,
        "desaparecidas": desaparecidas,
    }


def detectar_db() -> list[dict]:
    """El detector. **No reporta tamaños: reporta CAMBIOS.**

    Que una tabla pese 4 GB no es un hallazgo —puede ser exactamente lo que tiene
    que pesar— y llenar la pantalla con los 200 tamaños del día es el ruido que
    hace que nadie mire. Lo que se canta es lo que cambió respecto de ayer.
    """
    try:
        c = comparar()
    except Exception as e:
        logger.warning("av_agent_db: no pude comparar: %s", e)
        return []
    if not c.get("ok") or c.get("primera"):
        return []

    # ⚠️ `evidencia` es un **dict**, no un string: la columna es `jsonb` y el
    # resto del agente la lee como objeto. Guardar un string ahí no explota —
    # `json.dumps("x")` es JSON válido— pero la pantalla no encuentra ninguna
    # clave y el hallazgo queda mudo. El texto va adentro, con los números al
    # lado para que la evidencia se pueda auditar sin volver a consultar.
    out: list[dict] = []
    for t in c["nuevas"]:
        out.append(_h("tabla_nueva", t["tabla"], "media",
                      f"tabla NUEVA desde ayer · {mb(t['bytes'])}",
                      {"texto": f"no existía en la foto del {c['fecha_previa']} y "
                                f"hoy pesa {mb(t['bytes'])}. Si no la creaste "
                                f"vos, algo la está escribiendo.",
                       "bytes": t["bytes"], "filas": t["filas"]}))
    for t in c["crecieron"]:
        out.append(_h("crecio", t["tabla"],
                      "alta" if t["pct"] >= 100 else "media",
                      f"creció {mb(t['delta'])} (+{t['pct']}%) en un día",
                      {"texto": f"pasó de {mb(t['bytes'] - t['delta'])} a "
                                f"{mb(t['bytes'])}. El corte para avisar es "
                                f"{mb(c['corte_bytes'])}, calculado sobre el "
                                f"tamaño real de la base.",
                       "bytes": t["bytes"], "delta": t["delta"],
                       "pct": t["pct"], "filas_delta": t["filas_delta"],
                       "corte_bytes": c["corte_bytes"]}))
    for t in c["desaparecidas"]:
        out.append(_h("desaparecio", t["tabla"], "alta",
                      f"la tabla ya NO está · ayer pesaba {mb(t['bytes'])}",
                      {"texto": f"estaba en la foto del {c['fecha_previa']} y hoy "
                                f"no. Si fue a propósito, ignoralo; si no, "
                                f"alguien dropeó algo.",
                       "bytes_ayer": t["bytes"]}))
    return out


def _h(regla: str, tabla: str, severidad: str, motivo: str,
       evidencia: dict) -> dict:
    """La MISMA forma que cualquier hallazgo del agente (`av_agent._hallazgo`),
    para que la tabla, la pantalla y el diagnóstico lean una sola estructura."""
    return {"tipo": "db_cambio", "ticker": tabla, "regla": regla,
            "severidad": severidad, "motivo": motivo, "evidencia": evidencia}
