# -*- coding: utf-8 -*-
"""
PREVÉ — Configuración parametrizable (Fase 3).

Todos los parámetros de negocio y del modelo viven en la base (tablas `configuracion`, `empresas`,
`sucursales`, `ciclos_compra`). Esta clase los carga una vez, los guarda en caché y valida los tipos.

    cfg = Config.actual()
    cfg.get('adi_umbral')                 # 1.32
    cfg.set('adi_umbral', 1.4)            # valida el tipo e invalida la caché
    cfg.empresa['igv_factor']             # 1.18
    cfg.ciclo_principal['lead_time_dias'] # 36
    cfg.sucursales                        # [{'nombre', 'codigo', 'almacenes', 'entrada', 'salida', 'activa'}]

Valores por defecto = los que el código tenía fijos hasta la Fase 2 (los resultados no cambian
mientras no se editen).
"""
import json
import os
import shutil
import datetime
from dataclasses import dataclass, field

from database import Database, slug
from preve_utils import get_logger

logger = get_logger('config')

# clave → (valor por defecto, tipo, descripción)
DEFAULTS = {
    'usuario_nombre': ('Usuario', 'string', 'Nombre que aparece en el saludo de la pantalla de inicio.'),
    'sucursal_activa': ('', 'string', 'Sucursal seleccionada en la barra superior.'),
    'wizard_completado': (False, 'bool', 'True cuando se terminó el asistente de configuración inicial.'),
    'dias_entre_importaciones': (14, 'int', 'Cada cuántos días se importan los reportes del ERP.'),
    'z_nivel_servicio': ({'A': 1.645, 'B': 1.2816, 'C': 1.0364}, 'json',
                         'Factor Z del stock de seguridad por clase ABC (95% / 90% / 85% de servicio).'),
    'adi_umbral': (1.32, 'float', 'Umbral ADI de Syntetos-Boylan (intervalo medio entre demandas).'),
    'cv2_umbral': (0.49, 'float', 'Umbral CV² de Syntetos-Boylan (variabilidad de la demanda).'),
    'semanas_lanzamiento': (13, 'int', 'Semanas desde la primera venta para considerar un producto "nuevo".'),
    'lgbm_params': ({'n_estimators': 200, 'learning_rate': 0.05, 'max_depth': 6, 'min_child_samples': 10,
                     'random_state': 42}, 'json', 'Hiperparámetros de LightGBM.'),
    'catboost_params': ({'iterations': 200, 'learning_rate': 0.05, 'depth': 6, 'random_seed': 42}, 'json',
                        'Hiperparámetros de CatBoost.'),
    'tsb_params': ({'alpha_d': 0.1, 'alpha_p': 0.1}, 'json', 'Suavizados de Croston-TSB.'),
    'ventas_son_salidas_de_almacen': (False, 'bool',
                                      'True si el ERP registra cada venta también como "Salida" de almacén.'),
    'compras_son_entradas_de_almacen': (False, 'bool',
                                        'True si el ERP registra cada compra también como "Entrada" de almacén.'),
    'mapeo_columnas_ventas': ({'fecha': 'FECHA/HORA', 'codigo_barras': 'CÓDIGO DE BARRA', 'producto': 'PRODUCTO',
                               'cantidad': 'CANTIDAD', 'precio': 'PRECIO X UNIDAD', 'sucursal': 'ALMACÉN'}, 'json',
                              'Qué columna del Excel de ventas corresponde a cada campo de PREVÉ.'),
    'plantilla_categorias': ('RETAIL', 'string', 'Plantilla de categorías de acción en uso.'),
    'ultima_duracion_s': (None, 'float', 'Duración del último análisis (para estimar el tiempo restante).'),
    'pasos_s': (None, 'json', 'Duración de cada paso del último análisis.'),
}

CICLOS_DEFECTO = [
    # nombre, duración (días de revisión), lead time, día del reporte final, horizonte
    {'nombre': 'Internacional', 'duracion_dias': 105, 'lead_time_dias': 36, 'dia_reporte_final': 104,
     'horizonte_pronostico_dias': 141},
    {'nombre': 'Local', 'duracion_dias': 30, 'lead_time_dias': 10, 'dia_reporte_final': 29,
     'horizonte_pronostico_dias': 40},
]
EMPRESA_DEFECTO = {'nombre': 'Mi empresa', 'moneda': 'PEN', 'igv_factor': 1.18}

# Instalación existente de Yuju Store (había un preve_config.json): valores para precargar el asistente.
_LEGADO_EMPRESA = {'nombre': 'Yuju Store', 'moneda': 'PEN', 'igv_factor': 1.18}
_LEGADO_ALMACENES = {
    'san_jose': ['Tienda Juliaca', 'Almacén Juliaca'],     # el almacén compartido se suma a San José
    'santa_barbara': ['SANTA BARBARA'],
    'lima': ['YUJU STORE PRINCIPAL'],
}

RUTA_CONFIG_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'preve_config.json')
_CLAVE_JSON_MIGRADO = 'config_json_migrado'


class ConfigError(ValueError):
    """Valor de configuración con tipo o rango inválido."""


def validar_tipo(clave, valor, tipo):
    if valor is None:
        if DEFAULTS.get(clave, (0,))[0] is None:
            return valor
        raise ConfigError(f'"{clave}" no puede quedar vacío')
    ok = {'string': isinstance(valor, str),
          'int': isinstance(valor, int) and not isinstance(valor, bool),
          'float': isinstance(valor, (int, float)) and not isinstance(valor, bool),
          'bool': isinstance(valor, bool),
          'json': isinstance(valor, (dict, list))}[tipo]
    if not ok:
        raise ConfigError(f'"{clave}" debe ser de tipo {tipo} (recibido {type(valor).__name__})')
    return float(valor) if tipo == 'float' else valor


def normalizar_ubicacion(nombre):
    """'Almacén Juliaca' == 'almacen juliaca' == 'ALMACEN JULIACA'."""
    return slug(nombre)


@dataclass
class ParametrosModelo:
    """Parámetros que usa el pipeline. Los valores por defecto replican el comportamiento original."""
    horizonte_dias: float = 141
    lead_time_dias: float = 36
    igv_factor: float = 1.18
    z_nivel_servicio: dict = field(default_factory=lambda: dict(DEFAULTS['z_nivel_servicio'][0]))
    adi_umbral: float = 1.32
    cv2_umbral: float = 0.49
    semanas_lanzamiento: int = 13
    lgbm_params: dict = field(default_factory=lambda: dict(DEFAULTS['lgbm_params'][0]))
    catboost_params: dict = field(default_factory=lambda: dict(DEFAULTS['catboost_params'][0]))
    tsb_params: dict = field(default_factory=lambda: dict(DEFAULTS['tsb_params'][0]))
    almacenes_propios: frozenset = frozenset({'tienda_juliaca', 'almacen_juliaca'})
    ventas_son_salidas_de_almacen: bool = False
    compras_son_entradas_de_almacen: bool = False

    @property
    def horizonte_semanas(self):
        return self.horizonte_dias / 7.0


class Config:
    """Configuración cargada desde la base, con caché en memoria."""

    _actual = None

    def __init__(self, db=None):
        self.db = db or Database.default()
        self._cache = None
        self._asegurar_inicial()

    @classmethod
    def actual(cls):
        """Instancia compartida (se recrea si cambió la base por defecto, p. ej. en tests)."""
        db = Database.default()
        if cls._actual is None or cls._actual.db is not db:
            cls._actual = cls(db)
        return cls._actual

    @classmethod
    def reiniciar(cls):
        cls._actual = None

    # ── carga / caché ──────────────────────────────────────
    def invalidar(self):
        self._cache = None

    def _datos(self):
        if self._cache is None:
            guardado = self.db.listar_config()
            valores = {k: guardado.get(k, d[0]) for k, d in DEFAULTS.items()}
            for k, v in guardado.items():
                valores.setdefault(k, v)                       # claves dinámicas (carpetas:, almacenes:, ...)
            empresa = self.db.obtener_empresa()
            ciclos = self.db.listar_ciclos_compra().to_dict('records')
            suc = self.db.listar_sucursales(solo_activas=False).to_dict('records')
            self._cache = {'valores': valores, 'empresa': empresa, 'ciclos': ciclos, 'sucursales': suc}
        return self._cache

    def _asegurar_inicial(self):
        """Primera vez: importa preve_config.json si existe; si no, crea ciclos por defecto."""
        if not self.db.get_config(_CLAVE_JSON_MIGRADO) and os.path.exists(RUTA_CONFIG_JSON):
            self._importar_json_legado(RUTA_CONFIG_JSON)
        if self.db.listar_ciclos_compra(solo_activos=False).empty:
            for c in CICLOS_DEFECTO:
                self.db.guardar_ciclo_compra(c['nombre'], c['duracion_dias'], c['lead_time_dias'],
                                             c['horizonte_pronostico_dias'], c['dia_reporte_final'])

    def _importar_json_legado(self, ruta):
        """Migra el preve_config.json de versiones anteriores (una sola vez)."""
        try:
            with open(ruta, 'r', encoding='utf-8') as f:
                legado = json.load(f)
            if not isinstance(legado, dict):
                raise ValueError('no es un objeto JSON')
        except FileNotFoundError:
            return
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError):
            destino = f'{ruta}.corrupto-{datetime.datetime.now():%Y%m%d-%H%M%S}'
            try:
                shutil.copy2(ruta, destino)
            except OSError:
                logger.exception('No se pudo respaldar %s', ruta)
            logger.error('preve_config.json ilegible; respaldado en %s. Se usan valores por defecto.', destino)
            self.db.set_config(_CLAVE_JSON_MIGRADO, True)
            return
        self.db.actualizar_empresa(**_LEGADO_EMPRESA)
        mapa = {'usuario': 'usuario_nombre', 'sucursal': 'sucursal_activa'}
        for k, v in legado.items():
            clave = mapa.get(k, k)
            if clave in DEFAULTS and v is not None:
                try:
                    self.db.set_config(clave, validar_tipo(clave, v, DEFAULTS[clave][1]), DEFAULTS[clave][1])
                except ConfigError:
                    logger.warning('Valor inválido en preve_config.json para %s: %r (se ignora)', k, v)
        for nombre, carpetas in (legado.get('sucursales') or {}).items():
            codigo = slug(nombre)
            self.db.obtener_o_crear_sucursal(nombre)
            self.db.set_config(f'carpetas:{codigo}', {'entrada': carpetas.get('entrada', ''),
                                                      'salida': carpetas.get('salida', '')})
            self.db.set_config(f'almacenes:{codigo}', _LEGADO_ALMACENES.get(codigo, [nombre]))
        self.db.set_config(_CLAVE_JSON_MIGRADO, True, descripcion='preve_config.json ya importado')
        logger.info('Configuración importada desde %s', ruta)

    # ── valores simples ────────────────────────────────────
    def get(self, clave, defecto=None):
        v = self._datos()['valores'].get(clave)
        if v is None:
            return DEFAULTS[clave][0] if clave in DEFAULTS and defecto is None else defecto
        return v

    def set(self, clave, valor):
        if clave in DEFAULTS:
            _, tipo, desc = DEFAULTS[clave]
            valor = validar_tipo(clave, valor, tipo)
            self.db.set_config(clave, valor, tipo, desc)
        else:
            self.db.set_config(clave, valor)
        self.invalidar()

    # ── empresa ────────────────────────────────────────────
    @property
    def empresa(self):
        e = self._datos()['empresa'] or {}
        return {'nombre': e.get('nombre', EMPRESA_DEFECTO['nombre']), 'moneda': e.get('moneda', 'PEN'),
                'igv_factor': float(e.get('igv_factor') or 1.18)}

    def set_empresa(self, nombre, moneda, igv_factor):
        if not str(nombre).strip():
            raise ConfigError('El nombre de la empresa no puede quedar vacío')
        igv = float(igv_factor)
        if not 1.0 <= igv < 2.0:
            raise ConfigError('El factor de IGV debe estar entre 1.00 y 1.99 (ej. 1.18 = 18%)')
        self.db.actualizar_empresa(str(nombre).strip(), str(moneda).strip().upper() or 'PEN', igv)
        self.invalidar()

    # ── sucursales ─────────────────────────────────────────
    @property
    def sucursales(self):
        """Todas las sucursales (activas e inactivas) con sus almacenes y carpetas."""
        v = self._datos()['valores']
        res = []
        for s in self._datos()['sucursales']:
            c = s['codigo']
            carp = v.get(f'carpetas:{c}') or {}
            res.append({'nombre': s['nombre'], 'codigo': c, 'activa': bool(s['activa']),
                        'almacenes': list(v.get(f'almacenes:{c}') or [s['nombre']]),
                        'entrada': carp.get('entrada', ''), 'salida': carp.get('salida', '')})
        return res

    @property
    def nombres_sucursales(self):
        return [s['nombre'] for s in self.sucursales if s['activa']]

    def sucursal(self, nombre):
        cod = slug(nombre)
        return next((s for s in self.sucursales if s['codigo'] == cod), None)

    def guardar_sucursales(self, lista):
        """lista: [{'nombre', 'almacenes', 'activa'}]. Las que faltan se desactivan (no se borran: tienen historial)."""
        if not any(s.get('activa', True) for s in lista):
            raise ConfigError('Debe haber al menos una sucursal activa')
        codigos = [slug(s['nombre']) for s in lista]
        if len(set(codigos)) != len(codigos):
            raise ConfigError('Hay sucursales con el mismo nombre')
        for s in lista:
            cod = slug(s['nombre'])
            self.db.obtener_o_crear_sucursal(s['nombre'].strip())
            self.db.actualizar_sucursal(cod, nombre=s['nombre'].strip(), activa=bool(s.get('activa', True)))
            alm = [a.strip() for a in s.get('almacenes', []) if str(a).strip()] or [s['nombre'].strip()]
            self.db.set_config(f'almacenes:{cod}', alm)
        for s in self._datos()['sucursales']:
            if s['codigo'] not in codigos:
                self.db.actualizar_sucursal(s['codigo'], activa=False)
        self.invalidar()

    def carpetas(self, sucursal):
        s = self.sucursal(sucursal)
        return {'entrada': s['entrada'], 'salida': s['salida']} if s else {'entrada': '', 'salida': ''}

    def set_carpeta(self, sucursal, clave, ruta):
        cod = slug(sucursal)
        actual = dict(self.carpetas(sucursal))
        actual[clave] = ruta
        self.db.set_config(f'carpetas:{cod}', actual)
        self.invalidar()

    # ── ciclos de compra ───────────────────────────────────
    @property
    def ciclos(self):
        """Ciclos activos, el de mayor horizonte primero (es el principal)."""
        activos = [c for c in self._datos()['ciclos'] if c['activo']]
        return sorted(activos, key=lambda c: -c['horizonte_pronostico_dias'])

    @property
    def ciclo_principal(self):
        return self.ciclos[0] if self.ciclos else dict(CICLOS_DEFECTO[0], activo=True)

    def guardar_ciclos(self, lista):
        """lista: [{'nombre', 'duracion_dias', 'lead_time_dias', 'dia_reporte_final', 'horizonte_pronostico_dias'}]."""
        if not lista:
            raise ConfigError('Debe haber al menos un ciclo de compra')
        nombres = [c['nombre'].strip() for c in lista]
        if len(set(n.lower() for n in nombres)) != len(nombres) or not all(nombres):
            raise ConfigError('Cada ciclo necesita un nombre distinto')
        for c in lista:
            dur, lt = int(c['duracion_dias']), int(c['lead_time_dias'])
            if dur <= 0 or lt < 0:
                raise ConfigError(f'Ciclo "{c["nombre"]}": duración y lead time deben ser positivos')
            hz = int(c.get('horizonte_pronostico_dias') or dur + lt)
            final = c.get('dia_reporte_final')
            final = dur - 1 if final in (None, '') else int(final)
            if not 1 <= final <= dur:
                raise ConfigError(f'Ciclo "{c["nombre"]}": el día del reporte final debe estar entre 1 y {dur}')
            self.db.guardar_ciclo_compra(c['nombre'].strip(), dur, lt, hz, final, activo=True)
        self.db.desactivar_ciclos_excepto(nombres)
        self.invalidar()

    def guardar_parametros_modelo(self, valores):
        """Guarda los parámetros editados en el panel de Configuración (todo o nada).

        valores: dict con z_A, z_B, z_C, adi_umbral, cv2_umbral, semanas_lanzamiento,
        dias_entre_importaciones, ventas_son_salidas_de_almacen, compras_son_entradas_de_almacen y,
        opcionalmente, lgbm_params / catboost_params / tsb_params como texto JSON.
        """
        def numero(k, tipo=float, minimo=None, maximo=None):
            try:
                v = tipo(str(valores[k]).replace(',', '.').strip())
            except (KeyError, TypeError, ValueError):
                raise ConfigError(f'"{k}" debe ser un número') from None
            if (minimo is not None and v < minimo) or (maximo is not None and v > maximo):
                raise ConfigError(f'"{k}" debe estar entre {minimo} y {maximo}')
            return v

        nuevos = {
            'z_nivel_servicio': {c: numero(f'z_{c}', float, 0, 4) for c in ('A', 'B', 'C')},
            'adi_umbral': numero('adi_umbral', float, 1, 10),
            'cv2_umbral': numero('cv2_umbral', float, 0, 10),
            'semanas_lanzamiento': numero('semanas_lanzamiento', int, 1, 104),
            'dias_entre_importaciones': numero('dias_entre_importaciones', int, 1, 120),
            'ventas_son_salidas_de_almacen': bool(valores.get('ventas_son_salidas_de_almacen')),
            'compras_son_entradas_de_almacen': bool(valores.get('compras_son_entradas_de_almacen')),
        }
        for k in ('lgbm_params', 'catboost_params', 'tsb_params'):
            if k in valores:
                try:
                    d = json.loads(valores[k]) if isinstance(valores[k], str) else valores[k]
                except json.JSONDecodeError as e:
                    raise ConfigError(f'"{k}" no es un JSON válido: {e}') from None
                if not isinstance(d, dict):
                    raise ConfigError(f'"{k}" debe ser un objeto JSON, ej. {{"learning_rate": 0.05}}')
                nuevos[k] = d
        for k, v in nuevos.items():          # todo validado: recién ahora se escribe
            self.set(k, v)

    # ── parámetros del pipeline ────────────────────────────
    def parametros_modelo(self, sucursal=None, ciclo=None):
        ciclo = ciclo or self.ciclo_principal
        suc = self.sucursal(sucursal) if sucursal else None
        almacenes = frozenset(normalizar_ubicacion(a) for a in (suc['almacenes'] if suc else []))
        return ParametrosModelo(
            horizonte_dias=float(ciclo['horizonte_pronostico_dias']),
            lead_time_dias=float(ciclo['lead_time_dias']),
            igv_factor=self.empresa['igv_factor'],
            z_nivel_servicio=dict(self.get('z_nivel_servicio')),
            adi_umbral=float(self.get('adi_umbral')), cv2_umbral=float(self.get('cv2_umbral')),
            semanas_lanzamiento=int(self.get('semanas_lanzamiento')),
            lgbm_params=dict(self.get('lgbm_params')), catboost_params=dict(self.get('catboost_params')),
            tsb_params=dict(self.get('tsb_params')),
            almacenes_propios=almacenes or ParametrosModelo().almacenes_propios,
            ventas_son_salidas_de_almacen=bool(self.get('ventas_son_salidas_de_almacen')),
            compras_son_entradas_de_almacen=bool(self.get('compras_son_entradas_de_almacen')),
        )
