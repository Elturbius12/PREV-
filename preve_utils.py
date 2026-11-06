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


def _a_fecha(f):
    """datetime / date / Timestamp / 'AAAA-MM-DD' → datetime.date."""
    import datetime
    if isinstance(f, datetime.datetime):
        return f.date()
    if isinstance(f, datetime.date):
        return f
    return datetime.date.fromisoformat(str(f)[:10])


def nombre_periodo(inicio, fin):
    """Nombre de carpeta de período: 'AAAA-MM-DD_a_AAAA-MM-DD'. Lo usan la salida y la entrada."""
    return f'{_a_fecha(inicio).isoformat()}_a_{_a_fecha(fin).isoformat()}'


# ══════════════════════════════════════════════════════════════════
# Períodos de ENTRADA (descargas automáticas desde el ERP)
# ══════════════════════════════════════════════════════════════════
MARCADOR_DESCARGA = '.descarga_completa'
DIAS_PRIMERA_CARGA = 730


def marcar_descarga_completa(ruta_periodo):
    """Deja el marcador que indica que la descarga de ese período terminó bien."""
    import os
    with open(os.path.join(ruta_periodo, MARCADOR_DESCARGA), 'w', encoding='utf-8') as f:
        f.write('ok\n')


def quitar_marca_descarga(ruta_periodo):
    """Quita el marcador (al empezar a reintentar un período, hasta que termine bien)."""
    import os
    try:
        os.remove(os.path.join(ruta_periodo, MARCADOR_DESCARGA))
    except FileNotFoundError:
        pass


def periodos_importados(carpeta_entrada):
    """Períodos de entrada con descarga completa, del más antiguo al más reciente.
    Un período a medias (sin marcador) no cuenta como importado."""
    return listar_periodos(carpeta_entrada, MARCADOR_DESCARGA)


def ultimo_periodo_importado(carpeta_entrada):
    """Ruta del último período de entrada con descarga completa (o None)."""
    rutas = periodos_importados(carpeta_entrada)
    return rutas[-1] if rutas else None


def rango_descarga(carpeta_entrada, hoy=None):
    """(inicio, fin) de la próxima descarga automática, o None si ya está todo al día.

    El fin es siempre AYER (el día en curso aún no terminó de registrarse). Sin período previo es la
    primera carga histórica: DIAS_PRIMERA_CARGA días. Con período previo: desde el día siguiente a su fin.
    """
    import datetime
    hoy = _a_fecha(hoy) if hoy is not None else datetime.date.today()
    fin = hoy - datetime.timedelta(days=1)
    previo = ultimo_periodo_importado(carpeta_entrada)
    fin_previo = fin_de_periodo(previo) if previo else None
    inicio = fin_previo + datetime.timedelta(days=1) if fin_previo else hoy - datetime.timedelta(days=DIAS_PRIMERA_CARGA)
    return (inicio, fin) if inicio <= fin else None
