# -*- coding: utf-8 -*-
"""PREVÉ — Muestra anonimizada de los datos del ERP (reproducibilidad pública del artículo).

generar_muestra_anonima(carpeta_entrada, destino) elige N SKU al azar (semilla fija) entre los que tienen
ventas y escribe en `destino`:

  erp/                 los 4 reportes del ERP (listado, ventas, compras, movimientos) con el MISMO formato que
                       exporta el ERP, filtrados a esos SKU y anonimizados → el pipeline y
                       generar_resultados_articulo.py los leen sin cambios (--entrada destino/erp).
  ventas_muestra.csv   las líneas de venta anonimizadas (formato del ERP).
  catalogo_muestra.csv el catálogo anonimizado de los mismos SKU.
  README_muestra.md    qué se anonimizó y cómo reproducir.

Anonimización: códigos de barra → 2000000000NNN, SKU y nombre → SKU_NNN; clientes, comprobantes, usuarios,
empleados, observaciones, lotes, RUC y número de movimiento se reemplazan o vacían; proveedores → PROVEEDOR_NN;
todos los importes (precios, costos, totales, IGV, descuentos) se multiplican por un factor aleatorio fijo que no
se publica. Cantidades, fechas, stock, categorías y almacenes se conservan (son los datos que usa el modelo).
"""
import os
import unicodedata

import numpy as np
import pandas as pd

SEMILLA_MUESTRA = 20250607
N_SKUS_MUESTRA = 50

_IMPORTES = {'PRECIO COMPRA', 'PRECIO VENTA', 'PRECIO PROMEDIO', 'COSTO*', 'PRECIO X UNIDAD', 'DESCUENTO',
             'TOTAL', 'PRECIO', 'IGV', 'PRECIO+IGV', 'GASTO', 'IMPORTE'}
_GENERICOS = {'CLIENTE': 'CLIENTE', 'USUARIOS': '', 'EMPLEADOS': '', 'OBSERVACION': '', 'LOTE': '',
              'RUC': '', 'VARIANTE': ''}
_TITULOS = {'listado_productos': 'LISTADO DE PRODUCTOS', 'ventas': 'REPORTE DE VENTAS POR PRODUCTO (MUESTRA)',
            'compras': 'REPORTE DE COMPRAS POR PRODUCTO (MUESTRA)',
            'movimientos_producto': 'REPORTE DE MOVIMIENTOS POR PRODUCTO (MUESTRA)'}


def _norm(c):
    s = unicodedata.normalize('NFKD', str(c)).encode('ascii', 'ignore').decode()
    return s.strip().upper()


def _col(df, *nombres):
    objetivo = {_norm(n) for n in nombres}
    return next((c for c in df.columns if _norm(c) in objetivo), None)


def _anonimizar(df, alias_bc, alias_nombre, factor, proveedores, prefijo_doc):
    """Reemplaza códigos, nombres, identificadores e importes. alias_*: valor original normalizado → alias."""
    from preve_pipeline import COLUMNAS_CODIGO_BARRA, _normalizar_nombre, normalizar_codigo
    df = df.copy()
    bc_cols = [c for c in df.columns if c in COLUMNAS_CODIGO_BARRA]
    sku_por_fila = pd.Series(index=df.index, dtype=object)
    for c in bc_cols:
        sku_por_fila = sku_por_fila.fillna(df[c].map(normalizar_codigo).map(alias_bc))
    c_prod = _col(df, 'PRODUCTO')
    if c_prod:
        sku_por_fila = sku_por_fila.fillna(df[c_prod].map(_normalizar_nombre).map(alias_nombre))
    for c in bc_cols:
        df[c] = sku_por_fila.map(lambda a: f'2000000000{a[4:]}' if isinstance(a, str) else '')
    for nombre in ('PRODUCTO', 'SKU'):
        c = _col(df, nombre)
        if c:
            df[c] = sku_por_fila
    for c in df.columns:
        n = _norm(c)
        if n in _IMPORTES:
            v = pd.to_numeric(df[c], errors='coerce')
            df[c] = (v * factor).round(4).where(v.notna(), df[c])
        elif n in _GENERICOS:
            df[c] = _GENERICOS[n]
        elif n == 'PROVEEDOR':
            df[c] = df[c].map(lambda p: proveedores.setdefault(p, f'PROVEEDOR_{len(proveedores) + 1:02d}'))
        elif n == 'COMPROBANTE':
            docs = {d: f'{prefijo_doc}-{i + 1:06d}' for i, d in enumerate(pd.unique(df[c].astype(str)))}
            df[c] = df[c].astype(str).map(docs)
        elif n in ('NRO. MOVIMIENTO', 'N', '#'):
            df[c] = np.arange(1, len(df) + 1)
    df = df.drop(columns=[c for c in df.columns if str(c).startswith('Unnamed')])
    return df


def _escribir_erp(df, ruta, titulo):
    """Excel con el formato del ERP: una fila de título y debajo el encabezado."""
    with pd.ExcelWriter(ruta, engine='openpyxl') as xw:
        df.to_excel(xw, index=False, startrow=1, sheet_name='Hoja1')
        xw.sheets['Hoja1'].cell(row=1, column=1, value=titulo)


def generar_muestra_anonima(carpeta_entrada, destino, n=N_SKUS_MUESTRA, semilla=SEMILLA_MUESTRA,
                            log=lambda m: None):
    """Escribe la muestra anonimizada en `destino`. Devuelve {'carpeta', 'erp', 'skus', 'filas': {...}}."""
    from preve_pipeline import (COLUMNAS_CODIGO_BARRA, _normalizar_nombre, cargar_datos, fecha_desde_nombre,
                                normalizar_codigo)
    listado, ventas, compras, movs = cargar_datos(carpeta_entrada, lambda m: None)
    # catálogo: solo el listado más reciente (es el que el pipeline usa como stock actual)
    ultimo = listado['__archivo_origen'].iloc[-1]
    fecha_listado = fecha_desde_nombre(ultimo)
    cat = listado[listado['__archivo_origen'] == ultimo].drop(
        columns=[c for c in listado.columns if c.startswith('__')])
    cat = cat.assign(_bc=cat['CODIGO BARRAS'].map(normalizar_codigo))
    unicos = cat['_bc'].value_counts()
    cat = cat[(cat['_bc'] != '') & cat['_bc'].map(unicos).eq(1)]
    col_bc_v = next((c for c in ventas.columns if c in COLUMNAS_CODIGO_BARRA), None)
    if col_bc_v is None:
        raise ValueError('El reporte de ventas no tiene columna de código de barras.')
    cant = pd.to_numeric(ventas[_col(ventas, 'CANTIDAD')], errors='coerce').fillna(0)
    con_venta = set(ventas.loc[cant > 0, col_bc_v].map(normalizar_codigo))
    candidatos = sorted(set(cat['_bc']) & con_venta)
    if not candidatos:
        raise ValueError('No hay productos del catálogo con ventas para armar la muestra.')
    rng = np.random.default_rng(semilla)
    elegidos = sorted(rng.choice(candidatos, size=min(n, len(candidatos)), replace=False).tolist())
    factor = float(rng.uniform(0.6, 1.6))                      # fijo por la semilla; no se publica
    alias_bc = {bc: f'SKU_{i + 1:03d}' for i, bc in enumerate(elegidos)}
    cat = cat[cat['_bc'].isin(alias_bc)]
    alias_nombre = {_normalizar_nombre(nm): alias_bc[bc] for bc, nm in zip(cat['_bc'], cat['PRODUCTO'])}

    def filtrar(df, col_bc):
        m = pd.Series(False, index=df.index)
        if col_bc:
            m |= df[col_bc].map(normalizar_codigo).isin(alias_bc)
        c_prod = _col(df, 'PRODUCTO')
        if c_prod:
            m |= df[c_prod].map(_normalizar_nombre).isin(alias_nombre)
        return df[m]

    proveedores = {}
    cat_a = _anonimizar(cat.drop(columns='_bc'), alias_bc, alias_nombre, factor, proveedores, 'CAT')
    cat_a = cat_a.sort_values(_col(cat_a, 'SKU')).reset_index(drop=True)
    if _col(cat_a, 'N'):
        cat_a[_col(cat_a, 'N')] = np.arange(1, len(cat_a) + 1)       # tras ordenar por alias
    ven_a = _anonimizar(filtrar(ventas, col_bc_v), alias_bc, alias_nombre, factor, proveedores, 'V')
    col_bc_c = next((c for c in compras.columns if c in COLUMNAS_CODIGO_BARRA), None)
    com_a = _anonimizar(filtrar(compras, col_bc_c), alias_bc, alias_nombre, factor, proveedores, 'C')
    col_bc_m = next((c for c in movs.columns if c in COLUMNAS_CODIGO_BARRA), None)
    mov_a = _anonimizar(filtrar(movs, col_bc_m), alias_bc, alias_nombre, factor, proveedores, 'M')

    erp = os.path.join(destino, 'erp')
    os.makedirs(erp, exist_ok=True)
    for f in os.listdir(erp):
        if f.lower().endswith(('.xlsx', '.xls')):
            os.remove(os.path.join(erp, f))
    sufijo = fecha_listado.strftime('%Y-%m-%d') if fecha_listado is not None else 'muestra'
    archivos = {'listado_productos': (cat_a, f'listado_productos_{sufijo}.xlsx'),
                'ventas': (ven_a, 'reporte_ventas_producto_muestra.xlsx'),
                'compras': (com_a, 'Reporte_compras_por_producto_muestra.xlsx'),
                'movimientos_producto': (mov_a, 'Reporte_productos_por_movimiento_muestra.xlsx')}
    for tipo, (df, nombre) in archivos.items():
        _escribir_erp(df, os.path.join(erp, nombre), _TITULOS[tipo])
    ven_a.to_csv(os.path.join(destino, 'ventas_muestra.csv'), index=False, encoding='utf-8-sig')
    cat_a.to_csv(os.path.join(destino, 'catalogo_muestra.csv'), index=False, encoding='utf-8-sig')
    filas = {t: int(len(df)) for t, (df, _) in archivos.items()}
    _escribir_readme(destino, len(elegidos), filas, semilla, fecha_listado)
    log(f'  Muestra anónima: {len(elegidos)} SKU, filas {filas}')
    return {'carpeta': destino, 'erp': erp, 'skus': len(elegidos), 'filas': filas}


def _escribir_readme(destino, n, filas, semilla, fecha_listado):
    txt = f"""# Muestra anonimizada de datos — PREVÉ

Muestra de **{n} SKU** elegidos al azar (semilla `{semilla}`) entre los productos del catálogo con al menos una
venta, para que cualquier persona pueda ejecutar el análisis del artículo sin acceder a los datos reales.

## Contenido

| Archivo | Contenido |
|---|---|
| `erp/listado_productos_*.xlsx` | Catálogo (foto del stock al {fecha_listado.date() if fecha_listado is not None else 'último listado'}) — {filas['listado_productos']} filas |
| `erp/reporte_ventas_producto_muestra.xlsx` | Líneas de venta — {filas['ventas']} filas |
| `erp/Reporte_compras_por_producto_muestra.xlsx` | Compras — {filas['compras']} filas |
| `erp/Reporte_productos_por_movimiento_muestra.xlsx` | Movimientos de almacén — {filas['movimientos_producto']} filas |
| `ventas_muestra.csv` | Las mismas ventas en CSV |
| `catalogo_muestra.csv` | El mismo catálogo en CSV |

Los archivos de `erp/` tienen el mismo formato que exporta el ERP (fila de título + encabezado), así que el
pipeline los lee sin cambios.

## Qué se anonimizó

- **Códigos de barra** → `2000000000NNN`; **SKU y nombre del producto** → `SKU_001` … `SKU_{n:03d}` (el mismo
  alias en los cuatro archivos, así se conservan los cruces entre ventas, compras, movimientos y catálogo).
- **Clientes, usuarios, empleados, observaciones, lotes y RUC**: vaciados o reemplazados por un valor genérico.
- **Comprobantes**: renumerados (`V-000001`, `C-000001`…); **número de movimiento**: correlativo.
- **Proveedores** → `PROVEEDOR_01`, `PROVEEDOR_02`…
- **Importes** (precio de compra y venta, costo, precio promedio, totales, IGV, descuentos, gastos): multiplicados
  por un **factor aleatorio fijo** que no se publica. Las razones entre importes (p. ej. el margen porcentual)
  se conservan; los valores absolutos en S/. no son los reales.
- Se conservan: **fechas, cantidades, stock, categorías y nombres de almacén** (los usa la reconstrucción del
  stock y el modelo).

## Cómo reproducir

```bash
pip install -r requirements-lock.txt
python generar_resultados_articulo.py --sucursal Muestra --entrada salida/articulo/muestra_anonima/erp --salida salida/articulo_muestra
```

`--rapido` acorta la corrida (solo el horizonte configurado y sin simulación de compras). Con {n} SKU las cifras
no coinciden con las del artículo (que usa todos los productos); la muestra sirve para verificar que el código
corre de punta a punta y que la metodología es la descrita.
"""
    with open(os.path.join(destino, 'README_muestra.md'), 'w', encoding='utf-8') as f:
        f.write(txt)
