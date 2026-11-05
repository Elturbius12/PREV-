# -*- coding: utf-8 -*-
"""
Migra los datos de PREVÉ basados en archivos (JSON / CSV / historial SQLite por carpeta) a preve.db.

Uso:
    python scripts/migrar_a_sqlite.py                       # sucursales y carpetas de preve_config.json
    python scripts/migrar_a_sqlite.py --con-ventas          # además importa las ventas diarias de los Excel
    python scripts/migrar_a_sqlite.py --origen "San José=C:/ruta/salida" --db sqlite:///C:/tmp/preve.db

Qué importa por sucursal (carpeta de resultados):
    preve_config.json            → configuracion, sucursales
    ciclo_estado.json            → configuracion['ciclo_estado:<sucursal>']
    preve_historial.db           → ejecuciones, recomendaciones, metricas_ejecucion, decisiones_compra
    recomendacion_compra.csv     → productos, pronosticos, stock_actual
    ordenes_compra_abiertas.csv  → ordenes_compra_abiertas
    (--con-ventas) reportes ERP  → ventas_diarias

No modifica ni borra ningún archivo de origen. Al final compara los conteos de origen y destino y
termina con código 1 si alguno no coincide.
"""
import argparse
import datetime
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import pandas as pd  # noqa: E402

from database import Database, slug  # noqa: E402

CICLOS_DEFECTO = [
    # nombre, duración, lead time, día del reporte final, horizonte
    ('Internacional', 105, 36, 104, 141),
    ('Local', 30, 10, 29, 40),
]
CLAVES_CONFIG = {'usuario': 'usuario_nombre', 'sucursal': 'sucursal_activa',
                 'ventas_son_salidas_de_almacen': 'ventas_son_salidas_de_almacen',
                 'compras_son_entradas_de_almacen': 'compras_son_entradas_de_almacen',
                 'ultima_duracion_s': 'ultima_duracion_s', 'pasos_s': 'pasos_s'}


class Verificador:
    """Acumula (fuente, origen, destino) y reporta las diferencias."""

    def __init__(self):
        self.filas = []

    def registrar(self, fuente, origen, destino, nota=''):
        self.filas.append((fuente, origen, destino, nota))

    def imprimir(self):
        ancho = max([len(f[0]) for f in self.filas] + [20])
        print(f'\n{"FUENTE".ljust(ancho)}  {"ORIGEN":>9}  {"DESTINO":>9}  ESTADO')
        print('─' * (ancho + 36))
        for fuente, o, d, nota in self.filas:
            ok = o == d
            print(f'{fuente.ljust(ancho)}  {o:>9,}  {d:>9,}  {"✓" if ok else "✗ NO COINCIDE"} {nota}')
        return all(o == d for _, o, d, _ in self.filas)


def _leer_json(ruta):
    with open(ruta, 'r', encoding='utf-8') as f:
        return json.load(f)


def migrar_config(db, ruta_config, ver):
    if not os.path.exists(ruta_config):
        print(f'· Sin {ruta_config}, se omite la configuración')
        return {}
    cfg = _leer_json(ruta_config)
    escritas = 0
    for k, clave in CLAVES_CONFIG.items():
        if k in cfg:
            db.set_config(clave, cfg[k])
            escritas += 1
    origen = sum(1 for k in CLAVES_CONFIG if k in cfg)
    sucs = cfg.get('sucursales', {})
    for nombre, carpetas in sucs.items():
        db.obtener_o_crear_sucursal(nombre)
        db.set_config(f'carpetas:{slug(nombre)}', carpetas, descripcion=f'Carpetas de entrada/salida de {nombre}')
    n_suc = sum(1 for s in db.listar_sucursales()['codigo'] if s in {slug(n) for n in sucs})
    ver.registrar('preve_config.json · parámetros', origen, escritas)
    ver.registrar('preve_config.json · sucursales', len(sucs), n_suc)
    return cfg


def migrar_ciclos_compra(db, ver):
    for nombre, dur, lt, dia_final, hz in CICLOS_DEFECTO:
        db.guardar_ciclo_compra(nombre, dur, lt, hz, dia_final)
    ver.registrar('ciclos de compra (valores actuales del código)', len(CICLOS_DEFECTO),
                  len(db.listar_ciclos_compra()))


def migrar_carpeta(db, sucursal, carpeta, ver, entrada=None, con_ventas=False):
    print(f'\n▶ {sucursal}: {carpeta}')
    etq = f'[{sucursal}] '

    # ── estado del ciclo ──
    ruta = os.path.join(carpeta, 'ciclo_estado.json')
    if os.path.exists(ruta):
        estado = _leer_json(ruta)
        db.set_config(f'ciclo_estado:{slug(sucursal)}', estado, descripcion='Estado del ciclo (antes ciclo_estado.json)')
        guardado = db.get_config(f'ciclo_estado:{slug(sucursal)}')
        ver.registrar(etq + 'ciclo_estado.json · importaciones', len(estado.get('importaciones', [])),
                      len(guardado.get('importaciones', [])))

    # ── historial SQLite antiguo ──
    ruta = os.path.join(carpeta, 'preve_historial.db')
    if os.path.exists(ruta):
        res = db.importar_historial_legado(ruta, sucursal_defecto=sucursal)
        if res is None:
            print('· preve_historial.db ya se había importado antes (no se duplica)')
        else:
            for tabla, (o, d) in res.items():
                ver.registrar(etq + f'preve_historial.db · {tabla}', o, d)

    # ── última recomendación → productos, pronósticos y stock ──
    ruta = os.path.join(carpeta, 'recomendacion_compra.csv')
    if os.path.exists(ruta):
        rec = pd.read_csv(ruta, dtype={'sku': str})
        fecha_ej = datetime.datetime.fromtimestamp(os.path.getmtime(ruta))
        fecha_datos = pd.Timestamp(fecha_ej.date())
        meta = os.path.join(carpeta, 'recomendacion_anterior_meta.json')
        if os.path.exists(meta):
            fecha_datos = pd.Timestamp(_leer_json(meta)['fecha_datos'])
        inicio = fecha_datos + pd.Timedelta(days=1)
        antes_p = db.contar('pronosticos')
        db.guardar_productos(rec)
        db.guardar_pronosticos(sucursal, rec, fecha_ej, inicio, inicio + pd.Timedelta(days=140))
        ver.registrar(etq + 'recomendacion_compra.csv · pronósticos', len(rec), db.contar('pronosticos') - antes_p)
        stock = rec.set_index('sku')['stock_actual'].dropna()
        db.guardar_stock_actual(sucursal, stock, fecha_datos)
        ver.registrar(etq + 'recomendacion_compra.csv · stock actual', len(stock),
                      len(db.obtener_stock_actual(sucursal)))

    # ── órdenes abiertas ──
    ruta = os.path.join(carpeta, 'ordenes_compra_abiertas.csv')
    if os.path.exists(ruta):
        ordenes = pd.read_csv(ruta, dtype={'sku': str}, encoding='utf-8-sig')
        antes = db.contar('ordenes_compra_abiertas')
        for r in ordenes.to_dict('records'):
            db.guardar_orden_compra(r['sku'], r['cantidad'], proveedor=r.get('proveedor') or None,
                                    fecha_pedido=r.get('fecha_pedido'),
                                    fecha_estimada_llegada=r.get('fecha_estimada_llegada'),
                                    estado=str(r.get('estado') or 'abierta').strip().lower(), sucursal=sucursal)
        ver.registrar(etq + 'ordenes_compra_abiertas.csv', len(ordenes), db.contar('ordenes_compra_abiertas') - antes)

    # ── ventas diarias desde los reportes del ERP ──
    if con_ventas and entrada and os.path.isdir(entrada):
        from preve_pipeline import cargar_datos, construir_dim_producto, construir_mapas, reconstruir_stock
        silencio = lambda m: None  # noqa: E731
        print('· Leyendo reportes del ERP para importar ventas diarias (≈1 min)...')
        listado, ventas, compras, movs = cargar_datos(entrada, silencio)
        dim, mapa_df = construir_dim_producto(listado, silencio)
        _, vd, _ = reconstruir_stock(ventas, compras, movs, dim, *construir_mapas(dim, mapa_df),
                                     listado.attrs.get('fecha_listado'), silencio)
        db.guardar_productos(dim)
        db.guardar_ventas(sucursal, vd.assign(cantidad=-vd['mov']))
        en_base = db.obtener_ventas(sucursal, vd['fecha'].min(), vd['fecha'].max())
        ver.registrar(etq + 'reportes ERP · ventas diarias (sku×día)', len(vd), len(en_base))
        ver.registrar(etq + 'reportes ERP · unidades vendidas', int(round(-vd['mov'].sum())),
                      int(round(en_base['cantidad'].sum())))


def main(argv=None):
    p = argparse.ArgumentParser(description='Migra los datos de PREVÉ a preve.db')
    p.add_argument('--config', default=os.path.join(RAIZ, 'preve_config.json'))
    p.add_argument('--origen', action='append', default=[],
                   help='"Sucursal=carpeta_de_resultados" (se puede repetir). Por defecto, las de la configuración.')
    p.add_argument('--db', help='URL de la base destino (por defecto preve.db junto a la app o PREVE_DB_URL)')
    p.add_argument('--con-ventas', action='store_true', help='Importar también las ventas diarias de los Excel')
    a = p.parse_args(argv)

    db = Database(a.db) if a.db else Database.default()
    print(f'Base destino: {db.url}  (esquema v{db.version_esquema()})')
    ver = Verificador()
    cfg = migrar_config(db, a.config, ver)
    migrar_ciclos_compra(db, ver)

    origenes = []
    for o in a.origen:
        nombre, _, carpeta = o.partition('=')
        origenes.append((nombre.strip(), carpeta.strip(), None))
    if not origenes:
        for nombre, c in cfg.get('sucursales', {}).items():
            if c.get('salida'):
                origenes.append((nombre, c['salida'], c.get('entrada')))
    if not origenes:
        print('No hay carpetas de resultados que migrar.')
    for nombre, carpeta, entrada in origenes:
        if not os.path.isdir(carpeta):
            print(f'⚠ {nombre}: la carpeta {carpeta} no existe, se omite')
            continue
        migrar_carpeta(db, nombre, carpeta, ver, entrada, a.con_ventas)

    ok = ver.imprimir()
    print('\nConteos por tabla en preve.db:')
    for t, n in db.conteos().items():
        print(f'  {t:<26} {n:>9,}')
    print('\n✓ Migración verificada: todos los conteos coinciden.' if ok else '\n✗ Hay diferencias de conteo.')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
