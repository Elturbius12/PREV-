# Índice de Ceguera por Desabastecimiento (ICD)

Mide qué parte de los "ceros de venta" de un producto **no son falta de demanda sino falta de stock**.
Se calcula con solo dos datos que cualquier ERP tiene: ventas por día y stock al cierre de cada día.
No requiere PREVÉ.

## Definición

Para cada producto y cada semana (lunes a domingo):

1. **Venta semanal** = unidades vendidas en la semana.
2. **Disponibilidad semanal** = días de la semana con stock > 0 ÷ días con dato de stock.
   El stock negativo que muestran algunos ERP cuenta como 0.
3. **Semana sin stock**: tiene disponibilidad < 0,5, es decir, estuvo agotado más de la mitad de los días.
   Una semana sin ningún dato de stock **no** se considera sin stock.
4. **Semana sin venta**: tiene venta semanal = 0.
5. **Semana censurada**: semana sin venta **y** sin stock.
6. Solo se cuentan las semanas desde que el producto existe, es decir, desde su primera semana con venta o con stock.

```
ICD(producto) = semanas censuradas / semanas sin venta
```

Si el producto no tiene semanas sin venta, su ICD queda indefinido y no entra en la distribución.

Para un grupo (categoría ABC, segmento, sucursal o empresa) se usa el **ICD agregado ponderado**:

```
ICD(grupo) = Σ semanas censuradas / Σ semanas sin venta   (sobre los productos del grupo)
```

No es el promedio de los ICD individuales. Ese promedio y la mediana se reportan aparte, para describir la distribución.

## Interpretación

| ICD | Lectura |
|---|---|
| 0 | Todos los ceros de venta fueron "no hubo demanda": el historial es confiable. |
| 0,5 | La mitad de los ceros de venta ocurrió sin stock: el historial subestima la demanda. |
| 1 | Ningún cero de venta es informativo: el producto vendió cero solo cuando estaba agotado. |

## Cálculo en una hoja de cálculo

1. Tabla diaria con producto, fecha, unidades vendidas y stock al cierre.
2. Agregar por producto y semana: suma de ventas y promedio de `stock>0` (1/0).
3. Columna `censurada = (venta=0) Y (disponibilidad<0,5)` y columna `sin_venta = (venta=0)`.
4. ICD = CONTAR.SI(censurada) / CONTAR.SI(sin_venta).

Implementación de referencia: `analisis_censura.icd_serie()` y `analisis_censura.indice_ceguera()`.
