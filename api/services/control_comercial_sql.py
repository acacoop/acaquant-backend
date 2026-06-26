"""api/services/control_comercial_sql.py — vista CONTROL COMERCIAL (jefatura).

3 bloques (ver docs/img_1.png):
  1. Datos totales ALyC — por períodos FIJOS (Día/Semana/Mes/YTD/12M/2025/2024/Total),
     NO depende del filtro Desde/Hasta. Clientes activos + volumen + comisiones + % vs período
     anterior inmediato equivalente.
  2. Datos por operador — depende de Desde/Hasta. Por comercial: clientes activos/inactivos +
     volumen + comisiones + % vs el rango anterior de igual largo.
  3. Objetivos comerciales — Volumen/Comisiones Actual vs Objetivo (cargado por el jefe) +
     % alcanzado. Objetivos guardados por (operador, año, mes) en SQL, editables in-view.

Activo = la cuenta operó ≥1 vez en el período (negocio_movimientos). Inactivo = comitente
Activa del comercial que NO operó en el período. Volumen = Σ pesificado de negocio_movimientos
(cats de volumen). Comisiones = Σ arancel de operaciones (etapa <> 'solicitud').

SQL-native (no toca Mongo). Reusa helpers de comercial_sql (_PESIF, _CATS_VOLUMEN, _factor_usd).
"""
from __future__ import annotations

from datetime import date

from core.postgres import get_pool
from psycopg.rows import dict_row


# ── Tabla de objetivos (self-create, igual patrón que valuaciones.consolidado) ──
_DDL = """
CREATE SCHEMA IF NOT EXISTS clientes;
CREATE TABLE IF NOT EXISTS clientes.objetivos_comerciales (
    operador_email      text NOT NULL,
    anio                int  NOT NULL,
    mes                 int  NOT NULL CHECK (mes BETWEEN 1 AND 12),
    volumen_objetivo    numeric,
    comisiones_objetivo numeric,
    actualizado_por     text,
    actualizado_at      timestamptz DEFAULT now(),
    PRIMARY KEY (operador_email, anio, mes)
);
"""


def _ensure() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_DDL)
        conn.commit()


def listar_objetivos(*, anio: int, mes_desde: int = 1, mes_hasta: int = 12) -> dict:
    """Objetivos cargados en [anio, mes_desde..mes_hasta] (para el editor + Tabla 3).
    Devuelve filas crudas; el frontend agrega por operador según el período elegido."""
    _ensure()
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT operador_email, anio, mes, volumen_objetivo, comisiones_objetivo, "
            "actualizado_por, actualizado_at FROM clientes.objetivos_comerciales "
            "WHERE anio = %s AND mes BETWEEN %s AND %s ORDER BY operador_email, mes",
            (anio, mes_desde, mes_hasta))
        rows = cur.fetchall()
    for r in rows:
        if r.get("actualizado_at") is not None:
            r["actualizado_at"] = str(r["actualizado_at"])[:19]
        r["volumen_objetivo"] = float(r["volumen_objetivo"]) if r["volumen_objetivo"] is not None else None
        r["comisiones_objetivo"] = float(r["comisiones_objetivo"]) if r["comisiones_objetivo"] is not None else None
    return {"anio": anio, "objetivos": rows}


def set_objetivo(*, operador_email: str, anio: int, mes: int,
                 volumen_objetivo: float | None, comisiones_objetivo: float | None,
                 actor: str = "") -> dict:
    """Upsert del objetivo de un comercial para un (año, mes). Lo edita el jefe in-view."""
    _ensure()
    if not (1 <= int(mes) <= 12):
        return {"ok": False, "error": f"mes inválido: {mes!r}"}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO clientes.objetivos_comerciales "
            "(operador_email, anio, mes, volumen_objetivo, comisiones_objetivo, actualizado_por, actualizado_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (operador_email, anio, mes) DO UPDATE SET "
            "volumen_objetivo = EXCLUDED.volumen_objetivo, "
            "comisiones_objetivo = EXCLUDED.comisiones_objetivo, "
            "actualizado_por = EXCLUDED.actualizado_por, actualizado_at = now()",
            (operador_email, int(anio), int(mes),
             volumen_objetivo, comisiones_objetivo, actor))
        conn.commit()
    return {"ok": True, "operador_email": operador_email, "anio": anio, "mes": mes}


def _hoy_art() -> date:
    """Hoy en ART (UTC-3) sin depender de tz del server. Igual criterio que comercial_sql."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=3)).date()
