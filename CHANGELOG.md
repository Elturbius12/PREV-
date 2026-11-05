# Changelog

Todos los cambios relevantes de PREVÉ, organizados por fase.

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
