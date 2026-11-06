# Reporte final — PREVÉ: de prototipo a producto (Fases 0–7; Fases 8–10 en la sección 7)

Período: 2026-11-05 · Commits: `03495e9` (fase 0) → `fase 7` · Repositorio: https://github.com/Elturbius12/PREV-

## 1. Resumen

PREVÉ pasó de ser un prototipo con resultados incorrectos (fuga de información en LightGBM, scorecard que nunca corría, ROI siempre en S/ 0, sin stock en tránsito, colisiones de SKU que desaparecían) a una aplicación:
- **parametrizable:** sin valores de negocio fijos, con asistente inicial;
- **con base de datos versionada** y preparada para PostgreSQL;
- **con importación de cualquier ERP** y Negosy como plugin;
- **con reglas de acción editables**, sin `eval`;
- **con backtesting walk-forward**;
- **con 218 tests** (182 al cerrar la Fase 7; las Fases 8–10 sumaron el resto).

## 2. Bugs corregidos y cómo se verificaron

`AUDITORIA.md` listó **56 bugs** (30 de la lista original y 26 nuevos). Durante el trabajo aparecieron **3 más**: dos se reportaron y aprobaron, y uno lo introduje yo y lo corregí.

| Bug | Corrección | Verificación |
|---|---|---|
| C1/C2 Duplicación por exportaciones solapadas | Deduplicación entre archivos (conserva las líneas iguales de un mismo ticket) y subcarpeta por descarga | `test_1_1_*`; datos reales: 3 líneas duplicadas descartadas |
| C3/A16 Target de LightGBM con fuga | Suma de t+1…t+H sin solape; las features incluyen la semana actual | `test_1_2_*`; real: demanda LightGBM −30% |
| C4/C5/C6 Scorecard roto, con todo el historial y horizonte desalineado | Columna `mov`, solo el período nuevo y pronóstico prorrateado | `test_1_3_*` (WMAPE 0 con pronóstico perfecto) |
| C7 ROI siempre S/ 0 | Precio incluido y arrays alineados | `test_1_4_roi_positivo…` |
| C8/C9 Sin tránsito; ROP = nivel objetivo | `ordenes_compra_abiertas.csv`; ROP, nivel objetivo y cantidad a pedir separados | `test_1_5_*` |
| C10/C11 Doble descuento por ventas y salidas (y compras y entradas) | Parámetros configurables y detección automática | `test_1_6_*`, `test_c11_*`; real: salidas ≈1% de las ventas, sin doble descuento |
| C12/C13/A6 Colisiones de código de barras y códigos como float | Sufijos antes de deduplicar, código como texto y desambiguación por nombre | `test_1_9_*`, `test_a6_*`; real: **63 productos recuperados** |
| C14/A1 Extractor podía pulsar "Cerrar caja"; precedencia en Celery | Lista blanca exacta y paréntesis | `test_1_8_*` (13 casos) |
| A2/A3 Búsqueda arbitraria y sin filtro de sucursal | `ROW_NUMBER()` y filtro por sucursal | `test_1_7_*` |
| A4 Fecha ancla por mtime | Fecha del nombre del listado o última transacción | `test_1_9_fecha_ancla_*`; real: 30-oct → **23-oct** |
| A7/A8/A9 App bloqueada; ciclo y configuración corruptos se reiniciaban | `try/finally`, `CicloCorruptoError` y respaldo `.corrupto-*` | `test_1_9_*` |
| A10/A11 Tres listas de sucursales; almacenes de San José para todas | Una lista configurable con almacenes por sucursal | `test_traslados_segun_almacenes_*` |
| A12/A13/A14/A15 Ciclo y validación: importación contada aunque falle, ciclos desplazados, scorecard viejo, fechas de compra perdidas | Vista previa del ciclo, anclaje inicio + k·duración, sin JSON viejo y formato de fecha de respaldo | `test_a12_*`, `test_a13_*`, `test_a14_*`, `test_a15_*` |
| M1 `except Exception: pass` | Reemplazados por excepciones específicas con log | Revisión con grep: quedan solo los broad intencionales, que registran el error |
| M2 `int(NaN)` en la UI | `safe_int` | `test_1_9_safe_int` |
| M3/M4/M6 NEGOCIAR inconsistente, simulación con otra lógica, default REDUCIR | Plantillas de reglas compartidas | `test_fase5.py` (RETAIL_CLASICO = lógica anterior en 2.067 productos reales y 3.000 aleatorios) |
| M5/M12/M13/B6 Valores fijos (36, 141, 105, 1.18, Z, 'Julio'…) | `Config` y `ciclos_compra` | `test_fase3.py` |
| **Nuevo:** simulación con `rop − stock` tras la Fase 1 (lo introduje yo) | Las mismas fórmulas que el pipeline | `test_simulacion_sin_cambios_reproduce…` |
| **Nuevo:** sucursal exportada como tienda ("Tienda Juliaca") en la importación | Se acepta nombre o ubicación | `test_sucursal_por_nombre_de_tienda…`; real: 99,95% importado |
| **Nuevo:** stock histórico NaN (pivot) | `fillna(0)` | `test_7_0_*`; real: stock negativo histórico **11 → 776 SKU** (antes no se veía) |
| B1–B4 Código muerto | Eliminado (más 12 restos detectados con pyflakes) | pyflakes limpio, salvo las reexportaciones del alias |
| B5 Citas mal atribuidas | 7 frases eliminadas | — |
| B10 `print` | `logging` con rotación (`preve.log`) | `test_logging_a_archivo_con_rotacion` |

**Abiertos de la auditoría:**
- M8 (intervalos P10/P90 *in-sample*) y M10 (definición del "sobre-stock evitado" en el ROI).
- B7, B8 y B9: menores.
- B12: el reporte de movimientos del bot de Negosy sigue sin verificarse contra uno real.

## 3. Métricas antes / después

| | Antes (`8493117`) | Después |
|---|---|---|
| Código Python (sin tests) | 6.687 líneas en 12 archivos | 11.721 líneas en 28 archivos |
| Tests | 0 | **182** al cierre de la Fase 7 (116 funciones, 7 archivos); **218** hoy |
| Cobertura de los módulos críticos (medida al cierre de la Fase 7) | 0% | **93%**: pipeline 90%, database 93%, backtesting 92%, validación 97%, reglas 92%, importador 97%, config 92% |
| Valores de negocio fijos | ~20 (141, 36, 105, 30, 10, 40, 1.18 ×4, Z, ADI, CV², 13 semanas, 3 sets de hiperparámetros, 3 listas de sucursales, almacenes de San José, 'Julio', 'Yuju Store' ×6, `lt_orig`, 3 umbrales de NEGOCIAR) | **0** (todos en la base, con valores por defecto documentados) |
| `except Exception: pass` | 14 | 0 |
| SQL fuera de la capa de datos | `preve_historial.py` completo | 0 |
| Productos reconocidos (San José) | 2.004 | 2.067 |

**Impacto en las recomendaciones reales (San José, internacional):** S/ 675.023 con el prototipo → S/ 753.747 con la Fase 1 (bugs corregidos y lógica clásica) → **S/ 461.544** con la plantilla RETAIL (Fase 5), que solo recompra lo que no llega al lead time.

**Primer backtesting real** (6 meses, 12 ventanas de 14 días): WMAPE de LightGBM 66%, CatBoost 87% (subestima en 6 u por ventana) y Croston-TSB 133%.

## 4. Hallazgos sobre los datos (requieren acción del negocio)

1. **El listado de productos está filtrado** (`…filtrado.xlsx`, 2.067 productos; el 30-oct había uno de 4.248). Por eso el 42% de las líneas de venta, el 43% de las compras y el 42% de los movimientos no encuentran su producto. **Exportar el listado completo** es la mejora más grande y más barata.
2. **Stock reconstruido negativo en 776 productos** en algún momento de su historia. Faltan entradas: compras y traslados de productos que no están en el listado, o traslados desde Lima sin registrar.
3. **La simulación de compra del 03-ago-2026 sugiere unas 4 veces lo necesario** (266.925 u sugeridas contra ~62.600 reales estimadas) y aun así 166 productos tienen quiebre. Lo explican el stock histórico subestimado (punto 2), un stock de seguridad grande sobre un pronóstico ruidoso y el hecho de que el horizonte todavía esté incompleto (81 de 141 días). Hay que repetirla con el listado completo antes de usarla para ajustar parámetros.
4. **Croston-TSB** pronostica la mitad del catálogo con el peor error. Ver la deuda técnica, puntos 1 y 2.

## 5. Deuda técnica pendiente

1. **Calidad del pronóstico:** intervalos P10/P90 con residuos *in-sample* (M8); CatBoost con sesgo negativo; probar ADIDA/IMAPA o un modelo global para las series intermitentes.
2. **Backtesting:** re-entrena los 3 modelos en cada ventana (~40 s por ventana). Se puede cachear la segmentación y paralelizar Croston (`joblib`).
3. **El pipeline todavía lee los Excel** en cada análisis. Las ventas ya están en `ventas_diarias`, pero leer desde la base requiere también el stock y los movimientos históricos.
4. **Columnas `*_141d`:** el nombre es histórico aunque el horizonte sea configurable. Renombrarlas requiere migrar los CSV ya generados.
5. **Docstrings en la interfaz:** quedan ~100 funciones de tkinter sin docstring (callbacks y overrides).
6. **Lectura de Excel lenta** (~65 s para 119 mil filas con openpyxl). Se puede evaluar `python-calamine`.
7. **Tests de la interfaz:** solo hay pruebas de humo manuales con Tk oculto; no hay tests automáticos de la GUI.
8. **Plantilla FARMACIA:** pendiente de datos de vencimiento por lote.
9. **Multi-sucursal real:** traslados sugeridos entre sucursales (TRASLADAR) y planificar el almacén compartido como nodo propio (hoy se suma a San José, por decisión del negocio).
10. **Dependencias sin versión fija** en `requirements.txt`. Conviene fijarlas (`pip freeze`) antes de distribuir.
11. **`INSTALAR.bat` no se probó de punta a punta** en una PC limpia. Solo se verificaron la sintaxis y el chequeo de versión.

## 6. Próximos pasos para un servicio en la nube multi-tenant

1. **Base:** PostgreSQL gestionado (`PREVE_DB_URL`). Ya existe `empresa_id` en catálogo, sucursales, ciclos y categorías; faltaría agregarlo a `configuracion`, `perfiles_importacion` y `plantillas_categorias` (propias), y aplicar *Row-Level Security* por empresa.
2. **Migraciones:** pasar `MIGRACIONES` a Alembic, manteniendo la numeración.
3. **API:** exponer el orquestador como servicio (FastAPI): `POST /importaciones`, `POST /analisis` (asíncrono, con cola tipo Celery o RQ) y `GET /recomendaciones`. La UI de tkinter pasa a ser un cliente más, o se reemplaza por una web.
4. **Trabajos pesados:** entrenamiento y backtesting en *workers*. Los resultados ya se persisten en tablas, así que el front solo consulta.
5. **Archivos:** reportes subidos y Excel generados en almacenamiento de objetos (S3 o compatible), con URLs firmadas.
6. **Plugins por tenant:** las credenciales del ERP en un gestor de secretos; hoy están en `.env` o, sin cifrar, en la tabla `configuracion` (Fase 10). El extractor Playwright corre en un worker aislado.
7. **Autenticación y roles:** usuarios por empresa; el campo `decisiones_compra.usuario` ya existe.
8. **Observabilidad:** `logging` ya está centralizado; enviarlo a un colector y agregar métricas de duración por paso, que hoy se guardan en `pasos_s`.

## 7. Después de este reporte: Fases 8–10

- **Fase 8 — Depurar datos** (`preve_depurar.py`): borra la base de datos de datos y el historial, los reportes generados (las subcarpetas de período), los logs y el estado del ciclo para empezar de cero. Conserva la configuración (empresa, sucursales, ciclos, plantillas, parámetros). Siempre arma antes un ZIP de respaldo en `respaldos_depurar/`.
- **Fase 9 — Resultados por período:** cada análisis se guarda en `salida/AAAA-MM-DD_a_AAAA-MM-DD/` (la app lee siempre el más reciente). `ordenes_compra_abiertas.csv` y el estado del ciclo no cambian de lugar. La validación (scorecard) lee la recomendación del período anterior directamente. «Depurar datos» ahora borra solo las subcarpetas de período. Botón «Abrir carpeta de resultados».
- **Fase 10 — Negosy configurable e incremental:** URL de login, URL base por sucursal, texto de la tarjeta, usuario y contraseña se editan en Configuración (el `.env` sigue valiendo, con menor prioridad). La primera descarga trae 730 días; las siguientes, desde el día siguiente al último período importado **hasta ayer**, en `entrada/AAAA-MM-DD_a_AAAA-MM-DD/`, con marcador de descarga completa. El catálogo se descarga siempre por defecto porque de él sale el stock.

Pendiente de esta etapa: la descarga real contra Negosy con los ajustes nuevos solo se probó con un extractor simulado.
