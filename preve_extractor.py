# -*- coding: utf-8 -*-
"""Alias de compatibilidad: el extractor de Negosy ahora vive en plugins/negosy/extractor.py (Fase 4).

Se mantiene para no romper `python preve_extractor.py ...` ni `from preve_extractor import ...`.
"""
from plugins.negosy.extractor import *  # noqa: F401,F403
from plugins.negosy.extractor import (  # noqa: F401  (nombres usados por tests y scripts)
    TIENDAS, boton_modal_permitido, carpeta_descarga, extraer_datos, extraer_download_url, main,
)

if __name__ == '__main__':
    main()
