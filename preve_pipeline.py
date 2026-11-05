# -*- coding: utf-8 -*-
"""
PREVÉ - Motor de pronóstico y recomendación de compra · Yuju Store
=====================================================================
Este módulo automatiza TODO el proceso que antes se hizo a mano:
cargar los reportes crudos del ERP, limpiarlos, reconstruir el stock,
segmentar los productos, entrenar 3 modelos de pronóstico (LightGBM,
CatBoost, Croston-TSB) y calcular cuánto comprar de cada producto.

Reglas de negocio aplicadas (todas reales, confirmadas con el dueño del
negocio, nunca inventadas):
  - San José = "Tienda Juliaca" + "Almacén Juliaca" (misma unidad de
    negocio; traslados internos entre ambos se netean a 0).
  - Lead time = 36 días para todos los proveedores (dato real dado por
    el dueño; el ERP no distingue fecha de pedido/embarque/llegada).
  - Intervalo de revisión = 105 días. Horizonte total = 141 días.
  - SKU canónico = código de barras. Cuando un mismo código de barras
    corresponde a productos realmente distintos, se le agrega un sufijo
    (-2, -3...) para no mezclarlos.
  - Nunca se inventa un número: todo dato faltante queda en blanco o
    marcado como "pendiente"/"revisar".

Uso (desde preve_app.py, o desde consola):
    from preve_pipeline import ejecutar_pipeline
    ejecutar_pipeline(carpeta_entrada, carpeta_salida, progreso=print)
"""

import os
import re
import glob
import warnings
import numpy as np
import pandas as pd

from preve_utils import get_logger

warnings.filterwarnings('ignore')
logger = get_logger('pipeline')

# ---------------------------------------------------------------------------
# Parámetros de negocio (reales, confirmados con el dueño del negocio)
# ---------------------------------------------------------------------------
# Ciclo INTERNACIONAL (proveedores importados)
INTERVALO_REVISION_DIAS_INT = 105
LEAD_TIME_DIAS_INT = 36
HORIZONTE_DIAS_INT = INTERVALO_REVISION_DIAS_INT + LEAD_TIME_DIAS_INT   # 141
HORIZONTE_SEMANAS_INT = HORIZONTE_DIAS_INT / 7.0                         # 20.14

# Ciclo LOCAL (proveedores nacionales)
INTERVALO_REVISION_DIAS_LOCAL = 30
LEAD_TIME_DIAS_LOCAL = 10
HORIZONTE_DIAS_LOCAL = INTERVALO_REVISION_DIAS_LOCAL + LEAD_TIME_DIAS_LOCAL  # 40
HORIZONTE_SEMANAS_LOCAL = HORIZONTE_DIAS_LOCAL / 7.0                         # 5.71

# Por defecto se usa el ciclo internacional (el más largo / conservador)
HORIZONTE_DIAS = HORIZONTE_DIAS_INT
HORIZONTE_SEMANAS = HORIZONTE_SEMANAS_INT

SAN_JOSE_ALMACENES = {'Tienda Juliaca', 'Almacén Juliaca', 'Almacen Juliaca'}

Z_NIVEL_SERVICIO = {'A': 1.645, 'B': 1.2816, 'C': 1.0364}   # 95% / 90% / 85%

ADI_UMBRAL = 1.32
CV2_UMBRAL = 0.49
SEMANAS_LANZAMIENTO = 13

# FIX: horizonte entero en semanas para el target de LightGBM (141 d ≈ 20 sem)
HORIZONTE_SEMANAS_TARGET = int(round(HORIZONTE_DIAS / 7.0))

COLUMNAS_CODIGO_BARRA = ('CODIGO BARRAS', 'CÓDIGO DE BARRA', 'COD. BARRA')
# Columnas de numeración de fila del ERP: cambian entre exportaciones y no identifican la transacción
_COLUMNAS_NUMERACION = {'N', 'Nº', 'N°', '#'}

# Si más de esta fracción de las unidades vendidas aparece también como "Salida" de almacén,
# probablemente el ERP registra cada venta como salida (doble descuento).
UMBRAL_DOBLE_DESCUENTO = 0.8

NOMBRE_ORDENES_ABIERTAS = 'ordenes_compra_abiertas.csv'
COLUMNAS_ORDENES = ['sku', 'cantidad', 'proveedor', 'fecha_pedido', 'fecha_estimada_llegada', 'estado']


# ---------------------------------------------------------------------------
# Utilidades de lectura: los reportes del ERP tienen 1-2 filas de título
# antes de la fila real de encabezados. Esta función la detecta sola.
# ---------------------------------------------------------------------------
def _detectar_fila_encabezado(path, sheet_name, max_filas=6):
    raw = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=max_filas)
    claves = {'FECHA', 'SKU', 'PRODUCTO', 'CODIGO BARRAS', 'CÓDIGO DE BARRA',
              'N', 'Nº', '#', 'COD. BARRA'}
    for i in range(len(raw)):
        valores = set(str(v).strip().upper() for v in raw.iloc[i].tolist() if pd.notna(v))
        if valores & claves:
            return i
    return 1  # por defecto, la segunda fila


def normalizar_codigo(v):
    """Código de barras como texto: 7751234567890.0 → '7751234567890', NaN → ''."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ''
    if isinstance(v, (float, np.floating)) and float(v).is_integer():
        return str(int(v))
    s = str(v).strip()
    if re.fullmatch(r'\d+\.0+', s):
        s = s.split('.')[0]
    return '' if s.lower() == 'nan' else s


def _leer_hoja(path, sheet_name=None):
    xls = pd.ExcelFile(path)
    hoja = sheet_name or xls.sheet_names[0]
    fila = _detectar_fila_encabezado(path, hoja)
    # FIX: forzar códigos de barra como texto desde la lectura (antes llegaban como float si había vacíos)
    encabezados = [str(c).strip() for c in
                   pd.read_excel(xls, sheet_name=hoja, header=fila, nrows=0).columns]
    convertidores = {c: str for c in encabezados if c in COLUMNAS_CODIGO_BARRA}
    df = pd.read_excel(xls, sheet_name=hoja, header=fila, converters=convertidores)
    df = df.dropna(axis=1, how='all')
    df.columns = [str(c).strip() for c in df.columns]
    for c in COLUMNAS_CODIGO_BARRA:
        if c in df.columns:
            df[c] = df[c].map(normalizar_codigo)
    return df


def deduplicar_entre_archivos(dfs):
    """Une exportaciones que pueden solaparse en fechas sin contar dos veces la misma transacción.

    Dentro de un archivo, dos filas idénticas son transacciones reales (p. ej. dos líneas iguales en un
    ticket). Entre archivos, la misma fila es la misma transacción exportada dos veces. Por eso cada fila
    se numera por ocurrencia dentro de su archivo y se conserva el máximo de ocurrencias entre archivos.
    """
    # FIX: antes se concatenaba todo y las exportaciones solapadas duplicaban ventas/compras/movimientos
    if not dfs:
        return pd.DataFrame()
    partes = []
    for df in dfs:
        df = df.copy()
        clave = [c for c in df.columns if c not in _COLUMNAS_NUMERACION
                 and not str(c).startswith(('Unnamed', '__'))]
        df['__occ'] = df.groupby(clave, dropna=False).cumcount()
        partes.append(df)
    todo = pd.concat(partes, ignore_index=True)
    clave = [c for c in todo.columns if c not in _COLUMNAS_NUMERACION
             and not str(c).startswith(('Unnamed', '__'))] + ['__occ']
    return todo.drop_duplicates(subset=clave, keep='first').drop(columns='__occ').reset_index(drop=True)


def fecha_desde_nombre(path):
    """Fecha de exportación escrita en el nombre del archivo (YYYY-MM-DD o YYYYMMDD), o None."""
    nombre = os.path.basename(str(path))
    for patron in (r'(20\d{2})-(\d{2})-(\d{2})', r'(20\d{2})(\d{2})(\d{2})'):
        m = re.search(patron, nombre)
        if m:
            try:
                return pd.Timestamp(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
    return None


def _parse_fechas(serie, formatos, log, nombre_fuente):
    """Prueba cada formato y completa lo que falte con el parser genérico (día primero)."""
    # FIX: compras solo aceptaba '%d-%m-%Y'; si cambiaba el formato se perdían todas sin aviso
    res = pd.Series(pd.NaT, index=serie.index, dtype='datetime64[ns]')
    for fmt in formatos:
        faltan = res.isna()
        if not faltan.any():
            break
        res[faltan] = pd.to_datetime(serie[faltan], format=fmt, errors='coerce')
    faltan = res.isna() & serie.notna()
    if faltan.any():
        res[faltan] = pd.to_datetime(serie[faltan], dayfirst=True, errors='coerce', format='mixed')
    invalidas = int((res.isna() & serie.notna()).sum())
    if invalidas:
        log(f'  ⚠ {nombre_fuente}: {invalidas}/{len(serie)} filas con fecha no reconocida (se ignoran)')
    return res


def _parse_money(valor):
    if pd.isna(valor):
        return np.nan
    s = str(valor).replace('S/', '').replace(',', '').strip()
    try:
        return float(s)
    except ValueError:
        return np.nan


def _normalizar_nombre(s):
    if pd.isna(s):
        return ''
    s = str(s).upper().strip()
    s = re.sub(r'\s+', ' ', s)
    return s


def _clasificar_archivos(carpeta):
    """Agrupa los archivos de la carpeta de entrada por tipo de reporte,
    según el nombre del archivo (igual que los que ya exporta el ERP)."""
    patrones = {
        'listado_productos': r'listado.*productos',
        'ventas': r'reporte.*ventas.*producto',
        'compras': r'reporte.*compras.*producto',
        'movimientos_producto': r'reporte.*productos.*movimiento',
    }
       # Busca tambien dentro de subcarpetas (ej. DATA SJ/CATALOGO/, DATA SJ/VENTAS/...),
    # por si el usuario organiza sus reportes en carpetas separadas por tipo.
    archivos = (glob.glob(os.path.join(carpeta, '**', '*.xlsx'), recursive=True) +
                glob.glob(os.path.join(carpeta, '**', '*.xls'), recursive=True))
    # ~$archivo.xlsx son archivos temporales de Excel (quedan si tenias el libro abierto); se ignoran
    archivos = [a for a in archivos if not os.path.basename(a).startswith('~$')]
    clasificados = {k: [] for k in patrones}
    sin_clasificar = []
    for a in archivos:
        nombre = os.path.basename(a).lower()
        encontrado = False
        for tipo, patron in patrones.items():
            if re.search(patron, nombre):
                clasificados[tipo].append(a)
                encontrado = True
                break
        if not encontrado:
            sin_clasificar.append(a)
    return clasificados, sin_clasificar


# ---------------------------------------------------------------------------
# Etapa 1: cargar y unificar
# ---------------------------------------------------------------------------
def cargar_datos(carpeta_entrada, log):
    clasif, sin_clasificar = _clasificar_archivos(carpeta_entrada)
    if sin_clasificar:
        log(f'⚠ {len(sin_clasificar)} archivo(s) no reconocido(s), se ignoran: '
            f'{[os.path.basename(a) for a in sin_clasificar]}')
    for tipo in ['listado_productos', 'ventas', 'compras', 'movimientos_producto']:
        if not clasif[tipo]:
            raise ValueError(f'No se encontró ningún archivo de tipo "{tipo}" en la carpeta. '
                              f'Revisa que el nombre del archivo del ERP no haya cambiado.')

    # --- listado de productos (el más reciente = foto actual del stock) ---
    listados = []
    for f in clasif['listado_productos']:
        df = _leer_hoja(f)
        df['__archivo_origen'] = os.path.basename(f)
        # FIX: el orden entre listados usa la fecha del nombre del archivo; mtime solo como desempate
        fecha_nom = fecha_desde_nombre(f)
        df['__fecha_archivo'] = fecha_nom
        df['__mtime'] = os.path.getmtime(f)
        listados.append(df)
    # FIX: ya no se hace drop_duplicates('CODIGO BARRAS') aquí: borraba las colisiones antes de que
    # construir_dim_producto pudiera asignar sufijos. La deduplicación se hace allí, después.
    listado = pd.concat(listados, ignore_index=True)
    listado['__mtime'] = listado.groupby(listado['__fecha_archivo'].fillna(pd.Timestamp.min)).ngroup() * 1e10 \
        + listado['__mtime']
    listado = listado.sort_values('__mtime', kind='stable')
    fechas_listado = [x for x in listado['__fecha_archivo'].dropna().unique()]
    listado.attrs['fecha_listado'] = pd.Timestamp(max(fechas_listado)) if fechas_listado else None
    log(f'Listado de productos: {len(listado)} filas en {len(listados)} archivo(s) '
        f'(el más reciente se usa como stock actual)')

    def _cargar(tipo, etiqueta):
        dfs = [_leer_hoja(f) for f in clasif[tipo]]
        crudo = sum(len(d) for d in dfs)
        df = deduplicar_entre_archivos(dfs)
        dup = crudo - len(df)
        log(f'{etiqueta}: {len(df)} filas de {len(dfs)} archivo(s)'
            + (f' ({dup} duplicadas entre exportaciones solapadas, descartadas)' if dup else ''))
        return df

    ventas = _cargar('ventas', 'Ventas')
    compras = _cargar('compras', 'Compras')
    movimientos = _cargar('movimientos_producto', 'Movimientos de almacén')
    return listado, ventas, compras, movimientos


# ---------------------------------------------------------------------------
# Etapa 2: canonicalizar SKU (código de barras + sufijo si colisiona)
# ---------------------------------------------------------------------------
def construir_dim_producto(listado, log):
    df = listado.copy()
    if '__mtime' in df.columns:
        df = df.sort_values('__mtime', kind='stable')
    df['CODIGO BARRAS'] = df['CODIGO BARRAS'].map(normalizar_codigo)
    df['PRODUCTO'] = df['PRODUCTO'].astype(str).str.strip()

    # Detectar colisiones: mismo código de barras, nombres de producto distintos
    colisiones = 0
    sufijos = {}
    sku_final = []
    vistos = {}
    for _, row in df.iterrows():
        bc = row['CODIGO BARRAS']
        nombre = row['PRODUCTO']
        if bc not in vistos:
            vistos[bc] = [nombre]
            sku_final.append(bc)
        else:
            if nombre in vistos[bc]:
                idx = vistos[bc].index(nombre)
                sku_final.append(bc if idx == 0 else f'{bc}-{idx+1}')
            else:
                vistos[bc].append(nombre)
                colisiones += 1
                sku_final.append(f'{bc}-{len(vistos[bc])}')
    df['sku'] = sku_final
    log(f'Códigos de barra con colisión (mismo código, producto distinto): {colisiones}')
    # FIX: deduplicar DESPUÉS de asignar sufijos; si el mismo producto aparece en varios listados
    # se queda la fila del más reciente (df viene ordenado por antigüedad)
    n_antes = len(df)
    df = df.drop_duplicates('sku', keep='last')
    if n_antes > len(df):
        log(f'Listado: {n_antes - len(df)} filas repetidas del mismo producto (se usa la más reciente)')

    dim = pd.DataFrame({
        'sku': df['sku'],
        'codigo_barras': df['CODIGO BARRAS'],
        'nombre': df['PRODUCTO'],
        'categoria': df.get('CATEGORÍA'),
        'subcategoria': df.get('SUBCATEGORÍA'),
        'costo_puesto_almacen_pen': df.get('PRECIO COMPRA'),
        'precio_regular_pen': df.get('PRECIO VENTA'),
        'stock_listado_actual': pd.to_numeric(df.get('STOCK'), errors='coerce'),
    })
    return dim, df[['CODIGO BARRAS', 'PRODUCTO', 'sku']].rename(columns={'CODIGO BARRAS': 'codigo_barras', 'PRODUCTO': 'nombre'})


def construir_mapas(dim, mapa_nombre_df):
    """Mapas para asignar SKU a ventas, compras y movimientos.

    Returns:
        mapa_bc_a_sku: código de barras → SKU base (el primero registrado con ese código).
        mapa_bc_nombre_a_sku: (código, nombre normalizado) → SKU, para desambiguar colisiones.
        mapa_nombre_a_sku: nombre normalizado → SKU.
    """
    mapa_bc_a_sku, mapa_bc_nombre_a_sku, mapa_nombre_a_sku = {}, {}, {}
    for bc, nombre, sku in zip(mapa_nombre_df['codigo_barras'], mapa_nombre_df['nombre'], mapa_nombre_df['sku']):
        key = _normalizar_nombre(nombre)
        # FIX: antes dict(zip(...)) dejaba el ÚLTIMO sufijo y todas las ventas del código caían ahí
        if bc not in mapa_bc_a_sku or sku == bc:
            mapa_bc_a_sku[bc] = sku
        mapa_bc_nombre_a_sku[(bc, key)] = sku
        mapa_nombre_a_sku.setdefault(key, sku)
    return mapa_bc_a_sku, mapa_bc_nombre_a_sku, mapa_nombre_a_sku


def _mapear_por_codigo_barra(df, col_codigo_barra, mapa_bc_a_sku, log, nombre_fuente,
                             col_nombre=None, mapa_bc_nombre=None):
    df = df.copy()
    df[col_codigo_barra] = df[col_codigo_barra].map(normalizar_codigo)
    df['sku'] = df[col_codigo_barra].map(mapa_bc_a_sku)
    if col_nombre and mapa_bc_nombre and col_nombre in df.columns:
        # FIX: con colisión de código, el nombre del producto decide a qué sufijo va la fila
        por_nombre = pd.Series(list(zip(df[col_codigo_barra], df[col_nombre].map(_normalizar_nombre))),
                               index=df.index).map(mapa_bc_nombre)
        df['sku'] = por_nombre.fillna(df['sku'])
    sin_match = df['sku'].isna().sum()
    if sin_match:
        log(f'  {nombre_fuente}: {sin_match}/{len(df)} filas sin SKU reconocido (código de barra no está en el listado de productos)')
    return df


def _mapear_por_nombre(df, col_nombre, mapa_nombre_a_sku, log, nombre_fuente):
    df = df.copy()
    df['__nombre_norm'] = df[col_nombre].apply(_normalizar_nombre)
    df['sku'] = df['__nombre_norm'].map(mapa_nombre_a_sku)
    sin_match = df['sku'].isna().sum()
    if sin_match:
        log(f'  {nombre_fuente}: {sin_match}/{len(df)} filas sin SKU reconocido (nombre de producto no coincide exactamente con el listado)')
    return df


# ---------------------------------------------------------------------------
# Etapa 3: reconstruir stock diario (ancla = listado más reciente)
# ---------------------------------------------------------------------------
def reconstruir_stock(ventas, compras, movimientos, dim, mapa_bc_a_sku, mapa_bc_nombre_a_sku,
                      mapa_nombre_a_sku, fecha_ancla, log,
                      ventas_son_salidas_de_almacen=False, compras_son_entradas_de_almacen=False):
    """Reconstruye el stock diario hacia atrás desde el stock del listado (fecha_ancla).

    ventas_son_salidas_de_almacen: True si el ERP registra cada venta también como "Salida" de almacén.
        En ese caso las ventas NO se restan del stock (ya lo restan las salidas); siguen siendo la demanda.
    compras_son_entradas_de_almacen: ídem para compras y "Entrada" de almacén.
    fecha_ancla: fecha a la que corresponde el stock del listado. None = última transacción registrada.
    """
    skus = dim['sku'].tolist()

    # ---- ventas: resta stock ----
    ventas = _mapear_por_codigo_barra(ventas, 'CÓDIGO DE BARRA', mapa_bc_a_sku, log, 'Ventas',
                                      col_nombre='PRODUCTO', mapa_bc_nombre=mapa_bc_nombre_a_sku)
    ventas['fecha'] = _parse_fechas(ventas['FECHA/HORA'], ['%d-%m-%Y %H:%M', '%d-%m-%Y %H:%M:%S'], log, 'Ventas')
    ventas['cantidad'] = pd.to_numeric(ventas['CANTIDAD'], errors='coerce').fillna(0)
    ventas['fecha'] = ventas['fecha'].dt.normalize()
    ventas_diarias = ventas.dropna(subset=['sku', 'fecha']).groupby(
        ['sku', 'fecha'])['cantidad'].sum().reset_index()
    ventas_diarias.columns = ['sku', 'fecha', 'mov']
    ventas_diarias['mov'] = -ventas_diarias['mov']

    # ---- compras: suma stock ----
    compras = _mapear_por_codigo_barra(compras, 'COD. BARRA', mapa_bc_a_sku, log, 'Compras',
                                       col_nombre='PRODUCTO', mapa_bc_nombre=mapa_bc_nombre_a_sku)
    compras['fecha'] = _parse_fechas(compras['FECHA'], ['%d-%m-%Y', '%d-%m-%Y %H:%M'], log, 'Compras')
    compras['cantidad'] = pd.to_numeric(compras['CANTIDAD'], errors='coerce').fillna(0)
    compras['fecha'] = compras['fecha'].dt.normalize()
    compras_diarias = compras.dropna(subset=['sku', 'fecha']).groupby(
        ['sku', 'fecha'])['cantidad'].sum().reset_index()
    compras_diarias.columns = ['sku', 'fecha', 'mov']

    # ---- movimientos de almacén (ingresos/salidas/traslados) ----
    # FIX: si el reporte trae código de barras se usa (más confiable); si no, el nombre del producto
    col_bc_mov = next((c for c in COLUMNAS_CODIGO_BARRA if c in movimientos.columns), None)
    if col_bc_mov:
        movimientos = _mapear_por_codigo_barra(movimientos, col_bc_mov, mapa_bc_a_sku, log,
                                               'Movimientos de almacén', col_nombre='PRODUCTO',
                                               mapa_bc_nombre=mapa_bc_nombre_a_sku)
        sin = movimientos['sku'].isna()
        if sin.any() and 'PRODUCTO' in movimientos.columns:
            movimientos.loc[sin, 'sku'] = movimientos.loc[sin, 'PRODUCTO'].map(_normalizar_nombre).map(mapa_nombre_a_sku)
    else:
        movimientos = _mapear_por_nombre(movimientos, 'PRODUCTO', mapa_nombre_a_sku, log, 'Movimientos de almacén')
    movimientos['fecha'] = _parse_fechas(movimientos['FECHA'], ['%d-%m-%Y %I:%M %p', '%d-%m-%Y %H:%M'],
                                         log, 'Movimientos de almacén')
    movimientos['cantidad'] = pd.to_numeric(movimientos['CANTIDAD'], errors='coerce').fillna(0)

    def signo_movimiento(row):
        tipo = str(row.get('TIPO DE MOVIMIENTO', '')).strip()
        origen = str(row.get('ORIGEN', '')).strip()
        destino = str(row.get('DESTINO', '')).strip()
        if tipo == 'Entrada':
            return row['cantidad']
        if tipo in ('Salida', 'Salida de productos'):
            return -row['cantidad']
        if tipo == 'Traslado':
            origen_sj = origen in SAN_JOSE_ALMACENES
            destino_sj = destino in SAN_JOSE_ALMACENES
            if origen_sj and destino_sj:
                return 0.0  # traslado interno San Jose <-> San Jose, neto cero
            if destino_sj and not origen_sj:
                return row['cantidad']
            if origen_sj and not destino_sj:
                return -row['cantidad']
            return 0.0  # traslado fuera de San Jose, no afecta
        return 0.0

    movimientos['mov'] = movimientos.apply(signo_movimiento, axis=1) if len(movimientos) else 0.0
    movimientos['fecha'] = movimientos['fecha'].dt.normalize()
    mov_diarios = movimientos.dropna(subset=['sku', 'fecha']).groupby(
        ['sku', 'fecha'])['mov'].sum().reset_index()
    mov_diarios.columns = ['sku', 'fecha', 'mov']

    # FIX: detectar posible doble descuento (ventas que el ERP también registra como salidas/entradas)
    tipos = movimientos.get('TIPO DE MOVIMIENTO', pd.Series(dtype=str)).astype(str).str.strip()
    u_salidas = movimientos.loc[tipos.isin(['Salida', 'Salida de productos']), 'cantidad'].sum()
    u_entradas = movimientos.loc[tipos == 'Entrada', 'cantidad'].sum()
    u_ventas, u_compras = -ventas_diarias['mov'].sum(), compras_diarias['mov'].sum()
    if not ventas_son_salidas_de_almacen and u_ventas > 0 and u_salidas >= UMBRAL_DOBLE_DESCUENTO * u_ventas:
        log(f'⚠ Las salidas de almacén ({u_salidas:,.0f} u) equivalen al {u_salidas / u_ventas:.0%} de las ventas '
            f'({u_ventas:,.0f} u): es probable que el ERP registre cada venta como salida y el stock se descuente '
            f'dos veces. Revisa el parámetro ventas_son_salidas_de_almacen en preve_config.json.')
    if not compras_son_entradas_de_almacen and u_compras > 0 and u_entradas >= UMBRAL_DOBLE_DESCUENTO * u_compras:
        log(f'⚠ Las entradas de almacén ({u_entradas:,.0f} u) equivalen al {u_entradas / u_compras:.0%} de las '
            f'compras ({u_compras:,.0f} u): es probable que cada compra genere una entrada. Revisa el parámetro '
            f'compras_son_entradas_de_almacen en preve_config.json.')

    # ---- unir todos los movimientos netos diarios ----
    # FIX: según la configuración de la empresa, no se cuenta dos veces lo que el ERP ya registra en almacén
    partes = [mov_diarios]
    if not ventas_son_salidas_de_almacen:
        partes.append(ventas_diarias)
    if not compras_son_entradas_de_almacen:
        partes.append(compras_diarias)
    todos = pd.concat(partes, ignore_index=True)
    neto = todos.groupby(['sku', 'fecha'])['mov'].sum().reset_index()

    # FIX: la fecha ancla viene de los datos (nombre del listado o última transacción), no del mtime
    ultima_tx = pd.concat([ventas_diarias['fecha'], compras_diarias['fecha'], mov_diarios['fecha']]).max()
    if fecha_ancla is None or pd.isna(fecha_ancla):
        fecha_ancla = ultima_tx
        log(f'Fecha ancla del stock = última transacción registrada: {pd.Timestamp(fecha_ancla).date()}')
    fecha_ancla = pd.Timestamp(fecha_ancla).normalize()
    posteriores = int((neto['fecha'] > fecha_ancla).sum())
    if posteriores:
        log(f'⚠ {posteriores} movimientos diarios son posteriores a la fecha del listado ({fecha_ancla.date()}) '
            f'y no se usan para el stock: exporta un listado más reciente.')
    # el rango cubre también el período de ventas aunque no se resten del stock
    fecha_inicio = min(neto['fecha'].min(), ventas_diarias['fecha'].min())
    fechas = pd.date_range(fecha_inicio, fecha_ancla, freq='D')
    tabla = neto.pivot(index='fecha', columns='sku', values='mov').reindex(fechas, fill_value=0)
    tabla = tabla.reindex(columns=skus, fill_value=0)

    anclaje = dim.set_index('sku')['stock_listado_actual'].reindex(skus).fillna(0)
    stock = pd.DataFrame(index=tabla.index, columns=skus, dtype=float)
    stock.loc[fecha_ancla] = anclaje.values
    fechas_asc = list(tabla.index)
    idx_ancla = fechas_asc.index(fecha_ancla) if fecha_ancla in fechas_asc else len(fechas_asc) - 1
    actual = stock.loc[stock.index[idx_ancla]].copy()
    for i in range(idx_ancla, 0, -1):
        f_actual = fechas_asc[i]
        actual = actual - tabla.loc[f_actual]
        stock.loc[fechas_asc[i - 1]] = actual.values

    n_negativos = (stock < 0).sum()
    skus_con_negativo = int((n_negativos > 0).sum())
    log(f'SKU con stock negativo en algún punto del histórico reconstruido (inconsistencia del ERP): {skus_con_negativo}')

    return stock, ventas_diarias, n_negativos


# ---------------------------------------------------------------------------
# Etapa 4: panel semanal + segmentación ADI/CV2 + clase ABC
# ---------------------------------------------------------------------------
def construir_panel_y_segmentacion(stock, ventas_diarias, dim, log):
    ventas_diarias = ventas_diarias.copy()
    ventas_diarias['fecha'] = pd.to_datetime(ventas_diarias['fecha'])
    ventas_diarias['unidades'] = -ventas_diarias['mov']
    ventas_diarias = ventas_diarias[ventas_diarias['unidades'] >= 0]

    fecha_ini = ventas_diarias['fecha'].min()
    fecha_fin = ventas_diarias['fecha'].max()
    semanas = pd.date_range(fecha_ini, fecha_fin, freq='W-MON')

    ventas_diarias['semana'] = ventas_diarias['fecha'].dt.to_period('W-SUN').apply(lambda p: p.start_time)
    panel = ventas_diarias.groupby(['sku', 'semana'])['unidades'].sum().reset_index()
    panel.columns = ['sku', 'semana_inicio', 'unidades_venta_neta']

    # completar semanas faltantes por sku con 0
    todas_semanas = panel['semana_inicio'].unique()
    idx = pd.MultiIndex.from_product([dim['sku'].unique(), sorted(todas_semanas)], names=['sku', 'semana_inicio'])
    panel_full = panel.set_index(['sku', 'semana_inicio']).reindex(idx, fill_value=0).reset_index()

    # segmentación ADI / CV2 (sobre semanas con venta > 0)
    resumen = []
    primera_venta = ventas_diarias.groupby('sku')['fecha'].min()
    for sku, grupo in panel_full.groupby('sku'):
        grupo = grupo.sort_values('semana_inicio')
        fecha_1ra = primera_venta.get(sku)

        # Solo se mide ADI/CV2 sobre la vida real del producto (desde su primera
        # venta en adelante), nunca sobre semanas previas a que el producto existiera.
        if pd.notna(fecha_1ra):
            grupo = grupo[grupo['semana_inicio'] >= pd.Timestamp(fecha_1ra).normalize() - pd.Timedelta(days=7)]
        serie = grupo['unidades_venta_neta'].values
        n_semanas_obs = len(serie)
        con_venta = serie[serie > 0]
        n_con_venta = len(con_venta)

        semanas_desde_lanz = (
            (pd.Timestamp(fecha_fin) - fecha_1ra).days / 7.0 if pd.notna(fecha_1ra) else np.nan
        )

        if pd.isna(fecha_1ra) or semanas_desde_lanz < SEMANAS_LANZAMIENTO:
            modelo, segmento, adi, cv2 = 'CatBoost', 'Lanzamiento nuevo (<13 sem.)', np.nan, np.nan
        elif n_con_venta < 2:
            modelo, segmento, adi, cv2 = 'Croston-TSB', 'Sin historia suficiente', np.nan, np.nan
        else:
            adi = n_semanas_obs / n_con_venta
            cv2 = (np.std(con_venta) / np.mean(con_venta)) ** 2 if np.mean(con_venta) > 0 else np.nan
            if adi <= ADI_UMBRAL and cv2 <= CV2_UMBRAL:
                modelo, segmento = 'LightGBM', 'Regular / alta rotación'
            else:
                modelo, segmento = 'Croston-TSB', 'Intermitente'

        resumen.append({'sku': sku, 'adi': adi, 'cv2': cv2, 'semanas_observables': n_semanas_obs,
                         'semanas_con_venta': n_con_venta, 'segmento': segmento, 'modelo_asignado': modelo})

    segmentacion = pd.DataFrame(resumen)

    # clase ABC por margen real
    dimx = dim.set_index('sku')
    ventas_tot = ventas_diarias.groupby('sku')['unidades'].sum()
    margen = []
    for sku in dimx.index:
        costo = dimx.loc[sku, 'costo_puesto_almacen_pen']
        precio = dimx.loc[sku, 'precio_regular_pen']
        unid = ventas_tot.get(sku, 0)
        if pd.isna(costo) or pd.isna(precio):
            margen.append(np.nan)
        else:
            margen.append((precio / 1.18 - costo) * unid)
    dimx['margen_total'] = margen
    dimx_sorted = dimx.sort_values('margen_total', ascending=False)
    dimx_sorted['margen_total'] = dimx_sorted['margen_total'].fillna(0)
    total_margen = dimx_sorted['margen_total'].clip(lower=0).sum()
    dimx_sorted['cum_pct'] = dimx_sorted['margen_total'].clip(lower=0).cumsum() / max(total_margen, 1)
    dimx_sorted['clase_abc'] = np.where(dimx_sorted['cum_pct'] <= 0.8, 'A',
                                 np.where(dimx_sorted['cum_pct'] <= 0.95, 'B', 'C'))

    segmentacion = segmentacion.merge(dimx_sorted[['margen_total', 'clase_abc']].reset_index(), on='sku', how='left')
    segmentacion['clase_abc'] = segmentacion['clase_abc'].fillna('C')

    log(f'Segmentación: LightGBM={sum(segmentacion.modelo_asignado=="LightGBM")}, '
        f'CatBoost={sum(segmentacion.modelo_asignado=="CatBoost")}, '
        f'Croston-TSB={sum(segmentacion.modelo_asignado=="Croston-TSB")}')

    return panel_full, segmentacion, fecha_fin


# ---------------------------------------------------------------------------
# Etapa 5: entrenar los 3 modelos y pronosticar demanda a 141 días
# ---------------------------------------------------------------------------
VENTANAS_LGB = (4, 8, 13, 26, 52)


def construir_target_horizonte(serie, h):
    """Suma de las próximas h semanas (t+1 … t+h), sin incluir la semana t.

    rolling(h).sum() en t+h suma x[t+1 … t+h]; al desplazarlo -h queda en la fila t. Es equivalente a
    shift(-h).rolling(h) pero no pierde las primeras h-1 semanas. Las últimas h semanas no tienen un
    futuro completo y quedan en NaN (no se entrena con targets parciales).
    """
    # FIX: antes era shift(-1).rolling(21, min_periods=10): sumaba t-19 … t+1 (casi todo pasado,
    # solapado con las features) y aceptaba targets parciales
    return serie.rolling(h, min_periods=h).sum().shift(-h)


def construir_features_lgb(sub):
    """Ventas acumuladas de las últimas w semanas, INCLUYENDO la semana t (la última conocida)."""
    # FIX: antes se usaba shift(1): la semana más reciente nunca entraba en la predicción
    sub = sub.sort_values(['sku', 'semana_inicio']).copy()
    for w in VENTANAS_LGB:
        sub[f'ventas_{w}s'] = sub.groupby('sku')['unidades_venta_neta'].transform(
            lambda x: x.rolling(w, min_periods=1).sum())
    return sub, [f'ventas_{w}s' for w in VENTANAS_LGB]


def entrenar_y_pronosticar(panel_full, segmentacion, dim, fecha_fin, log):
    import lightgbm as lgb
    from catboost import CatBoostRegressor
    from statsforecast.models import TSB

    panel_full = panel_full.merge(segmentacion[['sku', 'modelo_asignado']], on='sku', how='left')
    resultados = []

    # ---------- Croston-TSB (demanda intermitente) ----------
    skus_croston = segmentacion[segmentacion.modelo_asignado == 'Croston-TSB']['sku'].tolist()
    log(f'Entrenando Croston-TSB para {len(skus_croston)} SKU...')
    for sku in skus_croston:
        serie = panel_full[panel_full.sku == sku].sort_values('semana_inicio')['unidades_venta_neta'].values.astype(float)
        if len(serie) < 4 or serie.sum() == 0:
            pred, std = 0.0, 0.0
        else:
            try:
                m = TSB(alpha_d=0.1, alpha_p=0.1)
                m.fit(serie)
                fc = m.predict(h=HORIZONTE_SEMANAS_TARGET)
                pred = float(np.mean(fc['mean']))
                std = float(np.std(serie))
            except (ValueError, ZeroDivisionError, FloatingPointError, KeyError) as e:
                # FIX: antes except Exception silencioso; ahora se registra qué SKU cae al promedio simple
                logger.warning('Croston-TSB falló para %s (%s); se usa el promedio simple', sku, e)
                pred, std = float(np.mean(serie)), float(np.std(serie))
        p50 = pred * HORIZONTE_SEMANAS
        p10 = max(0.0, pred - 1.2816 * std) * HORIZONTE_SEMANAS
        p90 = (pred + 1.2816 * std) * HORIZONTE_SEMANAS
        resultados.append({'sku': sku, 'demanda_p10_141d': p10, 'demanda_p50_141d': p50, 'demanda_p90_141d': p90})

    # ---------- LightGBM (regular / alta rotación) ----------
    skus_lgb = segmentacion[segmentacion.modelo_asignado == 'LightGBM']['sku'].tolist()
    log(f'Entrenando LightGBM para {len(skus_lgb)} SKU...')
    if skus_lgb:
        sub, feat_cols = construir_features_lgb(panel_full[panel_full.sku.isin(skus_lgb)])
        h = HORIZONTE_SEMANAS_TARGET
        sub['y_20s'] = sub.groupby('sku')['unidades_venta_neta'].transform(
            lambda x: construir_target_horizonte(x, h))
        train = sub.dropna(subset=feat_cols + ['y_20s'])
        if len(train) > 50:
            model = lgb.LGBMRegressor(n_estimators=200, learning_rate=0.05, max_depth=6,
                                       min_child_samples=10, random_state=42, verbosity=-1)
            model.fit(train[feat_cols], train['y_20s'])
            resid_std = float(np.std(model.predict(train[feat_cols]) - train['y_20s']))
            latest = sub.sort_values('semana_inicio').groupby('sku').tail(1)
            latest = latest.dropna(subset=feat_cols)
            # FIX: el target cubre h semanas enteras; se reescala al horizonte real (20.14 sem)
            # para que LightGBM, Croston y CatBoost expresen la demanda en el mismo horizonte
            escala = HORIZONTE_SEMANAS / h
            resid_std *= escala
            preds = np.clip(model.predict(latest[feat_cols]), 0, None) * escala
            for sku, p50 in zip(latest['sku'], preds):
                resultados.append({'sku': sku, 'demanda_p10_141d': max(0, p50 - 1.2816 * resid_std),
                                    'demanda_p50_141d': p50, 'demanda_p90_141d': p50 + 1.2816 * resid_std})
        else:
            log('  LightGBM: datos insuficientes para entrenar, se omite (no se inventa pronóstico).')

    # ---------- CatBoost (lanzamientos nuevos) ----------
    skus_cb = segmentacion[segmentacion.modelo_asignado == 'CatBoost']['sku'].tolist()
    log(f'Entrenando CatBoost para {len(skus_cb)} SKU...')
    if skus_cb:
        dimx = dim.set_index('sku')
        filas = []
        for sku in skus_cb:
            if sku not in dimx.index:
                continue
            filas.append({
                'sku': sku,
                'categoria': str(dimx.loc[sku, 'categoria']),
                'subcategoria': str(dimx.loc[sku, 'subcategoria']),
                'costo': dimx.loc[sku, 'costo_puesto_almacen_pen'],
                'precio': dimx.loc[sku, 'precio_regular_pen'],
            })
        base = pd.DataFrame(filas)
        ventas_8s = panel_full[panel_full.sku.isin(skus_cb)].sort_values(['sku', 'semana_inicio']).groupby('sku').tail(8)
        y = ventas_8s.groupby('sku')['unidades_venta_neta'].mean().rename('y')
        semanas_con_stock = ventas_8s.groupby('sku').size().rename('n_semanas')
        base = base.merge(y, on='sku', how='left').merge(semanas_con_stock, on='sku', how='left')

        cat_cols = ['categoria', 'subcategoria']
        for c in cat_cols:
            base[c] = base[c].fillna('SIN_DATO')
        num_cols = ['costo', 'precio']
        feat_cols = cat_cols + num_cols
        train = base.dropna(subset=num_cols + ['y'])
        train = train[train['n_semanas'].fillna(0) >= 4]
        if len(train) > 30:
            model = CatBoostRegressor(iterations=200, learning_rate=0.05, depth=6, cat_features=cat_cols,
                                       random_seed=42, verbose=False)
            model.fit(train[feat_cols], train['y'])
            resid_std = float(np.std(model.predict(train[feat_cols]) - train['y']))
            pred_base = base.dropna(subset=num_cols).copy()
            pred_base['pred_sem'] = np.clip(model.predict(pred_base[feat_cols]), 0, None)
            for _, r in pred_base.iterrows():
                p50 = r['pred_sem'] * HORIZONTE_SEMANAS
                # Incertidumbre escala con sqrt(semanas) para datos independientes
                sigma_horizonte = resid_std * HORIZONTE_SEMANAS ** 0.5
                p10 = max(0, p50 - 1.2816 * sigma_horizonte)
                p90 = p50 + 1.2816 * sigma_horizonte
                resultados.append({'sku': r['sku'], 'demanda_p10_141d': p10, 'demanda_p50_141d': p50, 'demanda_p90_141d': p90})
        else:
            log('  CatBoost: datos insuficientes para entrenar, se omite (no se inventa pronóstico).')

    pronostico = pd.DataFrame(resultados).drop_duplicates(subset='sku')
    sin_pronostico = segmentacion[~segmentacion.sku.isin(pronostico.sku)]
    log(f'Pronóstico generado para {len(pronostico)} SKU. Sin pronóstico posible: {len(sin_pronostico)}')
    return pronostico, sin_pronostico


# ---------------------------------------------------------------------------
# Etapa 6: recomendación de compra (ROP) + control de sanidad
# ---------------------------------------------------------------------------
def extraer_proveedor_principal(compras_raw, mapa_bc_a_sku):
    """Encuentra el proveedor principal de cada SKU (el que más unidades le ha vendido)."""
    df = compras_raw.copy()
    # Intentar varias columnas de proveedor que Negosy puede exportar
    col_prov = None
    for c in ['PROVEEDOR', 'Proveedor', 'RAZÓN SOCIAL', 'RAZON SOCIAL', 'EMPRESA']:
        if c in df.columns:
            col_prov = c
            break
    if col_prov is None:
        return pd.DataFrame(columns=['sku', 'proveedor_principal'])

    df['COD. BARRA'] = df['COD. BARRA'].astype(str).str.strip()
    df['sku'] = df['COD. BARRA'].map(mapa_bc_a_sku)
    df['cantidad'] = pd.to_numeric(df.get('CANTIDAD', 0), errors='coerce').fillna(0)
    df = df.dropna(subset=['sku'])
    if df.empty:
        return pd.DataFrame(columns=['sku', 'proveedor_principal'])

    # Proveedor que más unidades nos vendió por SKU
    agg = df.groupby(['sku', col_prov])['cantidad'].sum().reset_index()
    agg.columns = ['sku', 'proveedor_principal', '_cant']
    idx = agg.groupby('sku')['_cant'].idxmax()
    return agg.loc[idx, ['sku', 'proveedor_principal']].reset_index(drop=True)


# ---------------------------------------------------------------------------
# ACCIONES DE COMPRA – 7 categorías (Fase 2)
# ---------------------------------------------------------------------------
# Cada producto recibe exactamente UNA acción según su situación.
#
#   RECOMPRAR   – Stock bajo el punto de reorden, hay que reponer.
#   VIGILAR     – Producto nuevo o pronóstico incierto; comprar con cautela.
#   REDUCIR     – Hay sobre-stock moderado; no comprar y dejar que baje.
#   LIQUIDAR    – Sobre-stock severo o demanda casi nula; vender lo que hay.
#   PUBLICITAR  – Buen margen pero demanda declinante; promocionar.
#   NEGOCIAR    – Alto volumen, margen bajo; negociar mejor precio.
#   TRASLADAR   – (reservado Fase 5: balance multi-sucursal)
# ---------------------------------------------------------------------------
def _clasificar_accion(row):
    """Devuelve (accion, motivo) para una fila del DataFrame de recomendación."""
    stock = row['stock_actual']
    rop = row['rop']
    demanda = row['demanda_p50_141d']
    segmento = row.get('segmento', '')
    clase = row.get('clase_abc', 'C')
    margen_pct = row.get('margen_pct', 0)
    tendencia = row.get('tendencia_venta', 0)  # pendiente de venta semanal
    compra_sug = row.get('compra_sugerida_unid', 0)

    # --- 1. Producto nuevo o sin historia suficiente → VIGILAR ---
    if segmento in ('Lanzamiento nuevo (<13 sem.)', 'Sin historia suficiente'):
        if compra_sug > 0:
            return 'VIGILAR', 'Producto nuevo o con poca historia — comprar lo mínimo y monitorear.'
        return 'VIGILAR', 'Producto nuevo sin suficiente historial para recomendar compra.'

    # --- 2. Demanda prácticamente nula + stock alto → LIQUIDAR ---
    if demanda <= 0 and stock > 0:
        return 'LIQUIDAR', f'Sin demanda proyectada y {stock:.0f} unidades en stock — liquidar.'
    # Stock cubre > 3× el horizonte de demanda → LIQUIDAR
    if demanda > 0 and stock > 3 * demanda:
        return 'LIQUIDAR', (f'Stock ({stock:.0f}) cubre más de 3 veces la demanda '
                            f'esperada ({demanda:.0f}) — liquidar excedente.')

    # --- 3. Sobre-stock moderado (1.5× a 3× demanda) → REDUCIR ---
    if demanda > 0 and stock > 1.5 * demanda:
        return 'REDUCIR', (f'Stock ({stock:.0f}) holgado frente a demanda ({demanda:.0f}) '
                           f'— no comprar, dejar que baje naturalmente.')

    # --- 4. Buen margen pero tendencia declinante → PUBLICITAR ---
    if tendencia < -0.3 and clase in ('A', 'B') and margen_pct > 0.15:
        if compra_sug > 0:
            return 'PUBLICITAR', (f'La demanda está cayendo (tendencia {tendencia:+.1f} u/sem) '
                                  f'pero el margen es bueno ({margen_pct:.0%}) — publicitar para reactivar.')
        return 'PUBLICITAR', (f'Demanda declinante (tendencia {tendencia:+.1f} u/sem), margen bueno '
                              f'({margen_pct:.0%}) — publicitar antes de reabastecer.')

    # --- 5. Alto volumen + margen bajo → NEGOCIAR ---
    if clase == 'A' and 0 < margen_pct < 0.10:
        accion_extra = f' Comprar {compra_sug:.0f} unidades.' if compra_sug > 0 else ''
        return 'NEGOCIAR', (f'Producto de alta rotación (A) pero margen muy bajo ({margen_pct:.0%}) '
                            f'— negociar mejor precio con proveedor.{accion_extra}')

    # --- 6. Necesita reposición → RECOMPRAR ---
    if compra_sug > 0:
        # FIX: el disparador es el nivel objetivo (no el ROP); el ROP indica si ya es urgente
        transito = row.get('stock_en_transito', 0) or 0
        nivel = row.get('nivel_objetivo', rop)
        txt_tr = f' + {transito:.0f} en tránsito' if transito else ''
        urgente = ' Ya está bajo el punto de reorden: es urgente.' if stock + transito <= rop else ''
        return 'RECOMPRAR', (f'Stock ({stock:.0f}{txt_tr}) bajo el nivel objetivo ({nivel:.0f}) '
                             f'— comprar {compra_sug:.0f} unidades.{urgente}')

    # --- 7. Default: stock OK, no hacer nada especial → REDUCIR ---
    return 'REDUCIR', f'Stock ({stock:.0f}) suficiente para el horizonte — no comprar por ahora.'


def cargar_ordenes_abiertas(carpeta):
    """Stock en tránsito por SKU desde ordenes_compra_abiertas.csv (solo estado 'abierta').

    Si el archivo no existe se crea vacío con sus columnas, para que el usuario lo complete.
    """
    ruta = os.path.join(carpeta, NOMBRE_ORDENES_ABIERTAS)
    if not os.path.exists(ruta):
        pd.DataFrame(columns=COLUMNAS_ORDENES).to_csv(ruta, index=False, encoding='utf-8-sig')
        logger.info('Creada plantilla vacía de órdenes abiertas: %s', ruta)
        return pd.Series(dtype=float)
    df = pd.read_csv(ruta, dtype={'sku': str}, encoding='utf-8-sig')
    if df.empty or not {'sku', 'cantidad'} <= set(df.columns):
        return pd.Series(dtype=float)
    if 'estado' in df.columns:
        df = df[df['estado'].fillna('abierta').astype(str).str.strip().str.lower() == 'abierta']
    df['cantidad'] = pd.to_numeric(df['cantidad'], errors='coerce').fillna(0)
    return df.groupby('sku')['cantidad'].sum()


def calcular_recomendacion(pronostico, segmentacion, dim, stock, panel_full, fecha_fin, log,
                           compras_raw=None, mapa_bc_a_sku=None,
                           horizonte_semanas_override=None, stock_en_transito=None,
                           lead_time_dias=LEAD_TIME_DIAS_INT):
    """Calcula ROP, nivel objetivo y cantidad a pedir por SKU.

    ROP (punto de reorden) = demanda durante el lead time + stock de seguridad.
    Nivel objetivo         = demanda del horizonte (revisión + lead time) + stock de seguridad.
    Cantidad a pedir       = max(0, nivel objetivo − stock actual − stock en tránsito).
    stock_en_transito: Series sku → unidades pedidas y aún no recibidas (ver cargar_ordenes_abiertas).
    """
    fecha_stock_actual = stock.index.max()
    stock_actual = stock.loc[fecha_stock_actual].rename('stock_actual').reset_index()
    stock_actual.columns = ['sku', 'stock_actual']
    neg_hist = (stock < 0).sum().rename('dias_con_stock_negativo_historico').reset_index()
    neg_hist.columns = ['sku', 'dias_con_stock_negativo_historico']

    df = pronostico.merge(segmentacion[['sku', 'clase_abc', 'modelo_asignado', 'segmento']], on='sku', how='left')
    df = df.merge(stock_actual, on='sku', how='left')
    df = df.merge(neg_hist, on='sku', how='left')
    df = df.merge(dim[['sku', 'nombre', 'categoria', 'costo_puesto_almacen_pen', 'precio_regular_pen']], on='sku', how='left')

    # --- Reescalar demanda si se usa un horizonte distinto al estándar (141d) ---
    _hz = horizonte_semanas_override or HORIZONTE_SEMANAS
    if horizonte_semanas_override and horizonte_semanas_override != HORIZONTE_SEMANAS:
        factor = horizonte_semanas_override / HORIZONTE_SEMANAS
        for col in ['demanda_p50_141d', 'demanda_p10_141d', 'demanda_p90_141d']:
            if col in df.columns:
                df[col] = (df[col] * factor).round(1)
        log(f'Demanda reescalada al horizonte de {horizonte_semanas_override:.1f} semanas '
            f'({horizonte_semanas_override * 7:.0f} días) — factor {factor:.3f}')

    # --- Margen porcentual por producto ---
    df['margen_pct'] = np.where(
        df['precio_regular_pen'].notna() & df['costo_puesto_almacen_pen'].notna() & (df['precio_regular_pen'] > 0),
        (df['precio_regular_pen'] / 1.18 - df['costo_puesto_almacen_pen']) / (df['precio_regular_pen'] / 1.18),
        0
    )

    # --- Tendencia de venta (pendiente de las últimas 8 semanas) ---
    ult_semanas = panel_full[panel_full['semana_inicio'] > (fecha_fin - pd.Timedelta(weeks=8))]
    if len(ult_semanas) > 0:
        def _pendiente(g):
            if len(g) < 3:
                return 0.0
            x = np.arange(len(g))
            y = g['unidades_venta_neta'].values
            if y.sum() == 0:
                return 0.0
            coef = np.polyfit(x, y, 1)
            return coef[0]
        tendencias = ult_semanas.groupby('sku').apply(_pendiente).rename('tendencia_venta')
        df = df.merge(tendencias, on='sku', how='left')
    else:
        df['tendencia_venta'] = 0.0
    df['tendencia_venta'] = df['tendencia_venta'].fillna(0)

    # --- Stock de seguridad y punto de reorden ---
    df['z'] = df['clase_abc'].map(Z_NIVEL_SERVICIO).fillna(Z_NIVEL_SERVICIO['C'])
    df['sigma_demanda_141d'] = ((df['demanda_p90_141d'] - df['demanda_p50_141d']) / 1.2816).clip(lower=0)
    df['stock_seguridad'] = (df['z'] * df['sigma_demanda_141d']).clip(lower=0)
    # FIX: stock en tránsito real (órdenes abiertas) en vez de 0.0 fijo
    transito = stock_en_transito if stock_en_transito is not None else pd.Series(dtype=float)
    df['stock_en_transito'] = df['sku'].map(transito).fillna(0.0).astype(float)
    # FIX: ROP y nivel objetivo son conceptos distintos (antes "rop" era el nivel objetivo)
    horizonte_dias = _hz * 7.0
    df['demanda_lead_time'] = df['demanda_p50_141d'] * lead_time_dias / horizonte_dias
    df['rop'] = df['demanda_lead_time'] + df['stock_seguridad']
    df['nivel_objetivo'] = df['demanda_p50_141d'] + df['stock_seguridad']
    df['stock_actual'] = df['stock_actual'].fillna(0)
    df['cantidad_a_pedir'] = (df['nivel_objetivo'] - df['stock_actual']
                              - df['stock_en_transito']).clip(lower=0).round(0)
    df['compra_sugerida_unid'] = df['cantidad_a_pedir']      # alias: lo usan la UI, el historial y el informe
    df['costo_total_compra_pen'] = df['compra_sugerida_unid'] * df['costo_puesto_almacen_pen']
    df['dato_incompleto'] = df['costo_puesto_almacen_pen'].isna() | df['precio_regular_pen'].isna()

    # --- Control de sanidad ---
    max_hist = panel_full.groupby('sku')['unidades_venta_neta'].max().rename('venta_semanal_maxima_historica')
    df = df.merge(max_hist, on='sku', how='left')
    df['limite_sanidad'] = df['venta_semanal_maxima_historica'].fillna(0) * 25
    df['revisar_antes_de_comprar'] = (df['compra_sugerida_unid'] > df['limite_sanidad']) & (df['compra_sugerida_unid'] > 50)

    # --- Clasificar acción (7 categorías) ---
    acciones = df.apply(_clasificar_accion, axis=1, result_type='expand')
    acciones.columns = ['accion', 'motivo_accion']
    df = pd.concat([df, acciones], axis=1)

    # Mantener 'decision' por retrocompatibilidad
    df['decision'] = np.where(df['accion'] == 'RECOMPRAR', 'COMPRAR', 'NO COMPRAR')

    # --- Proveedor principal (si se pasaron los datos de compras) ---
    if compras_raw is not None and mapa_bc_a_sku is not None:
        proveedores = extraer_proveedor_principal(compras_raw, mapa_bc_a_sku)
        df = df.merge(proveedores, on='sku', how='left')
    else:
        df['proveedor_principal'] = ''

    resumen_acciones = df['accion'].value_counts().to_dict()
    log('Acciones: ' + ' | '.join(f'{k}: {v}' for k, v in sorted(resumen_acciones.items())))
    log(f'Inversión sugerida (RECOMPRAR): S/ {df.loc[df.accion=="RECOMPRAR", "costo_total_compra_pen"].sum():,.0f} | '
        f'A revisar manualmente: {df.revisar_antes_de_comprar.sum()}')
    return df.sort_values('costo_total_compra_pen', ascending=False)
