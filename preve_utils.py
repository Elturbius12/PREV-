# -*- coding: utf-8 -*-
"""Utilidades compartidas: logging y conversiones seguras."""
import logging
import math

_CONFIGURADO = False


def get_logger(nombre):
    """Logger del paquete PREVÉ (preve.<nombre>)."""
    return logging.getLogger(f'preve.{nombre}')


LOG_MAX_BYTES = 5 * 1024 * 1024


def configurar_logging(nivel=logging.INFO, carpeta=None):
    """Consola + preve.log con rotación (5 MB × 3). Devuelve la ruta del archivo de log."""
    import logging.handlers
    import os
    global _CONFIGURADO
    carpeta = carpeta or os.path.dirname(os.path.abspath(__file__))
    ruta = os.path.join(carpeta, 'preve.log')
    if _CONFIGURADO:
        return ruta
    fmt = logging.Formatter('%(asctime)s %(levelname)-7s %(name)s: %(message)s')
    raiz = logging.getLogger()
    raiz.setLevel(nivel)
    consola = logging.StreamHandler()
    consola.setFormatter(fmt)
    archivo = logging.handlers.RotatingFileHandler(ruta, maxBytes=LOG_MAX_BYTES, backupCount=3, encoding='utf-8')
    archivo.setFormatter(fmt)
    raiz.addHandler(consola)
    raiz.addHandler(archivo)
    _CONFIGURADO = True
    return ruta


def log_info(mensaje):
    """Destino por defecto de los mensajes de progreso (antes `print`): el logger 'preve' (consola + preve.log)."""
    logging.getLogger('preve').info(mensaje)


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
