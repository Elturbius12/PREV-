# -*- coding: utf-8 -*-
"""
PREVÉ — Depuración total de datos (Fase 8).

Permite empezar de cero: borra la base de datos, los reportes generados, los
logs y el estado del ciclo. De las carpetas de salida de las sucursales solo se
borran las subcarpetas de período (AAAA-MM-DD_a_AAAA-MM-DD); cualquier otro archivo
(p. ej. ordenes_compra_abiertas.csv) queda intacto. La configuración (empresa, sucursales, ciclos,
plantillas, parámetros del modelo, usuario) se conserva, para no tener que
pasar otra vez por el asistente.

Antes de borrar se arma un ZIP de respaldo con todo lo que se va a eliminar,
dentro de `respaldos_depurar/` junto a la app. Si algo salió mal, el usuario
abre el ZIP y recupera lo que necesite.

Uso programático:
    from preve_depurar import depurar_datos, preview_depuracion
    resumen = preview_depuracion()           # qué se borrará (sin tocar nada)
    reporte = depurar_datos(conservar_config=True)
"""
from __future__ import annotations

import datetime
import os
import shutil
import sqlite3
import zipfile

from preve_utils import get_logger, es_carpeta_periodo

logger = get_logger('depurar')

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────────────────────────────
# Archivos / carpetas que se eliminan en la depuración total
# ─────────────────────────────────────────────────────────────────────
# Archivos sueltos
ARCHIVOS_BORRABLES = [
    'preve.db',
    'preve.db-wal',
    'preve.db-shm',
    'preve.db-journal',
    'ciclo_estado.json',
    'preve.log',
    '.coverage',
]
# Carpetas completas
CARPETAS_BORRABLES = [
    'reportes_backtesting',
    '__pycache__',
    '.pytest_cache',
    'catboost_info',
]

# Tablas que se vacían cuando conservar_config=True (se mantienen las de configuración)
TABLAS_CONFIGURACION = {
    'empresas', 'sucursales', 'ciclos_compra', 'configuracion',
    'plantillas_categorias', 'categorias_activas', 'proveedores',
    'perfiles_importacion', 'productos',
}


def _carpetas_salida_configuradas():
    """Carpetas de salida de todas las sucursales configuradas (puede ser [] si no se puede leer la base)."""
    try:
        from preve_config import Config
        return [s['salida'] for s in Config.actual().sucursales if s.get('salida')]
    except Exception:  # noqa: BLE001 — sin configuración legible simplemente no hay carpetas de salida que limpiar
        logger.warning('No se pudieron leer las carpetas de salida de la configuración', exc_info=True)
        return []


def _periodos_en(carpetas_salida):
    """Subcarpetas de período dentro de cada carpeta de salida y dentro de sus subcarpetas directas
    (el modo multi-sucursal crea salida/<Sucursal>/<período>). Nunca devuelve otra cosa que carpetas de
    período reales (no enlaces simbólicos). -> [{'ruta', 'nombre', 'bytes', 'humano', 'raiz'}]"""
    vistos, res = set(), []

    def _agregar(ruta, raiz):
        real = os.path.realpath(ruta)
        if real in vistos or os.path.islink(ruta) or not os.path.isdir(ruta):
            return
        vistos.add(real)
        b = _tamanio(ruta)
        res.append({'ruta': ruta, 'nombre': os.path.relpath(ruta, os.path.dirname(raiz)), 'bytes': b,
                    'humano': _humano(b), 'raiz': raiz})

    for raiz in carpetas_salida:
        if not raiz or not os.path.isdir(raiz):
            continue
        try:
            hijos = sorted(os.listdir(raiz))
        except OSError:
            continue
        for n in hijos:
            ruta = os.path.join(raiz, n)
            if es_carpeta_periodo(n):
                _agregar(ruta, raiz)
            elif os.path.isdir(ruta) and not os.path.islink(ruta):
                try:
                    nietos = sorted(os.listdir(ruta))
                except OSError:
                    continue
                for m in nietos:
                    if es_carpeta_periodo(m):
                        _agregar(os.path.join(ruta, m), raiz)
    return res


def _ruta(nombre):
    return os.path.join(APP_DIR, nombre)


def _tamanio(ruta):
    """Tamaño en bytes de un archivo o carpeta. 0 si no existe."""
    if not os.path.exists(ruta):
        return 0
    if os.path.isfile(ruta):
        try:
            return os.path.getsize(ruta)
        except OSError:
            return 0
    total = 0
    for base, _, files in os.walk(ruta):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return total


def _humano(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f'{n:.1f} {u}' if u != 'B' else f'{int(n)} {u}'
        n /= 1024
    return f'{n:.1f} TB'


def preview_depuracion(carpetas_salida=None):
    """
    Devuelve qué se borrará y qué se conservará, sin tocar nada.

    carpetas_salida: carpetas de salida donde buscar períodos (por defecto, las de la configuración).

    Formato:
        {
            'archivos': [{'nombre': ..., 'bytes': ..., 'humano': ...}, ...],
            'carpetas': [{'nombre': ..., 'bytes': ..., 'humano': ...}, ...],
            'periodos': [{'nombre': ..., 'ruta': ..., 'bytes': ..., 'humano': ...}, ...],  # análisis guardados
            'tablas_a_vaciar': [...],         # las de ventas, pronósticos, métricas…
            'tablas_a_conservar': [...],      # las de configuración
            'total_bytes': int,
            'total_humano': str,
        }
    """
    archivos, total = [], 0
    for n in ARCHIVOS_BORRABLES:
        b = _tamanio(_ruta(n))
        if b > 0 or os.path.exists(_ruta(n)):
            archivos.append({'nombre': n, 'bytes': b, 'humano': _humano(b)})
            total += b
    carpetas = []
    for n in CARPETAS_BORRABLES:
        b = _tamanio(_ruta(n))
        if b > 0 or os.path.isdir(_ruta(n)):
            carpetas.append({'nombre': n, 'bytes': b, 'humano': _humano(b)})
            total += b
    periodos = _periodos_en(_carpetas_salida_configuradas() if carpetas_salida is None else carpetas_salida)
    total += sum(p['bytes'] for p in periodos)
    tablas_todas = _listar_tablas_bd()
    tablas_a_vaciar = sorted(t for t in tablas_todas if t not in TABLAS_CONFIGURACION)
    tablas_a_conservar = sorted(t for t in tablas_todas if t in TABLAS_CONFIGURACION)
    return {
        'archivos': archivos,
        'carpetas': carpetas,
        'periodos': periodos,
        'tablas_a_vaciar': tablas_a_vaciar,
        'tablas_a_conservar': tablas_a_conservar,
        'total_bytes': total,
        'total_humano': _humano(total),
    }


def _listar_tablas_bd():
    """Nombres de tablas de preve.db (ignora sqlite_*)."""
    db = _ruta('preve.db')
    if not os.path.exists(db):
        return []
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        nombres = [r[0] for r in cur.fetchall()]
        con.close()
        return nombres
    except sqlite3.Error:
        logger.warning('No se pudo leer el esquema de preve.db', exc_info=True)
        return []


def _hacer_respaldo(periodos=()):
    """Empaqueta todo lo que se va a borrar en respaldos_depurar/YYYY-MM-DD_HH-MM.zip."""
    carpeta = _ruta('respaldos_depurar')
    os.makedirs(carpeta, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    destino = os.path.join(carpeta, f'respaldo_{ts}.zip')
    items = 0
    with zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED) as z:
        for n in ARCHIVOS_BORRABLES:
            r = _ruta(n)
            if os.path.isfile(r):
                z.write(r, arcname=n)
                items += 1
        for n in CARPETAS_BORRABLES:
            r = _ruta(n)
            if os.path.isdir(r):
                for base, _, files in os.walk(r):
                    for f in files:
                        full = os.path.join(base, f)
                        arc = os.path.relpath(full, APP_DIR)
                        try:
                            z.write(full, arcname=arc)
                            items += 1
                        except OSError:
                            logger.warning('No se pudo respaldar %s', full, exc_info=True)
        for i, per in enumerate(periodos, 1):
            base_zip = f'salidas/{i}_{os.path.basename(per["raiz"])}'
            for base, _, files in os.walk(per['ruta']):
                for f in files:
                    full = os.path.join(base, f)
                    rel = os.path.relpath(full, os.path.dirname(per['raiz'])).replace(os.sep, '/')
                    try:
                        z.write(full, arcname=f'{base_zip}/{rel}')
                        items += 1
                    except OSError:
                        logger.warning('No se pudo respaldar %s', full, exc_info=True)
    return destino, items


def _vaciar_tablas_de_datos(conservar_config):
    """
    Si conservar_config=True, sólo vacía las tablas de datos (ventas, pronósticos,
    métricas, decisiones, historial, backtesting). Las tablas de configuración
    se mantienen para no perder empresa/sucursales/ciclos/plantillas.

    Si conservar_config=False, deja que se borre `preve.db` completo (desde fuera).
    """
    if not conservar_config:
        return {'tablas_vaciadas': [], 'total_filas': 0}
    db = _ruta('preve.db')
    if not os.path.exists(db):
        return {'tablas_vaciadas': [], 'total_filas': 0}
    tablas = _listar_tablas_bd()
    a_vaciar = [t for t in tablas if t not in TABLAS_CONFIGURACION]
    con = sqlite3.connect(db)
    total = 0
    vaciadas = []
    try:
        for t in a_vaciar:
            try:
                cur = con.execute(f'SELECT COUNT(*) FROM "{t}"')
                n = cur.fetchone()[0]
                con.execute(f'DELETE FROM "{t}"')
                total += n
                vaciadas.append({'tabla': t, 'filas': n})
            except sqlite3.Error:
                logger.warning('No se pudo vaciar la tabla %s', t, exc_info=True)
        # Reset de autoincrement
        try:
            con.execute('DELETE FROM sqlite_sequence')
        except sqlite3.Error:
            pass                      # sqlite_sequence sólo existe si hubo INTEGER PRIMARY KEY AUTOINCREMENT
        con.commit()
        try:
            con.execute('VACUUM')
        except sqlite3.Error:
            logger.warning('VACUUM falló tras la depuración', exc_info=True)
    finally:
        con.close()
    return {'tablas_vaciadas': vaciadas, 'total_filas': total}


def _borrar_archivos():
    borrados, errores = [], []
    for n in ARCHIVOS_BORRABLES:
        r = _ruta(n)
        if os.path.isfile(r):
            try:
                os.remove(r)
                borrados.append(n)
            except OSError as e:
                errores.append(f'{n}: {e}')
    return borrados, errores


def _borrar_carpetas():
    borradas, errores = [], []
    for n in CARPETAS_BORRABLES:
        r = _ruta(n)
        if os.path.isdir(r):
            try:
                shutil.rmtree(r)
                borradas.append(n)
            except OSError as e:
                errores.append(f'{n}: {e}')
    return borradas, errores


def _borrar_periodos(periodos):
    borrados, errores = [], []
    for per in periodos:
        ruta = per['ruta']
        # Doble comprobación: solo carpetas de período reales, nunca enlaces ni otros nombres
        if not es_carpeta_periodo(os.path.basename(ruta)) or os.path.islink(ruta):
            continue
        try:
            shutil.rmtree(ruta)
            borrados.append(per['nombre'])
        except OSError as e:
            errores.append(f'{per["nombre"]}: {e}')
    return borrados, errores


def depurar_datos(conservar_config: bool = True, hacer_respaldo: bool = True, carpetas_salida=None):
    """
    Borra la data histórica para empezar de cero.

    Args:
        conservar_config: True (por defecto) mantiene empresa, sucursales,
            ciclos, plantillas y parámetros. False borra TODO (requiere pasar
            otra vez por el asistente inicial).
        carpetas_salida: carpetas de salida donde borrar los períodos (por defecto, las de la
            configuración; se leen ANTES de tocar la base).
        hacer_respaldo: True (por defecto) empaqueta lo borrado en un ZIP
            dentro de respaldos_depurar/ antes de borrar.

    Returns:
        dict con lo que hizo: respaldo, archivos borrados, tablas vaciadas,
        errores. Pensado para mostrar un resumen al usuario.
    """
    reporte = {
        'inicio': datetime.datetime.now().isoformat(timespec='seconds'),
        'conservar_config': conservar_config,
        'respaldo': None,
        'items_respaldados': 0,
        'archivos_borrados': [],
        'carpetas_borradas': [],
        'periodos_borrados': [],
        'tablas_vaciadas': [],
        'filas_borradas': 0,
        'errores': [],
    }
    logger.info('Depuración iniciada (conservar_config=%s)', conservar_config)
    # Las carpetas de salida se leen de la configuración, así que se localizan antes de vaciar la base
    periodos = _periodos_en(_carpetas_salida_configuradas() if carpetas_salida is None else carpetas_salida)

    try:
        if hacer_respaldo:
            reporte['respaldo'], reporte['items_respaldados'] = _hacer_respaldo(periodos)
            logger.info('Respaldo: %s (%d archivos)',
                        reporte['respaldo'], reporte['items_respaldados'])
    except (OSError, zipfile.BadZipFile) as e:
        reporte['errores'].append(f'Respaldo: {e}')
        logger.warning('No se pudo crear el respaldo', exc_info=True)

    if conservar_config:
        # Vaciar primero las tablas de datos; luego borrar archivos auxiliares
        # (WAL/SHM) y carpetas de reportes.
        resumen = _vaciar_tablas_de_datos(conservar_config=True)
        reporte['tablas_vaciadas'] = resumen['tablas_vaciadas']
        reporte['filas_borradas'] = resumen['total_filas']
        # Con conservar_config, no borramos preve.db (sólo WAL/SHM/journal).
        archivos_a_tocar = [n for n in ARCHIVOS_BORRABLES if n != 'preve.db']
    else:
        archivos_a_tocar = list(ARCHIVOS_BORRABLES)

    # Borrado real
    for n in archivos_a_tocar:
        r = _ruta(n)
        if os.path.isfile(r):
            try:
                os.remove(r)
                reporte['archivos_borrados'].append(n)
            except OSError as e:
                reporte['errores'].append(f'{n}: {e}')

    borradas, errs = _borrar_carpetas()
    reporte['carpetas_borradas'] = borradas
    reporte['errores'].extend(errs)
    borrados, errs = _borrar_periodos(periodos)
    reporte['periodos_borrados'] = borrados
    reporte['errores'].extend(errs)

    reporte['fin'] = datetime.datetime.now().isoformat(timespec='seconds')
    logger.info('Depuración terminada: %d archivos, %d carpetas, %d períodos, %d filas, %d errores',
                len(reporte['archivos_borrados']), len(reporte['carpetas_borradas']), len(borrados),
                reporte['filas_borradas'], len(reporte['errores']))
    return reporte


def resumen_para_usuario(reporte):
    """Resumen legible del reporte de depurar_datos(), para mostrar en un toast o diálogo."""
    partes = []
    if reporte.get('respaldo'):
        partes.append(f'📦 Respaldo: {os.path.basename(reporte["respaldo"])} '
                      f'({reporte["items_respaldados"]} archivos)')
    if reporte['filas_borradas']:
        partes.append(f'🗑 {reporte["filas_borradas"]:,} filas eliminadas en '
                      f'{len(reporte["tablas_vaciadas"])} tablas')
    if reporte['archivos_borrados']:
        partes.append(f'📄 {len(reporte["archivos_borrados"])} archivos borrados')
    if reporte['carpetas_borradas']:
        partes.append(f'📁 {len(reporte["carpetas_borradas"])} carpetas borradas')
    if reporte.get('periodos_borrados'):
        partes.append(f'🗂 {len(reporte["periodos_borrados"])} análisis guardados borrados')
    if reporte['errores']:
        partes.append(f'⚠ {len(reporte["errores"])} errores (revisa preve.log)')
    if not partes:
        return 'No había datos para depurar.'
    return ' · '.join(partes)
