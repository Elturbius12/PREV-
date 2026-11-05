# -*- coding: utf-8 -*-
"""Interfaz que debe cumplir cualquier integración con un ERP (Fase 4).

Para agregar un ERP nuevo: crea `plugins/<nombre>/__init__.py` con una subclase de PluginERP decorada
con `@registrar`. Se detecta sola al iniciar (ver plugins.descubrir_plugins).
"""
from preve_utils import log_info
import os
from abc import ABC, abstractmethod


class PluginERP(ABC):
    """Descarga los reportes de un ERP a una carpeta para que PREVÉ los analice."""

    #: identificador estable (se guarda en configuracion['plugin_erp'])
    clave = ''
    #: nombre para mostrar
    nombre = ''
    descripcion = ''
    #: variables de entorno (.env) con las credenciales, p. ej. {'usuario': 'NEGOSY_USUARIO'}
    variables_credenciales = {}

    def credenciales(self):
        """{campo: valor} leídas del entorno; las que falten quedan vacías."""
        return {campo: os.getenv(var, '') for campo, var in self.variables_credenciales.items()}

    def credenciales_completas(self):
        """True si todas las credenciales están definidas en el entorno."""
        return all(self.credenciales().values())

    def opciones_disponibles(self):
        """Opciones que el usuario puede elegir (p. ej. tiendas). {opción: [valores posibles]}."""
        return {}

    @abstractmethod
    def extraer(self, carpeta_destino, opciones=None, log=log_info):
        """Descarga los reportes. Devuelve la carpeta donde quedaron (debe ser legible por el pipeline)."""


_REGISTRO = {}


def registrar(cls):
    """Decorador: registra un plugin por su `clave`."""
    if not cls.clave:
        raise ValueError(f'{cls.__name__} necesita un atributo "clave"')
    _REGISTRO[cls.clave] = cls
    return cls


def registro():
    """Copia del registro {clave: clase} de plugins."""
    return dict(_REGISTRO)
