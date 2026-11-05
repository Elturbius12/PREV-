# -*- coding: utf-8 -*-
"""Tests de humo: los módulos del motor se importan sin errores."""
import importlib

import pytest

MODULOS_MOTOR = ['preve_pipeline', 'preve_ciclo', 'preve_validacion', 'preve_historial',
                 'preve_simulacion', 'preve_orquestador', 'preve_extractor', 'preve_datos']


@pytest.mark.parametrize('nombre', MODULOS_MOTOR)
def test_modulo_importa(nombre):
    assert importlib.import_module(nombre) is not None


def test_ciclo_sin_estado_inicia_ciclo_1(carpeta_salida):
    from preve_ciclo import registrar_importacion
    info = registrar_importacion(carpeta_salida)
    assert info['ciclo_numero'] == 1
    assert info['dia_ciclo'] == 1
    assert info['etiqueta'] == 'PRELIMINAR'
