# -*- coding: utf-8 -*-
"""Fixtures compartidas. Todos los datos son sintéticos: nunca se usan reportes reales."""
import pytest


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
