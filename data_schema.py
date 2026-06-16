"""
data_schema.py — Modelo de datos compartido
─────────────────────────────────────────────
Define el esquema (columnas, tipos) y las funciones de normalización que usan
TODOS los backends de almacenamiento (Supabase, SharePoint y Excel local).

Tener una sola fuente de verdad evita que los conectores se desincronicen y
garantiza que un registro guardado en cualquier backend tenga exactamente la
misma forma.
"""

import pandas as pd

# Orden y nombres EXACTOS de las columnas del histórico de registros.
# Estos nombres se usan tal cual como columnas del Excel (hoja `Registros`)
# y como columnas de la tabla de Supabase.
# Las columnas nuevas van AL FINAL para no alterar el orden del histórico:
#   Tipo_bloque_PH  — bloque divisorio (perforación horizontal) del grupo;
#                     `Tipo_ladrillo` conserva el estructural (P.V.).
#   Bloques_PV_teo / Bloques_PH_teo — bloques teóricos por muro según geometría
#                     (snapshot al guardar, igual que Cumple_meta).
COLUMNAS = [
    "Fecha", "Oficial", "Ayudante", "Sector", "Piso", "Zona",
    "Largo_m", "Alto_m", "M2_ejecutados", "Num_sacos", "Consumo_real_sac_m2",
    "Consumo_mortero_kg", "Num_dovelas", "ML_dovelas",
    "Tipo_ladrillo", "Cumple_meta", "Observaciones", "Grupo_id", "Timestamp_registro",
    "Tipo_bloque_PH", "Bloques_PV_teo", "Bloques_PH_teo",
]

# Columnas que deben quedar como número decimal.
COLUMNAS_NUMERICAS = [
    "Largo_m", "Alto_m", "M2_ejecutados", "Num_sacos",
    "Consumo_real_sac_m2", "Consumo_mortero_kg", "Num_dovelas", "ML_dovelas",
    "Bloques_PV_teo", "Bloques_PH_teo",
]

# Columnas de fecha/hora.
COLUMNAS_FECHA = ["Fecha", "Timestamp_registro"]


def a_bool(valor) -> bool:
    """Convierte distintos formatos (texto/numérico) a booleano de Python."""
    if isinstance(valor, bool):
        return valor
    if pd.isna(valor):
        return False
    return str(valor).strip().upper() in ("TRUE", "VERDADERO", "SI", "SÍ", "1", "X")


def df_vacio() -> pd.DataFrame:
    """DataFrame vacío con todas las columnas del esquema."""
    return pd.DataFrame(columns=COLUMNAS)


def normalizar(df: pd.DataFrame) -> pd.DataFrame:
    """Garantiza que existan todas las columnas y tengan el tipo correcto."""
    for col in COLUMNAS:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[COLUMNAS].copy()

    df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce")
    df["Timestamp_registro"] = pd.to_datetime(df["Timestamp_registro"], errors="coerce")
    for col in COLUMNAS_NUMERICAS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Cumple_meta"] = df["Cumple_meta"].map(a_bool)
    return df


# ─────────────────────────────────────────────────────────────
# Salidas de almacén (vales de bloque, para conciliar desperdicio)
# ─────────────────────────────────────────────────────────────
# Una fila por vale de almacén. `Cantidad` SIEMPRE en unidades (la UI convierte
# estibas → unidades con `unds_por_estiba` del catálogo antes de guardar).
# Nombres usados tal cual en la tabla `almacen_salidas` (Supabase) y en la
# hoja `Salidas_almacen` (Excel/SharePoint).
COLUMNAS_SALIDAS = [
    "Fecha", "Sector", "Piso", "Tipo_bloque", "Cantidad",
    "No_vale", "Observaciones", "Timestamp_registro",
]


def df_vacio_salidas() -> pd.DataFrame:
    """DataFrame vacío con todas las columnas del esquema de salidas."""
    return pd.DataFrame(columns=COLUMNAS_SALIDAS)


def normalizar_salidas(df: pd.DataFrame) -> pd.DataFrame:
    """Garantiza columnas y tipos del esquema de salidas de almacén."""
    for col in COLUMNAS_SALIDAS:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[COLUMNAS_SALIDAS].copy()

    df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce")
    df["Timestamp_registro"] = pd.to_datetime(df["Timestamp_registro"], errors="coerce")
    df["Cantidad"] = pd.to_numeric(df["Cantidad"], errors="coerce")
    return df


# ─────────────────────────────────────────────────────────────
# Entradas de almacén (compras recibidas del proveedor)
# ─────────────────────────────────────────────────────────────
# Una fila por remisión de entrada (lo que el almacén recibe del proveedor),
# centraliza lo que antes vivía en el Excel "Control ladrillo". `Cantidad`
# SIEMPRE en unidades reales (ladrillos contados); las estibas se guardan
# aparte como dato logístico:
#   Estibas_ing  — estibas (pallets) que llegaron en la remisión.
#   Estibas_dev  — estibas (pallets vacíos) devueltas al proveedor; NO
#                  descuentan ladrillos, son control de pallets.
# El ACUMULADO no se guarda: se calcula al vuelo (evita desincronizarse).
COLUMNAS_ENTRADAS = [
    "Fecha", "Tipo_bloque", "Cantidad", "Estibas_ing", "Estibas_dev",
    "No_remision", "Proveedor", "Observaciones", "Timestamp_registro",
]


def df_vacio_entradas() -> pd.DataFrame:
    """DataFrame vacío con todas las columnas del esquema de entradas."""
    return pd.DataFrame(columns=COLUMNAS_ENTRADAS)


def normalizar_entradas(df: pd.DataFrame) -> pd.DataFrame:
    """Garantiza columnas y tipos del esquema de entradas de almacén."""
    for col in COLUMNAS_ENTRADAS:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[COLUMNAS_ENTRADAS].copy()

    df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce")
    df["Timestamp_registro"] = pd.to_datetime(df["Timestamp_registro"], errors="coerce")
    for col in ("Cantidad", "Estibas_ing", "Estibas_dev"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df
