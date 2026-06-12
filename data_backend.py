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
    "leer_salidas", "agregar_salidas", "leer_catalogo", "guardar_catalogo",
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
