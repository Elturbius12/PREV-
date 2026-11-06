# -*- coding: utf-8 -*-
"""Ajustes de Negosy guardados en la configuración (Fase 10).

La URL de login, la URL base y el texto de la tarjeta de cada tienda y las credenciales viven en la tabla
`configuracion` (vía preve_config.Config). Los valores de fábrica (TIENDAS_DEFECTO / LOGIN_URL_DEFECTO del
extractor) solo se usan cuando el usuario no guardó nada.

Cada tienda queda asociada a una SUCURSAL de PREVÉ (clave `negosy_tienda:<codigo>` con
{'base_url', 'selector_texto'}). Las tres tiendas de Yuju Store se asocian por el nombre de la sucursal;
una sucursal nueva usa su propio nombre como texto de la tarjeta y exige que el usuario escriba su URL.

SEGURIDAD: usuario y contraseña se guardan SIN CIFRAR en la base SQLite local (preve.db). PREVÉ es una app
de escritorio de un solo usuario: es el mismo nivel de confianza que el archivo .env que ya se usaba.
"""
from plugins.negosy.extractor import TIENDAS_DEFECTO, LOGIN_URL_DEFECTO
from preve_config import Config, ConfigError

#: código de sucursal → clave de TIENDAS_DEFECTO (la tienda "principal" es la sucursal Lima)
TIENDA_DE_SUCURSAL = {'san_jose': 'san_jose', 'santa_barbara': 'santa_barbara', 'lima': 'principal'}


def _clave_tienda(codigo):
    """Clave de la tienda de una sucursal: la de fábrica si la conocemos; si no, el código de la sucursal."""
    return TIENDA_DE_SUCURSAL.get(codigo, codigo)


def login_url(cfg=None):
    """URL de login configurada (o la de fábrica)."""
    return ((cfg or Config.actual()).get('negosy_login_url') or LOGIN_URL_DEFECTO).strip()


def tiendas_configuradas(cfg=None):
    """{clave_tienda: {nombre, base_url, selector_texto, sucursal, codigo}} de las sucursales activas.

    base_url queda vacía en una sucursal nueva hasta que el usuario la escribe en Configuración.
    """
    cfg = cfg or Config.actual()
    res = {}
    for s in cfg.sucursales:
        if not s['activa']:
            continue
        clave = _clave_tienda(s['codigo'])
        fabrica = TIENDAS_DEFECTO.get(clave) if s['codigo'] in TIENDA_DE_SUCURSAL else None
        guardado = cfg.get(f'negosy_tienda:{s["codigo"]}') or {}
        res[clave] = {
            'nombre': (fabrica or {}).get('nombre') or s['nombre'],
            'base_url': (guardado.get('base_url') or (fabrica or {}).get('base_url') or '').strip().rstrip('/'),
            'selector_texto': (guardado.get('selector_texto') or (fabrica or {}).get('selector_texto')
                               or s['nombre']).strip(),
            'sucursal': s['nombre'],
            'codigo': s['codigo'],
        }
    return res


def tienda_de_sucursal(nombre_sucursal, cfg=None):
    """Clave de la tienda de Negosy que corresponde a una sucursal (None si no hay)."""
    cfg = cfg or Config.actual()
    s = cfg.sucursal(nombre_sucursal) if nombre_sucursal else None
    return _clave_tienda(s['codigo']) if s else None


def _es_https(url):
    return str(url).strip().lower().startswith('https://')


def guardar_ajustes(cfg, login, usuario, contrasena, tiendas, actualizar_catalogo):
    """Valida y guarda los ajustes de Negosy (todo o nada).

    tiendas: {codigo_sucursal: {'base_url': ..., 'selector_texto': ...}}
    Lanza ConfigError si algo no es válido.
    """
    login, usuario, contrasena = str(login or '').strip(), str(usuario or '').strip(), str(contrasena or '')
    if not _es_https(login):
        raise ConfigError('La URL de login debe empezar con https://')
    if bool(usuario) != bool(contrasena):
        raise ConfigError('Escribe el usuario y la contraseña juntos (o deja ambos vacíos para usar el archivo .env).')
    nuevos = {}
    for codigo, t in (tiendas or {}).items():
        base = str(t.get('base_url', '')).strip().rstrip('/')
        if base and not _es_https(base):
            raise ConfigError(f'La URL base de "{codigo}" debe empezar con https://')
        nuevos[codigo] = {'base_url': base, 'selector_texto': str(t.get('selector_texto', '')).strip()}
    cfg.set('negosy_login_url', login)
    cfg.set('negosy_usuario', usuario)
    cfg.set('negosy_contrasena', contrasena)
    cfg.set('negosy_actualizar_catalogo', bool(actualizar_catalogo))
    for codigo, valor in nuevos.items():
        cfg.set(f'negosy_tienda:{codigo}', valor)
