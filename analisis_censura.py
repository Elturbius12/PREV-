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

import numpy as np
import pandas as pd

from preve_utils import get_logger

logger = get_logger('analisis_censura')

ADI_UMBRAL = 1.32          # por defecto; main() usa los de la configuración (ParametrosModelo)
CV2_UMBRAL = 0.49


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


def disponibilidad_semanal(stock, semanas):
    """DataFrame semana × sku con la fracción (0–1) de días de la semana con stock > 0.

    La semana va de `semana_inicio` (lunes) a domingo, igual que el panel. Días sin dato de stock no cuentan;
    una semana sin ningún dato queda en NaN (desconocida).
    """
    disp = (stock > 0).astype(float)
    semana = disp.index.to_period('W-SUN').start_time
    return disp.groupby(semana).mean().reindex(pd.DatetimeIndex(semanas))


def _rachas(mascara):
    """Duraciones de las rachas consecutivas de True."""
    rachas, n = [], 0
    for v in mascara:
        if v:
            n += 1
        elif n:
            rachas.append(n)
            n = 0
    if n:
        rachas.append(n)
    return rachas


def _duracion_media(por_sku):
    """Duración media (semanas) de todos los quiebres: Σ duraciones / Σ quiebres (0 si no hubo)."""
    n = por_sku['n_quiebres'].sum()
    return round(float((por_sku['duracion_promedio_quiebre'] * por_sku['n_quiebres']).sum() / n), 1) if n else 0.0


def analizar_censura(panel_full, stock, segmentacion=None, umbral_disponibilidad=0.5,
                     adi_umbral=None, cv2_umbral=None):
    """Análisis completo de censura por stock-out.

    panel_full: DataFrame con columnas [sku, semana_inicio, unidades_venta_neta]
    stock: DataFrame fecha × sku (stock al cierre del día)
    segmentacion: DataFrame con columnas [sku, segmento, clase_abc] (opcional, para enriquecer)

    Definiciones (para el artículo):
      · semana sin stock: menos de `umbral_disponibilidad` de sus días con stock > 0 (stock negativo del ERP =
        sin stock). Semana sin dato de stock = con stock (no se puede afirmar censura).
      · semana censurada: semana sin stock Y sin venta.
      · vida del producto: desde su primera semana con venta o con stock; antes no existía y no se cuenta.
      · segmento corregido: ADI/CV² recalculados excluyendo las semanas censuradas.
      · quiebre: racha de semanas consecutivas sin stock dentro de la vida del producto.

    Retorna dict con DataFrames: 'resumen_global', 'por_sku', 'por_clase_abc', 'cambios_segmento'.
    """
    adi_u = ADI_UMBRAL if adi_umbral is None else float(adi_umbral)
    cv2_u = CV2_UMBRAL if cv2_umbral is None else float(cv2_umbral)
    skus = sorted(panel_full['sku'].unique())
    semanas = sorted(pd.to_datetime(panel_full['semana_inicio'].unique()))
    ventas = (panel_full.assign(semana_inicio=pd.to_datetime(panel_full['semana_inicio']))
              .pivot_table(index='semana_inicio', columns='sku', values='unidades_venta_neta', aggfunc='sum')
              .reindex(index=semanas, columns=skus).fillna(0.0))
    if stock is not None and len(stock):
        disp = disponibilidad_semanal(stock, semanas).reindex(columns=skus)
    else:
        logger.warning('Sin datos de stock: no se puede medir la censura (todas las semanas cuentan con stock)')
        disp = pd.DataFrame(np.nan, index=ventas.index, columns=skus)

    def _seg(adi, cv2):
        if np.isnan(adi) or np.isnan(cv2):
            return 'Indeterminado'
        if adi <= adi_u and cv2 <= cv2_u:
            return 'Suave'
        if adi <= adi_u:
            return 'Errático'
        if cv2 <= cv2_u:
            return 'Intermitente'
        return 'Lumpy'

    # ── 1. Demanda censurada: venta=0 Y sin stock, dentro de la vida del producto ──
    resultados_sku = []
    for sku in skus:
        v = ventas[sku].to_numpy()
        d = disp[sku].to_numpy()
        sin_stock_sem = np.where(np.isnan(d), False, d < umbral_disponibilidad)
        vivo = np.flatnonzero((v > 0) | (np.nan_to_num(d) > 0))
        ini = vivo[0] if len(vivo) else len(v)
        serie, sin_stock = v[ini:], sin_stock_sem[ini:]

        n_total = len(serie)
        sin_venta = serie == 0
        censurada = sin_venta & sin_stock
        n_censurada = int(censurada.sum())
        n_sin_venta = int(sin_venta.sum())
        pct_censurada = n_censurada / n_sin_venta if n_sin_venta > 0 else 0.0

        # ADI/CV² original
        adi_orig, cv2_orig = _calcular_adi_cv2(serie)
        seg_orig = _seg(adi_orig, cv2_orig)

        # ADI/CV² excluyendo semanas censuradas
        adi_corr, cv2_corr = _calcular_adi_cv2(serie[~censurada])
        seg_corr = _seg(adi_corr, cv2_corr)

        # Quiebres de stock consecutivos
        quiebres = _rachas(sin_stock)

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
        por_sku['clase_abc'] = por_sku['clase_abc'].fillna('Sin dato')
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
        # promedio sobre los quiebres reales (los SKU sin quiebres no aportan duraciones 0)
        'duracion_promedio_quiebre_global': _duracion_media(por_sku),
    }])

    # ── Por clase ABC ──
    por_abc = por_sku.groupby('clase_abc').agg(
        skus=('sku', 'count'),
        semanas_censuradas=('semanas_censuradas', 'sum'),
        semanas_sin_venta=('semanas_sin_venta', 'sum'),
        cambios_segmento=('cambio_segmento', 'sum'),
    ).reset_index()
    por_abc['duracion_promedio_quiebre'] = por_abc['clase_abc'].map(
        {c: _duracion_media(g) for c, g in por_sku.groupby('clase_abc')})
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


# ══════════════════════════════════════════════════════════════════
# Índice de Ceguera por Desabastecimiento (ICD) — ver ICD_metodo.md
# ══════════════════════════════════════════════════════════════════
#   ICD = semanas censuradas / semanas sin venta
#   semana censurada = semana sin venta (venta = 0) en la que el producto NO tuvo stock disponible.
# ICD = 0: todos los ceros de venta fueron "no hubo demanda". ICD = 1: ningún cero de venta es confiable
# (todos coinciden con falta de stock). Para un grupo (categoría, segmento, empresa) se agrega sumando
# numerador y denominador (ICD agregado ponderado), no promediando los ICD de cada SKU.
COLUMNAS_ICD = ['semanas_sin_venta', 'semanas_censuradas', 'icd', 'skus', 'skus_con_icd',
                'icd_sku_media', 'icd_sku_mediana', 'icd_sku_p25', 'icd_sku_p75']


def icd_serie(ventas, sin_stock):
    """ICD de UNA serie (arrays alineados por semana). Implementación de referencia, sin dependencias de PREVÉ.

    ventas: unidades vendidas por semana. sin_stock: True si la semana no tuvo stock disponible.
    Devuelve (semanas_censuradas, semanas_sin_venta, icd); icd = NaN si no hubo semanas sin venta.
    """
    v = np.asarray(ventas, dtype=float)
    s = np.asarray(sin_stock, dtype=bool)
    sin_venta = v <= 0
    cens = int((sin_venta & s).sum())
    n0 = int(sin_venta.sum())
    return cens, n0, (cens / n0 if n0 else np.nan)


def _icd_grupo(g):
    n0, c = int(g['semanas_sin_venta'].sum()), int(g['semanas_censuradas'].sum())
    x = g['icd'].dropna()
    return {'semanas_sin_venta': n0, 'semanas_censuradas': c, 'icd': (c / n0 if n0 else np.nan),
            'skus': int(len(g)), 'skus_con_icd': int(len(x)),
            'icd_sku_media': float(x.mean()) if len(x) else np.nan,
            'icd_sku_mediana': float(x.median()) if len(x) else np.nan,
            'icd_sku_p25': float(x.quantile(.25)) if len(x) else np.nan,
            'icd_sku_p75': float(x.quantile(.75)) if len(x) else np.nan}


def indice_ceguera(por_sku, sucursal=None):
    """ICD por SKU, por clase ABC, por segmento ADI/CV² (original, con censura) y a nivel empresa.

    por_sku: la tabla 'por_sku' de analizar_censura (o varias concatenadas con columna 'sucursal').
    Devuelve dict de DataFrames: por_sku, por_clase_abc, por_segmento, por_sucursal, empresa, distribucion.
    """
    d = por_sku.copy()
    if sucursal is not None:
        d['sucursal'] = sucursal
    if 'sucursal' not in d.columns:
        d['sucursal'] = SIN_SUCURSAL
    d['icd'] = d['semanas_censuradas'] / d['semanas_sin_venta'].where(d['semanas_sin_venta'] > 0)

    def por(col):
        filas = [dict({col: k}, **_icd_grupo(g)) for k, g in d.groupby(col, dropna=False)]
        return pd.DataFrame(filas, columns=[col] + COLUMNAS_ICD)

    bins = [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0000001]
    etiquetas = [f'{a:.1f}–{min(b, 1):.1f}' for a, b in zip(bins[:-1], bins[1:])]
    x = d['icd'].dropna()
    dist = pd.cut(x, bins, labels=etiquetas, right=False, include_lowest=True).value_counts().reindex(etiquetas)
    distribucion = pd.DataFrame({'rango_icd': etiquetas, 'skus': dist.fillna(0).astype(int).values})
    distribucion['pct_skus'] = (distribucion['skus'] / max(1, len(x)) * 100).round(1)
    return {'por_sku': d[['sucursal', 'sku', 'clase_abc', 'segmento_original', 'semanas_sin_venta',
                          'semanas_censuradas', 'icd']],
            'por_clase_abc': por('clase_abc'), 'por_segmento': por('segmento_original').rename(
                columns={'segmento_original': 'segmento'}),
            'por_sucursal': por('sucursal'),
            'empresa': pd.DataFrame([dict({'nivel': 'Empresa'}, **_icd_grupo(d))], columns=['nivel'] + COLUMNAS_ICD),
            'distribucion': distribucion}


SIN_SUCURSAL = 'Sin dato'


def exportar_excel(resultados, ruta):
    """Exporta los resultados del análisis de censura a un Excel."""
    with pd.ExcelWriter(ruta, engine='openpyxl') as xw:
        resultados['resumen_global'].to_excel(xw, sheet_name='Resumen Global', index=False)
        resultados['por_clase_abc'].to_excel(xw, sheet_name='Por Clase ABC', index=False)
        resultados['cambios_segmento'].to_excel(xw, sheet_name='Cambios de Segmento', index=False)
        resultados['por_sku'].to_excel(xw, sheet_name='Detalle por SKU', index=False)
        icd = indice_ceguera(resultados['por_sku'])
        for clave, hoja in (('empresa', 'ICD Empresa'), ('por_clase_abc', 'ICD por ABC'),
                            ('por_segmento', 'ICD por Segmento'), ('distribucion', 'ICD Distribución')):
            icd[clave].to_excel(xw, sheet_name=hoja, index=False)

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
    resultados = analizar_censura(panel, datos.stock, seg, adi_umbral=params.adi_umbral,
                                  cv2_umbral=params.cv2_umbral)

    ruta = exportar_excel(resultados, args.salida)
    r = resultados['resumen_global'].iloc[0]
    print('\nResultados:')
    print(f'  SKU analizados: {int(r["total_skus"])}')
    print(f'  Semanas censuradas (sin stock y sin venta): {int(r["total_semanas_censuradas"])} '
          f'({r["pct_censura_global"]:.1f}% de las semanas sin venta)')
    print(f'  SKU que cambian de segmento al corregir: {int(r["skus_con_cambio_segmento"])} '
          f'({r["pct_skus_cambian"]:.1f}%)')
    print(f'  Duración promedio de quiebres: {r["duracion_promedio_quiebre_global"]:.1f} semanas')
    print(f'\nExcel exportado: {ruta}')


if __name__ == '__main__':
    main()
