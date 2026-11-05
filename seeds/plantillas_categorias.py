# -*- coding: utf-8 -*-
"""
Plantillas de categorías de acción que vienen con PREVÉ (Fase 5).

Orden de las reglas = prioridad: gana la primera que se cumple. Por eso DESCONTINUAR (sin ventas hace
180 días) va antes que LIQUIDAR, y LIQUIDAR antes que REDUCIR.

FARMACIA (lotes por vencer) queda pendiente: necesita fechas de vencimiento por lote, que los reportes
actuales no traen.
"""


def c(variable, operador, valor):
    return {'variable': variable, 'operador': operador, 'valor': valor}


def var(variable, factor=1):
    return {'variable': variable, 'factor': factor}


# NEGOCIAR unificado (Fase 5.2): margen menor al 10%, en todas las plantillas y en la simulación
UMBRAL_NEGOCIAR = 0.10

PLANTILLA_RETAIL = {
    'nombre': 'RETAIL',
    'descripcion': 'Tienda minorista (predeterminada). RECOMPRAR solo cuando el stock no llega al próximo '
                   'pedido; MANTENER cuando alcanza; LIQUIDAR / REDUCIR el exceso; DESCONTINUAR lo que no se '
                   'vende hace 6 meses.',
    'acciones_de_compra': ['RECOMPRAR'],
    'accion_por_defecto': 'MANTENER',
    'motivo_por_defecto': 'Stock razonable para la demanda esperada: no hace falta ninguna acción.',
    'reglas': [
        {'accion': 'DESCONTINUAR', 'descripcion': 'Sin ventas en los últimos 180 días',
         'condiciones': [c('dias_sin_venta', '>=', 180)]},
        {'accion': 'RECOMPRAR', 'descripcion': 'El stock no alcanza para cubrir el lead time y el margen es positivo',
         'condiciones': [c('stock_dias', '<', var('lead_time')), c('margen', '>', 0)]},
        {'accion': 'LIQUIDAR', 'descripcion': 'Más de 90 días de stock y rotación baja',
         'condiciones': [c('stock_dias', '>', 90), c('rotacion', '<', 0.3)]},
        {'accion': 'NEGOCIAR', 'descripcion': f'Alto volumen con margen menor al {UMBRAL_NEGOCIAR:.0%}',
         'condiciones': [c('margen', '<', UMBRAL_NEGOCIAR), c('volumen_alto', '==', 1)]},
        {'accion': 'PUBLICITAR', 'descripcion': 'Buen margen pero vende menos del 70% de lo pronosticado',
         'condiciones': [c('margen', '>', 0.4), c('ventas_sem', '<', var('pronostico_sem', 0.7))]},
        {'accion': 'REDUCIR', 'descripcion': 'Más de 60 días de stock y stock mayor a 1.5 × la demanda del horizonte',
         'condiciones': [c('stock_dias', '>', 60), c('stock_actual', '>', var('demanda_horizonte', 1.5))]},
        {'accion': 'MANTENER', 'descripcion': 'El stock cubre entre el lead time y 60 días',
         'condiciones': [c('stock_dias', '>=', var('lead_time')), c('stock_dias', '<=', 60)]},
    ],
}

# La lógica que PREVÉ usó hasta la Fase 4 (_clasificar_accion), expresada como reglas, para comparar.
PLANTILLA_RETAIL_CLASICO = {
    'nombre': 'RETAIL_CLASICO',
    'descripcion': 'La lógica original de PREVÉ (hasta nov-2026): RECOMPRAR siempre que el stock más el '
                   'tránsito no cubra el horizonte completo. Útil para comparar con RETAIL.',
    'acciones_de_compra': ['RECOMPRAR'],
    'accion_por_defecto': 'REDUCIR',
    'motivo_por_defecto': 'Stock suficiente para el horizonte — no comprar por ahora.',
    'reglas': [
        {'accion': 'VIGILAR', 'descripcion': 'Producto nuevo o con poca historia — comprar lo mínimo y monitorear',
         'condiciones': [c('es_nuevo', '==', 1)]},
        {'accion': 'LIQUIDAR', 'descripcion': 'Sin demanda proyectada y con stock',
         'condiciones': [c('demanda_horizonte', '<=', 0), c('stock_actual', '>', 0)]},
        {'accion': 'LIQUIDAR', 'descripcion': 'El stock cubre más de 3 veces la demanda esperada',
         'condiciones': [c('demanda_horizonte', '>', 0), c('stock_actual', '>', var('demanda_horizonte', 3))]},
        {'accion': 'REDUCIR', 'descripcion': 'Stock holgado frente a la demanda — dejar que baje',
         'condiciones': [c('demanda_horizonte', '>', 0), c('stock_actual', '>', var('demanda_horizonte', 1.5))]},
        {'accion': 'PUBLICITAR', 'descripcion': 'Demanda cayendo con buen margen — publicitar para reactivar',
         'condiciones': [c('tendencia', '<', -0.3), c('clase_ab', '==', 1), c('margen', '>', 0.15)]},
        {'accion': 'NEGOCIAR', 'descripcion': 'Alta rotación (A) pero margen muy bajo — negociar mejor precio',
         'condiciones': [c('volumen_alto', '==', 1), c('margen', '>', 0), c('margen', '<', UMBRAL_NEGOCIAR)]},
        {'accion': 'RECOMPRAR', 'descripcion': 'Stock más tránsito bajo el nivel objetivo',
         'condiciones': [c('cantidad_a_pedir', '>', 0)]},
    ],
}

PLANTILLA_MANUFACTURA = {
    'nombre': 'MANUFACTURA',
    'descripcion': 'Insumos y productos fabricados: REABASTECER materia prima, PRODUCIR_MAS lo que vende por '
                   'encima del pronóstico, SUSTITUIR lo que deja de ser rentable.',
    'acciones_de_compra': ['REABASTECER', 'PRODUCIR_MAS'],
    'accion_por_defecto': 'MANTENER',
    'motivo_por_defecto': 'Stock y demanda equilibrados.',
    'reglas': [
        {'accion': 'DESCONTINUAR', 'descripcion': 'Sin ventas en los últimos 180 días',
         'condiciones': [c('dias_sin_venta', '>=', 180)]},
        {'accion': 'REABASTECER', 'descripcion': 'El stock no alcanza para el lead time',
         'condiciones': [c('stock_dias', '<', var('lead_time'))]},
        {'accion': 'PRODUCIR_MAS', 'descripcion': 'Vende 20% más de lo pronosticado y quedan menos de 60 días de stock',
         'condiciones': [c('ventas_sem', '>', var('pronostico_sem', 1.2)), c('stock_dias', '<', 60)]},
        {'accion': 'SUSTITUIR', 'descripcion': 'Margen menor al 5% y demanda en baja',
         'condiciones': [c('margen', '<', 0.05), c('tendencia', '<', 0)]},
    ],
}

PLANTILLA_DISTRIBUCION = {
    'nombre': 'DISTRIBUCION',
    'descripcion': 'Distribuidora con varias sucursales: REPONER lo que no llega al próximo pedido, '
                   'TRANSFERIR_SUCURSAL el exceso que sí rota, PROMOCIONAR lo que vende por debajo de lo esperado.',
    'acciones_de_compra': ['REPONER'],
    'accion_por_defecto': 'MANTENER',
    'motivo_por_defecto': 'Stock y demanda equilibrados.',
    'reglas': [
        {'accion': 'DESCONTINUAR', 'descripcion': 'Sin ventas en los últimos 180 días',
         'condiciones': [c('dias_sin_venta', '>=', 180)]},
        {'accion': 'REPONER', 'descripcion': 'El stock no alcanza para el lead time',
         'condiciones': [c('stock_dias', '<', var('lead_time'))]},
        {'accion': 'TRANSFERIR_SUCURSAL', 'descripcion': 'Más de 120 días de stock de un producto que sí se vende',
         'condiciones': [c('stock_dias', '>', 120), c('ventas_sem', '>', 0)]},
        {'accion': 'PROMOCIONAR', 'descripcion': 'Margen > 30% pero vende menos del 70% de lo pronosticado',
         'condiciones': [c('margen', '>', 0.3), c('ventas_sem', '<', var('pronostico_sem', 0.7))]},
    ],
}

PLANTILLAS_SISTEMA = [PLANTILLA_RETAIL, PLANTILLA_RETAIL_CLASICO, PLANTILLA_MANUFACTURA, PLANTILLA_DISTRIBUCION]


def sembrar(db=None, forzar=False):
    """Crea las plantillas del sistema que falten (o reemplaza las del formato antiguo de la Fase 3).

    No pisa una plantilla que el usuario ya editó, salvo forzar=True.
    """
    from database import Database
    from preve_reglas import guardar_plantilla
    db = db or Database.default()
    existentes = db.listar_plantillas_categorias()
    actuales = {} if existentes.empty else dict(zip(existentes['nombre'], existentes['reglas']))
    creadas = []
    for p in PLANTILLAS_SISTEMA:
        guardada = actuales.get(p['nombre'])
        if forzar or guardada is None or not isinstance(guardada, dict):
            guardar_plantilla(p, db, es_sistema=True)
            creadas.append(p['nombre'])
    return creadas
