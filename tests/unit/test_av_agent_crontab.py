"""EL CRON DEL REPO vs EL QUE CORRE — dos copias sin árbitro (REGLA #9 B).

`deploy/crontab.txt` se declara «fuente de verdad» y TODO el sistema le cree:
`jobs_catalogo` lo parsea, `salud` arma un chequeo por línea, la tab SKILLS saca
de ahí el horario. Pero `deploy.sh` NO lo instala, así que un cron nuevo puede
vivir en el repo y no correr nunca — y **no falla nada**.
"""
from __future__ import annotations

from unittest.mock import patch

from api.services import av_agent_crontab as cr

_LINEA = ("45 19 * * 1-5 /root/TradingAV/deploy/run_job.sh saldos_a_operadores "
          "5m 'cd /root/TradingAV && venv/bin/python -m jobs.saldos_a_operadores'")


# ── el parseo ────────────────────────────────────────────────────────────────

def test_los_comentarios_y_las_variables_no_son_crons():
    """El archivo tiene 400 líneas de comentario y el crontab real trae `MAILTO=`
    y `PATH=`. Compararlos como si fueran jobs daría cientos de diferencias
    falsas y el detector se volvería ruido el primer día."""
    r = cr._lineas("# un comentario\nMAILTO=x@y.com\nPATH=/usr/bin\n\n" + _LINEA)
    assert r == {" ".join(_LINEA.split())}


def test_la_MISMA_orden_escrita_con_otros_espacios_es_LA_MISMA():
    a = cr._lineas("0 11 * * 1-5   systemctl restart control_saldos.service")
    b = cr._lineas("0 11 * * 1-5 systemctl  restart   control_saldos.service")
    assert a == b


def test_pero_OTRO_HORARIO_si_es_una_diferencia():
    """Colapsar espacios no puede tapar un cambio real: 16:45 y 16:30 son cosas
    distintas y el detector tiene que verlas distintas."""
    a = cr._lineas("45 19 * * 1-5 systemctl restart x.service")
    b = cr._lineas("30 19 * * 1-5 systemctl restart x.service")
    assert a != b


def test_el_NOMBRE_del_job_es_lo_que_se_lee():
    """La línea entera no entra en un aviso. Lo accionable es qué job es."""
    assert cr._que_job(_LINEA) == "saldos_a_operadores"
    assert cr._que_job("30 2 * * * systemctl stop motor_rofex.service") \
        == "motor_rofex.service"


# ── el detector ──────────────────────────────────────────────────────────────

def _con(repo: set[str], maquina: set[str] | None):
    return patch.multiple(cr, del_repo=lambda: repo, de_la_maquina=lambda: maquina)


def test_si_coinciden_NO_dice_nada():
    with _con({"a"}, {"a"}):
        assert cr.detectar_crontab() == []


def test_lo_que_esta_en_el_REPO_y_no_en_la_maquina_NO_CORRE():
    """El caso que originó todo: se agregó `saldos_a_operadores` al archivo, el
    catálogo lo muestra, salud arma su chequeo… y en la máquina no está."""
    with _con({_LINEA}, set()):
        h = cr.detectar_crontab()
    assert len(h) == 1 and h[0]["regla"] == "sin_instalar"
    assert h[0]["severidad"] == "alta"
    assert "NO están en la máquina" in h[0]["motivo"]
    assert h[0]["evidencia"]["jobs"] == ["saldos_a_operadores"]
    # El arreglo es UN comando y tiene que estar escrito.
    assert "crontab /root/TradingAV/deploy/crontab.txt" in h[0]["evidencia"]["texto"]


def test_lo_que_CORRE_y_el_repo_no_declara_tambien_se_canta():
    """La otra dirección: algo instalado a mano que nadie revisa, y que la
    próxima instalación del archivo se lleva puesto sin avisar."""
    with _con(set(), {_LINEA}):
        h = cr.detectar_crontab()
    assert len(h) == 1 and h[0]["regla"] == "sin_declarar"
    assert h[0]["severidad"] == "media"


def test_si_NO_SE_PUEDE_LEER_lo_DICE_en_vez_de_callarse():
    """**El silencio se lee igual que un verde** (§0.s). Sin esto, un `crontab`
    que no está en el PATH devolvería «ninguno instalado» y el detector cantaría
    los 40 jobs como caídos — o, peor, se quedaría mudo dando a entender que
    está todo bien."""
    with _con({_LINEA}, None):
        h = cr.detectar_crontab()
    assert len(h) == 1 and h[0]["regla"] == "no_pude_mirar"
    assert "no es que estén" in h[0]["evidencia"]["texto"]


def test_no_pude_mirar_NO_es_ninguno_instalado():
    """La distinción vive en `de_la_maquina`: `None` ≠ `set()`. Es la regla del
    AO29 (§0.v) aplicada acá."""
    import subprocess
    with patch.object(subprocess, "run", side_effect=FileNotFoundError("crontab")):
        assert cr.de_la_maquina() is None


def test_pero_no_hay_crontab_para_root_SI_es_un_dato():
    """`crontab -l` sale 1 con «no crontab for root» y eso NO es un fallo de
    lectura: es que no hay ninguno instalado, que es justo lo que hay que gritar."""
    import subprocess

    class _R:
        returncode, stdout, stderr = 1, "", "no crontab for root"
    with patch.object(subprocess, "run", lambda *a, **k: _R()):
        assert cr.de_la_maquina() == set()


def test_nunca_levanta():
    with patch.object(cr, "del_repo", side_effect=RuntimeError("boom")):
        assert cr.detectar_crontab() == []


# ── el archivo real del repo ─────────────────────────────────────────────────

def test_el_crontab_del_repo_se_parsea_y_tiene_los_jobs_conocidos():
    """Si el parser dejara de reconocer las líneas, el detector compararía dos
    conjuntos vacíos y diría «todo bien» para siempre."""
    r = cr.del_repo()
    assert len(r) > 20, f"solo {len(r)} crons parseados — ¿se rompió el regex?"
    jobs = {cr._que_job(x) for x in r}
    assert {"saldos_a_operadores", "av_agent", "av_agent_live"} <= jobs
