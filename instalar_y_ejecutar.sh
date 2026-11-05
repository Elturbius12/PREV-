#!/usr/bin/env bash
# Alias de compatibilidad (Fase 7): la instalación ahora vive en instalar.sh
exec "$(dirname "$0")/instalar.sh" "$@"
