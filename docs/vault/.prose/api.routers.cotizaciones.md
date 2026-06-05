Router `/api/cotizaciones`: thin wrappers sobre la capa de servicio para cotizaciones de mercado, organizados por dominio — macro (series BCRA BADLAR/CER/DOLAR + dólar MEP), repo/caución, derivados (futuros DLR, forwards, breakevens), renta fija, opciones, fair value, REM y el panel `argy` con returns calculados.

Conecta con: delega en services `macro`, `repo`, `derivados`, `renta_fija`, `opciones`, `fair_value`, `rem`, `argy`; aplica RBAC vía `api.auth.require_module`; lo monta `api.main`.
