# -*- coding: utf-8 -*-
"""
PREVÉ — Historial persistente en SQLite.

Guarda cada ejecución del pipeline para:
  - Comparar pronósticos entre ciclos
  - Alimentar el scorecard de validación con datos históricos reales
  - Dar trazabilidad completa al usuario

Tablas:
  - ejecuciones: metadata de cada corrida (fecha, etiqueta, ciclo, sucursal)
  - recomendaciones: snapshot de la recomendación completa por SKU por ejecución
  - metricas: scorecard agregado por ejecución (si hubo validación)
"""
import os
import json
import sqlite3
import datetime
import pandas as pd


DB_NAME = 'preve_historial.db'


def _db_path(carpeta_salida):
    return os.path.join(carpeta_salida, DB_NAME)


def _conectar(carpeta_salida):
    path = _db_path(carpeta_salida)
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA journal_mode=WAL')
    _crear_tablas(conn)
    return conn


def _crear_tablas(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ejecuciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        sucursal TEXT NOT NULL DEFAULT 'San José',
        etiqueta TEXT,
        ciclo_numero INTEGER,
        dia_ciclo INTEGER,
        importacion_num INTEGER,
        skus_evaluados INTEGER,
        skus_recomprar INTEGER,
        inversion_total REAL,
        wmape REAL,
        calificacion TEXT,
        metadata_json TEXT
    );

    CREATE TABLE IF NOT EXISTS recomendaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ejecucion_id INTEGER NOT NULL,
        sku TEXT NOT NULL,
        nombre TEXT,
        categoria TEXT,
        clase_abc TEXT,
        accion TEXT,
        stock_actual REAL,
        demanda_p50 REAL,
        rop REAL,
        compra_sugerida REAL,
        costo_total REAL,
        proveedor_principal TEXT,
        margen_pct REAL,
        tendencia_venta REAL,
        FOREIGN KEY (ejecucion_id) REFERENCES ejecuciones(id)
    );

    CREATE TABLE IF NOT EXISTS metricas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ejecucion_id INTEGER NOT NULL,
        wmape REAL,
        medape REAL,
        sesgo_medio_pct REAL,
        cobertura_pct REAL,
        calificacion TEXT,
        quiebres_sin_preve INTEGER,
        quiebres_con_preve INTEGER,
        quiebres_evitados INTEGER,
        roi_pct REAL,
        beneficio_total REAL,
        detalle_json TEXT,
        FOREIGN KEY (ejecucion_id) REFERENCES ejecuciones(id)
    );

    CREATE TABLE IF NOT EXISTS decisiones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT NOT NULL,
        ciclo_numero INTEGER NOT NULL DEFAULT 0,
        sucursal TEXT NOT NULL DEFAULT 'San José',
        estado TEXT NOT NULL,          -- comprado | pospuesto | descartado
        razon TEXT,
        hasta TEXT,                    -- fecha ISO hasta la que se pospone
        fecha TEXT NOT NULL,
        UNIQUE (sku, ciclo_numero, sucursal)
    );

    CREATE INDEX IF NOT EXISTS idx_rec_ejecucion ON recomendaciones(ejecucion_id);
    CREATE INDEX IF NOT EXISTS idx_rec_sku ON recomendaciones(sku);
    """)
    conn.commit()


def registrar_ejecucion(carpeta_salida, recomendacion, info_ciclo=None,
                        sucursal='San José', scorecard=None, log=print):
    """Guarda una ejecución completa en el historial.

    Args:
        carpeta_salida: carpeta donde está la BD.
        recomendacion: DataFrame con la recomendación.
        info_ciclo: dict del ciclo (puede ser None).
        sucursal: nombre de la sucursal.
        scorecard: dict con resultado de validación (puede ser None).
        log: función de progreso.

    Returns:
        int: ID de la ejecución registrada.
    """
    conn = _conectar(carpeta_salida)

    etiqueta = info_ciclo.get('etiqueta', '') if info_ciclo else ''
    ciclo_num = info_ciclo.get('ciclo_numero', 0) if info_ciclo else 0
    dia_ciclo = info_ciclo.get('dia_ciclo', 0) if info_ciclo else 0
    import_num = info_ciclo.get('importacion_num', 0) if info_ciclo else 0

    n_recomprar = int((recomendacion['accion'] == 'RECOMPRAR').sum()) if 'accion' in recomendacion.columns else 0
    inversion = float(recomendacion.loc[
        recomendacion.get('accion', pd.Series()) == 'RECOMPRAR', 'costo_total_compra_pen'
    ].sum()) if 'accion' in recomendacion.columns else float(recomendacion['costo_total_compra_pen'].sum())

    wmape = None
    calificacion = None
    if scorecard and 'agregadas' in scorecard:
        wmape = scorecard['agregadas'].get('wmape')
        calificacion = scorecard['agregadas'].get('calificacion')

    cur = conn.execute("""
        INSERT INTO ejecuciones (fecha, sucursal, etiqueta, ciclo_numero, dia_ciclo,
            importacion_num, skus_evaluados, skus_recomprar, inversion_total,
            wmape, calificacion, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.datetime.now().isoformat(),
        sucursal, etiqueta, ciclo_num, dia_ciclo, import_num,
        len(recomendacion), n_recomprar, round(inversion, 2),
        wmape, calificacion,
        json.dumps(info_ciclo, default=str) if info_ciclo else None,
    ))
    ej_id = cur.lastrowid

    # Guardar recomendaciones
    cols_map = {
        'sku': 'sku', 'nombre': 'nombre', 'categoria': 'categoria',
        'clase_abc': 'clase_abc', 'accion': 'accion',
        'stock_actual': 'stock_actual', 'demanda_p50_141d': 'demanda_p50',
        'rop': 'rop', 'compra_sugerida_unid': 'compra_sugerida',
        'costo_total_compra_pen': 'costo_total',
        'proveedor_principal': 'proveedor_principal',
        'margen_pct': 'margen_pct', 'tendencia_venta': 'tendencia_venta',
    }
    available = {k: v for k, v in cols_map.items() if k in recomendacion.columns}
    sub = recomendacion[list(available.keys())].rename(columns=available).copy()
    sub['ejecucion_id'] = ej_id
    sub.to_sql('recomendaciones', conn, if_exists='append', index=False)

    # Guardar métricas si hay scorecard
    if scorecard:
        agr = scorecard.get('agregadas', {})
        contra = scorecard.get('contrafactual', {})
        roi_data = scorecard.get('roi', {})
        conn.execute("""
            INSERT INTO metricas (ejecucion_id, wmape, medape, sesgo_medio_pct,
                cobertura_pct, calificacion, quiebres_sin_preve, quiebres_con_preve,
                quiebres_evitados, roi_pct, beneficio_total, detalle_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ej_id, agr.get('wmape'), agr.get('medape'), agr.get('sesgo_medio_pct'),
            agr.get('cobertura_intervalo_pct'), agr.get('calificacion'),
            contra.get('quiebres_sin_preve'), contra.get('quiebres_con_preve'),
            contra.get('quiebres_evitados'), roi_data.get('roi_pct'),
            roi_data.get('beneficio_total_estimado'),
            json.dumps(scorecard, default=str),
        ))

    conn.commit()
    conn.close()
    log(f'Historial: ejecución #{ej_id} registrada ({sucursal}, {etiqueta}, {len(recomendacion)} SKUs)')
    return ej_id


def consultar_historial(carpeta_salida, sucursal=None, ultimas_n=10):
    """Consulta las últimas ejecuciones del historial.

    Returns:
        DataFrame con las ejecuciones.
    """
    conn = _conectar(carpeta_salida)
    query = "SELECT * FROM ejecuciones"
    params = []
    if sucursal:
        query += " WHERE sucursal = ?"
        params.append(sucursal)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(ultimas_n)
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def consultar_recomendacion_sku(carpeta_salida, sku, ultimas_n=5):
    """Consulta el historial de recomendaciones para un SKU específico.

    Returns:
        DataFrame con las recomendaciones históricas del SKU.
    """
    conn = _conectar(carpeta_salida)
    df = pd.read_sql_query("""
        SELECT r.*, e.fecha, e.sucursal, e.etiqueta, e.ciclo_numero
        FROM recomendaciones r
        JOIN ejecuciones e ON r.ejecucion_id = e.id
        WHERE r.sku = ?
        ORDER BY e.id DESC LIMIT ?
    """, conn, params=[sku, ultimas_n])
    conn.close()
    return df


def consultar_evolucion_metricas(carpeta_salida, sucursal=None):
    """Consulta la evolución de métricas de calidad a lo largo del tiempo.

    Returns:
        DataFrame con métricas por ejecución.
    """
    conn = _conectar(carpeta_salida)
    query = """
        SELECT e.id, e.fecha, e.sucursal, e.etiqueta, e.ciclo_numero,
               e.skus_evaluados, e.inversion_total,
               m.wmape, m.calificacion, m.quiebres_evitados, m.roi_pct
        FROM ejecuciones e
        LEFT JOIN metricas m ON m.ejecucion_id = e.id
    """
    params = []
    if sucursal:
        query += " WHERE e.sucursal = ?"
        params.append(sucursal)
    query += " ORDER BY e.id"
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


# ══════════════════════════════════════════════════════════════════
# Decisiones del usuario sobre cada recomendación (comprado / posponer / descartar)
# ══════════════════════════════════════════════════════════════════
def historial_existe(carpeta_salida):
    return os.path.exists(_db_path(carpeta_salida))


def guardar_decision(carpeta_salida, sku, ciclo, estado, razon='', hasta=None, sucursal='San José'):
    """Guarda (o reemplaza) la decisión del usuario sobre un producto en un ciclo."""
    conn = _conectar(carpeta_salida)
    conn.execute("""
        INSERT INTO decisiones (sku, ciclo_numero, sucursal, estado, razon, hasta, fecha)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sku, ciclo_numero, sucursal) DO UPDATE SET
            estado = excluded.estado, razon = excluded.razon,
            hasta = excluded.hasta, fecha = excluded.fecha
    """, (str(sku), int(ciclo), sucursal, estado, razon or '', hasta,
          datetime.datetime.now().isoformat()))
    conn.commit()
    conn.close()


def borrar_decision(carpeta_salida, sku, ciclo, sucursal='San José'):
    conn = _conectar(carpeta_salida)
    conn.execute("DELETE FROM decisiones WHERE sku = ? AND ciclo_numero = ? AND sucursal = ?",
                 (str(sku), int(ciclo), sucursal))
    conn.commit()
    conn.close()


def cargar_decisiones(carpeta_salida, ciclo, sucursal='San José'):
    """{sku: {'estado', 'razon', 'hasta', 'fecha'}} del ciclo indicado."""
    if not historial_existe(carpeta_salida):
        return {}
    conn = _conectar(carpeta_salida)
    filas = conn.execute(
        "SELECT sku, estado, razon, hasta, fecha FROM decisiones WHERE ciclo_numero = ? AND sucursal = ?",
        (int(ciclo), sucursal)).fetchall()
    conn.close()
    return {f[0]: {'estado': f[1], 'razon': f[2] or '', 'hasta': f[3], 'fecha': f[4]} for f in filas}


# ══════════════════════════════════════════════════════════════════
# Consultas para la interfaz (comparación, scorecard, búsqueda)
# ══════════════════════════════════════════════════════════════════
def comparar_ultimas(carpeta_salida):
    """Compara la última ejecución con la anterior.

    Returns None si no hay historial; si no hay anterior, 'previa' es None.
    """
    if not historial_existe(carpeta_salida):
        return None
    conn = _conectar(carpeta_salida)
    filas = conn.execute(
        "SELECT id, fecha, inversion_total, skus_recomprar, etiqueta FROM ejecuciones ORDER BY id DESC LIMIT 2"
    ).fetchall()
    if not filas:
        conn.close()
        return None
    def _d(f):
        return {'id': f[0], 'fecha': f[1], 'inversion': f[2] or 0.0, 'recomprar': f[3] or 0, 'etiqueta': f[4] or ''}
    actual = _d(filas[0])
    previa = _d(filas[1]) if len(filas) > 1 else None
    por_sku = {}
    if previa:
        por_sku = dict(conn.execute(
            "SELECT sku, compra_sugerida FROM recomendaciones WHERE ejecucion_id = ?", (previa['id'],)).fetchall())
    conn.close()
    return {'actual': actual, 'previa': previa, 'previa_por_sku': por_sku}


def ultimo_scorecard(carpeta_salida):
    """{'wmape', 'calificacion'} de la última validación real, o None."""
    if not historial_existe(carpeta_salida):
        return None
    conn = _conectar(carpeta_salida)
    f = conn.execute("SELECT wmape, calificacion FROM metricas WHERE wmape IS NOT NULL ORDER BY id DESC LIMIT 1"
                     ).fetchone()
    conn.close()
    return {'wmape': f[0], 'calificacion': f[1]} if f else None


def ejecuciones_con_diff(carpeta_salida, ultimas_n=50):
    """Ejecuciones recientes con la variación de inversión respecto a la ejecución anterior."""
    if not historial_existe(carpeta_salida):
        return pd.DataFrame()
    conn = _conectar(carpeta_salida)
    df = pd.read_sql_query("SELECT * FROM ejecuciones ORDER BY id DESC LIMIT ?", conn, params=[ultimas_n + 1])
    conn.close()
    if df.empty:
        return df
    df['inversion_prev'] = df['inversion_total'].shift(-1)
    df['recomprar_prev'] = df['skus_recomprar'].shift(-1)
    return df.head(ultimas_n)


def buscar_en_historial(carpeta_salida, texto, limite=6):
    """Productos que coinciden con el texto en recomendaciones pasadas (último registro por SKU)."""
    if not historial_existe(carpeta_salida) or not texto.strip():
        return []
    q = f'%{texto.strip()}%'
    conn = _conectar(carpeta_salida)
    filas = conn.execute("""
        SELECT r.sku, r.nombre, r.accion, r.compra_sugerida, e.fecha, COUNT(*) AS veces
        FROM recomendaciones r JOIN ejecuciones e ON e.id = r.ejecucion_id
        WHERE r.nombre LIKE ? OR r.sku LIKE ?
        GROUP BY r.sku
        ORDER BY MAX(e.id) DESC LIMIT ?
    """, (q, q, limite)).fetchall()
    conn.close()
    return [{'sku': f[0], 'nombre': f[1], 'accion': f[2], 'compra': f[3], 'fecha': f[4], 'veces': f[5]}
            for f in filas]
