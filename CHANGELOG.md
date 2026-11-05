# Changelog

Todos los cambios relevantes de PREVÉ, organizados por fase.

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
