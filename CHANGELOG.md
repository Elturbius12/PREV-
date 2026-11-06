# Changelog

Todos los cambios relevantes de PREVÉ, organizados por fase.

## [Resultados por período] — 2026-11-06

### Cambiado
- **Cada análisis guarda sus resultados en su propia subcarpeta** dentro de la carpeta de salida: `salida/AAAA-MM-DD_a_AAAA-MM-DD/`. El período termina en la fecha de los datos importados y dura `dias_entre_importaciones` días (si no se puede determinar la fecha, se usa la de hoy). Ahí quedan `recomendacion_compra*.csv`, `skus_sin_pronostico.csv`, los `PREVE_*.xlsx`/`.csv`, el scorecard, el informe (Word/PDF) y `dashboard.html`. Repetir el análisis sobre los mismos datos reutiliza la misma carpeta.
- **Lo que no es resultado se queda en la raíz de salida:** `ordenes_compra_abiertas.csv` (lo editas a mano) y el respaldo `ciclo_estado.json`. El estado del ciclo sigue en la base de datos.
- **La app lee siempre el período más reciente** que tenga recomendación (un análisis que falló a medias no oculta el anterior): recomendaciones, informe, simulación, traslados.
- **La validación (scorecard) lee directo del período anterior.** Ya no se escriben `recomendacion_anterior.csv` ni `recomendacion_anterior_meta.json`; si existen de versiones antiguas y no hay período anterior, se usan una vez como referencia.
- **Depurar datos** ahora también borra las subcarpetas de período de las carpetas de salida de las sucursales (y las de `salida/<Sucursal>/` en modo multi-sucursal), con respaldo en el ZIP. No toca ningún otro archivo ni carpeta. `preview_depuracion()` agrega la clave `periodos`.

### Migración
- Los archivos sueltos que ya tenías en la raíz de salida **no se mueven ni se borran**: quedan huérfanos y puedes borrarlos a mano. A partir de esta versión todo análisis nuevo crea su subcarpeta.

### Tests
- `tests/test_periodos.py` (períodos, `ultimo_periodo`, depuración y pipeline completo) y ajustes a los tests de validación y migración.

## [Fase 7] — 2026-11-05 · Limpieza, logging, tests y documentación

### Corregido
- **Stock histórico NaN** en `reconstruir_stock` (bug reportado en la Fase 6): `fillna(0)` tras `pivot()`. Con los datos reales aparecen 776 SKU con stock negativo en algún día (antes se informaban 11, porque los NaN no contaban). La Simulación Compras del backtesting ya es válida.

### Cambiado
- **7.1 Código muerto eliminado:** `semanas`, `import copy`, `entry`/`CARD_ALT`/`self._activo` de la app, `OK_BG`/`WARN_BG`/`DANGER_BG`, `razon_abierta` y `VistaSimular.tecla`, más 12 restos que encontró pyflakes. 7 frases de carga con atribución dudosa eliminadas (incluida la falsa de Darwin) y una estadística sin fuente.
- **7.2 Logging:** `configurar_logging()` escribe en consola y en `preve.log` (rotación 5 MB × 3). Los 20 `log=print` por defecto pasan a `log_info` (logger `preve`).
- **7.4 Documentación:** README reescrito, `ARQUITECTURA.md` (capas, flujo del análisis y del backtesting, esquema), `CONTRIBUTING.md` (plugins, plantillas, migraciones) y docstrings en 107 funciones públicas del motor.
- **7.5 Instaladores:** `INSTALAR.bat` e `instalar.sh` verifican Python 3.11+ (y tkinter en Linux), crean `.venv`, instalan dependencias, migran la base (idempotente) e inician la app. Los scripts anteriores quedan como alias. `.gitattributes` fuerza LF en los `.sh`.

### Tests
- `tests/test_fase7.py`: stock sin NaN, conteo de negativos, carga de Excel del ERP con solapes, pipeline de punta a punta con los 3 modelos, clasificación clásica y logging.
- Cobertura: pipeline 68% → **90%**; módulos críticos en conjunto **93%**. Total: **182 tests**.

Ver `REPORTE_FINAL.md`.

## [Fase 6] — 2026-11-05 · Modo backtesting con walk-forward validation

### Agregado
- `preve_backtesting.py`.
  - **`DatosBacktest`** lee los reportes con las mismas reglas que el pipeline.
  - **`Backtester.run(inicio, fin)`** hace la validación *walk-forward*: en cada ventana (alineada al lunes, de `dias_entre_importaciones` días) entrena **solo con ventas anteriores** y compara contra lo vendido.
  - **Métricas:** MAPE con sMAPE cuando la venta real es 0, WMAPE, MAE, RMSE y sesgo, por producto, ventana y modelo.
  - **Datos insuficientes (6.2):** ventana sin datos globales, solo compras, producto sin primera venta (se omite) y demanda intermitente (se evalúa).
  - **Día de reporte final del ciclo:** sugerencia de compra "a medianoche" validada contra las ventas reales del horizonte. Si el horizonte todavía no terminó, la venta se extrapola y se marca como parcial.
  - **Cancelación** entre ventanas.
- Todo queda en la base (6.5): `metricas_periodo` (por producto y por modelo), `pronosticos` con `venta_real` y `ejecuciones_backtesting`.
- Excel `reportes_backtesting/backtesting_YYYY-MM-DD_HH-MM.xlsx`: Resumen General (métricas, evolución, por modelo, top 20 peor y mejor), Resumen por Modelo, una hoja por período (Código | Nombre | Modelo | Clasif | Pronóstico | Real | Error Abs | Error % | Bias), Simulación Compras y Ventanas omitidas.
- Interfaz: botón **naranja "Modo Backtesting"** en la barra superior. Oculta Inicio (dashboard), Recomendaciones, Decisión rápida y Simular (y con ellos el botón de informes), y muestra la vista Backtesting con rango (por defecto 6 meses), ventana, progreso y log en tiempo real, cancelación, resumen, botón para abrir el Excel y ejecuciones anteriores. Al desactivarlo todo queda guardado.
- RETAIL v2: regla **REVISAR_COMPRA** (stock bajo el lead time sin margen positivo), que cierra el hueco de la Fase 5. No cuenta como compra automática. Las plantillas del sistema ahora tienen versión y se actualizan solas.
- `tests/test_fase6.py` (12 tests, con un pronosticador falso que verifica la ausencia de fuga de información).

### Primer backtesting real (San José, 22-abr → 22-oct-2026, ventanas de 14 días, 8 min)
| Modelo | Productos | MAPE | WMAPE | Sesgo (u/ventana) |
|---|---|---|---|---|
| LightGBM | 221 | 142% | 66% | +1.4 |
| CatBoost | 1.132 | 129% | 87% | −6.0 (subestima) |
| Croston-TSB | 1.075 | 196% | 133% | +1.7 |
| **Total** | 1.665 | 171% | ~98% | |

El MAPE está dominado por la demanda intermitente: el sMAPE vale 200% cuando hay pronóstico y la venta fue 0. El WMAPE es más representativo.

### Bug encontrado (no estaba en la auditoría; reportado y pendiente de aprobación)
- **`reconstruir_stock` deja NaN el stock histórico** de los productos con días sin movimiento: `pivot()` crea NaN y `reindex(fill_value=0)` no los rellena. Al 02-ago-2026 solo 26 de 2.067 productos tienen stock numérico. No afecta la recomendación (usa el stock del día del listado). Sí afecta el conteo de stock negativo histórico y la **Simulación Compras** del backtesting, que queda inválida hasta corregirlo.

## [Fase 5] — 2026-11-05 · Plantillas de categorías + editor visual

### Agregado
- `preve_reglas.py`: motor de reglas **sin `eval`**. Las reglas son listas cerradas de variables (14), operadores (6) y números, o "otra variable × factor"; gana la primera regla que se cumple. También incluye validación con mensajes claros, motivos con los valores reales y la vista previa de conteos por acción.
- `seeds/plantillas_categorias.py`: plantillas RETAIL (predeterminada, según la especificación), RETAIL_CLASICO (la lógica anterior: coincide en los 2.067 productos reales y en 3.000 casos aleatorios), MANUFACTURA y DISTRIBUCION. Se siembran al iniciar sin pisar las editadas. FARMACIA queda pendiente (requiere vencimientos por lote).
- Variables nuevas en la recomendación: `venta_sem_reciente` (promedio de las últimas 4 semanas), `dias_sin_venta` y `regla_aplicada`.
- `preve_editor_plantillas.py`: editor visual (acción, descripción, condiciones con listas desplegables, orden ↑↓, acción por defecto y acciones de compra) con vista previa sobre los datos actuales y diferencia frente a lo guardado. Las plantillas del sistema se guardan como copia.
- Tarjeta "Categorías de acción" en Configuración: plantillas con su descripción, reglas, conteos con tus datos y botón para activarlas.
- `tests/test_fase5.py` (33 tests).

### Cambiado
- Cada plantilla define sus `acciones_de_compra`. El pipeline marca `decision = COMPRAR` y la interfaz, el historial, el Excel, las notas de compra, el dashboard y el informe usan esa columna en vez de `accion == 'RECOMPRAR'`.
- **5.2 NEGOCIAR unificado** en margen < 10%: plantillas, simulación (antes < −5%) e informe (el texto sale de la regla).
- La simulación reclasifica con la misma plantilla que el pipeline.

### Corregido
- **Bug introducido en la Fase 1:** la simulación calculaba la compra como `rop − stock`, pero desde la Fase 1 `rop` es el punto de reorden y no el nivel objetivo, así que un escenario sin cambios no reproducía la recomendación. Ahora usa nivel objetivo − stock − tránsito (test de regresión incluido).

### Impacto medido con datos reales (RETAIL frente a RETAIL_CLASICO)
| | Clásico | RETAIL |
|---|---|---|
| RECOMPRAR (internacional) | 651 | 530 |
| Inversión internacional | S/ 753.747 | S/ 461.544 (−39%) |
| DESCONTINUAR (sin venta ≥ 180 d) | — | 353 |
| MANTENER | — | 727 |
| Inversión local | S/ 115.606 | S/ 46.514 |

**Hueco detectado en las reglas RETAIL:** 59 productos con stock menor al lead time no cumplen ninguna regla (por ejemplo, margen ≤ 0 sin ser clase A) y caen en MANTENER por defecto.

## [Fase 4] — 2026-11-05 · Importación flexible + Negosy como plugin

### Agregado
- `preve_importador.py`: importación de ventas desde cualquier sistema.
  - Formatos xlsx, xls, csv y tsv. Detecta encabezado, separador y codificación (UTF-8 o Latin-1) y conserva los ceros a la izquierda de los códigos.
  - Sugiere el mapeo por sinónimos y detecta el formato de fecha (día/mes, mes/día, ISO, AM/PM; desempata por días > 12).
  - Lee números en formato regional (`1.234,5`, `S/ 3.50`).
  - Valida fecha, cantidad, producto existente (por código de barras o SKU) y sucursal (por nombre **o** por cualquiera de sus tiendas o almacenes).
  - Genera un reporte Excel con resumen y errores por fila. Las filas válidas se guardan en `ventas_diarias`.
- `preve_importador_ui.py`: ventana con **arrastrar y soltar** columnas a campos (o clic + clic), perfiles guardables, formato de fecha, sucursal por defecto y vista previa de las filas convertidas con el conteo de errores.
- Perfiles de importación en la base (tabla `perfiles_importacion`, migración 002).
- `plugins/`: clase abstracta `PluginERP` (`plugins/base.py`) y registro dinámico (`@registrar` + descubrimiento de subpaquetes). Si un plugin no carga, la app sigue funcionando.
- `plugins/negosy/`: `NegosyPlugin` con el extractor. *Corrección:* como `preve_extractor.py` sigue existiendo como alias, git lo registra como archivo nuevo; el historial anterior se consulta en `preve_extractor.py`. Las credenciales salen del `.env` y cada descarga queda en su subcarpeta con fecha y hora.
- Interfaz: tarjeta "Otras fuentes de datos" en Generar (importar archivo y, si hay plugin, "Descargar desde Negosy"). Tarjeta "Integración con tu ERP" en Configuración (por defecto ninguna: solo importación manual).
- `tests/test_fase4.py` (23 tests).

### Cambiado
- `preve_extractor.py` queda como alias de `plugins/negosy/extractor.py`. Siguen funcionando `python preve_extractor.py` y `from preve_extractor import ...`.

### Verificado con datos reales
`reporte_ventas_producto_2026.xlsx` (119.039 filas): se importaron 118.983 (99,95%). Las 56 con error tienen 51 productos fuera del catálogo y 5 líneas sin código, sin fecha o sin cantidad válida. Tiempo: ~70 s, la mayor parte en leer el Excel.

## [Fase 3] — 2026-11-05 · Configuración parametrizable y asistente inicial

### Agregado
- `preve_config.py`: clase `Config` con caché en memoria, invalidación al guardar y validación de tipos. Los valores por defecto (`DEFAULTS`, `CICLOS_DEFECTO`) son exactamente los que el código tenía fijos, así que los resultados no cambian mientras no se editen. `ParametrosModelo` agrupa lo que usa el pipeline.
- `preve_wizard.py`: asistente de 6 pantallas (empresa, sucursales, N ciclos de compra, frecuencia, mapeo de columnas de ventas con lectura de un archivo de ejemplo, y plantilla de categorías). Se abre si `wizard_completado` es falso y viene precargado con la configuración actual. La lógica (`estado_inicial`, `validar_paso`, `guardar_asistente`) se testea sin interfaz.
- Panel de Configuración: resumen de empresa, sucursales y ciclos (se editan con el asistente) y edición de los parámetros del modelo (Z por clase, ADI/CV², semanas de lanzamiento, frecuencia, doble descuento e hiperparámetros JSON), con guardado todo-o-nada.
- `aplicar_mapeo_ventas()`: el Excel de ventas puede tener otros nombres de columna.
- `tests/test_fase3.py` (19 tests).

### Cambiado
- Sin valores fijos: horizonte, lead time y duración salen de `ciclos_compra`; sucursales y almacenes de `sucursales` + `configuracion`; IGV y nombre de empresa de `empresas`; Z, umbrales ADI/CV², semanas de lanzamiento e hiperparámetros de `configuracion`. Afecta a `pipeline`, `orquestador`, `ciclo`, `simulacion` (`lt_orig`, horizonte, Z), `validacion` (IGV), `datos`, `informe`, `vistas` y `app` (título, logo, nombre por defecto).
- **N ciclos de compra:** el de mayor horizonte es el principal (reporte completo y etiquetas). Cada ciclo adicional genera sus notas de compra; "Local" conserva sus nombres de archivo.
- **Sucursales unificadas (A10, A11):** una sola lista (San José, Santa Barbara, Lima). Cada sucursal neutraliza los traslados entre sus propios almacenes, comparados sin mayúsculas ni tildes. San José = Tienda Juliaca + Almacén Juliaca (el almacén compartido se suma a San José), Santa Barbara = SANTA BARBARA, Lima = YUJU STORE PRINCIPAL.
- `preve_config.json` y `ciclo_estado.json` se importan a la base una sola vez. El estado del ciclo es por sucursal y las etiquetas PRELIMINAR/BORRADOR/FINAL escalan con la duración del ciclo.
- **Stock de seguridad del ROP** escalado a √(lead time / horizonte) (`stock_seguridad_lead_time`). El nivel objetivo y la cantidad a pedir no cambian.

## [Fase 2] — 2026-11-05 · Migración a SQLite con capa de abstracción

### Agregado
- `database.py`: clase `Database` sobre SQLAlchemy Core y **único lugar con SQL** del proyecto. Pasar a PostgreSQL es cambiar `PREVE_DB_URL`. Los upserts se resuelven por dialecto (SQLite o PostgreSQL).
- Esquema versionado (`schema_version` y `MIGRACIONES`, migración 001) con las 15 tablas del plan, más `ejecuciones`, `recomendaciones` y `metricas_ejecucion` del historial. `decisiones_compra` agrega `ciclo_numero`, `razon` y `hasta`, que ya usaba la app. `ordenes_compra_abiertas` agrega un `sucursal_id` opcional.
- `scripts/migrar_a_sqlite.py`: importa `preve_config.json`, `ciclo_estado.json`, `preve_historial.db`, los CSV de recomendación y de órdenes abiertas y, con `--con-ventas`, las ventas diarias de los Excel. Compara los conteos de origen y destino. Es idempotente y no modifica los archivos de origen.
- El pipeline guarda en `preve.db` el catálogo, las ventas diarias, el stock actual y los pronósticos de cada corrida (base para el backtesting de la Fase 6).
- `tests/test_database.py` (19 tests). Cada test usa su propia base temporal.

### Cambiado
- `preve_historial.py` es ahora una fachada sobre `Database` (misma API, sin SQL propio). La base es una sola, `preve.db` en la carpeta de la app. Si una carpeta de resultados tiene un `preve_historial.db` antiguo, se importa automáticamente la primera vez.
- `requirements.txt`: `sqlalchemy>=2.0`.

### Migración verificada (copia de DATA SJ)
Todos los conteos coinciden: 2 ejecuciones, 6.252 recomendaciones, 2.004 pronósticos y stocks, 54.891 ventas diarias (233.409 u), 9 claves de configuración y 2 ciclos de compra. La carpeta original quedó intacta (verificado con checksum).

## [Fase 1] — 2026-11-05 · Corrección de bugs críticos del motor

Cada corrección lleva un comentario `# FIX:` en el código y un test de regresión en `tests/test_fase1.py` (51 tests).

### Corregido
- **1.1 (C1, C2)** `deduplicar_entre_archivos()`: las exportaciones solapadas ya no duplican transacciones. Se conserva el máximo de ocurrencias entre archivos, así que dos líneas iguales dentro de un mismo ticket se mantienen. El extractor guarda cada descarga en `<destino>/YYYY-MM-DD_HH-MM-SS/`.
- **1.2 (C3, A16, M7)** Target de LightGBM = suma de t+1…t+20 sin solape (`rolling(h).sum().shift(-h)`). Las features incluyen la semana actual. El target se reescala a 20.14 semanas, igual que Croston y CatBoost.
- **1.3 (C4, C5, C6, A14)** El scorecard usa `-mov`, cuenta solo las ventas posteriores a la recomendación anterior (fecha guardada en `recomendacion_anterior_meta.json`) y prorratea el pronóstico a los días transcurridos. Tiene excepciones específicas con log y ya no reutiliza un scorecard viejo.
- **1.4 (C7)** ROI: `precio_regular_pen` en las métricas y `np.where` sobre arrays alineados.
- **1.5 (C8, C9)** Stock en tránsito desde `ordenes_compra_abiertas.csv` (se crea vacío). `rop` = demanda en el lead time + SS; `nivel_objetivo` = demanda del horizonte + SS; `cantidad_a_pedir` = max(0, nivel − stock − tránsito). Se mantiene el alias `compra_sugerida_unid`.
- **1.6 (C10, C11)** Parámetros `ventas_son_salidas_de_almacen` y `compras_son_entradas_de_almacen` en `preve_config.json` (por defecto `False`, verificado con los datos reales), más una advertencia automática si las salidas o entradas superan el 80% de las ventas o compras.
- **1.7 (A2, A3)** `buscar_en_historial()` con `ROW_NUMBER()`. Filtro por sucursal en `comparar_ultimas`, `ultimo_scorecard` y `ejecuciones_con_diff`.
- **1.8 (C14, A1)** El modal de caja usa una lista blanca exacta de botones y nunca compara por *substring*. Se corrigió la precedencia en la URL de Celery (`extraer_download_url`).
- **1.9** Nuevo módulo `preve_utils.py` (`logging`, `safe_int`).
  - Códigos de barra como texto desde la lectura (C13).
  - Sufijos de colisión antes de deduplicar (C12) y ventas y compras con colisión asignadas por nombre (A6).
  - Movimientos por código de barras cuando el reporte lo trae (A5).
  - `fecha_ancla` desde el nombre del listado o la última transacción (A4).
  - Fechas con formato de respaldo y aviso (A15).
  - `try/finally` en `_poll` (A7).
  - Configuración con respaldo `.corrupto-*` y escritura atómica (A9).
  - Ciclo corrupto lanza `CicloCorruptoError` y no se reinicia (A8).
  - Todos los `except Exception: pass` reemplazados (M1).
- **A12, A13** La importación se confirma en el ciclo solo al terminar bien, el mismo día cuenta una sola vez y los ciclos vencidos ya no se desplazan.

### Verificado con datos reales (San José, 2025–2026)
| | Antes | Después |
|---|---|---|
| Productos (SKU) | 2,004 | 2,067 (63 colisiones recuperadas) |
| Fecha ancla | 2026-10-30 (mtime) | 2026-10-23 (fecha del listado) |
| Demanda LightGBM (141 d) | 18,758 u | 13,162 u (−30%, ya sin fuga de información) |
| Inversión RECOMPRAR (internacional) | S/ 675,023 | S/ 753,747 |

## [Fase 0] — 2026-11-05 · Auditoría y preparación

### Agregado
- `AUDITORIA.md`: 56 bugs clasificados por severidad (30 ya listados y 26 nuevos).
- Carpeta `tests/` con la configuración de pytest (`pytest.ini`, `conftest.py`) y tests de humo de importación.
- `requirements-dev.txt` con `pytest` y `pytest-cov`.
- `CHANGELOG.md`.

### Cambiado
- `.gitignore`: ahora excluye `ciclo_estado.json`, `*.db-journal`, `*.db-wal`, `*.db-shm`, `datos_reales/`, `reportes_generados/`, `reportes_backtesting/` y los artefactos de cobertura.

## [Versión inicial] — 2026-11-04
- Primera publicación del prototipo (fases 1 a 4 del proyecto original).
