"""
app.py — Control de Mampostería
────────────────────────────────────────────────────
App Streamlit para registrar diariamente el trabajo de mampostería por oficial,
calcular indicadores y persistir el histórico en la nube.

El almacenamiento se elige automáticamente (ver data_backend.py):
    Supabase  →  SharePoint  →  Excel local (demo)
"""

import io
import math
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data_backend import (
    leer_datos, agregar_registros, eliminar_registros, estado,
    leer_config, guardar_config, config_persistente,
    leer_salidas, agregar_salidas,
    leer_entradas, agregar_entradas,
    leer_estibas, agregar_estibas,
    leer_conteos, agregar_conteos,
    eliminar_entradas, eliminar_salidas, eliminar_estibas,
    leer_catalogo, guardar_catalogo,
    leer_valores_ocultos, guardar_valores_ocultos,
)
from data_schema import (
    COLUMNAS_SALIDAS, COLUMNAS_ENTRADAS, COLUMNAS_ESTIBAS, COLUMNAS_CONTEOS,
)
from calculos import (
    TEORICO_SAC_M2, KG_POR_SACO,
    UMBRAL_DESPERDICIO_PCT, FACTOR_AJUSTE_BLOQUES, JUNTAS_CM,
    consumo_ratio, consumo_por, resumen_por,
    construir_filas_grupo, cumple_meta, repartir_por_m2,
    bloques_teoricos_muro, conciliacion,
    calculadora_muro, calculadora_combinado, resumen_pedido_por_tipo,
    rendimiento_por_junta,
)
import auth_supabase as auth

# ─────────────────────────────────────────────────────────────
# Constantes de presentación
# ─────────────────────────────────────────────────────────────
# Valor por defecto del nombre de proyecto. La meta y los kg/saco usan las
# constantes de `calculos`. Todos pueden sobrescribirse desde la configuración
# editable (ver _meta()/_kg()/_proyecto() y el panel de admin en el sidebar).
PROYECTO = "Mi obra"


# ─────────────────────────────────────────────────────────────
# Configuración editable (meta, kg/saco, proyecto)
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def cargar_config_cached() -> dict:
    return leer_config()


def _cfg() -> dict:
    """Configuración activa de la sesión (cargada en main, defectos si falta)."""
    return st.session_state.get("cfg", {})


def _meta() -> float:
    return float(_cfg().get("meta_sac_m2", TEORICO_SAC_M2))


def _kg() -> float:
    return float(_cfg().get("kg_por_saco", KG_POR_SACO))


def _proyecto() -> str:
    return _cfg().get("proyecto", PROYECTO)


def _umbral_pct() -> float:
    """Umbral del semáforo de desperdicio de bloques (%)."""
    return float(_cfg().get("umbral_desperdicio_pct", UMBRAL_DESPERDICIO_PCT))


def _factor_ajuste() -> float:
    """Factor que multiplica el teórico en la conciliación (cortes/trabas)."""
    return float(_cfg().get("factor_ajuste_bloques", FACTOR_AJUSTE_BLOQUES))


# Regla de negocio: el sobreprecio combinado (modulación + desperdicio) no debe
# pasar del 7 % en total. Se mide de forma aditiva: (factor − 1)·100 + desperdicio %.
TOPE_SOBRECONSUMO_PCT = 7.0


def _sobreconsumo_pct(factor: float, umbral_pct: float) -> float:
    """Sobreconsumo combinado en % = modulación (factor−1) + desperdicio."""
    return (float(factor) - 1.0) * 100.0 + float(umbral_pct)


def _aviso_tope(factor: float, umbral_pct: float) -> None:
    """Advierte (sin bloquear) si Factor de Modulación + desperdicio supera el tope."""
    total = _sobreconsumo_pct(factor, umbral_pct)
    if total > TOPE_SOBRECONSUMO_PCT + 1e-9:
        st.warning(
            f"⚠️ El sobreconsumo combinado es **{total:.1f} %** "
            f"(modulación {(factor - 1) * 100:.1f} % + desperdicio {umbral_pct:.1f} %) "
            f"y supera el tope recomendado de {TOPE_SOBRECONSUMO_PCT:g} %."
        )

st.set_page_config(page_title="Control de Mampostería", page_icon="🧱", layout="wide")


# ─────────────────────────────────────────────────────────────
# Carga de datos (con caché de 60 s)
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=60, show_spinner="Cargando datos…")
def cargar_datos_cached() -> pd.DataFrame:
    return leer_datos()


@st.cache_data(ttl=60, show_spinner="Cargando salidas de almacén…")
def cargar_salidas_cached() -> pd.DataFrame:
    return leer_salidas()


@st.cache_data(ttl=60, show_spinner="Cargando entradas de almacén…")
def cargar_entradas_cached() -> pd.DataFrame:
    return leer_entradas()


@st.cache_data(ttl=60, show_spinner="Cargando estibas devueltas…")
def cargar_estibas_cached() -> pd.DataFrame:
    return leer_estibas()


@st.cache_data(ttl=60, show_spinner="Cargando conteos de piso…")
def cargar_conteos_cached() -> pd.DataFrame:
    return leer_conteos()


@st.cache_data(ttl=60, show_spinner=False)
def cargar_catalogo_cached() -> list:
    return leer_catalogo()


@st.cache_data(ttl=60, show_spinner=False)
def cargar_ocultos_cached() -> dict:
    return leer_valores_ocultos()


def cargar_datos_estado():
    """Carga los datos y reporta el estado REAL de la conexión.

    Devuelve (df, conectado, detalle_error). NO detiene la app: deja que el
    sidebar muestre el estado y que `main` decida cómo seguir.
    """
    try:
        return cargar_datos_cached(), True, ""
    except Exception as e:
        return None, False, str(e)


# ─────────────────────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────────────────────
def opciones_unicas(df: pd.DataFrame, columna: str) -> list:
    """Valores únicos no nulos de una columna, ordenados — para autocompletado."""
    if df.empty or columna not in df.columns:
        return []
    return sorted(df[columna].dropna().astype(str).str.strip().replace("", pd.NA).dropna().unique().tolist())


def _ocultos() -> dict:
    """Mapa de valores ocultos por columna, cargado en la sesión."""
    return st.session_state.get("valores_ocultos") or {}


def opciones_visibles(df: pd.DataFrame, columna: str) -> list:
    """Como `opciones_unicas`, pero sin los valores que el usuario ocultó de la
    lista (los que ya no se usan). El historial no se toca: solo el desplegable."""
    ocultos = set(_ocultos().get(columna, []))
    return [v for v in opciones_unicas(df, columna) if v not in ocultos]


def campo_con_nuevo(label: str, opciones: list, key: str, opcional: bool = False) -> str:
    """
    Selectbox con opción de escribir un valor nuevo (manejo de oficiales,
    ayudantes, pisos y ladrillos irregulares, sin lista fija).
    Si `opcional`, agrega la opción "— Ninguno —" (por defecto) y puede devolver "".
    """
    NUEVO = "➕ Escribir nuevo…"
    NINGUNO = "— Ninguno —"
    base = ([NINGUNO] if opcional else []) + [NUEVO] + opciones
    seleccion = st.selectbox(label, base, key=f"sel_{key}")
    if seleccion == NINGUNO:
        return ""
    if seleccion == NUEVO:
        return st.text_input(f"Nuevo valor — {label}", key=f"new_{key}").strip()
    return seleccion


def _mayus(texto) -> str:
    """Normaliza texto digitado manualmente: sin espacios sobrantes y en MAYÚSCULAS.
    Así lo que se guarda (oficial, apto, piso…) queda consistente (APART 603, no
    'apart 603' ni 'Apart 603'), que es como ya lo escriben en obra."""
    return str(texto or "").strip().upper()


def color_consumo(val):
    """Rojo si supera la meta, verde si la cumple."""
    if pd.isna(val):
        return ""
    if val > _meta():
        return "color: #c0392b; font-weight: bold;"
    return "color: #1e8449; font-weight: bold;"


def estilar_consumo(styler, columna="Consumo_real_sac_m2"):
    """Aplica color condicional, compatible con pandas <2.1 (applymap) y >=2.1 (map)."""
    try:
        return styler.map(color_consumo, subset=[columna])
    except AttributeError:
        return styler.applymap(color_consumo, subset=[columna])


def color_desperdicio(val):
    """Semáforo del desperdicio de bloques (val en fracción: 0.10 = 10%):
    verde ≤ umbral, naranja ≤ 1.5×umbral, rojo por encima."""
    if pd.isna(val):
        return ""
    pct = val * 100
    umbral = _umbral_pct()
    if pct <= umbral:
        return "color: #1e8449; font-weight: bold;"
    if pct <= 1.5 * umbral:
        return "color: #d68910; font-weight: bold;"
    return "color: #c0392b; font-weight: bold;"


def estilar_desperdicio(styler, columna="Desperdicio_pct"):
    """Semáforo en la tabla de conciliación (mismo patrón que estilar_consumo)."""
    try:
        return styler.map(color_desperdicio, subset=[columna])
    except AttributeError:
        return styler.applymap(color_desperdicio, subset=[columna])


# ── Catálogo de bloques en sesión (cargado en main) ─────────
def _catalogo() -> list:
    """Catálogo de bloques activo de la sesión (lista de dicts)."""
    return st.session_state.get("catalogo") or []


def _bloques_clase(clase: str) -> list:
    return [b for b in _catalogo() if b.get("clase") == clase]


def _bloque_por_nombre(nombre: str):
    return next((b for b in _catalogo() if b.get("nombre") == nombre), None)


def _bloque_con_junta(bloque: dict | None, junta_cm: float):
    """Copia del bloque con la junta (cm) elegida en obra sobreescribiendo la del
    catálogo, para que el teórico use la pega real del muro."""
    if not bloque:
        return None
    return {**bloque, "junta_m": float(junta_cm) / 100.0}


# Opciones de "uso del muro" (como la hoja "Muro combinado" del Excel) y su
# mapeo al parámetro `uso` de `bloques_teoricos_muro` (P.V./P.H./Auto).
# Los muros que MEZCLAN dos tipologías (V12+V15 o PH12+PH15) se ingresan en la
# sección aparte "➕ Muros mixtos" (modo "Mixto"); la tabla principal es para
# muros normales (dovela P.V. + relleno P.H.).
USO_COMBINADO = "Combinado (P.V.+P.H.)"
USO_VERTICAL = "Vertical (solo P.V.)"
USO_HORIZONTAL = "Horizontal (solo P.H.)"
USOS_MURO = [USO_COMBINADO, USO_VERTICAL, USO_HORIZONTAL]
_USO_A_MODO = {USO_COMBINADO: "Auto", USO_VERTICAL: "P.V.", USO_HORIZONTAL: "P.H."}


def estado_presencia(dias_sin_venir: int) -> str:
    """Clasifica al oficial por días sin registrar trabajo."""
    if dias_sin_venir >= 30:
        return "Inactivo"
    if dias_sin_venir >= 7:
        return "En pausa"
    return "Activo"


def preparar_display(df: pd.DataFrame) -> pd.DataFrame:
    """Copia para mostrar: Fecha como texto y Cumple_meta como ✓/✗."""
    out = df.copy()
    if "Fecha" in out:
        out["Fecha"] = pd.to_datetime(out["Fecha"], errors="coerce").dt.strftime("%Y-%m-%d")
    if "Cumple_meta" in out:
        out["Cumple_meta"] = out["Cumple_meta"].map(lambda v: "✓ SÍ" if bool(v) else "✗ NO")
    return out


def excel_bytes(df: pd.DataFrame) -> bytes:
    """Excel de una sola hoja (`Registros`)."""
    return excel_libro({"Registros": df})


def _sin_timezone(df: pd.DataFrame) -> pd.DataFrame:
    """Excel (openpyxl) NO soporta datetimes con zona horaria. Convierte las
    columnas tz-aware (p.ej. `Timestamp_registro`, que en Supabase es timestamptz)
    a datetimes 'naïve' para poder exportar sin error."""
    df = df.copy()
    for col in df.columns:
        if isinstance(df[col].dtype, pd.DatetimeTZDtype):
            df[col] = df[col].dt.tz_localize(None)
    return df


def excel_libro(hojas: dict) -> bytes:
    """Crea un Excel con varias hojas: {nombre_hoja: DataFrame}."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for nombre, datos in hojas.items():
            _sin_timezone(datos).to_excel(writer, index=False, sheet_name=nombre[:31])  # 31 = límite Excel
    buffer.seek(0)
    return buffer.read()


def excel_datos_y_resumen(df: pd.DataFrame) -> bytes:
    """Excel con 2 hojas: 'Registros' (datos crudos) + 'Resumen_por_oficial'
    (m², consumo y % cumple ya calculados), listo para tablas dinámicas/gráficas."""
    hojas = {"Registros": df}
    if not df.empty:
        hojas["Resumen_por_oficial"] = resumen_por(df, "Oficial")
    return excel_libro(hojas)


# ─────────────────────────────────────────────────────────────
# Barra KPI global (visible en todas las pantallas)
# ─────────────────────────────────────────────────────────────
def barra_kpi_global(df: pd.DataFrame):
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Registros totales", len(df),
                help="Número de muros/filas registrados en toda la obra.")

    total_m2 = df["M2_ejecutados"].sum() if not df.empty else 0.0
    col2.metric("Total M² (obra)", f"{total_m2:,.1f} m²",
                help="Suma de m² ejecutados en toda la obra.")

    # El total de sacos se guarda solo en la 1.ª fila de cada grupo, así que
    # sum() no duplica (= total de mortero gastado en toda la obra).
    total_sacos = df["Num_sacos"].sum() if not df.empty else 0.0
    col3.metric("Sacos gastados", f"{total_sacos:,.0f}",
                help="Total de sacos de mortero consumidos en toda la obra.")
    col3.caption(f"= {total_sacos * _kg():,.0f} kg")

    prom = consumo_ratio(df) if not df.empty else float("nan")
    ayuda_consumo = (
        f"Mortero de TODA la obra = total de sacos ÷ total de m² (sac/m²). "
        f"Meta ≤ {_meta():g} sac/m²; menos es mejor."
    )
    if pd.notna(prom):
        col4.metric(
            "Consumo promedio mortero",
            f"{prom:.3f} sac/m²",
            delta=f"{prom - _meta():+.3f} vs meta",
            delta_color="inverse",  # menor consumo es mejor
            help=ayuda_consumo,
        )
        col4.caption(f"= {prom * _kg():.1f} kg/m²")
    else:
        col4.metric("Consumo promedio mortero", "—", help=ayuda_consumo)

    if not df.empty:
        col5.metric(
            "Registros que cumplen meta",
            f"{df['Cumple_meta'].mean() * 100:.0f}%",
            help=(f"% de registros cuyo consumo de mortero quedó en o por debajo "
                  f"de la meta (≤ {_meta():g} sac/m²)."),
        )
    else:
        col5.metric("Registros que cumplen meta", "—")

    st.divider()


# Columnas de llenado libre cuyas listas se pueden limpiar (ocultar valores que
# ya no se usan). Clave = columna del esquema; valor = etiqueta para el usuario.
_COLUMNAS_LISTAS = {
    "Oficial": "Oficiales",
    "Ayudante": "Ayudantes",
    "Piso": "Pisos",
    "Sector": "Sectores",
}


def _guardar_ocultos(ocultos: dict) -> None:
    """Persiste el mapa de ocultos, refresca la sesión y vuelve a dibujar."""
    try:
        guardar_valores_ocultos(ocultos)
        cargar_ocultos_cached.clear()
        st.session_state["valores_ocultos"] = leer_valores_ocultos()
    except Exception as e:
        st.error(f"No se pudo guardar: {e}")
        return
    st.toast("Lista actualizada ✅")
    st.rerun()


def _panel_limpiar_listas(df: pd.DataFrame) -> None:
    """Quitar/restaurar valores de las listas de autocompletado (oficiales que ya
    no vienen, pisos viejos, sectores, etc.). Ocultar NO borra el historial."""
    with st.expander("🧹 Limpiar listas (oficiales, ayudantes, pisos, sectores)"):
        if not config_persistente():
            st.info(
                "Para ocultar valores de forma permanente se necesita **Supabase**. "
                "Con SharePoint o el modo local las listas quedan completas."
            )
            return
        st.caption(
            "Quita de los desplegables los valores que ya no se usan (p. ej. un "
            "mampostero que no volvió). **No borra el historial ni los reportes**: "
            "solo lo saca de la lista al digitar, y lo puedes restaurar cuando quieras."
        )
        etiquetas = {v: k for k, v in _COLUMNAS_LISTAS.items()}
        etiqueta = st.selectbox("Lista", list(_COLUMNAS_LISTAS.values()),
                                key="limpiar_lista")
        col = etiquetas[etiqueta]

        ocultos = {k: list(v) for k, v in _ocultos().items()}   # copia editable
        ocultos_col = set(ocultos.get(col, []))
        todos = opciones_unicas(df, col)              # todos los del historial
        activos = [v for v in todos if v not in ocultos_col]

        c1, c2 = st.columns(2)
        with c1:
            quitar = st.multiselect(f"Quitar de «{etiqueta}»", activos,
                                    key=f"quitar_{col}")
            if st.button("➖ Quitar", key=f"btn_quitar_{col}", disabled=not quitar):
                ocultos[col] = sorted(ocultos_col | set(quitar))
                _guardar_ocultos(ocultos)
        with c2:
            restaurar = st.multiselect(f"Restaurar a «{etiqueta}»", sorted(ocultos_col),
                                       key=f"restaurar_{col}")
            if st.button("↩️ Restaurar", key=f"btn_restaurar_{col}",
                         disabled=not restaurar):
                ocultos[col] = sorted(ocultos_col - set(restaurar))
                _guardar_ocultos(ocultos)

        if ocultos_col:
            st.caption(f"**Ocultos en {etiqueta.lower()}:** " + ", ".join(sorted(ocultos_col)))


def _tabla_muros_mixtos(n: int, junta_cm: float, hay_catalogo: bool,
                        nombres_relleno: list) -> list:
    """Tabla desplegable de muros que mezclan DOS tipologías: P.V. (V12 + V15) o
    P.H. (PH 12 + PH 15). **Devuelve** la lista de muros parseados para SUMARLOS al
    mismo grupo/registro que los muros normales (un solo botón Guardar y un solo
    "# Sacos del grupo" repartido entre TODOS los muros).

    Reparto por muro (automático, según las dovelas):
      - # Dovelas > 0 → "Dovelas + Redes" va en las dovelas y "Relleno" en el resto.
      - # Dovelas = 0 → 50/50 entre los dos (aproximado, sin medidas exactas).
    Columnas internas Tipo_1 ("Dovelas + Redes") y Tipo_2 ("Relleno"). Usa el MISMO
    nonce `n` del formulario principal: al guardar el grupo se limpia con la normal.
    """
    muros = []
    with st.expander("➕ Muros mixtos (dos tipologías)"):
        if not hay_catalogo:
            st.info("Necesitas el catálogo de bloques para registrar muros mixtos.")
            return muros
        st.caption(
            "Para muros que mezclan **dos tipologías** — P.V. (V12 + V15) **o** "
            "P.H. (PH 12 + PH 15). Con **# Dovelas > 0**, el bloque de **Dovelas + "
            "Redes** va en las dovelas y el de **Relleno** en el resto; con "
            "**# Dovelas = 0** se reparte **50/50**. Se guardan en el **mismo "
            "registro** que los muros de arriba (comparten el **# de sacos del "
            "grupo** y el botón **Guardar**)."
        )
        mixtos_init = pd.DataFrame([{
            "Largo_m": 0.0, "Alto_m": 2.40, "Num_dovelas": 0, "Tipo_1": "", "Tipo_2": "",
        }])
        col_cfg = {
            "Largo_m": st.column_config.NumberColumn("Largo (m)", min_value=0.0, step=0.01, format="%.2f"),
            "Alto_m": st.column_config.NumberColumn("Alto muro (m)", min_value=0.0, step=0.01, format="%.2f"),
            "Num_dovelas": st.column_config.NumberColumn("# Dovelas", min_value=0, step=1, format="%d"),
            "Tipo_1": st.column_config.SelectboxColumn(
                "Dovelas + Redes", options=[""] + nombres_relleno, width="medium",
                help="Bloque de las dovelas y redes (P.V. o P.H.). Con # Dovelas = 0 es el 50%."),
            "Tipo_2": st.column_config.SelectboxColumn(
                "Relleno", options=[""] + nombres_relleno, width="medium",
                help="Bloque del relleno (P.V. o P.H.) — el resto del muro (o el otro 50%)."),
        }
        editado = st.data_editor(mixtos_init, num_rows="dynamic",
                                 key=f"mixtos_editor_{n}", width="stretch",
                                 column_config=col_cfg)
        for r in editado.itertuples():
            if not (pd.notna(r.Largo_m) and pd.notna(r.Alto_m)
                    and r.Largo_m > 0 and r.Alto_m > 0):
                continue
            t1 = r.Tipo_1.strip() if isinstance(r.Tipo_1, str) else ""
            t2 = r.Tipo_2.strip() if isinstance(r.Tipo_2, str) else ""
            ndov = int(r.Num_dovelas) if pd.notna(r.Num_dovelas) else 0
            muros.append({
                "Largo_m": float(r.Largo_m), "Alto_m": float(r.Alto_m),
                "Num_dovelas": ndov, "Uso": "Auto" if ndov > 0 else "Mixto",
                "uso_disp": "Mixto", "necesita_pv": True, "necesita_ph": True,
                "tipo_pv": t1, "tipo_ph": t2, "es_mixto": True,
                "bloque_pv": _bloque_con_junta(_bloque_por_nombre(t1), junta_cm) if t1 else None,
                "bloque_ph": _bloque_con_junta(_bloque_por_nombre(t2), junta_cm) if t2 else None,
            })
    return muros


# ─────────────────────────────────────────────────────────────
# Pantalla 1 — Ingreso de datos
# ─────────────────────────────────────────────────────────────
def pagina_ingreso(df: pd.DataFrame):
    st.header("📋 Ingreso de datos")

    # Mensaje flash tras un guardado exitoso.
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))

    _panel_limpiar_listas(df)

    # "Versión" del formulario: las keys de TODOS los widgets llevan este número.
    # Al guardar se incrementa → todos los widgets nacen de nuevo, limpios.
    # (Borrar las keys de session_state NO basta: st.data_editor conserva su
    # estado en el navegador y la tabla de muros quedaba con los datos viejos.)
    n = st.session_state.setdefault("ingreso_nonce", 0)

    oficiales = opciones_visibles(df, "Oficial")
    ayudantes = opciones_visibles(df, "Ayudante")
    pisos = opciones_visibles(df, "Piso")
    OTRO_SECTOR = "➕ Otro sector…"
    sectores = ["Torre", "Plataforma"] + [
        s for s in opciones_visibles(df, "Sector") if s not in ("Torre", "Plataforma")
    ] + [OTRO_SECTOR]

    # Fila 1 — Identificación
    c1, c2, c3 = st.columns(3)
    with c1:
        fecha = st.date_input("Fecha *", value=datetime.now().date(), key=f"in_fecha_{n}")
    with c2:
        sector_sel = st.selectbox("Sector *", sectores, key=f"in_sector_{n}")
        sector = (
            _mayus(st.text_input("Nuevo sector", key=f"in_sector_nuevo_{n}",
                                 placeholder="Ej: Sótano, Casino"))
            if sector_sel == OTRO_SECTOR else sector_sel
        )
    with c3:
        piso = campo_con_nuevo("Piso *", pisos, f"piso_{n}")

    # Fila 2 — Cuadrilla (oficial + ayudante opcional)
    c4, c5 = st.columns(2)
    with c4:
        oficial = campo_con_nuevo("Oficial *", oficiales, f"oficial_{n}")
    with c5:
        ayudante = campo_con_nuevo("Ayudante (opcional)", ayudantes, f"ayudante_{n}",
                                   opcional=True)

    # Fila 3 — Apto/Zona y junta de pega. El USO y el BLOQUE van POR muro, en la
    # tabla de abajo, para que un mismo registro pueda mezclar tipos (p.ej. un
    # muro combinado 12 y otro vertical 15).
    cat_pv = _bloques_clase("PV")
    cat_ph = _bloques_clase("PH")
    hay_catalogo = bool(cat_pv or cat_ph)
    c6, c7 = st.columns([3, 1])
    with c6:
        zona = st.text_input("Apto / Zona", placeholder="Ej: APART 501, ÚTIL 055",
                             key=f"in_zona_{n}",
                             help="Sirve para el control 'cuántos bloques por apto'.")
    with c7:
        junta_cm = st.selectbox(
            "Junta de pega (cm) *", JUNTAS_CM, index=JUNTAS_CM.index(1.5),
            key=f"in_junta_{n}",
            help="Espesor de la pega. En la obra la real es 1.5 cm.",
        )

    # Sección: muros del registro. Cada muro lleva su PROPIO uso y bloque(s); el
    # # de sacos es UNO solo para el grupo y se reparte entre los muros en
    # proporción a sus m² (ver `repartir_por_m2`).
    st.subheader("Muros y materiales")
    st.caption(
        "Agrega **una fila por muro** con su **uso** y su(s) **bloque(s)**. El "
        "**# de sacos es el TOTAL del grupo**: la app lo reparte entre los muros "
        "según sus m² y calcula, por muro, los **bloques** y el **mortero**. Las "
        "**# Dovelas** reparten P.V./P.H. en los muros **combinados**."
    )

    nombres_pv = [b["nombre"] for b in cat_pv]
    nombres_ph = [b["nombre"] for b in cat_ph]
    # Catálogo completo (P.V. + P.H.): solo lo usa la sección "➕ Muros mixtos".
    nombres_relleno = nombres_pv + nombres_ph
    muros_init = pd.DataFrame([{
        "Largo_m": 0.0, "Alto_m": 2.40, "Num_dovelas": 0,
        "Uso": USO_COMBINADO, "Bloque_PV": "", "Bloque_PH": "",
    }])
    col_cfg = {
        "Largo_m": st.column_config.NumberColumn("Largo (m)", min_value=0.0, step=0.01, format="%.2f"),
        "Alto_m": st.column_config.NumberColumn("Alto muro (m)", min_value=0.0, step=0.01, format="%.2f"),
        "Num_dovelas": st.column_config.NumberColumn("# Dovelas", min_value=0, step=1, format="%d"),
        "Uso": st.column_config.SelectboxColumn("Uso del muro", options=USOS_MURO, required=True, width="medium"),
    }
    if hay_catalogo:
        col_cfg["Bloque_PV"] = st.column_config.SelectboxColumn(
            "Bloque P.V. (dovelas)", options=[""] + nombres_pv, width="medium")
        col_cfg["Bloque_PH"] = st.column_config.SelectboxColumn(
            "Bloque P.H. (relleno)", options=[""] + nombres_ph, width="medium")
    else:
        muros_init = muros_init.drop(columns=["Bloque_PV", "Bloque_PH"])

    editado = st.data_editor(
        muros_init,
        num_rows="dynamic",
        key=f"muros_editor_{n}",
        width="stretch",
        column_config=col_cfg,
    )

    # Muros que mezclan dos tipologías (V12+V15 o PH12+PH15): tabla desplegable
    # aparte que se SUMA al MISMO grupo/registro (comparten # sacos y Guardar).
    muros_mixtos = _tabla_muros_mixtos(n, junta_cm, hay_catalogo, nombres_relleno)

    s1, s2 = st.columns([1, 3])
    with s1:
        sacos_total = st.number_input(
            "# Sacos (TOTAL del grupo)", min_value=0.0, step=0.5, format="%.1f",
            key=f"in_sacos_{n}"
        )
    with s2:
        observaciones = st.text_input("Observaciones", key=f"in_obs_{n}")

    # Lo digitado a mano se guarda en MAYÚSCULAS (consistencia del histórico y de
    # los agrupados por oficial/piso/apto). El Sector elegido de la lista no se
    # toca; el "Nuevo sector" ya se normalizó arriba.
    oficial = _mayus(oficial)
    ayudante = _mayus(ayudante)
    piso = _mayus(piso)
    zona = _mayus(zona)
    observaciones = _mayus(observaciones)

    # Parseo de la tabla → lista de muros, cada uno con su uso/bloque propios.
    def _celda_txt(v) -> str:
        return v.strip() if isinstance(v, str) else ""

    muros = []
    for r in editado.itertuples():
        if not (pd.notna(r.Largo_m) and pd.notna(r.Alto_m)
                and r.Largo_m > 0 and r.Alto_m > 0):
            continue
        uso_disp = getattr(r, "Uso", None)
        if not isinstance(uso_disp, str) or uso_disp not in USOS_MURO:
            uso_disp = USO_COMBINADO
        nec_pv = uso_disp in (USO_COMBINADO, USO_VERTICAL)
        nec_ph = uso_disp in (USO_COMBINADO, USO_HORIZONTAL)
        tipo_pv = _celda_txt(getattr(r, "Bloque_PV", "")) if (hay_catalogo and nec_pv) else ""
        tipo_ph = _celda_txt(getattr(r, "Bloque_PH", "")) if (hay_catalogo and nec_ph) else ""
        muros.append({
            "Largo_m": float(r.Largo_m), "Alto_m": float(r.Alto_m),
            "Num_dovelas": int(r.Num_dovelas) if pd.notna(r.Num_dovelas) else 0,
            "Uso": _USO_A_MODO.get(uso_disp, "Auto"), "uso_disp": uso_disp,
            "necesita_pv": nec_pv, "necesita_ph": nec_ph,
            "tipo_pv": tipo_pv, "tipo_ph": tipo_ph,
            "bloque_pv": _bloque_con_junta(_bloque_por_nombre(tipo_pv), junta_cm) if tipo_pv else None,
            "bloque_ph": _bloque_con_junta(_bloque_por_nombre(tipo_ph), junta_cm) if tipo_ph else None,
        })

    # Los muros mixtos se suman al MISMO grupo: el cálculo, el reparto de sacos y
    # el guardado tratan a todos por igual. `muros_normales` se conserva aparte
    # solo para validar el P.V./P.H. de la tabla normal con mensajes claros.
    muros_normales = muros
    muros = muros_normales + muros_mixtos

    # Cálculo automático en tiempo real (grupo + desglose por muro).
    n_muros = len(muros)
    m2s = [m["Largo_m"] * m["Alto_m"] for m in muros]
    m2_total = round(sum(m2s), 2)
    ml_total = round(sum(m["Num_dovelas"] * m["Alto_m"] for m in muros), 2)
    consumo_real = round(sacos_total / m2_total, 4) if m2_total > 0 else 0.0
    consumo_kg = round(sacos_total * _kg(), 2)
    consumo_kg_m2 = round(consumo_real * _kg(), 2)
    cumple = cumple_meta(consumo_real, m2_total, sacos_total, meta=_meta())
    sacos_muro = repartir_por_m2(float(sacos_total), m2s)
    factor = _factor_ajuste()

    # Desglose por muro: m², sacos repartidos y bloques teóricos (× factor).
    # `por_tipo` acumula los bloques por NOMBRE de tipo para el % final.
    filas_desglose = ""
    tot_pv = tot_ph = 0.0
    por_tipo: dict[str, float] = {}
    for i, m in enumerate(muros):
        teo = (bloques_teoricos_muro(
                   m["Largo_m"], m["Alto_m"], m["Num_dovelas"],
                   m["bloque_pv"], m["bloque_ph"], uso=m["Uso"])
               if (m["bloque_pv"] or m["bloque_ph"]) else {"pv": 0.0, "ph": 0.0})
        pv_aj = teo["pv"] * factor
        ph_aj = teo["ph"] * factor
        tot_pv += pv_aj
        tot_ph += ph_aj
        if m["tipo_pv"]:
            por_tipo[m["tipo_pv"]] = por_tipo.get(m["tipo_pv"], 0.0) + pv_aj
        if m["tipo_ph"]:
            por_tipo[m["tipo_ph"]] = por_tipo.get(m["tipo_ph"], 0.0) + ph_aj
        tipo_txt = " + ".join(t for t in (m["tipo_pv"], m["tipo_ph"]) if t) or "—"
        filas_desglose += (
            f"| {i + 1} · {m['uso_disp'].split(' ')[0]} | {tipo_txt} | "
            f"{m2s[i]:.2f} | {sacos_muro[i]:.1f} | {pv_aj:,.0f} | {ph_aj:,.0f} |\n"
        )

    desglose_md = ""
    if muros:
        desglose_md = (
            f"\n**Desglose por muro** (sacos repartidos por m² · bloques × Factor de Modulación {factor:g}):\n\n"
            "| Muro | Bloque(s) | m² | Sacos | Bloques P.V. | Bloques P.H. |\n"
            "|---|---|---|---|---|---|\n"
            f"{filas_desglose}"
            f"| **Total** | | **{m2_total:.2f}** | **{sacos_total:.1f}** | "
            f"**{tot_pv:,.0f}** | **{tot_ph:,.0f}** |\n"
        )
        # Relación aproximada por tipo de bloque (% del total de bloques del grupo).
        total_bloques = sum(por_tipo.values())
        if total_bloques > 0:
            filas_pct = "".join(
                f"| {t} | {v:,.0f} | {v / total_bloques * 100:.0f}% |\n"
                for t, v in sorted(por_tipo.items(), key=lambda kv: -kv[1]) if v > 0
            )
            desglose_md += (
                "\n**Relación aproximada por tipo** (% del total de bloques):\n\n"
                "| Tipo de bloque | Bloques | % |\n|---|---|---|\n"
                f"{filas_pct}"
                f"| **Total** | **{total_bloques:,.0f}** | **100%** |\n"
            )

    estado_txt = "✓ SÍ" if cumple else "✗ NO"
    st.info(
        f"""**⚡ Calculado automáticamente** — grupo de **{n_muros}** muro(s)

| Indicador | Valor |
|---|---|
| M² ejecutados (Σ Largo × Alto) | **{m2_total:.2f} m²** |
| ML dovelas (Σ # Dovelas × Alto) | **{ml_total:.2f} ml** |
| Consumo real ({sacos_total:.1f} sacos ÷ {m2_total:.2f} m²) | **{consumo_real:.3f} sac/m²** |
| Mortero por m² ({consumo_real:.3f} sac/m² × {_kg():g} kg/saco) | **{consumo_kg_m2:.1f} kg/m²** |
| Mortero TOTAL del grupo ({sacos_total:.1f} sacos × {_kg():g} kg/saco) | **{consumo_kg:.1f} kg** |
| Cumple meta (≤ {_meta():g}) | **{estado_txt}** |
{desglose_md}"""
    )

    # Guardar
    if st.button("💾 Guardar registro", type="primary"):
        errores = []
        if not oficial:
            errores.append("El campo **Oficial** es obligatorio.")
        if not sector:
            errores.append("El campo **Sector** es obligatorio (escribe el nuevo sector).")
        if not piso:
            errores.append("El campo **Piso** es obligatorio.")
        if n_muros == 0:
            errores.append("Agrega al menos un muro con **Largo** y **Alto** mayores que 0.")
        if hay_catalogo:
            faltan_pv = [str(i + 1) for i, m in enumerate(muros_normales)
                         if m["necesita_pv"] and not m["tipo_pv"]]
            faltan_ph = [str(i + 1) for i, m in enumerate(muros_normales)
                         if m["necesita_ph"] and not m["tipo_ph"]]
            faltan_mix = [str(i + 1) for i, m in enumerate(muros_mixtos)
                          if not (m["tipo_pv"] and m["tipo_ph"])]
            if faltan_pv:
                errores.append(f"Elige el **Bloque P.V.** en el/los muro(s): {', '.join(faltan_pv)}.")
            if faltan_ph:
                errores.append(f"Elige el **Bloque P.H.** en el/los muro(s): {', '.join(faltan_ph)}.")
            if faltan_mix:
                errores.append(f"En **➕ Muros mixtos**, elige **Dovelas + Redes** y **Relleno** en el/los muro(s): {', '.join(faltan_mix)}.")

        if errores:
            for e in errores:
                st.error(e)
            return

        # Guardia anti doble clic: un payload idéntico al recién guardado es un
        # doble envío, no un registro nuevo. La firma usa el tipo POR muro.
        firma_muros = [(m["Largo_m"], m["Alto_m"], m["Num_dovelas"],
                        m["uso_disp"], m["tipo_pv"], m["tipo_ph"]) for m in muros]
        firma = repr((str(fecha), oficial, ayudante, sector, piso, zona,
                      junta_cm, observaciones, float(sacos_total), firma_muros))
        ult = st.session_state.get("ultimo_registro_firma")
        if ult and ult[0] == firma and datetime.now().timestamp() - ult[1] < 120:
            st.warning(
                "⚠️ Este registro es **idéntico** al que se acaba de guardar — no se "
                "guardó otra vez (posible doble clic). Si de verdad es otro grupo "
                "igual, cambia algo (ej. Observaciones) o espera 2 minutos."
            )
            return

        base = {
            "Fecha": pd.to_datetime(fecha),
            "Oficial": oficial,
            "Ayudante": ayudante,
            "Sector": sector,
            "Piso": piso,
            "Zona": zona,
            "Observaciones": observaciones,
            "Timestamp_registro": datetime.now(),
        }

        try:
            with st.spinner("Guardando…"):
                # Cada muro ya trae su uso/bloque/tipo propios → la función reparte
                # los sacos por m² y arma los teóricos por muro.
                nuevas = construir_filas_grupo(
                    base, muros, sacos_total, kg_por_saco=_kg(), meta=_meta(),
                )
                agregar_registros(nuevas)  # append (eficiente en Supabase)
            st.cache_data.clear()
        except Exception as e:
            st.error(f"No se pudo guardar el registro: {e}")
            return

        st.session_state["ultimo_registro_firma"] = (firma, datetime.now().timestamp())
        st.session_state["flash"] = (
            f"Registro guardado · **{oficial}** · {sector} · {piso} · "
            f"{n_muros} muro(s) · {m2_total:.2f} m² · {sacos_total:.1f} sacos · "
            f"consumo {consumo_real:.3f} ({'cumple ✓' if cumple else 'NO cumple ✗'})."
        )
        # Limpiar el formulario: subir la "versión" hace que TODOS los widgets
        # (incluida la tabla de muros) nazcan de nuevo con sus valores por defecto.
        # Streamlit descarta solo el estado de las keys viejas no renderizadas.
        st.session_state["ingreso_nonce"] = n + 1
        st.rerun()


# ─────────────────────────────────────────────────────────────
# Pantalla 2 — Registros
# ─────────────────────────────────────────────────────────────
def pagina_registros(df: pd.DataFrame):
    st.header("📊 Registros")

    if df.empty:
        st.info("Aún no hay registros. Ve a **📋 Ingreso de datos** para agregar el primero.")
        return

    with st.expander("🔍 Filtros", expanded=True):
        f1, f2, f3 = st.columns(3)
        with f1:
            of = st.selectbox("Oficial", ["Todos"] + opciones_unicas(df, "Oficial"))
        with f2:
            se = st.selectbox("Sector", ["Todos"] + opciones_unicas(df, "Sector"))
        with f3:
            pi = st.selectbox("Piso", ["Todos"] + opciones_unicas(df, "Piso"))
        f4, f5 = st.columns(2)
        with f4:
            desde = st.date_input("Fecha desde", value=None)
        with f5:
            hasta = st.date_input("Fecha hasta", value=None)

    filtrado = df.copy()
    if of != "Todos":
        filtrado = filtrado[filtrado["Oficial"] == of]
    if se != "Todos":
        filtrado = filtrado[filtrado["Sector"] == se]
    if pi != "Todos":
        filtrado = filtrado[filtrado["Piso"] == pi]
    if desde is not None:
        filtrado = filtrado[filtrado["Fecha"] >= pd.to_datetime(desde)]
    if hasta is not None:
        filtrado = filtrado[filtrado["Fecha"] <= pd.to_datetime(hasta)]

    st.caption(f"**{len(filtrado)}** registros encontrados")

    display = preparar_display(filtrado)
    # Columna derivada solo para visualizar: kg de mortero por m² del registro.
    if "Consumo_real_sac_m2" in display.columns:
        display.insert(
            display.columns.get_loc("Consumo_real_sac_m2") + 1,
            "Mortero_kg_m2",
            (pd.to_numeric(display["Consumo_real_sac_m2"], errors="coerce") * _kg()).round(2),
        )
    styler = estilar_consumo(display.style).format(
        {
            "Largo_m": "{:.2f}", "Alto_m": "{:.2f}", "M2_ejecutados": "{:.2f}",
            "Num_sacos": "{:.1f}", "Consumo_real_sac_m2": "{:.3f}",
            "Mortero_kg_m2": "{:.1f}", "Consumo_mortero_kg": "{:.1f}",
            "ML_dovelas": "{:.2f}",
            "Bloques_PV_teo": "{:,.0f}", "Bloques_PH_teo": "{:,.0f}",
        },
        na_rep="—",
    )
    st.dataframe(styler, height=450, width="stretch")

    XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    st.caption("Descarga lo **filtrado** (lo que ves arriba) o **todo** el histórico:")
    d1, d2, d3 = st.columns(3)
    with d1:
        st.download_button(
            "⬇️ Excel (filtrado)",
            data=excel_datos_y_resumen(filtrado),
            file_name="registros_mamposteria.xlsx",
            mime=XLSX_MIME,
            width="stretch",
            help="Excel con 2 hojas: datos crudos + resumen por oficial.",
        )
    with d2:
        st.download_button(
            "⬇️ CSV (filtrado)",
            data=filtrado.to_csv(index=False).encode("utf-8"),
            file_name="registros_mamposteria.csv",
            mime="text/csv",
            width="stretch",
        )
    with d3:
        st.download_button(
            "⬇️ TODO el histórico (Excel)",
            data=excel_datos_y_resumen(df),
            file_name="registros_mamposteria_TODO.xlsx",
            mime=XLSX_MIME,
            width="stretch",
            help="Ignora los filtros: exporta todos los registros + resumen.",
        )


# ─────────────────────────────────────────────────────────────
# Pantalla 3 — Control
# ─────────────────────────────────────────────────────────────
def pagina_graficas(df: pd.DataFrame):
    st.header("📈 Control")

    if df.empty:
        st.info("Aún no hay datos para graficar.")
        return

    # ── Filtro por rango de fechas: afecta los KPIs y TODOS los gráficos ──
    # (Sirve para responder "¿qué pasó entre tal y tal fecha?"). Por defecto
    # toma el rango completo de los datos = muestra todo.
    fechas_validas = df["Fecha"].dropna()
    if not fechas_validas.empty:
        fmin, fmax = fechas_validas.min().date(), fechas_validas.max().date()
        st.caption("📅 **Filtrar por fechas**")
        cfa, cfb = st.columns(2)
        with cfa:
            desde = st.date_input("Desde", value=fmin, min_value=fmin, max_value=fmax,
                                  key="control_desde", format="YYYY-MM-DD")
        with cfb:
            hasta = st.date_input("Hasta", value=fmax, min_value=fmin, max_value=fmax,
                                  key="control_hasta", format="YYYY-MM-DD")
        if desde > hasta:               # por si invierten el orden
            desde, hasta = hasta, desde
        m_fecha = df["Fecha"].dt.date
        df = df[(m_fecha >= desde) & (m_fecha <= hasta)]
        rango_txt = "todo el periodo" if (desde, hasta) == (fmin, fmax) else f"{desde} → {hasta}"
        st.caption(f"Mostrando **{len(df)}** registro(s) · {rango_txt}.")
        if df.empty:
            st.info("No hay registros en ese rango de fechas. Amplía el rango.")
            return

    st.divider()

    # Tarjetas KPI — dos filas: arriba lo global, abajo lo de mamposteros.
    m2_total = df["M2_ejecutados"].sum()
    k1, k2, k3 = st.columns(3)
    k1.metric("Total M² ejecutados", f"{m2_total:,.1f} m²")
    prom = consumo_ratio(df)   # Σsacos ÷ Σm² (consistente con la barra KPI global)
    k2.metric(
        "Consumo promedio", f"{prom:.3f}",
        delta=f"{prom - _meta():+.3f} vs meta", delta_color="inverse",
    )
    k2.caption(f"= {prom * _kg():.1f} kg/m²")
    cumplen = int(df["Cumple_meta"].sum())
    k3.metric("Registros que cumplen", f"{cumplen} / {len(df)}")

    k4, k5, k6 = st.columns(3)
    n_ofic = df["Oficial"].nunique()
    k4.metric("Oficiales distintos", n_ofic)
    # Promedio acumulado por mampostero en el periodo (Σ m² ÷ nº oficiales activos).
    prom_m2 = m2_total / n_ofic if n_ofic else 0.0
    k5.metric(
        "Promedio M²/mampostero", f"{prom_m2:,.1f} m²",
        help="Total de m² ÷ nº de mamposteros activos (oficiales que pegaron en el "
             "periodo). Es el acumulado del periodo filtrado, no por día.",
    )
    # Ritmo diario: promedio de m² que pega un mampostero en un día trabajado.
    # = Σ m² ÷ Σ días trabajados (cada par oficial+día cuenta como un día-mampostero),
    # igual que el m²/día del cierre y comparable con la meta de m²/día.
    df_fo = df.dropna(subset=["Fecha", "Oficial"])
    dias_mamp = (df_fo.assign(_d=df_fo["Fecha"].dt.date)
                 .drop_duplicates(["Oficial", "_d"]).shape[0])
    prom_m2_dia = df_fo["M2_ejecutados"].sum() / dias_mamp if dias_mamp else 0.0
    k6.metric(
        "Prom. M²/mampostero-día", f"{prom_m2_dia:,.1f} m²",
        help="Promedio de m² que pega un mampostero en un día trabajado: total de m² ÷ "
             "días trabajados (cada día que un oficial pegó cuenta como uno). Es el "
             "ritmo diario, comparable con la meta de m²/día.",
    )

    st.divider()

    # ═══════════ Capítulo: Metros cuadrados (m²) ═══════════
    st.subheader("Metros cuadrados (m²)")

    oficiales_m2 = sorted(df["Oficial"].dropna().astype(str).unique().tolist())
    sel_m2 = st.multiselect(
        "Mamposteros a mostrar", oficiales_m2,
        key="graf_oficiales_m2", placeholder="Todos (elige para comparar)",
        help="Déjalo vacío para ver a todos; o elige uno o varios para comparar.",
    )
    ofis_m2 = sel_m2 or oficiales_m2   # vacío = todos
    por_oficial_m2 = (
        df[df["Oficial"].isin(ofis_m2)]
        .groupby("Oficial", as_index=False)["M2_ejecutados"].sum()
        .sort_values("M2_ejecutados", ascending=False)
    )
    if por_oficial_m2.empty:
        st.info("No hay m² registrados para los mamposteros elegidos.")
    else:
        fig1 = px.bar(
            por_oficial_m2, x="Oficial", y="M2_ejecutados",
            title="M² ejecutados por oficial", color_discrete_sequence=["#2980b9"],
        )
        fig1.update_layout(xaxis_title="", yaxis_title="M² ejecutados")
        st.plotly_chart(fig1, width="stretch")

    # Evolución diaria (M² + nº de mamposteros), filtrable por mes/semana
    MESES_EVO = ["ene", "feb", "mar", "abr", "may", "jun",
                 "jul", "ago", "sep", "oct", "nov", "dic"]
    df_evo = df.dropna(subset=["Fecha"]).copy()
    por_dia = None
    if df_evo.empty:
        st.info("Aún no hay registros con fecha para la evolución diaria.")
    else:
        fc1, fc2 = st.columns([1, 2])
        with fc1:
            modo_evo = st.radio("Ver evolución", ["Todo", "Por mes", "Por semana"],
                                horizontal=True, key="evo_modo")
        if modo_evo == "Por mes":
            df_evo["_per"] = df_evo["Fecha"].dt.to_period("M")
            opts = sorted(df_evo["_per"].unique(), reverse=True)
            with fc2:
                sel = st.selectbox(
                    "Mes", opts, key="evo_mes",
                    format_func=lambda p: f"{MESES_EVO[p.month - 1]} {p.year}")
            df_evo = df_evo[df_evo["_per"] == sel]
        elif modo_evo == "Por semana":
            df_evo["_per"] = df_evo["Fecha"].dt.to_period("W")
            opts = sorted(df_evo["_per"].unique(), reverse=True)
            with fc2:
                sel = st.selectbox(
                    "Semana", opts, key="evo_sem",
                    format_func=lambda p: f"{p.start_time:%d/%m} – {p.end_time:%d/%m/%Y}")
            df_evo = df_evo[df_evo["_per"] == sel]

        df_evo["Día"] = df_evo["Fecha"].dt.date
        por_dia = (
            df_evo.groupby("Día", as_index=False)
            .agg(M2_ejecutados=("M2_ejecutados", "sum"),
                 Mamposteros=("Oficial", "nunique"))
            .sort_values("Día")
        )

    if por_dia is not None and not por_dia.empty:
        # Eje X como TEXTO de fecha (dd/mm/aaaa): con pocos días Plotly metía marcas
        # con horas (confuso). Como categoría de texto, nunca muestra la hora.
        por_dia["DíaTxt"] = [d.strftime("%d/%m/%Y") for d in por_dia["Día"]]
        # M² como línea (eje izq.) y nº de mamposteros como barras suaves (eje der.).
        fig3 = go.Figure()
        fig3.add_trace(go.Bar(
            x=por_dia["DíaTxt"], y=por_dia["Mamposteros"], name="Mamposteros",
            marker_color="#aed6f1", opacity=0.6, yaxis="y2",
            hovertemplate="%{x}<br>Mamposteros: %{y}<extra></extra>",
        ))
        fig3.add_trace(go.Scatter(
            x=por_dia["DíaTxt"], y=por_dia["M2_ejecutados"], name="M² ejecutados",
            mode="lines+markers", line=dict(color="#2980b9"),
            hovertemplate="%{x}<br>M²: %{y:.1f}<extra></extra>",
        ))
        fig3.update_layout(
            title="Evolución diaria: M² ejecutados y nº de mamposteros",
            xaxis_title="",
            yaxis=dict(title="M² ejecutados"),
            yaxis2=dict(title="Mamposteros", overlaying="y", side="right",
                        showgrid=False, rangemode="tozero", dtick=1),
            legend=dict(orientation="h", yanchor="bottom", y=1.02,
                        xanchor="right", x=1),
        )
        st.plotly_chart(fig3, width="stretch")

    # ── M² ejecutados con filtros EN CASCADA (piso → zona → mampostero) ──
    # Al elegir el piso, las zonas y los mamposteros se acotan a ese piso, para no
    # buscar entre todas las zonas de la obra. Multiselect vacío = "todas/todos".
    st.markdown("**M² ejecutados — filtrar por piso, zona y mampostero**")
    ff1, ff2, ff3 = st.columns(3)

    # 1) Piso (todas las opciones).
    pisos_op = opciones_unicas(df, "Piso")
    with ff1:
        sel_pisos = st.multiselect("Piso", pisos_op, key="m2f_pisos",
                                   placeholder="Todos los pisos")
    df_m2f = df if not sel_pisos else df[df["Piso"].astype(str).str.strip().isin(sel_pisos)]

    # 2) Zona: solo las del/los piso(s) elegido(s). Se sanea la selección previa
    #    para que no quede una zona que ya no aplica (evita error de Streamlit).
    zonas_op = opciones_unicas(df_m2f, "Zona")
    if st.session_state.get("m2f_zonas"):
        st.session_state["m2f_zonas"] = [z for z in st.session_state["m2f_zonas"] if z in zonas_op]
    with ff2:
        sel_zonas = st.multiselect("Zona", zonas_op, key="m2f_zonas",
                                   placeholder="Todas las zonas")
    if sel_zonas:
        df_m2f = df_m2f[df_m2f["Zona"].astype(str).str.strip().isin(sel_zonas)]

    # 3) Mampostero: solo los que pegaron en ese piso/zona.
    ofis_op = opciones_unicas(df_m2f, "Oficial")
    if st.session_state.get("m2f_ofis"):
        st.session_state["m2f_ofis"] = [o for o in st.session_state["m2f_ofis"] if o in ofis_op]
    with ff3:
        sel_ofis = st.multiselect("Mampostero", ofis_op, key="m2f_ofis",
                                  placeholder="Todos")
    if sel_ofis:
        df_m2f = df_m2f[df_m2f["Oficial"].astype(str).str.strip().isin(sel_ofis)]

    agrupar = st.radio("Ver por", ["Piso", "Zona", "Mampostero"], horizontal=True,
                       key="m2f_agrupar")
    col_dim = {"Piso": "Piso", "Zona": "Zona", "Mampostero": "Oficial"}[agrupar]
    if df_m2f.empty:
        st.info("No hay datos con esos filtros. Ajusta zona, piso o mampostero.")
    else:
        # fillna("") ANTES de astype(str): con el dtype str nativo, astype(str)
        # deja el faltante como NaN y groupby lo descartaría (se perderían m²).
        agg_src = df_m2f.copy()
        agg_src[col_dim] = agg_src[col_dim].fillna("").astype(str).str.strip()
        agg_src.loc[agg_src[col_dim] == "", col_dim] = "(sin dato)"
        agg_m2 = (agg_src.groupby(col_dim, as_index=False)["M2_ejecutados"].sum()
                  .sort_values("M2_ejecutados", ascending=False))
        fig_m2f = px.bar(
            agg_m2, x=col_dim, y="M2_ejecutados", text_auto=".0f",
            title=f"M² ejecutados por {agrupar.lower()}",
            color_discrete_sequence=["#16a085"],
        )
        fig_m2f.update_layout(xaxis_title="", yaxis_title="M² ejecutados")
        st.plotly_chart(fig_m2f, width="stretch")
        st.caption(f"Total filtrado: **{df_m2f['M2_ejecutados'].sum():,.1f} m²** · "
                   f"{len(df_m2f)} registro(s).")

    st.divider()
    # ═══════════ Capítulo: Sacos y mortero ═══════════
    st.subheader("Sacos y mortero")
    cm1, cm2 = st.columns(2)

    with cm1:
        meta = _meta()
        # Filtro directo por nombre: quitar a un mampostero de poco volumen (cuyo
        # consumo sacos÷m² se dispara) reajusta la escala y deja ver a los demás.
        oficiales_todos = sorted(df["Oficial"].dropna().astype(str).unique().tolist())
        oficiales_sel = st.multiselect(
            "Mamposteros a mostrar", oficiales_todos,
            key="graf_oficiales_consumo", placeholder="Todos (elige para filtrar)",
            help="Déjalo vacío para ver a todos. Elige a algunos para enfocar "
                 "(p. ej. quitar a uno que hizo muy poco y no volvió: su consumo se "
                 "dispara y achica a los demás). La tabla de abajo muestra a todos.",
        )
        ofis_cons = oficiales_sel or oficiales_todos   # vacío = todos
        por_oficial_cons = consumo_por(df[df["Oficial"].isin(ofis_cons)], "Oficial")
        por_oficial_cons = por_oficial_cons.sort_values("Consumo", ascending=False)
        if por_oficial_cons.empty:
            st.info("No hay consumo registrado para los mamposteros elegidos.")
        else:
            por_oficial_cons["color"] = por_oficial_cons["Consumo"].apply(
                lambda v: "Supera meta" if v > meta else "Cumple meta"
            )
            fig2 = px.bar(
                por_oficial_cons, x="Oficial", y="Consumo",
                color="color",
                color_discrete_map={"Supera meta": "#c0392b", "Cumple meta": "#1e8449"},
                title=f"Consumo por oficial (Σsacos÷Σm², meta = {meta:g})",
            )
            fig2.add_hline(
                y=meta, line_dash="dash", line_color="red",
                annotation_text=f"Meta {meta:g}", annotation_position="top left",
            )
            fig2.update_layout(xaxis_title="", yaxis_title="Consumo (sac/m²)", legend_title="")
            st.plotly_chart(fig2, width="stretch")

    with cm2:
        oficiales_sacos = sorted(df["Oficial"].dropna().astype(str).unique().tolist())
        sel_sacos = st.multiselect(
            "Mamposteros a mostrar", oficiales_sacos,
            key="graf_oficiales_sacos", placeholder="Todos (elige para comparar)",
            help="Déjalo vacío para ver a todos; o elige uno o varios para comparar.",
        )
        ofis_sacos = sel_sacos or oficiales_sacos   # vacío = todos
        sacos_oficial = (
            df[df["Oficial"].isin(ofis_sacos)]
            .groupby("Oficial", as_index=False)["Num_sacos"].sum()
            .sort_values("Num_sacos", ascending=False)
        )
        if sacos_oficial.empty:
            st.info("No hay sacos registrados para los mamposteros elegidos.")
        else:
            figs1 = px.bar(
                sacos_oficial, x="Oficial", y="Num_sacos",
                title="Sacos consumidos por mampostero",
                color_discrete_sequence=["#8e44ad"],
            )
            figs1.update_layout(xaxis_title="", yaxis_title="Sacos")
            st.plotly_chart(figs1, width="stretch")

    dim = st.selectbox(
        "Agrupar sacos por", ["Piso", "Zona", "Sector"], key="graf_sacos_dim",
        help="Elige la dimensión para ver dónde se gastaron los sacos.",
    )
    base_dim = df.copy()
    base_dim[dim] = base_dim[dim].fillna("").astype(str).str.strip()
    base_dim.loc[base_dim[dim] == "", dim] = "(sin dato)"
    sacos_dim = (
        base_dim.groupby(dim, as_index=False)["Num_sacos"].sum()
        .sort_values("Num_sacos", ascending=False)
    )
    figs2 = px.bar(
        sacos_dim, x=dim, y="Num_sacos",
        title=f"Sacos consumidos por {dim.lower()}",
        color_discrete_sequence=["#16a085"],
    )
    figs2.update_layout(xaxis_title="", yaxis_title="Sacos")
    st.plotly_chart(figs2, width="stretch")

    # ── Tendencia en el tiempo: cuánto se consume por día/semana ──────────
    st.markdown("**Tendencia en el tiempo** — cuánto se consume por período.")
    df_t = df.dropna(subset=["Fecha"]).copy()
    if df_t.empty:
        st.info("Aún no hay registros con fecha para la tendencia de consumo.")
    else:
        periodo = st.radio("Período", ["Semana", "Día"], horizontal=True,
                           key="graf_sacos_periodo")
        if periodo == "Semana":
            df_t["_periodo"] = df_t["Fecha"].dt.to_period("W").apply(lambda p: p.start_time)
        else:
            df_t["_periodo"] = df_t["Fecha"].dt.normalize()
        agg = (df_t.groupby("_periodo", as_index=False)
               .agg(Sacos=("Num_sacos", "sum"), M2=("M2_ejecutados", "sum"))
               .sort_values("_periodo"))
        agg["Consumo"] = agg["Sacos"] / agg["M2"].where(agg["M2"] > 0)
        meta_t = _meta()
        ct1, ct2 = st.columns(2)
        with ct1:
            figt1 = px.bar(
                agg, x="_periodo", y="Sacos",
                title=f"Sacos consumidos por {periodo.lower()}",
                color_discrete_sequence=["#8e44ad"],
            )
            figt1.update_layout(xaxis_title="", yaxis_title="Sacos")
            st.plotly_chart(figt1, width="stretch")
        with ct2:
            figt2 = px.line(
                agg, x="_periodo", y="Consumo", markers=True,
                title=f"Consumo (sac/m²) por {periodo.lower()} · meta {meta_t:g}",
            )
            figt2.add_hline(y=meta_t, line_dash="dash", line_color="red",
                            annotation_text=f"Meta {meta_t:g}", annotation_position="top left")
            figt2.update_traces(line_color="#16a085")
            figt2.update_layout(xaxis_title="", yaxis_title="Consumo (sac/m²)")
            st.plotly_chart(figt2, width="stretch")

    st.divider()

    # ═══════════ Capítulo: Presencia de mamposteros ═══════════
    st.subheader("Presencia de mamposteros")
    st.caption(
        "Cuenta como presente quien aparece ese día como **oficial O como ayudante** "
        "(así un mampostero registrado de ayudante también cuenta como que vino). "
        "Cada barra va del **primer** al **último** día con registro. "
        "🟢 Activo · 🟡 En pausa (7+ días sin registrar) · ⚪ Inactivo (30+ días)."
    )

    df_f = df.dropna(subset=["Fecha"])
    if df_f.empty:
        # Sin fechas válidas no hay línea de tiempo (ref sería NaT y reventaría).
        st.info("Aún no hay registros con fecha para la línea de presencia.")
    else:
        ref = df_f["Fecha"].max()
        # Presencia REAL = la persona aparece como Oficial O como Ayudante ese día.
        _of = df_f[["Oficial", "Fecha"]].rename(columns={"Oficial": "Persona"}).assign(Rol="oficial")
        _ay = df_f[["Ayudante", "Fecha"]].rename(columns={"Ayudante": "Persona"}).assign(Rol="ayudante")
        larga = pd.concat([_of, _ay], ignore_index=True)
        larga["Persona"] = larga["Persona"].fillna("").astype(str).str.strip()
        larga = larga[larga["Persona"] != ""]

        def _dias_rol(rol: str) -> pd.Series:
            sub = larga[larga["Rol"] == rol]
            return sub.groupby("Persona")["Fecha"].apply(lambda s: s.dt.date.nunique())

        dias_of_s, dias_ay_s = _dias_rol("oficial"), _dias_rol("ayudante")
        # m² liderado como oficial (el ayudante no "lidera" m²): solo para el hover.
        m2_of = (df_f.assign(_o=df_f["Oficial"].fillna("").astype(str).str.strip())
                 .groupby("_o")["M2_ejecutados"].sum())

        pres = (
            larga.groupby("Persona")
            .agg(
                primer_dia=("Fecha", "min"),
                ultimo_dia=("Fecha", "max"),
                dias=("Fecha", lambda s: s.dt.date.nunique()),
            )
            .reset_index()
        )
        pres["dias_oficial"] = pres["Persona"].map(dias_of_s).fillna(0).astype(int)
        pres["dias_ayudante"] = pres["Persona"].map(dias_ay_s).fillna(0).astype(int)
        pres["m2_total"] = pres["Persona"].map(m2_of).fillna(0.0)
        pres["dias_sin_venir"] = (ref - pres["ultimo_dia"]).dt.days
        pres["Estado"] = pres["dias_sin_venir"].apply(estado_presencia)
        pres["fin"] = pres["ultimo_dia"] + pd.Timedelta(days=1)   # ancho visible si trabajó 1 día

        orden = pres.sort_values("ultimo_dia")["Persona"].tolist()
        fig4 = px.timeline(
            pres, x_start="primer_dia", x_end="fin", y="Persona", color="Estado",
            color_discrete_map={"Activo": "#1e8449", "En pausa": "#f39c12", "Inactivo": "#95a5a6"},
            hover_data={"dias": True, "dias_oficial": True, "dias_ayudante": True,
                        "m2_total": ":.0f", "dias_sin_venir": True,
                        "primer_dia": False, "fin": False},
        )
        # Marcas de los días con registro real (en cualquier rol) dentro de cada barra.
        dias_trab = larga.drop_duplicates(["Persona", "Fecha"])
        fig4.add_trace(go.Scatter(
            x=dias_trab["Fecha"], y=dias_trab["Persona"], mode="markers",
            marker=dict(color="rgba(44,62,80,0.45)", size=7, symbol="line-ns-open"),
            name="día con registro", hoverinfo="skip",
        ))
        # x en ms epoch: en plotly 6 add_vline+annotation falla con un Timestamp directo.
        fig4.add_vline(x=ref.timestamp() * 1000, line_dash="dash", line_color="#7f8c8d",
                       annotation_text="último dato")
        fig4.update_yaxes(categoryorder="array", categoryarray=orden, title="")
        fig4.update_xaxes(title="")
        fig4.update_layout(height=max(300, len(pres) * 38), legend_title="")
        st.plotly_chart(fig4, width="stretch")

    st.divider()

    # Tabla resumen por oficial
    st.subheader("Resumen por oficial")
    resumen = resumen_por(df, "Oficial")

    styler = estilar_consumo(resumen.style, columna="Consumo_promedio").format(
        {
            "M2_total": "{:.1f}", "Consumo_promedio": "{:.3f}",
            "Sacos_total": "{:.1f}", "Pct_cumple": "{:.0f}%",
        }
    )
    st.dataframe(styler, width="stretch")

    st.download_button(
        "⬇️ Descargar resumen (Excel)",
        data=excel_datos_y_resumen(df),
        file_name="resumen_mamposteria.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        help="Excel con 2 hojas: datos crudos + resumen por oficial.",
    )


# ─────────────────────────────────────────────────────────────
# Pantalla 4 — Cierres (diario y semanal)
# ─────────────────────────────────────────────────────────────
def _m2_sector(sub: pd.DataFrame, sector: str) -> float:
    return sub.loc[sub["Sector"] == sector, "M2_ejecutados"].sum()


def pagina_cierres(df: pd.DataFrame):
    st.header("📅 Cierres")

    if df.empty:
        st.info("Aún no hay datos para cerrar.")
        return

    df = df.dropna(subset=["Fecha"]).copy()
    if df.empty:
        st.info("Los registros no tienen una fecha válida; no se puede hacer el cierre.")
        return
    tab_dia, tab_semana, tab_comparar = st.tabs(
        ["📆 Cierre diario", "🗓️ Cierre semanal", "📊 Comparar periodos"]
    )

    # ── Cierre diario ────────────────────────────────────────
    with tab_dia:
        fechas = sorted(df["Fecha"].dt.date.unique())
        dia = st.date_input(
            "Día a cerrar", value=fechas[-1],
            min_value=fechas[0], max_value=fechas[-1],
        )
        del_dia = df[df["Fecha"].dt.date == dia]

        m2_torre = _m2_sector(del_dia, "Torre")
        m2_plat = _m2_sector(del_dia, "Plataforma")
        m2_total = del_dia["M2_ejecutados"].sum()

        t1, t2, t3 = st.columns(3)
        t1.metric("🏢 TOTAL M² TORRE", f"{m2_torre:,.2f} m²")
        t2.metric("🟦 TOTAL M² PLATAFORMA", f"{m2_plat:,.2f} m²")
        t3.metric("📌 TOTAL M² DÍA", f"{m2_total:,.2f} m²")

        if del_dia.empty:
            st.info("No hay registros para ese día.")
        else:
            cols = ["Sector", "Oficial", "Ayudante", "Piso", "Zona",
                    "M2_ejecutados", "Num_sacos", "Consumo_real_sac_m2", "Cumple_meta"]
            tabla = preparar_display(del_dia[cols].sort_values(["Sector", "Oficial"]))
            styler = estilar_consumo(tabla.style).format(
                {"M2_ejecutados": "{:.2f}", "Num_sacos": "{:.1f}",
                 "Consumo_real_sac_m2": "{:.3f}"}, na_rep="—",
            )
            st.dataframe(styler, width="stretch")

    # ── Cierre semanal ───────────────────────────────────────
    with tab_semana:
        df_sem = df.assign(_sem=df["Fecha"].dt.to_period("W"))
        opciones = sorted(df_sem["_sem"].unique(), reverse=True)

        def etiqueta(p):
            return f"Semana {p.start_time:%d/%m} – {p.end_time:%d/%m/%Y}"

        sel = st.selectbox("Semana", opciones, format_func=etiqueta)
        del_sem = df_sem[df_sem["_sem"] == sel]

        s1, s2, s3 = st.columns(3)
        s1.metric("🏢 M² Torre (semana)", f"{_m2_sector(del_sem, 'Torre'):,.1f} m²")
        s2.metric("🟦 M² Plataforma (semana)", f"{_m2_sector(del_sem, 'Plataforma'):,.1f} m²")
        s3.metric("📌 M² Total (semana)", f"{del_sem['M2_ejecutados'].sum():,.1f} m²")

        # Base por mampostero (alimenta metas y el detalle de abajo).
        cierre = resumen_por(
            del_sem, "Oficial",
            extra={"Dias": ("Fecha", lambda s: s.dt.date.nunique())},
        )

        # Metas: por defecto (800 m²/piso, 10 m²/día). Van en un expander colapsado
        # para que el residente no se confunda con campos editables entre resultados.
        with st.expander("⚙️ Ajustar metas (ábrelo solo si hubo festivo o cambian)"):
            mc1, mc2, mc3 = st.columns(3)
            with mc1:
                meta_piso = st.number_input(
                    "Meta por piso (m²/sem)", min_value=0.0, value=800.0, step=50.0,
                    key="cs_meta_piso", help="Meta semanal de m² por cada piso.",
                )
            with mc2:
                meta_dia = st.number_input(
                    "Meta por mampostero (m²/día)", min_value=0.0, value=10.0, step=1.0,
                    key="cs_meta_dia",
                    help="Cada mampostero debe pegar este m² por día trabajado.",
                )
            with mc3:
                dias_lab = st.number_input(
                    "Días laborables esta semana", min_value=1, max_value=7, value=5,
                    step=1, key="cs_dias_lab",
                    help="Lun–Vie = 5. Bájalo si hubo festivo y la meta se ajusta.",
                )
            meta_of = meta_dia * dias_lab
            st.caption(
                f"Meta semanal por mampostero = {meta_dia:g} m²/día × {dias_lab} día(s) "
                f"= **{meta_of:g} m²**."
            )

        # ── Avance vs meta de la semana ──────────────────────
        st.subheader("Avance vs meta de la semana")

        st.markdown("**Por piso**")
        piso = (del_sem.groupby("Piso", as_index=False)["M2_ejecutados"].sum()
                .rename(columns={"M2_ejecutados": "M2"}))
        piso["Falta"] = (meta_piso - piso["M2"]).clip(lower=0)
        piso["Estado"] = (piso["M2"] >= meta_piso).map({True: "Cumple", False: "No cumple"})

        # La meta de m²/piso se puede cumplir en UN solo piso O como la SUMA de lo
        # pegado en varios pisos: a veces el equipo reparte el trabajo en la semana
        # (p. ej. 300 en un piso + 300 en otro + 200 en otro = la meta de un piso).
        m2_combinado = float(piso["M2"].sum())
        st.caption(
            f"La meta de **{meta_piso:g} m²** se puede cumplir en un solo piso **o** "
            "sumando lo pegado en varios pisos (cuando el equipo reparte el trabajo)."
        )

        figp = px.bar(
            piso, x="M2", y="Piso", orientation="h", color="Estado", text_auto=".0f",
            color_discrete_map={"Cumple": "#1e8449", "No cumple": "#c0392b"},
            title=f"M² pegados por piso vs meta ({meta_piso:g} m²)",
        )
        figp.add_vline(x=meta_piso, line_dash="dash", line_color="red",
                       annotation_text="meta", annotation_position="top")
        figp.update_yaxes(categoryorder="total ascending", title="")
        figp.update_layout(xaxis_title="M² pegados", legend_title="")
        st.plotly_chart(figp, width="stretch")

        # Estado COMBINADO: la suma de todos los pisos contra la meta de un piso.
        if m2_combinado >= meta_piso:
            st.success(
                f"✅ Meta cumplida en conjunto: los pisos trabajados suman "
                f"{m2_combinado:,.0f} m² (≥ {meta_piso:g} m²)."
            )
        else:
            st.warning(
                f"⚠️ En conjunto van {m2_combinado:,.0f} m² de {meta_piso:g} m² "
                f"(faltan {meta_piso - m2_combinado:,.0f} m²)."
            )

        # Detalle informativo: qué pisos, por sí solos, no llegaron a la meta.
        pisos_no = piso[piso["Estado"] == "No cumple"].sort_values("Falta", ascending=False)
        if not pisos_no.empty:
            det = ", ".join(f"Piso {r.Piso} ({r.M2:.0f} m²)" for r in pisos_no.itertuples())
            st.caption(
                f"Por sí solos no llegaron a {meta_piso:g} m²: {det}. "
                "No es un problema si la suma combinada ya cumple."
            )

        st.markdown("**Por mampostero**")
        ofi = cierre.rename(columns={"M2_total": "M2"})[["Oficial", "M2", "Dias"]].copy()
        ofi["m2_dia"] = ofi["M2"] / ofi["Dias"].where(ofi["Dias"] > 0)
        ofi["Meta"] = meta_of
        ofi["Falta"] = (meta_of - ofi["M2"]).clip(lower=0)
        ofi["Estado"] = (ofi["M2"] >= meta_of).map({True: "Cumple", False: "No cumple"})
        figo = px.bar(
            ofi, x="M2", y="Oficial", orientation="h", color="Estado", text_auto=".0f",
            color_discrete_map={"Cumple": "#1e8449", "No cumple": "#c0392b"},
            title=f"M² ejecutados por mampostero vs meta ({meta_of:g} m²)",
        )
        figo.add_vline(x=meta_of, line_dash="dash", line_color="red",
                       annotation_text="meta", annotation_position="top")
        figo.update_yaxes(categoryorder="total ascending", title="")
        figo.update_layout(xaxis_title="M² pegados", legend_title="")
        st.plotly_chart(figo, width="stretch")

        ofi_no = ofi[ofi["Estado"] == "No cumple"].sort_values("Falta", ascending=False)
        if ofi_no.empty:
            st.success(f"✅ Todos los mamposteros alcanzaron sus {meta_of:g} m².")
        else:
            st.warning(f"⚠️ {len(ofi_no)} mampostero(s) NO alcanzaron los {meta_of:g} m² "
                       "de la semana. Por qué:")
            for r in ofi_no.itertuples():
                ritmo = "" if pd.isna(r.m2_dia) else f", ritmo {r.m2_dia:.1f} m²/día"
                st.write(
                    f"- **{r.Oficial}**: {r.M2:.1f} m² en {int(r.Dias)} día(s) "
                    f"(faltó {r.Falta:.0f} m²{ritmo})."
                )
            st.caption(
                f"**m²/día** = ritmo real por día trabajado (meta {meta_dia:g}). Si el "
                "ritmo es ≥ meta pero faltó m², fue por días no trabajados (p. ej. festivo "
                "o ausencia), no por lentitud."
            )

        # Detalle de consumo/sacos: disponible pero oculto para no saturar la vista.
        with st.expander("📋 Ver detalle por mampostero (consumo y sacos)"):
            styler = estilar_consumo(cierre.style, columna="Consumo_promedio").format(
                {"M2_total": "{:.1f}", "Sacos_total": "{:.1f}",
                 "Consumo_promedio": "{:.3f}", "Pct_cumple": "{:.0f}%"}
            )
            st.dataframe(styler, width="stretch")
            st.caption("Los M² se atribuyen al **oficial**; el ayudante queda "
                       "registrado pero no suma m² aparte.")

        resumen_comb = pd.DataFrame([{
            "Meta_piso": meta_piso,
            "Pisos_trabajados": len(piso),
            "M2_combinado": m2_combinado,
            "Falta": max(meta_piso - m2_combinado, 0.0),
            "Estado": "Cumple" if m2_combinado >= meta_piso else "No cumple",
        }])
        st.download_button(
            "⬇️ Descargar cierre semanal (Excel)",
            data=excel_libro({
                "Resumen_combinado": resumen_comb,
                "Mamposteros": cierre,
                "Metas_pisos": piso[["Piso", "M2", "Falta", "Estado"]],
                "Metas_mamposteros": ofi[["Oficial", "Dias", "M2", "m2_dia",
                                          "Meta", "Falta", "Estado"]],
            }),
            file_name=f"cierre_semanal_{sel.start_time:%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # ── Comparar periodos (semanas / meses) ──────────────────
    with tab_comparar:
        st.caption(
            "Compara el avance entre **semanas** o **meses** de la obra: M², sacos "
            "y consumo de mortero por periodo, con gráfica y descarga."
        )
        MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun",
                    "jul", "ago", "sep", "oct", "nov", "dic"]
        c1, c2 = st.columns(2)
        with c1:
            gran = st.radio("Agrupar por", ["Semana", "Mes"], horizontal=True,
                            key="cmp_gran")
        with c2:
            metrica = st.selectbox(
                "Métrica a comparar",
                ["M² ejecutados", "Sacos", "Consumo (sac/m²)"], key="cmp_metrica",
            )

        base = df.copy()
        base["_per"] = base["Fecha"].dt.to_period("W" if gran == "Semana" else "M")

        def _lbl(p):
            if gran == "Semana":
                return f"{p.start_time:%d/%m} – {p.end_time:%d/%m/%Y}"
            return f"{MESES_ES[p.month - 1]} {p.year}"

        # Una fila por periodo. El consumo es ratio de sumas (Σsacos÷Σm²), igual
        # que en el resto de la app, no el promedio de los consumos por fila.
        comp = (
            base.groupby("_per", as_index=False)
            .agg(M2=("M2_ejecutados", "sum"), Sacos=("Num_sacos", "sum"),
                 Registros=("Oficial", "count"),
                 Dias=("Fecha", lambda s: s.dt.date.nunique()))
            .sort_values("_per")
        )
        comp["Consumo"] = comp["Sacos"] / comp["M2"].where(comp["M2"] > 0)
        comp["Periodo"] = comp["_per"].apply(_lbl)

        # En modo Semana, subfiltro por mes para no listar todas las semanas de la
        # obra: cada semana se agrupa bajo el mes de su lunes (inicio). En modo Mes
        # no se aplica (queda como estaba).
        suf = "all"
        if gran == "Semana":
            comp["Mes"] = comp["_per"].apply(
                lambda p: f"{MESES_ES[p.start_time.month - 1]} {p.start_time.year}"
            )
            meses_disp = list(dict.fromkeys(comp["Mes"]))  # ya viene cronológico
            sel_meses = st.multiselect(
                "Filtrar por mes", meses_disp, default=meses_disp,
                key="cmp_meses_semana",
                help="Acota las semanas a uno o varios meses; cada semana se "
                     "cuenta en el mes de su lunes.",
            )
            comp = comp[comp["Mes"].isin(sel_meses)]
            suf = "|".join(sel_meses) if sel_meses else "none"

        # Selección libre de periodos: comparar semanas/meses salteados (p. ej.
        # 1.ª semana de junio vs 3.ª de diciembre). La key incluye granularidad y
        # meses elegidos para que al cambiarlos no queden semanas inválidas.
        periodos_disp = comp["Periodo"].tolist()
        sel_periodos = st.multiselect(
            "Periodos a comparar", periodos_disp, default=periodos_disp,
            key=f"cmp_periodos_{gran}_{suf}",
            help="Elige las semanas/meses a comparar; pueden ir salteados "
                 "(p. ej. 1.ª semana de junio y 3.ª de diciembre).",
        )
        comp = comp[comp["Periodo"].isin(sel_periodos)]
        if comp.empty:
            st.info("Selecciona al menos un periodo para comparar.")
        else:
            col_y = {"M² ejecutados": "M2", "Sacos": "Sacos",
                     "Consumo (sac/m²)": "Consumo"}[metrica]
            fig = px.bar(
                comp, x="Periodo", y=col_y, title=f"{metrica} por {gran.lower()}",
                category_orders={"Periodo": comp["Periodo"].tolist()},  # cronológico
                color_discrete_sequence=["#2980b9"], text_auto=".1f",
            )
            if col_y == "Consumo":
                fig.add_hline(y=_meta(), line_dash="dash", line_color="red",
                              annotation_text=f"Meta {_meta():g}",
                              annotation_position="top left")
            fig.update_layout(xaxis_title="", yaxis_title=metrica, showlegend=False)
            st.plotly_chart(fig, width="stretch")

            tabla_cmp = comp[["Periodo", "Registros", "Dias", "M2", "Sacos", "Consumo"]].rename(
                columns={"M2": "M2_total", "Sacos": "Sacos_total",
                         "Consumo": "Consumo_sac_m2"}
            )
            st.dataframe(
                tabla_cmp.style.format(
                    {"M2_total": "{:.1f}", "Sacos_total": "{:.1f}",
                     "Consumo_sac_m2": "{:.3f}"}, na_rep="—"
                ),
                width="stretch",
            )
            st.download_button(
                "⬇️ Descargar comparación (Excel)",
                data=excel_bytes(tabla_cmp),
                file_name=f"comparacion_{gran.lower()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


# ─────────────────────────────────────────────────────────────
# Pantalla 5 — Materiales y desperdicio (bloques P.V./P.H.)
# ─────────────────────────────────────────────────────────────
def _filtrar_fechas(df: pd.DataFrame, desde, hasta) -> pd.DataFrame:
    out = df.dropna(subset=["Fecha"])
    if desde is not None:
        out = out[out["Fecha"] >= pd.to_datetime(desde)]
    if hasta is not None:
        out = out[out["Fecha"] <= pd.to_datetime(hasta)]
    return out


def _resumen_almacen(df_ent: pd.DataFrame, df_sal: pd.DataFrame) -> pd.DataFrame:
    """Tabla por tipo de bloque: recibido (entradas), entregado (salidas) y
    stock en almacén, todo en unidades reales.

    El stock = recibido − entregado es el inventario teórico que aún debería
    estar en almacén. Control puro de lo que entra y sale; sin metas."""

    def _suma_por_tipo(df):
        if df is None or df.empty or "Tipo_bloque" not in df.columns:
            return {}
        s = df.copy()
        s["Cantidad"] = pd.to_numeric(s["Cantidad"], errors="coerce")
        return s.groupby("Tipo_bloque")["Cantidad"].sum().to_dict()

    recibido = _suma_por_tipo(df_ent)
    entregado = _suma_por_tipo(df_sal)

    tipos = sorted(set(recibido) | set(entregado))
    filas = []
    for t in tipos:
        rec = float(recibido.get(t, 0.0))
        ent = float(entregado.get(t, 0.0))
        if rec == 0 and ent == 0:
            continue  # tipo sin movimiento: no aporta nada
        filas.append({
            "Tipo de bloque": t,
            "Recibido": rec,
            "Entregado": ent,
            "Stock almacén": rec - ent,
        })
    return pd.DataFrame(filas)


def _avance_pedido(df_ent: pd.DataFrame, catalogo: list) -> pd.DataFrame:
    """Tabla por tipo: recibido (suma de TODAS las entradas) vs el TECHO del pedido
    (`meta_pedido` del catálogo) → % de avance del pedido a la ladrillera.

    Es la versión en vivo del "PEDIDOS RESUMEN" que se llevaba en el Excel Control
    ladrillo. El techo es el total previsto para la torre: un TOPE de gasto, no un
    objetivo exacto. Solo aparecen los tipos con techo o con material recibido."""
    if df_ent is None or df_ent.empty or "Tipo_bloque" not in df_ent.columns:
        rec = {}
    else:
        s = df_ent.copy()
        s["Cantidad"] = pd.to_numeric(s["Cantidad"], errors="coerce")
        rec = s.groupby("Tipo_bloque")["Cantidad"].sum().to_dict()

    filas = []
    for b in catalogo or []:
        nombre = str(b.get("nombre", "")).strip()
        if not nombre:
            continue
        techo = float(b.get("meta_pedido", 0) or 0)
        recibido = float(rec.get(nombre, 0.0))
        if techo <= 0 and recibido == 0:
            continue  # sin techo ni movimiento: no aporta
        filas.append({
            "Tipo de bloque": nombre,
            "Recibido": recibido,
            "Techo (pedido)": techo if techo > 0 else float("nan"),
            "% avance": (recibido / techo) if techo > 0 else float("nan"),
            "Pendiente": max(techo - recibido, 0.0) if techo > 0 else float("nan"),
        })
    return pd.DataFrame(filas)


def _form_entrada(df_ent: pd.DataFrame, catalogo: list):
    """Formulario de ENTRADA de almacén (compras recibidas del proveedor).

    Centraliza lo que vivía en el Excel "Control ladrillo": cada remisión que
    llega del proveedor, en unidades reales + estibas."""
    nombres_bloques = [b["nombre"] for b in catalogo]
    st.caption(
        "Digita cada **remisión de entrada** (lo que llega del proveedor al "
        "almacén). La **cantidad va en unidades reales** de ladrillo. El acumulado "
        "y el stock se calculan solos. Las estibas devueltas se registran aparte, "
        "más abajo."
    )
    if not nombres_bloques:
        st.warning("El catálogo de bloques está vacío: pide al admin configurarlo abajo.")
        return

    proveedores = opciones_unicas(df_ent, "Proveedor")
    with st.form("form_entrada", clear_on_submit=True):
        e1, e2, e3 = st.columns(3)
        with e1:
            fecha_e = st.date_input("Fecha de la entrada *", value=datetime.now().date(),
                                    key="ent_fecha")
        with e2:
            tipo_e = st.selectbox("Tipo de bloque *", nombres_bloques, key="ent_tipo")
        with e3:
            prov_e = st.text_input(
                "Proveedor *", key="ent_prov", placeholder="Ej: LAD SAN C",
                help="Quién despacha el material. Se autocompleta con los ya usados: "
                     + (", ".join(proveedores) if proveedores else "—"),
            )
        e4, e5 = st.columns(2)
        with e4:
            cantidad_e = st.number_input("Cantidad (unidades) *", min_value=0.0,
                                         step=1.0, format="%.0f", key="ent_cant")
        with e5:
            remision_e = st.text_input(
                "# Remisión *", key="ent_remision",
                help="Número del documento de entrada (remisión). Evita digitar dos "
                     "veces y permite auditar contra el proveedor.",
            )
        obs_e = st.text_input("Observaciones", key="ent_obs")
        enviar_e = st.form_submit_button("💾 Guardar entrada", type="primary")

    if enviar_e:
        errores = []
        if cantidad_e <= 0:
            errores.append("La **cantidad** debe ser mayor que 0.")
        if not prov_e.strip():
            errores.append("El **proveedor** es obligatorio.")
        if not remision_e.strip():
            errores.append("El **# de remisión** es obligatorio.")
        # Una remisión ya digitada con el mismo tipo de bloque es un duplicado.
        if remision_e.strip() and not df_ent.empty and "No_remision" in df_ent.columns:
            ya = df_ent[
                (df_ent["No_remision"].astype(str).str.strip() == remision_e.strip())
                & (df_ent["Tipo_bloque"].astype(str).str.strip() == tipo_e)
            ]
            if not ya.empty:
                errores.append(
                    f"La remisión **{remision_e.strip()}** ya está digitada con "
                    f"**{tipo_e}** (revisa el *Kardex* de abajo). Si trae otro tipo de "
                    "bloque, digítala con ese otro tipo; si es otra remisión, corrige "
                    "el número."
                )
        if errores:
            for e in errores:
                st.error(e)
            return

        # Guardia anti doble clic.
        firma_e = repr((str(fecha_e), tipo_e, float(cantidad_e),
                        remision_e.strip(), prov_e.strip(), obs_e.strip()))
        ult_e = st.session_state.get("ultima_entrada_firma")
        if ult_e and ult_e[0] == firma_e and datetime.now().timestamp() - ult_e[1] < 120:
            st.warning(
                "⚠️ Esta entrada es **idéntica** a la que se acaba de guardar — no se "
                "guardó otra vez (posible doble clic)."
            )
            return

        fila = pd.DataFrame([{
            "Fecha": pd.to_datetime(fecha_e),
            "Tipo_bloque": tipo_e,
            "Cantidad": float(cantidad_e),
            "Estibas_ing": None,
            "Estibas_dev": None,
            "No_remision": remision_e.strip(),
            "Proveedor": prov_e.strip(),
            "Observaciones": obs_e.strip(),
            "Timestamp_registro": datetime.now(),
        }])[COLUMNAS_ENTRADAS]
        try:
            with st.spinner("Guardando…"):
                agregar_entradas(fila)
            cargar_entradas_cached.clear()
        except Exception as e:
            st.error(f"No se pudo guardar la entrada: {e}")
            return
        st.session_state["ultima_entrada_firma"] = (firma_e, datetime.now().timestamp())
        st.session_state["flash_entrada"] = (
            f"Entrada guardada · **{tipo_e}** · {cantidad_e:,.0f} unidades."
        )
        for k in ("ent_fecha", "ent_tipo", "ent_prov", "ent_cant",
                  "ent_remision", "ent_obs"):
            st.session_state.pop(k, None)
        st.rerun()


def _form_salida(df: pd.DataFrame, df_sal: pd.DataFrame, nombres_bloques: list):
    """Formulario de SALIDA de almacén (entrega a obra, en UNIDADES reales).

    No se digita en estibas: una estiba no trae una cantidad fija (llega muy
    variable), así que convertir estibas→unidades falsearía el conteo. Se cuenta
    lo que realmente sale, en unidades."""
    st.caption(
        "Digita cada **remisión** (el documento con el que el almacén registra cada "
        "salida de material). Nadie cuenta ladrillos pegados: el desperdicio sale de "
        "comparar lo entregado contra el teórico de los muros registrados en "
        "📋 Ingreso de datos."
    )
    if not nombres_bloques:
        st.warning("El catálogo de bloques está vacío: pide al admin configurarlo abajo.")
        return

    with st.form("form_salida", clear_on_submit=True):
        v1, v2, v3 = st.columns(3)
        with v1:
            fecha_s = st.date_input("Fecha de la salida *", value=datetime.now().date(),
                                    key="sal_fecha")
        with v2:
            sectores_s = ["Torre", "Plataforma"] + [
                s for s in opciones_visibles(df, "Sector") if s not in ("Torre", "Plataforma")
            ]
            sector_s = st.selectbox("Sector *", sectores_s, key="sal_sector")
        with v3:
            piso_s = st.text_input("Piso *", placeholder="Ej: 5", key="sal_piso")
        v4, v5 = st.columns(2)
        with v4:
            tipo_s = st.selectbox("Tipo de bloque *", nombres_bloques, key="sal_tipo")
        with v5:
            cantidad_s = st.number_input(
                "Cantidad (unidades) *", min_value=0.0, step=1.0, format="%.0f",
                key="sal_cantidad",
                help="Unidades reales que salen del almacén. No se digita en estibas: "
                     "no traen una cantidad fija. Cuenta las unidades.",
            )
        v7, v8 = st.columns(2)
        with v7:
            vale_s = st.text_input(
                "# Remisión (opcional)",
                key="sal_vale",
                help="Número del documento de salida de almacén (remisión/vale). "
                     "Sirve para no digitar dos veces y auditar contra el almacén.",
            )
        with v8:
            obs_s = st.text_input("Observaciones", key="obs_salida")
        enviar_s = st.form_submit_button("💾 Guardar salida", type="primary")

    if enviar_s:
        errores = []
        if not piso_s.strip():
            errores.append("El campo **Piso** es obligatorio.")
        if cantidad_s <= 0:
            errores.append("La **cantidad** debe ser mayor que 0.")
        # Una remisión ya digitada con el mismo tipo de bloque es un duplicado.
        if vale_s.strip() and not df_sal.empty and "No_vale" in df_sal.columns:
            ya = df_sal[
                (df_sal["No_vale"].astype(str).str.strip() == vale_s.strip())
                & (df_sal["Tipo_bloque"].astype(str).str.strip() == tipo_s)
            ]
            if not ya.empty:
                errores.append(
                    f"La remisión **{vale_s.strip()}** ya está digitada con "
                    f"**{tipo_s}** (revisa el *Kardex* de abajo). Si la misma remisión "
                    "trae otro tipo de bloque, digítala con ese otro tipo; si es "
                    "otra remisión, corrige el número."
                )
        if errores:
            for e in errores:
                st.error(e)
            return

        # Guardia anti doble clic (cubre las salidas sin # remisión).
        firma_s = repr((str(fecha_s), sector_s, piso_s.strip(), tipo_s,
                        float(cantidad_s), vale_s.strip(), obs_s.strip()))
        ult_s = st.session_state.get("ultima_salida_firma")
        if ult_s and ult_s[0] == firma_s and datetime.now().timestamp() - ult_s[1] < 120:
            st.warning(
                "⚠️ Esta salida es **idéntica** a la que se acaba de guardar — no se "
                "guardó otra vez (posible doble clic)."
            )
            return

        fila = pd.DataFrame([{
            "Fecha": pd.to_datetime(fecha_s),
            "Sector": sector_s,
            "Piso": piso_s.strip(),
            "Tipo_bloque": tipo_s,
            "Cantidad": float(cantidad_s),
            "No_vale": vale_s.strip(),
            "Observaciones": obs_s.strip(),
            "Timestamp_registro": datetime.now(),
        }])[COLUMNAS_SALIDAS]
        try:
            with st.spinner("Guardando…"):
                agregar_salidas(fila)
            cargar_salidas_cached.clear()
        except Exception as e:
            st.error(f"No se pudo guardar la salida: {e}")
            return
        st.session_state["ultima_salida_firma"] = (firma_s, datetime.now().timestamp())
        st.session_state["flash_salida"] = (
            f"Salida guardada · **{tipo_s}** · {cantidad_s:,.0f} unidades · "
            f"{sector_s} · piso {piso_s.strip()}."
        )
        # Limpiar el formulario para que no quede listo para un segundo guardado.
        for k in ("sal_fecha", "sal_sector", "sal_piso", "sal_tipo",
                  "sal_cantidad", "sal_vale", "obs_salida"):
            st.session_state.pop(k, None)
        st.rerun()


def _kardex(df_ent: pd.DataFrame, df_sal: pd.DataFrame) -> pd.DataFrame:
    """Combina entradas (+) y salidas (−) en una sola línea de tiempo con el
    stock acumulado por tipo de bloque tras cada movimiento (estilo kardex)."""
    partes = []

    if df_ent is not None and not df_ent.empty:
        e = df_ent.copy()
        cant = pd.to_numeric(e["Cantidad"], errors="coerce")
        partes.append(pd.DataFrame({
            "Fecha": pd.to_datetime(e["Fecha"], errors="coerce"),
            "Timestamp_registro": pd.to_datetime(e["Timestamp_registro"], errors="coerce"),
            "Movimiento": "▲ Entrada",
            "Tipo_bloque": e["Tipo_bloque"].astype(str),
            "_signo": cant.fillna(0.0),
            "Origen / Destino": [str(p).strip() or "—" for p in e["Proveedor"]],
            "Remisión": e["No_remision"].astype(str),
        }))

    if df_sal is not None and not df_sal.empty:
        s = df_sal.copy()
        cant = pd.to_numeric(s["Cantidad"], errors="coerce")
        destino = [
            (str(sec).strip() or "—")
            + (f" · piso {str(pi).strip()}" if str(pi).strip() and str(pi) != "nan" else "")
            for sec, pi in zip(s["Sector"], s["Piso"])
        ]
        partes.append(pd.DataFrame({
            "Fecha": pd.to_datetime(s["Fecha"], errors="coerce"),
            "Timestamp_registro": pd.to_datetime(s["Timestamp_registro"], errors="coerce"),
            "Movimiento": "▼ Salida",
            "Tipo_bloque": s["Tipo_bloque"].astype(str),
            "_signo": -cant.fillna(0.0),
            "Origen / Destino": destino,
            "Remisión": s["No_vale"].astype(str),
        }))

    if not partes:
        return pd.DataFrame()

    k = pd.concat(partes, ignore_index=True)
    # Acumular cronológicamente (asc) el stock por tipo; mostrar luego más reciente arriba.
    k = k.sort_values(["Fecha", "Timestamp_registro"], na_position="first").reset_index(drop=True)
    k["Stock"] = k.groupby("Tipo_bloque")["_signo"].cumsum()
    k["Cantidad"] = k["_signo"]
    k = k.sort_values(["Fecha", "Timestamp_registro"], ascending=False, na_position="last")
    return k.drop(columns=["_signo"])


_TIPO_SIN_ESPECIFICAR = "(Sin especificar)"


def _form_estibas_dev(df_est: pd.DataFrame, nombres_bloques: list) -> None:
    """Formulario de ESTIBAS DEVUELTAS (pallets de madera regresados al proveedor).

    Ledger APARTE del material: las estibas devueltas no están ligadas al pedido
    ni a los ladrillos y NO afectan el stock de bloque. Solo control de pallets.
    `Tipo_bloque` es opcional: indica de qué bloque era el pallet (Catalán moreno…)."""
    proveedores = opciones_unicas(df_est, "Proveedor")
    opciones_tipo = [_TIPO_SIN_ESPECIFICAR] + list(nombres_bloques)
    with st.form("form_estibas", clear_on_submit=True):
        d1, d2, d3 = st.columns(3)
        with d1:
            fecha_d = st.date_input("Fecha *", value=datetime.now().date(),
                                    key="est_fecha")
        with d2:
            cant_d = st.number_input("Estibas devueltas (pallets) *", min_value=0.0,
                                     step=1.0, format="%.0f", key="est_cant")
        with d3:
            tipo_d = st.selectbox(
                "Tipo de bloque", opciones_tipo, key="est_tipo",
                help="De qué bloque era el pallet (Catalán moreno, etc.). "
                     "Opcional: déjalo en «(Sin especificar)» si no aplica.",
            )
        d4, d5 = st.columns(2)
        with d4:
            prov_d = st.text_input(
                "Proveedor", key="est_prov", placeholder="Ej: LAD SAN C",
                help="A quién se le regresan los pallets. Usados: "
                     + (", ".join(proveedores) if proveedores else "—"),
            )
        with d5:
            remision_d = st.text_input("# Remisión (opcional)", key="est_remision")
        obs_d = st.text_input("Observaciones", key="est_obs")
        enviar_d = st.form_submit_button("💾 Guardar devolución", type="primary")

    if not enviar_d:
        return
    if cant_d <= 0:
        st.error("La **cantidad de estibas devueltas** debe ser mayor que 0.")
        return

    tipo_guardar = "" if tipo_d == _TIPO_SIN_ESPECIFICAR else tipo_d

    # Guardia anti doble clic.
    firma_d = repr((str(fecha_d), float(cant_d), tipo_guardar, prov_d.strip(),
                    remision_d.strip(), obs_d.strip()))
    ult_d = st.session_state.get("ultima_estiba_firma")
    if ult_d and ult_d[0] == firma_d and datetime.now().timestamp() - ult_d[1] < 120:
        st.warning("⚠️ Esta devolución es **idéntica** a la recién guardada — no se "
                   "guardó otra vez (posible doble clic).")
        return

    fila = pd.DataFrame([{
        "Fecha": pd.to_datetime(fecha_d),
        "Cantidad": float(cant_d),
        "Tipo_bloque": tipo_guardar,
        "Proveedor": prov_d.strip(),
        "No_remision": remision_d.strip(),
        "Observaciones": obs_d.strip(),
        "Timestamp_registro": datetime.now(),
    }])[COLUMNAS_ESTIBAS]
    try:
        with st.spinner("Guardando…"):
            agregar_estibas(fila)
        cargar_estibas_cached.clear()
    except Exception as e:
        st.error(f"No se pudo guardar la devolución de estibas: {e}")
        return
    st.session_state["ultima_estiba_firma"] = (firma_d, datetime.now().timestamp())
    st.session_state["flash_estibas"] = (
        f"Estibas devueltas guardadas · {cant_d:,.0f} pallet(s)"
        f"{f' · {tipo_guardar}' if tipo_guardar else ''}"
        f"{f' · {prov_d.strip()}' if prov_d.strip() else ''}."
    )
    for k in ("est_fecha", "est_cant", "est_tipo", "est_prov", "est_remision", "est_obs"):
        st.session_state.pop(k, None)
    st.rerun()


def _seccion_estibas_dev(df_est: pd.DataFrame, nombres_bloques: list) -> None:
    """Sección independiente del stock: registrar y ver estibas (pallets) devueltas."""
    st.divider()
    st.subheader("♻️ Estibas devueltas")
    st.caption(
        "Pallets de madera **vacíos** que se regresan al proveedor. Es un control "
        "aparte: **no** está unido al pedido ni a los ladrillos y **no** cambia el "
        "stock de bloque."
    )
    if "flash_estibas" in st.session_state:
        st.success(st.session_state.pop("flash_estibas"))
    _form_estibas_dev(df_est, nombres_bloques)

    if df_est is None or df_est.empty:
        return
    e = df_est.copy()
    e["Cantidad"] = pd.to_numeric(e["Cantidad"], errors="coerce")
    total = e["Cantidad"].sum()
    st.metric("Total de estibas devueltas", f"{total:,.0f} pallet(s)")
    vista = e.sort_values("Fecha", ascending=False).head(40).copy()
    vista["Fecha"] = pd.to_datetime(vista["Fecha"], errors="coerce").dt.strftime("%Y-%m-%d")
    if "Tipo_bloque" in vista.columns:   # filas antiguas / pallets genéricos = "—"
        vista["Tipo_bloque"] = vista["Tipo_bloque"].replace("", pd.NA)
    cols = ["Fecha", "Cantidad", "Tipo_bloque", "Proveedor", "No_remision", "Observaciones"]
    st.dataframe(
        vista[cols].rename(columns={"Cantidad": "Pallets", "Tipo_bloque": "Tipo",
                                    "No_remision": "Remisión"})
        .style.format({"Pallets": "{:,.0f}"}, na_rep="—"),
        width="stretch", hide_index=True,
    )


def _tab_movimientos(df: pd.DataFrame, df_ent: pd.DataFrame, df_sal: pd.DataFrame,
                     df_est: pd.DataFrame, catalogo: list):
    """Pestaña única de almacén: registrar entrada o salida (un solo lugar),
    ver el stock por tipo y el kardex combinado de movimientos."""
    nombres_bloques = [b["nombre"] for b in catalogo]
    if not nombres_bloques:
        st.warning("El catálogo de bloques está vacío: pide al admin configurarlo abajo.")
        return

    modo = st.radio("¿Qué vas a registrar?", ["📦 Entrada", "📥 Salida"],
                    horizontal=True, key="mov_modo")
    if modo == "📦 Entrada":
        _form_entrada(df_ent, catalogo)
    else:
        _form_salida(df, df_sal, nombres_bloques)

    # ── Stock en almacén y avance del pedido ──────────────────────
    resumen = _resumen_almacen(df_ent, df_sal)
    if not resumen.empty:
        st.divider()
        st.subheader("📊 Stock en almacén")
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("Recibido total", f"{resumen['Recibido'].sum():,.0f} und")
        mc2.metric("Entregado a obra", f"{resumen['Entregado'].sum():,.0f} und")
        mc3.metric("Stock en almacén", f"{resumen['Stock almacén'].sum():,.0f} und")
        st.dataframe(
            resumen.style.format({
                "Recibido": "{:,.0f}", "Entregado": "{:,.0f}",
                "Stock almacén": "{:,.0f}",
            }, na_rep="—"),
            width="stretch", hide_index=True,
        )
        st.caption(
            "**Stock almacén = Recibido − Entregado** (inventario teórico que queda). "
            "Stock negativo = se entregó más de lo recibido: revisa entradas faltantes."
        )

    # ── Avance del pedido a la ladrillera (recibido vs techo) ─────
    avance = _avance_pedido(df_ent, catalogo)
    if not avance.empty:
        st.divider()
        st.subheader("🎯 Avance del pedido (recibido vs techo)")
        st.caption(
            "Suma de **todo lo recibido** por tipo contra el **techo del pedido** de la "
            "torre. El techo es un **tope de gasto previsto**, no un objetivo exacto. "
            "**% avance = recibido ÷ techo**; 🔴 = ya se pasó del techo."
        )

        def _estado_av(p):
            if pd.isna(p):
                return "(sin techo)"
            if p > 1:
                return "🔴 pasó el techo"
            if p >= 0.9:
                return "🟠 cerca"
            return "🟢 ok"

        vis = avance.copy()
        vis["Estado"] = vis["% avance"].apply(_estado_av)
        con_techo = avance[avance["Techo (pedido)"].notna()]
        if not con_techo.empty:
            tot_techo = con_techo["Techo (pedido)"].sum()
            tot_rec = con_techo["Recibido"].sum()
            a1, a2, a3 = st.columns(3)
            a1.metric("Techo total (pedido)", f"{tot_techo:,.0f} und")
            a2.metric("Recibido total", f"{tot_rec:,.0f} und")
            a3.metric("Avance global", f"{(tot_rec / tot_techo if tot_techo else 0):.0%}")
        st.dataframe(
            vis.style.format({
                "Recibido": "{:,.0f}", "Techo (pedido)": "{:,.0f}",
                "% avance": "{:.0%}", "Pendiente": "{:,.0f}",
            }, na_rep="—"),
            width="stretch", hide_index=True,
        )

    # ── Entradas y salidas combinadas ─────────────────────────────
    kx = _kardex(df_ent, df_sal)
    if not kx.empty:
        st.divider()
        st.subheader("📒 Entradas y salidas")
        tipos = ["Todos"] + sorted(kx["Tipo_bloque"].dropna().unique().tolist())
        f1, _ = st.columns([1, 2])
        with f1:
            tipo_f = st.selectbox("Filtrar por tipo", tipos, key="kardex_tipo")
        if tipo_f != "Todos":
            kx = kx[kx["Tipo_bloque"] == tipo_f]
        vista = kx.head(40).copy()
        vista["Fecha"] = pd.to_datetime(vista["Fecha"], errors="coerce").dt.strftime("%Y-%m-%d")
        cols = ["Fecha", "Movimiento", "Tipo_bloque", "Cantidad",
                "Origen / Destino", "Remisión", "Stock"]
        st.dataframe(
            vista[cols].style.format(
                {"Cantidad": "{:+,.0f}", "Stock": "{:,.0f}"}, na_rep="—"),
            width="stretch", hide_index=True,
        )
        st.caption(
            "▲ entra · ▼ sale. **Stock** = saldo del almacén para ese tipo después del "
            "movimiento. Se muestran los 40 más recientes."
        )

    # ── Estibas devueltas (control de pallets, aparte del stock) ───
    _seccion_estibas_dev(df_est, nombres_bloques)


def _tab_conciliacion(df: pd.DataFrame, df_sal: pd.DataFrame):
    """Sub-pestaña: teórico vs entregado con semáforo de desperdicio."""
    if df.empty and df_sal.empty:
        st.info("Aún no hay registros ni salidas para conciliar.")
        return

    st.caption(
        f"**Desperdicio % = (entregado − teórico ajustado) ÷ teórico ajustado.** "
        f"Teórico ajustado = teórico × **{_factor_ajuste():g}** (Factor de Modulación por cortes/trabas, "
        f"configurable). Semáforo: 🟢 ≤ {_umbral_pct():g}% · 🟠 ≤ {1.5 * _umbral_pct():g}% · 🔴 mayor. "
        "Solo los registros guardados con catálogo aportan teórico (los viejos no)."
    )

    fc1, fc2, fc3, fc4 = st.columns(4)
    with fc1:
        desde_c = st.date_input("Desde", value=None, key="conc_desde")
    with fc2:
        hasta_c = st.date_input("Hasta", value=None, key="conc_hasta")
    with fc3:
        sec_c = st.selectbox("Sector", ["Todos"] + opciones_unicas(df, "Sector"),
                             key="conc_sector")
    with fc4:
        nivel_c = st.selectbox("Agrupar por", ["Sector y piso", "Solo sector", "Toda la obra"],
                               key="conc_nivel")

    reg_f = _filtrar_fechas(df, desde_c, hasta_c)
    sal_f = _filtrar_fechas(df_sal, desde_c, hasta_c)
    if sec_c != "Todos":
        reg_f = reg_f[reg_f["Sector"] == sec_c]
        sal_f = sal_f[sal_f["Sector"] == sec_c]

    dims = {
        "Sector y piso": ("Sector", "Piso", "Tipo_bloque"),
        "Solo sector": ("Sector", "Tipo_bloque"),
        "Toda la obra": ("Tipo_bloque",),
    }[nivel_c]
    conc = conciliacion(reg_f, sal_f, dims=dims, factor_ajuste=_factor_ajuste())

    if conc.empty:
        st.info("No hay datos en el rango seleccionado.")
        return

    teo_t = conc["Teorico_ajustado"].sum()
    ent_t = conc["Entregado"].sum()
    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("Teórico ajustado", f"{teo_t:,.0f} und")
    mc2.metric("Entregado almacén", f"{ent_t:,.0f} und")
    if teo_t > 0:
        mc3.metric("Desperdicio global", f"{(ent_t - teo_t) / teo_t * 100:+.1f}%",
                   delta=f"umbral {_umbral_pct():g}%", delta_color="off")
    else:
        mc3.metric("Desperdicio global", "—")

    styler = estilar_desperdicio(conc.style).format(
        {
            "Teorico": "{:,.0f}", "Teorico_ajustado": "{:,.0f}",
            "Entregado": "{:,.0f}", "Diferencia": "{:+,.0f}",
            "Desperdicio_pct": "{:+.1%}",
        },
        na_rep="—",
    )
    st.dataframe(styler, width="stretch")
    st.caption(
        "**Diferencia > 0** = salió más bloque del que se pegó (desperdicio o pega sin "
        "registrar). **Sin teórico** (—) = se entregó un tipo que no aparece en ningún "
        "registro: revisar a qué muros se está yendo."
    )

    st.download_button(
        "⬇️ Descargar conciliación (Excel)",
        data=excel_bytes(conc),
        file_name="conciliacion_bloques.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _tab_graficas_desperdicio(df: pd.DataFrame, df_sal: pd.DataFrame):
    """Sub-pestaña: desperdicio % por piso/tipo y tendencia semanal."""
    conc_pt = conciliacion(df, df_sal, dims=("Piso", "Tipo_bloque"),
                           factor_ajuste=_factor_ajuste())
    conc_pt = conc_pt[conc_pt["Desperdicio_pct"].notna()].copy()
    if conc_pt.empty:
        st.info("Aún no hay teórico y entregado del mismo piso/tipo para graficar.")
        return

    conc_pt["Desperdicio %"] = conc_pt["Desperdicio_pct"] * 100
    fig = px.bar(
        conc_pt, x="Piso", y="Desperdicio %", color="Tipo_bloque", barmode="group",
        title="Desperdicio % por piso y tipo de bloque",
    )
    fig.add_hline(y=_umbral_pct(), line_dash="dash", line_color="red",
                  annotation_text=f"Umbral {_umbral_pct():g}%",
                  annotation_position="top left")
    fig.update_layout(xaxis_title="", legend_title="")
    st.plotly_chart(fig, width="stretch")

    df_sem = df.dropna(subset=["Fecha"]).copy()
    sal_sem = df_sal.dropna(subset=["Fecha"]).copy()
    if df_sem.empty or sal_sem.empty:
        return
    df_sem["Semana"] = df_sem["Fecha"].dt.to_period("W").dt.start_time
    sal_sem["Semana"] = sal_sem["Fecha"].dt.to_period("W").dt.start_time
    conc_sem = conciliacion(df_sem, sal_sem, dims=("Semana", "Tipo_bloque"),
                            factor_ajuste=_factor_ajuste())
    conc_sem = conc_sem[conc_sem["Desperdicio_pct"].notna()].copy()
    if conc_sem.empty:
        return
    conc_sem["Desperdicio %"] = conc_sem["Desperdicio_pct"] * 100
    fig2 = px.line(
        conc_sem, x="Semana", y="Desperdicio %", color="Tipo_bloque", markers=True,
        title="Tendencia semanal del desperdicio %",
    )
    fig2.add_hline(y=_umbral_pct(), line_dash="dash", line_color="red")
    fig2.update_layout(xaxis_title="", legend_title="")
    st.plotly_chart(fig2, width="stretch")


def _editor_catalogo(catalogo: list):
    """Expander (solo admin): editar el catálogo de bloques; persiste en Supabase."""
    with st.expander("📚 Catálogo de bloques (admin)"):
        st.caption(
            "Medidas del **bloque** en metros; el módulo (bloque + junta) define los "
            "bloques/m² (ej. 0.39 + 0.01 y 0.19 + 0.01 → 0.40×0.20 → 12.5 und/m²). "
            "⚠️ **No renombres** un tipo que ya tenga registros o salidas: el cruce "
            "teórico↔entregado se hace por nombre."
        )
        # Mismo defecto de st.data_editor que en Ingreso: la key lleva "versión"
        # para que tras guardar el editor renazca con el catálogo ya validado.
        nc = st.session_state.setdefault("catalogo_nonce", 0)
        cat_edit = st.data_editor(
            pd.DataFrame(catalogo).drop(columns=["meta_pedido"], errors="ignore"),
            num_rows="dynamic",
            key=f"catalogo_editor_{nc}",
            width="stretch",
            column_config={
                "nombre": st.column_config.TextColumn("Nombre", required=True),
                "clase": st.column_config.SelectboxColumn("Clase", options=["PV", "PH"],
                                                          default="PV", required=True),
                "largo_m": st.column_config.NumberColumn("Largo (m)", min_value=0.0,
                                                         step=0.01, format="%.3f"),
                "alto_m": st.column_config.NumberColumn("Alto (m)", min_value=0.0,
                                                        step=0.01, format="%.3f"),
                "espesor_m": st.column_config.NumberColumn("Espesor (m)", min_value=0.0,
                                                           step=0.01, format="%.3f"),
                "junta_m": st.column_config.NumberColumn("Junta (m)", min_value=0.0,
                                                         step=0.005, format="%.3f"),
                "unds_por_estiba": st.column_config.NumberColumn("Und/estiba", min_value=1,
                                                                 step=1, format="%d"),
            },
        )
        if not config_persistente():
            st.info("La edición permanente del catálogo requiere **Supabase**; "
                    "mientras tanto se usa el catálogo por defecto del código.")
            return
        if st.button("💾 Guardar catálogo"):
            try:
                # `meta_pedido` se oculta del editor; se re-empareja por nombre al
                # guardar para NO borrar las metas guardadas (las filas nuevas o
                # renombradas quedan en 0 = sin meta).
                metas = {str(b.get("nombre", "")).strip(): b.get("meta_pedido", 0)
                         for b in catalogo}
                registros = cat_edit.to_dict("records")
                for r in registros:
                    r["meta_pedido"] = metas.get(str(r.get("nombre", "")).strip(), 0)
                guardar_catalogo(registros)
                cargar_catalogo_cached.clear()
                st.session_state["catalogo"] = leer_catalogo()
            except Exception as e:
                st.error(f"No se pudo guardar el catálogo: {e}")
                return
            st.session_state["catalogo_nonce"] = nc + 1
            st.toast("Catálogo guardado ✅")
            st.rerun()


def _corregir_movimientos(df_ent: pd.DataFrame, df_sal: pd.DataFrame,
                          df_est: pd.DataFrame) -> None:
    """Panel (solo admin) para BORRAR un movimiento de almacén mal digitado.

    Para 'corregir' se borra el equivocado y se vuelve a digitar: el stock
    (Recibido − Entregado) se recalcula solo. Borra entradas, salidas y estibas
    devueltas. Usa DELETE dirigido por backend (id en Supabase); si los permisos
    lo impiden, no borra nada y avisa. Acción irreversible."""

    def _ffecha(v):
        return pd.to_datetime(v).strftime("%d/%m/%Y") if pd.notna(v) else "sin fecha"

    def _txt(v):
        return str(v).strip() if pd.notna(v) and str(v).strip() else "—"

    opciones = []
    if df_ent is not None and not df_ent.empty:
        for idx, r in df_ent.iterrows():
            opciones.append({"tipo": "Entrada", "idx": idx, "_lbl": (
                f"▲ Entrada · {_ffecha(r['Fecha'])} · {_txt(r['Tipo_bloque'])} · "
                f"{pd.to_numeric(r['Cantidad'], errors='coerce'):,.0f} und · "
                f"rem {_txt(r['No_remision'])}")})
    if df_sal is not None and not df_sal.empty:
        for idx, r in df_sal.iterrows():
            opciones.append({"tipo": "Salida", "idx": idx, "_lbl": (
                f"▼ Salida · {_ffecha(r['Fecha'])} · {_txt(r['Tipo_bloque'])} · "
                f"{pd.to_numeric(r['Cantidad'], errors='coerce'):,.0f} und · "
                f"{_txt(r['Sector'])}/{_txt(r['Piso'])}")})
    if df_est is not None and not df_est.empty:
        for idx, r in df_est.iterrows():
            opciones.append({"tipo": "Estiba dev", "idx": idx, "_lbl": (
                f"♻️ Estibas dev · {_ffecha(r['Fecha'])} · "
                f"{pd.to_numeric(r['Cantidad'], errors='coerce'):,.0f} pallet(s) · "
                f"{_txt(r['Proveedor'])}")})

    with st.expander("🗑️ Corregir / eliminar movimientos de almacén (admin)"):
        st.caption(
            "Borra **definitivamente** un movimiento mal digitado (entrada, salida "
            "o estiba devuelta), también de la base de datos. Para corregirlo, "
            "bórralo y vuelve a digitarlo: el stock se recalcula solo. **No se "
            "puede deshacer.**"
        )
        if not opciones:
            st.info("No hay movimientos de almacén para eliminar.")
            return

        op_df = pd.DataFrame(opciones).sort_values("_lbl").reset_index(drop=True)
        nd = st.session_state.setdefault("del_mov_nonce", 0)
        elegidos = st.multiselect(
            "Movimientos a eliminar", op_df.index.tolist(),
            format_func=lambda i: op_df.loc[i, "_lbl"], key=f"del_mov_sel_{nd}",
        )
        if not elegidos:
            return

        st.warning(
            f"Se eliminarán **{len(elegidos)} movimiento(s)** de la base de datos. "
            "**No se puede deshacer.**"
        )
        ok = st.checkbox("Sí, eliminar definitivamente", key=f"del_mov_ok_{nd}")
        if st.button("🗑️ Eliminar definitivamente", type="primary",
                     disabled=not ok, use_container_width=True, key=f"del_mov_btn_{nd}"):
            sel = op_df.loc[elegidos]
            fuentes = [("Entrada", eliminar_entradas, df_ent),
                       ("Salida", eliminar_salidas, df_sal),
                       ("Estiba dev", eliminar_estibas, df_est)]
            borradas = 0
            try:
                for tipo, fn, src in fuentes:
                    idxs = sel.loc[sel["tipo"] == tipo, "idx"].tolist()
                    if idxs:
                        borradas += fn(src.loc[idxs])
            except Exception as e:
                st.error(f"No se pudo eliminar: {e}")
                return
            if borradas == 0:
                st.error(
                    "No se borró nada. Si usas la clave **anon** en Supabase, falta "
                    "la política de borrado (DELETE) en la tabla: usa la clave "
                    "service_role o agrégala (ver supabase_schema.sql)."
                )
                return
            st.cache_data.clear()
            st.session_state["del_mov_nonce"] = nd + 1
            st.toast(f"Eliminado(s) {borradas} movimiento(s) ✅")
            st.rerun()


# ─────────────────────────────────────────────────────────────
# Pantalla — Control por piso (el "corte": entró − queda = gastado vs teórico)
# ─────────────────────────────────────────────────────────────
def _conteo_ultimo_por_tipo(df_con: pd.DataFrame, hasta=None) -> pd.DataFrame:
    """Último conteo físico (lo que QUEDA) por Sector/Piso/Tipo_bloque.

    Si un piso/tipo tiene varios conteos, toma el MÁS RECIENTE (la última foto del
    sobrante). `hasta` limita a conteos en/antes de esa fecha (el cierre del corte)."""
    cols = ["Sector", "Piso", "Tipo_bloque", "Queda"]
    if df_con is None or df_con.empty:
        return pd.DataFrame(columns=cols)
    c = df_con.copy()
    c["Cantidad"] = pd.to_numeric(c["Cantidad"], errors="coerce")
    c["Fecha"] = pd.to_datetime(c["Fecha"], errors="coerce")
    if hasta is not None:
        c = c[c["Fecha"] <= pd.to_datetime(hasta)]
    c = c.dropna(subset=["Cantidad"])
    for d in ("Sector", "Piso", "Tipo_bloque"):
        c[d] = c[d].fillna("").astype(str).str.strip()
    if c.empty:
        return pd.DataFrame(columns=cols)
    c = c.sort_values("Fecha").groupby(
        ["Sector", "Piso", "Tipo_bloque"], as_index=False).last()
    return c[["Sector", "Piso", "Tipo_bloque", "Cantidad"]].rename(
        columns={"Cantidad": "Queda"})


def _form_conteo(df: pd.DataFrame, catalogo: list):
    """Formulario del conteo físico del sobrante (el corte de cada piso)."""
    nombres = [b["nombre"] for b in catalogo]
    if not nombres:
        st.warning("El catálogo de bloques está vacío: pide al admin configurarlo.")
        return
    with st.form("form_conteo", clear_on_submit=True):
        k1, k2, k3 = st.columns(3)
        with k1:
            fecha = st.date_input("Fecha del corte *", value=datetime.now().date(),
                                  key="con_fecha")
        with k2:
            sectores = ["Torre", "Plataforma"] + [
                s for s in opciones_visibles(df, "Sector")
                if s not in ("Torre", "Plataforma")]
            sector = st.selectbox("Sector *", sectores, key="con_sector")
        with k3:
            piso = st.text_input("Piso *", placeholder="Ej: 5", key="con_piso")
        k4, k5 = st.columns(2)
        with k4:
            tipo = st.selectbox("Tipo de bloque *", nombres, key="con_tipo")
        with k5:
            cantidad = st.number_input(
                "Sobrante contado (unidades) *", min_value=0.0, step=1.0,
                format="%.0f", key="con_cant",
                help="Lo que QUEDÓ físico en el piso ese día (lo que cuentas en el corte).")
        obs = st.text_input("Observaciones", key="con_obs")
        enviar = st.form_submit_button("💾 Guardar conteo", type="primary")

    if enviar:
        if not piso.strip():
            st.error("El campo **Piso** es obligatorio.")
            return
        fila = pd.DataFrame([{
            "Fecha": pd.to_datetime(fecha),
            "Sector": sector,
            "Piso": piso.strip(),
            "Tipo_bloque": tipo,
            "Cantidad": float(cantidad),
            "Observaciones": obs.strip(),
            "Timestamp_registro": datetime.now(),
        }])[COLUMNAS_CONTEOS]
        try:
            with st.spinner("Guardando…"):
                agregar_conteos(fila)
            cargar_conteos_cached.clear()
        except Exception as e:
            st.error(f"No se pudo guardar el conteo: {e}")
            return
        st.session_state["flash_conteo"] = (
            f"Conteo guardado · piso {piso.strip()} · {tipo} · {cantidad:,.0f} sobrantes.")
        for k in ("con_fecha", "con_sector", "con_piso", "con_tipo", "con_cant", "con_obs"):
            st.session_state.pop(k, None)
        st.rerun()


def pagina_control_piso(df: pd.DataFrame):
    """Control por piso (el 'corte'): cruza ENTRÓ (salidas) − QUEDA (conteo) =
    GASTADO contra el TEÓRICO (muros), por piso y tipo, todo automático."""
    st.header("📍 Control por piso (corte)")
    if "flash_conteo" in st.session_state:
        st.success(st.session_state.pop("flash_conteo"))

    try:
        df_sal = cargar_salidas_cached()
    except Exception:
        df_sal = pd.DataFrame(columns=COLUMNAS_SALIDAS)
    try:
        df_ent = cargar_entradas_cached()
    except Exception:
        df_ent = pd.DataFrame(columns=COLUMNAS_ENTRADAS)
    try:
        df_con = cargar_conteos_cached()
    except Exception:
        df_con = pd.DataFrame(columns=COLUMNAS_CONTEOS)
        st.info("Si usas Supabase, corre la sección nueva de `supabase_schema.sql` "
                "(tabla `almacen_conteos`) para poder guardar los conteos.")

    catalogo = _catalogo()
    factor = _factor_ajuste()
    umbral = _umbral_pct()

    st.caption(
        "El **corte** de cada piso: **Entró** (salidas de almacén al piso) − **Queda** "
        "(lo que cuentas físico) = **Gastado**, contra el **Teórico** de los muros. "
        f"Teórico ajustado = teórico × {factor:g}. Semáforo: 🟢 ≤ {umbral:g} % · "
        f"🟠 ≤ {1.5 * umbral:g} % · 🔴 mayor. Sin conteo, el corte es *aparente* "
        "(no resta el sobrante).")

    f1, f2, f3 = st.columns(3)
    with f1:
        desde = st.date_input("Desde", value=None, key="cp_desde")
    with f2:
        hasta = st.date_input("Hasta (cierre del corte)", value=None, key="cp_hasta")
    with f3:
        sec_f = st.selectbox("Sector", ["Todos"] + opciones_unicas(df, "Sector"),
                             key="cp_sector")

    df_f = _filtrar_fechas(df, desde, hasta)
    sal_f = _filtrar_fechas(df_sal, desde, hasta)
    if sec_f != "Todos":
        df_f = df_f[df_f["Sector"] == sec_f]
        sal_f = sal_f[sal_f["Sector"] == sec_f]

    conc = conciliacion(df_f, sal_f, dims=("Sector", "Piso", "Tipo_bloque"),
                        factor_ajuste=factor)
    if conc.empty:
        st.info("Aún no hay salidas ni muros para cruzar en este filtro. Registra "
                "movimientos de almacén y muros, y agrega un conteo abajo.")
    else:
        queda = _conteo_ultimo_por_tipo(df_con, hasta)
        if sec_f != "Todos" and not queda.empty:
            queda = queda[queda["Sector"] == sec_f]
        t = conc.merge(queda, on=["Sector", "Piso", "Tipo_bloque"], how="left")
        t["Queda"] = pd.to_numeric(t.get("Queda"), errors="coerce")
        t["Gastado"] = (t["Entregado"] - t["Queda"]).round(1)
        # Base del desperdicio: gastado si hubo conteo; si no, el entregado (aparente).
        base = t["Gastado"].where(t["Queda"].notna(), t["Entregado"])
        t["Desperdicio"] = (base - t["Teorico_ajustado"]).round(1)
        t["Desp_pct"] = base.sub(t["Teorico_ajustado"]).div(
            t["Teorico_ajustado"].where(t["Teorico_ajustado"] > 0))

        def _sem(p):
            if pd.isna(p):
                return ""
            if p <= umbral / 100:
                return "🟢"
            if p <= 1.5 * umbral / 100:
                return "🟠"
            return "🔴"

        t["🚦"] = t["Desp_pct"].map(_sem)
        t["Corte"] = t["Queda"].map(
            lambda q: "real (con conteo)" if pd.notna(q) else "aparente")

        k1, k2, k3 = st.columns(3)
        k1.metric("Entró total (al piso)", f"{t['Entregado'].sum():,.0f}")
        k2.metric("Teórico total", f"{t['Teorico_ajustado'].sum():,.0f}")
        gast = base.sum()
        k3.metric("Desperdicio (vs teórico)", f"{gast - t['Teorico_ajustado'].sum():+,.0f}")

        vista = t.rename(columns={
            "Tipo_bloque": "Tipo", "Teorico_ajustado": "Teórico (aj.)",
            "Entregado": "Entró", "Desp_pct": "Desp. %"})
        cols = ["Sector", "Piso", "Tipo", "Entró", "Teórico (aj.)", "Queda",
                "Gastado", "Desperdicio", "Desp. %", "🚦", "Corte"]
        st.dataframe(
            vista[cols].style.format({
                "Entró": "{:,.0f}", "Teórico (aj.)": "{:,.1f}", "Queda": "{:,.0f}",
                "Gastado": "{:,.1f}", "Desperdicio": "{:+,.1f}", "Desp. %": "{:+.1%}",
            }, na_rep="—"),
            width="stretch", hide_index=True)

        # Gráfica por piso: Entró / Teórico / Gastado
        try:
            g = t.assign(_Gast=base).groupby("Piso", as_index=False).agg(
                **{"Entró": ("Entregado", "sum"),
                   "Teórico": ("Teorico_ajustado", "sum"),
                   "Gastado": ("_Gast", "sum")})
            if not g.empty:
                st.bar_chart(g.set_index("Piso")[["Entró", "Teórico", "Gastado"]])
        except Exception:
            pass

        # Sobrecosto P.V. (caro pegado donde iba P.H.)
        pv = t[t["Tipo_bloque"].astype(str).str.upper().str.startswith("P.V")]
        if not pv.empty:
            base_pv = pv["Gastado"].where(pv["Queda"].notna(), pv["Entregado"])
            extra = (base_pv - pv["Teorico_ajustado"]).clip(lower=0).sum()
            if extra >= 1:
                st.warning(
                    f"⚠️ **Sobrecosto P.V.**: ~{extra:,.0f} bloques **P.V. (caros) de más** "
                    "sobre el teórico — probablemente pegados donde iba P.H. (barato).")

    st.divider()
    avance = _avance_pedido(df_ent, catalogo)
    if not avance.empty:
        st.subheader("🎯 Avance del pedido (recibido vs techo)")
        av = avance.copy()
        for col in ("Recibido", "Techo (pedido)", "% avance", "Pendiente"):
            av[col] = pd.to_numeric(av[col], errors="coerce")
        av["Estado"] = av["% avance"].map(
            lambda p: "(sin techo)" if pd.isna(p) else
            ("🔴 pasó" if p > 1 else ("🟠 cerca" if p >= 0.9 else "🟢")))
        st.dataframe(
            av.style.format({
                "Recibido": "{:,.0f}", "Techo (pedido)": "{:,.0f}",
                "% avance": "{:.0%}", "Pendiente": "{:,.0f}"}, na_rep="—"),
            width="stretch", hide_index=True)

    st.divider()
    st.subheader("✍️ Registrar conteo del corte (lo que sobró en el piso)")
    st.caption("Cuando hacen el corte (cada 15 días o al cerrar el piso), cuenta lo "
               "que QUEDÓ físico por piso y tipo, y guárdalo aquí.")
    _form_conteo(df, catalogo)


def pagina_materiales(df: pd.DataFrame):
    st.header("🧱 Materiales y desperdicio")

    if "flash_salida" in st.session_state:
        st.success(st.session_state.pop("flash_salida"))
    if "flash_entrada" in st.session_state:
        st.success(st.session_state.pop("flash_entrada"))

    try:
        df_sal = cargar_salidas_cached()
    except Exception as e:
        st.error(
            "No se pudieron leer las salidas de almacén. Si usas Supabase, corre la "
            "sección nueva de `supabase_schema.sql` (tabla `almacen_salidas`)."
        )
        with st.expander("Detalle técnico"):
            st.code(str(e))
        return

    try:
        df_ent = cargar_entradas_cached()
    except Exception as e:
        st.error(
            "No se pudieron leer las entradas de almacén. Si usas Supabase, corre la "
            "sección nueva de `supabase_schema.sql` (tabla `almacen_entradas`)."
        )
        with st.expander("Detalle técnico"):
            st.code(str(e))
        return

    # Estibas devueltas: si la tabla aún no existe (falta correr el SQL nuevo),
    # se degrada a vacío para no tumbar la página — el resto sigue funcionando.
    try:
        df_est = cargar_estibas_cached()
    except Exception:
        df_est = pd.DataFrame(columns=COLUMNAS_ESTIBAS)

    catalogo = _catalogo()

    tab_mov, tab_conc, tab_graf = st.tabs(
        ["📦 Movimientos de almacén", "⚖️ Conciliación", "📈 Gráficas"]
    )
    with tab_mov:
        _tab_movimientos(df, df_ent, df_sal, df_est, catalogo)
    with tab_conc:
        _tab_conciliacion(df, df_sal)
    with tab_graf:
        _tab_graficas_desperdicio(df, df_sal)

    if _es_admin():
        _corregir_movimientos(df_ent, df_sal, df_est)
        _editor_catalogo(catalogo)


# ─────────────────────────────────────────────────────────────
# Autenticación — login con Supabase Auth (email + contraseña · $0, sin Azure)
# ─────────────────────────────────────────────────────────────
def _correos_autorizados() -> set:
    """Lista blanca de correos (en minúsculas) tomada de los secrets.
    Vacía ⇒ se permite cualquier cuenta que se registre/inicie sesión."""
    try:
        crudo = str(st.secrets.get("CORREOS_AUTORIZADOS", "")).strip()
    except Exception:
        crudo = ""
    return {c.strip().lower() for c in crudo.replace(";", ",").split(",") if c.strip()}


def _correos_admin() -> set:
    """Correos (en minúsculas) que pueden EDITAR la configuración.

    Se toman de `CORREOS_ADMIN` en los secrets. Si está vacío, se reutiliza la
    lista de `CORREOS_AUTORIZADOS`; y si esa también está vacía (modo abierto),
    cualquier usuario autenticado se considera admin."""
    try:
        crudo = str(st.secrets.get("CORREOS_ADMIN", "")).strip()
    except Exception:
        crudo = ""
    admins = {c.strip().lower() for c in crudo.replace(";", ",").split(",") if c.strip()}
    return admins or _correos_autorizados()


def _es_admin() -> bool:
    """True si el usuario en sesión puede editar la configuración."""
    correo = (st.session_state.get("auth_email") or "").lower()
    if not correo:
        return False
    admins = _correos_admin()
    return (not admins) or (correo in admins)


def _logout() -> None:
    """Cierra la sesión local (callback del botón 'Cerrar sesión')."""
    st.session_state.pop("auth_email", None)


def _pantalla_login() -> None:
    """Formulario de Iniciar sesión / Registrarse contra Supabase Auth."""
    st.markdown("## 🧱 Control de Mampostería")
    st.caption("App de MYT para registrar los muros del día y controlar el gasto de mortero y bloques.")

    tab_entrar, tab_registro = st.tabs(["Iniciar sesión", "Registrarse"])

    with tab_entrar:
        with st.form("form_login"):
            email = st.text_input("Correo")
            pwd = st.text_input("Contraseña", type="password")
            enviar = st.form_submit_button("Entrar", type="primary", use_container_width=True)
        if enviar:
            ok, payload = auth.iniciar_sesion(email, pwd)
            if ok:
                st.session_state["auth_email"] = payload
                st.rerun()
            else:
                st.error(payload)

    with tab_registro:
        with st.form("form_registro"):
            email_r = st.text_input("Correo", key="reg_email")
            pwd_r = st.text_input("Contraseña (mínimo 6 caracteres)", type="password", key="reg_pwd")
            crear = st.form_submit_button("Crear cuenta", use_container_width=True)
        if crear:
            ok, mensaje, necesita_confirmar = auth.registrar(email_r, pwd_r)
            if ok and not necesita_confirmar:
                st.session_state["auth_email"] = (email_r or "").strip().lower()
                st.rerun()
            elif ok:
                st.info(mensaje)
            else:
                st.error(mensaje)

    st.stop()


def requerir_login() -> None:
    """Bloquea la app hasta iniciar sesión. Si hay lista blanca, valida el correo."""
    if not auth.disponible():
        st.error(
            "El login necesita Supabase. Configura `SUPABASE_URL` y `SUPABASE_KEY` "
            "en `.streamlit/secrets.toml`."
        )
        st.stop()

    if not st.session_state.get("auth_email"):
        _pantalla_login()   # renderiza el formulario y hace st.stop()

    autorizados = _correos_autorizados()
    if autorizados and st.session_state["auth_email"] not in autorizados:
        st.error(
            f"La cuenta **{st.session_state['auth_email']}** no está autorizada.\n\n"
            "Pide al administrador que agregue tu correo a la lista de acceso."
        )
        st.button("Cerrar sesión", on_click=_logout)
        st.stop()


def _render_estado_conexion(conectado: bool) -> None:
    """Muestra en el sidebar el estado REAL de la conexión al backend de datos."""
    if not conectado:
        st.error("🔴 **No conectado a la base de datos**")
        return
    info = estado()
    render = st.warning if info["tipo"] == "local" else st.success
    render(f"{info['icono']} **{info['titulo']}**")


def _editor_config() -> None:
    """Panel (solo admin) para editar meta, kg/saco y proyecto; persiste en Supabase.

    El cambio aplica de inmediato a los indicadores que se calculan en vivo
    (colores, deltas vs meta, línea de meta) y a los registros NUEVOS. El histórico
    ya guardado conserva su `Cumple_meta`/`kg` del momento en que se ingresó."""
    with st.expander("⚙️ Configuración (admin)"):
        if not config_persistente():
            st.info(
                "La edición permanente requiere **Supabase**. Con SharePoint o el "
                "modo local los valores quedan fijos en el código."
            )
            return

        cfg = _cfg()
        with st.form("form_config"):
            proyecto = st.text_input("Nombre del proyecto", value=cfg.get("proyecto", PROYECTO))
            meta = st.number_input(
                "Meta teórica (sac/m²)", min_value=0.0, step=0.01, format="%.3f",
                value=float(cfg.get("meta_sac_m2", TEORICO_SAC_M2)),
            )
            kg = st.number_input(
                "KG por saco", min_value=0.0, step=0.5, format="%.1f",
                value=float(cfg.get("kg_por_saco", KG_POR_SACO)),
            )
            umbral = st.number_input(
                "Umbral desperdicio bloques (%)", min_value=0.0, step=0.5, format="%.1f",
                value=float(cfg.get("umbral_desperdicio_pct", UMBRAL_DESPERDICIO_PCT)),
                help="Semáforo de la conciliación en 🧱 Materiales (Test) (verde si ≤ umbral).",
            )
            factor = st.number_input(
                "Factor de Modulación (bloques teóricos)", min_value=0.5, max_value=2.0,
                step=0.01, format="%.2f",
                value=float(cfg.get("factor_ajuste_bloques", FACTOR_AJUSTE_BLOQUES)),
                help="Multiplica el teórico en la conciliación para cubrir medios "
                     "bloques/trabas (ej. 1.03–1.05). 1.00 = sin ajuste. La "
                     "modulación más el desperdicio no deberían pasar del 7 %.",
            )
            guardar = st.form_submit_button(
                "💾 Guardar configuración", type="primary", use_container_width=True
            )

        # Aviso (no bloquea) si modulación + desperdicio supera el 7 % recomendado.
        _aviso_tope(_factor_ajuste(), _umbral_pct())

        if guardar:
            if meta <= 0 or kg <= 0 or not proyecto.strip():
                st.error("La meta y los kg deben ser mayores que 0, y el proyecto no puede ir vacío.")
                return
            try:
                guardar_config(meta, kg, proyecto.strip(),
                               umbral_desperdicio_pct=umbral,
                               factor_ajuste_bloques=factor)
                cargar_config_cached.clear()             # invalida la caché de 60 s
                st.session_state["cfg"] = leer_config()  # refresca de inmediato
            except Exception as e:
                st.error(f"No se pudo guardar la configuración: {e}")
                return
            st.toast("Configuración guardada ✅")   # sobrevive al rerun
            st.rerun()


def _eliminar_registros(df: pd.DataFrame) -> None:
    """Panel (solo admin) para borrar DEFINITIVAMENTE registros mal digitados.

    La unidad es el **ingreso** (un `Grupo_id` = un envío del formulario, con
    todos sus muros). Usa un DELETE dirigido por Grupo_id (ver
    `eliminar_registros`): si los permisos lo impiden devuelve 0 y se avisa, sin
    duplicar ni corromper datos. Acción irreversible."""
    with st.expander("🗑️ Eliminar registros (admin)"):
        st.caption(
            "Borra **definitivamente** un ingreso mal digitado (con todos sus "
            "muros), también de la base de datos. **No se puede deshacer.**"
        )
        if df is None or df.empty:
            st.info("No hay registros para eliminar.")
            return

        d = df.copy()
        d["Grupo_id"] = d["Grupo_id"].fillna("").astype(str).str.strip()
        con_grupo = d[d["Grupo_id"] != ""]
        if con_grupo.empty:
            st.info("Los registros actuales no tienen identificador de grupo; "
                    "no se pueden eliminar desde aquí.")
            return

        resumen = (
            con_grupo.groupby("Grupo_id")
            .agg(Fecha=("Fecha", "first"), Oficial=("Oficial", "first"),
                 Sector=("Sector", "first"), Piso=("Piso", "first"),
                 Muros=("Largo_m", "count"), M2=("M2_ejecutados", "sum"),
                 Sacos=("Num_sacos", "sum"))
            .reset_index()
            .sort_values("Fecha", ascending=False)
        )

        def _txt(v):
            return str(v) if pd.notna(v) else "—"

        def _lbl(r):
            f = (pd.to_datetime(r["Fecha"]).strftime("%d/%m/%Y")
                 if pd.notna(r["Fecha"]) else "sin fecha")
            return (f"{f} · {_txt(r['Oficial'])} · {_txt(r['Sector'])}/"
                    f"{_txt(r['Piso'])} · {int(r['Muros'])} muro(s) · "
                    f"{r['M2']:.1f} m² · {r['Sacos']:.0f} sacos")

        resumen["_lbl"] = resumen.apply(_lbl, axis=1)

        # Nonce: tras borrar, sube y los widgets renacen vacíos (sin selección
        # ni checkbox marcado) y sin arrastrar opciones que ya no existen.
        nd = st.session_state.setdefault("del_nonce", 0)
        # Las opciones son índices únicos; el texto se muestra con format_func.
        # Así dos ingresos con la MISMA etiqueta no colisionan: cada uno es una
        # opción distinta y se borra el Grupo_id correcto.
        elegidos = st.multiselect(
            "Registros a eliminar", resumen.index.tolist(),
            format_func=lambda i: resumen.loc[i, "_lbl"], key=f"del_sel_{nd}",
            help="Cada opción es un ingreso completo (todos sus muros).",
        )
        if not elegidos:
            return

        ids = resumen.loc[elegidos, "Grupo_id"].tolist()
        n_filas = int(d["Grupo_id"].isin(ids).sum())
        st.warning(
            f"Se eliminarán **{len(ids)} ingreso(s)** = **{n_filas} fila(s)** de la "
            "base de datos. **No se puede deshacer.**"
        )
        ok = st.checkbox("Sí, eliminar definitivamente", key=f"del_ok_{nd}")
        if st.button("🗑️ Eliminar definitivamente", type="primary",
                     disabled=not ok, use_container_width=True):
            try:
                borradas = eliminar_registros(ids)
            except Exception as e:
                st.error(f"No se pudo eliminar: {e}")
                return
            if borradas == 0:
                st.error(
                    "No se borró nada. Si usas la clave **anon** en Supabase, falta "
                    "la política de borrado (DELETE) en la tabla: usa la clave "
                    "service_role o agrégala (ver supabase_schema.sql)."
                )
                return
            st.cache_data.clear()
            st.session_state["del_nonce"] = nd + 1
            st.toast(f"Eliminadas {borradas} fila(s) ✅")
            st.rerun()


# ─────────────────────────────────────────────────────────────
# Pantalla — Calculadora de mampostería (réplica del Excel)
# ─────────────────────────────────────────────────────────────
_SEMAFORO_EMOJI = {"VERDE": "🟢 VERDE", "NARANJA": "🟠 NARANJA", "ROJO": "🔴 ROJO"}


def _fmt_pct(x: float) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:.1f} %"


def _calc_tab_muro(cat_pv: list, cat_ph: list):
    """Calculadora de desperdicio de un muro (sandbox; no guarda nada)."""
    st.caption(
        "Cambia los valores y mira el desperdicio al instante. **No guarda** nada: "
        "es la misma cuenta de las hojas *Calculadora de muro* y *Muro combinado* "
        "del Excel, para estimar pedidos y revisar entregas."
    )
    modo = st.radio("Tipo de muro", ["Un solo tipo", "Combinado (P.V.+P.H.)"],
                    horizontal=True, key="calc_modo")

    c1, c2, c3 = st.columns(3)
    with c1:
        largo = st.number_input("Largo del muro (m)", min_value=0.0, value=4.5,
                                step=0.05, format="%.2f", key="calc_largo")
    with c2:
        alto = st.number_input("Alto del muro (m)", min_value=0.0, value=2.40,
                               step=0.05, format="%.2f", key="calc_alto")
    with c3:
        junta = st.selectbox("Junta de pega (cm)", JUNTAS_CM,
                             index=JUNTAS_CM.index(1.5), key="calc_junta")

    c4, c5 = st.columns(2)
    with c4:
        factor = st.number_input("Factor de Modulación", min_value=1.0, max_value=1.5,
                                 value=max(round(_factor_ajuste(), 2), 1.05),
                                 step=0.01, format="%.2f", key="calc_factor",
                                 help="Sube el teórico por cortes/medios bloques (1.00–1.10). "
                                      "Modulación + desperdicio no deberían pasar del 7 %.")
    with c5:
        umbral = st.number_input("Umbral desperdicio (%)", min_value=0.0,
                                 value=round(_umbral_pct(), 1), step=0.5,
                                 format="%.1f", key="calc_umbral",
                                 help="Verde hasta el umbral; rojo por encima de umbral×1.5.")

    # Aviso (no bloquea) si modulación + desperdicio supera el 7 % recomendado.
    _aviso_tope(factor, umbral)

    if modo == "Un solo tipo":
        nombres = [b["nombre"] for b in (cat_pv + cat_ph)]
        if not nombres:
            st.info("No hay bloques en el catálogo.")
            return
        cb1, cb2 = st.columns([2, 1])
        with cb1:
            nombre = st.selectbox("Tipo de bloque", nombres, key="calc_bloque")
        with cb2:
            entregado = st.number_input(
                "Simular entrega (bloques)",
                min_value=0, value=0, step=1, key="calc_entregado",
                help="Escribe cuántos bloques quieres simular que se entregan al apto — la app muestra el semáforo y el % de exceso al instante.",
            )
        bloque = _bloque_por_nombre(nombre)
        ent_param = entregado if entregado > 0 else None
        r = calculadora_muro(largo, alto, bloque, junta_cm=junta, factor=factor,
                             umbral_pct=umbral, entregado=ent_param)
        if not r:
            st.info("Completa largo y alto del muro.")
            return
        m1, m2 = st.columns(2)
        m1.metric("Teórico geométrico (Teoría)", f"{r['teorico_geom']:.0f} bloques",
                  help="Cuántos bloques caben matemáticamente en el área del muro.")
        m2.metric("Teórico ajustado (Real)", f"{r['teorico_ajustado']:.0f} bloques",
                  help=f"Lo que debes pedir: Teoría × Factor de Modulación {factor:g} (incluye cortes y mermas).")
        lim_verde_pct = umbral
        lim_rojo_pct = umbral * 1.5
        st.markdown(
            f"""| Indicador | Valor |
|---|---|
| Módulo bloque+junta | **{r['modulo']:.6f} m²** |
| Bloques por m² | **{r['bloques_m2']:.2f} und/m²** |
| Área del muro | **{r['area']:.2f} m²** |
| Teórico geométrico (Teoría) | **{r['teorico_geom']:.0f}** |
| Teórico ajustado (Real) (× {factor:g}) | **{r['teorico_ajustado']:.0f}** |
| 🟢 Normal hasta (umbral {lim_verde_pct:.1f} % sobre lo pedido) | **{r['lim_verde']:.0f} bloques** |
| 🔴 Alerta máxima (umbral × 1.5 = {lim_rojo_pct:.1f} % sobre lo pedido) | **{r['lim_rojo']:.0f} bloques** |"""
        )
        if ent_param is not None:
            desp_pct = r["desp_pct_ajustado"]
            margen = r["lim_verde"] - entregado
            if margen >= 0:
                margen_txt = f"Margen disponible antes de NARANJA | **{margen:.0f} bloques**"
            else:
                margen_txt = f"Exceso sobre el límite normal | **{abs(margen):.0f} bloques de más**"
            st.markdown(
                f"""| Resultado de la simulación | Valor |
|---|---|
| Bloques simulados | **{entregado:.0f}** |
| % simulado de exceso sobre lo pedido | **{_fmt_pct(desp_pct)}** |
| Umbral permitido configurado | **{umbral:.1f} %** |
| {margen_txt} |"""
            )
        else:
            st.caption("Escribe un número en **Simular entrega** para ver el semáforo y el % de desperdicio.")
        _badge_semaforo(r["semaforo"], entregado, r["lim_verde"], r["lim_rojo"], simulacion=True)
        return

    # Combinado P.V. + P.H.
    if not cat_pv or not cat_ph:
        st.info("Para el muro combinado necesitas bloques P.V. y P.H. en el catálogo.")
        return
    cc1, cc2, cc3 = st.columns(3)
    with cc1:
        nombre_pv = st.selectbox("Bloque P.V. (dovelas)", [b["nombre"] for b in cat_pv],
                                 key="calc_pv")
    with cc2:
        nombre_ph = st.selectbox("Bloque P.H. (relleno)", [b["nombre"] for b in cat_ph],
                                 key="calc_ph")
    with cc3:
        dovelas = st.number_input("Número de dovelas", min_value=0, value=3, step=1,
                                  key="calc_dovelas")
    ce1, ce2 = st.columns(2)
    with ce1:
        ent_pv = st.number_input(
            "Simular entrega P.V. (bloques)",
            min_value=0, value=0, step=1, key="calc_ent_pv",
            help="Escribe cuántos bloques P.V. (dovelas) quieres simular para ver el semáforo.",
        )
    with ce2:
        ent_ph = st.number_input(
            "Simular entrega P.H. (bloques)",
            min_value=0, value=0, step=1, key="calc_ent_ph",
            help="Escribe cuántos bloques P.H. (relleno) quieres simular para ver el semáforo.",
        )
    ent_pv_param = ent_pv if ent_pv > 0 else None
    ent_ph_param = ent_ph if ent_ph > 0 else None
    r = calculadora_combinado(
        largo, alto, "Combinado", dovelas, _bloque_por_nombre(nombre_pv),
        _bloque_por_nombre(nombre_ph), junta_cm=junta, factor=factor,
        umbral_pct=umbral, entregado_pv=ent_pv_param, entregado_ph=ent_ph_param,
    )
    if not r:
        st.info("Completa largo y alto del muro.")
        return
    m1, m2, m3 = st.columns(3)
    m1.metric("Teórico total (Teoría)", f"{r['total']:.0f} bloques",
              help="Suma de P.V. + P.H. sin Factor de Modulación.")
    m2.metric("Hiladas del muro", f"{r['hiladas']}",
              help="Número de filas de bloques que caben en el alto del muro con la junta elegida.")
    m3.metric("Rendimiento P.V.", f"{r['bloques_m2']:.2f} und/m²",
              help="Bloques por m² usando las dimensiones del bloque P.V. y la junta elegida.")

    lim_v_pct = umbral
    lim_r_pct = umbral * 1.5
    # Columnas con tipo uniforme (texto) para evitar warnings de Arrow
    hay_sim = ent_pv_param is not None or ent_ph_param is not None
    filas_tabla = [
        {"Concepto": "Teórico geométrico (Teoría)",
         "P.V. — dovelas": f"{r['pv']['teorico']:.0f}",
         "P.H. — relleno": f"{r['ph']['teorico']:.0f}"},
        {"Concepto": f"Teórico ajustado (Real) × {factor:g}",
         "P.V. — dovelas": f"{r['pv']['ajustado']:.0f}",
         "P.H. — relleno": f"{r['ph']['ajustado']:.0f}"},
        {"Concepto": f"🟢 Normal hasta (umbral {lim_v_pct:.1f} %)",
         "P.V. — dovelas": f"{r['pv']['lim_verde']:.0f} bloques",
         "P.H. — relleno": f"{r['ph']['lim_verde']:.0f} bloques"},
        {"Concepto": f"🔴 Alerta máxima (umbral × 1.5 = {lim_r_pct:.1f} %)",
         "P.V. — dovelas": f"{r['pv']['lim_rojo']:.0f} bloques",
         "P.H. — relleno": f"{r['ph']['lim_rojo']:.0f} bloques"},
    ]
    if hay_sim:
        filas_tabla += [
            {"Concepto": "Bloques simulados",
             "P.V. — dovelas": str(ent_pv) if ent_pv_param else "—",
             "P.H. — relleno": str(ent_ph) if ent_ph_param else "—"},
            {"Concepto": "% simulado de exceso sobre lo pedido",
             "P.V. — dovelas": _fmt_pct(r["pv"]["desp_pct"]) if ent_pv_param else "—",
             "P.H. — relleno": _fmt_pct(r["ph"]["desp_pct"]) if ent_ph_param else "—"},
            {"Concepto": "Resultado simulación",
             "P.V. — dovelas": _SEMAFORO_EMOJI.get(r["pv"]["semaforo"], "—") if ent_pv_param else "—",
             "P.H. — relleno": _SEMAFORO_EMOJI.get(r["ph"]["semaforo"], "—") if ent_ph_param else "—"},
        ]
    st.dataframe(pd.DataFrame(filas_tabla), hide_index=True, width="stretch")
    if not hay_sim:
        st.caption("Escribe valores en **Simular entrega P.V.** y/o **P.H.** para ver el resultado de la simulación.")
    else:
        for lado, ent, key in [("P.V.", ent_pv, "pv"), ("P.H.", ent_ph, "ph")]:
            if not (ent > 0):
                continue
            margen = r[key]["lim_verde"] - ent
            if margen >= 0:
                st.info(f"**{lado}** — te quedan **{margen:.0f} bloques** antes de entrar a NARANJA.")
            else:
                st.warning(f"**{lado}** — ya superaste el límite normal por **{abs(margen):.0f} bloques**.")


def _badge_semaforo(estado: str, entregado, lim_verde, lim_rojo, simulacion: bool = False):
    """Muestra el semáforo con color y explicación. simulacion=True cambia el lenguaje."""
    if not estado:
        return
    if simulacion:
        txt = (
            f"Simulando **{entregado:.0f}** bloques entregados. "
            f"Normal hasta **{lim_verde:.0f}** · alerta máxima desde **{lim_rojo:.0f}**."
        )
        if estado == "VERDE":
            st.success(f"🟢 **VERDE — simulación normal.** {txt} La cantidad simulada está dentro del rango esperado.")
        elif estado == "NARANJA":
            st.warning(f"🟠 **NARANJA — revisar.** {txt} La cantidad simulada supera el límite normal, habría pérdidas o exceso.")
        else:
            st.error(f"🔴 **ROJO — alerta en simulación.** {txt} La cantidad simulada supera el límite máximo.")
    else:
        txt = (
            f"Entregaste **{entregado:.0f}** bloques. "
            f"Normal hasta **{lim_verde:.0f}** · alerta máxima desde **{lim_rojo:.0f}**."
        )
        if estado == "VERDE":
            st.success(f"🟢 **VERDE — entrega normal.** {txt} Estás dentro del rango esperado.")
        elif estado == "NARANJA":
            st.warning(f"🟠 **NARANJA — revisar.** {txt} Se están entregando más bloques de lo previsto, revisa si hay pérdidas o errores de conteo.")
        else:
            st.error(f"🔴 **ROJO — acción inmediata.** {txt} El exceso supera el límite máximo, investiga la causa antes de seguir entregando.")


def _calc_tab_resumen_apto(df: pd.DataFrame):
    """RESUMEN POR TIPO: cuántos bloques teóricos por apto/piso (réplica del Excel)."""
    if df is None or df.empty:
        st.info("Aún no hay registros. Captura muros en **📋 Ingreso de datos**.")
        return

    # Factor y desperdicio editables SOLO en esta vista (simulación del pedido):
    # arrancan en los del proyecto, pero el usuario puede probar otros valores y
    # el cálculo de "Con factor"/"Factor + desperdicio" se actualiza al instante.
    factor_cfg = _factor_ajuste()
    umbral_cfg = _umbral_pct()
    FACTORES = [1.0, 1.02, 1.05, 1.08, 1.10]
    DESPERDICIOS = [2, 5, 7, 8, 9]

    def _idx(opts, val):
        try:
            return opts.index(val)
        except ValueError:   # el valor del proyecto no está en la lista → el más cercano
            return min(range(len(opts)), key=lambda i: abs(opts[i] - val))

    cf, cd, co = st.columns([1, 1, 1])
    with cf:
        factor = st.selectbox(
            "Factor de Modulación (cortes/trabas)", FACTORES,
            index=_idx(FACTORES, round(float(factor_cfg), 2)),
            format_func=lambda x: f"{x:g}", key="resapto_factor",
            help="Multiplica el teórico para cubrir cortes, medios bloques y "
                 "trabas. Arranca en el del proyecto; aquí podés simular otros. "
                 "Modulación + desperdicio no deberían pasar del 7 %.",
        )
    with cd:
        umbral = st.selectbox(
            "Desperdicio (%)", DESPERDICIOS,
            index=_idx(DESPERDICIOS, int(round(float(umbral_cfg)))),
            format_func=lambda x: f"{x:g} %", key="resapto_umbral",
            help="Margen de desperdicio que se suma sobre 'Con Factor de Modulación' "
                 "para el pedido final.",
        )
    with co:
        ocultar_cero = st.checkbox("Ocultar tipos sin uso", value=True,
                                   key="resapto_cero")

    st.caption(
        f"**Necesario** = bloques teóricos que llevan los muros · "
        f"**Con Factor de Modulación** = Necesario × Factor de Modulación {factor:g} "
        f"(cortes/trabas) · **Factor de Modulación + desperdicio** = Con Factor de "
        f"Modulación + margen de desperdicio ({umbral:g} %), redondeado hacia arriba "
        f"(lo que se le pide al proveedor)."
    )

    # Aviso (no bloquea) si modulación + desperdicio supera el 7 % recomendado.
    _aviso_tope(factor, umbral)

    catalogo = _catalogo()

    def _tabla_resumen(df_src, apto_param, label_obra):
        res = resumen_pedido_por_tipo(df_src, catalogo, apto=apto_param,
                                     factor=factor, umbral_pct=umbral)
        por = res["por_tipo"].copy()
        if ocultar_cero:
            por = por[por["Total_obra"] > 0]
        if por.empty:
            st.info("No hay bloques teóricos registrados para ese filtro.")
            return
        col_obra = label_obra
        # Títulos con el factor y el % en uso, para que se entienda de dónde sale cada
        # número (p.ej. "Con Factor de Modulación (×1.1)" y "Factor de Modulación + desperdicio (×1.1 +9%)").
        lbl_factor = f"Con Factor de Modulación (×{factor:g})"
        lbl_pedido = f"Factor de Modulación + desperdicio (×{factor:g} +{umbral:g}%)"
        display = por.rename(columns={
            "Tipo_bloque": "Tipo de bloque",
            "Total_obra": col_obra,
            "Con_factor": lbl_factor,
            "A_pedir": lbl_pedido,
        })
        cols_show = ["Tipo de bloque", col_obra, lbl_factor, lbl_pedido]
        if apto_param:
            display = display.rename(columns={"Total_apto": "Necesario apto"})
            cols_show = ["Tipo de bloque", "Necesario apto", col_obra, lbl_factor, lbl_pedido]
        st.dataframe(
            display[cols_show].style.format(
                {c: "{:,.0f}" for c in cols_show if c != "Tipo de bloque"}
            ),
            hide_index=True, width="stretch",
        )
        t = res["totales"]
        filas = []
        for lbl, k in [("P.V.", "pv"), ("P.H.", "ph"), ("GENERAL", "general")]:
            td = t[k]
            if apto_param:
                filas.append(
                    f"| **{lbl}** | {td['Total_apto']:,.0f} | {td['Total_obra']:,.0f} "
                    f"| {td['Con_factor']:,.0f} | **{td['A_pedir']:,.0f}** |"
                )
            else:
                filas.append(
                    f"| **{lbl}** | {td['Total_obra']:,.0f} "
                    f"| {td['Con_factor']:,.0f} | **{td['A_pedir']:,.0f}** |"
                )
        header = (
            f"| | Necesario apto | {col_obra} | {lbl_factor} | {lbl_pedido} |\n|---|---|---|---|---|"
            if apto_param else
            f"| | {col_obra} | {lbl_factor} | {lbl_pedido} |\n|---|---|---|---|"
        )
        st.markdown(header + "\n" + "\n".join(filas))

    # ── SECCIÓN 1: Totales del proyecto completo ─────────────────────────────
    st.subheader("📊 Totales del proyecto completo")
    st.caption("Suma de todos los muros registrados, sin filtro de piso ni apto.")
    _tabla_resumen(df, apto_param=None, label_obra="Necesario")

    st.divider()

    # ── SECCIÓN 2: Filtrado por piso / apto ──────────────────────────────────
    st.subheader("🔍 Detalle filtrado")
    pisos = ["Todos"] + [p for p in opciones_unicas(df, "Piso") if p]
    aptos = [z for z in opciones_unicas(df, "Zona") if z]

    f0, f1 = st.columns(2)
    with f0:
        piso_sel = st.selectbox("Piso", pisos, key="resapto_piso")
    with f1:
        apto_sel = st.selectbox("Apto / Zona", ["Todos"] + aptos, key="resapto_apto")

    df_f = df.copy()
    if piso_sel != "Todos":
        df_f = df_f[df_f["Piso"] == piso_sel]
    if apto_sel != "Todos":
        df_f = df_f[df_f["Zona"] == apto_sel]

    if df_f.empty:
        st.info("No hay registros para ese filtro.")
        return

    # El cálculo (incluido "Factor + desperdicio") refleja EXACTAMENTE el filtro elegido:
    # pasamos el df ya filtrado por piso+apto y apto_param=None para que
    # Necesario/Con factor/Factor + desperdicio salgan de la selección, no del proyecto.
    _tabla_resumen(df_f, apto_param=None, label_obra="Necesario")


def _calc_tab_rendimiento(catalogo: list):
    """Rendimiento und/m² por bloque y espesor de junta (hoja Referencia)."""
    st.caption("Bloques por m² (sin desperdicio) según el espesor de junta. "
               "Sale de la misma fórmula de la calculadora.")
    tabla = rendimiento_por_junta(catalogo)
    if tabla.empty:
        st.info("No hay bloques en el catálogo.")
        return
    cols_junta = [c for c in tabla.columns if c != "Bloque"]
    st.dataframe(
        tabla.style.format({c: "{:.1f}" for c in cols_junta}),
        hide_index=True, width="stretch",
    )


def _apto_excel(params: dict, df_tipo: pd.DataFrame, df_detalle: pd.DataFrame) -> bytes:
    """Excel del simulador de apto: Parámetros + Bloques por tipo + Detalle por muro.
    Se genera en memoria (openpyxl); NO lee ni escribe en Supabase."""
    df_param = pd.DataFrame({"Parámetro": list(params.keys()),
                             "Valor": list(params.values())})
    return excel_libro({
        "Parametros": df_param,
        "Por tipo": df_tipo,
        "Por muro": df_detalle,
    })


def _apto_pdf(params: dict, df_tipo: pd.DataFrame, df_detalle: pd.DataFrame):
    """PDF del simulador de apto. Devuelve None si falta `fpdf2`. Se genera en
    memoria; NO toca la base de datos."""
    try:
        from fpdf import FPDF
    except Exception:
        return None

    def _t(x) -> str:  # fuentes core de fpdf = latin-1: limpia lo que no entra
        s = (str(x).replace("²", "2").replace("×", "x").replace("→", "->")
             .replace("≤", "<=").replace("—", "-").replace("–", "-").replace("•", "-"))
        return s.encode("latin-1", "replace").decode("latin-1")  # cualquier otro → "?"

    def _fila(pdf, valores, anchos, h=6, border=1, align="C"):
        for v, w in zip(valores, anchos):
            pdf.cell(w, h, _t(v), border=border, align=align)
        pdf.ln(h)

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 9, _t("Simulación de apto — Mampostería MyT")); pdf.ln(9)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, _t(f"Generado: {datetime.now():%Y-%m-%d %H:%M}  ·  documento de simulación")); pdf.ln(8)

    # Parámetros
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, _t("Parámetros")); pdf.ln(7)
    pdf.set_font("Helvetica", "", 9)
    for k, v in params.items():
        _fila(pdf, [k, v], [80, 50], align="L")
    pdf.ln(4)

    # Bloques por tipo
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, _t("Bloques por tipo")); pdf.ln(7)
    anchos_t = [90, 35, 40, 35]
    pdf.set_font("Helvetica", "B", 9)
    _fila(pdf, list(df_tipo.columns), anchos_t)
    pdf.set_font("Helvetica", "", 9)
    for _, row in df_tipo.iterrows():
        _fila(pdf, [row[c] for c in df_tipo.columns], anchos_t)
    pdf.ln(4)

    # Relación por muro (cuánto P.V. vs P.H. lleva cada muro)
    if not df_detalle.empty:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, _t("Relación por muro")); pdf.ln(7)
        anchos_d = [14, 18, 26, 40, 16, 18, 40, 16, 18, 18]
        pdf.set_font("Helvetica", "B", 8)
        _fila(pdf, list(df_detalle.columns), anchos_d, h=6)
        pdf.set_font("Helvetica", "", 8)
        for _, row in df_detalle.iterrows():
            _fila(pdf, [row[c] for c in df_detalle.columns], anchos_d, h=6)

    return bytes(pdf.output())


def _calc_tab_apto(cat_pv: list, cat_ph: list):
    """Simulador de un apto completo (sandbox, no guarda): varios muros
    (combinados o de un solo tipo) → bloques por tipo en TRES niveles
    (teórico, con Factor de Modulación, y con el desperdicio FINAL compuesto).

    El punto clave: el Factor de Modulación y el % de desperdicio se MULTIPLICAN
    (no se suman). El desperdicio final = factor × (1 + desp) − 1; p.ej. 1.05 × 1.07
    = 1.1235 → 12.35 %, no 7 %.
    """
    st.caption(
        "Pon **un muro por fila** (combinado o de un solo tipo) y mira cuántos "
        "bloques de cada tipo necesita el apto en **tres niveles**: teórico, con "
        "**Factor de Modulación** y con el **desperdicio final**. **No guarda nada.**"
    )
    nombres_pv = [b["nombre"] for b in cat_pv]
    nombres_ph = [b["nombre"] for b in cat_ph]

    c1, c2, c3 = st.columns(3)
    with c1:
        junta = st.selectbox("Junta de pega (cm)", JUNTAS_CM,
                             index=JUNTAS_CM.index(1.5), key="apto_junta")
    with c2:
        factor = st.number_input(
            "Factor de Modulación", min_value=1.0, max_value=1.5,
            value=max(round(_factor_ajuste(), 2), 1.05), step=0.01, format="%.2f",
            key="apto_factor",
            help="Sube el teórico por cortes/medios bloques (ej. 1.05 = +5 %).")
    with c3:
        desp = st.number_input(
            "% Desperdicio", min_value=0.0, value=round(_umbral_pct(), 1),
            step=0.5, format="%.1f", key="apto_desp",
            help="Roturas/mermas. Se aplica DESPUÉS del factor (se multiplican).")

    # Desperdicio final compuesto: factor × (1 + desp) − 1 (NO es la suma).
    final_factor = factor * (1 + desp / 100.0)
    desp_final_pct = (final_factor - 1) * 100.0
    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("Factor de Modulación", f"+{(factor - 1) * 100:.1f} %")
    mc2.metric("Desperdicio", f"+{desp:.1f} %")
    mc3.metric("Desperdicio FINAL", f"+{desp_final_pct:.2f} %",
               help="Factor × (1 + desperdicio) − 1. Se multiplican, no se suman.")
    st.caption(
        f"Cálculo del final: **{factor:g} × {1 + desp / 100:.2f} = {final_factor:.4f}** "
        f"→ pides **{desp_final_pct:.2f} %** más que el teórico (no {desp:g} %)."
    )

    muros_init = pd.DataFrame([{
        "Largo_m": 0.0, "Alto_m": 2.40, "Num_dovelas": 0,
        "Uso": USO_COMBINADO, "Bloque_PV": "", "Bloque_PH": "",
    }])
    col_cfg = {
        "Largo_m": st.column_config.NumberColumn("Largo (m)", min_value=0.0, step=0.01, format="%.2f"),
        "Alto_m": st.column_config.NumberColumn("Alto muro (m)", min_value=0.0, step=0.01, format="%.2f"),
        "Num_dovelas": st.column_config.NumberColumn(
            "# Dovelas + redes", min_value=0, step=1, format="%d",
            help="Columnas P.V. de piso a techo: dovelas y redes juntas (cuentan igual)."),
        "Uso": st.column_config.SelectboxColumn("Uso del muro", options=USOS_MURO, required=True, width="medium"),
        "Bloque_PV": st.column_config.SelectboxColumn("Bloque P.V. (dovelas)", options=[""] + nombres_pv, width="medium"),
        "Bloque_PH": st.column_config.SelectboxColumn("Bloque P.H. (relleno)", options=[""] + nombres_ph, width="medium"),
    }
    editado = st.data_editor(muros_init, num_rows="dynamic", key="apto_editor",
                             width="stretch", column_config=col_cfg)

    # Acumula el teórico geométrico por NOMBRE de tipo y guarda el detalle por muro.
    por_tipo: dict[str, float] = {}
    detalle: list[dict] = []
    area_total = 0.0
    for r in editado.itertuples():
        if not (pd.notna(r.Largo_m) and pd.notna(r.Alto_m)
                and r.Largo_m > 0 and r.Alto_m > 0):
            continue
        uso_disp = getattr(r, "Uso", USO_COMBINADO)
        if not isinstance(uso_disp, str) or uso_disp not in USOS_MURO:
            uso_disp = USO_COMBINADO
        nec_pv = uso_disp in (USO_COMBINADO, USO_VERTICAL)
        nec_ph = uso_disp in (USO_COMBINADO, USO_HORIZONTAL)
        t_pv = (getattr(r, "Bloque_PV", "") or "").strip() if nec_pv else ""
        t_ph = (getattr(r, "Bloque_PH", "") or "").strip() if nec_ph else ""
        b_pv = _bloque_con_junta(_bloque_por_nombre(t_pv), junta) if t_pv else None
        b_ph = _bloque_con_junta(_bloque_por_nombre(t_ph), junta) if t_ph else None
        if not (b_pv or b_ph):
            continue
        ndov = int(r.Num_dovelas) if pd.notna(r.Num_dovelas) else 0
        teo = bloques_teoricos_muro(float(r.Largo_m), float(r.Alto_m), ndov,
                                    b_pv, b_ph, uso=_USO_A_MODO.get(uso_disp, "Auto"))
        if t_pv:
            por_tipo[t_pv] = por_tipo.get(t_pv, 0.0) + teo["pv"]
        if t_ph:
            por_tipo[t_ph] = por_tipo.get(t_ph, 0.0) + teo["ph"]
        area_m = float(r.Largo_m) * float(r.Alto_m)
        area_total += area_m
        pv_b, ph_b = teo["pv"], teo["ph"]
        tot_b = pv_b + ph_b
        detalle.append({
            "Muro": len(detalle) + 1, "m²": round(area_m, 2),
            "Uso": uso_disp.split(" (")[0],
            "Bloque P.V.": t_pv or "—", "P.V.": round(pv_b),
            "% P.V.": f"{pv_b / tot_b * 100:.0f}%" if tot_b else "—",
            "Bloque P.H.": t_ph or "—", "P.H.": round(ph_b),
            "% P.H.": f"{ph_b / tot_b * 100:.0f}%" if tot_b else "—",
            "Total": round(tot_b),
        })
    n_muros = len(detalle)

    if not por_tipo:
        st.info("Agrega muros con **Largo**, **Alto** y su(s) **bloque(s)** para ver el cálculo.")
        return

    filas = ""
    tt = tf = td = 0.0
    filas_tipo = []   # para la exportación
    for t, teo in sorted(por_tipo.items(), key=lambda kv: -kv[1]):
        con_f = teo * factor
        con_d = teo * final_factor
        tt += teo
        tf += con_f
        td += con_d
        filas += (f"| {t} | {teo:,.0f} | {con_f:,.0f} | {math.ceil(con_d):,d} |\n")
        filas_tipo.append({"Tipo de bloque": t, "Teórico": round(teo),
                           "Con factor": round(con_f), "Final": math.ceil(con_d)})

    st.markdown(
        f"**Bloques por tipo — apto de {n_muros} muro(s), {area_total:.2f} m²:**\n\n"
        f"| Tipo de bloque | Teórico | Con factor (×{factor:g}) | Final (+{desp_final_pct:.1f} %) |\n"
        "|---|---|---|---|\n"
        f"{filas}"
        f"| **Total** | **{tt:,.0f}** | **{tf:,.0f}** | **{math.ceil(td):,d}** |\n"
    )
    st.caption(
        f"El **Final** (lo que pides) ya trae el desperdicio compuesto: teórico × "
        f"{factor:g} × (1 + {desp:g} %). Son **{math.ceil(td) - round(tt):,d} bloques "
        f"de más** que el teórico (**+{desp_final_pct:.1f} %**)."
    )

    # Relación por muro: cuánto P.V. vs P.H. lleva CADA muro (Total = 100 %).
    df_detalle = pd.DataFrame(detalle)
    st.markdown("**Relación por muro** (de cada muro: cuánto es P.V. y cuánto P.H.):")
    st.dataframe(df_detalle, hide_index=True, width="stretch")

    # ── Descargas (Excel / PDF). Sandbox: se generan en memoria, NO tocan Supabase.
    params = {
        "Muros": n_muros, "Área total (m²)": round(area_total, 2),
        "Junta (cm)": junta, "Factor de Modulación": factor,
        "% Desperdicio": desp, "Desperdicio final (%)": round(desp_final_pct, 2),
    }
    df_tipo = pd.DataFrame(filas_tipo + [{
        "Tipo de bloque": "TOTAL", "Teórico": round(tt),
        "Con factor": round(tf), "Final": math.ceil(td)}])
    st.caption("📥 Descarga el resultado:")
    dc1, dc2 = st.columns(2)
    with dc1:
        st.download_button(
            "⬇️ Excel", data=_apto_excel(params, df_tipo, df_detalle),
            file_name="simulacion_apto.xlsx", width="stretch",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with dc2:
        pdf_bytes = _apto_pdf(params, df_tipo, df_detalle)
        if pdf_bytes is not None:
            st.download_button("⬇️ PDF", data=pdf_bytes, file_name="simulacion_apto.pdf",
                               mime="application/pdf", width="stretch")
        else:
            st.button("⬇️ PDF", disabled=True, width="stretch",
                      help="Falta la librería fpdf2 en el servidor.")


def pagina_calculadora():
    st.header("🧮 Calculadora de mampostería")
    cat_pv = _bloques_clase("PV")
    cat_ph = _bloques_clase("PH")
    if not (cat_pv or cat_ph):
        st.info("No hay catálogo de bloques cargado; configúralo en el panel admin.")
        return
    tab1, tab_apto, tab2 = st.tabs(
        ["🧱 Calculadora de muro", "🏠 Simular apto", "📐 Rendimiento"])
    with tab1:
        _calc_tab_muro(cat_pv, cat_ph)
    with tab_apto:
        _calc_tab_apto(cat_pv, cat_ph)
    with tab2:
        _calc_tab_rendimiento(_catalogo())


def pagina_resumen_pedido(df: pd.DataFrame):
    st.header("📦 Resumen ladrillos por apto")
    _calc_tab_resumen_apto(df)


def main():
    requerir_login()

    # Cargar datos PRIMERO para conocer el estado real de conexión a la BD.
    df, conectado, detalle_error = cargar_datos_estado()

    # Config editable (meta, kg/saco, proyecto): cacheada 60 s, defectos si falla.
    st.session_state["cfg"] = cargar_config_cached()
    # Catálogo de bloques (P.V./P.H.) para los bloques teóricos y las salidas.
    st.session_state["catalogo"] = cargar_catalogo_cached()
    # Valores ocultos de las listas (oficiales/ayudantes/pisos/sectores retirados).
    st.session_state["valores_ocultos"] = cargar_ocultos_cached()

    with st.sidebar:
        st.markdown("## Mampostería MyT")
        st.markdown("---")
        pagina = st.radio(
            "Navegación",
            ["📋 Ingreso de datos", "📈 Control", "📅 Cierres",
             "🧱 Materiales (Test)", "🧮 Calculadora", "📦 Resumen ladrillos",
             "📊 Registros"],
            label_visibility="collapsed",
        )
        st.markdown("---")
        st.caption(f"📁 Proyecto: {_proyecto()}")
        st.caption(f"🎯 Meta teórica: {_meta():g} sac/m²")
        st.caption(f"⚖️ KG por saco: {_kg():g} kg")
        if _es_admin():
            _editor_config()
            _eliminar_registros(df)
        st.markdown("---")
        _render_estado_conexion(conectado)
        st.markdown("---")
        st.caption(f"👤 {st.session_state.get('auth_email', '')}")
        st.button("Cerrar sesión", use_container_width=True, on_click=_logout)

    # Si no hay conexión, avisar en el área principal y no continuar.
    if not conectado:
        st.error(
            "No se pudo conectar a la base de datos. Verifica tu conexión a internet "
            "o que el proyecto de Supabase esté activo, y vuelve a intentar."
        )
        with st.expander("Detalle técnico"):
            st.code(detalle_error or "sin detalle")
        st.stop()

    barra_kpi_global(df)

    if pagina == "📋 Ingreso de datos":
        pagina_ingreso(df)
    elif pagina == "📊 Registros":
        pagina_registros(df)
    elif pagina == "📈 Control":
        pagina_graficas(df)
    elif pagina == "📅 Cierres":
        pagina_cierres(df)
    elif pagina == "🧱 Materiales (Test)":
        pagina_materiales(df)
    elif pagina == "🧮 Calculadora":
        pagina_calculadora()
    elif pagina == "📦 Resumen ladrillos":
        pagina_resumen_pedido(df)


if __name__ == "__main__":
    main()
