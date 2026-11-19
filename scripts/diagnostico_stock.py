# -*- coding: utf-8 -*-
"""Diagnóstico (solo lectura): ¿por qué hay ventas en días con stock reconstruido <= 0?

Lista los 15 SKU con más unidades vendidas en días cuyo stock inicial (cierre del día anterior) era <= 0 y, de cada
uno, sus entradas (compras + movimientos de entrada) y salidas (movimientos de salida/traslado) para ver si hay un
patrón, p. ej. ingresos que nunca se registraron. No modifica datos ni lógica.

    python scripts/diagnostico_stock.py [carpeta_de_reportes] [sucursal]
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from preve_config import Config                                                     # noqa: E402
from preve_pipeline import (aplicar_mapeo_ventas, cargar_datos, construir_dim_producto, construir_mapas,  # noqa: E402
                            reconstruir_stock, _mapear_por_codigo_barra, _parse_fechas)

TOP = 15


def main(carpeta=None, sucursal='San José'):
    conf = Config.actual()
    carpeta = carpeta or conf.carpetas(sucursal)['entrada']
    params = conf.parametros_modelo(sucursal)
    silencio = lambda m: None                                                       # noqa: E731
    listado, ventas, compras, movs = cargar_datos(carpeta, silencio)
    ventas = aplicar_mapeo_ventas(ventas, conf.get('mapeo_columnas_ventas'), silencio)
    dim, mapa_df = construir_dim_producto(listado, silencio)
    mapas = construir_mapas(dim, mapa_df)
    stock, vd, _ = reconstruir_stock(
        ventas.copy(), compras.copy(), movs.copy(), dim, *mapas, listado.attrs.get('fecha_listado'), silencio,
        ventas_son_salidas_de_almacen=params.ventas_son_salidas_de_almacen,
        compras_son_entradas_de_almacen=params.compras_son_entradas_de_almacen,
        almacenes_propios=params.almacenes_propios)

    vd.attrs.pop('pedidos_atipicos', None)
    inicial = stock.shift(1)                                    # stock al inicio de cada día = cierre del anterior
    v = vd.copy()
    v['unid'] = -v['mov']
    v = v[v['unid'] > 0]
    v['stock_ini'] = [inicial.at[f, s] if f in inicial.index else float('nan') for s, f in zip(v['sku'], v['fecha'])]
    sin = v[v['stock_ini'] <= 0]
    total_sin, total = sin['unid'].sum(), v['unid'].sum()
    print(f'Ventas en días con stock inicial <= 0: {total_sin:,.0f} u de {total:,.0f} ({total_sin / total:.1%}), '
          f'{sin["sku"].nunique():,} SKU, {int((stock < 0).any().sum()):,} SKU con stock negativo alguna vez.\n')

    # entradas y salidas por SKU, con las mismas reglas de mapeo que reconstruir_stock
    c = _mapear_por_codigo_barra(compras.copy(), 'COD. BARRA', mapas[0], silencio, 'Compras', col_nombre='PRODUCTO',
                                 mapa_bc_nombre=mapas[1])
    c['cantidad'] = pd.to_numeric(c['CANTIDAD'], errors='coerce').fillna(0)
    c['fecha'] = _parse_fechas(c['FECHA'], ['%d-%m-%Y', '%d-%m-%Y %H:%M'], silencio, 'Compras')
    m = movs.copy()
    m['sku'] = m['PRODUCTO'].astype(str).str.strip().str.upper().map(
        {str(n).strip().upper(): s for s, n in zip(dim['sku'], dim['nombre'])}) if 'nombre' in dim.columns else None
    m['cantidad'] = pd.to_numeric(m['CANTIDAD'], errors='coerce').fillna(0)
    m['fecha'] = _parse_fechas(m['FECHA'], ['%d-%m-%Y %I:%M %p', '%d-%m-%Y %H:%M'], silencio, 'Movimientos')
    tipo = m['TIPO DE MOVIMIENTO'].astype(str).str.strip() if 'TIPO DE MOVIMIENTO' in m.columns else ''

    top = sin.groupby('sku')['unid'].sum().nlargest(TOP)
    nombres = dim.set_index('sku')['nombre'] if 'nombre' in dim.columns else pd.Series(dtype=str)
    print(f'{"SKU":<16}{"Producto":<34}{"Vend. sin stock":>16}{"Días":>6}{"Stock mín":>10}{"Compras":>9}'
          f'{"Entradas":>9}{"Salidas":>8}{"Traslados":>10}{"Stock hoy":>10}{"Primera compra":>16}')
    for sku, u in top.items():
        mm = m[m['sku'] == sku] if 'sku' in m else m.iloc[0:0]
        tm = tipo[mm.index] if len(mm) else pd.Series(dtype=str)
        cc = c[c['sku'] == sku]
        primera = cc['fecha'].min()
        print(f'{str(sku):<16}{str(nombres.get(sku, ""))[:32]:<34}{u:>16,.0f}{int((sin["sku"] == sku).sum()):>6}'
              f'{stock[sku].min():>10,.0f}{cc["cantidad"].sum():>9,.0f}'
              f'{mm.loc[tm[tm == "Entrada"].index, "cantidad"].sum() if len(mm) else 0:>9,.0f}'
              f'{mm.loc[tm[tm.isin(["Salida", "Salida de productos"])].index, "cantidad"].sum() if len(mm) else 0:>8,.0f}'
              f'{mm.loc[tm[tm == "Traslado"].index, "cantidad"].sum() if len(mm) else 0:>10,.0f}'
              f'{stock[sku].iloc[-1]:>10,.0f}{str(primera.date()) if pd.notna(primera) else "—":>16}')
        primera_venta_sin = sin[sin['sku'] == sku]['fecha'].min()
        print(f'    primera venta sin stock: {primera_venta_sin.date()}'
              + (' · ANTES de la primera compra registrada (ingreso inicial no registrado)'
                 if pd.notna(primera) and primera_venta_sin < primera else ''))


if __name__ == '__main__':
    main(*(sys.argv[1:3]))
