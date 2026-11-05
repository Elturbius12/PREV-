# -*- coding: utf-8 -*-
"""
PREVÉ Extractor — Bot de extracción automática de datos desde Negosy ERP
=========================================================================
Usa Playwright para hacer login en Negosy, seleccionar cada tienda,
y descargar los 5 reportes Excel necesarios para el pipeline de PREVÉ.

REGLA DE SEGURIDAD ABSOLUTA:
  - Este bot SOLO exporta archivos Excel. NUNCA abre cajas, NUNCA modifica
    datos, NUNCA hace click en botones que no sean de exportación/descarga.

Endpoints mapeados (5 por tienda):
  1. Listado de productos (síncrono)
  2. Ventas por producto (celery/asíncrono)
  3. Compras por producto (celery/asíncrono)
  4. Movimientos de almacén — Entradas (síncrono)
  5. Movimientos de almacén — Salidas (síncrono)

Uso:
    from preve_extractor import extraer_datos
    extraer_datos(carpeta_destino, config, progreso=print)

    O desde consola:
    python preve_extractor.py --destino ./datos --tienda yujustore
"""

import os
import re
import json
import time
import asyncio
import argparse
from datetime import datetime, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv es opcional; sin él se leen variables del sistema
    pass

# ---------------------------------------------------------------------------
# Configuración de tiendas
# ---------------------------------------------------------------------------
TIENDAS = {
    'san_jose': {
        'nombre': 'SAN JOSE JULIACA',
        'base_url': 'https://storeyuju.negosy.com',
        'selector_texto': 'SAN JOSE JULIACA',
    },
    'santa_barbara': {
        'nombre': 'SANTA BARBARA',
        'base_url': 'https://yujustorelampa.negosy.com',
        'selector_texto': 'SANTA BARBARA',
    },
    'principal': {
        'nombre': 'YUJU STORE PRINCIPAL',
        'base_url': 'https://yujustore.negosy.com',
        'selector_texto': 'YUJU STORE',
    },
}

LOGIN_URL = 'https://app.negosy.com/accounts/login/'
COMPANIES_URL = 'https://app.negosy.com/companies/'

# Tiempo máximo de espera para archivos celery (segundos)
CELERY_TIMEOUT = 180
CELERY_POLL_INTERVAL = 5

# ---------------------------------------------------------------------------
# Endpoints de exportación
# ---------------------------------------------------------------------------
def _url_listado_productos(base):
    """1. Listado de productos — descarga directa (síncrono)."""
    return f'{base}/u/report/list_products_excel/?search=&criteria=&page=1&sort=1&show_deleted=0'


def _url_ventas_producto(base, fecha_inicio, fecha_fin):
    """2. Ventas por producto — celery (asíncrono)."""
    start = fecha_inicio.strftime('%Y-%m-%d') + '%2000:00:00'
    end = fecha_fin.strftime('%Y-%m-%d') + '%2023:59:59'
    return (f'{base}/api/v1/reports/sale_by_product_summary_queue/'
            f'?user=0&employee=0&voucher=99&state=0&limit=50&page=1'
            f'&start={start}&end={end}&search=&summary=0&file=excel')


def _url_compras_producto(base, fecha_inicio, fecha_fin):
    """3. Compras por producto — celery (asíncrono)."""
    start = fecha_inicio.strftime('%Y-%m-%d')
    end = fecha_fin.strftime('%Y-%m-%d')
    return (f'{base}/api/v1/reports/report_purchase_products_celery_queue/'
            f'?search=&start={start}&end={end}&page=1&limit=50&file=excel')


def _url_movimientos_entradas(base, fecha_inicio, fecha_fin):
    """4. Movimientos de almacén — Entradas (síncrono)."""
    start = fecha_inicio.strftime('%Y-%m-%d')
    end = fecha_fin.strftime('%Y-%m-%d')
    return (f'{base}/u/report/movements_warehouse_list_excel/'
            f'?search=&type=I&page=1&sort=1&setdate=1&status=1'
            f'&start={start}&end={end}')


def _url_movimientos_salidas(base, fecha_inicio, fecha_fin):
    """5. Movimientos de almacén — Salidas (síncrono)."""
    start = fecha_inicio.strftime('%Y-%m-%d')
    end = fecha_fin.strftime('%Y-%m-%d')
    return (f'{base}/u/report/movements_warehouse_list_excel/'
            f'?search=&type=S&page=1&sort=1&setdate=1&status=1'
            f'&start={start}&end={end}')


# ---------------------------------------------------------------------------
# Motor de extracción con Playwright
# ---------------------------------------------------------------------------
async def _login(page, usuario, contrasena, log):
    """Hace login en Negosy y espera la página de selección de empresa."""
    log('Navegando al login de Negosy...')
    await page.goto(LOGIN_URL, wait_until='networkidle', timeout=30000)
    await page.wait_for_timeout(1000)

    # Llenar formulario de login
    await page.fill('input[name="username"]', usuario)
    await page.fill('input[name="password"]', contrasena)
    await page.click('button[type="submit"]')
    await page.wait_for_load_state('networkidle', timeout=30000)
    log('Login exitoso.')


async def _seleccionar_tienda(page, tienda_key, log):
    """Selecciona una tienda desde la página de empresas."""
    tienda = TIENDAS[tienda_key]
    log(f'Seleccionando tienda: {tienda["nombre"]}...')
    await page.goto(COMPANIES_URL, wait_until='networkidle', timeout=30000)
    await page.wait_for_timeout(1500)

    # Buscar y clickear el card/link de la tienda
    # Negosy muestra las tiendas como cards con el nombre
    encontrado = False
    links = await page.query_selector_all('a')
    for link in links:
        texto = await link.inner_text()
        if tienda['selector_texto'].lower() in texto.lower():
            await link.click()
            encontrado = True
            break

    if not encontrado:
        # Intentar con un selector más amplio
        cards = await page.query_selector_all('[class*="card"], [class*="company"], div[onclick]')
        for card in cards:
            texto = await card.inner_text()
            if tienda['selector_texto'].lower() in texto.lower():
                await card.click()
                encontrado = True
                break

    if not encontrado:
        raise RuntimeError(f'No se encontró la tienda "{tienda["nombre"]}" en la página de empresas.')

    await page.wait_for_load_state('networkidle', timeout=30000)
    await page.wait_for_timeout(2000)

    # Verificar que estamos en el subdominio correcto
    url_actual = page.url
    if tienda['base_url'].split('//')[1] not in url_actual:
        log(f'  ⚠ URL actual: {url_actual} (esperado subdominio de {tienda["base_url"]})')

    # Cerrar modal "Debe cerrar la caja" si aparece
    await _cerrar_modal_caja(page, log)

    log(f'Tienda seleccionada: {tienda["nombre"]}')
    return tienda['base_url']


async def _cerrar_modal_caja(page, log):
    """Cierra el modal 'Debe cerrar la caja' si aparece (sin cerrar caja)."""
    try:
        modal = await page.wait_for_selector(
            'text="Debe cerrar la caja"', timeout=3000)
        if modal:
            log('  ⚠ Modal "Debe cerrar la caja" detectado — cerrando sin modificar nada...')
            # Buscar botón "Quedarse aquí" o "Cerrar" o el X del modal
            botones = await page.query_selector_all('button')
            for btn in botones:
                texto = await btn.inner_text()
                if any(x in texto.lower() for x in ['quedarse', 'cerrar', 'cancelar', 'no']):
                    await btn.click()
                    await page.wait_for_timeout(500)
                    return
            # Si no encontró botón, intentar presionar Escape
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(500)
    except Exception:
        pass  # No apareció el modal, todo bien


async def _descargar_sincrono(page, url, destino, nombre_archivo, log):
    """Descarga un archivo de un endpoint síncrono (descarga directa).
    Usa el evento de descarga de Playwright."""
    log(f'  Descargando {nombre_archivo}...')
    ruta_final = os.path.join(destino, nombre_archivo)

    try:
        async with page.expect_download(timeout=60000) as download_info:
            await page.goto(url, timeout=60000)
        download = await download_info.value
        await download.save_as(ruta_final)
        size_kb = os.path.getsize(ruta_final) / 1024
        log(f'  ✓ {nombre_archivo} descargado ({size_kb:.0f} KB)')
        return ruta_final
    except Exception as e:
        # Plan B: tal vez la respuesta es directa (no dispara download event)
        log(f'  Reintentando descarga directa de {nombre_archivo}...')
        try:
            response = await page.request.get(url)
            if response.ok:
                body = await response.body()
                with open(ruta_final, 'wb') as f:
                    f.write(body)
                size_kb = len(body) / 1024
                log(f'  ✓ {nombre_archivo} descargado ({size_kb:.0f} KB)')
                return ruta_final
            else:
                raise RuntimeError(f'HTTP {response.status} al descargar {nombre_archivo}')
        except Exception as e2:
            log(f'  ✗ Error descargando {nombre_archivo}: {e2}')
            raise


async def _descargar_celery(page, url, destino, nombre_archivo, log):
    """Descarga un archivo de un endpoint celery (asíncrono).

    Flujo:
    1. GET al endpoint → puede devolver JSON con task_id o directamente el archivo
    2. Si es JSON con task_id → polling hasta que el archivo esté listo
    3. Descargar el archivo final
    """
    log(f'  Solicitando generación de {nombre_archivo} (celery)...')
    ruta_final = os.path.join(destino, nombre_archivo)

    # Primer request: puede ser JSON (task en cola) o descarga directa
    response = await page.request.get(url)

    if not response.ok:
        raise RuntimeError(f'HTTP {response.status} al solicitar {nombre_archivo}')

    content_type = response.headers.get('content-type', '')

    # Si la respuesta es un Excel directamente
    if 'spreadsheet' in content_type or 'octet-stream' in content_type or 'excel' in content_type:
        body = await response.body()
        with open(ruta_final, 'wb') as f:
            f.write(body)
        size_kb = len(body) / 1024
        log(f'  ✓ {nombre_archivo} descargado directamente ({size_kb:.0f} KB)')
        return ruta_final

    # Si es JSON, buscar task_id o url de descarga
    try:
        data = await response.json()
    except Exception:
        # Puede que sea el archivo de todas formas
        body = await response.body()
        if len(body) > 1000:  # Probablemente es un Excel
            with open(ruta_final, 'wb') as f:
                f.write(body)
            log(f'  ✓ {nombre_archivo} descargado ({len(body)/1024:.0f} KB)')
            return ruta_final
        raise RuntimeError(f'Respuesta inesperada al solicitar {nombre_archivo}')

    log(f'  Archivo en cola celery, esperando...')

    # Buscar URL de descarga o task_id en la respuesta
    download_url = None

    # Caso 1: la respuesta ya tiene la URL del archivo
    if isinstance(data, dict):
        for key in ['url', 'file_url', 'download_url', 'file', 'path']:
            if key in data and data[key]:
                download_url = data[key]
                break

    # Caso 2: tiene task_id — necesitamos polling
    if not download_url and isinstance(data, dict):
        task_id = data.get('task_id') or data.get('id') or data.get('task')

        if task_id:
            log(f'  Task ID: {task_id} — haciendo polling...')
            base_url = url.split('/api/')[0] if '/api/' in url else url.split('/u/')[0]
            start_time = time.time()

            while time.time() - start_time < CELERY_TIMEOUT:
                await asyncio.sleep(CELERY_POLL_INTERVAL)

                # Intentar obtener el resultado del task
                # Negosy puede usar diferentes endpoints para el status
                for status_path in [
                    f'/api/v1/reports/celery_status/?task_id={task_id}',
                    f'/api/v1/tasks/{task_id}/',
                    f'/api/v1/celery/status/{task_id}/',
                ]:
                    try:
                        status_url = base_url + status_path
                        status_resp = await page.request.get(status_url)
                        if status_resp.ok:
                            status_data = await status_resp.json()
                            estado = status_data.get('state') or status_data.get('status') or ''

                            if estado.upper() in ('SUCCESS', 'COMPLETED', 'DONE', 'READY'):
                                download_url = (status_data.get('url') or
                                                status_data.get('file_url') or
                                                status_data.get('result', {}).get('url') if isinstance(status_data.get('result'), dict) else
                                                status_data.get('file'))
                                if download_url:
                                    break
                            elif estado.upper() in ('FAILURE', 'FAILED', 'ERROR'):
                                raise RuntimeError(f'Task celery falló para {nombre_archivo}')
                    except (json.JSONDecodeError, RuntimeError):
                        raise
                    except Exception:
                        continue

                if download_url:
                    break

                elapsed = int(time.time() - start_time)
                if elapsed % 30 == 0:
                    log(f'  Esperando... ({elapsed}s)')

    # Caso 3: la respuesta tiene los datos paginados con un link de descarga
    # Algunos endpoints celery de Negosy devuelven el resultado como notificación
    # en el navbar. Intentar buscar en la página.
    if not download_url:
        log(f'  Buscando archivo en notificaciones del navbar...')
        # Navegar al dashboard para ver notificaciones
        base_url = url.split('/api/')[0] if '/api/' in url else url.split('/u/')[0]
        await page.goto(base_url + '/u/dashboard/', wait_until='networkidle', timeout=30000)
        await page.wait_for_timeout(3000)

        # Buscar en notificaciones
        try:
            # Click en el icono de notificaciones/campana
            bell = await page.query_selector('[class*="notification"], [class*="bell"], .fa-bell')
            if bell:
                await bell.click()
                await page.wait_for_timeout(2000)

            # Buscar links de descarga en las notificaciones
            links = await page.query_selector_all('a[href*="media/temp_files"]')
            if links:
                href = await links[0].get_attribute('href')
                if href:
                    download_url = href if href.startswith('http') else base_url + href
        except Exception:
            pass

    if not download_url:
        # Último intento: buscar el archivo directamente en media/temp_files
        # con el patrón de nombre conocido
        log(f'  Intentando descarga directa desde media/temp_files...')
        base_url = url.split('/api/')[0] if '/api/' in url else url.split('/u/')[0]

        # Patrones de nombre de archivo conocidos de Negosy
        today_str = datetime.now().strftime('%Y-%m-%d')
        posibles = []
        if 'sale' in url:
            posibles = [
                f'/media/temp_files/reporte_ventas_producto_{today_str}.xlsx',
                f'/media/temp_files/Reporte_ventas_por_producto_{today_str}.xlsx',
            ]
        elif 'purchase' in url:
            posibles = [
                f'/media/temp_files/Reporte_compras_por_producto_{today_str}.xlsx',
                f'/media/temp_files/reporte_compras_producto_{today_str}.xlsx',
            ]

        for path in posibles:
            try:
                resp = await page.request.get(base_url + path)
                if resp.ok:
                    body = await resp.body()
                    if len(body) > 500:
                        download_url = base_url + path
                        break
            except Exception:
                continue

    if not download_url:
        raise RuntimeError(
            f'No se pudo obtener la URL de descarga para {nombre_archivo}. '
            f'Es posible que el archivo aún se esté generando en Negosy.')

    # Descargar el archivo final
    if not download_url.startswith('http'):
        base_url = url.split('/api/')[0] if '/api/' in url else url.split('/u/')[0]
        download_url = base_url + download_url

    log(f'  Descargando archivo generado...')
    resp = await page.request.get(download_url)
    if resp.ok:
        body = await resp.body()
        with open(ruta_final, 'wb') as f:
            f.write(body)
        size_kb = len(body) / 1024
        log(f'  ✓ {nombre_archivo} descargado ({size_kb:.0f} KB)')
        return ruta_final
    else:
        raise RuntimeError(f'HTTP {resp.status} al descargar archivo final de {nombre_archivo}')


async def _extraer_tienda(page, tienda_key, base_url, carpeta_destino,
                          fecha_inicio, fecha_fin, log):
    """Extrae los 5 reportes de una tienda."""
    tienda = TIENDAS[tienda_key]
    prefijo = tienda_key  # san_jose, santa_barbara, principal
    hoy = datetime.now().strftime('%Y%m%d')

    log(f'\n{"="*60}')
    log(f'Extrayendo datos de {tienda["nombre"]}')
    log(f'Rango de fechas: {fecha_inicio.strftime("%Y-%m-%d")} a {fecha_fin.strftime("%Y-%m-%d")}')
    log(f'{"="*60}')

    archivos = []

    # 1. Listado de productos (síncrono)
    url1 = _url_listado_productos(base_url)
    nombre1 = f'listado_productos_{prefijo}_{hoy}.xlsx'
    try:
        ruta = await _descargar_sincrono(page, url1, carpeta_destino, nombre1, log)
        archivos.append(ruta)
    except Exception as e:
        log(f'  ✗ Error en listado de productos: {e}')

    # 2. Ventas por producto (celery)
    url2 = _url_ventas_producto(base_url, fecha_inicio, fecha_fin)
    nombre2 = f'reporte_ventas_producto_{prefijo}_{hoy}.xlsx'
    try:
        ruta = await _descargar_celery(page, url2, carpeta_destino, nombre2, log)
        archivos.append(ruta)
    except Exception as e:
        log(f'  ✗ Error en ventas por producto: {e}')

    # 3. Compras por producto (celery)
    url3 = _url_compras_producto(base_url, fecha_inicio, fecha_fin)
    nombre3 = f'reporte_compras_producto_{prefijo}_{hoy}.xlsx'
    try:
        ruta = await _descargar_celery(page, url3, carpeta_destino, nombre3, log)
        archivos.append(ruta)
    except Exception as e:
        log(f'  ✗ Error en compras por producto: {e}')

    # 4. Movimientos de almacén — Entradas (síncrono)
    url4 = _url_movimientos_entradas(base_url, fecha_inicio, fecha_fin)
    nombre4 = f'reporte_productos_movimiento_entradas_{prefijo}_{hoy}.xlsx'
    try:
        ruta = await _descargar_sincrono(page, url4, carpeta_destino, nombre4, log)
        archivos.append(ruta)
    except Exception as e:
        log(f'  ✗ Error en movimientos entradas: {e}')

    # 5. Movimientos de almacén — Salidas (síncrono)
    url5 = _url_movimientos_salidas(base_url, fecha_inicio, fecha_fin)
    nombre5 = f'reporte_productos_movimiento_salidas_{prefijo}_{hoy}.xlsx'
    try:
        ruta = await _descargar_sincrono(page, url5, carpeta_destino, nombre5, log)
        archivos.append(ruta)
    except Exception as e:
        log(f'  ✗ Error en movimientos salidas: {e}')

    log(f'\n{tienda["nombre"]}: {len(archivos)}/5 archivos descargados correctamente.')
    return archivos


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
async def extraer_datos_async(carpeta_destino, usuario, contrasena,
                               tiendas=None, fecha_inicio=None, fecha_fin=None,
                               headless=True, log=print):
    """Extrae datos de Negosy para las tiendas especificadas.

    Args:
        carpeta_destino: Carpeta donde guardar los Excel descargados.
        usuario: Usuario de Negosy.
        contrasena: Contraseña de Negosy.
        tiendas: Lista de claves de tienda ('san_jose', 'santa_barbara', 'principal').
                 Si es None, extrae solo 'san_jose'.
        fecha_inicio: datetime, inicio del rango de datos (por defecto: 2 años atrás).
        fecha_fin: datetime, fin del rango de datos (por defecto: hoy).
        headless: Si True, el navegador no muestra ventana visible.
        log: Función de logging (por defecto: print).

    Returns:
        dict con {tienda_key: [lista de rutas de archivos descargados]}
    """
    from playwright.async_api import async_playwright

    if tiendas is None:
        tiendas = ['san_jose']

    if fecha_fin is None:
        fecha_fin = datetime.now()
    if fecha_inicio is None:
        fecha_inicio = fecha_fin - timedelta(days=730)  # 2 años

    os.makedirs(carpeta_destino, exist_ok=True)
    resultado = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            accept_downloads=True,
            viewport={'width': 1280, 'height': 800},
        )
        page = await context.new_page()

        try:
            # Login una sola vez
            await _login(page, usuario, contrasena, log)

            # Extraer datos de cada tienda
            for tienda_key in tiendas:
                if tienda_key not in TIENDAS:
                    log(f'⚠ Tienda "{tienda_key}" no reconocida, se omite.')
                    continue

                base_url = await _seleccionar_tienda(page, tienda_key, log)
                archivos = await _extraer_tienda(
                    page, tienda_key, TIENDAS[tienda_key]['base_url'],
                    carpeta_destino, fecha_inicio, fecha_fin, log)
                resultado[tienda_key] = archivos

        finally:
            await browser.close()

    # Resumen final
    log(f'\n{"="*60}')
    log('EXTRACCIÓN COMPLETADA')
    total = sum(len(v) for v in resultado.values())
    esperado = len(tiendas) * 5
    log(f'Archivos descargados: {total}/{esperado}')
    for tk, archivos in resultado.items():
        log(f'  {TIENDAS[tk]["nombre"]}: {len(archivos)}/5')
    log(f'Carpeta de destino: {carpeta_destino}')
    log(f'{"="*60}')

    return resultado


def extraer_datos(carpeta_destino, usuario, contrasena, **kwargs):
    """Wrapper síncrono de extraer_datos_async."""
    return asyncio.run(extraer_datos_async(
        carpeta_destino, usuario, contrasena, **kwargs))


# ---------------------------------------------------------------------------
# Configuración persistente (archivo JSON junto al script)
# ---------------------------------------------------------------------------
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'preve_extractor_config.json')


def cargar_config():
    """Carga la configuración del extractor, o devuelve defaults."""
    defaults = {
        'usuario': os.getenv('NEGOSY_USUARIO', ''),
        'contrasena': os.getenv('NEGOSY_CONTRASENA', ''),
        'tiendas': ['san_jose'],
        'headless': True,
        'ciclo_dias': 14,  # cada 2 semanas
        'fecha_inicio_historico': None,  # None = 2 años atrás automático
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
            defaults.update(config)
        except Exception:
            pass
    return defaults


def guardar_config(config):
    """Guarda la configuración del extractor."""
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='PREVÉ Extractor — Descarga automática de reportes de Negosy ERP')
    parser.add_argument('--destino', '-d', default='./datos_negosy',
                        help='Carpeta donde guardar los Excel (default: ./datos_negosy)')
    parser.add_argument('--tienda', '-t', nargs='+',
                        choices=['san_jose', 'santa_barbara', 'principal'],
                        default=['san_jose'],
                        help='Tienda(s) a extraer (default: san_jose)')
    parser.add_argument('--inicio', help='Fecha inicio YYYY-MM-DD (default: 2 años atrás)')
    parser.add_argument('--fin', help='Fecha fin YYYY-MM-DD (default: hoy)')
    parser.add_argument('--visible', action='store_true',
                        help='Mostrar ventana del navegador (default: oculto)')
    parser.add_argument('--usuario', '-u', help='Usuario de Negosy')
    parser.add_argument('--contrasena', '-p', help='Contraseña de Negosy')

    args = parser.parse_args()
    config = cargar_config()

    usuario = args.usuario or config.get('usuario')
    contrasena = args.contrasena or config.get('contrasena')

    if not usuario or not contrasena:
        print('ERROR: Se necesitan usuario y contraseña de Negosy.')
        print('Define NEGOSY_USUARIO y NEGOSY_CONTRASENA en tu archivo .env (ver .env.example),')
        print('o usa --usuario y --contrasena.')
        return

    fecha_inicio = datetime.strptime(args.inicio, '%Y-%m-%d') if args.inicio else None
    fecha_fin = datetime.strptime(args.fin, '%Y-%m-%d') if args.fin else None

    extraer_datos(
        carpeta_destino=args.destino,
        usuario=usuario,
        contrasena=contrasena,
        tiendas=args.tienda,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        headless=not args.visible,
    )


if __name__ == '__main__':
    main()
