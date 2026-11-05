# -*- coding: utf-8 -*-
"""Orquesta el pipeline completo y genera el Excel + dashboard finales."""
import os
import json
import datetime
import pandas as pd

from preve_pipeline import (
    cargar_datos, construir_dim_producto, reconstruir_stock,
    construir_panel_y_segmentacion, entrenar_y_pronosticar, calcular_recomendacion,
    extraer_proveedor_principal, _normalizar_nombre,
    HORIZONTE_SEMANAS_INT, HORIZONTE_SEMANAS_LOCAL,
)
from preve_ciclo import (
    registrar_importacion, resumen_ciclo_texto,
    HORIZONTE_LOCAL, LEAD_TIME_LOCAL,
)
from preve_validacion import generar_scorecard
from preve_historial import registrar_ejecucion
from preve_simulacion import simular, generar_excel_simulacion

# Sucursales conocidas de Yuju Store
SUCURSALES = {
    'San José': {'Tienda Juliaca', 'Almacén Juliaca', 'Almacen Juliaca'},
    'Ayaviri':  {'Tienda Ayaviri', 'Almacén Ayaviri', 'Almacen Ayaviri'},
    'Santa Lucía': {'Tienda Santa Lucía', 'Tienda Santa Lucia',
                    'Almacén Santa Lucía', 'Almacen Santa Lucia'},
}


def ejecutar_pipeline(carpeta_entrada, carpeta_salida, progreso=print,
                      sucursal='San José'):
    """Pipeline principal: genera reporte internacional + reporte local + ciclo."""
    os.makedirs(carpeta_salida, exist_ok=True)

    # ── Registrar importación en el ciclo ──
    progreso('Registrando importación en el ciclo de compras...')
    info_ciclo = registrar_importacion(carpeta_salida)
    etiqueta = info_ciclo['etiqueta']
    progreso(resumen_ciclo_texto(info_ciclo))

    progreso('Paso 1/7 — Cargando tus reportes del ERP...')
    listado, ventas, compras, movimientos = cargar_datos(carpeta_entrada, progreso)

    progreso('Paso 2/7 — Identificando productos y códigos de barra...')
    dim, mapa_nombre_df = construir_dim_producto(listado, progreso)
    mapa_bc_a_sku = dict(zip(dim['codigo_barras'], dim['sku']))
    mapa_nombre_a_sku = {}
    for _, r in mapa_nombre_df.iterrows():
        key = _normalizar_nombre(r['nombre'])
        mapa_nombre_a_sku.setdefault(key, r['sku'])

    # La fecha ancla es la fecha de modificación del listado de productos más reciente,
    # NO la fecha de hoy (el stock del listado refleja ese momento, no hoy).
    import glob as _glob
    _listados = _glob.glob(os.path.join(carpeta_entrada, '**', '*listado*productos*'), recursive=True)
    _listados += _glob.glob(os.path.join(carpeta_entrada, '**', '*Listado*Productos*'), recursive=True)
    _listados = [f for f in _listados if f.endswith(('.xlsx', '.xls')) and not os.path.basename(f).startswith('~$')]
    if _listados:
        _mas_reciente = max(_listados, key=os.path.getmtime)
        fecha_ancla = pd.Timestamp(datetime.datetime.fromtimestamp(os.path.getmtime(_mas_reciente)))
        progreso(f'Fecha ancla (mtime del listado más reciente): {fecha_ancla.strftime("%Y-%m-%d %H:%M")}')
    else:
        fecha_ancla = pd.Timestamp(datetime.date.today())
        progreso(f'⚠ No se encontró archivo de listado para determinar fecha ancla, usando hoy: {fecha_ancla.strftime("%Y-%m-%d")}')

    progreso('Paso 3/7 — Reconstruyendo el stock diario histórico...')
    stock, ventas_diarias, _ = reconstruir_stock(
        ventas, compras, movimientos, dim, mapa_bc_a_sku, mapa_nombre_a_sku, fecha_ancla, progreso)

    progreso('Paso 4/7 — Construyendo el panel semanal y segmentando productos...')
    panel_full, segmentacion, fecha_fin = construir_panel_y_segmentacion(stock, ventas_diarias, dim, progreso)

    # Excel rápido de clasificación: ya se puede revisar mientras los modelos siguen entrenando
    try:
        _generar_clasificacion(segmentacion, dim, stock, carpeta_salida, progreso)
    except Exception as e:
        progreso(f'⚠ No se pudo generar el Excel de clasificación: {e}')

    progreso('Paso 5/7 — Entrenando los 3 modelos de pronóstico (puede tardar unos minutos)...')
    pronostico, sin_pronostico = entrenar_y_pronosticar(panel_full, segmentacion, dim, fecha_fin, progreso)

    # ══════════════════════════════════════════════════════════════════
    # REPORTE INTERNACIONAL (horizonte 141 días)
    # ══════════════════════════════════════════════════════════════════
    progreso(f'Paso 6/7 — Reporte INTERNACIONAL ({etiqueta}): calculando compras a 141 días...')
    rec_int = calcular_recomendacion(pronostico, segmentacion, dim, stock, panel_full, fecha_fin, progreso,
                                     compras_raw=compras, mapa_bc_a_sku=mapa_bc_a_sku)

    progreso(f'Generando archivos del reporte internacional ({etiqueta})...')
    _generar_excel(rec_int, sin_pronostico, carpeta_salida, progreso,
                   etiqueta=etiqueta, info_ciclo=info_ciclo)
    _generar_notas_compra_proveedor(rec_int, carpeta_salida, progreso, etiqueta=etiqueta)
    _generar_dashboard(rec_int, sin_pronostico, carpeta_salida, progreso,
                       etiqueta=etiqueta, info_ciclo=info_ciclo)

    rec_int.to_csv(os.path.join(carpeta_salida, 'recomendacion_compra.csv'), index=False)
    sin_pronostico.to_csv(os.path.join(carpeta_salida, 'skus_sin_pronostico.csv'), index=False)

    # ══════════════════════════════════════════════════════════════════
    # REPORTE LOCAL (horizonte 40 días)
    # ══════════════════════════════════════════════════════════════════
    progreso('Paso 7/7 — Reporte LOCAL: calculando compras a 40 días para proveedores nacionales...')
    rec_local = calcular_recomendacion(pronostico, segmentacion, dim, stock, panel_full, fecha_fin, progreso,
                                       compras_raw=compras, mapa_bc_a_sku=mapa_bc_a_sku,
                                       horizonte_semanas_override=HORIZONTE_SEMANAS_LOCAL)

    # Solo generar nota de compra local para productos que necesitan compra
    _generar_notas_compra_proveedor(rec_local, carpeta_salida, progreso,
                                    etiqueta='LOCAL', nombre_archivo='PREVE_Notas_Compra_Local.xlsx')

    # Guardar CSV local
    rec_local.to_csv(os.path.join(carpeta_salida, 'recomendacion_compra_local.csv'), index=False)

    # ══════════════════════════════════════════════════════════════════
    # VALIDACIÓN AUTOMÁTICA (si existe recomendación anterior)
    # ══════════════════════════════════════════════════════════════════
    _intentar_validacion(rec_int, ventas_diarias, carpeta_salida, progreso)

    # ══════════════════════════════════════════════════════════════════
    # HISTORIAL SQLite
    # ══════════════════════════════════════════════════════════════════
    progreso('Registrando ejecución en el historial SQLite...')
    try:
        scorecard_result = None
        scorecard_path = os.path.join(carpeta_salida, 'PREVE_Scorecard.xlsx')
        if os.path.exists(scorecard_path):
            # Intentar leer el scorecard que _intentar_validacion pudo haber generado
            try:
                import json as _json
                _sc_json = os.path.join(carpeta_salida, 'scorecard_ultimo.json')
                if os.path.exists(_sc_json):
                    with open(_sc_json, 'r') as f:
                        scorecard_result = _json.load(f)
            except Exception:
                pass
        registrar_ejecucion(carpeta_salida, rec_int, info_ciclo=info_ciclo,
                            sucursal=sucursal, scorecard=scorecard_result, log=progreso)
    except Exception as e:
        progreso(f'⚠ No se pudo registrar en historial: {e}')

    # ══════════════════════════════════════════════════════════════════
    # INFORME PARA GERENCIA (Word + PDF)
    # ══════════════════════════════════════════════════════════════════
    progreso('Generando el informe para gerencia (Word y PDF)...')
    try:
        from preve_informe import generar_informe
        generar_informe(carpeta_salida, sucursal, log=progreso)
    except Exception as e:
        progreso(f'⚠ No se pudo generar el informe para gerencia: {e}')

    progreso('═' * 60)
    progreso(f'¡Listo! Reporte {etiqueta} + reporte LOCAL generados en: {carpeta_salida}')
    progreso(f'Sucursal: {sucursal}')
    progreso('═' * 60)
    return rec_int


NOMBRE_CLASIFICACION = 'PREVE_Clasificacion_Productos.xlsx'
GRUPOS_EXPLICACION = [
    ('Regular / alta rotación', 'LightGBM',
     'Se vende con frecuencia y de forma estable (ADI ≤ 1.32 y CV² ≤ 0.49). Es el grupo más predecible.'),
    ('Intermitente', 'Croston-TSB',
     'Se vende de forma esporádica o con cantidades muy variables. El pronóstico es menos preciso.'),
    ('Lanzamiento nuevo (<13 sem.)', 'CatBoost',
     'Producto con menos de 13 semanas de vida: poca historia, se recomienda comprar con cautela.'),
    ('Sin historia suficiente', 'Croston-TSB',
     'Menos de 2 semanas con ventas: no hay base para pronosticar; se marca para vigilar.'),
]


def _generar_clasificacion(segmentacion, dim, stock, carpeta_salida, log):
    """Excel rápido con el grupo de cada producto (se genera antes de entrenar los modelos)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo

    VERDE = '2D7D5F'
    seg = segmentacion.merge(dim[['sku', 'nombre', 'categoria']], on='sku', how='left')
    if len(stock):
        seg['stock_actual'] = seg['sku'].map(stock.loc[stock.index.max()])
    seg['segmento'] = seg['segmento'].fillna('')
    orden_abc = {'A': 0, 'B': 1, 'C': 2}
    seg = seg.assign(_o=seg['clase_abc'].map(orden_abc)).sort_values(['_o', 'margen_total'],
                                                                       ascending=[True, False])
    cols = [('sku', 'SKU (código de barra)'), ('nombre', 'Producto'), ('categoria', 'Categoría'),
            ('segmento', 'Grupo'), ('modelo_asignado', 'Modelo de pronóstico'), ('clase_abc', 'Clase ABC'),
            ('adi', 'ADI'), ('cv2', 'CV²'), ('semanas_observables', 'Semanas observadas'),
            ('semanas_con_venta', 'Semanas con venta'), ('stock_actual', 'Stock actual (reconstruido)'),
            ('margen_total', 'Margen acumulado (S/)')]
    cols = [(c, t) for c, t in cols if c in seg.columns]
    datos = seg[[c for c, _ in cols]].copy()
    for c in ('adi', 'cv2', 'margen_total'):
        if c in datos.columns:
            datos[c] = datos[c].round(2)

    encabezado = dict(font=Font(bold=True, color='FFFFFF'), fill=PatternFill('solid', fgColor=VERDE))
    wb = Workbook()
    ws = wb.active
    ws.title = 'Resumen'
    ws['A1'] = f'PREVÉ · Clasificación de productos ({len(seg):,} productos)'
    ws['A1'].font = Font(size=15, bold=True, color='FFFFFF')
    ws['A1'].fill = PatternFill('solid', fgColor=VERDE)
    ws.merge_cells('A1:E1')
    ws['A2'] = ('Vista previa generada antes de entrenar los modelos de pronóstico. '
                'La recomendación de compra llega al terminar el análisis.')
    ws['A2'].font = Font(italic=True, color='5E6F66')
    ws.merge_cells('A2:E2')
    r = 4
    for j, h in enumerate(['Grupo', 'Productos', '% del catálogo', 'Modelo', 'Qué significa'], 1):
        c = ws.cell(row=r, column=j, value=h)
        c.font, c.fill = encabezado['font'], encabezado['fill']
    total = max(len(seg), 1)
    for nombre, modelo, desc in GRUPOS_EXPLICACION:
        r += 1
        n = int((seg['segmento'] == nombre).sum())
        ws.cell(row=r, column=1, value=nombre)
        ws.cell(row=r, column=2, value=n)
        ws.cell(row=r, column=3, value=round(n / total, 3)).number_format = '0.0%'
        ws.cell(row=r, column=4, value=modelo)
        ws.cell(row=r, column=5, value=desc).alignment = Alignment(wrap_text=True, vertical='top')
    r += 2
    ws.cell(row=r, column=1, value='Cruce Grupo × Clase ABC').font = Font(bold=True)
    r += 1
    for j, h in enumerate(['Grupo', 'Clase A', 'Clase B', 'Clase C'], 1):
        c = ws.cell(row=r, column=j, value=h)
        c.font, c.fill = encabezado['font'], encabezado['fill']
    for nombre, _, _ in GRUPOS_EXPLICACION:
        r += 1
        ws.cell(row=r, column=1, value=nombre)
        for j, cl in enumerate(('A', 'B', 'C'), 2):
            ws.cell(row=r, column=j, value=int(((seg['segmento'] == nombre) & (seg['clase_abc'] == cl)).sum()))
    r += 2
    ws.cell(row=r, column=1, value='Clase A = productos que generan ~80% del margen; B = siguiente 15%; '
                                   'C = 5% restante.').font = Font(italic=True, color='5E6F66')
    for col, w in zip('ABCDE', (32, 12, 14, 14, 90)):
        ws.column_dimensions[col].width = w

    ws2 = wb.create_sheet('Productos')
    for j, (_, t) in enumerate(cols, 1):
        c = ws2.cell(row=1, column=j, value=t)
        c.font, c.fill = encabezado['font'], encabezado['fill']
        c.alignment = Alignment(horizontal='center', wrap_text=True)
    for i, fila in enumerate(datos.itertuples(index=False), start=2):
        for j, v in enumerate(fila, 1):
            ws2.cell(row=i, column=j, value=None if pd.isna(v) else v)
    ws2.add_table(Table(displayName='TablaClasificacion',
                        ref=f'A1:{get_column_letter(len(cols))}{len(datos) + 1}',
                        tableStyleInfo=TableStyleInfo(name='TableStyleLight9', showRowStripes=True)))
    for j, (c, t) in enumerate(cols, 1):
        ws2.column_dimensions[get_column_letter(j)].width = 44 if c == 'nombre' else max(13, min(30, len(t) + 4))
    ws2.freeze_panes = 'C2'

    ruta = os.path.join(carpeta_salida, NOMBRE_CLASIFICACION)
    wb.save(ruta)
    log(f'Excel de clasificación listo: {ruta}')


def ejecutar_pipeline_multi_sucursal(carpeta_entrada, carpeta_salida, progreso=print):
    """Ejecuta el pipeline para todas las sucursales que tengan datos.

    Busca subcarpetas con el nombre de cada sucursal dentro de carpeta_entrada,
    o ejecuta para San José si no hay subcarpetas (backward-compatible).
    """
    import os
    subcarpetas = [d for d in os.listdir(carpeta_entrada)
                   if os.path.isdir(os.path.join(carpeta_entrada, d))]

    sucursales_encontradas = {}
    for nombre_sucursal, almacenes in SUCURSALES.items():
        # Buscar subcarpeta que coincida
        for sub in subcarpetas:
            if sub.lower().replace(' ', '') in [
                nombre_sucursal.lower().replace(' ', ''),
                *[a.lower().replace(' ', '') for a in almacenes]
            ]:
                sucursales_encontradas[nombre_sucursal] = os.path.join(carpeta_entrada, sub)
                break

    if not sucursales_encontradas:
        # Sin subcarpetas → asumir que todo es San José (backward-compatible)
        progreso('Sin subcarpetas por sucursal — ejecutando como San José.')
        return ejecutar_pipeline(carpeta_entrada, carpeta_salida, progreso, sucursal='San José')

    progreso(f'Multi-sucursal: encontradas {len(sucursales_encontradas)} sucursales.')
    resultados = {}
    for suc_nombre, suc_carpeta in sucursales_encontradas.items():
        progreso(f'\n{"═" * 60}')
        progreso(f'SUCURSAL: {suc_nombre}')
        progreso(f'{"═" * 60}')
        suc_salida = os.path.join(carpeta_salida, suc_nombre.replace(' ', '_'))
        os.makedirs(suc_salida, exist_ok=True)
        try:
            rec = ejecutar_pipeline(suc_carpeta, suc_salida, progreso, sucursal=suc_nombre)
            resultados[suc_nombre] = rec
        except Exception as e:
            progreso(f'⚠ Error en sucursal {suc_nombre}: {e}')
            resultados[suc_nombre] = None

    # Generar reporte consolidado de TRASLADAR
    if len(resultados) > 1:
        _generar_traslados(resultados, carpeta_salida, progreso)

    return resultados


def _generar_traslados(resultados, carpeta_salida, log):
    """Analiza oportunidades de traslado entre sucursales.

    Si un SKU tiene exceso en sucursal A y falta en sucursal B,
    sugiere trasladar de A a B.
    """
    log('\nAnalizando oportunidades de traslado entre sucursales...')
    recs = {s: r for s, r in resultados.items() if r is not None and len(r) > 0}
    if len(recs) < 2:
        log('Se necesitan al menos 2 sucursales con datos para sugerir traslados.')
        return

    traslados = []
    sucursales = list(recs.keys())

    for i, suc_a in enumerate(sucursales):
        for suc_b in sucursales[i+1:]:
            df_a = recs[suc_a]
            df_b = recs[suc_b]
            # SKUs comunes
            skus_comunes = set(df_a['sku']) & set(df_b['sku'])
            for sku in skus_comunes:
                row_a = df_a[df_a['sku'] == sku].iloc[0]
                row_b = df_b[df_b['sku'] == sku].iloc[0]

                exceso_a = row_a['stock_actual'] - row_a.get('rop', row_a.get('demanda_p50_141d', 0))
                falta_b = row_b.get('rop', row_b.get('demanda_p50_141d', 0)) - row_b['stock_actual']

                if exceso_a > 5 and falta_b > 5:
                    trasladar = min(exceso_a, falta_b)
                    traslados.append({
                        'sku': sku,
                        'nombre': row_a.get('nombre', ''),
                        'desde': suc_a,
                        'hacia': suc_b,
                        'cantidad': int(trasladar),
                        'exceso_origen': int(exceso_a),
                        'falta_destino': int(falta_b),
                    })

                exceso_b = row_b['stock_actual'] - row_b.get('rop', row_b.get('demanda_p50_141d', 0))
                falta_a = row_a.get('rop', row_a.get('demanda_p50_141d', 0)) - row_a['stock_actual']

                if exceso_b > 5 and falta_a > 5:
                    trasladar = min(exceso_b, falta_a)
                    traslados.append({
                        'sku': sku,
                        'nombre': row_b.get('nombre', ''),
                        'desde': suc_b,
                        'hacia': suc_a,
                        'cantidad': int(trasladar),
                        'exceso_origen': int(exceso_b),
                        'falta_destino': int(falta_a),
                    })

    if traslados:
        df_traslados = pd.DataFrame(traslados).sort_values('cantidad', ascending=False)
        path = os.path.join(carpeta_salida, 'PREVE_Traslados_Sugeridos.csv')
        df_traslados.to_csv(path, index=False)
        log(f'Traslados sugeridos: {len(df_traslados)} movimientos → {path}')
    else:
        log('No se encontraron oportunidades de traslado entre sucursales.')


def ejecutar_simulacion(carpeta_salida, escenario, progreso=print):
    """Ejecuta una simulación what-if sobre la última recomendación guardada.

    Args:
        carpeta_salida: carpeta con recomendacion_compra.csv.
        escenario: dict con parámetros del escenario (ver preve_simulacion.simular).
        progreso: función de log.

    Returns:
        dict con recomendacion, comparacion, resumen.
    """
    csv_rec = os.path.join(carpeta_salida, 'recomendacion_compra.csv')
    if not os.path.exists(csv_rec):
        progreso('⚠ No se encontró recomendacion_compra.csv — ejecuta el pipeline primero.')
        return None

    rec = pd.read_csv(csv_rec)
    resultado = simular(rec, escenario, log=progreso)
    generar_excel_simulacion(resultado, carpeta_salida, log=progreso)
    return resultado


def _intentar_validacion(rec_actual, ventas_diarias, carpeta_salida, log):
    """Si existe una recomendación anterior guardada, genera el scorecard."""
    csv_anterior = os.path.join(carpeta_salida, 'recomendacion_anterior.csv')
    if not os.path.exists(csv_anterior):
        # Guardar la actual como "anterior" para la próxima vez
        rec_actual.to_csv(csv_anterior, index=False)
        log('Primera ejecución — no hay recomendación anterior para validar.')
        log('La recomendación actual se guardó para comparar en la próxima importación.')
        return

    log('Se encontró recomendación anterior — generando scorecard de validación...')
    try:
        recom_ant = pd.read_csv(csv_anterior)

        # Calcular ventas reales acumuladas desde la última recomendación
        # Usamos ventas_diarias que es un DataFrame con columnas sku + fecha + unidades
        if isinstance(ventas_diarias, pd.DataFrame) and 'sku' in ventas_diarias.columns:
            venta_real = ventas_diarias.groupby('sku')['unidades'].sum().reset_index()
            venta_real.columns = ['sku', 'venta_real']
        elif isinstance(ventas_diarias, pd.DataFrame):
            # ventas_diarias puede ser pivot (sku como columnas, fechas como índice)
            venta_real = ventas_diarias.sum().reset_index()
            venta_real.columns = ['sku', 'venta_real']
        else:
            log('⚠ Formato de ventas_diarias no reconocido — omitiendo validación.')
            rec_actual.to_csv(csv_anterior, index=False)
            return

        resultado = generar_scorecard(carpeta_salida, recom_ant, venta_real, log)
        log(f'Scorecard: {resultado["agregadas"]["calificacion"]} '
            f'(WMAPE {resultado["agregadas"]["wmape"]}%)')
        # Guardar JSON para que el historial lo recoja
        try:
            import json as _json
            with open(os.path.join(carpeta_salida, 'scorecard_ultimo.json'), 'w') as f:
                _json.dump(resultado, f, default=str, ensure_ascii=False)
        except Exception:
            pass

    except Exception as e:
        log(f'⚠ No se pudo generar el scorecard: {e}')

    # Actualizar la recomendación anterior con la actual
    rec_actual.to_csv(csv_anterior, index=False)
    log('Recomendación actual guardada como referencia para la próxima validación.')


def _generar_excel(df, sin_pron, carpeta_salida, log, etiqueta='FINAL', info_ciclo=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.worksheet.table import Table, TableStyleInfo

    ORANGE = '2D7D5F'   # acento verde salvia; el naranja de Yuju queda solo como banda del header de la app
    cols = {
        'sku': 'SKU (codigo de barra)', 'nombre': 'Nombre del producto', 'categoria': 'Categoria',
        'proveedor_principal': 'Proveedor principal',
        'clase_abc': 'Clase ABC (por margen)', 'modelo_asignado': 'Modelo usado',
        'accion': 'ACCIÓN', 'motivo_accion': 'Motivo de la acción',
        'stock_actual': 'Stock actual (reconstruido)', 'demanda_p50_141d': 'Demanda esperada (141 dias)',
        'demanda_p10_141d': 'Demanda minima esperada (P10)', 'demanda_p90_141d': 'Demanda maxima esperada (P90)',
        'stock_seguridad': 'Stock de seguridad', 'rop': 'Punto de reorden (ROP)',
        'compra_sugerida_unid': 'CANTIDAD A COMPRAR',
        'costo_puesto_almacen_pen': 'Costo unitario (S/)', 'costo_total_compra_pen': 'Inversion total (S/)',
        'margen_pct': 'Margen (%)', 'tendencia_venta': 'Tendencia (u/sem)',
        'revisar_antes_de_comprar': 'REVISAR ANTES DE COMPRAR', 'dato_incompleto': 'Dato incompleto',
    }
    available_cols = [c for c in cols.keys() if c in df.columns]
    df2 = df[available_cols].rename(columns=cols)
    for c in ['Demanda esperada (141 dias)', 'Stock de seguridad', 'Punto de reorden (ROP)', 'Inversion total (S/)']:
        if c in df2.columns:
            df2[c] = df2[c].round(1)
    if 'Margen (%)' in df2.columns:
        df2['Margen (%)'] = (df2['Margen (%)'] * 100).round(1)
    if 'Tendencia (u/sem)' in df2.columns:
        df2['Tendencia (u/sem)'] = df2['Tendencia (u/sem)'].round(2)

    wb = Workbook()
    ws = wb.active
    ws.title = 'Recomendacion de compra'
    ws.merge_cells('A1:F1')
    ws['A1'] = f'PREVÉ · Yuju Store — Recomendación de compra [{etiqueta}]'
    ws['A1'].font = Font(size=16, bold=True, color='FFFFFF')
    ws['A1'].fill = PatternFill('solid', fgColor=ORANGE)
    ws.row_dimensions[1].height = 28
    ws.merge_cells('A2:F2')
    ciclo_txt = ''
    if info_ciclo:
        ciclo_txt = f' | Ciclo #{info_ciclo["ciclo_numero"]} día {info_ciclo["dia_ciclo"]}/{105}'
    ws['A2'] = f'Generado: {datetime.date.today().isoformat()} | Reporte: {etiqueta} | Horizonte: 141 dias | SKU evaluados: {len(df2)}{ciclo_txt}'
    ws.append([])
    header_row = 4
    for j, col in enumerate(df2.columns, start=1):
        c = ws.cell(row=header_row, column=j, value=col)
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor=ORANGE)
        c.alignment = Alignment(horizontal='center', wrap_text=True)
    for i, row in enumerate(df2.itertuples(index=False), start=header_row + 1):
        for j, val in enumerate(row, start=1):
            ws.cell(row=i, column=j, value=val)
    last_row = header_row + len(df2)
    last_col = len(df2.columns)
    tab = Table(displayName='TablaRecomendacion', ref=f'A{header_row}:{get_column_letter(last_col)}{last_row}')
    tab.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=True)
    ws.add_table(tab)
    for j, col in enumerate(df2.columns, start=1):
        ws.column_dimensions[get_column_letter(j)].width = max(14, min(40, len(col) + 6))
    ws.freeze_panes = f'A{header_row + 1}'
    if 'ACCIÓN' in df2.columns:
        col_accion = list(df2.columns).index('ACCIÓN') + 1
        rng = f'{get_column_letter(col_accion)}{header_row+1}:{get_column_letter(col_accion)}{last_row}'
        # Colores por acción
        colores_accion = {
            'RECOMPRAR':  ('C6EFCE', '006100'),  # verde
            'VIGILAR':    ('BDD7EE', '1F4E79'),  # azul
            'REDUCIR':    ('FFF2CC', '7F6000'),  # amarillo
            'LIQUIDAR':   ('F4CCCC', '990000'),  # rojo claro
            'PUBLICITAR': ('D9D2E9', '351C75'),  # morado
            'NEGOCIAR':   ('FCE5CD', '783F04'),  # naranja
            'TRASLADAR':  ('D0E0E3', '0C343D'),  # teal
        }
        for accion, (bg, fg) in colores_accion.items():
            ws.conditional_formatting.add(rng, CellIsRule(
                operator='equal', formula=[f'"{accion}"'],
                fill=PatternFill('solid', fgColor=bg), font=Font(color=fg)))

    ws2 = wb.create_sheet('Resumen ejecutivo', 0)
    ws2.merge_cells('A1:C1')
    ws2['A1'] = f'PREVÉ · Resumen ejecutivo [{etiqueta}]'
    ws2['A1'].font = Font(size=16, bold=True, color='FFFFFF')
    ws2['A1'].fill = PatternFill('solid', fgColor=ORANGE)
    # Conteo por acción
    accion_col = 'ACCIÓN' if 'ACCIÓN' in df2.columns else None
    resumen = [
        ('SKU evaluados con pronóstico', len(df2)),
    ]
    if accion_col:
        for accion in ['RECOMPRAR', 'VIGILAR', 'REDUCIR', 'LIQUIDAR', 'PUBLICITAR', 'NEGOCIAR', 'TRASLADAR']:
            n = int((df2[accion_col] == accion).sum())
            if n > 0:
                resumen.append((f'  → {accion}', n))
    inversion = round(float(df2['Inversion total (S/)'].sum()), 2) if 'Inversion total (S/)' in df2.columns else 0
    resumen += [
        ('Inversión total sugerida (S/) [solo RECOMPRAR]',
         round(float(df2.loc[df2.get(accion_col, pd.Series()) == 'RECOMPRAR', 'Inversion total (S/)'].sum()), 2) if accion_col else inversion),
        ('SKU a REVISAR antes de comprar', int(df2['REVISAR ANTES DE COMPRAR'].sum()) if 'REVISAR ANTES DE COMPRAR' in df2.columns else 0),
        ('SKU SIN pronóstico (datos insuficientes)', len(sin_pron)),
        ('Proveedores distintos', int(df2['Proveedor principal'].nunique()) if 'Proveedor principal' in df2.columns else 0),
    ]
    if info_ciclo:
        resumen += [
            ('', ''),
            ('── CICLO DE COMPRAS ──', ''),
            (f'Ciclo #{info_ciclo["ciclo_numero"]}', f'Día {info_ciclo["dia_ciclo"]} de 105'),
            ('Importación #', info_ciclo['importacion_num']),
            ('Tipo de reporte', etiqueta),
            ('Días restantes para pedido', info_ciclo['dias_restantes']),
            ('Fecha límite del pedido', info_ciclo['fecha_limite_pedido']),
        ]
    r = 3
    for label, val in resumen:
        ws2.cell(row=r, column=1, value=label).font = Font(bold=True)
        ws2.cell(row=r, column=2, value=val)
        r += 1
    ws2.column_dimensions['A'].width = 50

    ws3 = wb.create_sheet('SKU sin pronostico')
    if len(sin_pron):
        for j, col in enumerate(sin_pron.columns, start=1):
            c = ws3.cell(row=1, column=j, value=col)
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = PatternFill('solid', fgColor=ORANGE)
        for i, row in enumerate(sin_pron.itertuples(index=False), start=2):
            for j, val in enumerate(row, start=1):
                ws3.cell(row=i, column=j, value=val)

    path = os.path.join(carpeta_salida, 'PREVE_Recomendacion_Compra.xlsx')
    wb.save(path)
    log(f'Excel generado: {path}')


def _generar_notas_compra_proveedor(df, carpeta_salida, log, etiqueta='FINAL',
                                    nombre_archivo='PREVE_Notas_Compra_Proveedor.xlsx'):
    """Genera un Excel con una hoja por proveedor, solo productos con acción RECOMPRAR."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    ORANGE = '2D7D5F'   # acento verde salvia; el naranja de Yuju queda solo como banda del header de la app
    comprar = df[df['accion'] == 'RECOMPRAR'].copy()
    if comprar.empty or 'proveedor_principal' not in comprar.columns:
        log('No hay productos para RECOMPRAR o sin datos de proveedor — se omite nota de compra.')
        return

    comprar['proveedor_principal'] = comprar['proveedor_principal'].fillna('SIN PROVEEDOR')
    proveedores = sorted(comprar['proveedor_principal'].unique())

    wb = Workbook()
    # Hoja resumen con todos los proveedores
    ws_resumen = wb.active
    ws_resumen.title = 'Resumen por proveedor'
    ws_resumen.merge_cells('A1:D1')
    ws_resumen['A1'] = f'PREVÉ · Notas de compra [{etiqueta}] agrupadas por proveedor'
    ws_resumen['A1'].font = Font(size=14, bold=True, color='FFFFFF')
    ws_resumen['A1'].fill = PatternFill('solid', fgColor=ORANGE)
    ws_resumen['A2'] = f'Generado: {datetime.date.today().isoformat()} | Proveedores: {len(proveedores)}'

    headers_res = ['Proveedor', 'SKU a comprar', 'Inversión total (S/)', 'Unidades totales']
    for j, h in enumerate(headers_res, 1):
        c = ws_resumen.cell(row=4, column=j, value=h)
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor=ORANGE)
    row_res = 5

    cols_nota = ['sku', 'nombre', 'categoria', 'compra_sugerida_unid',
                 'costo_puesto_almacen_pen', 'costo_total_compra_pen',
                 'stock_actual', 'demanda_p50_141d', 'motivo_accion']
    headers_nota = ['SKU', 'Producto', 'Categoría', 'Cantidad a comprar',
                    'Costo unit. (S/)', 'Inversión (S/)',
                    'Stock actual', 'Demanda 141d', 'Motivo']

    for prov in proveedores:
        sub = comprar[comprar['proveedor_principal'] == prov].sort_values('costo_total_compra_pen', ascending=False)
        inv = sub['costo_total_compra_pen'].sum()
        unid = sub['compra_sugerida_unid'].sum()

        # Fila resumen
        ws_resumen.cell(row=row_res, column=1, value=prov)
        ws_resumen.cell(row=row_res, column=2, value=len(sub))
        ws_resumen.cell(row=row_res, column=3, value=round(inv, 2))
        ws_resumen.cell(row=row_res, column=4, value=int(unid))
        row_res += 1

        # Hoja detallada por proveedor (nombre truncado a 31 chars para Excel)
        nombre_hoja = prov[:31] if len(prov) > 31 else prov
        # Evitar nombres duplicados
        existing = [ws.title for ws in wb.worksheets]
        if nombre_hoja in existing:
            nombre_hoja = nombre_hoja[:28] + f'_{proveedores.index(prov)}'
        ws = wb.create_sheet(nombre_hoja)
        ws.merge_cells('A1:D1')
        ws['A1'] = f'Nota de compra — {prov}'
        ws['A1'].font = Font(size=12, bold=True, color='FFFFFF')
        ws['A1'].fill = PatternFill('solid', fgColor=ORANGE)
        ws['A2'] = f'Inversión: S/ {inv:,.2f} | SKU: {len(sub)} | Unidades: {int(unid)}'

        available = [c for c in cols_nota if c in sub.columns]
        display_headers = [headers_nota[cols_nota.index(c)] for c in available]
        for j, h in enumerate(display_headers, 1):
            c = ws.cell(row=4, column=j, value=h)
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = PatternFill('solid', fgColor=ORANGE)
            c.alignment = Alignment(horizontal='center', wrap_text=True)
        for i, row in enumerate(sub[available].itertuples(index=False), start=5):
            for j, val in enumerate(row, start=1):
                ws.cell(row=i, column=j, value=val)
        for j in range(1, len(display_headers) + 1):
            ws.column_dimensions[get_column_letter(j)].width = max(14, min(40, len(display_headers[j-1]) + 4))

    # Ajustar anchos resumen
    for j, w in enumerate([40, 16, 22, 18], 1):
        ws_resumen.column_dimensions[get_column_letter(j)].width = w

    path = os.path.join(carpeta_salida, nombre_archivo)
    wb.save(path)
    log(f'Notas de compra [{etiqueta}] por proveedor: {path} ({len(proveedores)} proveedores)')


def _generar_dashboard(df, sin_pron, carpeta_salida, log, etiqueta='FINAL', info_ciclo=None):
    from preve_pipeline import HORIZONTE_SEMANAS

    def _explicacion(row):
        vs = (row['demanda_p50_141d'] / HORIZONTE_SEMANAS) if row['demanda_p50_141d'] else 0
        if row['decision'] == 'COMPRAR':
            txt = (f"Se espera vender ~{vs:.0f} unidades/semana en las próximas ~20 semanas. "
                   f"Tienes {row['stock_actual']:.0f} en stock y necesitas al menos {row['rop']:.0f} "
                   f"para no quedarte sin producto mientras llega el siguiente pedido. "
                   f"Por eso se sugiere comprar {row['compra_sugerida_unid']:.0f}.")
        else:
            txt = (f"Tu stock actual ({row['stock_actual']:.0f}) ya cubre lo que se espera vender "
                   f"en los próximos 141 días, así que no necesitas comprar por ahora.")
        if row.get('revisar_antes_de_comprar'):
            txt += " ⚠ Esta cantidad parece inflada por una venta puntual fuera de lo común — revisa antes de comprar."
        return txt

    df = df.copy()
    df['explicacion'] = df.apply(_explicacion, axis=1)

    cols = ['sku', 'nombre', 'categoria', 'proveedor_principal', 'clase_abc', 'modelo_asignado',
            'accion', 'motivo_accion', 'stock_actual',
            'demanda_p50_141d', 'rop', 'compra_sugerida_unid', 'decision', 'costo_total_compra_pen',
            'margen_pct', 'tendencia_venta',
            'revisar_antes_de_comprar', 'dato_incompleto', 'explicacion']
    d = df[[c for c in cols if c in df.columns]].copy()
    for c in ['stock_actual', 'demanda_p50_141d', 'rop', 'compra_sugerida_unid', 'costo_total_compra_pen']:
        if c in d.columns:
            d[c] = d[c].round(1)
    d = d.fillna('')
    # Conteo por acción
    acciones_conteo = {}
    if 'accion' in df.columns:
        for acc in ['RECOMPRAR', 'VIGILAR', 'REDUCIR', 'LIQUIDAR', 'PUBLICITAR', 'NEGOCIAR', 'TRASLADAR']:
            n = int((df['accion'] == acc).sum())
            if n > 0:
                acciones_conteo[acc.lower()] = n

    data = {
        'rows': d.to_dict(orient='records'),
        'resumen': {
            'total': len(df),
            'comprar': int((df['decision'] == 'COMPRAR').sum()),
            'no_comprar': int((df['decision'] == 'NO COMPRAR').sum()),
            'inversion': float(df.loc[df.get('accion', pd.Series()) == 'RECOMPRAR', 'costo_total_compra_pen'].sum()) if 'accion' in df.columns else float(df['costo_total_compra_pen'].sum()),
            'revisar': int(df['revisar_antes_de_comprar'].sum()),
            'incompleto': int(df['dato_incompleto'].sum()),
            'sin_pronostico': len(sin_pron),
            'acciones': acciones_conteo,
            'etiqueta': etiqueta,
            'ciclo': info_ciclo if info_ciclo else {},
        }
    }

    plantilla = os.path.join(os.path.dirname(__file__), 'dashboard_template.html')
    destino = os.path.join(carpeta_salida, 'dashboard.html')
    with open(plantilla, 'r', encoding='utf-8') as f:
        contenido = f.read()
    # Los datos quedan EMBEBIDOS dentro del propio HTML (no en un .json aparte),
    # asi el archivo funciona con doble clic, sin internet y sin que el navegador
    # bloquee la lectura de un archivo local aparte.
    contenido = contenido.replace('"__PREVE_DATA_JSON__"', json.dumps(data, ensure_ascii=False))
    with open(destino, 'w', encoding='utf-8') as f:
        f.write(contenido)
    log(f'Dashboard generado (autocontenido): {destino}')