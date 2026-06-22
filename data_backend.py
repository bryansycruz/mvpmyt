"""
data_backend.py — Selector de backend de almacenamiento
────────────────────────────────────────────────────────
Fachada única que usa `app.py`. Decide AUTOMÁTICAMENTE dónde leer/guardar los
datos según las credenciales presentes en `.streamlit/secrets.toml`, con esta
prioridad:

    1. Supabase    (si SUPABASE_URL y SUPABASE_KEY están configurados)
    2. SharePoint  (si están las credenciales de Azure/Graph)
    3. Excel local (modo demo, sin configurar nada)

Así se puede orientar la app a Supabase SIN eliminar SharePoint: basta con
configurar (o no) cada bloque de secrets. El resto de la app no cambia.

Interfaz pública:
    COLUMNAS                       # esquema de columnas
    leer_datos()        -> df
    agregar_registros(df_nuevas)   # append (vía normal de guardado)
    guardar_datos(df)              # reemplaza todo (migraciones)
    leer_salidas()      -> df      # vales de salida de almacén (bloques)
    agregar_salidas(df_nuevas)     # append de salidas
    leer_entradas()     -> df      # remisiones de entrada de almacén (compras)
    agregar_entradas(df_nuevas)    # append de entradas
    leer_catalogo()     -> list    # catálogo de bloques (P.V./P.H.)
    guardar_catalogo(lista)        # persiste el catálogo (solo Supabase)
    backend_actual()    -> "supabase" | "sharepoint" | "local"
    estado()            -> dict con info para mostrar en la UI
    modo_local()        -> bool   (compatibilidad)
"""

import json

import pandas as pd

from data_schema import COLUMNAS  # re-exportado para app.py
from calculos import (
    TEORICO_SAC_M2, KG_POR_SACO,
    UMBRAL_DESPERDICIO_PCT, FACTOR_AJUSTE_BLOQUES,
    CATALOGO_BLOQUES_DEFECTO, validar_catalogo,
)
import sharepoint_connector as sp
import supabase_connector as sb

__all__ = [
    "COLUMNAS", "leer_datos", "agregar_registros", "guardar_datos",
    "eliminar_registros",
    "leer_salidas", "agregar_salidas",
    "leer_entradas", "agregar_entradas",
    "leer_estibas", "agregar_estibas",
    "eliminar_entradas", "eliminar_salidas", "eliminar_estibas",
    "leer_catalogo", "guardar_catalogo",
    "leer_valores_ocultos", "guardar_valores_ocultos",
    "backend_actual", "estado", "modo_local",
    "leer_config", "guardar_config", "config_persistente", "CONFIG_DEFECTOS",
]

# Configuración editable de la app. Las claves coinciden con las filas de la
# tabla `config_app` en Supabase. Si una clave falta (o el backend no es
# Supabase), se usa el valor por defecto de aquí.
CONFIG_DEFECTOS = {
    "meta_sac_m2": TEORICO_SAC_M2,        # meta de consumo (sac/m²)
    "kg_por_saco": KG_POR_SACO,           # kilogramos por saco de mortero
    "proyecto": "Serrania Campestre",     # nombre del proyecto/obra
    "umbral_desperdicio_pct": UMBRAL_DESPERDICIO_PCT,  # semáforo conciliación (%)
    "factor_ajuste_bloques": FACTOR_AJUSTE_BLOQUES,    # ajuste del teórico (cortes/trabas)
}

# Clave de la tabla config_app donde se guarda el catálogo de bloques (JSON).
_CLAVE_CATALOGO = "catalogo_bloques"

# Clave donde se guardan los valores ocultos de las listas de autocompletado
# (oficiales, ayudantes, pisos, sectores que ya no se usan), por columna.
_CLAVE_VALORES_OCULTOS = "valores_ocultos"


# ─────────────────────────────────────────────────────────────
# Selección de backend
# ─────────────────────────────────────────────────────────────
def backend_actual() -> str:
    """Devuelve el backend activo según las credenciales configuradas."""
    if sb.disponible():
        return "supabase"
    if not sp.modo_local():   # hay credenciales de SharePoint válidas
        return "sharepoint"
    return "local"


def modo_local() -> bool:
    """True solo cuando se está usando el Excel local (modo demo)."""
    return backend_actual() == "local"


def estado() -> dict:
    """Información del backend activo para mostrar en el sidebar."""
    backend = backend_actual()
    return {
        "supabase": {
            "tipo": "supabase",
            "icono": "🟢",
            "titulo": "Conectado a la base de datos",
            "detalle": "Los registros se guardan en la base de datos PostgreSQL.",
        },
        "sharepoint": {
            "tipo": "sharepoint",
            "icono": "🟢",
            "titulo": "Conectado a SharePoint",
            "detalle": "Los registros se guardan en el Excel de SharePoint.",
        },
        "local": {
            "tipo": "local",
            "icono": "🟡",
            "titulo": "Modo local (demo)",
            "detalle": "Guardando en Excel local. Configura Supabase o SharePoint "
                       "en `secrets.toml` para persistir en la nube.",
        },
    }[backend]


# ─────────────────────────────────────────────────────────────
# Lectura
# ─────────────────────────────────────────────────────────────
def leer_datos() -> pd.DataFrame:
    backend = backend_actual()
    if backend == "supabase":
        return sb.leer_datos()
    return sp.leer_datos()   # cubre SharePoint y Excel local


# ─────────────────────────────────────────────────────────────
# Escritura
# ─────────────────────────────────────────────────────────────
def agregar_registros(df_nuevas: pd.DataFrame) -> None:
    """Añade filas nuevas al histórico (vía de guardado normal de la app).

    - Supabase: INSERT eficiente de solo las filas nuevas.
    - SharePoint / local: lee el histórico, concatena y reescribe el archivo
      completo (comportamiento original, intacto).
    """
    if df_nuevas is None or df_nuevas.empty:
        return

    if backend_actual() == "supabase":
        sb.insertar_registros(df_nuevas)
        return

    actual = sp.leer_datos()
    nuevo = pd.concat([actual, df_nuevas], ignore_index=True)
    sp.guardar_datos(nuevo)


def guardar_datos(df_completo: pd.DataFrame) -> None:
    """Reemplaza TODO el histórico. Pensado para migraciones/correcciones."""
    if backend_actual() == "supabase":
        sb.guardar_datos(df_completo)
        return
    sp.guardar_datos(df_completo)


def eliminar_registros(grupo_ids: list) -> int:
    """Elimina DEFINITIVAMENTE los registros de esos `Grupo_id`. Devuelve el
    número de filas borradas.

    - Supabase: DELETE dirigido por Grupo_id (no reinserta; si RLS lo deniega
      devuelve 0 sin duplicar nada).
    - SharePoint / local: lee el histórico, quita esas filas y reescribe.
    """
    ids = [str(g).strip() for g in (grupo_ids or []) if str(g).strip()]
    if not ids:
        return 0

    if backend_actual() == "supabase":
        return sb.eliminar_registros_por_grupo(ids)

    actual = sp.leer_datos()
    if actual is None or actual.empty or "Grupo_id" not in actual.columns:
        return 0
    mask = actual["Grupo_id"].astype(str).str.strip().isin(ids)
    n = int(mask.sum())
    if n:
        sp.guardar_datos(actual[~mask])
    return n


# ─────────────────────────────────────────────────────────────
# Salidas de almacén (vales de bloque, para conciliar desperdicio)
# ─────────────────────────────────────────────────────────────
def leer_salidas() -> pd.DataFrame:
    if backend_actual() == "supabase":
        return sb.leer_salidas()
    return sp.leer_salidas()   # cubre SharePoint y Excel local


def agregar_salidas(df_nuevas: pd.DataFrame) -> None:
    """Añade vales nuevos de salida de almacén (mismo patrón que registros)."""
    if df_nuevas is None or df_nuevas.empty:
        return

    if backend_actual() == "supabase":
        sb.insertar_salidas(df_nuevas)
        return

    actual = sp.leer_salidas()
    nuevo = pd.concat([actual, df_nuevas], ignore_index=True)
    sp.guardar_salidas(nuevo)


# ─────────────────────────────────────────────────────────────
# Entradas de almacén (remisiones de compra, para el stock y el pedido)
# ─────────────────────────────────────────────────────────────
def leer_entradas() -> pd.DataFrame:
    if backend_actual() == "supabase":
        return sb.leer_entradas()
    return sp.leer_entradas()   # cubre SharePoint y Excel local


def agregar_entradas(df_nuevas: pd.DataFrame) -> None:
    """Añade remisiones nuevas de entrada de almacén (mismo patrón que salidas)."""
    if df_nuevas is None or df_nuevas.empty:
        return

    if backend_actual() == "supabase":
        sb.insertar_entradas(df_nuevas)
        return

    actual = sp.leer_entradas()
    nuevo = pd.concat([actual, df_nuevas], ignore_index=True)
    sp.guardar_entradas(nuevo)


# ─────────────────────────────────────────────────────────────
# Estibas devueltas (pallets regresados al proveedor, ledger aparte)
# ─────────────────────────────────────────────────────────────
def leer_estibas() -> pd.DataFrame:
    if backend_actual() == "supabase":
        return sb.leer_estibas()
    return sp.leer_estibas()   # cubre SharePoint y Excel local


def agregar_estibas(df_nuevas: pd.DataFrame) -> None:
    """Añade devoluciones nuevas de estibas (mismo patrón que entradas)."""
    if df_nuevas is None or df_nuevas.empty:
        return

    if backend_actual() == "supabase":
        sb.insertar_estibas(df_nuevas)
        return

    actual = sp.leer_estibas()
    nuevo = pd.concat([actual, df_nuevas], ignore_index=True)
    sp.guardar_estibas(nuevo)


# ─────────────────────────────────────────────────────────────
# Borrado de movimientos de almacén (corregir errores; el stock se recalcula)
# ─────────────────────────────────────────────────────────────
def _eliminar_movimientos(filas_df: pd.DataFrame, sb_por_id, sp_leer, sp_guardar) -> int:
    """Borra DEFINITIVAMENTE las filas indicadas de un ledger de almacén.

    `filas_df` son las filas seleccionadas en la UI (vienen de la lectura, así
    que en Supabase traen la columna `id`). Devuelve el nº de filas borradas.

    - Supabase: DELETE dirigido por `id` (no reinserta; si RLS lo deniega
      devuelve 0 sin duplicar nada).
    - SharePoint / local: anti-join por `Timestamp_registro` (comparación en
      memoria, no string-match de timestamptz) y reescribe el ledger completo.
    """
    if filas_df is None or filas_df.empty:
        return 0

    if backend_actual() == "supabase":
        ids = [i for i in filas_df.get("id", pd.Series(dtype=float)).tolist()
               if pd.notna(i)]
        return sb_por_id(ids)

    actual = sp_leer()
    if actual is None or actual.empty or "Timestamp_registro" not in actual.columns:
        return 0
    objetivo = pd.to_datetime(filas_df.get("Timestamp_registro"), errors="coerce").dropna()
    mask = pd.to_datetime(actual["Timestamp_registro"], errors="coerce").isin(set(objetivo))
    n = int(mask.sum())
    if n:
        sp_guardar(actual[~mask])
    return n


def eliminar_entradas(filas_df: pd.DataFrame) -> int:
    """Borra las entradas de almacén seleccionadas. Devuelve nº borradas."""
    return _eliminar_movimientos(filas_df, sb.eliminar_entradas_por_id,
                                 sp.leer_entradas, sp.guardar_entradas)


def eliminar_salidas(filas_df: pd.DataFrame) -> int:
    """Borra las salidas de almacén seleccionadas. Devuelve nº borradas."""
    return _eliminar_movimientos(filas_df, sb.eliminar_salidas_por_id,
                                 sp.leer_salidas, sp.guardar_salidas)


def eliminar_estibas(filas_df: pd.DataFrame) -> int:
    """Borra las devoluciones de estibas seleccionadas. Devuelve nº borradas."""
    return _eliminar_movimientos(filas_df, sb.eliminar_estibas_por_id,
                                 sp.leer_estibas, sp.guardar_estibas)


# ─────────────────────────────────────────────────────────────
# Catálogo de bloques (P.V./P.H.) — JSON en config_app (solo Supabase)
# ─────────────────────────────────────────────────────────────
def leer_catalogo() -> list:
    """Catálogo efectivo de bloques: lo guardado en Supabase o el defecto.

    Siempre devuelve una lista de dicts válida (validar_catalogo). Con
    SharePoint/local el catálogo es fijo (el de calculos.py)."""
    if backend_actual() == "supabase":
        try:
            crudo = sb.leer_config_raw().get(_CLAVE_CATALOGO)
            if crudo:
                cat = validar_catalogo(json.loads(crudo))
                if cat:
                    return cat
        except Exception:
            pass   # tabla/clave inexistente o JSON corrupto → defecto
    return [dict(b) for b in CATALOGO_BLOQUES_DEFECTO]


def guardar_catalogo(lista: list) -> None:
    """Persiste el catálogo (solo Supabase). Lanza si no se puede guardar."""
    if not config_persistente():
        raise RuntimeError("El catálogo solo se puede guardar con Supabase.")
    limpio = validar_catalogo(lista)
    if not limpio:
        raise ValueError("El catálogo no puede quedar vacío: revisa los nombres y medidas.")
    sb.guardar_config_raw({_CLAVE_CATALOGO: json.dumps(limpio, ensure_ascii=False)})


# ─────────────────────────────────────────────────────────────
# Valores ocultos de las listas de autocompletado (por columna)
# ─────────────────────────────────────────────────────────────
def leer_valores_ocultos() -> dict:
    """Valores ocultos de las listas de autocompletado, por columna:
    {"Oficial": [...], "Ayudante": [...], "Piso": [...], "Sector": [...]}.

    Ocultar NO borra el historial: solo saca el valor de los desplegables al
    digitar. Solo persiste con Supabase; con otros backends devuelve {}."""
    if backend_actual() != "supabase":
        return {}
    try:
        crudo = sb.leer_config_raw().get(_CLAVE_VALORES_OCULTOS)
        if crudo:
            data = json.loads(crudo)
            if isinstance(data, dict):
                return {str(k): [str(v) for v in (vs or [])]
                        for k, vs in data.items()}
    except Exception:
        pass   # tabla/clave inexistente o JSON corrupto → nada oculto
    return {}


def guardar_valores_ocultos(data: dict) -> None:
    """Persiste el mapa de valores ocultos (solo Supabase). Normaliza a listas
    únicas y ordenadas, descartando vacíos y columnas sin valores."""
    if not config_persistente():
        raise RuntimeError("Ocultar valores de las listas requiere Supabase.")
    limpio = {}
    for col, valores in (data or {}).items():
        vals = sorted({str(v).strip() for v in (valores or []) if str(v).strip()})
        if vals:
            limpio[str(col)] = vals
    sb.guardar_config_raw({_CLAVE_VALORES_OCULTOS: json.dumps(limpio, ensure_ascii=False)})


# ─────────────────────────────────────────────────────────────
# Configuración editable (meta, kg/saco, proyecto)
# ─────────────────────────────────────────────────────────────
def config_persistente() -> bool:
    """True si el backend permite GUARDAR configuración (hoy: solo Supabase)."""
    return backend_actual() == "supabase"


def _a_float(valor, defecto: float) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return defecto


def leer_config() -> dict:
    """Configuración efectiva: lo guardado en Supabase sobre los valores por defecto.

    SIEMPRE devuelve todas las claves de CONFIG_DEFECTOS con el tipo correcto.
    Si el backend no es Supabase, o la tabla aún no existe, o falla la lectura,
    devuelve los valores por defecto (la app sigue funcionando exactamente como antes).
    """
    cfg = dict(CONFIG_DEFECTOS)
    if backend_actual() != "supabase":
        return cfg
    try:
        crudo = sb.leer_config_raw()
    except Exception:
        return cfg   # tabla inexistente o error de red → defectos
    for clave in ("meta_sac_m2", "kg_por_saco",
                  "umbral_desperdicio_pct", "factor_ajuste_bloques"):
        if clave in crudo:
            cfg[clave] = _a_float(crudo[clave], CONFIG_DEFECTOS[clave])
    if str(crudo.get("proyecto", "")).strip():
        cfg["proyecto"] = str(crudo["proyecto"]).strip()
    return cfg


def guardar_config(meta_sac_m2: float, kg_por_saco: float, proyecto: str,
                   umbral_desperdicio_pct: float | None = None,
                   factor_ajuste_bloques: float | None = None) -> None:
    """Guarda la configuración (solo Supabase). Lanza si el backend no la soporta.

    Los parámetros nuevos son opcionales para no romper llamadas existentes
    (si vienen en None, esas claves no se tocan)."""
    if not config_persistente():
        raise RuntimeError("La configuración solo se puede guardar con Supabase.")
    pares = {
        "meta_sac_m2": str(float(meta_sac_m2)),
        "kg_por_saco": str(float(kg_por_saco)),
        "proyecto": str(proyecto).strip(),
    }
    if umbral_desperdicio_pct is not None:
        pares["umbral_desperdicio_pct"] = str(float(umbral_desperdicio_pct))
    if factor_ajuste_bloques is not None:
        pares["factor_ajuste_bloques"] = str(float(factor_ajuste_bloques))
    sb.guardar_config_raw(pares)
