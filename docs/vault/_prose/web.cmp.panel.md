Componente contenedor genérico de toda la UI: una "tarjeta" con header (título, contador, subtítulo, acciones) y cuerpo. Soporta expandir a pantalla completa vía portal (botón maximizar + cierre con Escape) y modo fill para ocupar la celda del grid. Es el envoltorio visual estándar de prácticamente todos los paneles del tablero.

Conecta con: puramente presentacional, sin llamadas a la API. Lo reexporta ui.tsx y lo usan casi todas las vistas (renta-fija-live, scanner-view, watchlist, etc.).
