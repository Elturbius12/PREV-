# -*- coding: utf-8 -*-
"""
PREVÉ — Motor de reglas de categorías de acción (Fase 5).

Una plantilla es una lista ordenada de reglas; a cada producto se le asigna la acción de la PRIMERA
regla que cumple (si ninguna cumple, `accion_por_defecto`). Una regla es una lista de condiciones
unidas con Y. Varias reglas pueden llevar a la misma acción (eso es un O).

    {"accion": "LIQUIDAR",
     "condiciones": [{"variable": "stock_dias", "operador": ">", "valor": 90},
                     {"variable": "rotacion", "operador": "<", "valor": 0.3}]}

El valor de una condición puede ser un número o otra variable por un factor:
    {"variable": "ventas_sem", "operador": "<", "valor": {"variable": "pronostico_sem", "factor": 0.7}}

Nada se evalúa como código: solo variables, operadores y números de listas cerradas. Así el editor
visual no puede producir reglas que rompan el programa.
"""
import json
import operator

import numpy as np
import pandas as pd

from preve_utils import get_logger

logger = get_logger('reglas')

# variable → (descripción para el editor, unidad)
VARIABLES = {
    'stock_dias': ('Días que alcanza el stock (stock ÷ venta diaria esperada)', 'días'),
    'lead_time': ('Lead time del ciclo', 'días'),
    'margen': ('Margen sobre el precio sin impuesto (0.25 = 25%)', 'fracción'),
    'rotacion': ('Rotación en el horizonte (demanda del horizonte ÷ stock)', 'veces'),
    'ventas_sem': ('Venta real promedio de las últimas 4 semanas', 'u/sem'),
    'pronostico_sem': ('Venta semanal pronosticada', 'u/sem'),
    'demanda_horizonte': ('Demanda pronosticada para todo el horizonte', 'u'),
    'stock_actual': ('Stock actual', 'u'),
    'cantidad_a_pedir': ('Cantidad a pedir para llegar al nivel objetivo', 'u'),
    'dias_sin_venta': ('Días desde la última venta', 'días'),
    'tendencia': ('Tendencia de venta de las últimas 8 semanas', 'u/sem'),
    'volumen_alto': ('Producto clase A (1 = sí, 0 = no)', '0/1'),
    'clase_ab': ('Producto clase A o B (1 = sí, 0 = no)', '0/1'),
    'es_nuevo': ('Producto nuevo o sin historia suficiente (1 = sí, 0 = no)', '0/1'),
}
OPERADORES = {'<': operator.lt, '<=': operator.le, '>': operator.gt, '>=': operator.ge,
              '==': operator.eq, '!=': operator.ne}
TEXTO_OPERADOR = {'<': 'menor que', '<=': 'menor o igual que', '>': 'mayor que', '>=': 'mayor o igual que',
                  '==': 'igual a', '!=': 'distinto de'}


class ReglaInvalida(ValueError):
    pass


# ══════════════════════════════════════════════════════════════════
# Validación de plantillas
# ══════════════════════════════════════════════════════════════════
def _validar_valor(v, donde):
    if isinstance(v, bool):
        raise ReglaInvalida(f'{donde}: el valor debe ser un número')
    if isinstance(v, (int, float)):
        return
    if isinstance(v, dict):
        if v.get('variable') not in VARIABLES:
            raise ReglaInvalida(f'{donde}: variable de comparación desconocida "{v.get("variable")}"')
        if not isinstance(v.get('factor', 1), (int, float)) or isinstance(v.get('factor', 1), bool):
            raise ReglaInvalida(f'{donde}: el factor debe ser un número')
        return
    raise ReglaInvalida(f'{donde}: el valor debe ser un número o {{"variable", "factor"}}')


def validar_plantilla(p):
    """Lanza ReglaInvalida con un mensaje claro si la plantilla está mal formada."""
    if not isinstance(p, dict) or not isinstance(p.get('reglas'), list):
        raise ReglaInvalida('La plantilla debe tener una lista "reglas"')
    if not p['reglas']:
        raise ReglaInvalida('La plantilla necesita al menos una regla')
    if not str(p.get('accion_por_defecto', '')).strip():
        raise ReglaInvalida('Falta la acción por defecto (cuando ninguna regla se cumple)')
    for i, r in enumerate(p['reglas'], 1):
        if not str(r.get('accion', '')).strip():
            raise ReglaInvalida(f'Regla {i}: falta la acción')
        conds = r.get('condiciones')
        if not isinstance(conds, list) or not conds:
            raise ReglaInvalida(f'Regla {i} ({r.get("accion")}): necesita al menos una condición')
        for j, c in enumerate(conds, 1):
            donde = f'Regla {i} ({r["accion"]}), condición {j}'
            if c.get('variable') not in VARIABLES:
                raise ReglaInvalida(f'{donde}: variable desconocida "{c.get("variable")}"')
            if c.get('operador') not in OPERADORES:
                raise ReglaInvalida(f'{donde}: operador desconocido "{c.get("operador")}"')
            _validar_valor(c.get('valor'), donde)
    compra = p.get('acciones_de_compra', [])
    if not isinstance(compra, list):
        raise ReglaInvalida('"acciones_de_compra" debe ser una lista')
    return p


def acciones(p):
    """Acciones posibles de la plantilla, en orden de aparición."""
    vistas = []
    for a in [r['accion'] for r in p['reglas']] + [p['accion_por_defecto']]:
        if a not in vistas:
            vistas.append(a)
    return vistas


def describir_condicion(c):
    v = c['valor']
    if isinstance(v, dict):
        f = v.get('factor', 1)
        destino = v['variable'] if f == 1 else f'{f:g} × {v["variable"]}'
    else:
        destino = f'{v:g}'
    return f'{c["variable"]} {c["operador"]} {destino}'


def describir_regla(r):
    return ' y '.join(describir_condicion(c) for c in r['condiciones'])


# ══════════════════════════════════════════════════════════════════
# Variables a partir de la recomendación
# ══════════════════════════════════════════════════════════════════
def calcular_variables(df, horizonte_dias, lead_time_dias):
    """DataFrame con una columna por variable de VARIABLES (NaN si el dato no existe)."""
    def col(nombre, defecto=np.nan):
        return pd.to_numeric(df[nombre], errors='coerce') if nombre in df.columns else pd.Series(defecto, index=df.index)

    demanda = col('demanda_p50_141d').clip(lower=0)
    stock = col('stock_actual').fillna(0)
    venta_dia = demanda / float(horizonte_dias)
    v = pd.DataFrame(index=df.index)
    v['stock_dias'] = np.where(venta_dia > 0, stock.clip(lower=0) / venta_dia.where(venta_dia > 0), np.inf)
    v['lead_time'] = float(lead_time_dias)
    # margen desconocido (sin costo o precio) = NaN: no cumple ninguna condición sobre el margen
    margen = col('margen_pct')
    if 'dato_incompleto' in df.columns:
        incompleto = df['dato_incompleto'].astype(str).str.lower().isin(['true', '1'])
        margen = margen.where(~incompleto)
    v['margen'] = margen
    v['rotacion'] = np.where(stock > 0, demanda / stock.where(stock > 0), np.inf)
    v['ventas_sem'] = col('venta_sem_reciente')
    v['pronostico_sem'] = demanda / (float(horizonte_dias) / 7.0)
    v['demanda_horizonte'] = demanda
    v['stock_actual'] = stock
    v['cantidad_a_pedir'] = col('cantidad_a_pedir') if 'cantidad_a_pedir' in df.columns else col('compra_sugerida_unid')
    v['dias_sin_venta'] = col('dias_sin_venta')
    v['tendencia'] = col('tendencia_venta').fillna(0)
    clase = df['clase_abc'].astype(str) if 'clase_abc' in df.columns else pd.Series('C', index=df.index)
    v['volumen_alto'] = (clase == 'A').astype(float)
    v['clase_ab'] = clase.isin(['A', 'B']).astype(float)
    seg = df['segmento'].astype(str) if 'segmento' in df.columns else pd.Series('', index=df.index)
    v['es_nuevo'] = seg.str.contains('Lanzamiento|Sin historia', case=False, regex=True).astype(float)
    return v


# ══════════════════════════════════════════════════════════════════
# Clasificación
# ══════════════════════════════════════════════════════════════════
def _mascara(c, variables):
    izq = variables[c['variable']]
    val = c['valor']
    der = variables[val['variable']] * val.get('factor', 1) if isinstance(val, dict) else float(val)
    with np.errstate(invalid='ignore'):
        m = OPERADORES[c['operador']](izq, der)
    return pd.Series(m, index=variables.index).fillna(False) & izq.notna()


def _fmt(x):
    if pd.isna(x):
        return '—'
    if np.isinf(x):
        return '∞'
    return f'{x:.2f}' if abs(x) < 10 and x != int(x) else f'{x:,.0f}'


def clasificar(df, plantilla, horizonte_dias=141, lead_time_dias=36):
    """Devuelve DataFrame con accion, motivo_accion, regla_aplicada (nº, 0 = por defecto) y es_compra."""
    validar_plantilla(plantilla)
    variables = calcular_variables(df, horizonte_dias, lead_time_dias)
    accion = pd.Series(plantilla['accion_por_defecto'], index=df.index, dtype=object)
    regla_n = pd.Series(0, index=df.index)
    pendiente = pd.Series(True, index=df.index)
    for n, r in enumerate(plantilla['reglas'], 1):
        m = pendiente.copy()
        for c in r['condiciones']:
            m &= _mascara(c, variables)
        accion[m] = r['accion']
        regla_n[m] = n
        pendiente &= ~m
    compra = set(plantilla.get('acciones_de_compra', []))
    cantidad = variables['cantidad_a_pedir'].fillna(0)

    motivos = []
    for i in df.index:
        n = regla_n[i]
        if n == 0:
            txt = plantilla.get('motivo_por_defecto') or 'Ninguna regla especial aplica a este producto.'
        else:
            r = plantilla['reglas'][n - 1]
            partes = [f'{c["variable"]} = {_fmt(variables.at[i, c["variable"]])}' for c in r['condiciones']]
            txt = (r.get('descripcion') or f'Cumple {describir_regla(r)}') + f' ({", ".join(partes)}).'
        if accion[i] in compra and cantidad[i] > 0:
            txt += f' Comprar {cantidad[i]:,.0f} unidades.'
        motivos.append(txt)
    return pd.DataFrame({'accion': accion, 'motivo_accion': motivos, 'regla_aplicada': regla_n,
                         'es_compra': accion.isin(compra) & (cantidad > 0)}, index=df.index)


def vista_previa(df, plantilla, horizonte_dias=141, lead_time_dias=36):
    """{acción: nº de productos} con los datos actuales (para el editor)."""
    res = clasificar(df, plantilla, horizonte_dias, lead_time_dias)
    conteo = res['accion'].value_counts()
    return {a: int(conteo.get(a, 0)) for a in acciones(plantilla)}


def cargar_plantilla(nombre, db=None):
    """Plantilla guardada en la base (dict con reglas, accion_por_defecto, acciones_de_compra…)."""
    from database import Database
    db = db or Database.default()
    df = db.listar_plantillas_categorias()
    fila = df[df['nombre'] == nombre] if not df.empty else df
    if fila.empty:
        return None
    p = dict(fila.iloc[0]['reglas'])
    p['nombre'] = nombre
    p.setdefault('descripcion', fila.iloc[0]['descripcion'])
    return p


def guardar_plantilla(p, db=None, es_sistema=False):
    from database import Database
    validar_plantilla(p)
    db = db or Database.default()
    cuerpo = {k: p[k] for k in ('reglas', 'accion_por_defecto', 'acciones_de_compra', 'motivo_por_defecto',
                                'colores', 'version') if k in p}
    db.guardar_plantilla_categorias(p['nombre'], cuerpo, p.get('descripcion', ''), es_plantilla_sistema=es_sistema)


def copiar(p):
    return json.loads(json.dumps(p))
