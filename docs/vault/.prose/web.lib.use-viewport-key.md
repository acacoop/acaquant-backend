Hook `useViewportKey` — devuelve un contador que incrementa cada vez que cambia algo del entorno visual: resize, cambio de DPR, movimiento entre monitores, vuelta de foco al tab o `ResizeObserver` del `<html>`. Se usa como `key` en el `ResponsiveContainer` de recharts para forzar un remount, porque recharts a veces mide 0 en transiciones y no se recupera solo.

Conecta con: hook de cliente puro, sin red ni datos de negocio; lo consumen los componentes de gráficos (recharts) del frontend.
