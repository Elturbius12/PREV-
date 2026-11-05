# -*- coding: utf-8 -*-
"""Orquesta el pipeline completo y genera el Excel + dashboard finales."""
import os
import json
from sqlalchemy.exc import SQLAlchemyError
import datetime
import pandas as pd

from preve_pipeline import (
    cargar_datos, construir_dim_producto, construir_mapas, reconstruir_stock, aplicar_mapeo_ventas,
    construir_panel_y_segmentacion, entrenar_y_pronosticar, calcular_recomendacion,
    cargar_ordenes_abiertas,
)
from preve_ciclo import registrar_importacion, resumen_ciclo_texto
from preve_config import Config
from preve_validacion import generar_scorecard
from preve_historial import registrar_ejecucion
from preve_simulacion import simular, generar_excel_simulacion
from preve_utils import get_logger, log_info
from database import slug

logger = get_logger('orquestador')

NOMBRE_REC_ANTERIOR = 'recomendacion_anterior.csv'
NOMBRE_REC_ANTERIOR_META = 'recomendacion_anterior_meta.json'
NOMBRE_SCORECARD_JSON = 'scorecard_ultimo.json'
_COLS_DEMANDA = ('demanda_p50_141d', 'demanda_p10_141d', 'demanda_p90_141d')


def _archivos_ciclo_secundario(nombre):
    """(nota de compra, csv) de un ciclo secundario. 'Local' conserva los nombres de siempre."""
    if slug(nombre) == 'local':
        return 'PREVE_Notas_Compra_Local.xlsx', 'recomendacion_compra_local.csv'
    return f'PREVE_Notas_Compra_{slug(nombre)}.xlsx', f'recomendacion_compra_{slug(nombre)}.csv'


def ejecutar_pipeline(carpeta_entrada, carpeta_salida, progreso=log_info,
                      sucursal='San José'):
    """Pipeline principal: reporte del ciclo principal + notas de compra de cada ciclo secundario."""
    os.makedirs(carpeta_salida, exist_ok=True)
    # FIX (Fase 3): todos los parámetros salen de la configuración (antes fijos en el código)
    conf = Config.actual()
    conf.invalidar()
    principal = conf.ciclo_principal
    secundarios = conf.ciclos[1:]
    params = conf.parametros_modelo(sucursal, principal)
    # Fase 5: categorías de acción según la plantilla configurada
    from preve_reglas import cargar_plantilla
    plantilla = cargar_plantilla(conf.get('plantilla_categorias'))
    nombre_p = principal['nombre'].upper()

    # ── Ciclo: vista previa (se confirma recién al terminar con éxito) ──
    # FIX: antes la importación se registraba al inicio aunque el pipeline fallara después
    progreso('Calculando la posición en el ciclo de compras...')
    info_ciclo = registrar_importacion(carpeta_salida, guardar=False, sucursal=sucursal,
                                       duracion_dias=principal['duracion_dias'])
    etiqueta = info_ciclo['etiqueta']
    progreso(resumen_ciclo_texto(info_ciclo))

    if plantilla:
        progreso(f'Plantilla de categorías de acción: {plantilla["nombre"]}')
    progreso('Paso 1/7 — Cargando tus reportes del ERP...')
    listado, ventas, compras, movimientos = cargar_datos(carpeta_entrada, progreso)
    ventas = aplicar_mapeo_ventas(ventas, conf.get('mapeo_columnas_ventas'), progreso)

    progreso('Paso 2/7 — Identificando productos y códigos de barra...')
    dim, mapa_nombre_df = construir_dim_producto(listado, progreso)
    mapa_bc_a_sku, mapa_bc_nombre_a_sku, mapa_nombre_a_sku = construir_mapas(dim, mapa_nombre_df)

    # FIX: la fecha ancla sale de los datos (fecha del nombre del listado; si no hay, la última
    # transacción), ya no del mtime del archivo, que cambia al copiarlo o descomprimirlo
    fecha_ancla = listado.attrs.get('fecha_listado')
    if fecha_ancla is not None:
        progreso(f'Fecha ancla del stock (fecha del listado): {fecha_ancla.strftime("%Y-%m-%d")}')

    progreso('Paso 3/7 — Reconstruyendo el stock diario histórico...')
    stock, ventas_diarias, _ = reconstruir_stock(
        ventas, compras, movimientos, dim, mapa_bc_a_sku, mapa_bc_nombre_a_sku, mapa_nombre_a_sku,
        fecha_ancla, progreso,
        ventas_son_salidas_de_almacen=params.ventas_son_salidas_de_almacen,
        compras_son_entradas_de_almacen=params.compras_son_entradas_de_almacen,
        almacenes_propios=params.almacenes_propios)
    fecha_datos = stock.index.max()
    transito = cargar_ordenes_abiertas(carpeta_salida)
    if len(transito):
        progreso(f'Stock en tránsito: {len(transito)} SKU con órdenes abiertas ({transito.sum():,.0f} u)')

    progreso('Paso 4/7 — Construyendo el panel semanal y segmentando productos...')
    panel_full, segmentacion, fecha_fin = construir_panel_y_segmentacion(stock, ventas_diarias, dim, progreso,
                                                                         params=params)

    # Excel rápido de clasificación: ya se puede revisar mientras los modelos siguen entrenando
    try:
        _generar_clasificacion(segmentacion, dim, stock, carpeta_salida, progreso)
    except (OSError, ValueError, KeyError) as e:
        # FIX: excepciones específicas + traceback en el log (es una salida opcional, no detiene el análisis)
        logger.exception('No se pudo generar el Excel de clasificación')
        progreso(f'⚠ No se pudo generar el Excel de clasificación: {e}')

    progreso('Paso 5/7 — Entrenando los 3 modelos de pronóstico (puede tardar unos minutos)...')
    pronostico, sin_pronostico = entrenar_y_pronosticar(panel_full, segmentacion, dim, fecha_fin, progreso,
                                                        params=params)

    # ══════════════════════════════════════════════════════════════════
    # REPORTE DEL CICLO PRINCIPAL (el de mayor horizonte; en Yuju: internacional, 141 días)
    # ══════════════════════════════════════════════════════════════════
    hz_p = principal['horizonte_pronostico_dias']
    progreso(f'Paso 6/7 — Reporte {nombre_p} ({etiqueta}): calculando compras a {hz_p} días...')
    rec_int = calcular_recomendacion(pronostico, segmentacion, dim, stock, panel_full, fecha_fin, progreso,
                                     compras_raw=compras, mapa_bc_a_sku=mapa_bc_a_sku,
                                     stock_en_transito=transito, params=params, plantilla=plantilla)

    progreso(f'Generando archivos del reporte {nombre_p.lower()} ({etiqueta})...')
    info_ciclo['ciclo_nombre'] = principal['nombre']
    info_ciclo['horizonte_dias'] = hz_p
    _generar_excel(rec_int, sin_pronostico, carpeta_salida, progreso,
                   etiqueta=etiqueta, info_ciclo=info_ciclo)
    _generar_notas_compra_proveedor(rec_int, carpeta_salida, progreso, etiqueta=etiqueta)
    _generar_dashboard(rec_int, sin_pronostico, carpeta_salida, progreso,
                       etiqueta=etiqueta, info_ciclo=info_ciclo, horizonte_semanas=params.horizonte_semanas)

    rec_int.to_csv(os.path.join(carpeta_salida, 'recomendacion_compra.csv'), index=False)
    sin_pronostico.to_csv(os.path.join(carpeta_salida, 'skus_sin_pronostico.csv'), index=False)

    # ══════════════════════════════════════════════════════════════════
    # CICLOS SECUNDARIOS (en Yuju: local, 40 días) — FIX (Fase 3): N ciclos configurables, no solo 2
    # ══════════════════════════════════════════════════════════════════
    if not secundarios:
        progreso('Paso 7/7 — No hay ciclos de compra secundarios configurados.')
    for i, ciclo in enumerate(secundarios):
        hz = ciclo['horizonte_pronostico_dias']
        progreso(f'Paso 7/7 — Reporte {ciclo["nombre"].upper()} ({i + 1}/{len(secundarios)}): '
                 f'calculando compras a {hz} días...')
        rec_sec = calcular_recomendacion(pronostico, segmentacion, dim, stock, panel_full, fecha_fin, progreso,
                                         compras_raw=compras, mapa_bc_a_sku=mapa_bc_a_sku,
                                         horizonte_semanas_override=hz / 7.0, stock_en_transito=transito,
                                         lead_time_dias=ciclo['lead_time_dias'], params=params,
                                         plantilla=plantilla)
        nota, csv = _archivos_ciclo_secundario(ciclo['nombre'])
        _generar_notas_compra_proveedor(rec_sec, carpeta_salida, progreso,
                                        etiqueta=ciclo['nombre'].upper(), nombre_archivo=nota)
        rec_sec.to_csv(os.path.join(carpeta_salida, csv), index=False)

    # ══════════════════════════════════════════════════════════════════
    # VALIDACIÓN AUTOMÁTICA (si existe recomendación anterior)
    # ══════════════════════════════════════════════════════════════════
    scorecard_result = _intentar_validacion(rec_int, ventas_diarias, carpeta_salida, progreso,
                                            fecha_datos=fecha_datos, horizonte_dias=hz_p,
                                            igv_factor=params.igv_factor)

    # FIX: la importación se confirma en el ciclo solo cuando el análisis terminó bien
    info_ciclo = dict(registrar_importacion(carpeta_salida, sucursal=sucursal,
                                            duracion_dias=principal['duracion_dias']),
                      ciclo_nombre=principal['nombre'], horizonte_dias=hz_p)

    # ══════════════════════════════════════════════════════════════════
    # HISTORIAL SQLite
    # ══════════════════════════════════════════════════════════════════
    progreso('Registrando ejecución en el historial SQLite...')
    # FIX: el scorecard se pasa directo desde esta corrida; antes se releía un JSON que podía ser viejo
    try:
        registrar_ejecucion(carpeta_salida, rec_int, info_ciclo=info_ciclo,
                            sucursal=sucursal, scorecard=scorecard_result, log=progreso)
    except (OSError, ValueError, KeyError, SQLAlchemyError) as e:
        logger.exception('No se pudo registrar la ejecución en el historial')
        progreso(f'⚠ No se pudo registrar en historial: {e}')

    # ── Base de datos: ventas, stock y pronósticos acumulados (para backtesting y multi-sucursal) ──
    try:
        _guardar_en_base(sucursal, dim, segmentacion, ventas_diarias, stock, rec_int, fecha_datos, progreso,
                         horizonte_dias=hz_p)
    except (OSError, ValueError, KeyError, SQLAlchemyError) as e:
        logger.exception('No se pudieron guardar los datos en preve.db')
        progreso(f'⚠ No se pudieron guardar los datos en la base: {e}')

    # ══════════════════════════════════════════════════════════════════
    # INFORME PARA GERENCIA (Word + PDF)
    # ══════════════════════════════════════════════════════════════════
    progreso('Generando el informe para gerencia (Word y PDF)...')
    try:
        from preve_informe import generar_informe
        generar_informe(carpeta_salida, sucursal, log=progreso)
    except Exception as e:  # noqa: BLE001 — el informe es opcional: cualquier fallo no debe perder el análisis
        # FIX: el error ahora queda en el log con traceback completo
        logger.exception('No se pudo generar el informe para gerencia')
        progreso(f'⚠ No se pudo generar el informe para gerencia: {e}')

    progreso('═' * 60)
    extra = ' + ' + ', '.join(c['nombre'].upper() for c in secundarios) if secundarios else ''
    progreso(f'¡Listo! Reporte {nombre_p} {etiqueta}{extra} generados en: {carpeta_salida}')
    progreso(f'Sucursal: {sucursal}')
    progreso('═' * 60)
    return rec_int


def _guardar_en_base(sucursal, dim, segmentacion, ventas_diarias, stock, rec, fecha_datos, log, horizonte_dias=141):
    """Persiste en preve.db el catálogo, las ventas diarias, el stock actual y el pronóstico de esta corrida."""
    from database import Database
    db = Database.default()
    prod = dim.merge(segmentacion[['sku', 'segmento', 'modelo_asignado']], on='sku', how='left')
    db.guardar_productos(prod)
    vd = ventas_diarias.assign(cantidad=-ventas_diarias['mov'])[['sku', 'fecha', 'cantidad']]
    n_v = db.guardar_ventas(sucursal, vd)
    n_s = db.guardar_stock_actual(sucursal, stock.loc[stock.index.max()], fecha_datos)
    inicio = pd.Timestamp(fecha_datos) + pd.Timedelta(days=1)
    n_p = db.guardar_pronosticos(sucursal, rec, datetime.datetime.now(), inicio,
                                 inicio + pd.Timedelta(days=int(horizonte_dias) - 1))
    log(f'Base de datos: {n_v:,} ventas diarias, {n_s:,} stocks y {n_p:,} pronósticos guardados')


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


def ejecutar_pipeline_multi_sucursal(carpeta_entrada, carpeta_salida, progreso=log_info):
    """Ejecuta el pipeline para todas las sucursales que tengan datos.

    Busca subcarpetas con el nombre de cada sucursal dentro de carpeta_entrada,
    o ejecuta para San José si no hay subcarpetas (backward-compatible).
    """
    import os
    subcarpetas = [d for d in os.listdir(carpeta_entrada)
                   if os.path.isdir(os.path.join(carpeta_entrada, d))]

    sucursales_encontradas = {}
    # FIX (Fase 3): las sucursales y sus almacenes vienen de la configuración (antes una lista fija
    # distinta de la de la interfaz)
    conf_suc = {s['nombre']: s['almacenes'] for s in Config.actual().sucursales if s['activa']}
    for nombre_sucursal, almacenes in conf_suc.items():
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
        except Exception as e:  # noqa: BLE001 — una sucursal fallida no debe detener a las demás
            # FIX: el error ahora queda en el log con traceback completo
            logger.exception('Falló el pipeline de la sucursal %s', suc_nombre)
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

                # FIX: exceso/falta frente al nivel objetivo (antes "rop" era el nivel objetivo)
                exceso_a = row_a['stock_actual'] - row_a.get('nivel_objetivo', row_a.get('rop', 0))
                falta_b = row_b.get('nivel_objetivo', row_b.get('rop', 0)) - row_b['stock_actual']

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

                exceso_b = row_b['stock_actual'] - row_b.get('nivel_objetivo', row_b.get('rop', 0))
                falta_a = row_a.get('nivel_objetivo', row_a.get('rop', 0)) - row_a['stock_actual']

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


def ejecutar_simulacion(carpeta_salida, escenario, progreso=log_info):
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
    # FIX (Fase 3): lead time, horizonte y Z actuales desde la configuración (antes 36 / 141 fijos)
    from preve_reglas import cargar_plantilla
    conf = Config.actual()
    principal = conf.ciclo_principal
    resultado = simular(rec, escenario, log=progreso, lead_time_actual=principal['lead_time_dias'],
                        horizonte_actual=principal['horizonte_pronostico_dias'],
                        z_actual=conf.get('z_nivel_servicio'),
                        plantilla=cargar_plantilla(conf.get('plantilla_categorias')))
    generar_excel_simulacion(resultado, carpeta_salida, log=progreso)
    return resultado


def _intentar_validacion(rec_actual, ventas_diarias, carpeta_salida, log, fecha_datos=None,
                         horizonte_dias=141, igv_factor=1.18):
    """Valida la recomendación anterior contra las ventas reales ocurridas desde entonces.

    ventas_diarias: DataFrame sku / fecha / mov (mov negativo = unidades vendidas), de reconstruir_stock.
    fecha_datos: fecha hasta la que llegan los datos de esta corrida (por defecto, la última venta).
    Returns: dict del scorecard, o None si no hubo validación.
    """
    csv_anterior = os.path.join(carpeta_salida, NOMBRE_REC_ANTERIOR)
    meta_anterior = os.path.join(carpeta_salida, NOMBRE_REC_ANTERIOR_META)
    sc_json = os.path.join(carpeta_salida, NOMBRE_SCORECARD_JSON)
    # FIX: un scorecard de una corrida anterior nunca debe pasar como el de esta corrida
    if os.path.exists(sc_json):
        os.remove(sc_json)
    if fecha_datos is None:
        fecha_datos = ventas_diarias['fecha'].max()
    fecha_datos = pd.Timestamp(fecha_datos).normalize()

    fecha_ant = None
    if os.path.exists(meta_anterior):
        try:
            with open(meta_anterior, 'r', encoding='utf-8') as f:
                fecha_ant = pd.Timestamp(json.load(f)['fecha_datos']).normalize()
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            logger.warning('No se pudo leer %s; se omite la validación', meta_anterior, exc_info=True)

    resultado = None
    if not os.path.exists(csv_anterior):
        log('Primera ejecución — no hay recomendación anterior para validar.')
    elif fecha_ant is None:
        log('⚠ La recomendación anterior no tiene fecha de datos (versión antigua de PREVÉ): '
            'no se puede saber qué ventas validar. Se valida desde la próxima importación.')
    elif fecha_datos <= fecha_ant:
        log(f'Los datos ({fecha_datos.date()}) no son más nuevos que la recomendación anterior '
            f'({fecha_ant.date()}): no hay período nuevo que validar.')
    else:
        dias = (fecha_datos - fecha_ant).days
        log(f'Validando la recomendación del {fecha_ant.date()} contra {dias} días de ventas reales...')
        try:
            recom_ant = pd.read_csv(csv_anterior, dtype={'sku': str})
            # FIX: solo las ventas ocurridas DESPUÉS de la recomendación anterior (antes: todo el historial),
            # con la columna real 'mov' (antes 'unidades', que no existe → KeyError oculto)
            vd = ventas_diarias[(ventas_diarias['fecha'] > fecha_ant) & (ventas_diarias['fecha'] <= fecha_datos)]
            venta_real = (-vd.groupby('sku')['mov'].sum()).rename('venta_real').reset_index()
            # FIX: el pronóstico es para 141 días; se prorratea a los días realmente transcurridos
            factor = min(1.0, dias / float(horizonte_dias))
            for col in _COLS_DEMANDA:
                if col in recom_ant.columns:
                    recom_ant[col] = recom_ant[col] * factor
            resultado = generar_scorecard(carpeta_salida, recom_ant, venta_real, log, igv_factor=igv_factor)
            resultado['periodo'] = {'desde': str(fecha_ant.date()), 'hasta': str(fecha_datos.date()),
                                    'dias': dias, 'factor_prorrateo': round(factor, 4)}
            log(f'Scorecard: {resultado["agregadas"]["calificacion"]} '
                f'(WMAPE {resultado["agregadas"]["wmape"]}%)')
            with open(sc_json, 'w', encoding='utf-8') as f:
                json.dump(resultado, f, default=str, ensure_ascii=False)
        except (KeyError, ValueError, OSError, pd.errors.ParserError) as e:
            # FIX: antes un except Exception genérico ocultaba el KeyError
            logger.exception('Falló la validación de la recomendación anterior')
            log(f'⚠ No se pudo generar el scorecard: {type(e).__name__}: {e}')
            resultado = None

    # La recomendación actual pasa a ser la referencia, con la fecha de sus datos
    rec_actual.to_csv(csv_anterior, index=False)
    with open(meta_anterior, 'w', encoding='utf-8') as f:
        json.dump({'fecha_datos': str(fecha_datos.date())}, f)
    log('Recomendación actual guardada como referencia para la próxima validación.')
    return resultado


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
        'stock_actual': 'Stock actual (reconstruido)', 'demanda_p50_141d': 'Demanda esperada (horizonte)',
        'demanda_p10_141d': 'Demanda minima esperada (P10)', 'demanda_p90_141d': 'Demanda maxima esperada (P90)',
        'stock_en_transito': 'Stock en tránsito (órdenes abiertas)',
        'demanda_lead_time': 'Demanda durante el lead time',
        'stock_seguridad': 'Stock de seguridad', 'rop': 'Punto de reorden (ROP)',
        'nivel_objetivo': 'Nivel objetivo',
        'compra_sugerida_unid': 'CANTIDAD A COMPRAR',
        'costo_puesto_almacen_pen': 'Costo unitario (S/)', 'costo_total_compra_pen': 'Inversion total (S/)',
        'margen_pct': 'Margen (%)', 'tendencia_venta': 'Tendencia (u/sem)',
        'revisar_antes_de_comprar': 'REVISAR ANTES DE COMPRAR', 'dato_incompleto': 'Dato incompleto',
    }
    available_cols = [c for c in cols.keys() if c in df.columns]
    df2 = df[available_cols].rename(columns=cols)
    for c in ['Demanda esperada (horizonte)', 'Stock de seguridad', 'Punto de reorden (ROP)', 'Nivel objetivo',
              'Demanda durante el lead time', 'Inversion total (S/)']:
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
    # FIX (Fase 3): empresa, duración del ciclo y horizonte desde la configuración (antes "Yuju Store", 105 y 141)
    empresa = Config.actual().empresa['nombre']
    total_ciclo = (info_ciclo or {}).get('ciclo_dias', Config.actual().ciclo_principal['duracion_dias'])
    hz = (info_ciclo or {}).get('horizonte_dias', Config.actual().ciclo_principal['horizonte_pronostico_dias'])
    ws['A1'] = f'PREVÉ · {empresa} — Recomendación de compra [{etiqueta}]'
    ws['A1'].font = Font(size=16, bold=True, color='FFFFFF')
    ws['A1'].fill = PatternFill('solid', fgColor=ORANGE)
    ws.row_dimensions[1].height = 28
    ws.merge_cells('A2:F2')
    ciclo_txt = ''
    if info_ciclo:
        ciclo_txt = f' | Ciclo #{info_ciclo["ciclo_numero"]} día {info_ciclo["dia_ciclo"]}/{total_ciclo}'
    ws['A2'] = (f'Generado: {datetime.date.today().isoformat()} | Reporte: {etiqueta} | Horizonte: {hz} dias | '
                f'SKU evaluados: {len(df2)}{ciclo_txt}')
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
            'MANTENER':   ('D9EAD3', '274E13'),
            'DESCONTINUAR': ('E7E6E6', '3A3838'),
            'REPONER':    ('C6EFCE', '006100'),
            'REABASTECER': ('C6EFCE', '006100'),
            'PRODUCIR_MAS': ('C6EFCE', '006100'),
            'PROMOCIONAR': ('D9D2E9', '351C75'),
            'TRANSFERIR_SUCURSAL': ('D0E0E3', '0C343D'),
            'SUSTITUIR': ('FCE5CD', '783F04'),
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
        # FIX (Fase 5): las acciones dependen de la plantilla; se listan las que aparecen
        for accion in df2[accion_col].value_counts().index:
            n = int((df2[accion_col] == accion).sum())
            if n > 0:
                resumen.append((f'  → {accion}', n))
    inversion = round(float(df2['Inversion total (S/)'].sum()), 2) if 'Inversion total (S/)' in df2.columns else 0
    resumen += [
        ('Inversión total sugerida (S/) [solo acciones de compra]',
         round(float(df.loc[df['decision'] == 'COMPRAR', 'costo_total_compra_pen'].sum()), 2)
         if 'decision' in df.columns else inversion),
        ('SKU a REVISAR antes de comprar', int(df2['REVISAR ANTES DE COMPRAR'].sum()) if 'REVISAR ANTES DE COMPRAR' in df2.columns else 0),
        ('SKU SIN pronóstico (datos insuficientes)', len(sin_pron)),
        ('Proveedores distintos', int(df2['Proveedor principal'].nunique()) if 'Proveedor principal' in df2.columns else 0),
    ]
    if info_ciclo:
        resumen += [
            ('', ''),
            ('── CICLO DE COMPRAS ──', ''),
            (f'Ciclo #{info_ciclo["ciclo_numero"]}', f'Día {info_ciclo["dia_ciclo"]} de {total_ciclo}'),
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
    comprar = df[df['decision'] == 'COMPRAR'].copy()     # FIX (Fase 5): acción de compra de la plantilla
    if comprar.empty or 'proveedor_principal' not in comprar.columns:
        log('No hay productos para comprar o sin datos de proveedor — se omite nota de compra.')
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


def _generar_dashboard(df, sin_pron, carpeta_salida, log, etiqueta='FINAL', info_ciclo=None,
                       horizonte_semanas=141 / 7.0):
    # FIX (Fase 3): horizonte del ciclo configurado (antes HORIZONTE_SEMANAS fijo)
    hz_dias = round(horizonte_semanas * 7)

    def _explicacion(row):
        vs = (row['demanda_p50_141d'] / horizonte_semanas) if row['demanda_p50_141d'] else 0
        if row['decision'] == 'COMPRAR':
            # FIX: la explicación usa el nivel objetivo (cobertura del horizonte), no el ROP
            transito = row.get('stock_en_transito', 0) or 0
            txt = (f"Se espera vender ~{vs:.0f} unidades/semana en las próximas ~{horizonte_semanas:.0f} semanas. "
                   f"Tienes {row['stock_actual']:.0f} en stock"
                   + (f" y {transito:.0f} en camino" if transito else "")
                   + f"; para cubrir el horizonte con margen de seguridad necesitas "
                   f"{row.get('nivel_objetivo', row['rop']):.0f}. "
                   f"Por eso se sugiere comprar {row['compra_sugerida_unid']:.0f}.")
        else:
            txt = (f"Tu stock actual ({row['stock_actual']:.0f}) ya cubre lo que se espera vender "
                   f"en los próximos {hz_dias} días, así que no necesitas comprar por ahora.")
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
        for acc in df['accion'].value_counts().index:
            n = int((df['accion'] == acc).sum())
            if n > 0:
                acciones_conteo[acc.lower()] = n

    data = {
        'rows': d.to_dict(orient='records'),
        'resumen': {
            'total': len(df),
            'comprar': int((df['decision'] == 'COMPRAR').sum()),
            'no_comprar': int((df['decision'] == 'NO COMPRAR').sum()),
            'inversion': float(df.loc[df['decision'] == 'COMPRAR', 'costo_total_compra_pen'].sum()),
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