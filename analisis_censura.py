# -*- coding: utf-8 -*-
"""PREVÉ — Análisis de censura por stock-out (script independiente para el artículo).

Lee el panel y stock ya construidos y calcula:
  1. % de semanas con venta=0 que coinciden con stock=0 (demanda censurada)
  2. Cuántos SKU cambian de segmento ADI/CV² al excluir semanas censuradas
  3. Duración promedio de los quiebres de stock por SKU

Uso:
    python analisis_censura.py <carpeta_entrada> [<sucursal>] [--salida <ruta.xlsx>]

Exporta los resultados a un Excel pensado para el artículo académico, no para el usuario operativo.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

from preve_utils import get_logger

logger = get_logger('analisis_censura')

ADI_UMBRAL = 1.32
CV2_UMBRAL = 0.49


def _segmento(adi, cv2):
    if np.isnan(adi) or np.isnan(cv2):
        return 'Indeterminado'
    if adi <= ADI_UMBRAL and cv2 <= CV2_UMBRAL:
        return 'Suave'
    elif adi <= ADI_UMBRAL:
        return 'Errático'
    elif cv2 <= CV2_UMBRAL:
        return 'Intermitente'
    return 'Lumpy'


def _calcular_adi_cv2(serie):
    """ADI y CV² sobre una serie de ventas semanales."""
    con_venta = serie[serie > 0]
    n_total = len(serie)
    n_con = len(con_venta)
    if n_con < 2:
        return np.nan, np.nan
    adi = n_total / n_con
    cv2 = (np.std(con_venta) / np.mean(con_venta)) ** 2 if np.mean(con_venta) > 0 else np.nan
    return adi, cv2


def analizar_censura(panel_full, stock, segmentacion=None):
    """Análisis completo de censura por stock-out.

    panel_full: DataFrame con columnas [sku, semana_inicio, unidades_venta_neta]
    stock: DataFrame fecha × sku (stock al cierre del día)
    segmentacion: DataFrame con columnas [sku, segmento, clase_abc] (opcional, para enriquecer)

    Retorna dict con DataFrames: 'resumen_global', 'por_sku', 'por_clase_abc', 'cambios_segmento',
    'quiebres_stock'.
    """
    skus = sorted(panel_full['sku'].unique())
    semanas = sorted(panel_full['semana_inicio'].unique())

    # Stock semanal: stock > 0 al inicio de cada semana
    if stock is not None and len(stock):
        stock_sem = {}
        for sem in semanas:
            sem_ts = pd.Timestamp(sem)
            dia_inicio = sem_ts - pd.Timedelta(days=1)
            cercano = stock.index[stock.index <= dia_inicio]
            if len(cercano):
                stock_sem[sem] = (stock.loc[cercano[-1]] > 0).astype(int)
            else:
                stock_sem[sem] = pd.Series(0, index=stock.columns)
        stock_semanal = pd.DataFrame(stock_sem).T
    else:
        logger.warning('Sin datos de stock; el análisis de censura será limitado')
        stock_semanal = pd.DataFrame(0, index=semanas, columns=skus)

    # ── 1. Demanda censurada: venta=0 Y stock=0 ──
    resultados_sku = []
    for sku in skus:
        serie = panel_full[panel_full.sku == sku].set_index('semana_inicio')['unidades_venta_neta'].reindex(
            semanas, fill_value=0.0)
        tiene_stock = stock_semanal[sku].reindex(semanas, fill_value=0) if sku in stock_semanal.columns else pd.Series(
            0, index=semanas)

        n_total = len(serie)
        sin_venta = serie == 0
        sin_stock = tiene_stock == 0
        censurada = sin_venta & sin_stock
        n_censurada = int(censurada.sum())
        n_sin_venta = int(sin_venta.sum())
        pct_censurada = n_censurada / n_sin_venta if n_sin_venta > 0 else 0.0

        # ADI/CV² original
        adi_orig, cv2_orig = _calcular_adi_cv2(serie.values)
        seg_orig = _segmento(adi_orig, cv2_orig)

        # ADI/CV² excluyendo semanas censuradas
        serie_sin_censura = serie[~censurada]
        adi_corr, cv2_corr = _calcular_adi_cv2(serie_sin_censura.values)
        seg_corr = _segmento(adi_corr, cv2_corr)

        # Quiebres de stock consecutivos
        quiebres = []
        duracion_actual = 0
        for s in semanas:
            st = tiene_stock.get(s, 0) if hasattr(tiene_stock, 'get') else tiene_stock.loc[s]
            if st == 0:
                duracion_actual += 1
            else:
                if duracion_actual > 0:
                    quiebres.append(duracion_actual)
                duracion_actual = 0
        if duracion_actual > 0:
            quiebres.append(duracion_actual)

        resultados_sku.append({
            'sku': sku,
            'semanas_total': n_total,
            'semanas_sin_venta': n_sin_venta,
            'semanas_censuradas': n_censurada,
            'pct_censurada_de_sin_venta': round(pct_censurada * 100, 1),
            'pct_censurada_de_total': round(n_censurada / n_total * 100, 1) if n_total > 0 else 0.0,
            'adi_original': round(adi_orig, 3) if not np.isnan(adi_orig) else None,
            'cv2_original': round(cv2_orig, 3) if not np.isnan(cv2_orig) else None,
            'segmento_original': seg_orig,
            'adi_corregido': round(adi_corr, 3) if not np.isnan(adi_corr) else None,
            'cv2_corregido': round(cv2_corr, 3) if not np.isnan(cv2_corr) else None,
            'segmento_corregido': seg_corr,
            'cambio_segmento': seg_orig != seg_corr,
            'n_quiebres': len(quiebres),
            'duracion_promedio_quiebre': round(float(np.mean(quiebres)), 1) if quiebres else 0.0,
            'duracion_max_quiebre': max(quiebres) if quiebres else 0,
        })

    por_sku = pd.DataFrame(resultados_sku)

    # Enriquecer con clase ABC si viene segmentación
    if segmentacion is not None and 'clase_abc' in segmentacion.columns:
        por_sku = por_sku.merge(segmentacion[['sku', 'clase_abc']], on='sku', how='left')
    else:
        por_sku['clase_abc'] = 'Sin dato'

    # ── Resumen global ──
    total_sem_sin_venta = por_sku['semanas_sin_venta'].sum()
    total_censuradas = por_sku['semanas_censuradas'].sum()
    resumen_global = pd.DataFrame([{
        'total_skus': len(skus),
        'total_semanas_sin_venta': int(total_sem_sin_venta),
        'total_semanas_censuradas': int(total_censuradas),
        'pct_censura_global': round(total_censuradas / total_sem_sin_venta * 100, 1) if total_sem_sin_venta > 0 else 0,
        'skus_con_cambio_segmento': int(por_sku['cambio_segmento'].sum()),
        'pct_skus_cambian': round(por_sku['cambio_segmento'].mean() * 100, 1),
        'duracion_promedio_quiebre_global': round(por_sku['duracion_promedio_quiebre'].mean(), 1),
    }])

    # ── Por clase ABC ──
    por_abc = por_sku.groupby('clase_abc').agg(
        skus=('sku', 'count'),
        semanas_censuradas=('semanas_censuradas', 'sum'),
        semanas_sin_venta=('semanas_sin_venta', 'sum'),
        cambios_segmento=('cambio_segmento', 'sum'),
        duracion_promedio_quiebre=('duracion_promedio_quiebre', 'mean'),
    ).reset_index()
    por_abc['pct_censura'] = (por_abc['semanas_censuradas'] / por_abc['semanas_sin_venta'].replace(0, np.nan) * 100).round(1)

    # ── Cambios de segmento ──
    cambios = por_sku[por_sku['cambio_segmento']][
        ['sku', 'clase_abc', 'segmento_original', 'segmento_corregido',
         'adi_original', 'adi_corregido', 'cv2_original', 'cv2_corregido',
         'semanas_censuradas', 'pct_censurada_de_total']
    ].copy()

    return {
        'resumen_global': resumen_global,
        'por_sku': por_sku,
        'por_clase_abc': por_abc,
        'cambios_segmento': cambios,
    }


def exportar_excel(resultados, ruta):
    """Exporta los resultados del análisis de censura a un Excel."""
    with pd.ExcelWriter(ruta, engine='openpyxl') as xw:
        resultados['resumen_global'].to_excel(xw, sheet_name='Resumen Global', index=False)
        resultados['por_clase_abc'].to_excel(xw, sheet_name='Por Clase ABC', index=False)
        resultados['cambios_segmento'].to_excel(xw, sheet_name='Cambios de Segmento', index=False)
        resultados['por_sku'].to_excel(xw, sheet_name='Detalle por SKU', index=False)

        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
        for ws in xw.book.worksheets:
            for c in ws[1]:
                c.font = Font(bold=True, color='FFFFFF')
                c.fill = PatternFill('solid', fgColor='2D7D5F')
            for j, col in enumerate(ws.iter_cols(min_row=1, max_row=1), 1):
                ws.column_dimensions[get_column_letter(j)].width = max(14, len(str(col[0].value)) + 4)
            ws.freeze_panes = 'A2'
    return ruta


def main():
    parser = argparse.ArgumentParser(description='Análisis de censura por stock-out para PREVÉ')
    parser.add_argument('carpeta_entrada', help='Carpeta con los reportes del ERP')
    parser.add_argument('sucursal', nargs='?', default='San José', help='Sucursal a analizar')
    parser.add_argument('--salida', default='analisis_censura.xlsx', help='Ruta del Excel de salida')
    args = parser.parse_args()

    from preve_backtesting import DatosBacktest
    from preve_config import Config

    conf = Config.actual()
    params = conf.parametros_modelo(args.sucursal)
    print(f'Cargando datos de {args.sucursal}...')
    datos = DatosBacktest.desde_carpeta(args.carpeta_entrada, args.sucursal, params=params)

    from preve_pipeline import construir_panel_y_segmentacion
    panel, seg, fecha_fin = construir_panel_y_segmentacion(
        datos.stock, datos.ventas_diarias, datos.dim, print, params=params)

    print('Analizando censura por stock-out...')
    resultados = analizar_censura(panel, datos.stock, seg)

    ruta = exportar_excel(resultados, args.salida)
    r = resultados['resumen_global'].iloc[0]
    print(f'\nResultados:')
    print(f'  SKU analizados: {r["total_skus"]}')
    print(f'  Semanas censuradas (stock=0 y venta=0): {r["total_semanas_censuradas"]} '
          f'({r["pct_censura_global"]:.1f}% de las semanas sin venta)')
    print(f'  SKU que cambian de segmento al corregir: {r["skus_con_cambio_segmento"]} '
          f'({r["pct_skus_cambian"]:.1f}%)')
    print(f'  Duración promedio de quiebres: {r["duracion_promedio_quiebre_global"]:.1f} semanas')
    print(f'\nExcel exportado: {ruta}')


if __name__ == '__main__':
    main()
