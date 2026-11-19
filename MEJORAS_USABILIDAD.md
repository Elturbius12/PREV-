# PREVÉ — Mejoras de usabilidad para usuario no técnico

> Evaluación hecha el 2026-11-06 desde la perspectiva de un dueño de tienda
> sin conocimientos estadísticos ni técnicos. PREVÉ pasó de prototipo roto a
> producto funcional en código, pero todavía le falta capa de usabilidad para
> que una tienda sin equipo técnico lo use sin acompañamiento.
>
> **Nota general: 7.5 / 10**
>
> Para un usuario con conocimiento de logística (como el equipo de Yuju Store),
> está listo. Para un dueño de tienda que nunca abrió Excel con fórmulas, no.

---

## 🚨 Lo que NO va a funcionar para un usuario normal

### 1. "Modo Backtesting" — demasiado técnico

El nombre asusta. Un dueño de tienda no sabe qué es *walk-forward validation* ni
*WMAPE*. Encima el texto dice:

> *"Cada ventana entrena los 3 modelos (≈1 minuto por ventana): 6 meses con
> ventanas de 14 días tardan unos 15–20 minutos."*

**Qué piensa el usuario:** *¿ventanas? ¿modelos? ¿para qué sirve esto?*

**Propuesta:**
- Renombrar a **"Probar qué tan bien acierta PREVÉ"**.
- Explicación en una frase: *"Le muestra a PREVÉ los datos del pasado por partes
  para que pronostique como si no supiera lo que vino después. Luego compara
  con la realidad para que sepas si puedes confiar en él."*
- Reemplazar "ventana" por "pedazo" en toda la interfaz.

### 2. El wizard pide cosas que un usuario no sabe

- **Paso 2:** "Sucursales con almacenes separados por comas, como aparecen en el ERP" →
  ¿cuál es el nombre exacto en Negosy? Si se equivoca por un acento, no matchea.
- **Paso 3:** "Lead time, día del reporte final, horizonte" → tres palabras técnicas
  en un solo paso.
- **Paso 5:** "Mapea las columnas del Excel" → ¿y si no entiende *mapear*?

**Propuesta:**
- Botón de ayuda **(?)** al lado de cada campo con un ejemplo visual real.
- Tooltip con captura de pantalla (ej.: "así aparece en Negosy").
- Validación en vivo: si escribe un nombre de almacén que no matchea con los
  reportes que ya cargó, le avisa.

### 3. Reporte de errores de importación

El usuario ve: *"importamos 58% de las líneas, 42% no encontró su producto"*.
Piensa: PREVÉ está roto.

**Propuesta:**
- Botón **"Ver los productos que no se reconocieron"**.
- Lista con columnas: código, nombre, cuántas líneas lo mencionan, cuál es el
  producto más parecido del catálogo (fuzzy match).
- Un clic → "Es el mismo producto" → se guarda el alias y reimporta.

### 4. Los números del backtesting son indescifrables

> WMAPE 66%, CatBoost 87%, Croston-TSB 133%

**Traducir:**
- "LightGBM acierta en promedio 1 de cada 3 productos de alta rotación. Bueno."
- "CatBoost (productos nuevos): acierta 1 de cada 8. Necesita mejorar."
- "Croston-TSB (productos de baja venta): tiene problemas serios."

**Y además:** semáforo 🟢🟡🔴 por producto en la bandeja de recomendaciones.

### 5. "Stock en tránsito: completa `ordenes_compra_abiertas.csv`"

Decirle a un usuario que edite un CSV es mandarlo a abrir Excel, equivocarse en
el formato, y volver.

**Propuesta:** formulario dentro de la app con **"+ Agregar orden pendiente"**
→ proveedor (dropdown), producto (autocomplete), cantidad, fecha estimada.

### 6. El informe para gerencia (Word/PDF)

El gerente lo lee en 30 segundos. Si no tiene al inicio una página de
**"Qué debo hacer esta semana"** con 3 bullets gigantes, se pierde entre tablas.

**Propuesta:**
- Página 1: 3 bullets de acción en 24 pt.
- Página 2: inversión total, top 10 productos urgentes, 1 gráfico.
- El resto queda como anexo.

### 7. Logs técnicos en la cara del usuario

`preve.log` es útil para el desarrollador, pero si algo sale mal, el usuario
promedio no lo va a abrir.

**Propuesta:** botón **"Reportar problema"** que empaca el log + el último error +
la configuración + los últimos 20 clics en un ZIP listo para enviar por correo
o WhatsApp.

### 8. "Y si el proveedor tarda más" — Simular

Está GENIAL pero escondido en el menú. Debería ser el primer sell point:
*"¿Qué pasa si suben el dólar? ¿Si el proveedor se demora? Prueba sin arriesgar."*

**Propuesta:** tarjeta grande en el Inicio: *"Prueba un escenario antes de comprar →"*.

---

## 🎯 Problemas de experiencia más graves

| # | Problema | Impacto |
|---|---|---|
| 1 | El usuario no sabe si los números son correctos | **Crítico** — un error sutil puede costar S/ miles |
| 2 | No hay onboarding con datos de ejemplo | Usuario nuevo abre y no sabe por dónde empezar |
| 3 | 20 minutos de espera sin saber si puede cerrar la app | Si cierra, pierde el análisis |
| 4 | "Simulación sugirió 4× más de lo necesario" → el usuario confía y compra | **Crítico** — descrédito total del sistema |
| 5 | No hay forma de deshacer una decisión marcada como "comprado" | Un clic equivocado y adiós registro |
| 6 | No hay ayuda contextual ni tutorial | La curva de aprendizaje es alta |

---

## 💡 Lo que agregaría (prioridad alta)

1. **Modo demo con datos ficticios** — abrir la app sin importar nada y jugar.
2. **Alertas inteligentes** — *"El producto X lleva 3 análisis seguidos marcado
   urgente, ¿qué pasó?"*.
3. **Explicación por producto** — al tocar cualquier producto: *"Lo sugiero
   porque vendiste X en los últimos N días y tu stock aguanta Y"*.
4. **Modo "revisar antes de comprar"** — botón: *"Antes de confirmar la nota
   de compra, revisa estos 5 productos con más riesgo"*.
5. **Confianza visible** — cada sugerencia con medidor 🟢🟡🔴 basado en qué tan
   bien el modelo pronostica ese producto históricamente.
6. **Deshacer decisión** — en "Decisión rápida", permitir deshacer la última
   acción (Ctrl+Z).
7. **Persistir estado del análisis** — si el usuario cierra la app a los 15 min
   del análisis, al reabrir retoma desde donde iba (o al menos le avisa).

---

## 🔍 Bugs y mejoras concretas encontradas al leer el código

### Hardcodes del rubro / ciudad

- **`FRASES_CARGA` en `preve_app.py`:**
  - *"Juliaca es uno de los principales centros comerciales del sur del Perú"* —
    si una empresa de Lima o Arequipa usa PREVÉ, la frase se ve rara. Debería
    tomar la ciudad de la configuración o eliminarse.
  - *"Las importaciones de autopartes en Perú crecen cada año. Es un mercado
    con mucho potencial."* — hardcode del rubro del autor. Debería eliminarse
    o parametrizarse por rubro.
  - *"El comercio internacional mueve más de 25 billones de dólares al año."* —
    dato incorrecto. En 2024 el comercio global está en ~33 billones USD
    (fuente: OMC). Debería eliminarse (mantener solo frases verificables).

### Credenciales

- **`.env.example`** usa placeholders (`tu_usuario_ejemplo`) correctamente.
  Pendiente: revisar el historial de git (`git log -p -- .env`) por si en algún
  commit viejo quedaron credenciales reales. Si aparecen, hay que:
  1. Cambiar la contraseña de Negosy inmediatamente.
  2. Reescribir el historial con `git filter-repo` o aceptar que ya están
     públicas y rotar la clave.

### Deuda técnica del reporte final (según REPORTE_FINAL.md)

| # | Pendiente | Por qué importa |
|---|---|---|
| 1 | Intervalos P10/P90 con residuos in-sample (M8) | Stock de seguridad subestimado |
| 2 | CatBoost con sesgo negativo | Subestima productos nuevos sistemáticamente |
| 3 | Probar ADIDA/IMAPA para intermitentes | Croston-TSB da 133% WMAPE |
| 4 | Backtesting ~40s por ventana | Se puede paralelizar con joblib |
| 5 | Pipeline sigue leyendo Excel en cada análisis | Lento; migrar a leer de BD |
| 6 | `requirements.txt` sin versiones fijas | Puede romperse en una PC nueva |
| 7 | `INSTALAR.bat` no probado en PC limpia | Riesgo al distribuir |
| 8 | ~100 funciones de tkinter sin docstring | Deuda de mantenimiento |
| 9 | Sin tests automáticos de la GUI | Un cambio de UI puede romper algo sin detectarse |
| 10 | Plantilla FARMACIA pendiente | Si quieres venderle a una farmacia |
| 11 | TRASLADAR entre sucursales (multi-sucursal real) | Fase 5 del plan original |

---

## 🚀 Orden sugerido para seguir (próximas fases)

### Fase 8 — Botón "Depurar datos" + primeras correcciones de usabilidad
*(hecho en este entregable)*

- ✅ Botón "Depurar datos" en Configuración
- ✅ Frases hardcodeadas (Juliaca, autopartes, 25 billones) eliminadas o parametrizadas
- ✅ Nombre del modo backtesting cambiado
- ✅ Documento MEJORAS_USABILIDAD.md

### Fase 9 — UX para usuario no técnico (grande)

- Tooltips y ayudas contextuales en el wizard
- Pantalla de errores de importación con fuzzy match
- Formulario para órdenes de compra abiertas (adiós CSV)
- "Reportar problema" (empaqueta logs)
- Deshacer decisión
- Explicación por producto al tocar la fila

### Fase 10 — Confianza en los resultados

- Medidor 🟢🟡🔴 por producto
- Traducción de métricas del backtesting al lenguaje común
- Alertas cuando un producto aparece urgente en 3 análisis seguidos
- Página 1 del informe gerencial reescrita como "3 cosas que hacer"

### Fase 11 — Modelos (una vez con datos limpios)

- Analizar por qué Croston-TSB tiene 133% WMAPE
- Corregir sesgo de CatBoost
- Intervalos P10/P90 out-of-sample
- Probar ADIDA/IMAPA para demanda intermitente

### Fase 12 — Multi-sucursal real y TRASLADAR

- Nodo "almacén central" separado de San José
- Sugerencias de traslado entre sucursales
- Simulación multi-sucursal

### Fase 13 — Cloud multi-tenant

- PostgreSQL + Row-Level Security por empresa
- FastAPI + workers con Celery/RQ
- UI web (puede reemplazar a tkinter o coexistir)
- Secretos en gestor dedicado (no `.env`)
