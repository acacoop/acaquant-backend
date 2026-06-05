Vista `/derivados` — ahora SOLO Opciones (Agro y Sintéticos se promovieron a `/agro` y `/sinteticos`). SSR de la meta de opciones (`/api/cotizaciones/opciones/meta`: tasa, VR local/ADR) en paralelo con `getMe()`; la chain de opciones se polleea client-side al montar. Pasa `isAdmin` al shell para habilitar el editor de tasa.

Conecta con: backend `GET /api/cotizaciones/opciones/meta`, `getMe()`; componente `DerivadosShell`.
