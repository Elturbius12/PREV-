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


# ══════════════════════════════════════════════════════════════════
# Carpetas de período (cada análisis vive en salida/AAAA-MM-DD_a_AAAA-MM-DD)
# ══════════════════════════════════════════════════════════════════
import re as _re

PATRON_PERIODO = _re.compile(r'^\d{4}-\d{2}-\d{2}_a_\d{4}-\d{2}-\d{2}$')
NOMBRE_REC_CSV = 'recomendacion_compra.csv'


def es_carpeta_periodo(nombre):
    """True si el nombre tiene la forma AAAA-MM-DD_a_AAAA-MM-DD."""
    return bool(PATRON_PERIODO.match(nombre or ''))


def listar_periodos(carpeta_salida, con_archivo=None):
    """Rutas de las subcarpetas de período de `carpeta_salida`, de la más antigua a la más reciente.

    con_archivo: si se indica, solo cuentan las que contienen ese archivo.
    """
    import os
    if not carpeta_salida or not os.path.isdir(carpeta_salida):
        return []
    try:
        nombres = sorted(n for n in os.listdir(carpeta_salida) if es_carpeta_periodo(n))
    except OSError:
        return []
    rutas = [os.path.join(carpeta_salida, n) for n in nombres if os.path.isdir(os.path.join(carpeta_salida, n))]
    if con_archivo:
        rutas = [r for r in rutas if os.path.exists(os.path.join(r, con_archivo))]
    return rutas


def ultimo_periodo(carpeta_salida, con_archivo=NOMBRE_REC_CSV):
    """Ruta del período más reciente (o None). Por defecto exige que tenga la recomendación."""
    rutas = listar_periodos(carpeta_salida, con_archivo)
    return rutas[-1] if rutas else None


def fin_de_periodo(ruta_periodo):
    """Fecha final (datetime.date) codificada en el nombre de la carpeta de período, o None."""
    import os
    import datetime
    nombre = os.path.basename(os.path.normpath(ruta_periodo or ''))
    if not es_carpeta_periodo(nombre):
        return None
    try:
        return datetime.date.fromisoformat(nombre.split('_a_')[1])
    except ValueError:
        return None
