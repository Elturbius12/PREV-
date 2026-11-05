# -*- coding: utf-8 -*-
"""
PREVÉ — Módulo de validación y scorecard.

Evalúa qué tan buenas fueron las recomendaciones de PREVÉ comparando
lo que sugirió vs. lo que realmente pasó, usando los datos del ciclo siguiente.

Tres componentes:
  1. Scorecard de precisión del pronóstico (MAPE, sesgo, cobertura).
  2. Análisis contrafactual: ¿qué habría pasado si se compraba lo que dijo PREVÉ?
  3. Estimación de ROI: ahorro o ganancia potencial por seguir las recomendaciones.

Uso:
    from preve_validacion import generar_scorecard
    generar_scorecard(carpeta_salida, recomendacion_anterior, ventas_reales, log)
"""
import os
import datetime
import numpy as np
import pandas as pd


# ──────────────────────────────────────────────────────────
# 1. Scorecard de precisión del pronóstico
# ──────────────────────────────────────────────────────────

def calcular_metricas_pronostico(recom_anterior, ventas_reales_periodo, log=print):
    """Compara pronóstico anterior vs. ventas reales en el periodo evaluado.

    Args:
        recom_anterior: DataFrame con la recomendación pasada (debe tener
            'sku', 'demanda_p50_141d', 'demanda_p10_141d', 'demanda_p90_141d',
            'stock_actual', 'compra_sugerida_unid', 'accion').
        ventas_reales_periodo: DataFrame con ventas reales acumuladas en el periodo,
            columnas 'sku' y 'venta_real'.

    Returns:
        DataFrame con métricas por SKU y dict con métricas agregadas.
    """
    df = recom_anterior[['sku']].copy()
    for col in ['demanda_p50_141d', 'demanda_p10_141d', 'demanda_p90_141d',
                'stock_actual', 'compra_sugerida_unid', 'accion', 'clase_abc',
                'nombre', 'categoria', 'costo_puesto_almacen_pen',
                'precio_regular_pen']:   # FIX: sin el precio, el ROI salía siempre S/ 0
        if col in recom_anterior.columns:
            df[col] = recom_anterior[col].values

    df = df.merge(ventas_reales_periodo[['sku', 'venta_real']], on='sku', how='left')
    df['venta_real'] = df['venta_real'].fillna(0)

    # --- Métricas por SKU ---
    pronostico = df['demanda_p50_141d'].fillna(0)
    real = df['venta_real']

    # Error absoluto porcentual (evitar div/0)
    df['error_abs'] = (pronostico - real).abs()
    df['ape'] = np.where(real > 0, df['error_abs'] / real * 100, np.nan)

    # Sesgo: positivo = sobreestimó, negativo = subestimó
    df['sesgo'] = pronostico - real
    df['sesgo_pct'] = np.where(real > 0, df['sesgo'] / real * 100, np.nan)

    # Cobertura: ¿la venta real cayó dentro del intervalo P10-P90?
    p10 = df['demanda_p10_141d'].fillna(0)
    p90 = df['demanda_p90_141d'].fillna(pronostico * 2)
    df['dentro_intervalo'] = (real >= p10) & (real <= p90)

    # --- Métricas agregadas ---
    mask_con_venta = real > 0
    n_con_venta = mask_con_venta.sum()

    mape = df.loc[mask_con_venta, 'ape'].mean() if n_con_venta > 0 else np.nan
    medape = df.loc[mask_con_venta, 'ape'].median() if n_con_venta > 0 else np.nan
    sesgo_medio = df.loc[mask_con_venta, 'sesgo_pct'].mean() if n_con_venta > 0 else np.nan
    cobertura = df['dentro_intervalo'].mean() * 100

    # WMAPE ponderado por volumen
    total_real = real.sum()
    wmape = (df['error_abs'].sum() / total_real * 100) if total_real > 0 else np.nan

    agregadas = {
        'skus_evaluados': len(df),
        'skus_con_venta': int(n_con_venta),
        'mape': round(mape, 1) if not np.isnan(mape) else None,
        'medape': round(medape, 1) if not np.isnan(medape) else None,
        'wmape': round(wmape, 1) if not np.isnan(wmape) else None,
        'sesgo_medio_pct': round(sesgo_medio, 1) if not np.isnan(sesgo_medio) else None,
        'cobertura_intervalo_pct': round(cobertura, 1),
    }

    # Calificación
    if wmape is not None and not np.isnan(wmape):
        if wmape <= 25:
            agregadas['calificacion'] = 'EXCELENTE'
        elif wmape <= 40:
            agregadas['calificacion'] = 'BUENO'
        elif wmape <= 60:
            agregadas['calificacion'] = 'ACEPTABLE'
        else:
            agregadas['calificacion'] = 'MEJORABLE'
    else:
        agregadas['calificacion'] = 'SIN DATOS'

    log(f'Scorecard: WMAPE={agregadas["wmape"]}% | MedAPE={agregadas["medape"]}% | '
        f'Sesgo={agregadas["sesgo_medio_pct"]}% | Cobertura P10-P90={agregadas["cobertura_intervalo_pct"]}% '
        f'→ {agregadas["calificacion"]}')

    return df, agregadas


# ──────────────────────────────────────────────────────────
# 2. Análisis contrafactual
# ──────────────────────────────────────────────────────────

def analisis_contrafactual(metricas_sku, log=print):
    """¿Qué habría pasado si se compraba exactamente lo que PREVÉ sugirió?

    Calcula por SKU:
      - stock_sin_preve: stock original sin comprar lo sugerido → quiebres
      - stock_con_preve: stock original + compra sugerida → quiebres evitados
      - sobre_stock_evitado: stock excedente que no se compró (ahorro)

    Returns:
        DataFrame con análisis y dict resumen.
    """
    df = metricas_sku.copy()

    stock = df['stock_actual'].fillna(0)
    compra = df['compra_sugerida_unid'].fillna(0)
    venta = df['venta_real'].fillna(0)
    costo = df['costo_puesto_almacen_pen'].fillna(0)

    # Escenario SIN PREVÉ (no comprar nada extra)
    df['stock_final_sin_preve'] = (stock - venta).clip(lower=0)
    df['quiebre_sin_preve'] = (venta > stock)
    df['unidades_perdidas_sin_preve'] = (venta - stock).clip(lower=0)

    # Escenario CON PREVÉ (comprar lo sugerido)
    stock_con = stock + compra
    df['stock_final_con_preve'] = (stock_con - venta).clip(lower=0)
    df['quiebre_con_preve'] = (venta > stock_con)
    df['unidades_perdidas_con_preve'] = (venta - stock_con).clip(lower=0)

    # Quiebres evitados
    df['quiebre_evitado'] = df['quiebre_sin_preve'] & ~df['quiebre_con_preve']

    # Sobre-stock evitado (productos donde PREVÉ dijo NO COMPRAR y tenían stock de sobra)
    no_comprar = df['accion'].isin(['REDUCIR', 'LIQUIDAR']) if 'accion' in df.columns else (compra == 0)
    df['sobre_stock_unidades'] = np.where(no_comprar, (stock - venta).clip(lower=0), 0)
    df['sobre_stock_valor'] = df['sobre_stock_unidades'] * costo

    # Resumen
    resumen = {
        'quiebres_sin_preve': int(df['quiebre_sin_preve'].sum()),
        'quiebres_con_preve': int(df['quiebre_con_preve'].sum()),
        'quiebres_evitados': int(df['quiebre_evitado'].sum()),
        'unidades_perdidas_sin_preve': int(df['unidades_perdidas_sin_preve'].sum()),
        'unidades_perdidas_con_preve': int(df['unidades_perdidas_con_preve'].sum()),
        'valor_sobre_stock_evitado': round(float(df['sobre_stock_valor'].sum()), 2),
    }

    log(f'Contrafactual: quiebres sin PREVÉ={resumen["quiebres_sin_preve"]} → '
        f'con PREVÉ={resumen["quiebres_con_preve"]} '
        f'({resumen["quiebres_evitados"]} evitados) | '
        f'Sobre-stock evitado: S/ {resumen["valor_sobre_stock_evitado"]:,.0f}')

    return df, resumen


# ──────────────────────────────────────────────────────────
# 3. Estimación de ROI
# ──────────────────────────────────────────────────────────

def estimar_roi(metricas_sku, contrafactual, resumen_contra, log=print, igv_factor=1.18):
    """Estima el ROI de usar PREVÉ.

    Componentes del beneficio:
      A) Ventas recuperadas: unidades que se habrían perdido por quiebre de stock,
         valoradas al precio de venta (neto de IGV).
      B) Capital liberado: sobre-stock que PREVÉ evitó comprar, valorado al costo.

    Returns:
        dict con los componentes del ROI.
    """
    df = contrafactual.copy()
    costo = df['costo_puesto_almacen_pen'].fillna(0)

    # A) Ventas recuperadas por evitar quiebres
    # Solo contar las unidades que PREVÉ habría salvado
    unidades_salvadas = df['unidades_perdidas_sin_preve'] - df['unidades_perdidas_con_preve']
    unidades_salvadas = unidades_salvadas.clip(lower=0)

    # Precio neto (sin IGV)
    # FIX: antes df.get(..., pd.Series(0)) devolvía una Serie de largo 1 cuando faltaba la columna y
    # np.where la difundía como precio 0 para todos; ahora se trabaja sobre arrays alineados al df
    if 'precio_regular_pen' in df.columns:
        precio = pd.to_numeric(df['precio_regular_pen'], errors='coerce').to_numpy(dtype=float)
    else:
        precio = np.full(len(df), np.nan)
    costo_arr = costo.to_numpy(dtype=float)
    # FIX (Fase 3): IGV de la empresa configurada (antes 1.18 fijo)
    precio_neto = np.where(~np.isnan(precio), precio / igv_factor,
                           costo_arr * 1.3)  # margen estimado si no hay precio
    margen_unitario = pd.Series(precio_neto - costo_arr, index=df.index)
    df['venta_recuperada_valor'] = unidades_salvadas * margen_unitario

    beneficio_ventas = float(df['venta_recuperada_valor'].clip(lower=0).sum())

    # B) Capital liberado (sobre-stock evitado)
    capital_liberado = resumen_contra['valor_sobre_stock_evitado']

    # Costo financiero estimado del capital inmovilizado (tasa mensual ~2% × 5 meses ≈ 10%)
    ahorro_financiero = capital_liberado * 0.10

    # ROI total
    beneficio_total = beneficio_ventas + ahorro_financiero
    inversion_compra = float(df.loc[df['compra_sugerida_unid'] > 0, 'compra_sugerida_unid'].fillna(0)
                             .mul(costo[df['compra_sugerida_unid'] > 0]).sum())

    roi_pct = (beneficio_total / inversion_compra * 100) if inversion_compra > 0 else 0

    resultado = {
        'ventas_recuperadas_margen': round(beneficio_ventas, 2),
        'capital_liberado': round(capital_liberado, 2),
        'ahorro_financiero_estimado': round(ahorro_financiero, 2),
        'beneficio_total_estimado': round(beneficio_total, 2),
        'inversion_sugerida': round(inversion_compra, 2),
        'roi_pct': round(roi_pct, 1),
    }

    log(f'ROI estimado: beneficio S/ {beneficio_total:,.0f} sobre inversión S/ {inversion_compra:,.0f} '
        f'→ ROI {roi_pct:.1f}%')
    log(f'  Ventas recuperadas (margen): S/ {beneficio_ventas:,.0f}')
    log(f'  Ahorro financiero por no sobre-stockear: S/ {ahorro_financiero:,.0f}')

    return resultado


# ──────────────────────────────────────────────────────────
# 4. Generador de Excel scorecard
# ──────────────────────────────────────────────────────────

def generar_scorecard_excel(metricas_sku, agregadas, resumen_contra, roi, carpeta_salida, log=print):
    """Genera PREVE_Scorecard.xlsx con todas las métricas de validación."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    ORANGE = 'FF6B00'
    wb = Workbook()

    # ── Hoja 1: Resumen ──
    ws = wb.active
    ws.title = 'Resumen scorecard'
    ws.merge_cells('A1:D1')
    ws['A1'] = 'PREVÉ · Scorecard de validación'
    ws['A1'].font = Font(size=16, bold=True, color='FFFFFF')
    ws['A1'].fill = PatternFill('solid', fgColor=ORANGE)
    ws['A2'] = f'Generado: {datetime.date.today().isoformat()}'

    r = 4
    secciones = [
        ('── PRECISIÓN DEL PRONÓSTICO ──', ''),
        ('SKU evaluados', agregadas['skus_evaluados']),
        ('SKU con venta en el período', agregadas['skus_con_venta']),
        ('WMAPE (error ponderado por volumen)', f'{agregadas["wmape"]}%' if agregadas['wmape'] else 'N/A'),
        ('MedAPE (error mediano)', f'{agregadas["medape"]}%' if agregadas['medape'] else 'N/A'),
        ('Sesgo medio', f'{agregadas["sesgo_medio_pct"]}%' if agregadas['sesgo_medio_pct'] else 'N/A'),
        ('Cobertura intervalo P10-P90', f'{agregadas["cobertura_intervalo_pct"]}%'),
        ('Calificación', agregadas['calificacion']),
        ('', ''),
        ('── ANÁLISIS CONTRAFACTUAL ──', ''),
        ('Quiebres de stock SIN PREVÉ', resumen_contra['quiebres_sin_preve']),
        ('Quiebres de stock CON PREVÉ', resumen_contra['quiebres_con_preve']),
        ('Quiebres evitados', resumen_contra['quiebres_evitados']),
        ('Unidades perdidas SIN PREVÉ', resumen_contra['unidades_perdidas_sin_preve']),
        ('Unidades perdidas CON PREVÉ', resumen_contra['unidades_perdidas_con_preve']),
        ('Valor de sobre-stock evitado (S/)', f'S/ {resumen_contra["valor_sobre_stock_evitado"]:,.0f}'),
        ('', ''),
        ('── RETORNO SOBRE LA INVERSIÓN ──', ''),
        ('Ventas recuperadas (margen)', f'S/ {roi["ventas_recuperadas_margen"]:,.0f}'),
        ('Capital liberado', f'S/ {roi["capital_liberado"]:,.0f}'),
        ('Ahorro financiero estimado', f'S/ {roi["ahorro_financiero_estimado"]:,.0f}'),
        ('Beneficio total estimado', f'S/ {roi["beneficio_total_estimado"]:,.0f}'),
        ('Inversión sugerida', f'S/ {roi["inversion_sugerida"]:,.0f}'),
        ('ROI estimado', f'{roi["roi_pct"]}%'),
    ]
    for label, val in secciones:
        c1 = ws.cell(row=r, column=1, value=label)
        ws.cell(row=r, column=2, value=val)
        if label.startswith('──'):
            c1.font = Font(bold=True, color=ORANGE)
        elif label in ('Calificación', 'ROI estimado'):
            c1.font = Font(bold=True)
            ws.cell(row=r, column=2).font = Font(bold=True, size=12)
        r += 1

    ws.column_dimensions['A'].width = 45
    ws.column_dimensions['B'].width = 25

    # ── Hoja 2: Detalle por SKU ──
    ws2 = wb.create_sheet('Detalle por SKU')
    detail_cols = ['sku', 'nombre', 'categoria', 'clase_abc', 'accion',
                   'demanda_p50_141d', 'venta_real', 'error_abs', 'ape', 'sesgo_pct',
                   'dentro_intervalo', 'quiebre_sin_preve', 'quiebre_con_preve', 'quiebre_evitado']
    available = [c for c in detail_cols if c in metricas_sku.columns]
    headers = {
        'sku': 'SKU', 'nombre': 'Producto', 'categoria': 'Categoría', 'clase_abc': 'ABC',
        'accion': 'Acción', 'demanda_p50_141d': 'Pronóstico', 'venta_real': 'Venta real',
        'error_abs': 'Error abs.', 'ape': 'APE %', 'sesgo_pct': 'Sesgo %',
        'dentro_intervalo': '¿En intervalo?', 'quiebre_sin_preve': 'Quiebre sin PREVÉ',
        'quiebre_con_preve': 'Quiebre con PREVÉ', 'quiebre_evitado': 'Quiebre evitado',
    }
    for j, col in enumerate(available, 1):
        c = ws2.cell(row=1, column=j, value=headers.get(col, col))
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor=ORANGE)
    for i, row in enumerate(metricas_sku[available].itertuples(index=False), 2):
        for j, val in enumerate(row, 1):
            cell_val = val
            if isinstance(val, (np.bool_, bool)):
                cell_val = 'Sí' if val else 'No'
            elif isinstance(val, float) and not np.isnan(val):
                cell_val = round(val, 1)
            ws2.cell(row=i, column=j, value=cell_val)
    for j in range(1, len(available) + 1):
        ws2.column_dimensions[get_column_letter(j)].width = max(12, min(35, len(headers.get(available[j-1], '')) + 4))
    ws2.freeze_panes = 'A2'

    path = os.path.join(carpeta_salida, 'PREVE_Scorecard.xlsx')
    wb.save(path)
    log(f'Scorecard generado: {path}')
    return path


# ──────────────────────────────────────────────────────────
# 5. Función orquestadora de validación
# ──────────────────────────────────────────────────────────

def generar_scorecard(carpeta_salida, recomendacion_anterior, ventas_reales_periodo, log=print,
                      igv_factor=1.18):
    """Ejecuta todo el proceso de validación y genera el Excel scorecard.

    Args:
        carpeta_salida: carpeta donde guardar el Excel.
        recomendacion_anterior: DataFrame con la recomendación del ciclo anterior.
        ventas_reales_periodo: DataFrame con columnas 'sku' y 'venta_real'
            (ventas acumuladas en el período evaluado).
        log: función de progreso.

    Returns:
        dict con todas las métricas.
    """
    log('Validación 1/3 — Calculando precisión del pronóstico...')
    metricas_sku, agregadas = calcular_metricas_pronostico(
        recomendacion_anterior, ventas_reales_periodo, log)

    log('Validación 2/3 — Análisis contrafactual...')
    contrafactual_df, resumen_contra = analisis_contrafactual(metricas_sku, log)

    log('Validación 3/3 — Estimación de ROI...')
    roi = estimar_roi(metricas_sku, contrafactual_df, resumen_contra, log, igv_factor=igv_factor)

    log('Generando Excel scorecard...')
    path = generar_scorecard_excel(contrafactual_df, agregadas, resumen_contra, roi, carpeta_salida, log)

    return {
        'agregadas': agregadas,
        'contrafactual': resumen_contra,
        'roi': roi,
        'excel_path': path,
    }
