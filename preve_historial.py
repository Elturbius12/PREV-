# -*- coding: utf-8 -*-
"""
PREVÉ — Historial de ejecuciones y decisiones.

Fachada sobre `database.Database` (Fase 2): ya no ejecuta SQL propio. Mantiene la misma API que
usaban la interfaz y el orquestador. El parámetro `carpeta_salida` se conserva por compatibilidad:
la base es única (preve.db), pero si en esa carpeta hay un `preve_historial.db` antiguo se importa
automáticamente la primera vez.
"""
import os


from database import Database
from preve_utils import get_logger, log_info

logger = get_logger('historial')

DB_NAME = 'preve_historial.db'          # nombre del historial antiguo (una base por carpeta)


def _db_path(carpeta_salida):
    return os.path.join(carpeta_salida, DB_NAME)


def _db(carpeta_salida=None):
    """Base de datos compartida; importa el historial antiguo de la carpeta si existe y no se importó."""
    db = Database.default()
    if carpeta_salida and os.path.exists(_db_path(carpeta_salida)):
        db.importar_historial_legado(_db_path(carpeta_salida))
    return db


def registrar_ejecucion(carpeta_salida, recomendacion, info_ciclo=None,
                        sucursal='San José', scorecard=None, log=log_info):
    """Guarda una ejecución completa en el historial. Devuelve el id de la ejecución."""
    ej_id = _db(carpeta_salida).registrar_ejecucion(sucursal, recomendacion, info_ciclo, scorecard)
    etiqueta = (info_ciclo or {}).get('etiqueta', '')
    log(f'Historial: ejecución #{ej_id} registrada ({sucursal}, {etiqueta}, {len(recomendacion)} SKUs)')
    return ej_id


def consultar_historial(carpeta_salida, sucursal=None, ultimas_n=10):
    """Últimas ejecuciones (de la sucursal si se indica)."""
    return _db(carpeta_salida).consultar_ejecuciones(sucursal, ultimas_n)


def consultar_recomendacion_sku(carpeta_salida, sku, ultimas_n=5):
    """Historial de recomendaciones de un SKU."""
    return _db(carpeta_salida).recomendaciones_de_sku(sku, ultimas_n)


def consultar_evolucion_metricas(carpeta_salida, sucursal=None):
    """Ejecuciones con sus métricas de validación, en orden cronológico."""
    return _db(carpeta_salida).evolucion_metricas(sucursal)


# ══════════════════════════════════════════════════════════════════
# Decisiones del usuario sobre cada recomendación (comprado / posponer / descartar)
# ══════════════════════════════════════════════════════════════════
def historial_existe(carpeta_salida):
    """True si hay al menos una ejecución registrada."""
    return _db(carpeta_salida).hay_ejecuciones()


def guardar_decision(carpeta_salida, sku, ciclo, estado, razon='', hasta=None, sucursal='San José'):
    """Guarda (o reemplaza) la decisión del usuario sobre un producto en un ciclo."""
    _db(carpeta_salida).guardar_decision(sku, sucursal, ciclo, estado, razon, hasta)


def borrar_decision(carpeta_salida, sku, ciclo, sucursal='San José'):
    """Elimina la decisión del usuario sobre un producto en un ciclo."""
    _db(carpeta_salida).borrar_decision(sku, sucursal, ciclo)


def cargar_decisiones(carpeta_salida, ciclo, sucursal='San José'):
    """{sku: {'estado', 'razon', 'hasta', 'fecha'}} del ciclo indicado."""
    return _db(carpeta_salida).cargar_decisiones(sucursal, ciclo)


# ══════════════════════════════════════════════════════════════════
# Consultas para la interfaz (comparación, scorecard, búsqueda)
# ══════════════════════════════════════════════════════════════════
def comparar_ultimas(carpeta_salida, sucursal=None):
    """Compara la última ejecución con la anterior (de la misma sucursal si se indica).

    Returns None si no hay historial; si no hay anterior, 'previa' es None.
    """
    db = _db(carpeta_salida)
    df = db.consultar_ejecuciones(sucursal, limite=2)
    if df.empty:
        return None

    def _d(f):
        return {'id': int(f['id']), 'fecha': f['fecha'], 'inversion': f['inversion_total'] or 0.0,
                'recomprar': int(f['skus_recomprar'] or 0), 'etiqueta': f['etiqueta'] or ''}
    filas = df.to_dict('records')
    actual = _d(filas[0])
    previa = _d(filas[1]) if len(filas) > 1 else None
    por_sku = db.compras_sugeridas(previa['id']) if previa else {}
    return {'actual': actual, 'previa': previa, 'previa_por_sku': por_sku}


def ultimo_scorecard(carpeta_salida, sucursal=None):
    """{'wmape', 'calificacion'} de la última validación real (de la sucursal si se indica), o None."""
    return _db(carpeta_salida).ultimo_scorecard(sucursal)


def ejecuciones_con_diff(carpeta_salida, ultimas_n=50, sucursal=None):
    """Ejecuciones recientes con la variación de inversión respecto a la ejecución anterior."""
    df = _db(carpeta_salida).consultar_ejecuciones(sucursal, limite=ultimas_n + 1)
    if df.empty:
        return df
    df['inversion_prev'] = df['inversion_total'].shift(-1)
    df['recomprar_prev'] = df['skus_recomprar'].shift(-1)
    return df.head(ultimas_n)


def buscar_en_historial(carpeta_salida, texto, limite=6, sucursal=None):
    """Productos que coinciden con el texto en recomendaciones pasadas (último registro por SKU)."""
    if not texto.strip():
        return []
    return _db(carpeta_salida).buscar_en_historial(texto, limite, sucursal)


