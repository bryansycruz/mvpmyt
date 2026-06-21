"""
sharepoint_connector.py
───────────────────────
Lectura y escritura del Excel histórico de mampostería alojado en SharePoint,
usando la API de Microsoft Graph con autenticación de tipo *client credentials*
(sin login de usuario).

Funciones públicas:
    get_access_token()        -> str
    leer_datos()              -> pd.DataFrame   (hoja `Registros`)
    guardar_datos(df)         -> None           (preserva la hoja de salidas)
    leer_salidas()            -> pd.DataFrame   (hoja `Salidas_almacen`)
    guardar_salidas(df)       -> None           (preserva las demás hojas)
    leer_entradas()           -> pd.DataFrame   (hoja `Entradas_almacen`)
    guardar_entradas(df)      -> None           (preserva las demás hojas)

El libro tiene TRES hojas; cada guardado reescribe el archivo completo, así que
siempre se escriben las tres (si no, se perderían las otras).

Las credenciales se leen de st.secrets (.streamlit/secrets.toml).
"""

import io
import os

import requests
import pandas as pd
import streamlit as st

# `msal` se importa de forma PEREZOSA dentro de get_access_token() (no aquí):
# data_backend importa este conector SIEMPRE (aunque el backend activo sea
# Supabase). Si `msal` falla o no está instalado en el entorno de despliegue,
# un import de nivel superior tumbaría toda la app; perezoso solo afecta a quien
# realmente use SharePoint. Mismo patrón que `from supabase import ...`.

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"

# Archivo de respaldo local. Se usa automáticamente cuando NO hay credenciales
# de SharePoint configuradas (modo demo/prueba). Al configurar los secrets, la
# app cambia sola a SharePoint sin tocar el código.
ARCHIVO_LOCAL = os.path.join(os.path.dirname(__file__), "datos_mamposteria_local.xlsx")

# El esquema de datos (columnas, tipos y normalización) vive en data_schema.py,
# compartido por todos los backends (Supabase, SharePoint, Excel local).
from data_schema import (
    COLUMNAS, normalizar, df_vacio,
    normalizar_salidas, df_vacio_salidas,
    normalizar_entradas, df_vacio_entradas,
)

# Aliases internos para mantener el resto del módulo sin cambios.
_normalizar = normalizar
_df_vacio = df_vacio

# Hojas del libro: registros de pega + salidas + entradas de almacén.
HOJA_REGISTROS = "Registros"
HOJA_SALIDAS = "Salidas_almacen"
HOJA_ENTRADAS = "Entradas_almacen"


# ─────────────────────────────────────────────────────────────
# Autenticación
# ─────────────────────────────────────────────────────────────
def get_access_token() -> str:
    """Obtiene un token OAuth2 vía client credentials (sin login de usuario)."""
    import msal  # perezoso: solo se necesita si se usa SharePoint (ver cabecera)

    app = msal.ConfidentialClientApplication(
        client_id=st.secrets["CLIENT_ID"],
        client_credential=st.secrets["CLIENT_SECRET"],
        authority=f"https://login.microsoftonline.com/{st.secrets['TENANT_ID']}",
    )
    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" not in result:
        # Mensaje claro de Azure AD (p.ej. credencial inválida o sin consentimiento).
        detalle = result.get("error_description", result.get("error", "error desconocido"))
        raise RuntimeError(f"No se pudo obtener token de Azure AD: {detalle}")
    return result["access_token"]


def _headers(token: str, *, content: bool = False) -> dict:
    cabeceras = {"Authorization": f"Bearer {token}"}
    if content:
        cabeceras["Content-Type"] = "application/octet-stream"
    return cabeceras


# ─────────────────────────────────────────────────────────────
# Resolución del archivo en SharePoint
# ─────────────────────────────────────────────────────────────
def _get_site_id(token: str) -> str:
    """Resuelve el ID del sitio de SharePoint a partir del dominio y el nombre."""
    site = st.secrets["SHAREPOINT_SITE"]
    name = st.secrets["SHAREPOINT_SITE_NAME"]
    url = f"{GRAPH_ROOT}/sites/{site}:/sites/{name}"
    resp = requests.get(url, headers=_headers(token), timeout=30)
    resp.raise_for_status()
    return resp.json()["id"]


def _file_content_url(token: str) -> str:
    """URL de Graph para leer/escribir el contenido binario del archivo."""
    site_id = _get_site_id(token)
    path = st.secrets["SHAREPOINT_FILE_PATH"]
    return f"{GRAPH_ROOT}/sites/{site_id}/drive/root:/{path}:/content"


# ─────────────────────────────────────────────────────────────
# Modo de almacenamiento (SharePoint vs. local)
# ─────────────────────────────────────────────────────────────
def _secrets_configurados() -> bool:
    """True solo si TODAS las credenciales de SharePoint están presentes y no
    son los valores de ejemplo (xxxx…). Si faltan, se usa el modo local."""
    requeridas = [
        "TENANT_ID", "CLIENT_ID", "CLIENT_SECRET",
        "SHAREPOINT_SITE", "SHAREPOINT_SITE_NAME", "SHAREPOINT_FILE_PATH",
    ]
    try:
        for clave in requeridas:
            valor = str(st.secrets[clave]).strip()
            if not valor or valor.lower().startswith(("xxxx", "tuempresa")):
                return False
        return True
    except Exception:
        return False


def modo_local() -> bool:
    """Indica si la app está operando contra el Excel local (modo demo)."""
    return not _secrets_configurados()


# ─────────────────────────────────────────────────────────────
# Lectura / escritura (libro de DOS hojas: Registros + Salidas_almacen)
# ─────────────────────────────────────────────────────────────
def _leer_hoja_local(hoja: str, normalizador, vacio) -> pd.DataFrame:
    if not os.path.exists(ARCHIVO_LOCAL):
        return vacio()
    try:
        df = pd.read_excel(ARCHIVO_LOCAL, sheet_name=hoja)
    except Exception:
        return vacio()   # archivo sin esa hoja (p.ej. libro viejo de una sola hoja)
    return normalizador(df)


def _descargar_libro() -> bytes | None:
    """Descarga el archivo completo de SharePoint (None si aún no existe)."""
    token = get_access_token()
    url = _file_content_url(token)
    resp = requests.get(url, headers=_headers(token), timeout=60)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.content


def _leer_hoja_sharepoint(hoja: str, normalizador, vacio) -> pd.DataFrame:
    contenido = _descargar_libro()
    if contenido is None:
        return vacio()
    try:
        df = pd.read_excel(io.BytesIO(contenido), sheet_name=hoja)
    except Exception:
        return vacio()
    return normalizador(df)


def _sin_timezone(df: pd.DataFrame) -> pd.DataFrame:
    """openpyxl no soporta datetimes con zona horaria: los vuelve 'naïve'."""
    df = df.copy()
    for col in df.columns:
        if isinstance(df[col].dtype, pd.DatetimeTZDtype):
            df[col] = df[col].dt.tz_localize(None)
    return df


def _escribir_libro(registros: pd.DataFrame, salidas: pd.DataFrame,
                    entradas: pd.DataFrame) -> bytes:
    """Serializa el libro COMPLETO (las tres hojas) a bytes de Excel."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        _sin_timezone(_normalizar(registros)).to_excel(
            writer, index=False, sheet_name=HOJA_REGISTROS)
        _sin_timezone(normalizar_salidas(salidas)).to_excel(
            writer, index=False, sheet_name=HOJA_SALIDAS)
        _sin_timezone(normalizar_entradas(entradas)).to_excel(
            writer, index=False, sheet_name=HOJA_ENTRADAS)
    buffer.seek(0)
    return buffer.read()


def _guardar_libro(registros: pd.DataFrame, salidas: pd.DataFrame,
                   entradas: pd.DataFrame) -> None:
    """Reescribe el archivo (local o SharePoint) con las TRES hojas."""
    contenido = _escribir_libro(registros, salidas, entradas)
    if modo_local():
        with open(ARCHIVO_LOCAL, "wb") as f:
            f.write(contenido)
        return
    token = get_access_token()
    url = _file_content_url(token)
    resp = requests.put(url, headers=_headers(token, content=True), data=contenido)
    resp.raise_for_status()


def leer_datos() -> pd.DataFrame:
    """
    Lee la hoja `Registros` (SharePoint o Excel local) como DataFrame
    normalizado. Si el archivo o la hoja no existen, retorna un DataFrame
    vacío con las columnas correctas.
    """
    if modo_local():
        return _leer_hoja_local(HOJA_REGISTROS, _normalizar, _df_vacio)
    return _leer_hoja_sharepoint(HOJA_REGISTROS, _normalizar, _df_vacio)


def leer_salidas() -> pd.DataFrame:
    """Lee la hoja `Salidas_almacen` (vales de bloque) normalizada."""
    if modo_local():
        return _leer_hoja_local(HOJA_SALIDAS, normalizar_salidas, df_vacio_salidas)
    return _leer_hoja_sharepoint(HOJA_SALIDAS, normalizar_salidas, df_vacio_salidas)


def leer_entradas() -> pd.DataFrame:
    """Lee la hoja `Entradas_almacen` (remisiones de compra) normalizada."""
    if modo_local():
        return _leer_hoja_local(HOJA_ENTRADAS, normalizar_entradas, df_vacio_entradas)
    return _leer_hoja_sharepoint(HOJA_ENTRADAS, normalizar_entradas, df_vacio_entradas)


def guardar_datos(df: pd.DataFrame) -> None:
    """
    Sube el histórico completo de registros. Lee primero las otras hojas
    para reescribirlas intactas (el guardado reemplaza el archivo entero).
    """
    _guardar_libro(df, leer_salidas(), leer_entradas())


def guardar_salidas(df_salidas: pd.DataFrame) -> None:
    """Sube las salidas de almacén completas, preservando las demás hojas."""
    _guardar_libro(leer_datos(), df_salidas, leer_entradas())


def guardar_entradas(df_entradas: pd.DataFrame) -> None:
    """Sube las entradas de almacén completas, preservando las demás hojas."""
    _guardar_libro(leer_datos(), leer_salidas(), df_entradas)
