# AUDITORÍA DE CÓDIGO — PREVÉ (Fase 0)

Fecha: 2026-11-05 · Commit auditado: `8493117` · Alcance: los 12 módulos `preve_*.py`, `dashboard_template.html` y los scripts de instalación.

**Leyenda**
- `[L-x.y]`: bug que ya estaba en tu lista (sección x.y del plan).
- `[NUEVO]`: bug que no estaba en la lista. Se reporta aquí y **no se corrige sin tu confirmación**.

---

## Estructura confirmada

| Módulo | Líneas | Rol |
|---|---|---|
| `preve_pipeline.py` | 713 | Carga de los Excel, SKU canónico, reconstrucción de stock, segmentación ADI/CV², los 3 modelos y la recomendación |
| `preve_orquestador.py` | 727 | Ejecuta el pipeline, los Excel, el dashboard, la validación, el historial, el informe y el multi-sucursal |
| `preve_ciclo.py` | 172 | Estado del ciclo de 105 días en `ciclo_estado.json` |
| `preve_validacion.py` | 366 | Scorecard (WMAPE), contrafactual y ROI |
| `preve_historial.py` | 369 | SQLite `preve_historial.db`: ejecuciones, recomendaciones, métricas y decisiones |
| `preve_simulacion.py` | 307 | Simulación *what-if* sobre la recomendación ya calculada |
| `preve_extractor.py` | 635 | Bot Playwright para Negosy |
| `preve_datos.py` | 389 | Capa de datos de la UI (configuración, KPIs, explicaciones) |
| `preve_app.py` | 864 | Ventana principal tkinter |
| `preve_vistas.py` | 1065 | Las 7 vistas |
| `preve_ui.py` | ~460 | Componentes gráficos |
| `preve_informe.py` | 637 | Informe Word y PDF |

Dependencias reales entre módulos: `app → vistas → datos → historial, ciclo`; `app → orquestador → pipeline, ciclo, validacion, historial, simulacion, informe`. El extractor **no está conectado a la app**: solo funciona por CLI.

Hoy **no existe ningún test**.

---

## 🔴 CRÍTICO — el resultado del motor es incorrecto

| # | Archivo | Bug | Origen |
|---|---|---|---|
| C1 | `preve_pipeline.py:163-178` | `cargar_datos()` concatena todos los Excel sin deduplicar. Si dos exportaciones se solapan en fechas, las ventas, compras y movimientos se cuentan dos veces. | [L-1.1] |
| C2 | `preve_extractor.py:417` | Todas las descargas se guardan en la misma carpeta (`./datos_negosy`) con el nombre `..._{YYYYMMDD}`. Las descargas de días distintos se acumulan y se solapan, lo que agrava C1. | [L-1.1] |
| C3 | `preve_pipeline.py:463` | El target de LightGBM es `shift(-1).rolling(21)`, que en la semana *t* suma las semanas *t-19 … t+1*. Casi todo el target es pasado y se solapa con las features (fuga de información). Debe ser `shift(-21).rolling(21)` = *t+1 … t+21*. Además, `min_periods=10` acepta targets parciales al final de la serie. | [L-1.2] |
| C4 | `preve_orquestador.py:411` | `_intentar_validacion()` usa la columna `ventas_diarias['unidades']`, pero esa tabla solo tiene `sku, fecha, mov` (con `mov` negativo). Lanza `KeyError` y el `except Exception` lo oculta, así que **el scorecard nunca se generó**. | [L-1.3] |
| C5 | `preve_orquestador.py:411` | Aunque C4 se arregle, suma las ventas de **todo el historial**, no solo las ventas desde la recomendación anterior. | [L-1.3] |
| C6 | `preve_orquestador.py:394-437` | **Horizonte desalineado en la validación.** El pronóstico anterior es la demanda de 141 días, pero se compara con las ventas de las ~14 días entre importaciones (por eso se sobrescribe `recomendacion_anterior.csv` en cada corrida). El WMAPE saldría ~90% siempre. Hay que prorratear el pronóstico a los días realmente transcurridos o validar solo cuando se completen los 141 días. | **[NUEVO]** |
| C7 | `preve_validacion.py:40-45, 193-199` | `metricas_sku` no incluye `precio_regular_pen`. Por eso `df.get(...)` devuelve `pd.Series(0)` de longitud 1, el precio neto queda en 0, el margen es negativo, `clip(0)` lo deja en 0 y **el ROI siempre sale S/ 0**. | [L-1.4] |
| C8 | `preve_pipeline.py:680-683` | `stock_en_transito = 0.0` está fijo en el código. Las órdenes ya emitidas se vuelven a recomendar. | [L-1.5] |
| C9 | `preve_pipeline.py:681-683` | El "ROP" se calcula como demanda **de 141 días** + SS. En realidad es el *nivel objetivo* (order-up-to). El ROP real es demanda **durante el lead time** + SS. Como resultado, `RECOMPRAR` se dispara casi siempre. | [L-1.5] |
| C10 | `preve_pipeline.py:285-286` | `reconstruir_stock()` resta las ventas **y también** los movimientos de "Salida". Si el ERP registra cada venta como salida de almacén, el stock histórico se descuenta dos veces. | [L-1.6] |
| C11 | `preve_pipeline.py:283-284` | Mismo problema con las entradas: las compras suman stock **y** los movimientos "Entrada" también. Si Negosy genera una entrada de almacén por cada compra, se duplican los ingresos. Necesita su propio parámetro (`compras_son_entradas_de_almacen`). | **[NUEVO]** |
| C12 | `preve_pipeline.py:159` | En `cargar_datos()` se aplica `drop_duplicates('CODIGO BARRAS')` **antes** de `construir_dim_producto()`. Por eso la lógica de sufijos por colisión nunca se ejecuta. | [L-1.9] |
| C13 | `preve_pipeline.py:188, 228` | Los códigos de barra numéricos se leen como `float` si la columna tiene vacíos (`7751234567890.0`). No coinciden con los de ventas y compras, y esas filas quedan "sin SKU". | [L-1.9] |
| C14 | `preve_extractor.py:187` | `_cerrar_modal_caja()` hace clic en el **primer** botón cuyo texto contenga `cerrar` o `no`. Eso incluye **"Cerrar caja"**. Rompe la regla de seguridad del propio módulo. | [L-1.8] |

## 🟠 ALTO

| # | Archivo | Bug | Origen |
|---|---|---|---|
| A1 | `preve_extractor.py:311-314` | En el polling de Celery falta precedencia en `a or b or c if cond else d`. Se evalúa como `(a or b or c) if cond else d` y la URL se pierde cuando `result` no es un dict. | [L-1.8] |
| A2 | `preve_historial.py:359-365` | `buscar_en_historial()` hace `GROUP BY sku` con columnas no agregadas. `nombre`, `accion` y `compra` salen de una fila arbitraria, no de la última. | [L-1.7] |
| A3 | `preve_historial.py:302-350` | `comparar_ultimas`, `ultimo_scorecard` y `ejecuciones_con_diff` no filtran por sucursal. Comparan San José con otra sucursal si comparten la carpeta. | [L-1.7] |
| A4 | `preve_orquestador.py:53-62` | `fecha_ancla` usa el `mtime` del archivo, que cambia al copiar, descomprimir o volver a guardar. El stock queda anclado a una fecha falsa. | [L-1.9] |
| A5 | `preve_pipeline.py:274` | Los movimientos se asocian por **nombre** de producto. Con nombres duplicados o renombrados, el movimiento cae en el SKU equivocado o se pierde. | [L-1.9] |
| A6 | `preve_orquestador.py:47` | `mapa_bc_a_sku = dict(zip(codigo_barras, sku))`. Cuando hay colisión (una vez arreglado C12), **todas** las ventas de ese código van al último sufijo. Hay que desambiguar ventas y compras usando también el nombre. | **[NUEVO]** |
| A7 | `preve_app.py:816-818` | Si `_al_terminar_ok()` lanza una excepción (por ejemplo, `kpis()` con NaN), `self.generando` queda en `True` y la app se bloquea: `ir()` no navega. | [L-1.9] |
| A8 | `preve_ciclo.py:53-57` | Un `ciclo_estado.json` corrupto devuelve `None`, y entonces `iniciar_ciclo` reinicia en el ciclo #1 y **pierde el historial del ciclo**. | [L-1.9] |
| A9 | `preve_datos.py:45-64` | `cargar_config` y `guardar_config` silencian todos los errores. Un JSON corrupto se sobrescribe con los valores por defecto y se pierden las carpetas. | [L-1.9] |
| A10 | `preve_orquestador.py:23` vs `preve_datos.py:24` vs `preve_extractor.py:39` | **Tres listas de sucursales distintas**: orquestador (San José, Ayaviri, Santa Lucía), UI (San José, Santa Barbara, Lima) y extractor (san_jose, santa_barbara, principal). | **[NUEVO]** |
| A11 | `preve_pipeline.py:56, 288-296` | `SAN_JOSE_ALMACENES` se aplica a **todas** las sucursales. Para cualquier sucursal que no sea San José, todos los traslados dan 0 y el stock se reconstruye mal. Además, la app nunca llama a `ejecutar_pipeline_multi_sucursal`. | **[NUEVO]** |
| A12 | `preve_orquestador.py:36-38` | `registrar_importacion()` se ejecuta **antes** de cargar los datos. Si el pipeline falla, o se repite el mismo día, la importación queda contada igual y la etiqueta PRELIMINAR/BORRADOR/FINAL avanza. | **[NUEVO]** |
| A13 | `preve_ciclo.py:102-105` | Al vencer el ciclo, el nuevo ciclo empieza el día de la importación y no en `inicio + 105`. Los ciclos se van desplazando con cada atraso. | **[NUEVO]** |
| A14 | `preve_orquestador.py:124-136` | El historial vuelve a leer `scorecard_ultimo.json` aunque la validación de esta corrida haya fallado. Registra un WMAPE viejo como si fuera nuevo. | **[NUEVO]** |
| A15 | `preve_pipeline.py:266` | Las fechas de compras usan solo `format='%d-%m-%Y'`, sin plan B (las ventas y los movimientos sí lo tienen). Si cambia el formato, **todas** las compras pasan a `NaT` y se descartan sin ningún aviso. | **[NUEVO]** |
| A16 | `preve_pipeline.py:461, 471` | Las features de LightGBM usan `shift(1)`, pero la predicción se hace con la última fila. **La semana más reciente nunca entra** en las features de predicción. | **[NUEVO]** (se corrige junto con C3) |

## 🟡 MEDIO

| # | Archivo | Bug | Origen |
|---|---|---|---|
| M1 | varios | Hay 30 `except Exception` genéricos (14 con `pass`). Ocultan errores reales: C4 es el caso más grave. | [L-1.9] |
| M2 | `preve_vistas.py`, `preve_informe.py`, `preve_simulacion.py` | `int(x)` sobre valores NaN lanza `ValueError` en la UI. Por ejemplo, `int(r['compra_sugerida_unid'])` en `vistas.py:248, 585, 715` e `informe.py:231, 303`. | [L-1.9] |
| M3 | `preve_simulacion.py:192` vs `preve_pipeline.py:613` vs `preve_informe.py:205` | NEGOCIAR usa tres reglas distintas: `0 < margen < 0.10`, `margen < -0.05` y el texto "<10%". | [L-5.2] |
| M4 | `preve_simulacion.py:181-202` | `_reclasificar_accion` es otra lógica distinta: VIGILAR es el *default* y no reconoce productos nuevos. Una simulación sin cambios ya altera las acciones. | **[NUEVO]** (se resuelve en Fase 5) |
| M5 | `preve_simulacion.py:89, 117` | `lt_orig = 36` está fijo en el código. El lead time solo escala el SS y no la demanda durante el lead time. La compra ignora el stock en tránsito. | [L-3.1] + **[NUEVO]** |
| M6 | `preve_pipeline.py:623-624` | El *default* "stock suficiente" se etiqueta **REDUCIR**. Mezcla productos sanos con sobre-stock. Debería ser MANTENER. | **[NUEVO]** (se resuelve en Fase 5) |
| M7 | `preve_pipeline.py:449, 517` vs `463` | Croston y CatBoost multiplican por 20.14 semanas (141 d), pero LightGBM predice 21 semanas. La demanda de LightGBM sale inflada ~4%. | **[NUEVO]** |
| M8 | `preve_pipeline.py:470, 513` | Los intervalos P10/P90 usan los residuos **in-sample**, que son demasiado estrechos. Esto subestima el SS y la "confianza". | **[NUEVO]** |
| M9 | `preve_datos.py:224` | `fecha_datos` = `mtime` del CSV, es decir, la hora de la corrida y no la fecha de los datos. Las fechas de quiebre que muestra la UI se desplazan. | **[NUEVO]** |
| M10 | `preve_validacion.py:146-149` | El "sobre-stock evitado" suma el stock sobrante de REDUCIR/LIQUIDAR. Eso no es capital *evitado*, es sobre-stock *existente*. Infla el beneficio. | **[NUEVO]** |
| M11 | `preve_orquestador.py:103-106` | El reporte LOCAL recalcula **todos** los productos, también los de proveedores internacionales. No distingue el tipo de proveedor. | **[NUEVO]** (se resuelve en Fase 2/3) |
| M12 | `preve_pipeline.py:403`, `preve_datos.py:329`, `preve_validacion.py:195` | IGV 1.18 fijo en el código. | [L-3.1] |
| M13 | `preve_pipeline.py:41-62`, `preve_ciclo.py:28-36`, `preve_datos.py:22`, `preve_orquestador.py:483, 548` | Horizonte, lead time, Z, umbrales y "105" fijos en el código y duplicados. | [L-3.1] |
| M14 | `preve_historial.py` | Las conexiones no se cierran si hay una excepción (no hay `try/finally` ni `with`). | **[NUEVO]** |

## 🟢 BAJO

| # | Archivo | Bug | Origen |
|---|---|---|---|
| B1 | `preve_pipeline.py:344` | Variable `semanas` sin usar. | [L-7.1] |
| B2 | `preve_simulacion.py:14` | `import copy` sin usar. | [L-7.1] |
| B3 | `preve_app.py`, `preve_vistas.py` | `entry`, `CARD_ALT`, `self._activo`, `OK_BG/WARN_BG/DANGER_BG`, `razon_abierta` y `VistaSimular.tecla` sin usar. | [L-7.1] |
| B4 | `preve_orquestador.py:16` | Se importan `HORIZONTE_LOCAL` y `LEAD_TIME_LOCAL` sin usarlos. | [L-7.1] |
| B5 | `preve_app.py:38-84` | Citas mal atribuidas: la de "adaptarse al cambio" no es de Darwin, y otras son dudosas. | [L-7.1] |
| B6 | `preve_vistas.py:1032` | El nombre por defecto `'Julio'` está fijo en el código. | [L-3.1] |
| B7 | `preve_extractor.py:24, 327-329` | El docstring usa `--tienda yujustore`, que es una opción inválida. `elapsed % 30 == 0` casi nunca se cumple. | **[NUEVO]** |
| B8 | `preve_validacion.py:260-262` | `if agregadas['wmape']` muestra "N/A" cuando el WMAPE es exactamente 0. | **[NUEVO]** |
| B9 | `preve_pipeline.py:35` | `warnings.filterwarnings('ignore')` global oculta las advertencias de pandas y de los modelos. | **[NUEVO]** |
| B10 | todo | `print` y callbacks en vez de `logging`. | [L-7.2] |
| B11 | `requirements.txt` | Versiones sin fijar. El entorno usa Python 3.14 y los scripts no lo verifican. | [L-7.5] |
| B12 | `preve_extractor.py` vs `preve_pipeline.py:115` | **Riesgo, no verificado:** el reporte `movements_warehouse_list_excel` del bot puede tener columnas distintas de `Reporte_productos_por_movimiento` (TIPO DE MOVIMIENTO, ORIGEN, DESTINO). Necesito un archivo real para confirmarlo. | **[NUEVO]** |

---

## Observaciones sobre el plan (antes de la Fase 1)

1. **1.2 · "21 semanas":** 141 días son 20.14 semanas. Propongo usar `round(horizonte_dias / 7)` y que la demanda de los 3 modelos se exprese en el mismo horizonte (M7).
2. **1.3 · validación:** arreglar la columna no basta (ver C6). Propongo comparar las ventas reales del período transcurrido con el pronóstico **prorrateado** a esos días (`p50 × días / 141`).
3. **1.6 · doble descuento:** propongo detectarlo además de forma automática. Si más del 80% de las ventas tiene una "Salida" con el mismo SKU, fecha y cantidad, se avisa en el log. El valor por defecto sigue siendo configurable.
4. **Datos reales:** para validar C10, C11, C13 y B12 necesito al menos un juego de reportes reales (en local, no se suben). Sin ellos, los tests usarán datos sintéticos.
5. **Alcance:** las fases 2 a 6 (SQLAlchemy, wizard, importador con *drag & drop*, editor visual y backtesting con UI) son grandes. Cada una va a necesitar varias iteraciones con tu confirmación.

## Totales

| Severidad | Listados | Nuevos | Total |
|---|---|---|---|
| Crítico | 12 | 2 | 14 |
| Alto | 7 | 9 | 16 |
| Medio | 4 | 10 | 14 |
| Bajo | 7 | 5 | 12 |
| **Total** | **30** | **26** | **56** |
