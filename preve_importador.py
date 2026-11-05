# -*- coding: utf-8 -*-
"""
PREVÉ — Importación flexible de ventas (Fase 4).

Sirve para cualquier ERP o planilla, sin depender de Negosy:
    1. detectar_formato()      xlsx / xls / csv / tsv
    2. leer_archivo()          encabezado detectado solo, separador y codificación de CSV autodetectados
    3. sugerir_mapeo()         adivina qué columna es cada campo de PREVÉ (el usuario lo corrige en la UI)
    4. detectar_formato_fecha() prueba día/mes, mes/día, ISO… y elige el que más fechas válidas da
    5. validar()               fechas válidas, cantidades numéricas, productos existentes → errores por fila
    6. importar()              guarda las filas válidas en ventas_diarias y escribe el reporte de errores

Los mapeos se pueden guardar como "perfil de importación" en la base (tabla perfiles_importacion).
"""
import csv
import datetime
import os
import re
import unicodedata

import numpy as np
import pandas as pd

from preve_utils import get_logger, log_info

logger = get_logger('importador')

# campo → (título, obligatorio, sinónimos para sugerir el mapeo)
CAMPOS = {
    'fecha': ('Fecha', True, ['fecha', 'fecha/hora', 'fecha hora', 'date', 'dia', 'fecha venta', 'fecha_emision']),
    'producto': ('Producto (código de barras o SKU)', True,
                 ['codigo de barra', 'codigo barras', 'cod. barra', 'cod barra', 'ean', 'barcode', 'sku',
                  'codigo', 'cod producto', 'id producto']),
    'cantidad': ('Cantidad', True, ['cantidad', 'cant', 'unidades', 'qty', 'quantity', 'cantidad vendida']),
    'sucursal': ('Sucursal / almacén', False, ['sucursal', 'almacen', 'tienda', 'local', 'store', 'branch']),
    'precio': ('Precio unitario', False, ['precio x unidad', 'precio unitario', 'precio', 'price', 'p. unit',
                                          'precio venta']),
    'nombre': ('Nombre del producto', False, ['producto', 'nombre', 'descripcion', 'articulo', 'item']),
}
CAMPOS_OBLIGATORIOS = [c for c, (_, ob, _) in CAMPOS.items() if ob]

FORMATOS_FECHA = [
    ('%d-%m-%Y %H:%M', 'día-mes-año hora'), ('%d/%m/%Y %H:%M', 'día/mes/año hora'),
    ('%d-%m-%Y', 'día-mes-año'), ('%d/%m/%Y', 'día/mes/año'), ('%Y-%m-%d', 'año-mes-día (ISO)'),
    ('%Y-%m-%d %H:%M:%S', 'ISO con hora'), ('%m/%d/%Y', 'mes/día/año (EE. UU.)'),
    ('%d-%m-%Y %I:%M %p', 'día-mes-año hora AM/PM'), ('%d.%m.%Y', 'día.mes.año'),
]
EXTENSIONES = {'.xlsx': 'xlsx', '.xlsm': 'xlsx', '.xls': 'xls', '.csv': 'csv', '.tsv': 'tsv', '.txt': 'csv'}
NOMBRE_REPORTE_ERRORES = 'errores_importacion_{:%Y-%m-%d_%H-%M}.xlsx'


def _norm(texto):
    t = unicodedata.normalize('NFKD', str(texto)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[\s_]+', ' ', t).strip()


# ══════════════════════════════════════════════════════════════════
# 1–2. Formato y lectura
# ══════════════════════════════════════════════════════════════════
def detectar_formato(ruta):
    """'xlsx' | 'xls' | 'csv' | 'tsv'. Raises ValueError si no es un formato soportado."""
    ext = os.path.splitext(ruta)[1].lower()
    if ext not in EXTENSIONES:
        raise ValueError(f'Formato no soportado: "{ext}". Usa Excel (.xlsx/.xls) o texto (.csv/.tsv).')
    formato = EXTENSIONES[ext]
    if formato == 'csv':
        with open(ruta, 'rb') as f:
            muestra = f.read(4096).decode('utf-8', errors='ignore')
        if muestra.count('\t') > max(muestra.count(','), muestra.count(';')):
            formato = 'tsv'
    return formato


def _separador(muestra):
    try:
        return csv.Sniffer().sniff(muestra, delimiters=',;\t|').delimiter
    except csv.Error:
        return ','


def leer_archivo(ruta, formato=None, separador=None):
    """DataFrame con todas las columnas como texto (para no perder ceros a la izquierda en los códigos)."""
    formato = formato or detectar_formato(ruta)
    if formato in ('xlsx', 'xls'):
        from preve_pipeline import _detectar_fila_encabezado
        hoja = pd.ExcelFile(ruta).sheet_names[0]
        fila = _detectar_fila_encabezado(ruta, hoja)
        df = pd.read_excel(ruta, sheet_name=hoja, header=fila, dtype=str)
    else:
        for codificacion in ('utf-8-sig', 'latin-1'):
            try:
                with open(ruta, 'r', encoding=codificacion) as f:
                    muestra = f.read(8192)
                break
            except UnicodeDecodeError:
                continue
        sep = separador or ('\t' if formato == 'tsv' else _separador(muestra))
        df = pd.read_csv(ruta, sep=sep, dtype=str, encoding=codificacion, keep_default_na=True)
    df = df.dropna(axis=1, how='all').dropna(axis=0, how='all')
    df.columns = [str(c).strip() for c in df.columns]
    return df[[c for c in df.columns if not c.startswith('Unnamed')]].reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════
# 3–4. Mapeo y formato de fecha
# ══════════════════════════════════════════════════════════════════
def sugerir_mapeo(columnas):
    """{campo: columna} con la mejor coincidencia por sinónimos (exacta primero, luego parcial)."""
    cols = {c: _norm(c) for c in columnas}
    usadas, mapeo = set(), {}
    for campo, (_, _, sinonimos) in CAMPOS.items():
        elegida = None
        for s in sinonimos:                                   # coincidencia exacta
            elegida = next((c for c, n in cols.items() if n == s and c not in usadas), None)
            if elegida:
                break
        if not elegida:                                       # coincidencia parcial
            for s in sinonimos:
                elegida = next((c for c, n in cols.items() if s in n and c not in usadas), None)
                if elegida:
                    break
        if elegida:
            mapeo[campo] = elegida
            usadas.add(elegida)
    return mapeo


def detectar_formato_fecha(serie, muestra=500):
    """Formato strptime que interpreta más valores; 'auto' si ninguno supera al parser genérico.

    Ante empate entre día/mes y mes/día, gana el que tenga días > 12 válidos (desempata la ambigüedad).
    """
    valores = serie.dropna().astype(str).str.strip()
    valores = valores[valores != ''].head(muestra)
    if valores.empty:
        return 'auto'
    mejor, mejor_n = 'auto', int(pd.to_datetime(valores, dayfirst=True, errors='coerce', format='mixed').notna().sum())
    for fmt, _ in FORMATOS_FECHA:
        n = int(pd.to_datetime(valores, format=fmt, errors='coerce').notna().sum())
        if n > mejor_n or (n == mejor_n and mejor == 'auto' and n == len(valores)):
            mejor, mejor_n = fmt, n
    return mejor


def convertir_fechas(serie, formato='auto'):
    """Convierte texto a fechas con el formato dado ("auto" = día primero, formato mixto)."""
    if not formato or formato == 'auto':
        return pd.to_datetime(serie, dayfirst=True, errors='coerce', format='mixed')
    return pd.to_datetime(serie.astype(str).str.strip(), format=formato, errors='coerce')


def _a_numero(serie):
    """'1,234.5' / '1.234,5' / 'S/ 3.50' → float; lo que no se pueda, NaN."""
    s = serie.astype(str).str.replace(r'[^\d,.\-]', '', regex=True)
    europeo = s.str.contains(r'\d\.\d{3},\d', regex=True) | s.str.fullmatch(r'-?\d+,\d+')
    s = s.where(~europeo, s.str.replace('.', '', regex=False).str.replace(',', '.', regex=False))
    s = s.str.replace(',', '', regex=False)
    return pd.to_numeric(s.replace('', np.nan), errors='coerce')


# ══════════════════════════════════════════════════════════════════
# 5. Validación
# ══════════════════════════════════════════════════════════════════
def validar(df, mapeo, formato_fecha='auto', productos=None, sucursales=None, sucursal_defecto=None):
    """Convierte y valida. Devuelve (validas, errores).

    validas: DataFrame sku / fecha / cantidad / precio_unitario / sucursal / nombre.
    errores: DataFrame fila (número de fila del archivo, 1 = primera de datos) / columna / valor / error.
    productos: {código o SKU: sku}; si se pasa, los productos que no estén son error.
    sucursales: nombres válidos; si se pasa, una sucursal desconocida es error.
    """
    faltan = [CAMPOS[c][0] for c in CAMPOS_OBLIGATORIOS if not mapeo.get(c) or mapeo[c] not in df.columns]
    if faltan:
        raise ValueError('Faltan columnas obligatorias en el mapeo: ' + ', '.join(faltan))
    errores = []

    def error(mask, campo, mensaje):
        col = mapeo.get(campo, campo)
        for i in df.index[mask]:
            errores.append({'fila': int(i) + 1, 'columna': col,
                            'valor': '' if campo not in mapeo else df.at[i, mapeo[campo]], 'error': mensaje})

    fecha = convertir_fechas(df[mapeo['fecha']], formato_fecha)
    error(fecha.isna(), 'fecha', f'Fecha no válida (formato esperado: {formato_fecha})')
    cantidad = _a_numero(df[mapeo['cantidad']])
    error(cantidad.isna(), 'cantidad', 'La cantidad no es un número')
    from preve_pipeline import normalizar_codigo
    codigo = df[mapeo['producto']].map(normalizar_codigo)
    error(codigo == '', 'producto', 'Falta el código del producto')
    sku = codigo if productos is None else codigo.map(productos)
    if productos is not None:
        error(sku.isna() & (codigo != ''), 'producto', 'El producto no existe en el catálogo')

    if mapeo.get('sucursal') in df.columns:
        sucursal = df[mapeo['sucursal']].fillna('').astype(str).str.strip()
        sucursal = sucursal.where(sucursal != '', sucursal_defecto or '')
    else:
        sucursal = pd.Series(sucursal_defecto or '', index=df.index)
    vacia = sucursal == ''
    error(vacia, 'sucursal', 'Falta la sucursal')
    if sucursales is not None:
        # sucursales: lista de nombres o {sucursal: [tiendas/almacenes]}; el ERP suele exportar la ubicación
        # ("Tienda Juliaca") y no el nombre de la sucursal ("San José"), así que se aceptan ambos
        validas_norm = {}
        for s in sucursales:
            validas_norm[_norm(s)] = s
            for ubic in (sucursales[s] if isinstance(sucursales, dict) else []):
                validas_norm.setdefault(_norm(ubic), s)
        normal = sucursal.map(lambda s: validas_norm.get(_norm(s)))
        error(normal.isna() & ~vacia, 'sucursal', 'Sucursal desconocida (no está en la configuración)')
        sucursal = normal

    precio = _a_numero(df[mapeo['precio']]) if mapeo.get('precio') in df.columns else pd.Series(np.nan, index=df.index)
    nombre = df[mapeo['nombre']] if mapeo.get('nombre') in df.columns else pd.Series('', index=df.index)

    errores_df = pd.DataFrame(errores, columns=['fila', 'columna', 'valor', 'error']).sort_values(
        ['fila', 'columna'], kind='stable').reset_index(drop=True)
    malas = set(errores_df['fila'] - 1)
    ok = ~df.index.isin(malas)
    validas = pd.DataFrame({'sku': sku[ok], 'fecha': fecha[ok].dt.normalize(), 'cantidad': cantidad[ok],
                            'precio_unitario': precio[ok], 'sucursal': sucursal[ok], 'nombre': nombre[ok]}
                           ).reset_index(drop=True)
    return validas, errores_df


# ══════════════════════════════════════════════════════════════════
# 6. Importación
# ══════════════════════════════════════════════════════════════════
def sucursales_y_ubicaciones():
    """{sucursal activa: [tiendas/almacenes]} desde la configuración."""
    from preve_config import Config
    return {s['nombre']: s['almacenes'] for s in Config.actual().sucursales if s['activa']}


class ResultadoImportacion(dict):
    """dict con filas_leidas, filas_validas, filas_con_error, guardadas, por_sucursal, ruta_errores."""

    @property
    def resumen(self):
        txt = (f'{self["filas_validas"]:,} de {self["filas_leidas"]:,} filas importadas '
               f'({self["guardadas"]:,} días-producto guardados).')
        if self['filas_con_error']:
            txt += f' {self["filas_con_error"]:,} filas con errores: revisa {os.path.basename(self["ruta_errores"])}.'
        return txt


def escribir_reporte_errores(errores, carpeta, nombre=None):
    """Excel con resumen de errores y detalle por fila. Devuelve la ruta."""
    ruta = os.path.join(carpeta, nombre or NOMBRE_REPORTE_ERRORES.format(datetime.datetime.now()))
    resumen = errores.groupby('error').size().rename('filas').reset_index().sort_values('filas', ascending=False)
    with pd.ExcelWriter(ruta) as xw:
        resumen.to_excel(xw, sheet_name='Resumen', index=False)
        errores.to_excel(xw, sheet_name='Errores por fila', index=False)
    return ruta


def importar(ruta, mapeo, db=None, formato_fecha=None, sucursal_defecto=None, exigir_producto_existente=True,
             carpeta_errores=None, log=log_info):
    """Lee, valida y guarda las ventas válidas en ventas_diarias. Devuelve ResultadoImportacion."""
    from database import Database

    db = db or Database.default()
    df = leer_archivo(ruta)
    log(f'Leídas {len(df):,} filas de {os.path.basename(ruta)}')
    if not formato_fecha or formato_fecha == 'detectar':
        formato_fecha = detectar_formato_fecha(df[mapeo['fecha']])
        log(f'Formato de fecha detectado: {formato_fecha}')
    productos = db.productos_por_codigo() if exigir_producto_existente else None
    if exigir_producto_existente and not productos:
        raise ValueError('El catálogo de productos está vacío: genera primero un análisis con el listado de '
                         'productos, o desmarca "solo productos existentes".')
    validas, errores = validar(df, mapeo, formato_fecha, productos, sucursales_y_ubicaciones(), sucursal_defecto)
    por_sucursal, guardadas = {}, 0
    for suc, grupo in validas.groupby('sucursal'):
        n = db.guardar_ventas(suc, grupo[['sku', 'fecha', 'cantidad', 'precio_unitario']])
        por_sucursal[suc] = n
        guardadas += n
    ruta_err = None
    if len(errores):
        ruta_err = escribir_reporte_errores(errores, carpeta_errores or os.path.dirname(os.path.abspath(ruta)))
    res = ResultadoImportacion(filas_leidas=len(df), filas_validas=len(validas),
                               filas_con_error=int(errores['fila'].nunique()) if len(errores) else 0,
                               guardadas=guardadas, por_sucursal=por_sucursal, ruta_errores=ruta_err,
                               formato_fecha=formato_fecha)
    log(res.resumen)
    return res
