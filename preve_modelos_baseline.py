# -*- coding: utf-8 -*-
"""PREVÉ — Registro de modelos comparables (modo comparación) y zoo de baselines.

ÚNICA fuente de verdad de los nombres de modelo: la configuración (`modelos_activos`), `comparar_modelos`,
la interfaz, las traducciones y la asignación guardada por SKU usan las constantes de este módulo.

Dos niveles de nombre:
  · MODELOS_COMPARABLES: lo que se configura (incluye la familia 'Promedio móvil').
  · nombres concretos: lo que se evalúa y se guarda por SKU. Igual al configurable, salvo el promedio móvil,
    que se expande en una variante por ventana: nombre_promedio_movil(4) -> 'Promedio móvil 4 sem.'.

Cada modelo concreto tiene dos formas de uso:
  · pronosticar_desde_panel(nombre, panel, seg, dim, stock, fecha_fin, params, log, skus=None)
      -> DataFrame [sku, demanda_p10_141d, demanda_p50_141d, demanda_p90_141d]. La usa producción cuando
         `usar_asignacion_modelos` está activo.
  · pronosticador(nombre) -> función con la firma de `pronosticador_pipeline` (preve_backtesting):
      (ventas_hasta, stock_hasta, dim, horizonte_dias, params, log) -> (pronostico, panel, seg, fecha_fin),
      con `pronostico['modelo_asignado'] == nombre` (cada pronosticador se etiqueta con su propio nombre).

Estos modelos no se usan en producción salvo que el usuario active `usar_asignacion_modelos`.
"""
import dataclasses
import re
import warnings

import numpy as np
import pandas as pd

from preve_utils import get_logger

logger = get_logger('modelos_baseline')

Z90 = 1.2816
COLUMNAS_PRONOSTICO = ['sku', 'demanda_p10_141d', 'demanda_p50_141d', 'demanda_p90_141d']

# ══════════════════════════════════════════════════════════════════
# Nombres canónicos (no escribir estas cadenas en ningún otro módulo: importarlas)
# ══════════════════════════════════════════════════════════════════
MODELO_PRODUCCION = 'Pipeline (producción)'          # el pipeline tal cual (GBM + Croston-TSB + CatBoost)
MODELO_GBM = 'GBM con disponibilidad'
MODELO_GBM_SIN_DISP = 'GBM sin disponibilidad'
MODELO_CATBOOST = 'CatBoost'
MODELO_CROSTON_TSB = 'Croston-TSB'
MODELO_NAIVE = 'Naive'
MODELO_PROMEDIO_MOVIL = 'Promedio móvil'             # familia: una variante por ventana configurada
MODELO_SES = 'SES'
MODELO_HOLT = 'Holt amortiguado'
MODELO_CROSTON = 'Croston clásico'
MODELO_SBA = 'SBA'
MODELO_TSB_NEGBIN = 'TSB NegBin'

MODELOS_COMPARABLES = (MODELO_PRODUCCION, MODELO_GBM, MODELO_GBM_SIN_DISP, MODELO_CATBOOST, MODELO_CROSTON_TSB,
                       MODELO_NAIVE, MODELO_PROMEDIO_MOVIL, MODELO_SES, MODELO_HOLT, MODELO_CROSTON, MODELO_SBA,
                       MODELO_TSB_NEGBIN)
VENTANAS_PROMEDIO_MOVIL = (4, 8, 13)

_RE_PROMEDIO = re.compile(r'^' + re.escape(MODELO_PROMEDIO_MOVIL) + r' (\d+) sem\.$')


def nombre_promedio_movil(ventana):
    """'Promedio móvil 4 sem.'"""
    return f'{MODELO_PROMEDIO_MOVIL} {int(ventana)} sem.'


def ventana_promedio_movil(nombre):
    """Ventana (int) de un nombre concreto de promedio móvil, o None si no lo es."""
    m = _RE_PROMEDIO.match(str(nombre))
    return int(m.group(1)) if m else None


def validar_ventanas(ventanas):
    """Lista ordenada de ventanas enteras >= 1 sin repetir; ValueError si alguna no lo es."""
    out = []
    for v in ventanas or ():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or int(v) < 1:
            raise ValueError(f'Ventana de promedio móvil inválida: {v!r} (debe ser un entero ≥ 1)')
        out.append(int(v))
    if not out:
        raise ValueError('Debe haber al menos una ventana de promedio móvil')
    return sorted(set(out))


def validar_modelos(nombres):
    """(válidos, desconocidos) respecto de MODELOS_COMPARABLES, conservando el orden canónico."""
    pedidos = list(nombres or [])
    desconocidos = [n for n in pedidos if n not in MODELOS_COMPARABLES]
    validos = [n for n in MODELOS_COMPARABLES if n in pedidos]
    return validos, desconocidos


def expandir_modelos(nombres, ventanas=VENTANAS_PROMEDIO_MOVIL):
    """Nombres configurables -> nombres concretos (el promedio móvil se expande por ventana)."""
    out = []
    for n in nombres:
        if n == MODELO_PROMEDIO_MOVIL:
            out.extend(nombre_promedio_movil(w) for w in validar_ventanas(ventanas))
        else:
            out.append(n)
    return out


def es_modelo_concreto(nombre):
    """True si `nombre` es un nombre concreto que se puede pronosticar (o el pipeline de producción)."""
    if ventana_promedio_movil(nombre) is not None:
        return True
    return nombre in MODELOS_COMPARABLES and nombre != MODELO_PROMEDIO_MOVIL


# ══════════════════════════════════════════════════════════════════
# Utilidades comunes
# ══════════════════════════════════════════════════════════════════
def _filas(sku, pred_semanal, sigma_semanal, hs):
    """Fila de pronóstico para el horizonte (hs semanas) a partir de una tasa semanal y su desvío."""
    p50 = max(0.0, float(pred_semanal) * hs)
    sg = max(0.0, float(sigma_semanal) * hs)
    return {'sku': sku, 'demanda_p10_141d': max(0.0, p50 - Z90 * sg), 'demanda_p50_141d': p50,
            'demanda_p90_141d': p50 + Z90 * sg}


def _df(resultados):
    """DataFrame de pronóstico con columnas float a partir de una lista de registros o de otro DataFrame."""
    if isinstance(resultados, pd.DataFrame):
        pron = resultados.reindex(columns=COLUMNAS_PRONOSTICO).copy()
    else:
        pron = pd.DataFrame(list(resultados or []), columns=COLUMNAS_PRONOSTICO)
    pron[COLUMNAS_PRONOSTICO[1:]] = pron[COLUMNAS_PRONOSTICO[1:]].astype(float)
    return pron.drop_duplicates(subset='sku').reset_index(drop=True)


def _series_por_sku(panel, stock, fecha_fin, skus=None):
    """{sku: serie semanal desde su primera venta} sin la última semana si está incompleta.

    Usa la misma matriz que el GBM (preve_modelo_gbm._matrices): misma regla de semana incompleta. Antes de la
    primera venta el producto no existía: esas semanas en 0 no se usan (sesgarían promedios y suavizados).
    """
    from preve_modelo_gbm import _matrices
    skus_m, _, V, _, _ = _matrices(panel, stock, fecha_fin)
    pedidos = None if skus is None else set(skus)
    out = {}
    for i, sku in enumerate(skus_m):
        if pedidos is not None and sku not in pedidos:
            continue
        fila = V[i]
        nz = np.flatnonzero(fila > 0)
        out[sku] = fila[nz[0]:] if len(nz) else np.zeros(0)
    return out


MIN_HISTORIA_BASELINE = 4   # mínimo de semanas de historia para cualquier baseline

def _por_serie(panel, stock, fecha_fin, p, skus, fn, minimo=None):
    """Aplica fn(serie) -> (pred_semanal, sigma_semanal) a cada SKU. Sin ventas: no se pronostica (como el
    pipeline: no se inventa demanda). Con historia más corta que `minimo`: se OMITE el SKU (antes se usaba
    el promedio simple, lo que inflaba el error cuando una venta grande era toda la historia)."""
    minimo = minimo if minimo is not None else MIN_HISTORIA_BASELINE
    hs = p.horizonte_semanas
    res = []
    for sku, serie in _series_por_sku(panel, stock, fecha_fin, skus).items():
        if len(serie) == 0 or serie.sum() == 0:
            continue
        if len(serie) < minimo:
            # Historia insuficiente: se omite (no se inventa un pronóstico con 1-3 semanas)
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                pred, sigma = fn(serie)
        except (ValueError, ZeroDivisionError, FloatingPointError, np.linalg.LinAlgError, KeyError) as e:
            logger.warning('Modelo falló para %s (%s); se omite el SKU', sku, e)
            continue
        if not np.isfinite(pred):
            pred, sigma = float(np.mean(serie)), float(np.std(serie))
        res.append(_filas(sku, pred, sigma if np.isfinite(sigma) else 0.0, hs))
    return _df(res)


# ══════════════════════════════════════════════════════════════════
# Modelos (todos con la firma: panel, seg, dim, stock, fecha_fin, p, log, skus)
# ══════════════════════════════════════════════════════════════════
def _naive(panel, seg, dim, stock, fecha_fin, p, log, skus=None):
    """Repite la última semana. Requiere al menos MIN_HISTORIA_BASELINE semanas."""
    return _por_serie(panel, stock, fecha_fin, p, skus,
                      lambda s: (float(s[-1]), float(np.std(s)) if len(s) > 1 else 0.0))


def _promedio_movil(ventana):
    def fn(panel, seg, dim, stock, fecha_fin, p, log, skus=None):
        def f(s):
            cola = s[-ventana:]
            return float(np.mean(cola)), (float(np.std(cola)) if len(cola) > 1 else 0.0)
        return _por_serie(panel, stock, fecha_fin, p, skus, f)
    return fn


def _ses(panel, seg, dim, stock, fecha_fin, p, log, skus=None):
    """Suavizado exponencial simple (statsmodels, alfa optimizado)."""
    from statsmodels.tsa.holtwinters import SimpleExpSmoothing

    def f(s):
        m = SimpleExpSmoothing(s, initialization_method='estimated').fit(optimized=True)
        return float(m.forecast(1)[0]), float(np.std(m.resid))
    return _por_serie(panel, stock, fecha_fin, p, skus, f, minimo=4)


def _holt(panel, seg, dim, stock, fecha_fin, p, log, skus=None):
    """Holt con tendencia amortiguada (statsmodels); tasa = promedio del pronóstico en el horizonte."""
    from statsmodels.tsa.holtwinters import Holt
    ht = max(1, int(round(p.horizonte_semanas)))

    def f(s):
        m = Holt(s, damped_trend=True, initialization_method='estimated').fit(optimized=True)
        return float(np.mean(np.clip(m.forecast(ht), 0, None))), float(np.std(m.resid))
    return _por_serie(panel, stock, fecha_fin, p, skus, f, minimo=4)


def _statsforecast(clase):
    def fn(panel, seg, dim, stock, fecha_fin, p, log, skus=None):
        ht = max(1, int(round(p.horizonte_semanas)))

        def f(s):
            m = clase()
            m.fit(s)
            return float(np.mean(m.predict(h=ht)['mean'])), float(np.std(s))
        return _por_serie(panel, stock, fecha_fin, p, skus, f, minimo=4)
    return fn


def _croston(panel, seg, dim, stock, fecha_fin, p, log, skus=None):
    from statsforecast.models import CrostonClassic
    return _statsforecast(CrostonClassic)(panel, seg, dim, stock, fecha_fin, p, log, skus)


def _sba(panel, seg, dim, stock, fecha_fin, p, log, skus=None):
    from statsforecast.models import CrostonSBA
    return _statsforecast(CrostonSBA)(panel, seg, dim, stock, fecha_fin, p, log, skus)


def tsb_negbin_serie(serie, alpha_d=0.1, alpha_p=0.1):
    """(tasa semanal, desvío) de TSB con tamaño de demanda Binomial Negativa.

    Probabilidad de demanda: actualización TSB (suavizado alpha_p sobre el indicador "hubo venta").
    Tamaño condicional: media y desvío de una Binomial Negativa ajustada por momentos a las semanas con venta
    (si no hay sobredispersión, var <= media, se usa la media y el desvío empíricos). alpha_d se acepta para
    compartir los parámetros `tsb_params` del pipeline pero no se usa: el tamaño sale del ajuste NB.
    """
    from scipy.stats import nbinom
    serie = np.asarray(serie, dtype=float)
    nz = serie[serie > 0]
    if len(nz) < 2:
        return (float(np.mean(serie)) if len(serie) else 0.0), (float(np.std(serie)) if len(serie) else 0.0)
    p_hat = float((serie[:max(1, len(serie) // 4)] > 0).mean())      # arranque: frecuencia del primer cuarto
    for y in serie:
        p_hat = alpha_p * (1.0 if y > 0 else 0.0) + (1 - alpha_p) * p_hat
    mu, var = float(np.mean(nz)), float(np.var(nz, ddof=1))
    if var <= mu:
        z_mean, z_std = mu, float(np.std(nz))
    else:
        r, q = mu ** 2 / (var - mu), mu / var
        z_mean, z_std = float(nbinom.mean(r, q)), float(nbinom.std(r, q))
    return p_hat * z_mean, p_hat * z_std


def _tsb_negbin(panel, seg, dim, stock, fecha_fin, p, log, skus=None):
    tsb = dict(p.tsb_params or {})
    return _por_serie(panel, stock, fecha_fin, p, skus,
                      lambda s: tsb_negbin_serie(s, tsb.get('alpha_d', 0.1), tsb.get('alpha_p', 0.1)), minimo=4)


def _gbm(usar_disponibilidad):
    def fn(panel, seg, dim, stock, fecha_fin, p, log, skus=None):
        from preve_modelo_gbm import pronosticar_gbm
        out = pronosticar_gbm(panel, stock, fecha_fin, p.horizonte_semanas, log,
                              usar_disponibilidad=usar_disponibilidad)
        if out is None:
            return _df([])
        return _df(out[out['sku'].isin(skus)] if skus is not None else out)
    return fn


def _croston_tsb(panel, seg, dim, stock, fecha_fin, p, log, skus=None):
    """Croston-TSB usando la MISMA serie recortada (_series_por_sku) que los demás baselines.

    Antes usaba panel_full directo (sin recortar ceros previos), lo que producía series distintas
    al resto de los baselines y un pronóstico inconsistente.
    """
    hs = p.horizonte_semanas
    tsb = dict(p.tsb_params or {})
    alpha_d, alpha_p = tsb.get('alpha_d', 0.1), tsb.get('alpha_p', 0.1)
    res = []
    for sku, serie in _series_por_sku(panel, stock, fecha_fin, skus).items():
        if len(serie) < MIN_HISTORIA_BASELINE or serie.sum() == 0:
            continue
        try:
            pred, sigma = tsb_negbin_serie(serie, alpha_d, alpha_p)
        except Exception:
            continue
        if np.isfinite(pred):
            res.append(_filas(sku, pred, sigma if np.isfinite(sigma) else 0.0, hs))
    return _df(res)


def _catboost(panel, seg, dim, stock, fecha_fin, p, log, skus=None):
    """El CatBoost del pipeline (por categoría y precio, mismo código), entrenado y aplicado a todos los SKU."""
    from preve_pipeline import _pronosticar_catboost
    out = _df(_pronosticar_catboost(list(seg['sku']), panel, dim, p, log))
    return out[out['sku'].isin(skus)].reset_index(drop=True) if skus is not None else out


_FUNCIONES = {
    MODELO_GBM: _gbm(True),
    MODELO_GBM_SIN_DISP: _gbm(False),
    MODELO_CATBOOST: _catboost,
    MODELO_CROSTON_TSB: _croston_tsb,
    MODELO_NAIVE: _naive,
    MODELO_SES: _ses,
    MODELO_HOLT: _holt,
    MODELO_CROSTON: _croston,
    MODELO_SBA: _sba,
    MODELO_TSB_NEGBIN: _tsb_negbin,
}
assert set(_FUNCIONES) | {MODELO_PRODUCCION, MODELO_PROMEDIO_MOVIL} == set(MODELOS_COMPARABLES)


def _funcion(nombre):
    w = ventana_promedio_movil(nombre)
    if w is not None:
        return _promedio_movil(w)
    if nombre not in _FUNCIONES:
        raise KeyError(f'Modelo desconocido: {nombre!r}. Válidos: {", ".join(MODELOS_COMPARABLES)}')
    return _FUNCIONES[nombre]


def pronosticar_desde_panel(nombre, panel, seg, dim, stock, fecha_fin, params, log=lambda m: None, skus=None):
    """Pronóstico del modelo concreto `nombre` (no el pipeline) sobre un panel ya construido."""
    if nombre == MODELO_PRODUCCION:
        raise ValueError('El pipeline de producción no se pronostica por esta vía (es el comportamiento normal).')
    return _funcion(nombre)(panel, seg, dim, stock, fecha_fin, params, log, skus)


def pronosticador(nombre):
    """Función con la firma de `pronosticador_pipeline`, etiquetada con `nombre` en 'modelo_asignado'."""
    if nombre == MODELO_PRODUCCION:
        from preve_backtesting import pronosticador_pipeline
        return pronosticador_pipeline
    fn = _funcion(nombre)                         # valida el nombre al construir, no al primer uso

    def _pronosticar(ventas_hasta, stock_hasta, dim, horizonte_dias, params, log):
        from preve_pipeline import construir_panel_y_segmentacion
        p = dataclasses.replace(params, horizonte_dias=float(horizonte_dias))
        panel, seg, fecha_fin = construir_panel_y_segmentacion(stock_hasta, ventas_hasta, dim, log, params=p)
        pron = fn(panel, seg, dim, stock_hasta, fecha_fin, p, log, None)
        pron = pron.merge(seg[['sku', 'segmento']], on='sku', how='left')
        pron['modelo_asignado'] = nombre
        log(f'  {nombre}: {len(pron):,} SKU pronosticados')
        return pron, panel, seg, fecha_fin
    _pronosticar.nombre_modelo = nombre
    return _pronosticar
