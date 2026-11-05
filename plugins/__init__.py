# -*- coding: utf-8 -*-
"""Registro dinámico de integraciones con ERP.

    from plugins import listar_plugins, obtener_plugin
    listar_plugins()                 # {'negosy': NegosyPlugin, ...}
    obtener_plugin('negosy')         # instancia, o None

Si no hay ningún plugin configurado, PREVÉ funciona solo con importación manual de archivos.
"""
import importlib
import pkgutil

from plugins.base import PluginERP, registrar, registro
from preve_utils import get_logger

logger = get_logger('plugins')
_DESCUBIERTO = False


def descubrir_plugins():
    """Importa cada subpaquete de plugins/ para que sus clases se registren solas."""
    global _DESCUBIERTO
    if _DESCUBIERTO:
        return
    for info in pkgutil.iter_modules(__path__):
        if not info.ispkg:
            continue
        try:
            importlib.import_module(f'{__name__}.{info.name}')
        except ImportError:
            # un plugin con dependencias faltantes no debe impedir que la app arranque
            logger.exception('No se pudo cargar el plugin "%s"', info.name)
    _DESCUBIERTO = True


def listar_plugins():
    """{clave: clase} de los plugins disponibles."""
    descubrir_plugins()
    return registro()


def obtener_plugin(clave):
    """Instancia del plugin con esa clave, o None."""
    if not clave:
        return None
    cls = listar_plugins().get(clave)
    return cls() if cls else None


__all__ = ['PluginERP', 'registrar', 'descubrir_plugins', 'listar_plugins', 'obtener_plugin']
