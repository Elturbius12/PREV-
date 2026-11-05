# Arquitectura de PREVÉ

## Capas

```
┌──────────────────────────── Interfaz (tkinter) ───────────────────────────────┐
│ preve_app ── preve_vistas ── preve_ui                                          │
│   ├─ preve_wizard (asistente)        ├─ preve_importador_ui (importar ventas)  │
│   ├─ preve_editor_plantillas (reglas) └─ preve_vista_backtesting               │
│   └─ preve_datos (KPIs, explicaciones, decisiones)                             │
└───────────────┬───────────────────────────────────────────────┬────────────────┘
                │                                               │
┌───────────────▼──────────── Orquestación ─────────────────────▼────────────────┐
│ preve_orquestador ── ejecutar_pipeline / multi_sucursal / simulación           │
│ preve_backtesting ── walk-forward + simulación del día de reporte              │
└───────┬───────────────┬──────────────┬───────────────┬────────────────┬────────┘
        │               │              │               │                │
┌───────▼──────┐ ┌──────▼──────┐ ┌─────▼──────┐ ┌──────▼───────┐ ┌──────▼────────┐
│preve_pipeline│ │preve_reglas │ │preve_ciclo │ │preve_validac.│ │preve_informe  │
│ lectura, SKU,│ │ + seeds/    │ │ ciclo de   │ │ scorecard,   │ │ Word / PDF    │
│ stock, ADI/  │ │ plantillas  │ │ compras    │ │ ROI          │ │               │
│ CV², modelos,│ └─────────────┘ └────────────┘ └──────────────┘ └───────────────┘
│ ROP/nivel    │
└──────┬───────┘
       │                  ┌──────────── Configuración y datos ────────────┐
       └─────────────────►│ preve_config (Config, ParametrosModelo)       │
                          │ preve_historial (fachada) · preve_importador  │
                          │ database.py ── SQLAlchemy Core ── preve.db    │
                          └───────────────────────▲───────────────────────┘
                                                  │
                          plugins/ (PluginERP) ── negosy/extractor (Playwright)
```

Reglas de dependencia:
- **Solo `database.py` ejecuta SQL.** Todo lo demás llama a métodos de `Database`.
- El pipeline **no** conoce la interfaz ni la base: recibe `ParametrosModelo` y una plantilla, devuelve DataFrames y escribe mensajes por un callback `log`.
- La interfaz nunca calcula pronósticos: lee los CSV y la base que dejó el orquestador.

## Flujo de un análisis (`ejecutar_pipeline`)

```
Config.actual() ─► ParametrosModelo (ciclo principal, sucursal) + plantilla de categorías
      │
reportes ERP (xlsx) ─► cargar_datos ─► deduplicar_entre_archivos ─► aplicar_mapeo_ventas
      │
      ├─► construir_dim_producto (SKU = código de barras, sufijo si colisiona) ─► construir_mapas
      │
      ├─► reconstruir_stock (ancla = fecha del listado; traslados entre almacenes propios = 0;
      │                     sin doble descuento según configuración) ─► stock diario, ventas_diarias
      │
      ├─► construir_panel_y_segmentacion (panel semanal, ADI/CV², ABC por margen)
      ├─► entrenar_y_pronosticar (Croston-TSB · LightGBM · CatBoost → p10/p50/p90 del horizonte)
      │
      ├─► calcular_recomendacion por ciclo (ROP, nivel objetivo, tránsito, variables de reglas)
      │       └─► preve_reglas.clasificar (plantilla) ─► accion / motivo / decision
      │
      ├─► Excel, notas de compra por proveedor, dashboard.html, CSV
      ├─► _intentar_validacion (ventas reales desde la recomendación anterior, prorrateadas)
      ├─► registrar_importacion (ciclo: se confirma solo si todo salió bien)
      ├─► historial + base: ejecución, recomendaciones, ventas_diarias, stock_actual, pronósticos
      └─► informe para gerencia (Word / PDF)
```

## Backtesting

```
DatosBacktest.desde_carpeta ─► ventanas de N días (alineadas al lunes)
   para cada ventana [t, t+N):
       estado_ventana ─► omitir (sin datos / solo compras) o evaluar
       evaluar_ventana: datos.hasta(t) ─► pronosticador ─► comparar con ventas de [t, t+N)
   para cada día de reporte final d del ciclo:
       simular_compra: datos.hasta(d) ─► recomendación ─► comparar con ventas de [d, d+H)
   ─► metricas_periodo, pronosticos(venta_real), ejecuciones_backtesting + Excel
```

El pronosticador es inyectable (`Backtester(pronosticador=...)`): los tests usan uno falso que registra qué fechas vio, para verificar que no haya fuga de información.

## Base de datos (`preve.db`)

| Grupo | Tablas |
|---|---|
| Empresa | `empresas`, `sucursales`, `proveedores`, `ciclos_compra`, `configuracion` |
| Catálogo y datos | `productos`, `ventas_diarias`, `stock_actual`, `ordenes_compra_abiertas` |
| Modelo | `pronosticos`, `metricas_periodo`, `ejecuciones_backtesting` |
| Usuario | `decisiones_compra`, `plantillas_categorias`, `categorias_activas`, `perfiles_importacion` |
| Historial | `ejecuciones`, `recomendaciones`, `metricas_ejecucion` |
| Esquema | `schema_version` (migraciones en `database.MIGRACIONES`) |

**Migraciones:** agregar `(versión, descripción, función)` al final de `MIGRACIONES`; nunca editar una anterior. `Database()` aplica las pendientes al abrirse.

**PostgreSQL:** `PREVE_DB_URL=postgresql+psycopg://usuario:clave@host/preve`. Los upserts usan `ON CONFLICT` del dialecto correspondiente.

## Configuración

`preve_config.DEFAULTS` documenta cada clave con su tipo y descripción. Las claves dinámicas por sucursal son `almacenes:<codigo>`, `carpetas:<codigo>` y `ciclo_estado:<codigo>`. Los archivos de versiones anteriores (`preve_config.json`, `ciclo_estado.json`, `preve_historial.db`) se importan una sola vez.

## Logs y errores

`preve_utils.configurar_logging()` escribe en consola y en `preve.log` con rotación. Los módulos usan `get_logger('<módulo>')` y los mensajes de progreso pasan por `log_info`. No hay `except Exception: pass`: los errores esperables se capturan por tipo y quedan en el log con su traceback.
