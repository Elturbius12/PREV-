# -*- coding: utf-8 -*-
"""Plugin de Negosy ERP: descarga los 5 reportes por tienda con Playwright (ver extractor.py)."""
from plugins.base import PluginERP, registrar
from preve_utils import log_info


@registrar
class NegosyPlugin(PluginERP):
    clave = 'negosy'
    nombre = 'Negosy'
    descripcion = ('Descarga automática de productos, ventas, compras y movimientos de almacén desde Negosy. '
                   'Requiere NEGOSY_USUARIO y NEGOSY_CONTRASENA en el archivo .env.')
    variables_credenciales = {'usuario': 'NEGOSY_USUARIO', 'contrasena': 'NEGOSY_CONTRASENA'}

    def opciones_disponibles(self):
        """Tiendas de Negosy que se pueden descargar."""
        from plugins.negosy.extractor import TIENDAS
        return {'tiendas': list(TIENDAS)}

    def extraer(self, carpeta_destino, opciones=None, log=log_info):
        """Descarga los 5 reportes de las tiendas elegidas a una subcarpeta con fecha y la devuelve."""
        from plugins.negosy.extractor import carpeta_descarga, extraer_datos
        cred = self.credenciales()
        if not all(cred.values()):
            raise RuntimeError('Faltan las credenciales de Negosy: define NEGOSY_USUARIO y NEGOSY_CONTRASENA '
                               'en el archivo .env (ver .env.example).')
        opciones = opciones or {}
        carpeta = carpeta_descarga(carpeta_destino)
        extraer_datos(carpeta, cred['usuario'], cred['contrasena'],
                      tiendas=opciones.get('tiendas') or ['san_jose'],
                      headless=opciones.get('headless', True), log=log, crear_subcarpeta=False)
        return carpeta
