Vista `/valuaciones` — performance e historia por cuenta. Wrapper `force-dynamic` que delega en `ValuacionesShell`, donde vive todo el state (cuenta seleccionada, sub-tab, fetches).

Conecta con: componente `ValuacionesShell` → backend `/api/valuaciones` (service `valuaciones`, XIRR/PnL por cuenta). Vive bajo el layout raíz.
