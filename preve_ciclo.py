# -*- coding: utf-8 -*-
"""
Gestión del ciclo inteligente de compras de PREVÉ.

Ciclo internacional: 105 días (revisión) + 36 días (lead time) = 141 días horizonte.
Ciclo local:         30 días (revisión) + 10 días (lead time) =  40 días horizonte.

El usuario importa datos del ERP cada ~2 semanas (≈14 días).
Cada importación genera:
  - Un reporte de compra INTERNACIONAL con etiqueta según el momento del ciclo:
      • PRELIMINAR  (día 1–42  del ciclo, importaciones 1–3)
      • BORRADOR    (día 43–84, importaciones 4–6)
      • FINAL       (día 85–105, importación 7+)
  - Un reporte de compra LOCAL (horizonte 40d) con notas de compra para
    proveedores locales, si hay productos que necesitan reposición a corto plazo.

El archivo de estado del ciclo se guarda en `ciclo_estado.json` dentro de la
carpeta de salida para persistir entre ejecuciones.
"""
import os
import json
import datetime


# ──────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────
CICLO_INTERNACIONAL_DIAS = 105
LEAD_TIME_INTERNACIONAL  = 36
HORIZONTE_INTERNACIONAL  = CICLO_INTERNACIONAL_DIAS + LEAD_TIME_INTERNACIONAL  # 141

CICLO_LOCAL_DIAS  = 30
LEAD_TIME_LOCAL   = 10
HORIZONTE_LOCAL   = CICLO_LOCAL_DIAS + LEAD_TIME_LOCAL  # 40

INTERVALO_IMPORTACION = 14  # días entre importaciones

# Umbrales para la etiqueta del reporte (en días del ciclo)
_LIMITE_PRELIMINAR = 42   # día 1–42
_LIMITE_BORRADOR   = 84   # día 43–84
# día 85–105 → FINAL


class CicloCorruptoError(RuntimeError):
    """El archivo de estado del ciclo existe pero no se puede leer. Requiere intervención del usuario."""


def _ruta_estado(carpeta_salida):
    return os.path.join(carpeta_salida, 'ciclo_estado.json')


def cargar_estado_ciclo(carpeta_salida):
    """Carga el estado del ciclo desde disco.

    Returns: dict, o None si el archivo no existe (primera vez).
    Raises: CicloCorruptoError si el archivo existe pero está dañado.
    """
    ruta = _ruta_estado(carpeta_salida)
    if not os.path.exists(ruta):
        return None
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
    if not isinstance(estado, dict) or 'ciclo_inicio' not in estado:
        raise CicloCorruptoError(f'El archivo del ciclo de compras no tiene el formato esperado:\n{ruta}')
    return estado


def guardar_estado_ciclo(carpeta_salida, estado):
    """Persiste el estado del ciclo en disco."""
    ruta = _ruta_estado(carpeta_salida)
    with open(ruta, 'w', encoding='utf-8') as f:
        json.dump(estado, f, ensure_ascii=False, indent=2, default=str)


def iniciar_ciclo(carpeta_salida, fecha_inicio=None, guardar=True, anterior=None):
    """Inicia un nuevo ciclo de compras internacionales.

    anterior: estado del ciclo previo (si se omite, se lee de disco) para continuar la numeración.
    """
    fecha_inicio = fecha_inicio or datetime.date.today()
    estado = {
        'ciclo_inicio': str(fecha_inicio),
        'ciclo_dias': CICLO_INTERNACIONAL_DIAS,
        'importaciones': [],
        'ciclo_numero': 1,
    }
    # Buscar ciclo anterior para incrementar número
    if anterior is None:
        anterior = cargar_estado_ciclo(carpeta_salida)
    if anterior and 'ciclo_numero' in anterior:
        estado['ciclo_numero'] = anterior['ciclo_numero'] + 1
    if guardar:
        guardar_estado_ciclo(carpeta_salida, estado)
    return estado


def registrar_importacion(carpeta_salida, fecha=None, guardar=True):
    """Registra una nueva importación de datos en el ciclo actual.

    guardar=False devuelve la misma información sin escribir nada (vista previa al iniciar el análisis;
    la importación se confirma con guardar=True cuando el análisis termina bien).
    Varias importaciones el mismo día cuentan como una sola.

    Returns:
        dict con: dia_ciclo, importacion_num, etiqueta, dias_restantes,
                  fecha_limite_pedido, generar_local
    """
    fecha = fecha or datetime.date.today()
    estado = cargar_estado_ciclo(carpeta_salida)

    if estado is None:
        # Primera vez — iniciar ciclo automáticamente
        estado = iniciar_ciclo(carpeta_salida, fecha, guardar=False, anterior={})

    inicio = datetime.date.fromisoformat(estado['ciclo_inicio'])
    dia_ciclo = (fecha - inicio).days + 1  # 1-based

    # Si pasó el ciclo, iniciar uno nuevo automáticamente
    if dia_ciclo > CICLO_INTERNACIONAL_DIAS:
        # FIX: el ciclo nuevo arranca donde terminó el anterior (inicio + k·105), no el día de la
        # importación; si se saltaron ciclos enteros, la numeración avanza igual
        saltos = (dia_ciclo - 1) // CICLO_INTERNACIONAL_DIAS
        inicio = inicio + datetime.timedelta(days=saltos * CICLO_INTERNACIONAL_DIAS)
        anterior = dict(estado, ciclo_numero=estado.get('ciclo_numero', 1) + saltos - 1)
        estado = iniciar_ciclo(carpeta_salida, inicio, guardar=False, anterior=anterior)
        dia_ciclo = (fecha - inicio).days + 1

    # Registrar importación
    # FIX: repetir el análisis el mismo día no cuenta como una importación nueva
    importacion = {
        'fecha': str(fecha),
        'dia_ciclo': dia_ciclo,
    }
    if not estado['importaciones'] or estado['importaciones'][-1].get('fecha') != str(fecha):
        estado['importaciones'].append(importacion)
    importacion_num = len(estado['importaciones'])

    # Etiqueta del reporte
    if dia_ciclo <= _LIMITE_PRELIMINAR:
        etiqueta = 'PRELIMINAR'
    elif dia_ciclo <= _LIMITE_BORRADOR:
        etiqueta = 'BORRADOR'
    else:
        etiqueta = 'FINAL'

    dias_restantes = max(0, CICLO_INTERNACIONAL_DIAS - dia_ciclo)
    fecha_limite = inicio + datetime.timedelta(days=CICLO_INTERNACIONAL_DIAS - 1)

    # ¿Generar reporte local? Sí, siempre que se importan datos
    generar_local = True

    if guardar:
        guardar_estado_ciclo(carpeta_salida, estado)

    return {
        'dia_ciclo': dia_ciclo,
        'importacion_num': importacion_num,
        'etiqueta': etiqueta,
        'dias_restantes': dias_restantes,
        'fecha_limite_pedido': str(fecha_limite),
        'generar_local': generar_local,
        'ciclo_numero': estado.get('ciclo_numero', 1),
        'ciclo_inicio': estado['ciclo_inicio'],
    }


def resumen_ciclo_texto(info_ciclo):
    """Genera un texto resumen del estado del ciclo para mostrar en la UI."""
    e = info_ciclo['etiqueta']
    d = info_ciclo['dia_ciclo']
    r = info_ciclo['dias_restantes']
    n = info_ciclo['importacion_num']
    c = info_ciclo['ciclo_numero']

    barra = min(100, int(d / CICLO_INTERNACIONAL_DIAS * 100))

    lineas = [
        f'Ciclo #{c} — Día {d} de {CICLO_INTERNACIONAL_DIAS}  [{barra}%]',
        f'Importación #{n} del ciclo',
        f'Tipo de reporte: {e}',
        f'Días restantes para el pedido internacional: {r}',
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
