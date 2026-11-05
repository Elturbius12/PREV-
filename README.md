# PREVÉ — Pronóstico de demanda y asistente de compras

Aplicación de escritorio (Python + tkinter) que pronostica la demanda de cada producto de **Yuju Store** y le dice al comprador **qué comprar, cuánto y cuándo**, a partir de los reportes que exporta su ERP (Negosy).

## El problema que resuelve

Una tienda con miles de productos no puede decidir a ojo qué reponer. Comprar de menos genera quiebres de stock y ventas perdidas; comprar de más inmoviliza capital en productos que no rotan. Además, la demanda de un catálogo grande es muy heterogénea: hay productos que se venden todos los días, otros esporádicamente y otros recién lanzados sin historia. PREVÉ:

- Reconstruye el stock diario histórico a partir de ventas, compras y movimientos de almacén.
- Elige el modelo de pronóstico adecuado para cada producto.
- Calcula el punto de reorden (ROP), la cantidad a comprar y la inversión necesaria.
- Explica cada recomendación en lenguaje natural y registra la decisión del usuario.

**Principio del proyecto:** nunca inventa un número. Si un dato falta, queda en blanco o pendiente.

## Arquitectura híbrida de modelos

Cada producto se segmenta con la clasificación de **Syntetos-Boylan** (ADI = intervalo medio entre demandas, CV² = coeficiente de variación al cuadrado de la demanda; umbral ADI = 1.32) y se asigna a un modelo:

| Segmento | Modelo | Por qué |
|---|---|---|
| Alta rotación (demanda frecuente) | **LightGBM** | Aprovecha estacionalidad, tendencia y rezagos con árboles de gradiente. |
| Productos nuevos / poca historia | **CatBoost** | Robusto con pocos datos y variables categóricas (categoría, subcategoría). |
| Baja rotación / demanda intermitente | **Croston-TSB** | Modelo diseñado para series con muchos ceros (vía `statsforecast`). |

Los productos con pronóstico inestable se marcan para revisión manual. El nivel de servicio objetivo depende de la clase ABC: A = 95 %, B = 90 %, C = 85 %.

## Ciclos de compra

| Ciclo | Revisión | Lead time | Horizonte de cobertura |
|---|---|---|---|
| **Internacional** | cada **105 días** | **36 días** | 141 días |
| **Local** | cada **30 días** | **10 días** | 40 días |

Dentro del ciclo internacional el usuario importa datos del ERP aproximadamente cada 14 días; las corridas de las primeras semanas se consideran preliminares y se aprueba la compra definitiva al acercarse la fecha límite.

## Categorías de acción

| Acción | Significado |
|---|---|
| **RECOMPRAR** | El stock está bajo el punto de reorden: comprar la cantidad sugerida. |
| **REDUCIR** | Stock suficiente o holgado para el horizonte: no comprar por ahora. |
| **LIQUIDAR** | Sin demanda proyectada o stock mayor a 3 veces la demanda: rematar. |
| **PUBLICITAR** | Demanda declinante con buen margen: impulsar con promoción. |
| **NEGOCIAR** | Alta rotación (clase A) con margen muy bajo: renegociar costo con el proveedor. |
| **VIGILAR** | Producto nuevo o con poca historia: comprar lo mínimo y monitorear. |

## Estructura del proyecto

| Archivo | Descripción |
|---|---|
| `preve_app.py` | Ventana principal tkinter: navegación, atajos, hilos de ejecución y notificaciones. |
| `preve_ui.py` | Componentes visuales reutilizables (botones, tarjetas, sliders, tema, escalado DPI). |
| `preve_vistas.py` | Pantallas: Inicio, Generar, Recomendaciones, Decisión rápida, Simular, Historial, Configuración. |
| `preve_datos.py` | Capa de datos de la UI: configuración persistente, lectura de resultados, formato de números. |
| `preve_pipeline.py` | Núcleo analítico: carga de reportes, reconstrucción de stock, segmentación, entrenamiento y recomendación. |
| `preve_orquestador.py` | Ejecuta el pipeline completo y genera el Excel de resultados y el dashboard. |
| `preve_ciclo.py` | Control de los ciclos de compra (105 días / lead time 36 días) y fechas límite. |
| `preve_validacion.py` | Scorecard: compara pronósticos pasados con ventas reales (WMAPE, sesgo). |
| `preve_historial.py` | Historial de ejecuciones y decisiones del usuario en SQLite. |
| `preve_simulacion.py` | Simulador de escenarios («¿y si la demanda sube 20 %?», «¿y si el proveedor tarda más?»). |
| `preve_informe.py` | Informes en Word y PDF con gráficos. |
| `preve_extractor.py` | Bot Playwright que descarga los reportes Excel desde Negosy (solo exporta, nunca modifica). |
| `dashboard_template.html` | Plantilla del dashboard HTML. |
| `INSTALAR_Y_EJECUTAR.bat` / `instalar_y_ejecutar.sh` | Instalan dependencias y abren la app (Windows / Mac-Linux). |
| `.env.example` | Plantilla de variables de entorno. |

## Instalación y ejecución

Requiere **Python 3.10+** (en Windows, marca «Add python.exe to PATH» al instalar).

```bash
git clone https://github.com/Elturbius12/PREV-.git
cd PREV-
python -m venv .venv
.venv\Scripts\activate          # Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
python preve_app.py
```

O simplemente haz doble clic en `INSTALAR_Y_EJECUTAR.bat` (Windows) o `instalar_y_ejecutar.sh` (Mac/Linux).

Para usar el extractor automático de Negosy, instala además el navegador de Playwright una vez:

```bash
playwright install chromium
python preve_extractor.py --destino ./datos_negosy --tienda san_jose
```

### Reportes de entrada

En la carpeta de entrada deben estar los reportes del ERP, sin renombrar:
`listado_productos...xlsx`, `reporte_ventas_producto...xlsx` (puede haber varios periodos), `Reporte_compras_por_producto...xlsx` y `Reporte_productos_por_movimiento...xlsx`.
Estos archivos contienen datos reales del negocio y **no se incluyen en el repositorio**.

## Configuración del `.env`

1. Copia `.env.example` a `.env`.
2. Completa tus credenciales de Negosy:

```env
NEGOSY_USUARIO=tu_usuario
NEGOSY_CONTRASENA=tu_contrasena
```

El `.env` está en `.gitignore` y nunca se sube. Las carpetas de trabajo por sucursal se guardan en `preve_config.json`, que se crea solo desde la pantalla *Configuración* y tampoco se versiona.

## Estado del proyecto

- ✅ **Fase 1:** carga de reportes, reconstrucción de stock y segmentación ADI/CV².
- ✅ **Fase 2:** modelos híbridos (LightGBM, CatBoost, Croston-TSB), ROP y cantidad a comprar.
- ✅ **Fase 3:** ciclos de compra, historial, validación (scorecard) y decisiones del usuario.
- ✅ **Fase 4:** interfaz de escritorio, informes Word/PDF y extractor automático de Negosy.
- ⏳ **Fase 5 (pendiente):** soporte multi-sucursal completo y simulación avanzada de escenarios.

## Guía para colaboradores / otras IAs

Puntos del código que se pueden mejorar:

1. **Pruebas automáticas:** no hay tests. Empezar por `preve_pipeline.py` (`_clasificar_accion`, `reconstruir_stock`, segmentación) con datos sintéticos pequeños.
2. **Datos de ejemplo:** falta una carpeta `sample_data/` con reportes sintéticos y anonimizados para poder probar la app sin datos reales.
3. **Modularizar `preve_pipeline.py` y `preve_vistas.py`:** son archivos muy largos (≈35 KB y ≈55 KB); separar carga, stock, modelos y reglas de negocio.
4. **Constantes duplicadas:** los ciclos (105/36 días) aparecen en `preve_pipeline.py` y `preve_ciclo.py`; centralizarlos en un único módulo de configuración.
5. **Importaciones dentro de funciones** (`openpyxl`, `json`, `os` repetidos en `preve_orquestador.py`): moverlas al inicio y extraer una utilidad común de escritura de Excel.
6. **Extractor Negosy:** los selectores y URLs están fijos en el código; moverlos a configuración y agregar reintentos ante cambios de la web. Hay imports sin usar (`re`, `Path`).
7. **Rutas y sucursales:** `preve_config.json` guarda rutas absolutas; usar rutas relativas y completar el soporte multi-sucursal (Fase 5).
8. **Entrenamiento:** no hay validación cruzada temporal ni búsqueda de hiperparámetros (LightGBM usa parámetros fijos); evaluar backtesting rolling por segmento.
9. **Rendimiento:** el entrenamiento por producto es secuencial; paralelizar con `joblib` o entrenar modelos globales por segmento.
10. **Empaquetado:** agregar `pyproject.toml`, versiones fijadas en `requirements.txt` y un ejecutable con PyInstaller.
11. **Logging:** reemplazar `print`/callbacks por el módulo `logging` con niveles y archivo de log.
12. **Internacionalización y tipado:** agregar type hints y separar textos de la UI.

## Licencia

Proyecto personal/académico de Yuju Store. Pendiente de definir licencia.
