# Formulación matemática de la política de compras de PREVÉ

Este documento describe **la regla que ejecuta el código**, en notación formal. Todas las fórmulas salen de
`preve_pipeline.calcular_recomendacion` (la usan la app, `preve_simulacion.simular` para los escenarios what-if
y `Backtester.simular_compra` en el backtesting del artículo) y de los modelos de pronóstico
(`preve_modelo_gbm.py`, `preve_modelos_baseline.py`). La notación está en la Tabla 0
(`Tabla_0_variables.csv`).

## 1. Problema

Para cada SKU *i* y cada fecha de pedido del ciclo (calendario fijo cada *C* días), decidir la cantidad
*Q_i* que minimiza el costo esperado del ciclo:

```
min_{Q_i}  E[ K·1{Q_i > 0} + h·(s_i + q^tr_i + Q_i − D_i)^+ + p·(D_i − s_i − q^tr_i − Q_i)^+ ]
s.a.       P( D_i > s_i + q^tr_i + Q_i ) ≤ 1 − α_i         (restricción de nivel de servicio)
           Q_i ≥ 0,  entero
```

- `D_i`: demanda del SKU en el horizonte de protección `H` (revisión + lead time), variable aleatoria.
- `K`: costo de emitir el pedido; `h`: costo unitario de mantener inventario; `p`: costo unitario de faltante.
- `α_i = Φ(z_i)`: nivel de servicio objetivo según la clase ABC.

**Cómo lo resuelve el código.** PREVÉ no optimiza `K`, `h` ni `p` numéricamente. Aplica la solución estándar de una
política de **revisión periódica con nivel objetivo (R, S)**:

- La fecha de pedido la fija el calendario (`C` días, ver *Configuración › Calendario de compras*). Por eso
  `K` no cambia `Q_i`: el pedido del ciclo se emite igual.
- La restricción de servicio se cumple con una aproximación normal de `D_i`. El cuantil `α_i` del nivel
  objetivo es `S_i = μ_i + z_i·σ_i`.
- `h` y `p` solo entran de forma implícita, a través de la elección de `z_i` por clase ABC. Las clases A
  (las de mayor margen y mayor costo de faltante) reciben un `z` más alto.
- En el artículo, el costo de inventario se mide *ex post* como **exceso** (`E`, capital inmovilizado a costo)
  y el de faltante como **venta perdida** (`VP`, margen no capturado). Ver la sección 4.

## 2. Variables de decisión y parámetros

| Símbolo | Columna en el código | Definición |
|---|---|---|
| `Q_i` | `cantidad_a_pedir` = `compra_sugerida_unid` | cantidad a pedir (variable de decisión) |
| `R_i` | `rop` | punto de reorden (variable de decisión informativa: ver la nota de la sección 3) |
| `S_i` | `nivel_objetivo` | nivel objetivo (order-up-to) |
| `H` | `horizonte_dias` = `ciclo['horizonte_pronostico_dias']` | horizonte de protección (141 d en el ciclo internacional, 40 d en el local) |
| `L` | `lead_time_dias` | lead time (36 d internacional, 10 d local) |
| `C` | `ciclo['duracion_dias']` | ciclo de revisión (105 d internacional, 30 d local); `H = C + L` |
| `z_i` | `z` = `z_nivel_servicio[clase_abc]` | A = 1,645 (α = 95 %), B = 1,2816 (α = 90 %), C = 1,0364 (α = 85 %); SKU sin clase → `z` de C |
| `s_i` | `stock_actual` | stock reconstruido al cierre del día anterior (NaN → 0) |
| `q^tr_i` | `stock_en_transito` | unidades de órdenes abiertas aún no recibidas (0 si no hay archivo) |

## 3. Fórmulas exactas (`calcular_recomendacion`)

Pronóstico del horizonte (de cada modelo): `\hat d_i = P50_i` (`demanda_p50_141d`) y `P90_i` (`demanda_p90_141d`).

```
σ_i            = max(0, (P90_i − P50_i) / 1.2816)                 # sigma_demanda_141d
SS_i           = max(0, z_i · σ_i)                                # stock_seguridad
d^L_i          = P50_i · L / H                                    # demanda_lead_time
SS^L_i         = SS_i · √( min(1, L / H) )                        # stock_seguridad_lead_time
R_i            = d^L_i + SS^L_i                                   # rop
S_i            = P50_i + SS_i                                     # nivel_objetivo
Q_i            = round( max(0, S_i − s_i − q^tr_i) )              # cantidad_a_pedir
```

`1.2816 = Φ⁻¹(0,90)`: los modelos construyen `P90 = P50 + 1.2816·σ`, y la recomendación recupera `σ` invirtiendo
esa relación.

**Nota sobre ROP y Q.** En el código la cantidad a pedir se calcula contra el **nivel objetivo** `S_i`, no contra
el ROP: `Q_i = max(0, S_i − s_i − q^tr_i)`. El ROP `R_i = d^L_i + SS_i·√(L/H)` solo sirve para la clasificación
de acciones y como referencia en la UI. La fórmula alternativa `ROP = \hat d + z·σ·√(L/H)`, `Q = max(0, ROP − s)`
**no** es la que ejecuta PREVÉ. Antes de la Fase 1, el "ROP" era en realidad el nivel objetivo (ítem C9 de
AUDITORIA.md), y hasta la Fase 5 la simulación what-if compraba `rop − stock` (ver el comentario en
`preve_simulacion.py`). Las dos cosas se corrigieron.

**Acción.** `decision = COMPRAR` solo si la acción es RECOMPRAR (plantilla de reglas o `_clasificar_accion`). En la
**simulación del artículo** (`Backtester.simular_compra`) se usa `Q_i` tal cual, sea cual sea la acción. Así,
E0, E1 y E2 se comparan con la misma regla y lo único que cambia es el pronóstico.

### Cómo se obtiene σ (la incertidumbre del pronóstico)

| Modelo | P50 | σ del horizonte |
|---|---|---|
| GBM (E1, E2 y el núcleo del Pipeline) | `μ_i · H/ht` (HistGradientBoosting, pérdida Poisson) | `σ_i = k·√(a·μ_i + b·μ_i²)·H/ht`. `(a, b)` se ajustan por mínimos cuadrados a los residuos² **fuera de muestra** (leave-one-corte-out sobre los cortes semanales de entrenamiento); `k = INFLACION_SIGMA = 1,0`; `ht = round(H/7)` |
| Naive (E0) y demás baselines | `tasa_semanal · H/7` | `σ_i = desvío_semanal · H/7` (p. ej. Naive: desvío de toda la serie semanal) |
| Croston-TSB / CatBoost (dentro del Pipeline) | ver `preve_pipeline.py` | `P90 = P50 + 1.2816·σ_horizonte` |

Para el artículo, la Tabla 12 compara la cobertura de este intervalo paramétrico con la de un intervalo
**empírico fuera de muestra** construido con los residuos del backtesting: `P10 = \hat d + q10(r)`,
`P90 = \hat d + q90(r)`, con `r = real − pronóstico` del mismo SKU en ventanas anteriores
(`preve_articulo.intervalos_oos`, ítem M8). Ese intervalo es de diagnóstico: **no** cambia `Q_i`.

## 4. Evaluación de la política (simulación retrospectiva)

Para cada fecha de pedido `t` del calendario dentro del rango del backtesting (`Backtester.dias_reporte`), con
la demanda observada del horizonte `d_i = Σ_{t}^{t+H} venta_i` (extrapolada si el horizonte está incompleto):

```
atendido_i  = min(d_i, s_i + Q_i)
faltante_i  = max(0, d_i − s_i − Q_i)
sobrante_i  = max(0, s_i + Q_i − d_i)
NS          = Σ atendido / Σ d × 100                       # nivel de servicio (%)
E           = Σ sobrante_i · c^u_i                        # exceso de inventario (S/.)
VP          = Σ faltante_i · m_i,   m_i = p_i / IGV − c^u_i # venta perdida (S/.)
I           = Σ Q_i · c^u_i                               # inversión (S/.)
EE          = E_{E0} − E_{modelo}                         # exceso evitado (S/.), base común
```

`d_i` también está censurada por los quiebres de stock, así que el `NS` simulado es una cota optimista (está
declarada como limitación en `metodologia.txt`).

## 5. Escenarios

| Escenario | Modelo (`preve_articulo.ESCENARIOS`) | Qué representa |
|---|---|---|
| **E0** (base) | `Naive` | Comprador sin sistema: repite la venta de la última semana × H/7. σ = desvío semanal histórico. |
| **E1** (propuesto sin innovación) | `GBM sin disponibilidad` | El mismo GBM, sin las variables de censura (`av4`, `av13`, `av26`, `stock`). |
| **E2** (propuesto con innovación) | `Pipeline (producción)` | El modelo completo de producción, con GBM con disponibilidad (ICD / `av_k` / stock). |

Los tres escenarios usan la misma regla `(R, S)` de la sección 3. Solo cambian `P50_i` y `P90_i`. La mejora en
precisión se reporta como

```
mejora_E2 = 100 × (WMAPE_E0 − WMAPE_E2) / WMAPE_E0
```

(`Tabla_11_escenarios.csv`, columna `mejora_wmape_vs_E0_pct`). La economía de la Tabla 11 se calcula sobre la
base común (sucursal, SKU, día de reporte) que los tres escenarios simularon.
