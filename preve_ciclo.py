# -*- coding: utf-8 -*-
"""
Gestión del ciclo inteligente de compras de PREVÉ.

La duración del ciclo, el lead time y el horizonte vienen de la tabla `ciclos_compra` (Fase 3);
las constantes de abajo son solo los valores por defecto (ciclo internacional de Yuju Store).

El usuario importa datos del ERP cada ~2 semanas. Cada importación genera un reporte con etiqueta
según el momento del ciclo:
    • PRELIMINAR  (primer 40% del ciclo; día 1–42 en un ciclo de 105)
    • BORRADOR    (40%–80%; día 43–84)
    • FINAL       (último 20%; día 85–105)

Estado del ciclo: con `sucursal` se guarda en la base (configuracion['ciclo_estado:<sucursal>']);
sin `sucursal` (o si la base aún no lo tiene) se usa/importa `ciclo_estado.json` de la carpeta de salida.
"""
import os
import json
import datetime


# ──────────────────────────────────────────────────────────
# Valores por defecto (los reales se leen de ciclos_compra)
# ──────────────────────────────────────────────────────────
CICLO_INTERNACIONAL_DIAS = 105
LEAD_TIME_INTERNACIONAL  = 36
HORIZONTE_INTERNACIONAL  = CICLO_INTERNACIONAL_DIAS + LEAD_TIME_INTERNACIONAL  # 141

CICLO_LOCAL_DIAS  = 30
LEAD_TIME_LOCAL   = 10
HORIZONTE_LOCAL   = CICLO_LOCAL_DIAS + LEAD_TIME_LOCAL  # 40

INTERVALO_IMPORTACION = 14  # días entre importaciones

# Fracciones del ciclo para la etiqueta del reporte
FRACCION_PRELIMINAR = 0.4
FRACCION_BORRADOR = 0.8


class CicloCorruptoError(RuntimeError):
    """El estado del ciclo existe pero no se puede leer. Requiere intervención del usuario."""


def limites_etiqueta(duracion_dias):
    """(último día PRELIMINAR, último día BORRADOR) para un ciclo de esa duración. 105 → (42, 84)."""
    return round(duracion_dias * FRACCION_PRELIMINAR), round(duracion_dias * FRACCION_BORRADOR)


def _ruta_estado(carpeta_salida):
    return os.path.join(carpeta_salida, 'ciclo_estado.json')


def _clave(sucursal):
    from database import slug
    return f'ciclo_estado:{slug(sucursal)}'


def _validar(estado, origen):
    if not isinstance(estado, dict) or 'ciclo_inicio' not in estado:
        raise CicloCorruptoError(f'El estado del ciclo de compras no tiene el formato esperado:\n{origen}')
    return estado


def _leer_json(ruta):
    # FIX: antes un archivo corrupto devolvía None y el ciclo se reiniciaba en #1, perdiendo el historial
    try:
        with open(ruta, 'r', encoding='utf-8') as f:
            estado = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        raise CicloCorruptoError(
            f'El archivo del ciclo de compras está dañado y no se puede leer:\n{ruta}\n\n'
            f'Para no perder el número de ciclo, PREVÉ no lo reinicia automáticamente. Repáralo o '
            f'restáuralo desde una copia; si prefieres empezar un ciclo nuevo, renómbralo o bórralo.'
            f'\n\nDetalle: {e}') from e
    return _validar(estado, ruta)


def cargar_estado_ciclo(carpeta_salida, sucursal=None):
    """Carga el estado del ciclo.

    Returns: dict, o None si todavía no hay ciclo (primera vez).
    Raises: CicloCorruptoError si el estado existe pero está dañado.
    """
    if sucursal:
        from database import Database
        db = Database.default()
        estado = db.get_config(_clave(sucursal))
        if estado is not None:
            return _validar(estado, f'base de datos ({_clave(sucursal)})')
    ruta = _ruta_estado(carpeta_salida) if carpeta_salida else None
    if not ruta or not os.path.exists(ruta):
        return None
    estado = _leer_json(ruta)
    if sucursal:
        # FIX (Fase 3): el ciclo_estado.json antiguo se importa a la base una sola vez
        db.set_config(_clave(sucursal), estado, descripcion='Estado del ciclo (importado de ciclo_estado.json)')
    return estado


def guardar_estado_ciclo(carpeta_salida, estado, sucursal=None):
    """Persiste el estado del ciclo (en la base si hay sucursal; si no, en ciclo_estado.json)."""
    if sucursal:
        from database import Database
        Database.default().set_config(_clave(sucursal), json.loads(json.dumps(estado, default=str)))
        return
    with open(_ruta_estado(carpeta_salida), 'w', encoding='utf-8') as f:
        json.dump(estado, f, ensure_ascii=False, indent=2, default=str)


def iniciar_ciclo(carpeta_salida, fecha_inicio=None, guardar=True, anterior=None, sucursal=None,
                  duracion_dias=CICLO_INTERNACIONAL_DIAS):
    """Inicia un nuevo ciclo de compras.

    anterior: estado del ciclo previo (si se omite, se lee) para continuar la numeración.
    """
    fecha_inicio = fecha_inicio or datetime.date.today()
    estado = {
        'ciclo_inicio': str(fecha_inicio),
        'ciclo_dias': int(duracion_dias),
        'importaciones': [],
        'ciclo_numero': 1,
    }
    if anterior is None:
        anterior = cargar_estado_ciclo(carpeta_salida, sucursal)
    if anterior and 'ciclo_numero' in anterior:
        estado['ciclo_numero'] = anterior['ciclo_numero'] + 1
    if guardar:
        guardar_estado_ciclo(carpeta_salida, estado, sucursal)
    return estado


def registrar_importacion(carpeta_salida, fecha=None, guardar=True, sucursal=None,
                          duracion_dias=CICLO_INTERNACIONAL_DIAS):
    """Registra una nueva importación de datos en el ciclo actual.

    guardar=False devuelve la misma información sin escribir nada (vista previa al iniciar el análisis;
    la importación se confirma con guardar=True cuando el análisis termina bien).
    Varias importaciones el mismo día cuentan como una sola.

    Returns:
        dict con: dia_ciclo, importacion_num, etiqueta, dias_restantes,
                  fecha_limite_pedido, generar_local, ciclo_numero, ciclo_inicio, ciclo_dias
    """
    fecha = fecha or datetime.date.today()
    duracion_dias = int(duracion_dias)
    estado = cargar_estado_ciclo(carpeta_salida, sucursal)

    if estado is None:
        # Primera vez — iniciar ciclo automáticamente
        estado = iniciar_ciclo(carpeta_salida, fecha, guardar=False, anterior={}, sucursal=sucursal,
                               duracion_dias=duracion_dias)

    inicio = datetime.date.fromisoformat(estado['ciclo_inicio'])
    dia_ciclo = (fecha - inicio).days + 1  # 1-based

    # Si pasó el ciclo, iniciar uno nuevo automáticamente
    if dia_ciclo > duracion_dias:
        # FIX: el ciclo nuevo arranca donde terminó el anterior (inicio + k·duración), no el día de la
        # importación; si se saltaron ciclos enteros, la numeración avanza igual
        saltos = (dia_ciclo - 1) // duracion_dias
        inicio = inicio + datetime.timedelta(days=saltos * duracion_dias)
        anterior = dict(estado, ciclo_numero=estado.get('ciclo_numero', 1) + saltos - 1)
        estado = iniciar_ciclo(carpeta_salida, inicio, guardar=False, anterior=anterior, sucursal=sucursal,
                               duracion_dias=duracion_dias)
        dia_ciclo = (fecha - inicio).days + 1

    # FIX: repetir el análisis el mismo día no cuenta como una importación nueva
    importacion = {
        'fecha': str(fecha),
        'dia_ciclo': dia_ciclo,
    }
    if not estado['importaciones'] or estado['importaciones'][-1].get('fecha') != str(fecha):
        estado['importaciones'].append(importacion)
    importacion_num = len(estado['importaciones'])

    # Etiqueta del reporte
    lim_pre, lim_bor = limites_etiqueta(duracion_dias)
    if dia_ciclo <= lim_pre:
        etiqueta = 'PRELIMINAR'
    elif dia_ciclo <= lim_bor:
        etiqueta = 'BORRADOR'
    else:
        etiqueta = 'FINAL'

    dias_restantes = max(0, duracion_dias - dia_ciclo)
    fecha_limite = inicio + datetime.timedelta(days=duracion_dias - 1)

    # ¿Generar reporte local? Sí, siempre que se importan datos
    generar_local = True

    if guardar:
        estado['ciclo_dias'] = duracion_dias
        guardar_estado_ciclo(carpeta_salida, estado, sucursal)

    return {
        'dia_ciclo': dia_ciclo,
        'importacion_num': importacion_num,
        'etiqueta': etiqueta,
        'dias_restantes': dias_restantes,
        'fecha_limite_pedido': str(fecha_limite),
        'generar_local': generar_local,
        'ciclo_numero': estado.get('ciclo_numero', 1),
        'ciclo_inicio': estado['ciclo_inicio'],
        'ciclo_dias': duracion_dias,
    }


def resumen_ciclo_texto(info_ciclo):
    """Genera un texto resumen del estado del ciclo para mostrar en la UI."""
    e = info_ciclo['etiqueta']
    d = info_ciclo['dia_ciclo']
    r = info_ciclo['dias_restantes']
    n = info_ciclo['importacion_num']
    c = info_ciclo['ciclo_numero']
    total = info_ciclo.get('ciclo_dias', CICLO_INTERNACIONAL_DIAS)

    barra = min(100, int(d / total * 100))

    lineas = [
        f'Ciclo #{c} — Día {d} de {total}  [{barra}%]',
        f'Importación #{n} del ciclo',
        f'Tipo de reporte: {e}',
        f'Días restantes para el pedido: {r}',
        f'Fecha límite del pedido: {info_ciclo["fecha_limite_pedido"]}',
    ]

    if e == 'PRELIMINAR':
        lineas.append('→ Los pronósticos aún se están afinando. Usa este reporte para '
                       'identificar tendencias, no para hacer pedidos definitivos.')
    elif e == 'BORRADOR':
        lineas.append('→ Los pronósticos ya tienen buena base. Revisa las cantidades '
                       'sugeridas y valida con tu equipo antes del reporte final.')
    else:
        lineas.append('→ REPORTE FINAL: las cantidades sugeridas están listas para '
                       'convertirse en órdenes de compra. Revisa y confirma.')

    return '\n'.join(lineas)
