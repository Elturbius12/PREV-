# -*- coding: utf-8 -*-
"""Fixtures compartidas. Todos los datos son sintéticos: nunca se usan reportes reales."""
import pytest


@pytest.fixture(autouse=True)
def base_de_datos_aislada(tmp_path, monkeypatch):
    """Cada test usa su propio preve.db temporal (nunca la base real de la app)."""
    from database import Database
    import preve_config
    Database.cerrar_todas()
    preve_config.Config.reiniciar()
    monkeypatch.setenv('PREVE_DB_URL', 'sqlite:///' + str(tmp_path / 'preve_test.db').replace('\\', '/'))
    # nunca leer el preve_config.json real de la app
    monkeypatch.setattr(preve_config, 'RUTA_CONFIG_JSON', str(tmp_path / 'no_existe_preve_config.json'))
    yield
    Database.cerrar_todas()
    preve_config.Config.reiniciar()


@pytest.fixture
def log():
    """Logger que acumula mensajes para poder inspeccionarlos en los tests."""
    mensajes = []

    def _log(msg):
        mensajes.append(str(msg))
    _log.mensajes = mensajes
    return _log


@pytest.fixture
def carpeta_salida(tmp_path):
    d = tmp_path / 'salida'
    d.mkdir()
    return str(d)
