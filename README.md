# PREVÉ — Pronóstico de demanda y asistente de compras

Aplicación de escritorio (Python + tkinter) que pronostica la demanda de cada producto y le dice al comprador **qué comprar, cuánto y cuándo**. Lo calcula a partir de los reportes del ERP (Negosy u otro sistema). Nació para Yuju Store y es configurable para cualquier empresa: sucursales, ciclos de compra, IGV, modelos y reglas.

**Principio del proyecto:** nunca inventa un número. Si un dato falta, queda en blanco o pendiente.

## Qué hace

1. **Importa** los reportes del ERP (listado, ventas, compras, movimientos). Acepta Excel o CSV de cualquier sistema con mapeo de columnas, y descarga automática desde Negosy como plugin (incremental: solo trae lo que falta desde la última descarga).
2. **Reconstruye el stock diario** hacia atrás desde el stock del listado. Neutraliza los traslados entre almacenes de la misma sucursal y evita el doble descuento si el ERP registra ventas como salidas.
3. **Segmenta** cada producto con ADI/CV² (Syntetos-Boylan) y le asigna un modelo:

| Segmento | Modelo |
|---|---|
| Demanda regular / alta rotación | **LightGBM** |
| Producto nuevo (< 13 semanas) | **CatBoost** |
| Demanda intermitente o sin historia | **Croston-TSB** |

4. **Calcula** el punto de reorden, el nivel objetivo y la cantidad a pedir, descontando el stock en tránsito de las órdenes abiertas.
5. **Clasifica** cada producto con una plantilla de reglas: RECOMPRAR, MANTENER, LIQUIDAR, REDUCIR, PUBLICITAR, NEGOCIAR, DESCONTINUAR, REVISAR_COMPRA…
6. **Valida** contra las ventas reales: un scorecard en cada análisis y un **modo backtesting** *walk-forward*.

### Fórmulas

```
stock de seguridad (SS) = z(clase ABC) · σ de la demanda del horizonte
ROP                     = demanda durante el lead time + SS · √(lead time / horizonte)
nivel objetivo          = demanda del horizonte + SS
cantidad a pedir        = max(0, nivel objetivo − stock − stock en tránsito)
```

### Ciclos de compra (configurables, N ciclos)

| Ciclo | Revisión | Lead time | Horizonte |
|---|---|---|---|
| Internacional (principal) | cada 105 días | 36 días | 141 días |
| Local | cada 30 días | 10 días | 40 días |

El ciclo de mayor horizonte es el principal: define las etiquetas PRELIMINAR / BORRADOR / FINAL y el reporte completo. Los demás generan sus notas de compra.

**Calendario fijo.** Los ciclos se cuentan desde un día 1 (por defecto el 1 de enero; se cambia en *Configuración › Calendario de compras*), sin importar cuándo se genere la recomendación. Con ciclos de 105 días: ciclo 1 = 1 ene–15 abr (el día 105 es la fecha del pedido), ciclo 2 = 16 abr–29 jul, ciclo 3 = 30 jul–11 nov… Una recomendación generada el 9 de noviembre es la del **ciclo 3, día 103 (FINAL)**. La vista *Calendario* muestra todos los ciclos con sus fechas.

## Instalación

Requiere **Python 3.11+**. En Windows, marca «Add python.exe to PATH» al instalar.

- **Windows:** doble clic en `INSTALAR.bat`.
- **Linux / macOS:** `./instalar.sh`.

El instalador verifica la versión de Python, crea un entorno virtual `.venv`, instala `requirements.txt`, prepara la base `preve.db` (e importa la configuración y el historial de versiones anteriores sin duplicar) y abre la app.

Instalación manual:

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python scripts/migrar_a_sqlite.py # opcional: --con-ventas importa también las ventas de los Excel
python preve_app.py
```

Para reproducir exactamente: `pip install -r requirements-lock.txt` (versiones fijadas con las que se generó la corrida canónica del artículo; `requirements.txt` solo tiene las versiones mínimas).

La primera vez se abre un **asistente de 6 pantallas**: empresa, sucursales y sus almacenes, ciclos de compra, frecuencia de análisis, columnas del Excel de ventas y plantilla de categorías. Todo se puede editar después en *Configuración*.

### Credenciales y URLs del ERP

Solo hacen falta para la descarga automática desde Negosy. En *Configuración → Integración con tu ERP* (elige Negosy) se editan la URL de login, la URL base de cada sucursal, el usuario y la contraseña. Quedan en `preve.db` **sin cifrar** (app de escritorio de un solo usuario, mismo nivel de confianza que un `.env`).

También siguen funcionando las variables de entorno, y si hay credenciales guardadas en Configuración **tienen prioridad**:

```bash
cp .env.example .env    # y completar
NEGOSY_USUARIO=tu_usuario
NEGOSY_CONTRASENA=tu_contrasena
```

El `.env` nunca se sube a git.

## Dónde quedan los archivos

Cada análisis y cada descarga tienen su propia subcarpeta con las fechas de los **datos** (`AAAA-MM-DD_a_AAAA-MM-DD`):

```
entrada/                                   ← reportes del ERP
├── 2024-11-06_a_2026-11-05/               ← primera descarga: 730 días
└── 2026-11-06_a_2026-11-19/               ← siguientes: desde el día después del anterior hasta AYER
    └── .descarga_completa                 ← solo cuenta como importada si la descarga terminó bien
salida/                                    ← resultados
├── ordenes_compra_abiertas.csv            ← lo editas a mano; se queda en la raíz
├── 2026-11-06_a_2026-11-19/               ← una subcarpeta por análisis
│   ├── PREVE_Recomendacion_Compra.xlsx, PREVE_Notas_Compra_*.xlsx, PREVE_Clasificacion_Productos.xlsx
│   ├── PREVE_Informe_Gerencia.docx/.pdf, PREVE_Tablero.xlsx (si lo exportas desde el Tablero)
│   └── datos/                             ← CSV internos que lee la app (no hace falta abrirlos)
```

Los cuadros y figuras del artículo (botón *📊 Cuadros y figuras del artículo* en Inicio) quedan en `salida/articulo/`: `tablas_articulo.xlsx` (todas las tablas, una hoja cada una, con índice), `figuras/`, `metodologia.txt` y `datos_csv/`.

### Resultados del artículo (reproducibilidad)

```bash
pip install -r requirements-lock.txt
python generar_resultados_articulo.py --congelar          # corrida canónica → salida/articulo_canonico/ + META.json
python generar_resultados_articulo.py --muestra-anonima   # además escribe salida/articulo/muestra_anonima/ (50 SKU)
python generar_resultados_articulo.py --sucursal Muestra --entrada salida/articulo/muestra_anonima/erp --salida salida/articulo_muestra
```

- **El artículo cita siempre `salida/articulo_canonico/`.** Su `META.json` registra la fecha, la versión de Python, el `pip freeze`, el SHA-256 de cada reporte de entrada, el comando exacto y el commit de git.
- Cualquier corrida sin `--congelar` compara el WMAPE de la Tabla 4 con la canónica y escribe un **WARNING** en el log y en `metodologia.txt` si alguno difiere en más de 0,5 pp.
- Tablas nuevas:
  - Tabla 0: diccionario de variables.
  - Tabla 10: residuos (Shapiro-Wilk y Ljung-Box).
  - Tabla 11: escenarios E0 Naive / E1 GBM sin disponibilidad / E2 Pipeline.
  - Tabla 12: cobertura de los intervalos P10/P90 fuera de muestra.
  - Tabla 7: suma las columnas `escenario` y `exceso_evitado_pen` (= exceso de Naive − exceso del modelo).
- La regla de compra en notación formal está en [`formulacion_compras.md`](formulacion_compras.md).
- La muestra anónima (`README_muestra.md` dentro de la carpeta) trae los 4 reportes del ERP de 50 SKU al azar, en el mismo formato del ERP. Los códigos y nombres se reemplazan por `SKU_001…` y los importes se escalan. Así se puede ejecutar todo el análisis sin acceder a los datos reales.

La app lee siempre el período más reciente (*Inicio → Abrir carpeta de resultados* lo abre). El estado del ciclo vive en la base de datos. «Reiniciar PREVÉ» (Configuración) borra, entre otras cosas, solo las subcarpetas de período de la salida (con respaldo ZIP previo); nunca toca `ordenes_compra_abiertas.csv`. Los archivos sueltos de versiones anteriores no se mueven ni se borran.

El catálogo de productos se descarga siempre por defecto: de él sale el stock actual. Desmarcar «Actualizar el catálogo» reutiliza el último y deja el stock con la fecha de ese catálogo (solo para pruebas).

## Uso

| Pantalla | Para qué |
|---|---|
| Inicio | **Qué hacer hoy** (acciones priorizadas con su botón), ciclo, inversión, salud del inventario y botón de cuadros del artículo |
| Tablero | Gráficos del último análisis con una frase de lectura cada uno; *Exportar tablero a Excel* (tablas + figuras en un archivo) |
| Calendario | Ciclos fijos de compra: inicio, preliminar, borrador y fecha del pedido |
| Generar | Carpetas de reportes, importar ventas desde un archivo, descargar desde el ERP y ejecutar el análisis |
| Recomendaciones | Bandeja por producto: explicación, confianza y decisión (comprado / posponer / descartar) |
| Decisión rápida | Las 20 compras de mayor impacto, una por una |
| Simular | ¿Y si la demanda sube 20%? ¿Y si el proveedor tarda más? |
| Historial | Análisis anteriores con la variación de inversión |
| Configuración | Empresa, sucursales, ciclos, categorías de acción (con editor visual), ERP (URLs y credenciales de Negosy), calendario, parámetros del modelo y «Reiniciar PREVÉ» (borrar datos o reiniciar todo) |
| **Modo Backtesting** | Botón naranja de la barra superior: valida el modelo contra el pasado y exporta un Excel |

Atajos: `Ctrl+G` generar · `Ctrl+S` simular · `Ctrl+K` buscar · `Ctrl+H` historial · `Ctrl+R` recomendaciones · `Ctrl+D` decisión rápida · `Ctrl+I` inicio.

**Stock en tránsito:** completa `ordenes_compra_abiertas.csv` (se crea vacío en la raíz de la carpeta de resultados, no dentro de un período) con las órdenes emitidas y aún no recibidas.

## Estructura

| Módulo | Qué hace |
|---|---|
| `preve_app.py`, `preve_vistas.py`, `preve_ui.py` | Interfaz tkinter (ventana, vistas, componentes) |
| `preve_wizard.py`, `preve_editor_plantillas.py`, `preve_importador_ui.py`, `preve_vista_backtesting.py` | Asistente inicial, editor de reglas, importación y backtesting en la UI |
| `preve_pipeline.py` | Motor: lectura, SKU canónico, stock, segmentación, modelos y recomendación |
| `preve_orquestador.py` | Corre el pipeline por ciclo y genera Excel, notas de compra, dashboard, validación e informe |
| `preve_reglas.py`, `seeds/plantillas_categorias.py` | Motor de reglas (sin `eval`) y plantillas RETAIL, RETAIL_CLASICO, MANUFACTURA y DISTRIBUCION |
| `preve_backtesting.py` | Backtesting walk-forward y simulación del día de reporte |
| `preve_validacion.py` | Scorecard (WMAPE), contrafactual y ROI |
| `preve_simulacion.py` | Escenarios *what-if* |
| `preve_importador.py` | Importación flexible de ventas (xlsx/csv/tsv) con validación por fila |
| `preve_config.py` | Configuración con caché y validación de tipos |
| `database.py` | Capa de datos (SQLAlchemy Core): único lugar con SQL; SQLite por defecto, PostgreSQL con `PREVE_DB_URL` |
| `preve_historial.py`, `preve_ciclo.py`, `preve_datos.py` | Historial, ciclo de compras y capa de datos de la UI |
| `preve_informe.py` | Informe para gerencia (Word y PDF) |
| `preve_depurar.py` | «Depurar datos»: borra los períodos de resultados y/o el historial, con respaldo ZIP |
| `preve_utils.py` | Logging y carpetas de período (`nombre_periodo`, `ultimo_periodo`, `rango_descarga`) |
| `plugins/` | Integraciones con ERP (`PluginERP`), por ahora `negosy` (`extractor.py` descarga, `tiendas.py` guarda sus ajustes) |
| `scripts/migrar_a_sqlite.py` | Migración de los archivos de versiones anteriores a `preve.db` |
| `tests/` | 218 tests (pytest) |

Detalle técnico en [ARQUITECTURA.md](ARQUITECTURA.md), guía para contribuir en [CONTRIBUTING.md](CONTRIBUTING.md), cambios por fase en [CHANGELOG.md](CHANGELOG.md) y el estado del proyecto en [REPORTE_FINAL.md](REPORTE_FINAL.md).

## Tests

```bash
pip install -r requirements-dev.txt
pytest                                          # 218 tests
pytest --cov=preve_pipeline --cov=database --cov=preve_validacion --cov=preve_backtesting
```

Cada test usa una base temporal propia y datos sintéticos: nunca toca `preve.db` ni reportes reales.

## Logs

`preve.log` en la carpeta de la app (rota cada 5 MB y guarda 3 archivos). El detalle técnico de cualquier error queda ahí.

## Licencia

Proyecto de Yuju Store. Licencia pendiente de definir.
