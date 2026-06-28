"""
test_calculos.py — Pruebas de la lógica de negocio (sin dependencias extra)
───────────────────────────────────────────────────────────────────────────
Ejecutar:  python test_calculos.py
No necesita pytest: usa asserts y sale con código ≠ 0 si algo falla.
Sirve como red de seguridad para no re-introducir el bug del consumo
(ratio de sumas vs. promedio por fila).
"""

import math

import pandas as pd

from calculos import (
    consumo_ratio, consumo_por, resumen_por, construir_filas_grupo,
    cumple_meta, TEORICO_SAC_M2,
    bloques_por_m2, hiladas_muro, bloques_teoricos_muro,
    desperdicio_pct, teorico_por_tipo, conciliacion, validar_catalogo,
    calculadora_muro, calculadora_combinado, resumen_pedido_por_tipo,
    rendimiento_por_junta, JUNTAS_CM,
)
from data_schema import normalizar

BASE = {
    "Fecha": pd.Timestamp("2026-06-02"), "Oficial": "Juan", "Ayudante": "",
    "Sector": "Torre", "Piso": "5", "Zona": "EJE 1", "Tipo_ladrillo": "LISO",
    "Observaciones": "", "Timestamp_registro": pd.Timestamp("2026-06-02 08:00"),
}

# Bloques de prueba (módulo 0.40×0.20 → 12.5 und/m², como los rayados de la obra).
BPV = {"nombre": "P.V. rayado 12", "clase": "PV", "largo_m": 0.39, "alto_m": 0.19,
       "espesor_m": 0.12, "junta_m": 0.01, "unds_por_estiba": 90}
BPH = {"nombre": "P.H. rayado 12", "clase": "PH", "largo_m": 0.39, "alto_m": 0.19,
       "espesor_m": 0.12, "junta_m": 0.01, "unds_por_estiba": 90}


def test_consumo_ratio_es_suma_sobre_suma():
    df = pd.DataFrame({"M2_ejecutados": [10, 90], "Num_sacos": [10, 9]})
    assert math.isclose(consumo_ratio(df), 19 / 100)   # 0.19, NO el promedio 0.55


def test_consumo_ratio_sin_m2_es_nan():
    df = pd.DataFrame({"M2_ejecutados": [0], "Num_sacos": [5]})
    assert math.isnan(consumo_ratio(df))


def test_cumple_meta_limites():
    assert cumple_meta(TEORICO_SAC_M2, 10, 8) is True       # justo en la meta
    assert cumple_meta(TEORICO_SAC_M2 + 0.01, 10, 8) is False
    assert cumple_meta(0.5, 0, 8) is False                  # sin m²
    assert cumple_meta(0.5, 10, 0) is False                 # sin sacos


def test_construir_filas_grupo_total_sacos_una_vez():
    muros = [
        {"Largo_m": 2.0, "Alto_m": 2.5, "Num_dovelas": 3},   # 5 m²
        {"Largo_m": 4.0, "Alto_m": 2.5, "Num_dovelas": 0},   # 10 m²
    ]
    out = construir_filas_grupo(BASE, muros, sacos_total=9.0)
    assert len(out) == 2
    assert math.isclose(out["Num_sacos"].sum(), 9.0)          # total NO se multiplica
    assert math.isclose(out["M2_ejecutados"].sum(), 15.0)
    # consumo del grupo = 9 / 15 = 0.6, repetido en ambas filas
    assert (abs(out["Consumo_real_sac_m2"] - 0.6) < 1e-9).all()
    assert math.isclose(out.iloc[0]["ML_dovelas"], 3 * 2.5)   # dovelas × alto
    assert out["Grupo_id"].nunique() == 1                     # mismas filas, un grupo


def test_resumen_por_usa_ratio_no_promedio():
    # Dos grupos del mismo oficial: el promedio por fila daría 0.55, el ratio 0.19.
    df = pd.DataFrame({
        "Oficial": ["A", "A"],
        "Fecha": [pd.Timestamp("2026-06-01"), pd.Timestamp("2026-06-02")],
        "M2_ejecutados": [10.0, 90.0],
        "Num_sacos": [10.0, 9.0],
        "Consumo_real_sac_m2": [1.0, 0.1],
        "Cumple_meta": [False, True],
    })
    r = resumen_por(df, "Oficial")
    fila = r.iloc[0]
    assert math.isclose(fila["Consumo_promedio"], 0.19)
    assert math.isclose(fila["M2_total"], 100.0)
    assert math.isclose(fila["Pct_cumple"], 50.0)
    assert fila["Registros"] == 2


def test_consumo_por_ordena_y_calcula():
    df = pd.DataFrame({
        "Oficial": ["A", "A", "B"],
        "M2_ejecutados": [10.0, 90.0, 50.0],
        "Num_sacos": [10.0, 9.0, 5.0],
    })
    t = consumo_por(df, "Oficial").set_index("Oficial")
    assert math.isclose(t.loc["A", "Consumo"], 0.19)
    assert math.isclose(t.loc["B", "Consumo"], 0.10)


def test_bloques_por_m2():
    # módulo 0.40×0.20 → 12.5 bloques por m²
    assert math.isclose(bloques_por_m2(0.39, 0.19, 0.01), 12.5)


def test_hiladas_redondeo_half_up():
    assert hiladas_muro(2.40, 0.19, 0.01) == 12
    assert hiladas_muro(2.42, 0.19, 0.01) == 12
    # 2.50/0.20 = 12.5 → 13 (half-up; round() bancario de Python daría 12)
    assert hiladas_muro(2.50, 0.19, 0.01) == 13
    assert hiladas_muro(0.0, 0.19, 0.01) == 0


def test_bloques_teoricos_mixto():
    # muro 3.0×2.4 con 2 dovelas y divisorio: 90 en total, 24 P.V. + 66 P.H.
    teo = bloques_teoricos_muro(3.0, 2.4, 2, BPV, BPH)
    assert teo["hiladas"] == 12
    assert math.isclose(teo["total"], 90.0)
    assert math.isclose(teo["pv"], 24.0)
    assert math.isclose(teo["ph"], 66.0)


def test_bloques_teoricos_100_pv():
    # sin bloque divisorio elegido → todo el muro es P.V.
    teo = bloques_teoricos_muro(3.0, 2.4, 2, BPV, None)
    assert math.isclose(teo["pv"], 90.0) and teo["ph"] == 0.0


def test_bloques_teoricos_100_ph():
    # divisorio elegido y 0 dovelas → todo el muro es P.H.
    teo = bloques_teoricos_muro(3.0, 2.4, 0, BPV, BPH)
    assert teo["pv"] == 0.0 and math.isclose(teo["ph"], 90.0)


def test_bloques_teoricos_uso_forzado():
    # `Uso` por muro le gana a la regla automática
    teo = bloques_teoricos_muro(3.0, 2.4, 2, BPV, BPH, uso="P.H.")
    assert teo["pv"] == 0.0 and math.isclose(teo["ph"], 90.0)
    teo = bloques_teoricos_muro(3.0, 2.4, 0, BPV, BPH, uso="P.V.")
    assert math.isclose(teo["pv"], 90.0) and teo["ph"] == 0.0


def test_bloques_pv_capado_al_total():
    # muro corto con muchas dovelas: P.V. no puede superar el total del muro
    teo = bloques_teoricos_muro(0.4, 2.4, 5, BPV, BPH)
    assert teo["pv"] <= teo["total"]
    assert teo["ph"] >= 0.0


def test_bloques_teoricos_relleno_mixto_50_50():
    # muro SIN dovelas completado con dos tipologías → 90 total, 45 + 45
    teo = bloques_teoricos_muro(3.0, 2.4, 0, BPV, BPH, uso="Mixto")
    assert math.isclose(teo["total"], 90.0)
    assert math.isclose(teo["pv"], 45.0)
    assert math.isclose(teo["ph"], 45.0)


def test_bloques_teoricos_mixto_un_solo_bloque_cae_a_100():
    # mixto pero con un solo bloque elegido → 100 % de ese tipo (no se pierde)
    teo = bloques_teoricos_muro(3.0, 2.4, 0, BPV, None, uso="Mixto")
    assert math.isclose(teo["pv"], 90.0) and teo["ph"] == 0.0


def test_desperdicio_pct():
    assert math.isclose(desperdicio_pct(110, 100), 0.10)
    assert math.isclose(desperdicio_pct(90, 100), -0.10)   # ahorro también se ve
    assert math.isnan(desperdicio_pct(50, 0))


def _df_registros_teorico():
    return pd.DataFrame({
        "Sector": ["Torre", "Torre", "Torre"],
        "Piso": ["5", "5", "6"],
        "Tipo_ladrillo": ["P.V. rayado 12"] * 3,
        "Tipo_bloque_PH": ["P.H. rayado 12", "P.H. rayado 12", None],
        "Bloques_PV_teo": [24.0, 12.0, 50.0],
        "Bloques_PH_teo": [66.0, 30.0, None],   # fila histórica sin teórico P.H.
    })


def test_teorico_por_tipo_formato_largo():
    t = teorico_por_tipo(_df_registros_teorico()).set_index(["Sector", "Piso", "Tipo_bloque"])
    assert math.isclose(t.loc[("Torre", "5", "P.V. rayado 12"), "Teorico"], 36.0)
    assert math.isclose(t.loc[("Torre", "5", "P.H. rayado 12"), "Teorico"], 96.0)
    assert math.isclose(t.loc[("Torre", "6", "P.V. rayado 12"), "Teorico"], 50.0)
    assert ("Torre", "6", "P.H. rayado 12") not in t.index   # NaN no aporta


def test_conciliacion_outer_merge_y_factor():
    df_sal = pd.DataFrame({
        "Sector": ["Torre", "Torre"],
        "Piso": ["5", "5"],
        "Tipo_bloque": ["P.V. rayado 12", "P.H. rayado 15"],   # el 15 NO tiene teórico
        "Cantidad": [40.0, 100.0],
    })
    out = conciliacion(_df_registros_teorico(), df_sal, factor_ajuste=1.0)
    out = out.set_index(["Sector", "Piso", "Tipo_bloque"])
    # entregado sin teórico no se pierde (es la alerta más importante)
    assert math.isclose(out.loc[("Torre", "5", "P.H. rayado 15"), "Entregado"], 100.0)
    # teórico sin entregado tampoco
    assert math.isclose(out.loc[("Torre", "6", "P.V. rayado 12"), "Teorico"], 50.0)
    fila = out.loc[("Torre", "5", "P.V. rayado 12")]
    assert math.isclose(fila["Desperdicio_pct"], (40 - 36) / 36)
    # con factor de ajuste el teórico crece y el desperdicio baja
    out2 = conciliacion(_df_registros_teorico(), df_sal, factor_ajuste=1.05)
    fila2 = out2.set_index(["Sector", "Piso", "Tipo_bloque"]).loc[("Torre", "5", "P.V. rayado 12")]
    assert math.isclose(fila2["Teorico_ajustado"], round(36 * 1.05, 1))


def test_construir_filas_grupo_retrocompatible_sin_catalogo():
    muros = [{"Largo_m": 3.0, "Alto_m": 2.4, "Num_dovelas": 2}]
    out = construir_filas_grupo(BASE, muros, sacos_total=5.0)   # sin bloque_pv
    assert out["Bloques_PV_teo"].isna().all()
    assert out["Bloques_PH_teo"].isna().all()


def test_construir_filas_grupo_con_catalogo():
    base = {**BASE, "Tipo_bloque_PH": "P.H. rayado 12"}
    muros = [{"Largo_m": 3.0, "Alto_m": 2.4, "Num_dovelas": 2, "Uso": "Auto"}]
    out = construir_filas_grupo(base, muros, sacos_total=5.0,
                                bloque_pv=BPV, bloque_ph=BPH)
    assert math.isclose(out.iloc[0]["Bloques_PV_teo"], 24.0)
    assert math.isclose(out.iloc[0]["Bloques_PH_teo"], 66.0)
    assert out.iloc[0]["Tipo_bloque_PH"] == "P.H. rayado 12"


def test_relleno_pv_se_acredita_por_nombre():
    # El relleno puede ser un bloque P.V. (V12/V15): la conciliación agrupa por
    # NOMBRE de tipo, sin importar la clase. Un muro con dovelas P.V.12 y relleno
    # P.V.15 debe acreditar el relleno al tipo "P.V. rayado 15".
    muros = [{
        "Largo_m": 3.0, "Alto_m": 2.4, "Num_dovelas": 2, "Uso": "Auto",
        "tipo_pv": "P.V. rayado 12", "tipo_ph": "P.V. rayado 15",
        "bloque_pv": BPV, "bloque_ph": {**BPH, "nombre": "P.V. rayado 15", "clase": "PV"},
    }]
    fila = construir_filas_grupo(BASE, muros, sacos_total=5.0).iloc[0]
    assert math.isclose(fila["Bloques_PV_teo"], 24.0)
    assert math.isclose(fila["Bloques_PH_teo"], 66.0)
    assert fila["Tipo_bloque_PH"] == "P.V. rayado 15"


def test_construir_filas_grupo_relleno_mixto():
    # Muro sin dovelas con V12 + V15 al 50/50: cada tipo se guarda en un slot y
    # se cuenta por separado (sin columnas nuevas).
    muros = [{
        "Largo_m": 3.0, "Alto_m": 2.4, "Num_dovelas": 0, "Uso": "Mixto",
        "tipo_pv": "V12", "tipo_ph": "V15",
        "bloque_pv": {**BPV, "nombre": "V12"}, "bloque_ph": {**BPH, "nombre": "V15"},
    }]
    fila = construir_filas_grupo(BASE, muros, sacos_total=5.0).iloc[0]
    assert math.isclose(fila["Bloques_PV_teo"], 45.0)
    assert math.isclose(fila["Bloques_PH_teo"], 45.0)
    assert fila["Tipo_ladrillo"] == "V12"
    assert fila["Tipo_bloque_PH"] == "V15"


def test_normalizar_historico_sin_columnas_nuevas():
    viejo = pd.DataFrame([{
        "Fecha": "2026-01-15", "Oficial": "Juan", "Sector": "Torre", "Piso": "2",
        "Largo_m": 2.0, "Alto_m": 2.4, "M2_ejecutados": 4.8, "Num_sacos": 4.0,
        "Cumple_meta": "TRUE",
    }])
    out = normalizar(viejo)
    assert "Bloques_PV_teo" in out.columns and "Tipo_bloque_PH" in out.columns
    assert pd.isna(out.iloc[0]["Bloques_PV_teo"])
    assert out.iloc[0]["Cumple_meta"] is True or out.iloc[0]["Cumple_meta"] == True  # noqa: E712


# Bloques reales (medidas SIN pega) para los números del Excel.
CAT_R12_PV = {"nombre": "P.V. rayado 12", "clase": "PV", "largo_m": 0.40,
              "alto_m": 0.20, "espesor_m": 0.12, "junta_m": 0.015}
CAT_R12_PH = {"nombre": "P.H. rayado 12", "clase": "PH", "largo_m": 0.40,
              "alto_m": 0.20, "espesor_m": 0.12, "junta_m": 0.015}


def test_calculadora_muro_numeros_del_excel():
    # Hoja "Calculadora de muro": 4.5×2.4, P.V. rayado 12, junta 1.5, factor 1.05,
    # umbral 7, entregado 130. Módulo 0.089225, teórico ≈ 121, ajustado ≈ 127.1.
    r = calculadora_muro(4.5, 2.4, CAT_R12_PV, junta_cm=1.5, factor=1.05,
                         umbral_pct=7, entregado=130)
    assert math.isclose(r["modulo"], 0.089225, rel_tol=1e-6)
    assert math.isclose(r["bloques_m2"], 11.2076, rel_tol=1e-4)
    assert math.isclose(r["area"], 10.8)
    assert math.isclose(r["teorico_geom"], 121.04, rel_tol=1e-3)
    assert math.isclose(r["teorico_ajustado"], 127.09, rel_tol=1e-3)
    assert math.isclose(r["lim_verde"], 135.99, rel_tol=1e-3)
    assert math.isclose(r["lim_rojo"], 140.43, rel_tol=1e-3)
    assert math.isclose(r["desp_pct_ajustado"], (130 - 127.09) / 127.09, rel_tol=1e-2)
    assert math.isclose(r["desp_pct_geom"], (130 - 121.04) / 121.04, rel_tol=1e-2)
    assert r["semaforo"] == "VERDE"   # 130 <= 136


def test_calculadora_muro_semaforo_rojo_y_sin_entregado():
    rojo = calculadora_muro(4.5, 2.4, CAT_R12_PV, junta_cm=1.5, factor=1.05,
                            umbral_pct=7, entregado=200)
    assert rojo["semaforo"] == "ROJO"
    # sin entregado: geometría sí, desperdicio/semáforo vacíos
    sin = calculadora_muro(4.5, 2.4, CAT_R12_PV, junta_cm=1.5)
    assert math.isnan(sin["desp_pct_ajustado"]) and sin["semaforo"] == ""


def test_calculadora_combinado_split_pv_ph():
    # Hoja "Muro combinado": 4×2.4, combinado, 3 dovelas, rayado 12 P.V./P.H.,
    # junta 1.5. hiladas 11 → P.V. = min(3×11, total) = 33; P.H. = resto.
    r = calculadora_combinado(4.0, 2.4, "Combinado (P.V.+P.H.)", 3, CAT_R12_PV,
                              CAT_R12_PH, junta_cm=1.5, factor=1.05, umbral_pct=7,
                              entregado_pv=35, entregado_ph=78)
    assert r["hiladas"] == 11
    assert math.isclose(r["total"], 107.6, rel_tol=1e-2)
    assert math.isclose(r["pv"]["teorico"], 33.0)
    assert math.isclose(r["ph"]["teorico"], round(r["total"] - 33.0, 1))
    assert r["pv"]["semaforo"] == "VERDE" and r["ph"]["semaforo"] == "VERDE"


def test_calculadora_combinado_solo_vertical_y_horizontal():
    v = calculadora_combinado(4.0, 2.4, "Vertical (solo P.V.)", 0, CAT_R12_PV,
                              CAT_R12_PH, junta_cm=1.5)
    assert v["ph"]["teorico"] == 0.0
    assert math.isclose(v["pv"]["teorico"], v["total"], abs_tol=0.1)
    h = calculadora_combinado(4.0, 2.4, "Horizontal (solo P.H.)", 3, CAT_R12_PV,
                              CAT_R12_PH, junta_cm=1.5)
    assert h["pv"]["teorico"] == 0.0
    assert math.isclose(h["ph"]["teorico"], h["total"], abs_tol=0.1)


def test_bloques_teoricos_solo_ph_sin_pv():
    # Muro 100 % P.H. sin bloque P.V.: el teórico cae todo en P.H.
    teo = bloques_teoricos_muro(3.0, 2.4, 0, None, BPH, uso="P.H.")
    assert teo["pv"] == 0.0
    assert math.isclose(teo["ph"], 90.0) and teo["hiladas"] == 12


def test_resumen_pedido_por_tipo():
    catalogo = [CAT_R12_PV, CAT_R12_PH]
    df = pd.DataFrame({
        "Sector": ["Torre"] * 3, "Piso": ["5", "5", "6"],
        "Zona": ["APART 501", "APART 501", "APART 603"],
        "Tipo_ladrillo": ["P.V. rayado 12"] * 3,
        "Tipo_bloque_PH": ["P.H. rayado 12", None, None],
        "Bloques_PV_teo": [33.0, 0.0, 50.0],
        "Bloques_PH_teo": [44.5, None, None],
    })
    res = resumen_pedido_por_tipo(df, catalogo, apto="APART 501",
                                  factor=1.05, umbral_pct=7)
    por = res["por_tipo"].set_index("Tipo_bloque")
    # P.V. rayado 12: apto 33, obra 83 (33+50), con factor 87.15, a pedir ceil(93.25)=94
    assert math.isclose(por.loc["P.V. rayado 12", "Total_apto"], 33.0)
    assert math.isclose(por.loc["P.V. rayado 12", "Total_obra"], 83.0)
    assert math.isclose(por.loc["P.V. rayado 12", "Con_factor"], 87.2, rel_tol=1e-2)
    assert por.loc["P.V. rayado 12", "A_pedir"] == 94
    assert math.isclose(por.loc["P.H. rayado 12", "Total_obra"], 44.5)
    # Totales por clase
    assert math.isclose(res["totales"]["pv"]["Total_obra"], 83.0)
    assert math.isclose(res["totales"]["ph"]["Total_obra"], 44.5)
    assert math.isclose(res["totales"]["general"]["Total_obra"], 127.5)


def test_resumen_pedido_sin_datos_no_revienta():
    res = resumen_pedido_por_tipo(pd.DataFrame(), [CAT_R12_PV], apto=None,
                                  factor=1.05, umbral_pct=7)
    assert (res["por_tipo"]["Total_obra"] == 0).all()
    assert res["totales"]["general"]["A_pedir"] == 0


def test_resumen_pedido_filtrado_refleja_la_seleccion():
    """La página 'Resumen ladrillos' filtra el df por piso+apto ANTES de llamar
    (con apto=None), para que 'A pedir' salga de la selección, no del proyecto."""
    catalogo = [CAT_R12_PV, CAT_R12_PH]
    df = pd.DataFrame({
        "Sector": ["Torre"] * 3, "Piso": ["5", "5", "6"],
        "Zona": ["APART 501", "APART 502", "APART 501"],
        "Tipo_ladrillo": ["P.V. rayado 12"] * 3,
        "Tipo_bloque_PH": ["P.H. rayado 12", "P.H. rayado 12", "P.H. rayado 12"],
        "Bloques_PV_teo": [30.0, 28.0, 50.0],
        "Bloques_PH_teo": [46.0, 44.0, 60.0],
    })
    # Filtro piso=5 + apto=501 → solo la 1ª fila (PV 30, PH 46).
    df_sel = df[(df["Piso"] == "5") & (df["Zona"] == "APART 501")]
    res = resumen_pedido_por_tipo(df_sel, catalogo, apto=None,
                                  factor=1.05, umbral_pct=7)
    g = res["totales"]
    # PV: 30 → con factor 31.5 → a pedir ceil(31.5*1.07)=ceil(33.7)=34
    assert math.isclose(g["pv"]["Total_obra"], 30.0)
    assert g["pv"]["A_pedir"] == 34
    # PH: 46 → con factor 48.3 → a pedir ceil(48.3*1.07)=ceil(51.7)=52
    assert math.isclose(g["ph"]["Total_obra"], 46.0)
    assert g["ph"]["A_pedir"] == 52
    # No se mezcla con el resto del proyecto (que sumaría 108 PV).
    assert g["general"]["Total_obra"] == 76.0


def test_rendimiento_por_junta_coincide_con_referencia():
    tabla = rendimiento_por_junta([CAT_R12_PV]).set_index("Bloque")
    # Hoja "Referencia" del Excel para el rayado: 11.6 / 11.2 / 11.0 / 10.8 / 10.5
    assert list(tabla.columns) == [f"{j:g} cm" for j in JUNTAS_CM]
    assert math.isclose(tabla.loc["P.V. rayado 12", "1 cm"], 11.6, abs_tol=0.05)
    assert math.isclose(tabla.loc["P.V. rayado 12", "1.5 cm"], 11.2, abs_tol=0.05)
    assert math.isclose(tabla.loc["P.V. rayado 12", "2.4 cm"], 10.5, abs_tol=0.05)


def test_validar_catalogo_limpia_y_completa():
    crudo = [
        {"nombre": " P.V. rayado 12 ", "clase": "p.v.", "largo_m": 0.39,
         "alto_m": 0.19, "espesor_m": 0.12, "junta_m": 0.01, "unds_por_estiba": 90},
        {"nombre": "", "clase": "PH"},                # sin nombre → descartada
        {"nombre": "Raro", "clase": "XX", "largo_m": -1},  # clase y largo inválidos → defaults
        "no soy un dict",
    ]
    cat = validar_catalogo(crudo)
    assert len(cat) == 2
    assert cat[0]["nombre"] == "P.V. rayado 12" and cat[0]["clase"] == "PV"
    assert cat[1]["clase"] == "PV" and cat[1]["largo_m"] == 0.40   # default del catálogo
    assert validar_catalogo("no-lista") == []


if __name__ == "__main__":
    fallos = 0
    pruebas = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for prueba in pruebas:
        try:
            prueba()
            print(f"[OK]   {prueba.__name__}")
        except AssertionError as e:
            fallos += 1
            print(f"[FAIL] {prueba.__name__}  ->  {e or 'assert fallo'}")
        except Exception as e:  # noqa: BLE001
            fallos += 1
            print(f"[FAIL] {prueba.__name__}  ->  ERROR {type(e).__name__}: {e}")
    print(f"\n{len(pruebas) - fallos}/{len(pruebas)} pruebas OK")
    raise SystemExit(1 if fallos else 0)
