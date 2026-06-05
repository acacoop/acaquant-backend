Componente invisible que fuerza un `router.refresh()` de Next.js cada N milisegundos (default 5s) para revalidar datos de páginas server-rendered sin recargar toda la página. Se monta en vistas que muestran datos live y dependen de SSR.

Conecta con: usa el router de Next; no hace fetch propio — solo dispara la revalidación de las server components que lo contienen.
