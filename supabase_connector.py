"""
supabase_connector.py
─────────────────────
Lectura y escritura del histórico de mampostería en una tabla de Supabase
(PostgreSQL gestionado, con plan gratuito). Expone la MISMA interfaz que
`sharepoint_connector` para que el resto de la app no dependa del backend:

    disponible()              -> bool   (¿hay credenciales válidas?)
    leer_datos()              -> pd.DataFrame
    insertar_registros(df)    -> None   (append eficiente: solo filas nuevas)
    guardar_datos(df)         -> None   (reemplaza TODO el histórico; migraciones)
    leer_salidas()            -> pd.DataFrame  (vales de salida de almacén)
    insertar_salidas(df)      -> None   (append de salidas de almacén)
    leer_entradas()           -> pd.DataFrame  (remisiones de entrada de almacén)
    insertar_entradas(df)     -> None   (append de entradas de almacén)

Credenciales en st.secrets (.streamlit/secrets.toml):

    SUPABASE_URL   = "https://xxxxxxxx.supabase.co"
    SUPABASE_KEY   = "eyJhbGciOi..."           # anon o service_role
    SUPABASE_TABLE = "registros_mamposteria"   # opcional (este es el valor por defecto)
"""

import math

import pandas as pd
import streamlit as st

from data_schema import (
    COLUMNAS, COLUMNAS_FECHA, df_vacio, normalizar,
    COLUMNAS_SALIDAS, df_vacio_salidas, normalizar_salidas,
    COLUMNAS_ENTRADAS, df_vacio_entradas, normalizar_entradas,
    COLUMNAS_ESTIBAS, df_vacio_estibas, normalizar_estibas,
)

TABLA_POR_DEFECTO = "registros_mamposteria"
TABLA_CONFIG_POR_DEFECTO = "config_app"     # tabla clave-valor de configuración
TABLA_SALIDAS_POR_DEFECTO = "almacen_salidas"   # vales de salida de bloque
TABLA_ENTRADAS_POR_DEFECTO = "almacen_entradas"  # remisiones de entrada de bloque
TABLA_ESTIBAS_POR_DEFECTO = "almacen_estibas_dev"  # pallets devueltos al proveedor


# ─────────────────────────────────────────────────────────────
# Configuración / disponibilidad
# ─────────────────────────────────────────────────────────────
def disponible() -> bool:
    """True solo si hay URL y KEY de Supabase presentes y no son placeholders."""
    try:
        url = str(st.secrets["SUPABASE_URL"]).strip()
        key = str(st.secrets["SUPABASE_KEY"]).strip()
    except Exception:
        return False
    if not url or not key:
        return False
    # Rechaza SOLO los valores de ejemplo (placeholders con 'xxxx'). Ojo: un ref
    # real puede empezar por 'tu' (p.ej. 'tuefiiko…'), así que no se puede usar
    # el prefijo 'https://tu' como señal de placeholder (causaba falsos negativos).
    if "xxxx" in url.lower() or "xxxx" in key.lower():
        return False
    return url.startswith("https://") and ".supabase.co" in url.lower()


def _nombre_tabla() -> str:
    try:
        valor = str(st.secrets["SUPABASE_TABLE"]).strip()
        return valor or TABLA_POR_DEFECTO
    except Exception:
        return TABLA_POR_DEFECTO


def _nombre_tabla_config() -> str:
    try:
        valor = str(st.secrets["SUPABASE_TABLE_CONFIG"]).strip()
        return valor or TABLA_CONFIG_POR_DEFECTO
    except Exception:
        return TABLA_CONFIG_POR_DEFECTO


def _nombre_tabla_salidas() -> str:
    try:
        valor = str(st.secrets["SUPABASE_TABLE_SALIDAS"]).strip()
        return valor or TABLA_SALIDAS_POR_DEFECTO
    except Exception:
        return TABLA_SALIDAS_POR_DEFECTO


def _nombre_tabla_entradas() -> str:
    try:
        valor = str(st.secrets["SUPABASE_TABLE_ENTRADAS"]).strip()
        return valor or TABLA_ENTRADAS_POR_DEFECTO
    except Exception:
        return TABLA_ENTRADAS_POR_DEFECTO


def _nombre_tabla_estibas() -> str:
    try:
        valor = str(st.secrets["SUPABASE_TABLE_ESTIBAS"]).strip()
        return valor or TABLA_ESTIBAS_POR_DEFECTO
    except Exception:
        return TABLA_ESTIBAS_POR_DEFECTO


@st.cache_resource(show_spinner=False)
def _cliente():
    """Cliente de Supabase cacheado (una sola conexión por sesión)."""
    from supabase import create_client  # import perezoso: solo si se usa Supabase

    return create_client(
        str(st.secrets["SUPABASE_URL"]).strip(),
        str(st.secrets["SUPABASE_KEY"]).strip(),
    )


# ─────────────────────────────────────────────────────────────
# Conversión DataFrame ↔ registros JSON
# ─────────────────────────────────────────────────────────────
def _a_registros(df: pd.DataFrame, columnas: list = COLUMNAS,
                 normalizador=normalizar) -> list[dict]:
    """Convierte un DataFrame a una lista de dicts lista para insertar en Supabase.

    - Fechas → texto ISO 8601 (lo que Postgres espera para date/timestamptz).
    - NaN/NaT → None (null en JSON).
    Sirve para registros (por defecto) y para salidas de almacén: ambos esquemas
    usan los mismos nombres de columnas de fecha (`Fecha`, `Timestamp_registro`).
    """
    df = normalizador(df)
    registros = []
    for _, fila in df.iterrows():
        registro = {}
        for col in columnas:
            valor = fila[col]
            if col in COLUMNAS_FECHA:
                if pd.isna(valor):
                    registro[col] = None
                elif col == "Fecha":   # columna tipo `date` en Postgres
                    registro[col] = pd.Timestamp(valor).date().isoformat()
                else:                  # `timestamptz` (Timestamp_registro)
                    registro[col] = pd.Timestamp(valor).isoformat()
            elif isinstance(valor, float) and math.isnan(valor):
                registro[col] = None
            elif pd.isna(valor):
                registro[col] = None
            elif isinstance(valor, (pd.Timestamp,)):
                registro[col] = valor.isoformat()
            else:
                # numpy → tipos nativos de Python para que sean serializables a JSON
                registro[col] = getattr(valor, "item", lambda: valor)()
        registros.append(registro)
    return registros


# ─────────────────────────────────────────────────────────────
# Lectura / escritura
# ─────────────────────────────────────────────────────────────
def leer_datos() -> pd.DataFrame:
    """Descarga toda la tabla y la retorna como DataFrame normalizado."""
    cliente = _cliente()
    resp = cliente.table(_nombre_tabla()).select(",".join(f'"{c}"' for c in COLUMNAS)).execute()
    datos = resp.data or []
    if not datos:
        return df_vacio()
    df = pd.DataFrame(datos)
    return normalizar(df)


def insertar_registros(df_nuevas: pd.DataFrame) -> None:
    """Inserta SOLO las filas nuevas (append). Es la vía normal de guardado."""
    if df_nuevas is None or df_nuevas.empty:
        return
    registros = _a_registros(df_nuevas)
    cliente = _cliente()
    cliente.table(_nombre_tabla()).insert(registros).execute()


def guardar_datos(df_completo: pd.DataFrame) -> None:
    """Reemplaza TODO el histórico (borra y re-inserta).

    Se mantiene por compatibilidad con la interfaz unificada y para migraciones.
    En el uso normal de la app conviene usar `insertar_registros` (append).
    """
    cliente = _cliente()
    tabla = _nombre_tabla()
    # Supabase exige un filtro en delete: este borra todas las filas con id válido.
    cliente.table(tabla).delete().neq("id", 0).execute()
    if df_completo is not None and not df_completo.empty:
        cliente.table(tabla).insert(_a_registros(df_completo)).execute()


def eliminar_registros_por_grupo(grupo_ids: list) -> int:
    """Borra DEFINITIVAMENTE las filas cuyo `Grupo_id` esté en la lista.

    Hace SOLO un DELETE dirigido (no reinserta): si RLS lo deniega, no borra
    nada y devuelve 0 — nunca duplica datos. Devuelve el nº de filas borradas
    (PostgREST retorna las filas eliminadas en `resp.data`).
    """
    ids = [str(g).strip() for g in (grupo_ids or []) if str(g).strip()]
    if not ids:
        return 0
    cliente = _cliente()
    resp = (cliente.table(_nombre_tabla())
            .delete().in_("Grupo_id", ids).execute())
    return len(resp.data or [])


# ─────────────────────────────────────────────────────────────
# Salidas de almacén (vales de bloque, tabla `almacen_salidas`)
# ─────────────────────────────────────────────────────────────
def _con_id(crudo: list[dict], normalizador) -> pd.DataFrame:
    """Normaliza una respuesta de Supabase pero CONSERVA la columna `id`.

    El normalizador reindexa a las columnas del esquema (que no incluyen `id`),
    así que el `id` se re-pega al final. Sirve para poder borrar una fila puntual
    de almacén por su id. El `id` extra no molesta a los demás consumidores.
    """
    bruto = pd.DataFrame(crudo)
    df = normalizador(bruto)
    if "id" in bruto.columns:
        df["id"] = pd.to_numeric(bruto["id"], errors="coerce").values
    return df


def _eliminar_por_id(tabla: str, ids: list) -> int:
    """DELETE dirigido por `id` en una tabla de almacén. Devuelve nº borradas.

    No reinserta: si RLS lo deniega, no borra nada y devuelve 0 (nunca duplica).
    PostgREST retorna las filas eliminadas en `resp.data`.
    """
    limpio = [int(i) for i in (ids or []) if pd.notna(i)]
    if not limpio:
        return 0
    cliente = _cliente()
    resp = cliente.table(tabla).delete().in_("id", limpio).execute()
    return len(resp.data or [])


def leer_salidas() -> pd.DataFrame:
    """Descarga todas las salidas de almacén como DataFrame normalizado (con `id`)."""
    cliente = _cliente()
    cols = ",".join(['"id"'] + [f'"{c}"' for c in COLUMNAS_SALIDAS])
    resp = cliente.table(_nombre_tabla_salidas()).select(cols).execute()
    datos = resp.data or []
    if not datos:
        return df_vacio_salidas()
    return _con_id(datos, normalizar_salidas)


def insertar_salidas(df_nuevas: pd.DataFrame) -> None:
    """Inserta SOLO las salidas nuevas (append)."""
    if df_nuevas is None or df_nuevas.empty:
        return
    registros = _a_registros(df_nuevas, columnas=COLUMNAS_SALIDAS,
                             normalizador=normalizar_salidas)
    cliente = _cliente()
    cliente.table(_nombre_tabla_salidas()).insert(registros).execute()


def eliminar_salidas_por_id(ids: list) -> int:
    """Borra DEFINITIVAMENTE las salidas con esos `id`. Devuelve nº borradas."""
    return _eliminar_por_id(_nombre_tabla_salidas(), ids)


# ─────────────────────────────────────────────────────────────
# Entradas de almacén (remisiones de compra, tabla `almacen_entradas`)
# ─────────────────────────────────────────────────────────────
def leer_entradas() -> pd.DataFrame:
    """Descarga todas las entradas de almacén como DataFrame normalizado (con `id`)."""
    cliente = _cliente()
    cols = ",".join(['"id"'] + [f'"{c}"' for c in COLUMNAS_ENTRADAS])
    resp = cliente.table(_nombre_tabla_entradas()).select(cols).execute()
    datos = resp.data or []
    if not datos:
        return df_vacio_entradas()
    return _con_id(datos, normalizar_entradas)


def insertar_entradas(df_nuevas: pd.DataFrame) -> None:
    """Inserta SOLO las entradas nuevas (append)."""
    if df_nuevas is None or df_nuevas.empty:
        return
    registros = _a_registros(df_nuevas, columnas=COLUMNAS_ENTRADAS,
                             normalizador=normalizar_entradas)
    cliente = _cliente()
    cliente.table(_nombre_tabla_entradas()).insert(registros).execute()


def eliminar_entradas_por_id(ids: list) -> int:
    """Borra DEFINITIVAMENTE las entradas con esos `id`. Devuelve nº borradas."""
    return _eliminar_por_id(_nombre_tabla_entradas(), ids)


# ─────────────────────────────────────────────────────────────
# Estibas devueltas (pallets, tabla `almacen_estibas_dev`)
# ─────────────────────────────────────────────────────────────
def leer_estibas() -> pd.DataFrame:
    """Descarga todas las estibas devueltas como DataFrame normalizado (con `id`)."""
    cliente = _cliente()
    cols = ",".join(['"id"'] + [f'"{c}"' for c in COLUMNAS_ESTIBAS])
    resp = cliente.table(_nombre_tabla_estibas()).select(cols).execute()
    datos = resp.data or []
    if not datos:
        return df_vacio_estibas()
    return _con_id(datos, normalizar_estibas)


def insertar_estibas(df_nuevas: pd.DataFrame) -> None:
    """Inserta SOLO las devoluciones de estibas nuevas (append)."""
    if df_nuevas is None or df_nuevas.empty:
        return
    registros = _a_registros(df_nuevas, columnas=COLUMNAS_ESTIBAS,
                             normalizador=normalizar_estibas)
    cliente = _cliente()
    cliente.table(_nombre_tabla_estibas()).insert(registros).execute()


def eliminar_estibas_por_id(ids: list) -> int:
    """Borra DEFINITIVAMENTE las estibas devueltas con esos `id`. Devuelve nº borradas."""
    return _eliminar_por_id(_nombre_tabla_estibas(), ids)


# ─────────────────────────────────────────────────────────────
# Configuración (tabla clave-valor: meta, kg/saco, proyecto…)
# ─────────────────────────────────────────────────────────────
def leer_config_raw() -> dict:
    """Lee la tabla de config como {clave: valor_texto}.

    Devuelve {} si la tabla está vacía. Si la tabla aún no existe (u otro error),
    propaga la excepción para que la capa superior decida usar los valores por
    defecto sin romper la app.
    """
    cliente = _cliente()
    resp = cliente.table(_nombre_tabla_config()).select("clave,valor").execute()
    return {r["clave"]: r["valor"] for r in (resp.data or []) if r.get("clave")}


def guardar_config_raw(pares: dict) -> None:
    """Upsert de {clave: valor} en la tabla de config (clave = PK)."""
    if not pares:
        return
    registros = [{"clave": str(k), "valor": str(v)} for k, v in pares.items()]
    cliente = _cliente()
    cliente.table(_nombre_tabla_config()).upsert(registros, on_conflict="clave").execute()
