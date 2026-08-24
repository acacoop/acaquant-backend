"""Los detectores. Cada uno mira y devuelve, o levanta `SinDatos`.

**No llevan `try/except` propio ni deciden qué significa no poder mirar.** Eso
lo hace el motor, una sola vez, para todos (§2.4 del doc).
"""
from agente.detectores import datos, mercado, sistema

__all__ = ["datos", "mercado", "sistema"]
