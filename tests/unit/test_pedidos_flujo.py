"""Tests del circuito del BUZÓN DE PEDIDOS (triaje IA + aprobación por Telegram).

Lo crítico acá NO es el formateo: es que el server ahora LEE de Telegram. Los
tests que importan son los de la jaula —qué se acepta como orden válida y de
quién— y los de la validación de lo que devuelve el modelo. Si algo de esto
falla, un mensaje de afuera mueve estado adentro.

Nada toca la DB ni la red.
"""
from __future__ import annotations

import pytest

from jobs import pedidos_inbox as inbox
from jobs import pedidos_triage as triage

# ── La jaula: qué cuenta como una orden válida ──────────────────────────────

@pytest.mark.parametrize("dato", [
    "pedido:aceptar:12",
    "pedido:descartar:7",
    "pedido:aceptar:999999",
])
def test_dato_valido_se_interpreta(dato):
    m = inbox._DATO_RE.match(dato)
    assert m and m.group(1) in inbox._ESTADO


@pytest.mark.parametrize("dato", [
    "",                              # vacío
    "pedido:aceptar",                # sin id
    "pedido:aceptar:",               # id vacío
    "pedido:borrar:12",              # acción inexistente
    "pedido:aceptar:12; DROP TABLE manager.pedidos",   # inyección
    "pedido:aceptar:12\npedido:aceptar:13",            # dos órdenes en una
    "PEDIDO:ACEPTAR:12",             # otra caja
    "otra_cosa:aceptar:12",
    "pedido:aceptar:-1",
    "pedido:aceptar:1e9",
    "pedido:aceptar:99999999999999",  # más largo que el techo
])
def test_dato_invalido_se_descarta(dato):
    """Un tap es un VOTO, no un comando: cualquier cosa que no sea exactamente
    `pedido:<aceptar|descartar>:<id>` no se interpreta. Sin esto, el contenido
    que llega de afuera empezaría a decidir qué hace el server."""
    assert inbox._DATO_RE.match(dato) is None


def test_solo_hay_dos_acciones_posibles():
    """El mapa de acciones es cerrado: no se puede llegar a 'hecho' ni a
    ningún otro estado desde Telegram."""
    assert set(inbox._ESTADO) == {"aceptar", "descartar"}
    assert set(inbox._ESTADO.values()) == {"aceptado", "descartado"}


def test_autorizacion_exige_lista_blanca_y_chat(monkeypatch):
    """Default-deny: sin ids en la lista blanca, NADIE aprueba — ni siquiera
    desde el chat correcto."""
    from core import notify

    update = {"update_id": 5, "callback_query": {
        "id": "cb1", "data": "pedido:aceptar:3",
        "from": {"id": 4242}, "message": {"message_id": 9, "chat": {"id": 111}}}}
    monkeypatch.setattr(notify, "_api", lambda *a, **kw: {"ok": True, "result": [update]})

    monkeypatch.setattr(notify, "TELEGRAM_ADMIN_IDS", ())
    monkeypatch.setattr(notify, "TELEGRAM_CHAT_ID", "111")
    taps, _off = notify.leer_taps()
    assert taps[0]["autorizado"] is False           # lista vacía → nadie

    monkeypatch.setattr(notify, "TELEGRAM_ADMIN_IDS", ("4242",))
    taps, _off = notify.leer_taps()
    assert taps[0]["autorizado"] is True

    monkeypatch.setattr(notify, "TELEGRAM_CHAT_ID", "999")   # otro chat
    taps, _off = notify.leer_taps()
    assert taps[0]["autorizado"] is False


def test_el_offset_avanza_para_no_reprocesar(monkeypatch):
    from core import notify

    ups = [{"update_id": 10, "callback_query": {"id": "a", "data": "pedido:aceptar:1",
                                                "from": {"id": 1}, "message": {"chat": {"id": "1"}}}},
           {"update_id": 11, "callback_query": {"id": "b", "data": "pedido:aceptar:2",
                                                "from": {"id": 1}, "message": {"chat": {"id": "1"}}}}]
    monkeypatch.setattr(notify, "_api", lambda *a, **kw: {"ok": True, "result": ups})
    _taps, off = notify.leer_taps()
    assert off == 12                                 # último + 1


def test_sin_respuesta_de_telegram_no_avanza_el_offset(monkeypatch):
    """Si la llamada falla, el offset queda donde estaba: los taps se vuelven a
    entregar en la próxima corrida en vez de perderse."""
    from core import notify

    monkeypatch.setattr(notify, "_api", lambda *a, **kw: None)
    taps, off = notify.leer_taps(offset=7)
    assert taps == [] and off == 7


# ── Lo que devuelve el modelo: se valida, no se cree ────────────────────────

def test_parseo_acepta_json_envuelto_en_backticks():
    d = triage._parsear('```json\n{"impacto": "alto", "esfuerzo": "chico", '
                        '"spec": "x", "duplicado_de": null}\n```')
    assert d["impacto"] == "alto"


def test_parseo_sin_json_devuelve_none():
    """Sin JSON usable el pedido queda SIN triar y se reintenta mañana —
    preferible a inventarle un impacto que después alguien usa para priorizar."""
    assert triage._parsear("no sé qué hacer con esto") is None
    assert triage._parsear("") is None


def test_saneado_encierra_los_enums_y_el_duplicado():
    """La jaula del lado del modelo: propone, el código valida. Un impacto
    inventado cae a 'medio' y un duplicado que apunta a un pedido inexistente
    se descarta (si no, la cola quedaría con referencias colgadas)."""
    out = triage._sanear(
        {"impacto": "CRÍTICO!!", "esfuerzo": "titánico",
         "duplicado_de": 999, "spec": " hacer X "}, ids_validos={1, 2})
    assert out["impacto"] == "medio" and out["esfuerzo"] == "medio"
    assert out["duplicado_de"] is None
    assert out["spec"] == "hacer X"

    ok = triage._sanear({"impacto": "alto", "esfuerzo": "chico",
                         "duplicado_de": "2", "spec": "y"}, ids_validos={1, 2})
    assert ok["duplicado_de"] == 2      # string numérico válido se acepta


def test_duplicado_exacto_no_gasta_tokens():
    previos = [{"id": 4, "texto": "Falta el filtro por CARTERA en operaciones."}]
    p = {"id": 9, "texto": "falta el filtro por cartera en operaciones"}
    assert triage._duplicado_exacto(p, previos) == 4
    assert triage._duplicado_exacto({"id": 9, "texto": "otra cosa"}, previos) is None
    assert triage._duplicado_exacto({"id": 9, "texto": ""}, previos) is None


# ── Persistencia: las dos ramas, sin CASE WHEN sobre un parámetro ──────────

class _CursorFalso:
    def __init__(self):
        self.sql = None
        self.params = None

    def execute(self, sql, params):
        self.sql, self.params = sql, params


def test_persistir_pedido_normal_no_toca_el_estado():
    """Un pedido triado sigue 'nuevo': la decisión de aceptarlo o descartarlo
    es humana. Si acá se tocara el estado, el triaje estaría decidiendo."""
    cur = _CursorFalso()
    triage._persistir(cur, 7, {"impacto": "alto", "esfuerzo": "chico",
                               "spec": "hacer X", "duplicado_de": None})
    assert "estado" not in cur.sql
    assert "triado_at = now()" in cur.sql
    assert cur.params == ("alto", "chico", "hacer X", None, 7)


def test_persistir_duplicado_lo_descarta_apuntando_al_original():
    cur = _CursorFalso()
    triage._persistir(cur, 9, {"impacto": "bajo", "esfuerzo": "chico",
                               "spec": None, "duplicado_de": 4})
    assert "estado = 'descartado'" in cur.sql
    assert "duplicado del #4" in cur.params


def test_persistir_no_usa_case_when_sobre_un_parametro():
    """REGRESIÓN (primera corrida real 2026-07-22): la versión anterior hacía
    `CASE WHEN %s IS NULL` y Postgres no puede inferir el tipo de un parámetro
    suelto dentro de un IS NULL → IndeterminateDatatype. Reventaba DESPUÉS de
    haber gastado la llamada al LLM, que es lo caro. La rama se decide en
    Python."""
    for dup in (None, 4):
        cur = _CursorFalso()
        triage._persistir(cur, 1, {"impacto": "alto", "esfuerzo": "chico",
                                   "spec": "x", "duplicado_de": dup})
        assert "CASE" not in cur.sql.upper()
        assert cur.sql.count("%s") == len(cur.params)


# ── Prioridad: el orden de lectura es el orden de trabajo ───────────────────

def test_la_cola_ordena_por_lo_que_mas_rinde():
    pedidos = [
        {"id": 1, "impacto": "bajo",  "esfuerzo": "chico"},
        {"id": 2, "impacto": "alto",  "esfuerzo": "grande"},
        {"id": 3, "impacto": "alto",  "esfuerzo": "chico"},
        {"id": 4, "impacto": "medio", "esfuerzo": "chico"},
    ]
    assert [p["id"] for p in sorted(pedidos, key=triage._orden)] == [3, 2, 4, 1]


def test_el_export_usa_los_mismos_pesos_que_el_triaje():
    """Dos criterios de prioridad distintos (uno en el aviso de Telegram y otro
    en la cola del repo) harían que lo que ves primero en el celular no sea lo
    primero que se trabaja."""
    from scripts.gen_pedidos import _peso

    p = {"id": 3, "impacto": "alto", "esfuerzo": "chico"}
    assert _peso(p) == triage._orden(p)


def _git_falso(monkeypatch, salidas=None):
    """Captura los comandos git que se ejecutan, sin correr ninguno."""
    import subprocess as sp

    corridas = []
    salidas = salidas or {}

    class _R:
        def __init__(self, out=b""):
            self.returncode = 0
            self.stdout = out
            self.stderr = b""

    def _fake(cmd, **kw):
        corridas.append((cmd, kw))
        clave = cmd[1] if len(cmd) > 1 else ""
        return _R(salidas.get(clave, b"deadbeef"))

    monkeypatch.setattr(sp, "run", _fake)
    return corridas


def test_publicar_NO_escribe_en_disco(monkeypatch, tmp_path):
    """REGRESIÓN: la primera versión escribía docs/PEDIDOS.md en el checkout
    de producción y lo dejaba SUELTO (untracked). Resultado: el `git pull` del
    deploy empezó a abortar con "untracked working tree files would be
    overwritten by merge" — el buzón bloqueaba los deploys."""
    from scripts import gen_pedidos as gp

    _git_falso(monkeypatch, {"rev-parse": b"base123"})
    monkeypatch.setattr(gp, "SALIDA", str(tmp_path / "PEDIDOS.md"))
    gp.publicar("# contenido de prueba")
    assert not (tmp_path / "PEDIDOS.md").exists()


def test_publicar_manda_el_contenido_por_stdin(monkeypatch):
    """El texto va del SQL al objeto de git sin pasar por el filesystem."""
    from scripts import gen_pedidos as gp

    corridas = _git_falso(monkeypatch, {"rev-parse": b"base123"})
    gp.publicar("# hola buzón")
    hash_obj = [c for c in corridas if c[0][1] == "hash-object"]
    assert hash_obj, "no se creó el blob"
    assert "--stdin" in hash_obj[0][0]
    assert hash_obj[0][1]["input"] == "# hola buzón".encode()


def test_publicar_NO_toca_el_checkout_de_produccion(monkeypatch):
    """EL punto del diseño. El Droplet es producción y casi siempre está detrás
    de main. La salida fácil ante el rechazo por fast-forward sería que el job
    haga `git pull` — y eso convertiría un cron en un DEPLOY AUTOMÁTICO: código
    nuevo en producción sin que nadie lo decida. El commit se arma al costado."""
    from scripts import gen_pedidos as gp

    corridas = _git_falso(monkeypatch, {"rev-parse": b"base123"})
    gp.publicar("# buzón")
    verbos = [c[0][1] for c in corridas]

    # jamás nada que mueva el working tree, la rama o el índice real
    for prohibido in ("pull", "merge", "rebase", "checkout", "reset", "add",
                      "commit", "stash"):
        assert prohibido not in verbos, f"publicar() ejecutó `git {prohibido}`"
    # el commit se construye con plumbing sobre origin/main
    assert {"fetch", "read-tree", "commit-tree", "push"} <= set(verbos)


def test_publicar_usa_un_indice_temporal(monkeypatch):
    """read-tree/update-index/write-tree tienen que ir contra un GIT_INDEX_FILE
    propio: contra el índice real dejarían cosas staged en producción."""
    from scripts import gen_pedidos as gp

    corridas = _git_falso(monkeypatch, {"rev-parse": b"base123"})
    gp.publicar("# buzón")
    for cmd, kw in corridas:
        if cmd[1] in ("read-tree", "update-index", "write-tree"):
            env = kw.get("env") or {}
            assert env.get("GIT_INDEX_FILE"), f"{cmd[1]} usó el índice real"


def test_publicar_escribe_un_solo_path(monkeypatch):
    from scripts import gen_pedidos as gp

    corridas = _git_falso(monkeypatch, {"rev-parse": b"base123"})
    gp.publicar("# buzón")
    escrituras = [c[0] for c in corridas if c[0][1] == "update-index"]
    assert len(escrituras) == 1
    assert escrituras[0][-1].endswith("docs/PEDIDOS.md")


def test_publicar_no_revienta_si_no_puede_pushear(monkeypatch):
    """Sin credencial de push en el server no revienta nada: la publicación es
    un extra, y los pedidos ya están guardados en la base."""
    import subprocess as sp

    from scripts import gen_pedidos as gp

    def _boom(cmd, **kw):
        raise sp.CalledProcessError(128, cmd, stderr=b"fatal: could not read Username")

    monkeypatch.setattr(sp, "run", _boom)
    msg = gp.publicar("# buzón")
    assert "NO se pudo publicar" in msg and "could not read Username" in msg


def test_el_mensaje_arma_un_par_de_botones_por_pedido():
    texto, botones = triage._mensaje([
        {"id": 7, "titulo": "Filtro por cartera", "impacto": "alto",
         "esfuerzo": "chico", "vista": "operaciones", "spec": "agregar el filtro"},
    ])
    assert "#7" in texto and "agregar el filtro" in texto
    assert botones == [[("✅ #7", "pedido:aceptar:7"), ("❌ #7", "pedido:descartar:7")]]
    # y lo que viaja en el botón es exactamente lo que la jaula sabe leer
    for fila in botones:
        for _t, dato in fila:
            assert inbox._DATO_RE.match(dato)
