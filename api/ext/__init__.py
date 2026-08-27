"""api/ext — API EXTERNA para accionistas (`/ext/v1`). Ver docs/API_EXTERNA.md.

Sub-app montada aparte de `/api`, con su propia autenticación, su propio rate
limit y su propio OpenAPI. Está separada A PROPÓSITO: en `/api` un router nuevo
nace ALCANZABLE y hay que acordarse de gatearlo (por eso existe
`GUEST_PATH_PREFIXES`, REGLA #8). Acá el default-deny es topología — un router
nuevo de la mesa es físicamente inalcanzable desde `/ext`, sin listas que
mantener ni tests que recordar.
"""
