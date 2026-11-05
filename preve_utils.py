# -*- coding: utf-8 -*-
"""Utilidades compartidas: logging y conversiones seguras."""
import logging
import math

_CONFIGURADO = False


def get_logger(nombre):
    """Logger del paquete PREVÉ (preve.<nombre>)."""
    return logging.getLogger(f'preve.{nombre}')


def configurar_logging(nivel=logging.INFO):
    """Configura la salida a consola una sola vez (la rotación a archivo llega en la Fase 7)."""
    global _CONFIGURADO
    if _CONFIGURADO:
        return
    logging.basicConfig(level=nivel, format='%(asctime)s %(levelname)-7s %(name)s: %(message)s')
    _CONFIGURADO = True


def safe_int(val, default=0):
    """int() que no revienta con NaN, None, inf o texto no numérico."""
    # FIX: int(NaN) lanzaba ValueError y rompía vistas e informe
    try:
        v = float(val)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return int(v)
