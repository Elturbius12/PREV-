# -*- coding: utf-8 -*-
"""PREVÉ — Zoo de modelos baseline para comparación experimental.

Cada función tiene la misma firma que `pronosticador_pipeline()` en preve_backtesting.py:
recibe (ventas_hasta, stock_hasta, dim, horizonte_dias, params, log) y devuelve
(pronostico_df, panel, segmentacion, fecha_fin).

El pronóstico es un DataFrame con columnas [sku, demanda_p10_141d, demanda_p50_141d, demanda_p90_141d].
Estos modelos son SOLO para comparación (backtesting); nunca se usan en el pipeline de producción
a menos que la selección automática (Cambio 4) los asigne.
"""
import dataclasses

import numpy as np
import pandas as pd

from preve_utils import get_logger

logger = get_logger('modelos_baseline')

Z90 = 1.2816


def _preparar(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log):
    """Construye panel y segmentación usando el pipeline estándar."""
    from preve_pipeline import construir_panel_y_segmentacion
    p = dataclasses.replace(params, horizonte_dias=float(horizonte_dias))
    panel, seg, fecha_fin = construir_panel_y_segmentacion(stock_hasta, ventas_hasta, dim, log, params=p)
    return panel, seg, fecha_fin, p


def _serie_semanal(panel, sku):
    """Serie temporal de ventas semanales para un SKU, ordenada cronológicamente."""
    s = panel[panel.sku == sku].sort_values('semana_inicio')['unidades_venta_neta'].values.astype(float)
    return s


def _resultado(sku, p50, sigma, hs):
    """Genera la fila de pronóstico con intervalos p10/p50/p90."""
    p50_h = max(0.0, float(p50) * hs)
    sg = max(0.0, float(sigma) * hs)
    return {'sku': sku,
            'demanda_p10_141d': max(0.0, p50_h - Z90 * sg),
            'demanda_p50_141d': p50_h,
            'demanda_p90_141d': p50_h + Z90 * sg}


def _empaquetar(resultados, panel, seg, fecha_fin):
    """Convierte la lista de resultados al formato estándar."""
    _COLS = ['sku', 'demanda_p10_141d', 'demanda_p50_141d', 'demanda_p90_141d']
    pron = pd.DataFrame(resultados or [], columns=_COLS)
    pron[_COLS[1:]] = pron[_COLS[1:]].astype(float)
    pron = pron.merge(seg[['sku', 'modelo_asignado', 'segmento']], on='sku', how='left')
    return pron, panel, seg, fecha_fin


# ══════════════════════════════════════════════════════════════════
# 1. Naïve: repetir la última observación
# ══════════════════════════════════════════════════════════════════
def pronosticador_naive(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log):
    panel, seg, fecha_fin, p = _preparar(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log)
    hs = p.horizonte_semanas
    resultados = []
    for sku in seg['sku']:
        serie = _serie_semanal(panel, sku)
        if len(serie) < 1 or serie.sum() == 0:
            pred, sigma = 0.0, 0.0
        else:
            pred = serie[-1]
            sigma = float(np.std(serie)) if len(serie) > 1 else 0.0
        resultados.append(_resultado(sku, pred, sigma, hs))
    log(f'  Naïve: {len(resultados)} SKU pronosticados')
    return _empaquetar(resultados, panel, seg, fecha_fin)


# ══════════════════════════════════════════════════════════════════
# 2. Promedio móvil simple (SMA)
# ══════════════════════════════════════════════════════════════════
def pronosticador_sma(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log, ventana=4):
    panel, seg, fecha_fin, p = _preparar(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log)
    hs = p.horizonte_semanas
    resultados = []
    for sku in seg['sku']:
        serie = _serie_semanal(panel, sku)
        if len(serie) < 1 or serie.sum() == 0:
            pred, sigma = 0.0, 0.0
        else:
            cola = serie[-ventana:] if len(serie) >= ventana else serie
            pred = float(np.mean(cola))
            sigma = float(np.std(cola)) if len(cola) > 1 else 0.0
        resultados.append(_resultado(sku, pred, sigma, hs))
    log(f'  SMA({ventana}): {len(resultados)} SKU pronosticados')
    return _empaquetar(resultados, panel, seg, fecha_fin)


def pronosticador_sma4(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log):
    return pronosticador_sma(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log, ventana=4)


def pronosticador_sma8(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log):
    return pronosticador_sma(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log, ventana=8)


def pronosticador_sma13(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log):
    return pronosticador_sma(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log, ventana=13)


# ══════════════════════════════════════════════════════════════════
# 3. SES — Suavizado exponencial simple
# ══════════════════════════════════════════════════════════════════
def pronosticador_ses(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log):
    from statsmodels.tsa.holtwinters import SimpleExpSmoothing
    panel, seg, fecha_fin, p = _preparar(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log)
    hs = p.horizonte_semanas
    resultados = []
    for sku in seg['sku']:
        serie = _serie_semanal(panel, sku)
        if len(serie) < 4 or serie.sum() == 0:
            pred, sigma = 0.0, 0.0
        else:
            try:
                m = SimpleExpSmoothing(serie, initialization_method='estimated').fit(optimized=True)
                pred = float(m.forecast(1)[0])
                sigma = float(np.std(m.resid))
            except Exception:
                pred, sigma = float(np.mean(serie)), float(np.std(serie))
        resultados.append(_resultado(sku, pred, sigma, hs))
    log(f'  SES: {len(resultados)} SKU pronosticados')
    return _empaquetar(resultados, panel, seg, fecha_fin)


# ══════════════════════════════════════════════════════════════════
# 4. Holt amortiguado (damped trend)
# ══════════════════════════════════════════════════════════════════
def pronosticador_holt(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log):
    from statsmodels.tsa.holtwinters import Holt
    panel, seg, fecha_fin, p = _preparar(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log)
    hs = p.horizonte_semanas
    ht = max(1, int(round(hs)))
    resultados = []
    for sku in seg['sku']:
        serie = _serie_semanal(panel, sku)
        if len(serie) < 4 or serie.sum() == 0:
            pred, sigma = 0.0, 0.0
        else:
            try:
                m = Holt(serie, damped_trend=True, initialization_method='estimated').fit(optimized=True)
                fc = m.forecast(ht)
                pred = float(np.mean(fc))
                sigma = float(np.std(m.resid))
            except Exception:
                pred, sigma = float(np.mean(serie)), float(np.std(serie))
        resultados.append(_resultado(sku, pred, sigma, hs))
    log(f'  Holt amortiguado: {len(resultados)} SKU pronosticados')
    return _empaquetar(resultados, panel, seg, fecha_fin)


# ══════════════════════════════════════════════════════════════════
# 5. Croston clásico (sin corrección TSB)
# ══════════════════════════════════════════════════════════════════
def pronosticador_croston(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log):
    from statsforecast.models import CrostonClassic
    panel, seg, fecha_fin, p = _preparar(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log)
    hs = p.horizonte_semanas
    ht = max(1, int(round(hs)))
    resultados = []
    for sku in seg['sku']:
        serie = _serie_semanal(panel, sku)
        if len(serie) < 4 or serie.sum() == 0:
            pred, sigma = 0.0, 0.0
        else:
            try:
                m = CrostonClassic()
                m.fit(serie)
                fc = m.predict(h=ht)
                pred = float(np.mean(fc['mean']))
                sigma = float(np.std(serie))
            except Exception:
                pred, sigma = float(np.mean(serie)), float(np.std(serie))
        resultados.append(_resultado(sku, pred, sigma, hs))
    log(f'  Croston clásico: {len(resultados)} SKU pronosticados')
    return _empaquetar(resultados, panel, seg, fecha_fin)


# ══════════════════════════════════════════════════════════════════
# 6. SBA — Syntetos-Boylan Approximation
# ══════════════════════════════════════════════════════════════════
def pronosticador_sba(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log):
    from statsforecast.models import SBA as SBAModel
    panel, seg, fecha_fin, p = _preparar(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log)
    hs = p.horizonte_semanas
    ht = max(1, int(round(hs)))
    resultados = []
    for sku in seg['sku']:
        serie = _serie_semanal(panel, sku)
        if len(serie) < 4 or serie.sum() == 0:
            pred, sigma = 0.0, 0.0
        else:
            try:
                m = SBAModel()
                m.fit(serie)
                fc = m.predict(h=ht)
                pred = float(np.mean(fc['mean']))
                sigma = float(np.std(serie))
            except Exception:
                pred, sigma = float(np.mean(serie)), float(np.std(serie))
        resultados.append(_resultado(sku, pred, sigma, hs))
    log(f'  SBA: {len(resultados)} SKU pronosticados')
    return _empaquetar(resultados, panel, seg, fecha_fin)


# ══════════════════════════════════════════════════════════════════
# 7. TSB con distribución Binomial Negativa
# ══════════════════════════════════════════════════════════════════
def _tsb_negbin_sku(serie, alpha_d=0.1, alpha_p=0.1):
    """TSB con demanda modelada como Binomial Negativa (exceso de ceros / lumpy).

    La parte de probabilidad de demanda se actualiza como TSB estándar.
    La magnitud condicional se estima por MLE de una Binomial Negativa
    sobre las observaciones no-cero, lo que captura mejor la sobredispersión
    que la media simple de Croston.
    """
    from scipy.stats import nbinom
    nz = serie[serie > 0]
    if len(nz) < 2:
        return 0.0, 0.0

    # Probabilidad de demanda (TSB)
    p_hat = alpha_p
    for y in serie:
        p_hat = alpha_p * (1 if y > 0 else 0) + (1 - alpha_p) * p_hat

    # Magnitud condicional: ajustar NegBin a las observaciones no-cero
    mu_nz = float(np.mean(nz))
    var_nz = float(np.var(nz, ddof=1)) if len(nz) > 1 else mu_nz
    if var_nz <= mu_nz:
        z_mean = mu_nz
        z_std = float(np.std(nz))
    else:
        # Parámetros NegBin: r = mu² / (var - mu), p = mu / var
        r = mu_nz ** 2 / (var_nz - mu_nz)
        p_nb = mu_nz / var_nz
        try:
            z_mean = float(nbinom.mean(r, p_nb))
            z_std = float(nbinom.std(r, p_nb))
        except Exception:
            z_mean, z_std = mu_nz, float(np.std(nz))

    pred = p_hat * z_mean
    sigma = p_hat * z_std
    return pred, sigma


def pronosticador_tsb_negbin(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log):
    panel, seg, fecha_fin, p = _preparar(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log)
    hs = p.horizonte_semanas
    resultados = []
    for sku in seg['sku']:
        serie = _serie_semanal(panel, sku)
        if len(serie) < 4 or serie.sum() == 0:
            pred, sigma = 0.0, 0.0
        else:
            try:
                pred, sigma = _tsb_negbin_sku(serie, **p.tsb_params)
            except Exception:
                pred, sigma = float(np.mean(serie)), float(np.std(serie))
        resultados.append(_resultado(sku, pred, sigma, hs))
    log(f'  TSB NegBin: {len(resultados)} SKU pronosticados')
    return _empaquetar(resultados, panel, seg, fecha_fin)


# ══════════════════════════════════════════════════════════════════
# GBM sin disponibilidad (wrapper del pipeline estándar)
# ══════════════════════════════════════════════════════════════════
def pronosticador_gbm_sin_disponibilidad(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log):
    """Pipeline completo pero con el GBM sin features de disponibilidad."""
    from preve_pipeline import construir_panel_y_segmentacion, entrenar_y_pronosticar
    p = dataclasses.replace(params, horizonte_dias=float(horizonte_dias))
    panel, seg, fecha_fin = construir_panel_y_segmentacion(stock_hasta, ventas_hasta, dim, log, params=p)

    from preve_modelo_gbm import pronosticar_gbm
    hs = p.horizonte_semanas
    gbm, _ = pronosticar_gbm(panel, stock_hasta, fecha_fin, hs, log, usar_disponibilidad=False)

    resultados = []
    if gbm is not None:
        resultados.extend(gbm.to_dict('records'))
        seg.loc[seg.sku.isin(set(gbm.sku)), 'modelo_asignado'] = 'GBM sin disponibilidad'

    # Fallback a promedio simple para SKU sin pronóstico GBM
    pronosticados = set(r['sku'] for r in resultados)
    for sku in seg['sku']:
        if sku in pronosticados:
            continue
        serie = _serie_semanal(panel, sku)
        pred = float(np.mean(serie)) if len(serie) > 0 else 0.0
        sigma = float(np.std(serie)) if len(serie) > 1 else 0.0
        resultados.append(_resultado(sku, pred, sigma, hs))

    pron = _empaquetar(resultados, panel, seg, fecha_fin)
    return pron


# ══════════════════════════════════════════════════════════════════
# Registro: nombre → función pronosticadora
# ══════════════════════════════════════════════════════════════════
MODELOS_DISPONIBLES = {
    'Naive': pronosticador_naive,
    'SMA(4)': pronosticador_sma4,
    'SMA(8)': pronosticador_sma8,
    'SMA(13)': pronosticador_sma13,
    'SES': pronosticador_ses,
    'Holt amortiguado': pronosticador_holt,
    'Croston clásico': pronosticador_croston,
    'SBA': pronosticador_sba,
    'TSB NegBin': pronosticador_tsb_negbin,
    'GBM sin disponibilidad': pronosticador_gbm_sin_disponibilidad,
}
