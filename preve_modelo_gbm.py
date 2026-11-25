# -*- coding: utf-8 -*-
"""Modelo global de demanda (gradient boosting con pérdida de Poisson) para PREVÉ.

Un solo modelo aprende de TODOS los SKU a la vez en lugar de uno por producto. En vez de adivinar el nivel de
cada producto con su propio historial (Croston, promedios), aprende cómo se comporta la demanda futura según:
  · ventas recientes (últimas 1, 2, 4, 8, 13, 26 y 52 semanas) y su tendencia,
  · cuántas semanas tuvo ventas y cuánto tiempo lleva sin vender,
  · antigüedad del producto,
  · DISPONIBILIDAD: stock actual y fracción de días con stock en las últimas semanas. Sin esto el modelo
    confunde "no se vendió porque no había" con "no hay demanda" (probado: 76 % -> 62 % de error).

Walk-forward sobre las ventas reales de San José (2025-04 a 2026-11), pronóstico a 14 días, error ponderado:
  Croston-TSB actual 104 %  ·  promedio 4 semanas 95 %  ·  este modelo 62 %.
Se usa scikit-learn (HistGradientBoostingRegressor) porque ya es dependencia del proyecto y es la implementación con
la que se validó; no depende de LightGBM.
"""
import numpy as np
import pandas as pd

from preve_utils import get_logger

logger = get_logger('modelo_gbm')

VENTANAS = (1, 2, 4, 8, 13, 26, 52)
CORTE_MIN = 8            # primer corte semanal que se usa para entrenar (hace falta historia para las features)
MIN_CORTES = 4           # cortes de entrenamiento mínimos para confiar en el modelo
MIN_FILAS = 300
INFLACION_SIGMA = 1.0    # se calibra con el walk-forward (ver CHANGELOG)
Z90 = 1.2816

PARAMS_GBM = dict(loss='poisson', max_iter=250, learning_rate=0.03, max_leaf_nodes=31, min_samples_leaf=40,
                  l2_regularization=1.0, random_state=42)

FEATURES = ([f's{w}' for w in VENTANAS] + ['nz4', 'nz13', 'nz26', 'av4', 'av13', 'av26',
                                           'since_last', 'age', 'stock', 'trend'])
COLS_DISPONIBILIDAD = ('av4', 'av13', 'av26', 'stock')   # requieren el historial de stock


def _matrices(panel_full, stock, fecha_fin):
    """(skus, semanas, V, A, S): ventas, disponibilidad semanal (0-1) y stock al inicio de cada semana.

    S tiene una columna más que V: la última es el stock al cierre de los datos (punto de pronóstico).
    Si la última semana está incompleta (los datos no llegan hasta el domingo) se descarta: una semana a medias
    haría parecer que las ventas recientes cayeron.
    """
    p = panel_full[['sku', 'semana_inicio', 'unidades_venta_neta']].copy()
    p['semana_inicio'] = pd.to_datetime(p['semana_inicio'])
    semanas = pd.date_range(p['semana_inicio'].min(), p['semana_inicio'].max(), freq='7D')
    V = (p.pivot_table(index='sku', columns='semana_inicio', values='unidades_venta_neta', aggfunc='sum')
          .reindex(columns=semanas).fillna(0.0))
    skus = V.index.tolist()

    fin_datos = pd.Timestamp(fecha_fin)
    if stock is not None and len(stock):
        fin_datos = max(fin_datos, pd.Timestamp(stock.index.max()))
    if fin_datos.normalize().weekday() != 6 and len(semanas) > 1:
        semanas = semanas[:-1]
        V = V.iloc[:, :-1]
        logger.info('Se descarta la última semana (incompleta, datos hasta %s)', fin_datos.date())

    n, T = V.shape
    if stock is not None and len(stock):
        st = stock.reindex(columns=skus)
        disp = (st.fillna(0) > 0).astype(float)
        sem_disp = disp.groupby(disp.index.to_period('W-SUN').start_time).mean()
        A = sem_disp.reindex(index=semanas).T.reindex(skus).fillna(0.0).values
        previos = list(semanas - pd.Timedelta(days=1)) + [semanas[-1] + pd.Timedelta(days=6)]
        S = st.reindex(previos, method='ffill').fillna(0.0).T.values
    else:
        A = np.full((n, T), np.nan)
        S = np.full((n, T + 1), np.nan)
        return skus, semanas, V.values.astype(float), A, S
    V = V.values.astype(float)
    # Robustez ante inconsistencias del ERP: el stock reconstruido puede ser negativo y una venta prueba que
    # había stock. Stock negativo -> 0; semana con ventas y disponibilidad 0 -> al menos 1 día de 7.
    S = np.clip(S, 0, None)
    A = np.where((V > 0) & (A == 0), 1 / 7, A)
    return skus, semanas, V, A, S


class _Features:
    """Features por corte semanal k (usa solo semanas < k), vectorizadas sobre todos los SKU."""

    def __init__(self, V, A, S):
        n, T = V.shape
        self.n, self.T = n, T
        self.V, self.S = V, S
        self.cs = np.concatenate([np.zeros((n, 1)), np.cumsum(V, 1)], 1)
        self.cn = np.concatenate([np.zeros((n, 1)), np.cumsum(V > 0, 1)], 1)
        self.ca = np.concatenate([np.zeros((n, 1)), np.cumsum(A, 1)], 1)
        con_venta = V > 0
        self.first = np.where(con_venta.any(1), con_venta.argmax(1), -1)
        # última semana con venta antes del corte k (-1 si nunca)
        ult = np.full((n, T + 1), -1.0)
        cur = np.full(n, -1.0)
        for k in range(T + 1):
            ult[:, k] = cur
            if k < T:
                cur = np.where(con_venta[:, k], k, cur)
        self.ult = ult

    def __call__(self, k):
        c = {}
        for w in VENTANAS:
            a = max(0, k - w)
            c[f's{w}'] = (self.cs[:, k] - self.cs[:, a]) / min(w, k)
        for w in (4, 13, 26):
            a = max(0, k - w)
            c[f'nz{w}'] = (self.cn[:, k] - self.cn[:, a]) / min(w, k)
            c[f'av{w}'] = (self.ca[:, k] - self.ca[:, a]) / min(w, k)
        c['since_last'] = np.where(self.ult[:, k] >= 0, k - self.ult[:, k], k + 1)
        c['age'] = np.where(self.first >= 0, k - self.first, -1)
        c['stock'] = self.S[:, k]
        c['trend'] = c['s2'] / (c['s8'] + 0.1)
        return pd.DataFrame(c)[FEATURES]


def pronosticar_gbm(panel_full, stock, fecha_fin, hs, log=print, inflacion_sigma=None,
                    usar_disponibilidad=True, calcular_importancias=False, devolver_extras=False):
    """Pronostica la demanda de los próximos `hs` semanas para cada SKU con historia de ventas.

    Devuelve DataFrame [sku, demanda_p10_141d, demanda_p50_141d, demanda_p90_141d], o None si no hay historia
    suficiente para entrenar (comportamiento original).

    Opcionales (todos apagados por defecto, no cambian el pronóstico):
      usar_disponibilidad=False  excluye av4/av13/av26/stock de las features (experimento "sin corrección").
      calcular_importancias=True estima la importancia de cada variable (permutation importance; es lento).
      devolver_extras=True       devuelve (DataFrame o None, extras) con extras = {'features': [...],
                                 'importancias': DataFrame [variable, importancia] o None}.
    """
    out, extras = _pronosticar_gbm(panel_full, stock, fecha_fin, hs, log, inflacion_sigma, usar_disponibilidad,
                                   calcular_importancias)
    return (out, extras) if devolver_extras else out


def importancia_permutacion(modelo, X, y, n_muestra=2000, n_repeticiones=5, semilla=42):
    """DataFrame [variable, importancia] (aumento del error absoluto medio al barajar cada variable)."""
    from sklearn.inspection import permutation_importance
    n = min(n_muestra, len(X))
    idx = np.random.default_rng(semilla).choice(len(X), n, replace=False)
    pi = permutation_importance(modelo, X.iloc[idx], np.asarray(y)[idx], n_repeats=n_repeticiones,
                                random_state=semilla, scoring='neg_mean_absolute_error')
    return (pd.DataFrame({'variable': list(X.columns), 'importancia': pi.importances_mean})
            .sort_values('importancia', ascending=False).reset_index(drop=True))


def _pronosticar_gbm(panel_full, stock, fecha_fin, hs, log, inflacion_sigma, usar_disponibilidad,
                     calcular_importancias):
    from sklearn.ensemble import HistGradientBoostingRegressor
    infl = INFLACION_SIGMA if inflacion_sigma is None else inflacion_sigma
    extras = {'features': [], 'importancias': None}
    skus, semanas, V, A, S = _matrices(panel_full, stock, fecha_fin)
    n, K = V.shape
    ht = max(1, int(round(hs)))
    cortes = list(range(CORTE_MIN, K - ht + 1))
    if len(cortes) < MIN_CORTES:
        log(f'  Modelo GBM: solo {K} semanas de historia (hacen falta ≥ {CORTE_MIN + MIN_CORTES + ht - 1}); '
            f'no se usa el modelo global.')
        return None, extras
    F = _Features(V, A, S)
    sin_stock = stock is None or not len(stock)
    if sin_stock or not usar_disponibilidad:
        cols = [c for c in FEATURES if c not in COLS_DISPONIBILIDAD]
    else:
        cols = list(FEATURES)
    extras['features'] = cols
    cs = F.cs
    Xs, ys = [], []
    for kk in cortes:
        m = (F.first >= 0) & (F.first < kk)
        if not m.any():
            continue
        Xs.append(F(kk)[cols][m])
        ys.append((cs[:, kk + ht] - cs[:, kk])[m])
    if not Xs or sum(len(x) for x in Xs) < MIN_FILAS:
        log('  Modelo GBM: muy pocas observaciones para entrenar; no se usa el modelo global.')
        return None, extras
    X = pd.concat(Xs, ignore_index=True)
    y = np.concatenate(ys)
    modelo = HistGradientBoostingRegressor(**PARAMS_GBM)
    modelo.fit(X, y)
    if calcular_importancias:
        # HistGradientBoostingRegressor no expone feature_importances_: se estima por permutación sobre una
        # muestra del entrenamiento. Solo bajo pedido (modo comparación / artículo): es lento.
        extras['importancias'] = importancia_permutacion(modelo, X, y)

    # Incertidumbre: var = a·mu + b·mu² ajustada a residuos FUERA DE MUESTRA (leave-one-corte-out).
    # Antes se usaban residuos de entrenamiento, que subestiman σ porque el modelo se ve a sí mismo.
    # Ahora: para cada corte k, se entrena sin él y se predicen los SKU de k → residuos OOS.
    oos_mu, oos_y = [], []
    # Separar filas por corte (cada corte contribuyó un bloque de filas)
    limites_corte = []
    pos = 0
    for kk in cortes:
        m = (F.first >= 0) & (F.first < kk)
        nk = int(m.sum())
        limites_corte.append((pos, pos + nk))
        pos += nk
    if len(cortes) >= 3:
        # LOO por corte: entrenar sin corte k, predecir corte k
        for i, kk in enumerate(cortes):
            lo, hi = limites_corte[i]
            if hi - lo == 0:
                continue
            X_train_loo = pd.concat([X.iloc[:lo], X.iloc[hi:]], ignore_index=True)
            y_train_loo = np.concatenate([y[:lo], y[hi:]])
            if len(X_train_loo) < MIN_FILAS:
                continue
            m_loo = HistGradientBoostingRegressor(**PARAMS_GBM)
            m_loo.fit(X_train_loo, y_train_loo)
            pred_k = np.clip(m_loo.predict(X.iloc[lo:hi]), 1e-6, None)
            oos_mu.append(pred_k)
            oos_y.append(y[lo:hi])
        log(f'  σ calibrado con {len(oos_mu)} folds leave-one-corte-out ({sum(len(m) for m in oos_mu):,} residuos OOS).')
    if len(oos_mu) >= 2:
        oos_mu_arr = np.concatenate(oos_mu)
        oos_y_arr = np.concatenate(oos_y)
        r2 = (oos_y_arr - oos_mu_arr) ** 2
        ab, *_ = np.linalg.lstsq(np.column_stack([oos_mu_arr, oos_mu_arr ** 2]), r2, rcond=None)
    else:
        # Fallback: si no hay suficientes cortes para LOO, usar residuos de entrenamiento (como antes)
        mu_tr = np.clip(modelo.predict(X), 1e-6, None)
        r2 = (y - mu_tr) ** 2
        ab, *_ = np.linalg.lstsq(np.column_stack([mu_tr, mu_tr ** 2]), r2, rcond=None)
        log('  σ: pocos cortes para LOO, se usan residuos de entrenamiento (sesgo optimista).')
    a, b = max(float(ab[0]), 0.0), max(float(ab[1]), 0.0)

    activos = F.first >= 0
    Xp = F(K)[cols][activos]
    mu = np.clip(modelo.predict(Xp), 0, None)
    sigma = infl * np.sqrt(a * mu + b * mu ** 2)
    esc = hs / ht
    p50 = mu * esc
    sg = sigma * esc
    out = pd.DataFrame({'sku': np.asarray(skus)[activos],
                        'demanda_p10_141d': np.clip(p50 - Z90 * sg, 0, None),
                        'demanda_p50_141d': p50,
                        'demanda_p90_141d': p50 + Z90 * sg})
    disp_txt = 'con disponibilidad' if (not sin_stock and usar_disponibilidad) else 'sin disponibilidad'
    log(f'  Modelo GBM (Poisson, {disp_txt}): {len(X):,} observaciones de {len(cortes)} cortes semanales, '
        f'{len(out):,} SKU pronosticados.')
    return out, extras
