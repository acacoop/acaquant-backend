"""Zona horaria Argentina — ÚNICO lugar para now / tz / cálculo de frescura.

Reemplaza el anti-patrón `datetime.utcnow() - timedelta(hours=3)` regado por el
repo: `utcnow()` devuelve naive (deprecado) y el `-3h` hardcodeado se rompe con
el horario de verano y al mezclar naive/aware. Usar SIEMPRE:

    from core.tz import AR_TZ, ahora_ar, ahora_utc, asegurar_aware, segundos_desde

- `ahora_ar()`  → datetime aware en hora Argentina.
- `ahora_utc()` → datetime aware en UTC.
- `asegurar_aware(dt, assume=UTC)` → si dt es naive, le asume `assume`.
- `segundos_desde(dt, assume=UTC)` → antigüedad en segundos, robusta a tz.

`core/` no importa nada del proyecto (regla de capas), así que esto lo pueden
usar engines/, jobs/ y api/ por igual.
"""
from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def ahora_ar() -> datetime:
    """Now aware en hora Argentina (respeta DST automáticamente)."""
    return datetime.now(AR_TZ)


def ahora_utc() -> datetime:
    """Now aware en UTC."""
    return datetime.now(UTC)


def asegurar_aware(dt: datetime, assume: tzinfo = UTC) -> datetime:
    """Devuelve `dt` aware. Si viene naive, le asume la tz `assume` (default UTC).

    No convierte: solo etiqueta el naive. Para convertir, encadenar `.astimezone()`.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=assume)


def segundos_desde(dt: datetime, assume: tzinfo = UTC,
                   ahora: datetime | None = None) -> float:
    """Antigüedad de `dt` en segundos respecto a `ahora` (default: now UTC).

    Normaliza ambos a UTC antes de restar → inmune a naive/aware y a la tz.
    Si `dt` es naive, se asume `assume`.
    """
    ref = asegurar_aware(ahora) if ahora is not None else ahora_utc()
    return (ref.astimezone(UTC) - asegurar_aware(dt, assume).astimezone(UTC)).total_seconds()
