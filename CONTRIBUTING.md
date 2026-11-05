# Cómo contribuir a PREVÉ

## Preparar el entorno

```bash
python -m venv .venv && .venv\Scripts\activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Reglas de la casa:
- **Nunca inventar un número:** si falta un dato, NaN / "—" / "pendiente", nunca un supuesto.
- **SQL solo en `database.py`.**
- **Parámetros en la configuración,** no en el código (`preve_config.DEFAULTS` o `ciclos_compra`).
- **Nada de `except Exception: pass`:** capturar por tipo y registrar con `logger.exception`.
- **Cada bug corregido lleva un comentario `# FIX:` y un test** que lo reproduce.
- **Datos reales fuera del repositorio:** los tests usan datos sintéticos y una base temporal (ver `tests/conftest.py`).

## Agregar un plugin de ERP

1. Crea el paquete `plugins/<miErp>/__init__.py`:

```python
from plugins.base import PluginERP, registrar


@registrar
class MiErpPlugin(PluginERP):
    clave = 'mi_erp'                       # se guarda en configuracion['plugin_erp']
    nombre = 'Mi ERP'
    descripcion = 'Descarga ventas, compras, listado y movimientos desde Mi ERP.'
    variables_credenciales = {'usuario': 'MIERP_USUARIO', 'token': 'MIERP_TOKEN'}   # leídas del .env

    def opciones_disponibles(self):
        return {'tiendas': ['central', 'norte']}

    def extraer(self, carpeta_destino, opciones=None, log=print):
        cred = self.credenciales()
        # ... descargar los reportes a una subcarpeta nueva de carpeta_destino ...
        return carpeta_creada
```

2. `extraer()` debe dejar archivos que el pipeline reconozca por su nombre (`listado…productos`, `reporte…ventas…producto`, `reporte…compras…producto`, `reporte…productos…movimiento`). Si las columnas son distintas a las de Negosy, el usuario las mapea en *Configuración*; también puede usarse la importación manual.
3. Agrega las variables a `.env.example`.
4. No hay que registrarlo en ningún otro lado: `plugins.descubrir_plugins()` importa cada subpaquete. Un plugin que falla al importar se registra en el log y no impide que la app arranque.
5. Tests: mockea la descarga (ver `tests/test_fase4.py::test_plugin_negosy_exige_credenciales_y_usa_subcarpeta`).

**Regla de seguridad:** un plugin solo **lee y exporta**. Nunca hace clic en botones que modifiquen datos del ERP (ver la lista blanca de `plugins/negosy/extractor.py`).

## Agregar o modificar una plantilla de categorías

**Desde la app:** *Configuración → Categorías de acción → Editar reglas…*. Las plantillas del sistema se guardan como copia con otro nombre.

**En código:** agrega un dict en `seeds/plantillas_categorias.py` y súmalo a `PLANTILLAS_SISTEMA`:

```python
PLANTILLA_MAYORISTA = {
    'nombre': 'MAYORISTA',
    'version': 1,
    'descripcion': 'Venta por volumen …',
    'acciones_de_compra': ['REPONER'],          # suman a la inversión y a las notas de compra
    'accion_por_defecto': 'MANTENER',
    'motivo_por_defecto': 'Stock y demanda equilibrados.',
    'reglas': [                                  # gana la PRIMERA que se cumple; condiciones unidas con Y
        {'accion': 'DESCONTINUAR', 'descripcion': 'Sin ventas en 180 días',
         'condiciones': [c('dias_sin_venta', '>=', 180)]},
        {'accion': 'REPONER', 'descripcion': 'No llega al próximo pedido',
         'condiciones': [c('stock_dias', '<', var('lead_time'))]},
    ],
}
```

- Variables disponibles: `preve_reglas.VARIABLES`. Operadores: `<`, `<=`, `>`, `>=`, `==`, `!=`.
- El valor puede ser un número o `var('otra_variable', factor)`.
- Para cambiar una plantilla del sistema que ya se usa, **sube su `version`**: `sembrar()` actualiza las bases existentes.
- Variable nueva: agrégala a `VARIABLES` y calcúlala en `preve_reglas.calcular_variables()` (NaN si no hay dato: así no cumple ninguna condición).
- Agrega un test con casos por acción (ver `tests/test_fase5.py::test_reglas_retail`).

## Cambiar el esquema de la base

Agrega `_migracion_00N(conn)` y su entrada al final de `database.MIGRACIONES`. No modifiques migraciones ya publicadas.

## Commits

- Un commit por cambio coherente: `fix(...)`, `feat(...)`, `chore(...)`.
- Registra los cambios relevantes en `CHANGELOG.md`.
- `pytest` debe pasar antes de cada commit.
