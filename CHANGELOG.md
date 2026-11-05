# Changelog

Todos los cambios relevantes de PREVÉ, organizados por fase.

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
