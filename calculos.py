"""
calculos.py — Lógica de negocio de mampostería (SIN Streamlit)
──────────────────────────────────────────────────────────────
Funciones puras (solo pandas/Python) para los cálculos del proyecto. Al no
depender de Streamlit son fáciles de **probar** (ver test_calculos.py) y de
**reutilizar** desde la app, scripts de migración o futuros backends.

Regla de oro del consumo (confirmada con el negocio):
    consumo = Σ sacos ÷ Σ m²   (ratio de sumas)
NUNCA el promedio de los consumos por fila: como el total de sacos se guarda
una sola vez por grupo de muros, promediar por fila pondera mal y da cifras
distintas a la realidad.
"""

import uuid

import pandas as pd

from data_schema import COLUMNAS

# ── Constantes del negocio ──────────────────────────────────────────
TEORICO_SAC_M2 = 0.84    # Meta de consumo: sacos por m² (cumple si consumo ≤ meta)
KG_POR_SACO = 42.5       # Kilogramos por saco de mortero

# Desperdicio de bloque: umbral del semáforo y factor de ajuste del teórico
# (cubre medios bloques, trabas y cortes; configurable desde la app).
UMBRAL_DESPERDICIO_PCT = 7.0
FACTOR_AJUSTE_BLOQUES = 1.0

# Catálogo por defecto de bloques de Serrania (editable por admin en Supabase).
# clase: "PV" = perforación vertical (estructural, donde van las dovelas)
#        "PH" = perforación horizontal (divisorio).
# Medidas del BLOQUE SIN pega, en metros. `junta_m` es la pega real promedio de
# la obra (1.5 cm en Serrania; con 2.0 cm el admin la sube en el catálogo).
# bloque + junta = módulo: 0.415 × 0.215 → 11.2 und/m², hilada de 0.215 m.
# `unds_por_estiba` permite digitar las salidas de almacén en estibas.
CATALOGO_BLOQUES_DEFECTO = [
    {"nombre": "P.V. rayado 12", "clase": "PV", "largo_m": 0.40, "alto_m": 0.20,
     "espesor_m": 0.12, "junta_m": 0.015, "unds_por_estiba": 90},
    {"nombre": "P.V. rayado 15", "clase": "PV", "largo_m": 0.40, "alto_m": 0.20,
     "espesor_m": 0.15, "junta_m": 0.015, "unds_por_estiba": 72},
    {"nombre": "P.H. rayado 12", "clase": "PH", "largo_m": 0.40, "alto_m": 0.20,
     "espesor_m": 0.12, "junta_m": 0.015, "unds_por_estiba": 90},
    {"nombre": "P.H. rayado 15", "clase": "PH", "largo_m": 0.40, "alto_m": 0.20,
     "espesor_m": 0.15, "junta_m": 0.015, "unds_por_estiba": 72},
]


# ── Cálculos elementales ────────────────────────────────────────────
def consumo_ratio(sub: pd.DataFrame) -> float:
    """Consumo de un conjunto de filas = Σ sacos ÷ Σ m² (NaN si no hay m²)."""
    m2 = sub["M2_ejecutados"].sum()
    sacos = sub["Num_sacos"].sum()
    return sacos / m2 if m2 > 0 else float("nan")


def cumple_meta(consumo: float, m2_total: float, sacos_total: float,
                meta: float = TEORICO_SAC_M2) -> bool:
    """True si el consumo está dentro de la meta y hay trabajo y material reales.

    `meta` permite usar el valor configurado en la app (ver config en Supabase).
    Por defecto usa la constante del negocio para no romper tests ni scripts.
    """
    return bool((consumo <= meta) and (m2_total > 0) and (sacos_total > 0))


def consumo_por(df: pd.DataFrame, dim: str) -> pd.DataFrame:
    """Tabla [dim, M2_total, Sacos_total, Consumo] usando ratio de sumas.

    Es la forma correcta de agregar el consumo por cualquier dimensión
    (oficial, piso, sector…): suma m² y sacos por grupo y divide al final.
    """
    g = (
        df.groupby(dim)
        .agg(M2_total=("M2_ejecutados", "sum"), Sacos_total=("Num_sacos", "sum"))
        .reset_index()
    )
    g["Consumo"] = g["Sacos_total"] / g["M2_total"].where(g["M2_total"] > 0)
    return g


# ── Bloques teóricos por tipo (P.V. / P.H.) ─────────────────────────
def bloques_por_m2(largo_bloque_m: float, alto_bloque_m: float,
                   junta_m: float = 0.01) -> float:
    """Bloques por m² de muro según el módulo (bloque + junta).

    Ej.: bloque 0.39×0.19 con junta 0.01 → módulo 0.40×0.20 → 12.5 und/m².
    """
    modulo = (largo_bloque_m + junta_m) * (alto_bloque_m + junta_m)
    return 1.0 / modulo if modulo > 0 else float("nan")


def hiladas_muro(alto_muro_m: float, alto_bloque_m: float,
                 junta_m: float = 0.01) -> int:
    """Número de hiladas = alto del muro ÷ módulo de altura, redondeo half-up.

    Se redondea a 6 decimales antes del half-up para que los residuos de punto
    flotante (0.19 + 0.01 ≠ 0.20 exacto) no bajen una hilada (2.50 m → 13, no 12).
    """
    modulo = alto_bloque_m + junta_m
    if modulo <= 0 or alto_muro_m <= 0:
        return 0
    return int(round(alto_muro_m / modulo, 6) + 0.5)


def bloques_teoricos_muro(largo_m: float, alto_m: float, num_dovelas: int,
                          bloque_pv: dict, bloque_ph: dict | None = None,
                          uso: str = "Auto") -> dict:
    """Bloques teóricos de un muro, repartidos entre P.V. y P.H.

    Regla (cada dovela = una columna completa de bloques P.V., piso a techo):
      - Sin bloque divisorio (`bloque_ph=None`) o `uso="P.V."` → 100 % P.V.
      - Con divisorio y 0 dovelas, o `uso="P.H."`              → 100 % P.H.
      - Con divisorio y dovelas > 0 (uso "Auto")               → mixto:
            P.V. = dovelas × hiladas (capado al total) y P.H. = resto.

    Devuelve {"hiladas": int, "total": float, "pv": float, "ph": float}.
    Es una estimación geométrica: medios bloques y trabas se cubren con el
    factor de ajuste en la conciliación, no aquí.
    """
    modo = str(uso or "Auto").upper().replace(".", "").strip()
    if largo_m <= 0 or alto_m <= 0 or not bloque_pv:
        return {"hiladas": 0, "total": 0.0, "pv": 0.0, "ph": 0.0}

    es_ph = modo == "PH" or (modo != "PV" and bloque_ph is not None and num_dovelas <= 0)
    es_pv = not es_ph and (modo == "PV" or bloque_ph is None)

    # El total se calcula con el bloque predominante del muro.
    bloque_base = bloque_ph if (es_ph or not es_pv) and bloque_ph else bloque_pv
    total = round(largo_m * alto_m * bloques_por_m2(
        bloque_base["largo_m"], bloque_base["alto_m"], bloque_base.get("junta_m", 0.015)
    ), 1)
    hiladas = hiladas_muro(alto_m, bloque_pv["alto_m"], bloque_pv.get("junta_m", 0.015))

    if es_ph:
        return {"hiladas": hiladas, "total": total, "pv": 0.0, "ph": total}
    if es_pv:
        return {"hiladas": hiladas, "total": total, "pv": total, "ph": 0.0}

    pv = min(float(max(num_dovelas, 0)) * hiladas, total)
    return {"hiladas": hiladas, "total": total, "pv": round(pv, 1),
            "ph": round(total - pv, 1)}


def desperdicio_pct(entregado: float, teorico: float) -> float:
    """(entregado − teórico) ÷ teórico. NaN si no hay teórico (no hay base)."""
    return (entregado - teorico) / teorico if teorico and teorico > 0 else float("nan")


def teorico_por_tipo(df: pd.DataFrame, dims=("Sector", "Piso")) -> pd.DataFrame:
    """Bloques teóricos acumulados en formato largo: [*dims, Tipo_bloque, Teorico].

    Une los P.V. (Tipo_ladrillo + Bloques_PV_teo) con los P.H.
    (Tipo_bloque_PH + Bloques_PH_teo). Las filas históricas sin teórico (NaN)
    simplemente no aportan.
    """
    dims = list(dims)
    cols_out = dims + ["Tipo_bloque", "Teorico"]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols_out)

    partes = []
    for col_tipo, col_teo in (("Tipo_ladrillo", "Bloques_PV_teo"),
                              ("Tipo_bloque_PH", "Bloques_PH_teo")):
        if col_tipo not in df.columns or col_teo not in df.columns:
            continue
        sub = df[dims + [col_tipo, col_teo]].copy()
        sub.columns = cols_out
        partes.append(sub)
    if not partes:
        return pd.DataFrame(columns=cols_out)

    largo = pd.concat(partes, ignore_index=True)
    largo["Teorico"] = pd.to_numeric(largo["Teorico"], errors="coerce")
    largo["Tipo_bloque"] = largo["Tipo_bloque"].fillna("").astype(str).str.strip()
    largo = largo[(largo["Teorico"] > 0) & (largo["Tipo_bloque"] != "")]
    if largo.empty:
        return pd.DataFrame(columns=cols_out)
    return largo.groupby(dims + ["Tipo_bloque"], as_index=False)["Teorico"].sum()


def conciliacion(df_reg: pd.DataFrame, df_sal: pd.DataFrame,
                 dims=("Sector", "Piso", "Tipo_bloque"),
                 factor_ajuste: float = FACTOR_AJUSTE_BLOQUES) -> pd.DataFrame:
    """Concilia teórico (registros de pega) vs entregado (salidas de almacén).

    `dims` debe incluir "Tipo_bloque". Outer merge: un tipo entregado sin
    teórico (o al revés) NO se pierde — esas filas son justo las alertas.
    Devuelve [*dims, Teorico, Teorico_ajustado, Entregado, Diferencia,
    Desperdicio_pct] (Desperdicio_pct en fracción: 0.10 = 10 %).
    """
    dims = list(dims)
    dims_geo = [d for d in dims if d != "Tipo_bloque"]
    cols_out = dims + ["Teorico", "Teorico_ajustado", "Entregado",
                       "Diferencia", "Desperdicio_pct"]

    teo = teorico_por_tipo(df_reg, dims=dims_geo)
    if df_sal is None or df_sal.empty:
        ent = pd.DataFrame(columns=dims + ["Entregado"])
    else:
        sal = df_sal.copy()
        sal["Cantidad"] = pd.to_numeric(sal["Cantidad"], errors="coerce")
        ent = (sal.groupby(dims, as_index=False)["Cantidad"].sum()
               .rename(columns={"Cantidad": "Entregado"}))

    if teo.empty and ent.empty:
        return pd.DataFrame(columns=cols_out)

    out = pd.merge(teo, ent, on=dims, how="outer")
    out["Teorico"] = pd.to_numeric(out.get("Teorico"), errors="coerce").fillna(0.0)
    out["Entregado"] = pd.to_numeric(out.get("Entregado"), errors="coerce").fillna(0.0)
    out["Teorico_ajustado"] = (out["Teorico"] * float(factor_ajuste)).round(1)
    out["Diferencia"] = (out["Entregado"] - out["Teorico_ajustado"]).round(1)
    out["Desperdicio_pct"] = out.apply(
        lambda r: desperdicio_pct(r["Entregado"], r["Teorico_ajustado"]), axis=1
    )
    return out[cols_out].sort_values(dims).reset_index(drop=True)


def validar_catalogo(crudo) -> list[dict]:
    """Limpia un catálogo crudo (JSON/editor): descarta entradas inválidas y
    completa con valores por defecto. Devuelve [] si nada es rescatable."""
    if not isinstance(crudo, list):
        return []

    def _num(item, clave, defecto, minimo_excl=0.0):
        try:
            v = float(item.get(clave, defecto))
        except (TypeError, ValueError):
            return defecto
        return v if pd.notna(v) and v > minimo_excl else defecto

    limpio = []
    for item in crudo:
        if not isinstance(item, dict):
            continue
        nombre = str(item.get("nombre", "") or "").strip()
        if not nombre or nombre.lower() == "nan":
            continue
        clase = str(item.get("clase", "PV") or "PV").upper().replace(".", "").strip()
        limpio.append({
            "nombre": nombre,
            "clase": clase if clase in ("PV", "PH") else "PV",
            "largo_m": _num(item, "largo_m", 0.40),
            "alto_m": _num(item, "alto_m", 0.20),
            "espesor_m": _num(item, "espesor_m", 0.12),
            "junta_m": _num(item, "junta_m", 0.015, minimo_excl=-1.0),  # 0 es válido (medidas modulares)
            "unds_por_estiba": _num(item, "unds_por_estiba", 1),
        })
    return limpio


# ── Construcción de filas a guardar ─────────────────────────────────
def construir_filas_grupo(base: dict, muros: list, sacos_total: float,
                          kg_por_saco: float = KG_POR_SACO,
                          meta: float = TEORICO_SAC_M2,
                          bloque_pv: dict | None = None,
                          bloque_ph: dict | None = None) -> pd.DataFrame:
    """
    Convierte un grupo de muros (que comparten `sacos_total`) en filas del esquema.
    - Una fila por muro (cada uno con su Largo/Alto/M²/dovelas).
    - El total de sacos va SOLO en la primera fila del grupo (las demás en 0),
      igual que la celda combinada en Excel: así Σ Num_sacos = total real.
    - Consumo y Cumple_meta se calculan a nivel de grupo (Σsacos ÷ Σm²) y se
      repiten en todas las filas del grupo.
    - `Grupo_id` enlaza las filas. Un muro solo = grupo de uno.
    - Con `bloque_pv` (dict del catálogo) se calculan y guardan los bloques
      teóricos por muro (`Bloques_PV_teo`/`Bloques_PH_teo`, snapshot). Cada
      muro puede traer la clave opcional `Uso` ("Auto"/"P.V."/"P.H.").
      Sin catálogo, esas columnas quedan vacías (comportamiento histórico).
    """
    grupo_id = uuid.uuid4().hex[:8]
    m2_total = sum(m["Largo_m"] * m["Alto_m"] for m in muros)
    consumo = round(sacos_total / m2_total, 4) if m2_total > 0 else 0.0
    cumple = cumple_meta(consumo, m2_total, sacos_total, meta=meta)

    filas = []
    for i, m in enumerate(muros):
        fila = {
            **base,
            "Largo_m": m["Largo_m"],
            "Alto_m": m["Alto_m"],
            "M2_ejecutados": round(m["Largo_m"] * m["Alto_m"], 2),
            "Num_sacos": float(sacos_total) if i == 0 else 0.0,
            "Consumo_real_sac_m2": consumo,
            "Consumo_mortero_kg": round(sacos_total * kg_por_saco, 2) if i == 0 else 0.0,
            "Num_dovelas": int(m.get("Num_dovelas", 0)),
            "ML_dovelas": round(m.get("Num_dovelas", 0) * m["Alto_m"], 2),
            "Cumple_meta": bool(cumple),
            "Grupo_id": grupo_id,
        }
        if bloque_pv:
            teo = bloques_teoricos_muro(
                m["Largo_m"], m["Alto_m"], int(m.get("Num_dovelas", 0)),
                bloque_pv, bloque_ph, uso=m.get("Uso", "Auto"),
            )
            fila["Bloques_PV_teo"] = teo["pv"]
            fila["Bloques_PH_teo"] = teo["ph"]
        filas.append(fila)

    df = pd.DataFrame(filas)
    # Columnas del esquema que no vinieron (p.ej. sin catálogo) quedan en NA.
    for col in COLUMNAS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[COLUMNAS]


# ── Agregados para tablas/resúmenes ─────────────────────────────────
def resumen_por(df: pd.DataFrame, dim: str = "Oficial", extra: dict | None = None) -> pd.DataFrame:
    """Resumen por dimensión con el consumo calculado como ratio de sumas.

    Devuelve columnas: [dim, Registros, M2_total, Sacos_total, Consumo_promedio,
    Pct_cumple, *extra] ordenado por M2_total desc. `extra` permite añadir
    agregados propios de cada pantalla (p.ej. Dias=("Fecha", ...)).
    """
    aggs = {
        "Registros": ("Oficial", "count"),
        "M2_total": ("M2_ejecutados", "sum"),
        "Sacos_total": ("Num_sacos", "sum"),
        "Pct_cumple": ("Cumple_meta", "mean"),
    }
    if extra:
        aggs.update(extra)
    g = df.groupby(dim).agg(**aggs).reset_index()
    # Consumo correcto = ratio de sumas (no promedio de la columna por fila).
    g["Consumo_promedio"] = g["Sacos_total"] / g["M2_total"].where(g["M2_total"] > 0)
    g["Pct_cumple"] = g["Pct_cumple"] * 100
    return g.sort_values("M2_total", ascending=False)
