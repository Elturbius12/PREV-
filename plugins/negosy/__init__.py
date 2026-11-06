# -*- coding: utf-8 -*-
"""Plugin de Negosy ERP: descarga los 5 reportes por tienda con Playwright (ver extractor.py).

Qué se descarga y a dónde lo decide este plugin (Fase 10); el extractor solo ejecuta la descarga:
  • URLs y credenciales salen de la configuración (tiendas.py), con el .env como respaldo.
  • El rango es incremental: desde el día siguiente al último período importado hasta AYER.
    Sin período previo es la primera carga histórica (730 días).
  • Cada descarga va a <entrada>/AAAA-MM-DD_a_AAAA-MM-DD y solo cuenta como importada cuando terminó bien.
"""
import glob
import os
import shutil

from plugins.base import PluginERP, registrar
from preve_utils import (log_info, rango_descarga, periodos_importados, quitar_marca_descarga,
                         marcar_descarga_completa)

#: patrones (por tienda) de los 5 reportes que forman una descarga completa
_REPORTES = ('listado_productos', 'reporte_ventas_producto', 'reporte_compras_producto',
             'reporte_productos_movimiento_entradas', 'reporte_productos_movimiento_salidas')


def archivos_faltantes(carpeta, tienda):
    """Reportes de la tienda que no están en la carpeta del período."""
    return [r for r in _REPORTES if not glob.glob(os.path.join(carpeta, f'{r}_{tienda}_*.xlsx'))]


def ultimo_listado(carpeta_entrada, tienda):
    """Ruta del listado de productos más reciente de esa tienda entre los períodos ya importados (o None)."""
    candidatos = []
    for periodo in periodos_importados(carpeta_entrada):
        candidatos += glob.glob(os.path.join(periodo, f'listado_productos_{tienda}_*.xlsx'))
    # el nombre termina en la fecha de descarga (AAAAMMDD): ordena por ella
    return max(candidatos, key=lambda r: os.path.basename(r)) if candidatos else None


@registrar
class NegosyPlugin(PluginERP):
    clave = 'negosy'
    nombre = 'Negosy'
    descripcion = ('Descarga automática de productos, ventas, compras y movimientos de almacén desde Negosy. '
                   'Usuario, contraseña y URLs se configuran aquí mismo (o con NEGOSY_USUARIO y '
                   'NEGOSY_CONTRASENA en el archivo .env).')
    variables_credenciales = {'usuario': 'NEGOSY_USUARIO', 'contrasena': 'NEGOSY_CONTRASENA'}

    # ── credenciales: primero lo guardado en Configuración, después el .env ──
    def _guardadas(self):
        from preve_config import Config
        cfg = Config.actual()
        return {'usuario': (cfg.get('negosy_usuario') or '').strip(), 'contrasena': cfg.get('negosy_contrasena') or ''}

    def credenciales(self):
        guardadas = self._guardadas()
        return guardadas if all(guardadas.values()) else super().credenciales()

    def origen_credenciales(self):
        """'configuracion', 'env' o None."""
        if all(self._guardadas().values()):
            return 'configuracion'
        return 'env' if all(PluginERP.credenciales(self).values()) else None

    def credenciales_en_ambos_lados(self):
        """True si hay credenciales completas en Configuración y también en el .env."""
        return all(self._guardadas().values()) and all(PluginERP.credenciales(self).values())

    def opciones_disponibles(self):
        """Tiendas de Negosy (una por sucursal activa) que se pueden descargar."""
        from plugins.negosy.tiendas import tiendas_configuradas
        return {'tiendas': list(tiendas_configuradas())}

    def extraer(self, carpeta_destino, opciones=None, log=log_info, sucursal=None):
        """Descarga los reportes nuevos a <carpeta_destino>/AAAA-MM-DD_a_AAAA-MM-DD y devuelve esa carpeta.

        Devuelve None si no hay nada nuevo que descargar (los datos ya llegan hasta ayer).
        """
        from preve_config import Config
        from plugins.negosy import extractor as ex
        from plugins.negosy.tiendas import tiendas_configuradas, tienda_de_sucursal, login_url
        cred = self.credenciales()
        if not all(cred.values()):
            raise RuntimeError('Faltan las credenciales de Negosy: guárdalas en Configuración › Integración con '
                               'tu ERP, o define NEGOSY_USUARIO y NEGOSY_CONTRASENA en el archivo .env.')
        opciones = opciones or {}
        cfg = Config.actual()
        tiendas_cfg = tiendas_configuradas(cfg)
        por_defecto = tienda_de_sucursal(sucursal, cfg) if sucursal else None
        claves = list(opciones.get('tiendas') or ([por_defecto] if por_defecto else list(tiendas_cfg)[:1]))
        desconocidas = [k for k in claves if k not in tiendas_cfg]
        if desconocidas or not claves:
            raise RuntimeError(f'Tienda de Negosy no configurada: {", ".join(desconocidas) or "ninguna"}. '
                               'Revisa las sucursales activas en Configuración.')
        for k in claves:
            if not tiendas_cfg[k]['base_url']:
                raise RuntimeError(f'Falta la URL base de Negosy para {tiendas_cfg[k]["nombre"]}: '
                                   'escríbela en Configuración › Integración con tu ERP.')

        rango = rango_descarga(carpeta_destino)
        if rango is None:
            log('Ya tienes los datos hasta ayer: no hay nada nuevo que descargar.')
            return None
        inicio, fin = rango
        carpeta = ex.carpeta_descarga(carpeta_destino, inicio, fin)
        quitar_marca_descarga(carpeta)      # hasta que termine bien, este período no cuenta como importado
        log(f'Período a descargar: {inicio.isoformat()} a {fin.isoformat()} → {carpeta}')

        # Catálogo: es la fuente del stock, por eso por defecto se descarga siempre. Copiar el último solo
        # tiene sentido para pruebas (el stock quedaría con la fecha de ese catálogo).
        actualizar = opciones.get('actualizar_catalogo')
        if actualizar is None:
            actualizar = bool(cfg.get('negosy_actualizar_catalogo'))
        sin_listado = set()
        for k in claves:
            previo = None if actualizar else ultimo_listado(carpeta_destino, k)
            if previo:
                shutil.copy2(previo, os.path.join(carpeta, os.path.basename(previo)))
                sin_listado.add(k)
                log(f'Catálogo: copiado del período anterior ({os.path.basename(previo)}). ⚠ El stock sale de '
                    'ese catálogo: puede estar desactualizado.')
            else:
                log('Catálogo: descargado de Negosy')

        ex.extraer_datos(carpeta, cred['usuario'], cred['contrasena'], tiendas=claves,
                         fecha_inicio=inicio, fecha_fin=fin, headless=opciones.get('headless', True), log=log,
                         crear_subcarpeta=False, login_url=login_url(cfg), tiendas_config=tiendas_cfg,
                         tiendas_sin_listado=sin_listado)

        faltan = {k: archivos_faltantes(carpeta, k) for k in claves}
        faltan = {k: v for k, v in faltan.items() if v}
        if faltan:
            detalle = '; '.join(f'{tiendas_cfg[k]["nombre"]}: {", ".join(v)}' for k, v in faltan.items())
            raise RuntimeError(f'La descarga quedó incompleta ({detalle}). No se marcó como importada: '
                               'vuelve a intentarlo y se reutilizará la misma carpeta.')
        marcar_descarga_completa(carpeta)
        return carpeta
