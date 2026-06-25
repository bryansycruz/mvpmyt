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

# Espesores de junta de pega (cm) que ofrece la calculadora, igual que la hoja
# "Referencia" del Excel. La pega real de Serrania es 1.5 cm.
JUNTAS_CM = [1.0, 1.5, 1.8, 2.0, 2.4]

# Catálogo por defecto de bloques de Serrania (editable por admin en Supabase).
# clase: "PV" = perforación vertical (estructural, donde van las dovelas)
#        "PH" = perforación horizontal (divisorio).
# Medidas del BLOQUE SIN pega, en metros. `junta_m` es la pega real promedio de
# la obra (1.5 cm en Serrania; con 2.0 cm el admin la sube en el catálogo).
# bloque + junta = módulo: 0.415 × 0.215 → 11.2 und/m², hilada de 0.215 m.
# `unds_por_estiba` permite digitar las salidas de almacén en estibas.
# `meta_pedido` es el total pedido del proyecto/torre para ese tipo de bloque
# (0 = sin meta); sirve para ver el % del pedido recibido en 📦 Movimientos de almacén.
#
# Los tipos y sus números (unds_por_estiba, meta_pedido) salen del control real
# de entrada (Excel "Control ladrillo", una hoja por tipo). El admin los edita en
# el catálogo. Catalán moreno: cara de pega 0.30 (largo) × 0.10 (alto), espesor 0.15;
# así rinde 27.6 und/m² con junta 1.5 cm (confirmado). Viene en P.V. y P.H.; medidas
# de Bloque 20: POR CONFIRMAR (solo afectan el teórico de la
# conciliación si se usan en muros).
CATALOGO_BLOQUES_DEFECTO = [
    # P.V. = perforación vertical (estructural, donde van las dovelas).
    {"nombre": "P.V. rayado 12", "clase": "PV", "largo_m": 0.40, "alto_m": 0.20,
     "espesor_m": 0.12, "junta_m": 0.015, "unds_por_estiba": 135, "meta_pedido": 183614},
    {"nombre": "P.V. rayado 15", "clase": "PV", "largo_m": 0.40, "alto_m": 0.20,
     "espesor_m": 0.15, "junta_m": 0.015, "unds_por_estiba": 118, "meta_pedido": 113822},
    {"nombre": "P.V. liso 12", "clase": "PV", "largo_m": 0.40, "alto_m": 0.20,
     "espesor_m": 0.12, "junta_m": 0.015, "unds_por_estiba": 108, "meta_pedido": 13814},
    {"nombre": "P.V. liso 15", "clase": "PV", "largo_m": 0.40, "alto_m": 0.20,
     "espesor_m": 0.15, "junta_m": 0.015, "unds_por_estiba": 96, "meta_pedido": 30784},
    {"nombre": "Catalán moreno", "clase": "PV", "largo_m": 0.30, "alto_m": 0.10,
     "espesor_m": 0.15, "junta_m": 0.015, "unds_por_estiba": 280, "meta_pedido": 63045},
    {"nombre": "Bloque 20", "clase": "PV", "largo_m": 0.40, "alto_m": 0.20,
     "espesor_m": 0.20, "junta_m": 0.015, "unds_por_estiba": 1, "meta_pedido": 6833},
    # P.H. = perforación horizontal (divisorio).
    {"nombre": "P.H. rayado 12", "clase": "PH", "largo_m": 0.40, "alto_m": 0.20,
     "espesor_m": 0.12, "junta_m": 0.015, "unds_por_estiba": 135, "meta_pedido": 0},
    {"nombre": "P.H. rayado 15", "clase": "PH", "largo_m": 0.40, "alto_m": 0.20,
     "espesor_m": 0.15, "junta_m": 0.015, "unds_por_estiba": 125, "meta_pedido": 0},
    # Mismo bloque Catalán que el P.V. de arriba, pero con perforación horizontal
    # (uso divisorio). Mismas medidas; meta_pedido va en la variante P.V.
    {"nombre": "Catalán moreno P.H.", "clase": "PH", "largo_m": 0.30, "alto_m": 0.10,
     "espesor_m": 0.15, "junta_m": 0.015, "unds_por_estiba": 280, "meta_pedido": 0},
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
                          bloque_pv: dict | None, bloque_ph: dict | None = None,
                          uso: str = "Auto") -> dict:
    """Bloques teóricos de un muro, repartidos entre P.V. y P.H.

    Regla (cada dovela = una columna completa de bloques P.V., piso a techo):
      - Sin bloque divisorio (`bloque_ph=None`) o `uso="P.V."` → 100 % P.V.
      - Con divisorio y 0 dovelas, o `uso="P.H."`              → 100 % P.H.
      - Con divisorio y dovelas > 0 (uso "Auto")               → mixto:
            P.V. = dovelas × hiladas (capado al total) y P.H. = resto.

    Acepta muro 100 % P.H. sin bloque P.V. (`bloque_pv=None`, `bloque_ph` dado y
    `uso="P.H."`): usa el P.H. como base de hiladas/total y el teórico cae todo en
    la columna P.H.

    Devuelve {"hiladas": int, "total": float, "pv": float, "ph": float}.
    Es una estimación geométrica: medios bloques y trabas se cubren con el
    factor de ajuste en la conciliación, no aquí.
    """
    modo = str(uso or "Auto").upper().replace(".", "").strip()
    if largo_m <= 0 or alto_m <= 0 or (not bloque_pv and not bloque_ph):
        return {"hiladas": 0, "total": 0.0, "pv": 0.0, "ph": 0.0}

    # Sin bloque P.V. el muro solo puede ser P.H. (relleno).
    if not bloque_pv:
        modo = "PH"

    es_ph = modo == "PH" or (modo != "PV" and bloque_ph is not None and num_dovelas <= 0)
    es_pv = not es_ph and (modo == "PV" or bloque_ph is None)

    # El total se calcula con el bloque predominante del muro.
    bloque_base = bloque_ph if (es_ph or not es_pv) and bloque_ph else bloque_pv
    # Para las hiladas usa el P.V.; si no hay (muro solo P.H.), usa el P.H.
    bloque_hilada = bloque_pv or bloque_ph
    total = round(largo_m * alto_m * bloques_por_m2(
        bloque_base["largo_m"], bloque_base["alto_m"], bloque_base.get("junta_m", 0.015)
    ), 1)
    hiladas = hiladas_muro(alto_m, bloque_hilada["alto_m"], bloque_hilada.get("junta_m", 0.015))

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


# ── Calculadora de muro (réplica del Excel) ─────────────────────────
def _semaforo_entrega(entregado: float, lim_verde: float, lim_rojo: float) -> str:
    """VERDE/NARANJA/ROJO según los dos límites del umbral (×1 y ×1.5)."""
    if entregado <= lim_verde:
        return "VERDE"
    if entregado <= lim_rojo:
        return "NARANJA"
    return "ROJO"


def calculadora_muro(largo_m: float, alto_m: float, bloque: dict,
                     junta_cm: float = 1.5, factor: float = 1.05,
                     umbral_pct: float = UMBRAL_DESPERDICIO_PCT,
                     entregado: float | None = None) -> dict:
    """Calculadora de desperdicio de un muro de un solo tipo de bloque.

    Réplica de la hoja "Calculadora de muro": módulo, bloques/m², área, teórico
    geométrico y ajustado, límites del semáforo y desperdicio %.

    Si `entregado` es None, las claves de desperdicio/semáforo quedan en NaN/"".
    """
    if not bloque or largo_m <= 0 or alto_m <= 0:
        return {}
    junta_m = float(junta_cm) / 100.0
    largo_b = float(bloque["largo_m"])
    alto_b = float(bloque["alto_m"])
    modulo = (largo_b + junta_m) * (alto_b + junta_m)
    bloques_m2 = 1.0 / modulo if modulo > 0 else float("nan")
    area = largo_m * alto_m
    teorico_geom = area * bloques_m2
    teorico_ajustado = teorico_geom * float(factor)
    lim_verde = teorico_ajustado * (1 + umbral_pct / 100.0)
    lim_rojo = teorico_ajustado * (1 + 1.5 * umbral_pct / 100.0)

    res = {
        "modulo": modulo,
        "bloques_m2": bloques_m2,
        "area": area,
        "teorico_geom": teorico_geom,
        "teorico_ajustado": teorico_ajustado,
        "lim_verde": lim_verde,
        "lim_rojo": lim_rojo,
        "desp_pct_ajustado": float("nan"),
        "desp_pct_geom": float("nan"),
        "semaforo": "",
    }
    if entregado is not None:
        ent = float(entregado)
        res["desp_pct_ajustado"] = desperdicio_pct(ent, teorico_ajustado)
        res["desp_pct_geom"] = desperdicio_pct(ent, teorico_geom)
        res["semaforo"] = _semaforo_entrega(ent, lim_verde, lim_rojo)
    return res


def calculadora_combinado(largo_m: float, alto_m: float, uso: str,
                          num_dovelas: int, bloque_pv: dict | None,
                          bloque_ph: dict | None = None, junta_cm: float = 1.5,
                          factor: float = 1.05,
                          umbral_pct: float = UMBRAL_DESPERDICIO_PCT,
                          entregado_pv: float | None = None,
                          entregado_ph: float | None = None) -> dict:
    """Control de un muro combinado P.V. (dovelas) + P.H. (relleno).

    Réplica de la hoja "Muro combinado": el total se calcula con el bloque P.V.
    (`MIN(dovelas×hiladas, total)` para el P.V., resto para el P.H.). `uso` acepta
    "Combinado"/"Vertical"/"Horizontal" (o "Auto"/"P.V."/"P.H."). Devuelve el
    teórico y, si hay entregado, el desperdicio % y el semáforo por cada tipo.
    """
    if largo_m <= 0 or alto_m <= 0 or (not bloque_pv and not bloque_ph):
        return {}
    modo = str(uso or "").upper()
    es_vertical = "VERTIC" in modo or modo in ("P.V.", "PV")
    es_horizontal = "HORIZ" in modo or modo in ("P.H.", "PH")

    junta_m = float(junta_cm) / 100.0
    area = largo_m * alto_m
    # Bloque base de la geometría: el P.V. manda; si solo hay P.H., el P.H.
    base = bloque_pv or bloque_ph
    bloques_m2 = bloques_por_m2(base["largo_m"], base["alto_m"], junta_m)
    hiladas = hiladas_muro(alto_m, base["alto_m"], junta_m)
    total = area * bloques_m2

    if es_vertical or not bloque_ph:
        pv_teo, ph_teo = total, 0.0
    elif es_horizontal or not bloque_pv:
        pv_teo, ph_teo = 0.0, total
    else:  # combinado
        pv_teo = min(float(max(num_dovelas, 0)) * hiladas, total)
        ph_teo = total - pv_teo

    def _lado(teo: float, entregado: float | None) -> dict:
        ajustado = teo * float(factor)
        lim_v = ajustado * (1 + umbral_pct / 100.0)
        lim_r = ajustado * (1 + 1.5 * umbral_pct / 100.0)
        d = {"teorico": teo, "ajustado": ajustado, "lim_verde": lim_v,
             "lim_rojo": lim_r, "desp_pct": float("nan"), "semaforo": ""}
        if entregado is not None:
            ent = float(entregado)
            d["desp_pct"] = desperdicio_pct(ent, ajustado)
            d["semaforo"] = _semaforo_entrega(ent, lim_v, lim_r) if ajustado > 0 else ""
        return d

    return {
        "area": area, "bloques_m2": bloques_m2, "hiladas": hiladas, "total": total,
        "pv": _lado(round(pv_teo, 1), entregado_pv),
        "ph": _lado(round(ph_teo, 1), entregado_ph),
    }


def resumen_pedido_por_tipo(df_reg: pd.DataFrame, catalogo: list,
                            apto: str | None = None, factor: float = 1.0,
                            umbral_pct: float = 0.0,
                            dim_apto: str = "Zona") -> dict:
    """Resumen de bloques teóricos por tipo, para el pedido (réplica de la hoja
    "Total por tipo" → RESUMEN POR TIPO).

    Agrupa el teórico ya guardado (`Bloques_PV_teo`/`Bloques_PH_teo`) por
    `dim_apto` (por defecto la Zona/apto) y por tipo. Por cada tipo del catálogo:
      - Total_apto  : teórico del apto seleccionado (filtro).
      - Total_obra  : teórico de TODA la obra.
      - Con_factor  : Total_obra × factor (cortes/medios bloques).
      - A_pedir     : ceil(Con_factor × (1 + umbral/100)), redondeo hacia arriba.
    Devuelve {"por_tipo": DataFrame, "totales": {pv/ph/general: {...}}}.
    `Con_factor`/`A_pedir` se calculan sobre Total_obra (pedido de toda la obra),
    igual que el Excel.
    """
    import math

    cols = ["Tipo_bloque", "Clase", "Total_apto", "Total_obra",
            "Con_factor", "A_pedir"]
    catalogo = catalogo or []
    clase_de = {b.get("nombre"): b.get("clase", "PV") for b in catalogo}

    teo = teorico_por_tipo(df_reg, dims=[dim_apto]) if df_reg is not None else None
    if teo is None or teo.empty:
        obra = pd.Series(dtype=float)
        apto_s = pd.Series(dtype=float)
    else:
        obra = teo.groupby("Tipo_bloque")["Teorico"].sum()
        if apto:
            apto_s = (teo[teo[dim_apto] == apto]
                      .groupby("Tipo_bloque")["Teorico"].sum())
        else:
            apto_s = pd.Series(dtype=float)

    def _a_pedir(con_factor: float) -> int:
        return int(math.ceil(con_factor * (1 + umbral_pct / 100.0)))

    filas = []
    for b in catalogo:
        nombre = b.get("nombre")
        t_obra = float(obra.get(nombre, 0.0))
        t_apto = float(apto_s.get(nombre, 0.0))
        con_f = t_obra * float(factor)
        filas.append({
            "Tipo_bloque": nombre, "Clase": b.get("clase", "PV"),
            "Total_apto": round(t_apto, 1), "Total_obra": round(t_obra, 1),
            "Con_factor": round(con_f, 1), "A_pedir": _a_pedir(con_f),
        })
    por_tipo = pd.DataFrame(filas, columns=cols)

    totales = {}
    for clave, clase in (("pv", "PV"), ("ph", "PH"), ("general", None)):
        sub = por_tipo if clase is None else por_tipo[por_tipo["Clase"] == clase]
        t_obra = float(sub["Total_obra"].sum())
        con_f = t_obra * float(factor)
        totales[clave] = {
            "Total_apto": round(float(sub["Total_apto"].sum()), 1),
            "Total_obra": round(t_obra, 1),
            "Con_factor": round(con_f, 1),
            "A_pedir": _a_pedir(con_f),
        }
    return {"por_tipo": por_tipo, "totales": totales}


def rendimiento_por_junta(catalogo: list, juntas_cm: list | None = None) -> pd.DataFrame:
    """Tabla de rendimiento (und/m² sin desperdicio) por bloque y espesor de junta.

    Réplica de la hoja "Referencia": para cada bloque del catálogo calcula los
    bloques/m² a cada junta de `juntas_cm` (por defecto `JUNTAS_CM`).
    """
    juntas = juntas_cm if juntas_cm is not None else JUNTAS_CM
    cols = ["Bloque"] + [f"{j:g} cm" for j in juntas]
    filas = []
    for b in (catalogo or []):
        fila = {"Bloque": b.get("nombre")}
        for j in juntas:
            fila[f"{j:g} cm"] = round(
                bloques_por_m2(b["largo_m"], b["alto_m"], float(j) / 100.0), 1
            )
        filas.append(fila)
    return pd.DataFrame(filas, columns=cols)


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
            "meta_pedido": _num(item, "meta_pedido", 0, minimo_excl=-1.0),  # 0 = sin meta
        })
    return limpio


# ── Construcción de filas a guardar ─────────────────────────────────
def repartir_por_m2(total: float, m2s: list) -> list:
    """Reparte `total` (p.ej. sacos) entre muros en proporción a sus m².

    El ÚLTIMO muro recibe el remanente para que la suma sea EXACTAMENTE `total`
    (evita el centavo perdido por redondeo). Si la suma de m² es 0, todo 0.
    """
    suma = sum(m2s)
    if suma <= 0 or not m2s:
        return [0.0 for _ in m2s]
    out, acum = [], 0.0
    for i, a in enumerate(m2s):
        if i < len(m2s) - 1:
            v = round(total * a / suma, 2)
            out.append(v)
            acum += v
        else:
            out.append(round(total - acum, 2))
    return out


def construir_filas_grupo(base: dict, muros: list, sacos_total: float,
                          kg_por_saco: float = KG_POR_SACO,
                          meta: float = TEORICO_SAC_M2,
                          bloque_pv: dict | None = None,
                          bloque_ph: dict | None = None) -> pd.DataFrame:
    """
    Convierte un grupo de muros (que comparten `sacos_total`) en filas del esquema.
    - Una fila por muro (cada uno con su Largo/Alto/M²/dovelas y, opcionalmente,
      su propio uso y bloques P.V./P.H.).
    - Los sacos del grupo se REPARTEN entre los muros en proporción a sus m²
      (`repartir_por_m2`): así cada muro guarda su porción de sacos y de mortero,
      y Σ Num_sacos = total real. El consumo (Σsacos ÷ Σm²) es del grupo y se
      repite en todas las filas (todas dan el mismo sac/m², el promedio real).
    - `Cumple_meta` también es del grupo. `Grupo_id` enlaza las filas; un muro
      solo = grupo de uno.
    - Bloques teóricos por muro (`Bloques_PV_teo`/`Bloques_PH_teo`, snapshot):
      cada muro puede traer sus propios `bloque_pv`/`bloque_ph` (dicts del
      catálogo), `tipo_pv`/`tipo_ph` (nombres) y `Uso` ("Auto"/"P.V."/"P.H.").
      Si no, usa los del grupo (`bloque_pv`/`bloque_ph`) y los
      `Tipo_ladrillo`/`Tipo_bloque_PH` de `base`. Sin bloque, esas columnas
      quedan vacías (comportamiento histórico).
    """
    grupo_id = uuid.uuid4().hex[:8]
    m2s = [m["Largo_m"] * m["Alto_m"] for m in muros]
    m2_total = sum(m2s)
    consumo = round(sacos_total / m2_total, 4) if m2_total > 0 else 0.0
    cumple = cumple_meta(consumo, m2_total, sacos_total, meta=meta)
    sacos_muro = repartir_por_m2(float(sacos_total), m2s)

    filas = []
    for i, m in enumerate(muros):
        b_pv = m.get("bloque_pv", bloque_pv)
        b_ph = m.get("bloque_ph", bloque_ph)
        s_i = sacos_muro[i]
        fila = {
            **base,
            "Largo_m": m["Largo_m"],
            "Alto_m": m["Alto_m"],
            "M2_ejecutados": round(m2s[i], 2),
            "Num_sacos": s_i,
            "Consumo_real_sac_m2": consumo,
            "Consumo_mortero_kg": round(s_i * kg_por_saco, 2),
            "Num_dovelas": int(m.get("Num_dovelas", 0)),
            "ML_dovelas": round(m.get("Num_dovelas", 0) * m["Alto_m"], 2),
            "Cumple_meta": bool(cumple),
            "Grupo_id": grupo_id,
        }
        # Tipo por muro (si el muro lo trae); si no, se queda el de `base`.
        if "tipo_pv" in m:
            fila["Tipo_ladrillo"] = m["tipo_pv"]
        if "tipo_ph" in m:
            fila["Tipo_bloque_PH"] = m["tipo_ph"]
        if b_pv or b_ph:
            teo = bloques_teoricos_muro(
                m["Largo_m"], m["Alto_m"], int(m.get("Num_dovelas", 0)),
                b_pv, b_ph, uso=m.get("Uso", "Auto"),
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
