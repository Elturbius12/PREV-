# -*- coding: utf-8 -*-
"""
PREVÉ — Capa de datos (SQLAlchemy Core).

Todo el SQL del proyecto vive aquí: el resto del código llama a métodos de `Database` y nunca escribe
consultas. Migrar a PostgreSQL = cambiar la URL de conexión (variable de entorno PREVE_DB_URL).

    db = Database.default()                  # sqlite:///<carpeta de la app>/preve.db
    db = Database('postgresql+psycopg://user:pass@host/preve')

El esquema se versiona en la tabla `schema_version`; `MIGRACIONES` lista los pasos en orden y
`Database.migrar()` aplica los pendientes (se llama sola al abrir la base).
"""
import datetime
import json
import os
import re
import unicodedata

import pandas as pd
from sqlalchemy import (Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, MetaData, String,
                        Table, Text, UniqueConstraint, create_engine, delete, event, func, insert, select,
                        update)

from preve_utils import get_logger

logger = get_logger('database')

DB_NOMBRE = 'preve.db'
ENV_URL = 'PREVE_DB_URL'
EMPRESA_DEFECTO = 'Empresa principal'
_LOTE = 500

metadata = MetaData()


def _ahora():
    return datetime.datetime.now()


# ══════════════════════════════════════════════════════════════════
# Esquema
# ══════════════════════════════════════════════════════════════════
schema_version = Table(
    'schema_version', metadata,
    Column('version', Integer, primary_key=True),
    Column('descripcion', String(200)),
    Column('aplicada_en', DateTime, default=_ahora),
)

empresas = Table(
    'empresas', metadata,
    Column('id', Integer, primary_key=True),
    Column('nombre', String(200), nullable=False, unique=True),
    Column('moneda', String(10), nullable=False, default='PEN'),
    Column('igv_factor', Float, nullable=False, default=1.18),
    Column('creada_en', DateTime, default=_ahora),
)

sucursales = Table(
    'sucursales', metadata,
    Column('id', Integer, primary_key=True),
    Column('empresa_id', Integer, ForeignKey('empresas.id'), nullable=False),
    Column('codigo', String(60), nullable=False),
    Column('nombre', String(200), nullable=False),
    Column('activa', Boolean, nullable=False, default=True),
    UniqueConstraint('empresa_id', 'codigo'),
)

productos = Table(
    'productos', metadata,
    Column('id', Integer, primary_key=True),
    Column('empresa_id', Integer, ForeignKey('empresas.id'), nullable=False),
    Column('codigo_barras', String(80)),
    Column('sku', String(100), nullable=False),
    Column('nombre', String(400)),
    Column('categoria', String(200)),
    Column('clasificacion_adi_cv2', String(80)),
    Column('modelo_asignado', String(40)),
    Column('precio_regular', Float),
    Column('fecha_primera_venta', Date),
    UniqueConstraint('empresa_id', 'sku'),
    Index('ix_productos_codigo_barras', 'empresa_id', 'codigo_barras'),
)

proveedores = Table(
    'proveedores', metadata,
    Column('id', Integer, primary_key=True),
    Column('empresa_id', Integer, ForeignKey('empresas.id'), nullable=False),
    Column('nombre', String(300), nullable=False),
    Column('tipo', String(40)),                    # internacional / local / ...
    Column('lead_time_dias', Integer),
    Column('condiciones_pago', String(200)),
    UniqueConstraint('empresa_id', 'nombre'),
)

ciclos_compra = Table(
    'ciclos_compra', metadata,
    Column('id', Integer, primary_key=True),
    Column('empresa_id', Integer, ForeignKey('empresas.id'), nullable=False),
    Column('nombre', String(100), nullable=False),
    Column('duracion_dias', Integer, nullable=False),
    Column('lead_time_dias', Integer, nullable=False),
    Column('dia_reporte_final', Integer),
    Column('horizonte_pronostico_dias', Integer, nullable=False),
    Column('activo', Boolean, nullable=False, default=True),
    UniqueConstraint('empresa_id', 'nombre'),
)

ventas_diarias = Table(
    'ventas_diarias', metadata,
    Column('id', Integer, primary_key=True),
    Column('producto_id', Integer, ForeignKey('productos.id'), nullable=False),
    Column('sucursal_id', Integer, ForeignKey('sucursales.id'), nullable=False),
    Column('fecha', Date, nullable=False),
    Column('cantidad', Float, nullable=False),
    Column('precio_unitario', Float),
    UniqueConstraint('producto_id', 'sucursal_id', 'fecha'),
    Index('ix_ventas_sucursal_fecha', 'sucursal_id', 'fecha'),
)

stock_actual = Table(
    'stock_actual', metadata,
    Column('producto_id', Integer, ForeignKey('productos.id'), primary_key=True),
    Column('sucursal_id', Integer, ForeignKey('sucursales.id'), primary_key=True),
    Column('cantidad', Float, nullable=False),
    Column('fecha_actualizacion', Date, nullable=False),
)

ordenes_compra_abiertas = Table(
    'ordenes_compra_abiertas', metadata,
    Column('id', Integer, primary_key=True),
    Column('producto_id', Integer, ForeignKey('productos.id'), nullable=False),
    Column('proveedor_id', Integer, ForeignKey('proveedores.id')),
    Column('sucursal_id', Integer, ForeignKey('sucursales.id')),
    Column('cantidad', Float, nullable=False),
    Column('fecha_pedido', Date),
    Column('fecha_estimada_llegada', Date),
    Column('estado', String(20), nullable=False, default='abierta'),
)

pronosticos = Table(
    'pronosticos', metadata,
    Column('id', Integer, primary_key=True),
    Column('producto_id', Integer, ForeignKey('productos.id'), nullable=False),
    Column('sucursal_id', Integer, ForeignKey('sucursales.id'), nullable=False),
    Column('modelo', String(40)),
    Column('fecha_ejecucion', DateTime, nullable=False),
    Column('periodo_inicio', Date, nullable=False),
    Column('periodo_fin', Date, nullable=False),
    Column('valor_p10', Float),
    Column('valor_p50', Float),
    Column('valor_p90', Float),
    Column('venta_real', Float),
    Index('ix_pronosticos_producto_periodo', 'producto_id', 'sucursal_id', 'periodo_inicio'),
)

metricas_periodo = Table(
    'metricas_periodo', metadata,
    Column('id', Integer, primary_key=True),
    Column('producto_id', Integer, ForeignKey('productos.id')),
    Column('modelo', String(40)),
    Column('periodo_inicio', Date, nullable=False),
    Column('periodo_fin', Date, nullable=False),
    Column('mape', Float),
    Column('mae', Float),
    Column('rmse', Float),
    Column('bias', Float),
    Column('muestras', Integer),
)

ejecuciones_backtesting = Table(
    'ejecuciones_backtesting', metadata,
    Column('id', Integer, primary_key=True),
    Column('empresa_id', Integer, ForeignKey('empresas.id'), nullable=False),
    Column('fecha_inicio', Date, nullable=False),
    Column('fecha_fin', Date, nullable=False),
    Column('total_productos', Integer),
    Column('total_periodos', Integer),
    Column('mape_promedio', Float),
    Column('ruta_excel', Text),
    Column('configuracion_json', Text),
)

decisiones_compra = Table(
    'decisiones_compra', metadata,
    Column('id', Integer, primary_key=True),
    Column('producto_id', Integer, ForeignKey('productos.id'), nullable=False),
    Column('sucursal_id', Integer, ForeignKey('sucursales.id'), nullable=False),
    Column('fecha', DateTime, nullable=False),
    Column('accion', String(40)),
    Column('cantidad_sugerida', Float),
    Column('cantidad_aprobada', Float),
    Column('estado', String(20), nullable=False),     # comprado | pospuesto | descartado
    Column('usuario', String(100)),
    # columnas que ya usaba la app (decisiones por ciclo, con razón y posposición)
    Column('ciclo_numero', Integer, nullable=False, default=0),
    Column('razon', Text),
    Column('hasta', Date),
    UniqueConstraint('producto_id', 'sucursal_id', 'ciclo_numero'),
)

configuracion = Table(
    'configuracion', metadata,
    Column('clave', String(150), primary_key=True),
    Column('valor', Text),
    Column('descripcion', Text),
    Column('tipo', String(10), nullable=False, default='string'),   # string / int / float / bool / json
)

plantillas_categorias = Table(
    'plantillas_categorias', metadata,
    Column('id', Integer, primary_key=True),
    Column('nombre', String(100), nullable=False, unique=True),
    Column('descripcion', Text),
    Column('reglas_json', Text, nullable=False),
    Column('es_plantilla_sistema', Boolean, nullable=False, default=False),
)

categorias_activas = Table(
    'categorias_activas', metadata,
    Column('id', Integer, primary_key=True),
    Column('empresa_id', Integer, ForeignKey('empresas.id'), nullable=False),
    Column('nombre', String(60), nullable=False),
    Column('condicion_python', Text, nullable=False),
    Column('color', String(10)),
    Column('orden', Integer, nullable=False, default=0),
    Column('accion_sugerida', Text),
    UniqueConstraint('empresa_id', 'nombre'),
)

# ── Historial de ejecuciones del pipeline (antes preve_historial.db en cada carpeta) ──
ejecuciones = Table(
    'ejecuciones', metadata,
    Column('id', Integer, primary_key=True),
    Column('sucursal_id', Integer, ForeignKey('sucursales.id'), nullable=False),
    Column('fecha', DateTime, nullable=False),
    Column('etiqueta', String(20)),
    Column('ciclo_numero', Integer),
    Column('dia_ciclo', Integer),
    Column('importacion_num', Integer),
    Column('skus_evaluados', Integer),
    Column('skus_recomprar', Integer),
    Column('inversion_total', Float),
    Column('wmape', Float),
    Column('calificacion', String(20)),
    Column('metadata_json', Text),
    Column('origen', String(300)),      # ruta del historial antiguo si vino de una migración
)

recomendaciones = Table(
    'recomendaciones', metadata,
    Column('id', Integer, primary_key=True),
    Column('ejecucion_id', Integer, ForeignKey('ejecuciones.id', ondelete='CASCADE'), nullable=False),
    Column('producto_id', Integer, ForeignKey('productos.id')),
    Column('sku', String(100), nullable=False),
    Column('nombre', String(400)),
    Column('categoria', String(200)),
    Column('clase_abc', String(2)),
    Column('accion', String(40)),
    Column('stock_actual', Float),
    Column('stock_en_transito', Float),
    Column('demanda_p50', Float),
    Column('rop', Float),
    Column('nivel_objetivo', Float),
    Column('compra_sugerida', Float),
    Column('costo_total', Float),
    Column('proveedor_principal', String(300)),
    Column('margen_pct', Float),
    Column('tendencia_venta', Float),
    Index('ix_recomendaciones_ejecucion', 'ejecucion_id'),
    Index('ix_recomendaciones_sku', 'sku'),
)

metricas_ejecucion = Table(
    'metricas_ejecucion', metadata,
    Column('id', Integer, primary_key=True),
    Column('ejecucion_id', Integer, ForeignKey('ejecuciones.id', ondelete='CASCADE'), nullable=False),
    Column('wmape', Float),
    Column('medape', Float),
    Column('sesgo_medio_pct', Float),
    Column('cobertura_pct', Float),
    Column('calificacion', String(20)),
    Column('quiebres_sin_preve', Integer),
    Column('quiebres_con_preve', Integer),
    Column('quiebres_evitados', Integer),
    Column('roi_pct', Float),
    Column('beneficio_total', Float),
    Column('detalle_json', Text),
)


perfiles_importacion = Table(
    'perfiles_importacion', metadata,
    Column('id', Integer, primary_key=True),
    Column('nombre', String(100), nullable=False, unique=True),
    Column('tipo', String(30), nullable=False, default='ventas'),
    Column('mapeo_columnas', Text, nullable=False),          # JSON {campo: columna del archivo}
    Column('formato_fecha', String(40)),                     # p. ej. '%d-%m-%Y %H:%M' o 'auto'
    Column('separador', String(5)),                          # CSV/TSV; None = autodetectar
    Column('creado_en', DateTime, default=_ahora),
)

_TABLAS_001 = [t for n, t in metadata.tables.items() if n != 'perfiles_importacion']


def _migracion_001(conn):
    """Esquema inicial completo."""
    metadata.create_all(conn, tables=_TABLAS_001)


def _migracion_002(conn):
    """Fase 4: perfiles de importación manual."""
    metadata.create_all(conn, tables=[perfiles_importacion])


# (versión, descripción, función). Agregar migraciones nuevas al final, nunca editar las anteriores.
MIGRACIONES = [
    (1, 'Esquema inicial', _migracion_001),
    (2, 'Perfiles de importación', _migracion_002),
]


# ══════════════════════════════════════════════════════════════════
# Utilidades
# ══════════════════════════════════════════════════════════════════
def slug(texto):
    """'San José' → 'san_jose' (código estable para sucursales)."""
    t = unicodedata.normalize('NFKD', str(texto)).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '_', t.lower()).strip('_') or 'sin_nombre'


def _a_fecha(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    ts = pd.to_datetime(v, errors='coerce')
    return None if pd.isna(ts) else ts.date()


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _entero(v, defecto=None):
    f = _num(v)
    return defecto if f is None else int(f)


def _texto(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)


def _lotes(filas, n=_LOTE):
    for i in range(0, len(filas), n):
        yield filas[i:i + n]


def _url_defecto():
    url = os.environ.get(ENV_URL)
    if url:
        return url
    carpeta = os.path.dirname(os.path.abspath(__file__))
    return 'sqlite:///' + os.path.join(carpeta, DB_NOMBRE).replace('\\', '/')


# ══════════════════════════════════════════════════════════════════
# Clase Database
# ══════════════════════════════════════════════════════════════════
class Database:
    """Punto único de acceso a la base de datos de PREVÉ."""

    _instancias = {}

    def __init__(self, url=None):
        self.url = url or _url_defecto()
        self.engine = create_engine(self.url, future=True)
        if self.engine.dialect.name == 'sqlite':
            event.listen(self.engine, 'connect', _pragmas_sqlite)
        self.migrar()

    @classmethod
    def default(cls):
        """Instancia compartida para la URL por defecto (respeta PREVE_DB_URL)."""
        url = _url_defecto()
        if url not in cls._instancias:
            cls._instancias[url] = cls(url)
        return cls._instancias[url]

    @classmethod
    def cerrar_todas(cls):
        """Libera las conexiones de todas las instancias compartidas (tests y cierre de la app)."""
        for db in cls._instancias.values():
            db.engine.dispose()
        cls._instancias.clear()

    # ── migraciones ────────────────────────────────────────
    def version_esquema(self):
        """Versión de esquema aplicada (0 si la base está vacía)."""
        with self.engine.connect() as conn:
            if not self.engine.dialect.has_table(conn, 'schema_version'):
                return 0
            return conn.execute(select(func.max(schema_version.c.version))).scalar() or 0

    def migrar(self):
        """Aplica las migraciones pendientes, cada una en su propia transacción."""
        actual = self.version_esquema()
        for version, desc, fn in MIGRACIONES:
            if version <= actual:
                continue
            with self.engine.begin() as conn:
                fn(conn)
                conn.execute(insert(schema_version).values(version=version, descripcion=desc,
                                                           aplicada_en=_ahora()))
            logger.info('Migración %s aplicada: %s', version, desc)

    # ── upsert genérico (SQLite y PostgreSQL) ──────────────
    def _insert_dialecto(self, tabla):
        nombre = self.engine.dialect.name
        if nombre == 'sqlite':
            from sqlalchemy.dialects.sqlite import insert as ins
        elif nombre == 'postgresql':
            from sqlalchemy.dialects.postgresql import insert as ins
        else:
            raise NotImplementedError(f'Upsert no soportado para {nombre}')
        return ins(tabla)

    def _upsert(self, conn, tabla, filas, claves, actualizar):
        if not filas:
            return
        stmt = self._insert_dialecto(tabla)
        if actualizar:
            stmt = stmt.on_conflict_do_update(index_elements=claves,
                                              set_={c: stmt.excluded[c] for c in actualizar})
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=claves)
        for lote in _lotes(filas):
            conn.execute(stmt, lote)

    # ── configuración clave/valor ──────────────────────────
    def set_config(self, clave, valor, tipo=None, descripcion=None):
        """Guarda clave/valor en `configuracion`; el tipo se infiere del valor si no se indica."""
        tipo = tipo or ('bool' if isinstance(valor, bool) else 'int' if isinstance(valor, int)
                        else 'float' if isinstance(valor, float) else 'json' if isinstance(valor, (dict, list))
                        else 'string')
        texto = json.dumps(valor, ensure_ascii=False) if tipo == 'json' else (
            ('true' if valor else 'false') if tipo == 'bool' else None if valor is None else str(valor))
        fila = {'clave': clave, 'valor': texto, 'tipo': tipo}
        if descripcion is not None:
            fila['descripcion'] = descripcion
        with self.engine.begin() as conn:
            self._upsert(conn, configuracion, [fila], ['clave'], [k for k in fila if k != 'clave'])

    def get_config(self, clave, defecto=None):
        """Valor de la clave ya convertido a su tipo, o `defecto` si no existe."""
        with self.engine.connect() as conn:
            f = conn.execute(select(configuracion.c.valor, configuracion.c.tipo)
                             .where(configuracion.c.clave == clave)).first()
        if f is None or f.valor is None:
            return defecto
        return _convertir_config(f.valor, f.tipo)

    def listar_config(self, prefijo=''):
        """{clave: valor} de todas las claves que empiezan con `prefijo`."""
        with self.engine.connect() as conn:
            filas = conn.execute(select(configuracion).where(configuracion.c.clave.like(f'{prefijo}%'))
                                 .order_by(configuracion.c.clave)).all()
        return {f.clave: _convertir_config(f.valor, f.tipo) if f.valor is not None else None for f in filas}

    # ── empresas / sucursales ──────────────────────────────
    def obtener_o_crear_empresa(self, nombre=EMPRESA_DEFECTO, moneda='PEN', igv_factor=1.18):
        """id de la empresa con ese nombre; la crea si no existe."""
        with self.engine.begin() as conn:
            fid = conn.execute(select(empresas.c.id).where(empresas.c.nombre == nombre)).scalar()
            if fid is None:
                fid = conn.execute(insert(empresas).values(nombre=nombre, moneda=moneda, igv_factor=igv_factor,
                                                           creada_en=_ahora())).inserted_primary_key[0]
        return fid

    def obtener_empresa(self, empresa_id=None):
        """Fila de la empresa como dict (la por defecto si no se indica id)."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        with self.engine.connect() as conn:
            f = conn.execute(select(empresas).where(empresas.c.id == empresa_id)).first()
        return dict(f._mapping) if f else None

    def actualizar_empresa(self, nombre=None, moneda=None, igv_factor=None, empresa_id=None):
        """Cambia nombre, moneda y/o IGV de la empresa (solo los valores no None)."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        valores = {k: v for k, v in (('nombre', nombre), ('moneda', moneda), ('igv_factor', igv_factor))
                   if v is not None}
        if valores:
            with self.engine.begin() as conn:
                conn.execute(update(empresas).where(empresas.c.id == empresa_id).values(**valores))

    def actualizar_sucursal(self, codigo, nombre=None, activa=None, empresa_id=None):
        """Cambia nombre y/o estado activo de una sucursal identificada por su código."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        valores = {k: v for k, v in (('nombre', nombre), ('activa', activa)) if v is not None}
        if valores:
            with self.engine.begin() as conn:
                conn.execute(update(sucursales).where(sucursales.c.empresa_id == empresa_id,
                                                      sucursales.c.codigo == codigo).values(**valores))

    def desactivar_ciclos_excepto(self, nombres, empresa_id=None):
        """Desactiva (no borra) los ciclos que ya no están en la lista."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        with self.engine.begin() as conn:
            conn.execute(update(ciclos_compra).where(ciclos_compra.c.empresa_id == empresa_id,
                                                     ciclos_compra.c.nombre.not_in(list(nombres)))
                         .values(activo=False))

    def empresa_por_defecto(self):
        """Primera empresa registrada (la crea si no hay ninguna)."""
        with self.engine.connect() as conn:
            fid = conn.execute(select(empresas.c.id).order_by(empresas.c.id).limit(1)).scalar()
        return fid if fid is not None else self.obtener_o_crear_empresa()

    def obtener_o_crear_sucursal(self, nombre, empresa_id=None, codigo=None):
        """id de la sucursal (código = slug del nombre); la crea si no existe."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        codigo = codigo or slug(nombre)
        with self.engine.begin() as conn:
            sid = conn.execute(select(sucursales.c.id).where(sucursales.c.empresa_id == empresa_id,
                                                             sucursales.c.codigo == codigo)).scalar()
            if sid is None:
                sid = conn.execute(insert(sucursales).values(empresa_id=empresa_id, codigo=codigo, nombre=nombre,
                                                             activa=True)).inserted_primary_key[0]
        return sid

    def listar_sucursales(self, empresa_id=None, solo_activas=True):
        """DataFrame de sucursales de la empresa (solo activas por defecto)."""
        q = select(sucursales)
        if empresa_id:
            q = q.where(sucursales.c.empresa_id == empresa_id)
        if solo_activas:
            q = q.where(sucursales.c.activa.is_(True))
        return self._df(q.order_by(sucursales.c.id))

    # ── productos / proveedores ────────────────────────────
    def guardar_productos(self, df, empresa_id=None):
        """Upsert de productos. df con columna 'sku' y opcionales codigo_barras, nombre, categoria,
        clasificacion_adi_cv2 (o segmento), modelo_asignado, precio_regular (o precio_regular_pen),
        fecha_primera_venta. Devuelve {sku: producto_id}."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        alias = {'precio_regular_pen': 'precio_regular', 'segmento': 'clasificacion_adi_cv2'}
        d = df.rename(columns={k: v for k, v in alias.items() if v not in df.columns})
        cols = [c for c in ('codigo_barras', 'nombre', 'categoria', 'clasificacion_adi_cv2', 'modelo_asignado',
                            'precio_regular', 'fecha_primera_venta') if c in d.columns]
        filas = []
        for r in d.drop_duplicates('sku').to_dict('records'):
            fila = {'empresa_id': empresa_id, 'sku': str(r['sku'])}
            for c in cols:
                v = r[c]
                fila[c] = (_num(v) if c == 'precio_regular' else _a_fecha(v) if c == 'fecha_primera_venta'
                           else _texto(v))
            filas.append(fila)
        with self.engine.begin() as conn:
            self._upsert(conn, productos, filas, ['empresa_id', 'sku'], cols)
        return self.mapa_productos(empresa_id)

    def mapa_productos(self, empresa_id=None):
        """{sku: producto_id} de la empresa."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        with self.engine.connect() as conn:
            filas = conn.execute(select(productos.c.sku, productos.c.id)
                                 .where(productos.c.empresa_id == empresa_id)).all()
        return {f.sku: f.id for f in filas}

    def _id_producto(self, sku, empresa_id=None):
        """id del producto; lo crea con solo el SKU si todavía no existe."""
        mapa = self.mapa_productos(empresa_id)
        if str(sku) in mapa:
            return mapa[str(sku)]
        return self.guardar_productos(pd.DataFrame({'sku': [str(sku)]}), empresa_id)[str(sku)]

    def obtener_productos(self, empresa_id=None):
        """DataFrame con el catálogo de productos de la empresa."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        return self._df(select(productos).where(productos.c.empresa_id == empresa_id).order_by(productos.c.sku))

    def obtener_o_crear_proveedor(self, nombre, empresa_id=None, tipo=None, lead_time_dias=None):
        """id del proveedor con ese nombre; lo crea si no existe."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        with self.engine.begin() as conn:
            pid = conn.execute(select(proveedores.c.id).where(proveedores.c.empresa_id == empresa_id,
                                                              proveedores.c.nombre == nombre)).scalar()
            if pid is None:
                pid = conn.execute(insert(proveedores).values(
                    empresa_id=empresa_id, nombre=nombre, tipo=tipo,
                    lead_time_dias=lead_time_dias)).inserted_primary_key[0]
        return pid

    def listar_proveedores(self, empresa_id=None):
        """DataFrame de proveedores de la empresa."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        return self._df(select(proveedores).where(proveedores.c.empresa_id == empresa_id)
                        .order_by(proveedores.c.nombre))

    # ── ciclos de compra ───────────────────────────────────
    def guardar_ciclo_compra(self, nombre, duracion_dias, lead_time_dias, horizonte_pronostico_dias=None,
                             dia_reporte_final=None, activo=True, empresa_id=None):
        """Crea o actualiza un ciclo de compra por nombre (horizonte = duración + lead time si falta)."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        fila = {'empresa_id': empresa_id, 'nombre': nombre, 'duracion_dias': int(duracion_dias),
                'lead_time_dias': int(lead_time_dias),
                'horizonte_pronostico_dias': int(horizonte_pronostico_dias or duracion_dias + lead_time_dias),
                'dia_reporte_final': dia_reporte_final if dia_reporte_final is None else int(dia_reporte_final),
                'activo': bool(activo)}
        with self.engine.begin() as conn:
            self._upsert(conn, ciclos_compra, [fila], ['empresa_id', 'nombre'],
                         [k for k in fila if k not in ('empresa_id', 'nombre')])

    def listar_ciclos_compra(self, empresa_id=None, solo_activos=True):
        """DataFrame de ciclos de compra (solo activos por defecto)."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        q = select(ciclos_compra).where(ciclos_compra.c.empresa_id == empresa_id)
        if solo_activos:
            q = q.where(ciclos_compra.c.activo.is_(True))
        return self._df(q.order_by(ciclos_compra.c.id))

    # ── ventas diarias ─────────────────────────────────────
    def guardar_ventas(self, sucursal, df, empresa_id=None):
        """Upsert de ventas diarias. df: sku, fecha, cantidad [, precio_unitario]. Devuelve filas escritas."""
        if df is None or df.empty:
            return 0
        sid = self.obtener_o_crear_sucursal(sucursal, empresa_id)
        mapa = self.mapa_productos(empresa_id)
        faltan = sorted(set(df['sku'].astype(str)) - set(mapa))
        if faltan:
            mapa = self.guardar_productos(pd.DataFrame({'sku': faltan}), empresa_id)
        d = df.assign(sku=df['sku'].astype(str), fecha=pd.to_datetime(df['fecha']).dt.date).groupby(
            ['sku', 'fecha'], as_index=False).agg(
            cantidad=('cantidad', 'sum'),
            **({'precio_unitario': ('precio_unitario', 'mean')} if 'precio_unitario' in df.columns else {}))
        filas = [{'producto_id': mapa[r['sku']], 'sucursal_id': sid, 'fecha': r['fecha'],
                  'cantidad': float(r['cantidad']), 'precio_unitario': _num(r.get('precio_unitario'))}
                 for r in d.to_dict('records')]
        with self.engine.begin() as conn:
            self._upsert(conn, ventas_diarias, filas, ['producto_id', 'sucursal_id', 'fecha'],
                         ['cantidad', 'precio_unitario'])
        return len(filas)

    def obtener_ventas(self, sucursal=None, fecha_desde=None, fecha_hasta=None, skus=None):
        """DataFrame sku / fecha / cantidad / precio_unitario / sucursal."""
        q = (select(productos.c.sku, ventas_diarias.c.fecha, ventas_diarias.c.cantidad,
                    ventas_diarias.c.precio_unitario, sucursales.c.nombre.label('sucursal'))
             .join(productos, productos.c.id == ventas_diarias.c.producto_id)
             .join(sucursales, sucursales.c.id == ventas_diarias.c.sucursal_id))
        if sucursal:
            q = q.where(sucursales.c.codigo == slug(sucursal))
        if fecha_desde:
            q = q.where(ventas_diarias.c.fecha >= _a_fecha(fecha_desde))
        if fecha_hasta:
            q = q.where(ventas_diarias.c.fecha <= _a_fecha(fecha_hasta))
        if skus is not None:
            q = q.where(productos.c.sku.in_([str(s) for s in skus]))
        df = self._df(q.order_by(productos.c.sku, ventas_diarias.c.fecha))
        if not df.empty:
            df['fecha'] = pd.to_datetime(df['fecha'])
        return df

    # ── stock actual ───────────────────────────────────────
    def guardar_stock_actual(self, sucursal, stock_por_sku, fecha, empresa_id=None):
        """stock_por_sku: dict o Series sku → cantidad."""
        sid = self.obtener_o_crear_sucursal(sucursal, empresa_id)
        serie = pd.Series(stock_por_sku, dtype=float).dropna()
        mapa = self.mapa_productos(empresa_id)
        faltan = sorted(set(map(str, serie.index)) - set(mapa))
        if faltan:
            mapa = self.guardar_productos(pd.DataFrame({'sku': faltan}), empresa_id)
        f = _a_fecha(fecha)
        filas = [{'producto_id': mapa[str(s)], 'sucursal_id': sid, 'cantidad': float(q), 'fecha_actualizacion': f}
                 for s, q in serie.items()]
        with self.engine.begin() as conn:
            self._upsert(conn, stock_actual, filas, ['producto_id', 'sucursal_id'],
                         ['cantidad', 'fecha_actualizacion'])
        return len(filas)

    def obtener_stock_actual(self, sucursal):
        """DataFrame sku / cantidad / fecha_actualizacion de la sucursal."""
        q = (select(productos.c.sku, stock_actual.c.cantidad, stock_actual.c.fecha_actualizacion)
             .join(productos, productos.c.id == stock_actual.c.producto_id)
             .join(sucursales, sucursales.c.id == stock_actual.c.sucursal_id)
             .where(sucursales.c.codigo == slug(sucursal)))
        return self._df(q)

    # ── órdenes de compra abiertas ─────────────────────────
    def guardar_orden_compra(self, sku, cantidad, proveedor=None, fecha_pedido=None, fecha_estimada_llegada=None,
                             estado='abierta', sucursal=None, empresa_id=None):
        """Registra una orden de compra (abierta por defecto). Devuelve su id."""
        pid = self._id_producto(sku, empresa_id)
        prov_id = self.obtener_o_crear_proveedor(proveedor, empresa_id) if proveedor else None
        sid = self.obtener_o_crear_sucursal(sucursal, empresa_id) if sucursal else None
        with self.engine.begin() as conn:
            return conn.execute(insert(ordenes_compra_abiertas).values(
                producto_id=pid, proveedor_id=prov_id, sucursal_id=sid, cantidad=float(cantidad),
                fecha_pedido=_a_fecha(fecha_pedido), fecha_estimada_llegada=_a_fecha(fecha_estimada_llegada),
                estado=estado)).inserted_primary_key[0]

    def obtener_ordenes_compra(self, estado=None):
        """DataFrame de órdenes de compra, opcionalmente filtradas por estado."""
        q = (select(ordenes_compra_abiertas.c.id, productos.c.sku, ordenes_compra_abiertas.c.cantidad,
                    proveedores.c.nombre.label('proveedor'), sucursales.c.nombre.label('sucursal'),
                    ordenes_compra_abiertas.c.fecha_pedido, ordenes_compra_abiertas.c.fecha_estimada_llegada,
                    ordenes_compra_abiertas.c.estado)
             .join(productos, productos.c.id == ordenes_compra_abiertas.c.producto_id)
             .outerjoin(proveedores, proveedores.c.id == ordenes_compra_abiertas.c.proveedor_id)
             .outerjoin(sucursales, sucursales.c.id == ordenes_compra_abiertas.c.sucursal_id))
        if estado:
            q = q.where(ordenes_compra_abiertas.c.estado == estado)
        return self._df(q.order_by(ordenes_compra_abiertas.c.id))

    def obtener_stock_en_transito(self, sucursal=None):
        """Series sku → unidades en órdenes abiertas (de la sucursal o sin sucursal asignada)."""
        q = (select(productos.c.sku, func.sum(ordenes_compra_abiertas.c.cantidad).label('cantidad'))
             .join(productos, productos.c.id == ordenes_compra_abiertas.c.producto_id)
             .where(ordenes_compra_abiertas.c.estado == 'abierta'))
        if sucursal:
            sid = self.obtener_o_crear_sucursal(sucursal)
            q = q.where((ordenes_compra_abiertas.c.sucursal_id == sid) | ordenes_compra_abiertas.c.sucursal_id.is_(None))
        df = self._df(q.group_by(productos.c.sku))
        return df.set_index('sku')['cantidad'] if not df.empty else pd.Series(dtype=float)

    # ── pronósticos y métricas ─────────────────────────────
    def guardar_pronosticos(self, sucursal, df, fecha_ejecucion, periodo_inicio, periodo_fin, empresa_id=None):
        """df: sku, modelo (o modelo_asignado), p10/p50/p90 (o demanda_p*_141d) [, venta_real]."""
        if df is None or df.empty:
            return 0
        sid = self.obtener_o_crear_sucursal(sucursal, empresa_id)
        mapa = self.mapa_productos(empresa_id)
        faltan = sorted(set(df['sku'].astype(str)) - set(mapa))
        if faltan:
            mapa = self.guardar_productos(pd.DataFrame({'sku': faltan}), empresa_id)

        def col(r, *nombres):
            return next((_num(r[n]) for n in nombres if n in r), None)
        filas = [{'producto_id': mapa[str(r['sku'])], 'sucursal_id': sid,
                  'modelo': _texto(r.get('modelo', r.get('modelo_asignado'))),
                  'fecha_ejecucion': pd.Timestamp(fecha_ejecucion).to_pydatetime(),
                  'periodo_inicio': _a_fecha(periodo_inicio), 'periodo_fin': _a_fecha(periodo_fin),
                  'valor_p10': col(r, 'valor_p10', 'p10', 'demanda_p10_141d'),
                  'valor_p50': col(r, 'valor_p50', 'p50', 'demanda_p50_141d'),
                  'valor_p90': col(r, 'valor_p90', 'p90', 'demanda_p90_141d'),
                  'venta_real': col(r, 'venta_real')}
                 for r in df.to_dict('records')]
        with self.engine.begin() as conn:
            for lote in _lotes(filas):
                conn.execute(insert(pronosticos), lote)
        return len(filas)

    def obtener_pronosticos(self, sucursal=None, desde=None, hasta=None, skus=None):
        """Pronósticos guardados con sku y sucursal, filtrables por período y SKUs."""
        q = (select(pronosticos, productos.c.sku, sucursales.c.nombre.label('sucursal'))
             .join(productos, productos.c.id == pronosticos.c.producto_id)
             .join(sucursales, sucursales.c.id == pronosticos.c.sucursal_id))
        if sucursal:
            q = q.where(sucursales.c.codigo == slug(sucursal))
        if desde:
            q = q.where(pronosticos.c.periodo_inicio >= _a_fecha(desde))
        if hasta:
            q = q.where(pronosticos.c.periodo_fin <= _a_fecha(hasta))
        if skus is not None:
            q = q.where(productos.c.sku.in_([str(s) for s in skus]))
        return self._df(q.order_by(pronosticos.c.id))

    def guardar_metricas(self, df):
        """df: periodo_inicio, periodo_fin y opcionales sku (o producto_id), modelo, mape, mae, rmse, bias,
        muestras."""
        if df is None or df.empty:
            return 0
        mapa = self.mapa_productos() if 'sku' in df.columns else {}
        filas = []
        for r in df.to_dict('records'):
            pid = r.get('producto_id') or (mapa.get(str(r['sku'])) if 'sku' in r else None)
            filas.append({'producto_id': pid, 'modelo': _texto(r.get('modelo')),
                          'periodo_inicio': _a_fecha(r['periodo_inicio']), 'periodo_fin': _a_fecha(r['periodo_fin']),
                          **{k: _num(r.get(k)) for k in ('mape', 'mae', 'rmse', 'bias')},
                          'muestras': None if r.get('muestras') is None else int(r['muestras'])})
        with self.engine.begin() as conn:
            for lote in _lotes(filas):
                conn.execute(insert(metricas_periodo), lote)
        return len(filas)

    def obtener_metricas(self, modelo=None):
        """Filas de metricas_periodo (opcionalmente de un modelo) con el sku de cada una."""
        q = select(metricas_periodo, productos.c.sku).outerjoin(productos, productos.c.id == metricas_periodo.c.producto_id)
        if modelo:
            q = q.where(metricas_periodo.c.modelo == modelo)
        return self._df(q.order_by(metricas_periodo.c.id))

    def guardar_ejecucion_backtesting(self, fecha_inicio, fecha_fin, total_productos=None, total_periodos=None,
                                      mape_promedio=None, ruta_excel=None, configuracion=None, empresa_id=None):
        """Registra una corrida de backtesting. Devuelve su id."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        with self.engine.begin() as conn:
            return conn.execute(insert(ejecuciones_backtesting).values(
                empresa_id=empresa_id, fecha_inicio=_a_fecha(fecha_inicio), fecha_fin=_a_fecha(fecha_fin),
                total_productos=total_productos, total_periodos=total_periodos, mape_promedio=mape_promedio,
                ruta_excel=ruta_excel,
                configuracion_json=json.dumps(configuracion, default=str) if configuracion else None
            )).inserted_primary_key[0]

    def listar_ejecuciones_backtesting(self):
        """Corridas de backtesting, la más reciente primero."""
        return self._df(select(ejecuciones_backtesting).order_by(ejecuciones_backtesting.c.id.desc()))

    # ── plantillas y categorías ────────────────────────────
    def guardar_plantilla_categorias(self, nombre, reglas, descripcion='', es_plantilla_sistema=False):
        """Crea o reemplaza una plantilla de categorías (por nombre)."""
        fila = {'nombre': nombre, 'descripcion': descripcion,
                'reglas_json': json.dumps(reglas, ensure_ascii=False), 'es_plantilla_sistema': bool(es_plantilla_sistema)}
        with self.engine.begin() as conn:
            self._upsert(conn, plantillas_categorias, [fila], ['nombre'],
                         ['descripcion', 'reglas_json', 'es_plantilla_sistema'])

    def listar_plantillas_categorias(self):
        """DataFrame de plantillas; la columna `reglas` trae el JSON decodificado."""
        df = self._df(select(plantillas_categorias).order_by(plantillas_categorias.c.id))
        if not df.empty:
            df['reglas'] = df['reglas_json'].map(json.loads)
        return df

    def guardar_categorias_activas(self, categorias, empresa_id=None):
        """Reemplaza las categorías activas. categorias: lista de dicts nombre, condicion_python, color,
        orden, accion_sugerida."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        with self.engine.begin() as conn:
            conn.execute(delete(categorias_activas).where(categorias_activas.c.empresa_id == empresa_id))
            if categorias:
                conn.execute(insert(categorias_activas), [
                    {'empresa_id': empresa_id, 'nombre': c['nombre'], 'condicion_python': c['condicion_python'],
                     'color': c.get('color'), 'orden': int(c.get('orden', i)), 'accion_sugerida': c.get('accion_sugerida')}
                    for i, c in enumerate(categorias)])

    def listar_categorias_activas(self, empresa_id=None):
        """Categorías activas de la empresa, en su orden."""
        empresa_id = empresa_id or self.empresa_por_defecto()
        return self._df(select(categorias_activas).where(categorias_activas.c.empresa_id == empresa_id)
                        .order_by(categorias_activas.c.orden))

    # ── perfiles de importación ────────────────────────────
    def guardar_perfil_importacion(self, nombre, mapeo_columnas, formato_fecha=None, separador=None, tipo='ventas'):
        """Crea o reemplaza un perfil de importación (mapeo de columnas + formato de fecha)."""
        fila = {'nombre': nombre.strip(), 'tipo': tipo, 'mapeo_columnas': json.dumps(mapeo_columnas, ensure_ascii=False),
                'formato_fecha': formato_fecha, 'separador': separador, 'creado_en': _ahora()}
        with self.engine.begin() as conn:
            self._upsert(conn, perfiles_importacion, [fila], ['nombre'],
                         ['tipo', 'mapeo_columnas', 'formato_fecha', 'separador'])

    def listar_perfiles_importacion(self, tipo=None):
        """DataFrame de perfiles; la columna `mapeo` trae el dict decodificado."""
        q = select(perfiles_importacion)
        if tipo:
            q = q.where(perfiles_importacion.c.tipo == tipo)
        df = self._df(q.order_by(perfiles_importacion.c.nombre))
        if not df.empty:
            df['mapeo'] = df['mapeo_columnas'].map(json.loads)
        return df

    def obtener_perfil_importacion(self, nombre):
        """Perfil como dict (con `mapeo` decodificado), o None."""
        with self.engine.connect() as conn:
            f = conn.execute(select(perfiles_importacion).where(perfiles_importacion.c.nombre == nombre)).first()
        if not f:
            return None
        d = dict(f._mapping)
        d['mapeo'] = json.loads(d['mapeo_columnas'])
        return d

    def eliminar_perfil_importacion(self, nombre):
        """Borra el perfil con ese nombre (si existe)."""
        with self.engine.begin() as conn:
            conn.execute(delete(perfiles_importacion).where(perfiles_importacion.c.nombre == nombre))

    def productos_por_codigo(self, empresa_id=None):
        """{código de barras o SKU: sku} para validar importaciones."""
        p = self.obtener_productos(empresa_id)
        mapa = {str(s): str(s) for s in p['sku']} if not p.empty else {}
        if not p.empty:
            for bc, sku in zip(p['codigo_barras'], p['sku']):
                if bc and str(bc) not in mapa:
                    mapa[str(bc)] = str(sku)
        return mapa

    # ── decisiones de compra ───────────────────────────────
    def guardar_decision(self, sku, sucursal, ciclo_numero, estado, razon='', hasta=None, accion=None,
                         cantidad_sugerida=None, cantidad_aprobada=None, usuario=None, fecha=None):
        """Guarda (o reemplaza) la decisión del usuario sobre un producto en un ciclo y sucursal."""
        fila = {'producto_id': self._id_producto(sku), 'sucursal_id': self.obtener_o_crear_sucursal(sucursal),
                'ciclo_numero': int(ciclo_numero), 'estado': estado, 'razon': _texto(razon) or '',
                'hasta': _a_fecha(hasta),
                'fecha': pd.Timestamp(fecha).to_pydatetime() if fecha else _ahora(), 'accion': accion,
                'cantidad_sugerida': _num(cantidad_sugerida), 'cantidad_aprobada': _num(cantidad_aprobada),
                'usuario': usuario}
        with self.engine.begin() as conn:
            self._upsert(conn, decisiones_compra, [fila], ['producto_id', 'sucursal_id', 'ciclo_numero'],
                         [k for k in fila if k not in ('producto_id', 'sucursal_id', 'ciclo_numero')])

    def borrar_decision(self, sku, sucursal, ciclo_numero):
        """Elimina la decisión del producto en ese ciclo y sucursal (deshacer)."""
        pid = self.mapa_productos().get(str(sku))
        if pid is None:
            return
        sid = self.obtener_o_crear_sucursal(sucursal)
        with self.engine.begin() as conn:
            conn.execute(delete(decisiones_compra).where(decisiones_compra.c.producto_id == pid,
                                                         decisiones_compra.c.sucursal_id == sid,
                                                         decisiones_compra.c.ciclo_numero == int(ciclo_numero)))

    def cargar_decisiones(self, sucursal, ciclo_numero):
        """{sku: {'estado', 'razon', 'hasta', 'fecha'}} del ciclo indicado."""
        q = (select(productos.c.sku, decisiones_compra.c.estado, decisiones_compra.c.razon,
                    decisiones_compra.c.hasta, decisiones_compra.c.fecha)
             .join(productos, productos.c.id == decisiones_compra.c.producto_id)
             .join(sucursales, sucursales.c.id == decisiones_compra.c.sucursal_id)
             .where(sucursales.c.codigo == slug(sucursal), decisiones_compra.c.ciclo_numero == int(ciclo_numero)))
        with self.engine.connect() as conn:
            filas = conn.execute(q).all()
        return {f.sku: {'estado': f.estado, 'razon': f.razon or '',
                        'hasta': f.hasta.isoformat() if f.hasta else None,
                        'fecha': f.fecha.isoformat() if f.fecha else None} for f in filas}

    def contar_decisiones(self):
        """Cantidad total de decisiones guardadas."""
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(decisiones_compra)).scalar()

    # ── historial de ejecuciones ───────────────────────────
    def registrar_ejecucion(self, sucursal, recomendacion, info_ciclo=None, scorecard=None, fecha=None,
                            origen=None):
        """Guarda una ejecución del pipeline con su recomendación por SKU. Devuelve el id."""
        sid = self.obtener_o_crear_sucursal(sucursal)
        info = info_ciclo or {}
        rec = recomendacion
        tiene_accion = 'accion' in rec.columns
        # Fase 5: compra = columna 'decision' (la acción de compra depende de la plantilla)
        compra = (rec['decision'].astype(str) == 'COMPRAR') if 'decision' in rec.columns else (
            rec['accion'] == 'RECOMPRAR' if tiene_accion else pd.Series(False, index=rec.index))
        n_recomprar = int(compra.sum()) if tiene_accion else 0
        costo = rec['costo_total_compra_pen'] if 'costo_total_compra_pen' in rec.columns else pd.Series(0.0, index=rec.index)
        inversion = float(costo[compra].sum() if tiene_accion else costo.sum())
        agr = (scorecard or {}).get('agregadas', {})
        mapa = self.guardar_productos(rec[[c for c in ('sku', 'nombre', 'categoria', 'precio_regular_pen',
                                                       'modelo_asignado', 'segmento') if c in rec.columns]])
        cols = {'sku': 'sku', 'nombre': 'nombre', 'categoria': 'categoria', 'clase_abc': 'clase_abc',
                'accion': 'accion', 'stock_actual': 'stock_actual', 'stock_en_transito': 'stock_en_transito',
                'demanda_p50_141d': 'demanda_p50', 'rop': 'rop', 'nivel_objetivo': 'nivel_objetivo',
                'compra_sugerida_unid': 'compra_sugerida', 'costo_total_compra_pen': 'costo_total',
                'proveedor_principal': 'proveedor_principal', 'margen_pct': 'margen_pct',
                'tendencia_venta': 'tendencia_venta'}
        disponibles = {k: v for k, v in cols.items() if k in rec.columns}
        texto = {'sku', 'nombre', 'categoria', 'clase_abc', 'accion', 'proveedor_principal'}
        with self.engine.begin() as conn:
            ej_id = conn.execute(insert(ejecuciones).values(
                sucursal_id=sid, fecha=pd.Timestamp(fecha).to_pydatetime() if fecha else _ahora(),
                etiqueta=_texto(info.get('etiqueta')) or '', ciclo_numero=_entero(info.get('ciclo_numero'), 0),
                dia_ciclo=_entero(info.get('dia_ciclo'), 0), importacion_num=_entero(info.get('importacion_num'), 0),
                skus_evaluados=len(rec), skus_recomprar=n_recomprar, inversion_total=round(inversion, 2),
                wmape=_num(agr.get('wmape')), calificacion=_texto(agr.get('calificacion')),
                metadata_json=json.dumps(info_ciclo, default=str) if info_ciclo else None,
                origen=origen)).inserted_primary_key[0]
            filas = []
            for r in rec[list(disponibles)].to_dict('records'):
                fila = {'ejecucion_id': ej_id, 'producto_id': mapa.get(str(r['sku']))}
                for k, v in disponibles.items():
                    fila[v] = _texto(r[k]) if v in texto else _num(r[k])
                filas.append(fila)
            for lote in _lotes(filas):
                conn.execute(insert(recomendaciones), lote)
            if scorecard:
                contra, roi = scorecard.get('contrafactual', {}), scorecard.get('roi', {})
                conn.execute(insert(metricas_ejecucion).values(
                    ejecucion_id=ej_id, wmape=agr.get('wmape'), medape=agr.get('medape'),
                    sesgo_medio_pct=agr.get('sesgo_medio_pct'), cobertura_pct=agr.get('cobertura_intervalo_pct'),
                    calificacion=agr.get('calificacion'), quiebres_sin_preve=contra.get('quiebres_sin_preve'),
                    quiebres_con_preve=contra.get('quiebres_con_preve'),
                    quiebres_evitados=contra.get('quiebres_evitados'), roi_pct=roi.get('roi_pct'),
                    beneficio_total=roi.get('beneficio_total_estimado'),
                    detalle_json=json.dumps(scorecard, default=str)))
        return ej_id

    def _q_ejecuciones(self):
        return (select(ejecuciones, sucursales.c.nombre.label('sucursal'))
                .join(sucursales, sucursales.c.id == ejecuciones.c.sucursal_id))

    def consultar_ejecuciones(self, sucursal=None, limite=10, asc=False):
        """Ejecuciones del pipeline con nombre de sucursal (la más reciente primero salvo asc=True)."""
        q = self._q_ejecuciones()
        if sucursal:
            q = q.where(sucursales.c.codigo == slug(sucursal))
        q = q.order_by(ejecuciones.c.id.asc() if asc else ejecuciones.c.id.desc())
        if limite:
            q = q.limit(limite)
        df = self._df(q)
        if not df.empty:
            df['fecha'] = df['fecha'].map(lambda x: x.isoformat() if hasattr(x, 'isoformat') else x)
        return df

    def compras_sugeridas(self, ejecucion_id):
        """{sku: compra_sugerida} de una ejecución."""
        with self.engine.connect() as conn:
            return dict(conn.execute(select(recomendaciones.c.sku, recomendaciones.c.compra_sugerida)
                                     .where(recomendaciones.c.ejecucion_id == ejecucion_id)).all())

    def recomendaciones_de_sku(self, sku, limite=5):
        """Historial de recomendaciones de un SKU, la más reciente primero."""
        q = (select(recomendaciones, ejecuciones.c.fecha, sucursales.c.nombre.label('sucursal'),
                    ejecuciones.c.etiqueta, ejecuciones.c.ciclo_numero)
             .join(ejecuciones, ejecuciones.c.id == recomendaciones.c.ejecucion_id)
             .join(sucursales, sucursales.c.id == ejecuciones.c.sucursal_id)
             .where(recomendaciones.c.sku == str(sku)).order_by(ejecuciones.c.id.desc()).limit(limite))
        return self._df(q)

    def evolucion_metricas(self, sucursal=None):
        """Ejecuciones con sus métricas de validación (si las hubo), en orden cronológico."""
        q = (select(ejecuciones.c.id, ejecuciones.c.fecha, sucursales.c.nombre.label('sucursal'),
                    ejecuciones.c.etiqueta, ejecuciones.c.ciclo_numero, ejecuciones.c.skus_evaluados,
                    ejecuciones.c.inversion_total, metricas_ejecucion.c.wmape, metricas_ejecucion.c.calificacion,
                    metricas_ejecucion.c.quiebres_evitados, metricas_ejecucion.c.roi_pct)
             .join(sucursales, sucursales.c.id == ejecuciones.c.sucursal_id)
             .outerjoin(metricas_ejecucion, metricas_ejecucion.c.ejecucion_id == ejecuciones.c.id))
        if sucursal:
            q = q.where(sucursales.c.codigo == slug(sucursal))
        return self._df(q.order_by(ejecuciones.c.id))

    def ultimo_scorecard(self, sucursal=None):
        """WMAPE y calificación de la última validación real (de la sucursal si se indica), o None."""
        q = (select(metricas_ejecucion.c.wmape, metricas_ejecucion.c.calificacion)
             .join(ejecuciones, ejecuciones.c.id == metricas_ejecucion.c.ejecucion_id)
             .join(sucursales, sucursales.c.id == ejecuciones.c.sucursal_id)
             .where(metricas_ejecucion.c.wmape.is_not(None)))
        if sucursal:
            q = q.where(sucursales.c.codigo == slug(sucursal))
        with self.engine.connect() as conn:
            f = conn.execute(q.order_by(metricas_ejecucion.c.id.desc()).limit(1)).first()
        return {'wmape': f.wmape, 'calificacion': f.calificacion} if f else None

    def buscar_en_historial(self, texto, limite=6, sucursal=None):
        """Último registro de cada SKU cuyo nombre o SKU coincide con el texto."""
        patron = f'%{texto.strip()}%'
        rn = func.row_number().over(partition_by=recomendaciones.c.sku,
                                    order_by=ejecuciones.c.id.desc()).label('rn')
        veces = func.count().over(partition_by=recomendaciones.c.sku).label('veces')
        base = (select(recomendaciones.c.sku, recomendaciones.c.nombre, recomendaciones.c.accion,
                       recomendaciones.c.compra_sugerida, ejecuciones.c.fecha, ejecuciones.c.id.label('ej_id'),
                       rn, veces)
                .join(ejecuciones, ejecuciones.c.id == recomendaciones.c.ejecucion_id)
                .join(sucursales, sucursales.c.id == ejecuciones.c.sucursal_id)
                .where(recomendaciones.c.nombre.like(patron) | recomendaciones.c.sku.like(patron)))
        if sucursal:
            base = base.where(sucursales.c.codigo == slug(sucursal))
        sub = base.subquery()
        q = select(sub).where(sub.c.rn == 1).order_by(sub.c.ej_id.desc()).limit(limite)
        with self.engine.connect() as conn:
            filas = conn.execute(q).all()
        return [{'sku': f.sku, 'nombre': f.nombre, 'accion': f.accion, 'compra': f.compra_sugerida,
                 'fecha': f.fecha.isoformat() if hasattr(f.fecha, 'isoformat') else f.fecha, 'veces': f.veces}
                for f in filas]

    def hay_ejecuciones(self):
        """True si el historial tiene al menos una ejecución."""
        with self.engine.connect() as conn:
            return (conn.execute(select(func.count()).select_from(ejecuciones)).scalar() or 0) > 0

    # ── conteos (validación de migraciones) ────────────────
    def contar(self, nombre_tabla):
        """Cantidad de filas de una tabla del esquema."""
        tabla = metadata.tables[nombre_tabla]
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(tabla)).scalar()

    def conteos(self):
        """{tabla: filas} de todas las tablas (para validar migraciones)."""
        return {t: self.contar(t) for t in metadata.tables if t != 'schema_version'}

    # ── historial antiguo (preve_historial.db por carpeta) ─
    def importar_historial_legado(self, ruta_db, sucursal_defecto='San José'):
        """Importa un preve_historial.db antiguo. Idempotente: no vuelve a importar la misma ruta.

        Returns: dict con los conteos origen/importados por tabla, o None si ya estaba importado.
        """
        ruta = os.path.abspath(ruta_db)
        clave = f'legado_importado:{ruta}'
        if self.get_config(clave):
            return None
        legado = create_engine('sqlite:///' + ruta.replace('\\', '/'))
        try:
            with legado.connect() as conn:
                tablas = set(legado.dialect.get_table_names(conn))

                def leer(t):
                    return pd.read_sql_table(t, conn) if t in tablas else pd.DataFrame()
                ej, rec, met, dec = leer('ejecuciones'), leer('recomendaciones'), leer('metricas'), leer('decisiones')
        finally:
            legado.dispose()

        resumen = {'ejecuciones': [len(ej), 0], 'recomendaciones': [len(rec), 0],
                   'metricas': [len(met), 0], 'decisiones': [len(dec), 0]}
        mapa_rec = {'demanda_p50': 'demanda_p50_141d', 'compra_sugerida': 'compra_sugerida_unid',
                    'costo_total': 'costo_total_compra_pen'}
        for _, e in ej.sort_values('id').iterrows():
            r = rec[rec['ejecucion_id'] == e['id']].rename(columns=mapa_rec)
            m = met[met['ejecucion_id'] == e['id']] if len(met) else met
            sc = None
            if len(m):
                mm = m.iloc[0]
                try:
                    sc = json.loads(mm['detalle_json']) if mm.get('detalle_json') else None
                except (TypeError, json.JSONDecodeError):
                    sc = None
                sc = sc or {'agregadas': {'wmape': mm.get('wmape'), 'calificacion': mm.get('calificacion')}}
            info = None
            if e.get('metadata_json'):
                try:
                    info = json.loads(e['metadata_json'])
                except json.JSONDecodeError:
                    info = None
            info = info or {'etiqueta': e.get('etiqueta'), 'ciclo_numero': e.get('ciclo_numero'),
                            'dia_ciclo': e.get('dia_ciclo'), 'importacion_num': e.get('importacion_num')}
            if r.empty:
                r = pd.DataFrame(columns=['sku'])
            ej_id = self.registrar_ejecucion(e.get('sucursal') or sucursal_defecto, r, info, sc,
                                             fecha=e['fecha'], origen=ruta)
            # conservar la inversión y los conteos tal como se guardaron originalmente
            with self.engine.begin() as conn:
                conn.execute(update(ejecuciones).where(ejecuciones.c.id == ej_id).values(
                    inversion_total=_num(e.get('inversion_total')), skus_recomprar=_entero(e.get('skus_recomprar')),
                    skus_evaluados=_entero(e.get('skus_evaluados')), wmape=_num(e.get('wmape')),
                    calificacion=_texto(e.get('calificacion'))))
            resumen['ejecuciones'][1] += 1
            resumen['recomendaciones'][1] += len(r)
            resumen['metricas'][1] += 1 if len(m) else 0
        for _, d in dec.iterrows():
            self.guardar_decision(d['sku'], d.get('sucursal') or sucursal_defecto, d.get('ciclo_numero', 0),
                                  d['estado'], d.get('razon'), d.get('hasta'), fecha=d.get('fecha'))
            resumen['decisiones'][1] += 1
        self.set_config(clave, True, descripcion='Historial antiguo ya importado (no repetir)')
        logger.info('Historial antiguo importado desde %s: %s', ruta, resumen)
        return resumen

    # ── interno ────────────────────────────────────────────
    def _df(self, q):
        with self.engine.connect() as conn:
            res = conn.execute(q)
            return pd.DataFrame(res.fetchall(), columns=list(res.keys()))


def _convertir_config(valor, tipo):
    if tipo == 'int':
        return int(valor)
    if tipo == 'float':
        return float(valor)
    if tipo == 'bool':
        return str(valor).lower() in ('1', 'true', 'si', 'sí', 'yes')
    if tipo == 'json':
        return json.loads(valor)
    return valor


def _pragmas_sqlite(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute('PRAGMA foreign_keys=ON')
    cur.execute('PRAGMA journal_mode=WAL')
    cur.close()
