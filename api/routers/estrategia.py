"""Router /api/estrategia — vista TRADING → tab ESTRATEGIA (módulo `trading`,
admin-only, mismo gate que /api/trading).

Señal intradía con trazabilidad (ESTRATEGIA QUANT). Thin wrapper: la lógica
vive en api/services/estrategia.py; el ÚNICO emisor de señales es
engines/estrategia.py. Doc vivo: docs/ESTRATEGIA_QUANT.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from api.services import estrategia as svc

router = APIRouter(prefix="/api/estrategia", tags=["Estrategia"])


@router.get("/live")
def live():
    """Última evaluación del motor por ticker del universo (orden |score| desc).
    [{ticker, ts, score, direccion, cobertura, factores, indice_ref, precio,
      pesos_version, inputs}]. El motor upsertea cada ~60s durante la rueda."""
    return svc.get_live()


@router.get("/track-record")
def track_record(dias: int = Query(90, ge=1, le=365)):
    """Trazabilidad del modelo: por horizonte (n, hit_rate, expectativa,
    mfe/mae promedio, curva de equity) + edge por factor + flag de muestra
    mínima. Solo señales RESUELTAS no-parciales."""
    return svc.get_track_record(dias=dias)


@router.get("/senales")
def senales(dias: int = Query(30, ge=1, le=365), limite: int = Query(200, ge=1, le=1000)):
    """Tabla de señales resueltas (una fila por señal × horizonte, desc por ts)
    para auditar el ledger fila a fila."""
    return svc.get_senales(dias=dias, limite=limite)


@router.get("/contexto")
def contexto():
    """Contexto determinista por ticker foco: ATR-20 (rango típico diario, ARS) +
    Efficiency Ratio intradía (choppy). [{ticker, fecha, close, atr, atr_pct,
    er_dia, er_reciente, choppy}]. El ATR sale del OHLC diario; el ER se deriva
    en vivo del tape (None fuera de la rueda)."""
    return svc.get_contexto()
