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

warnings.filterwarnings('ignore')

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


def _leer_hoja(path, sheet_name=None):
    xls = pd.ExcelFile(path)
    hoja = sheet_name or xls.sheet_names[0]
    fila = _detectar_fila_encabezado(path, hoja)
    df = pd.read_excel(xls, sheet_name=hoja, header=fila)
    df = df.dropna(axis=1, how='all')
    df.columns = [str(c).strip() for c in df.columns]
    return df


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
        df['__mtime'] = os.path.getmtime(f)
        listados.append(df)
    listado = pd.concat(listados, ignore_index=True)
    listado = listado.sort_values('__mtime').drop_duplicates('CODIGO BARRAS', keep='last')
    log(f'Listado de productos: {len(listado)} productos (archivo más reciente usado como stock actual)')

    # --- ventas (todas las que haya, cualquier año) ---
    ventas_list = []
    for f in clasif['ventas']:
        df = _leer_hoja(f, sheet_name=None)
        ventas_list.append(df)
    ventas = pd.concat(ventas_list, ignore_index=True)
    log(f'Ventas: {len(ventas)} filas de {len(clasif["ventas"])} archivo(s)')

    # --- compras ---
    compras_list = [_leer_hoja(f) for f in clasif['compras']]
    compras = pd.concat(compras_list, ignore_index=True)
    log(f'Compras: {len(compras)} filas de {len(clasif["compras"])} archivo(s)')

    # --- movimientos de almacén por producto (ingresos/salidas/traslados) ---
    mov_list = [_leer_hoja(f) for f in clasif['movimientos_producto']]
    movimientos = pd.concat(mov_list, ignore_index=True)
    log(f'Movimientos de almacén: {len(movimientos)} filas de {len(clasif["movimientos_producto"])} archivo(s)')

    return listado, ventas, compras, movimientos


# ---------------------------------------------------------------------------
# Etapa 2: canonicalizar SKU (código de barras + sufijo si colisiona)
# ---------------------------------------------------------------------------
def construir_dim_producto(listado, log):
    df = listado.copy()
    df['CODIGO BARRAS'] = df['CODIGO BARRAS'].astype(str).str.strip()
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


def _mapear_por_codigo_barra(df, col_codigo_barra, mapa_bc_a_sku, log, nombre_fuente):
    df = df.copy()
    df[col_codigo_barra] = df[col_codigo_barra].astype(str).str.strip()
    df['sku'] = df[col_codigo_barra].map(mapa_bc_a_sku)
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
def reconstruir_stock(ventas, compras, movimientos, dim, mapa_bc_a_sku, mapa_nombre_a_sku,
                       fecha_ancla, log):
    skus = dim['sku'].tolist()

    # ---- ventas: resta stock ----
    ventas = _mapear_por_codigo_barra(ventas, 'CÓDIGO DE BARRA', mapa_bc_a_sku, log, 'Ventas')
    ventas['fecha'] = pd.to_datetime(ventas['FECHA/HORA'], format='%d-%m-%Y %H:%M', errors='coerce')
    ventas['fecha'] = ventas['fecha'].fillna(pd.to_datetime(ventas['FECHA/HORA'], dayfirst=True, errors='coerce'))
    ventas['cantidad'] = pd.to_numeric(ventas['CANTIDAD'], errors='coerce').fillna(0)
    ventas['fecha'] = ventas['fecha'].dt.normalize()
    ventas_diarias = ventas.dropna(subset=['sku', 'fecha']).groupby(
        ['sku', 'fecha'])['cantidad'].sum().reset_index()
    ventas_diarias.columns = ['sku', 'fecha', 'mov']
    ventas_diarias['mov'] = -ventas_diarias['mov']

    # ---- compras: suma stock ----
    compras = _mapear_por_codigo_barra(compras, 'COD. BARRA', mapa_bc_a_sku, log, 'Compras')
    compras['fecha'] = pd.to_datetime(compras['FECHA'], format='%d-%m-%Y', errors='coerce')
    compras['cantidad'] = pd.to_numeric(compras['CANTIDAD'], errors='coerce').fillna(0)
    compras['fecha'] = compras['fecha'].dt.normalize()
    compras_diarias = compras.dropna(subset=['sku', 'fecha']).groupby(
        ['sku', 'fecha'])['cantidad'].sum().reset_index()
    compras_diarias.columns = ['sku', 'fecha', 'mov']

    # ---- movimientos de almacén (ingresos/salidas/traslados), por nombre de producto ----
    movimientos = _mapear_por_nombre(movimientos, 'PRODUCTO', mapa_nombre_a_sku, log, 'Movimientos de almacén')
    movimientos['fecha'] = pd.to_datetime(movimientos['FECHA'], format='%d-%m-%Y %I:%M %p', errors='coerce')
    movimientos['fecha'] = movimientos['fecha'].fillna(pd.to_datetime(movimientos['FECHA'], errors='coerce'))
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

    movimientos['mov'] = movimientos.apply(signo_movimiento, axis=1)
    movimientos['fecha'] = movimientos['fecha'].dt.normalize()
    mov_diarios = movimientos.dropna(subset=['sku', 'fecha']).groupby(
        ['sku', 'fecha'])['mov'].sum().reset_index()
    mov_diarios.columns = ['sku', 'fecha', 'mov']

    # ---- unir todos los movimientos netos diarios ----
    todos = pd.concat([ventas_diarias, compras_diarias, mov_diarios], ignore_index=True)
    neto = todos.groupby(['sku', 'fecha'])['mov'].sum().reset_index()

    fecha_ancla = pd.Timestamp(fecha_ancla).normalize()
    fecha_inicio = neto['fecha'].min()
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
                fc = m.predict(h=21)
                pred = float(np.mean(fc['mean']))
                std = float(np.std(serie))
            except Exception:
                pred, std = float(np.mean(serie)), float(np.std(serie))
        p50 = pred * HORIZONTE_SEMANAS
        p10 = max(0.0, pred - 1.2816 * std) * HORIZONTE_SEMANAS
        p90 = (pred + 1.2816 * std) * HORIZONTE_SEMANAS
        resultados.append({'sku': sku, 'demanda_p10_141d': p10, 'demanda_p50_141d': p50, 'demanda_p90_141d': p90})

    # ---------- LightGBM (regular / alta rotación) ----------
    skus_lgb = segmentacion[segmentacion.modelo_asignado == 'LightGBM']['sku'].tolist()
    log(f'Entrenando LightGBM para {len(skus_lgb)} SKU...')
    if skus_lgb:
        sub = panel_full[panel_full.sku.isin(skus_lgb)].sort_values(['sku', 'semana_inicio']).copy()
        for w in [4, 8, 13, 26, 52]:
            sub[f'ventas_{w}s'] = sub.groupby('sku')['unidades_venta_neta'].transform(
                lambda x: x.shift(1).rolling(w, min_periods=1).sum())
        feat_cols = [f'ventas_{w}s' for w in [4, 8, 13, 26, 52]]
        sub['y_20s'] = sub.groupby('sku')['unidades_venta_neta'].transform(
            lambda x: x.shift(-1).rolling(21, min_periods=10).sum())
        train = sub.dropna(subset=feat_cols + ['y_20s'])
        if len(train) > 50:
            model = lgb.LGBMRegressor(n_estimators=200, learning_rate=0.05, max_depth=6,
                                       min_child_samples=10, random_state=42, verbosity=-1)
            model.fit(train[feat_cols], train['y_20s'])
            resid_std = float(np.std(model.predict(train[feat_cols]) - train['y_20s']))
            latest = sub.sort_values('semana_inicio').groupby('sku').tail(1)
            latest = latest.dropna(subset=feat_cols)
            preds = np.clip(model.predict(latest[feat_cols]), 0, None)
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
        return 'RECOMPRAR', (f'Stock ({stock:.0f}) bajo el punto de reorden ({rop:.0f}) '
                             f'— comprar {compra_sug:.0f} unidades.')

    # --- 7. Default: stock OK, no hacer nada especial → REDUCIR ---
    return 'REDUCIR', f'Stock ({stock:.0f}) suficiente para el horizonte — no comprar por ahora.'


def calcular_recomendacion(pronostico, segmentacion, dim, stock, panel_full, fecha_fin, log,
                           compras_raw=None, mapa_bc_a_sku=None,
                           horizonte_semanas_override=None):
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
    df['stock_en_transito'] = 0.0
    df['rop'] = df['demanda_p50_141d'] + df['stock_seguridad']
    df['stock_actual'] = df['stock_actual'].fillna(0)
    df['compra_sugerida_unid'] = (df['rop'] - df['stock_actual'] - df['stock_en_transito']).clip(lower=0).round(0)
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
