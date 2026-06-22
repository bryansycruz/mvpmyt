"""
test_data_backend.py — Pruebas de la fachada de borrado de registros
────────────────────────────────────────────────────────────────────
Ejecutar:  python test_data_backend.py
No necesita pytest: usa asserts y sale con código ≠ 0 si algo falla.

Cubre `eliminar_registros` (borrado DEFINITIVO de un ingreso por Grupo_id),
que es la pieza nueva y más delicada: se prueba con stubs de backend para no
tocar Supabase ni red. Garantiza que:
  - ids vacíos/None no borran nada (ni llaman al backend);
  - la ruta local/SharePoint quita SOLO las filas del grupo y reescribe;
  - un id inexistente no reescribe el archivo;
  - los ids se normalizan (espacios) antes de comparar;
  - la ruta Supabase delega en el DELETE dirigido por Grupo_id.
"""

import pandas as pd

import data_backend as db


class _FakeSP:
    """Stub del conector SharePoint/local: lee/guarda en memoria."""

    def __init__(self, df):
        self._df = df
        self.guardado = None          # último df reescrito (None = no se llamó)

    def leer_datos(self):
        return self._df.copy()

    def guardar_datos(self, df):
        self.guardado = df.copy()


def _df():
    return pd.DataFrame({
        "Grupo_id": ["g1", "g1", "g2", ""],   # la última fila no tiene grupo
        "Oficial": ["A", "A", "B", "C"],
        "M2_ejecutados": [10.0, 5.0, 7.0, 4.0],
    })


def _con_backend(nombre, fn, fake_sp=None, fake_sb=None):
    """Ejecuta fn() con backend_actual y sb/sp parcheados; restaura al salir."""
    orig_backend, orig_sp, orig_sb = db.backend_actual, db.sp, db.sb
    db.backend_actual = lambda: nombre
    if fake_sp is not None:
        db.sp = fake_sp
    if fake_sb is not None:
        db.sb = fake_sb
    try:
        return fn()
    finally:
        db.backend_actual, db.sp, db.sb = orig_backend, orig_sp, orig_sb


# ── ids vacíos: no tocan nada ───────────────────────────────────────
def test_eliminar_sin_ids_devuelve_cero():
    assert db.eliminar_registros([]) == 0
    assert db.eliminar_registros(None) == 0
    # strings vacíos/espacios se filtran y ni siquiera consultan el backend
    assert db.eliminar_registros(["", "   "]) == 0


# ── ruta local/SharePoint ───────────────────────────────────────────
def test_eliminar_local_quita_grupo_y_reescribe():
    fake = _FakeSP(_df())
    n = _con_backend("local", lambda: db.eliminar_registros(["g1"]), fake_sp=fake)
    assert n == 2                                   # g1 tenía 2 filas
    assert fake.guardado is not None
    assert list(fake.guardado["Grupo_id"]) == ["g2", ""]   # quedan las otras


def test_eliminar_local_normaliza_ids_con_espacios():
    fake = _FakeSP(_df())
    n = _con_backend("local", lambda: db.eliminar_registros([" g1 "]), fake_sp=fake)
    assert n == 2                                   # " g1 " == "g1"


def test_eliminar_local_id_inexistente_no_reescribe():
    fake = _FakeSP(_df())
    n = _con_backend("local", lambda: db.eliminar_registros(["zzz"]), fake_sp=fake)
    assert n == 0
    assert fake.guardado is None                    # no se reescribió el archivo


def test_eliminar_local_varios_grupos():
    fake = _FakeSP(_df())
    n = _con_backend("local", lambda: db.eliminar_registros(["g1", "g2"]), fake_sp=fake)
    assert n == 3
    assert list(fake.guardado["Grupo_id"]) == [""]  # solo queda la fila sin grupo


# ── ruta Supabase: delega en el DELETE dirigido ─────────────────────
def test_eliminar_supabase_delega_por_grupo():
    registro = {}

    class _FakeSB:
        @staticmethod
        def eliminar_registros_por_grupo(ids):
            registro["ids"] = ids
            return len(ids)

    n = _con_backend("supabase",
                     lambda: db.eliminar_registros([" g1 ", "g2"]),
                     fake_sb=_FakeSB)
    assert n == 2
    assert registro["ids"] == ["g1", "g2"]          # llega normalizado, sin espacios


# ─────────────────────────────────────────────────────────────
# Borrado de movimientos de almacén + alta/lectura de estibas devueltas
# (lo nuevo de 2026-06-22). Mismos stubs en memoria, sin tocar Supabase.
# ─────────────────────────────────────────────────────────────
class _FakeAlmacen:
    """Stub de sp para entradas/salidas/estibas: lee/guarda en memoria."""

    def __init__(self, entradas=None, salidas=None, estibas=None):
        self._d = {"entradas": entradas, "salidas": salidas, "estibas": estibas}
        self.guardado = {}     # {'estibas': df, ...} último reescrito por tipo

    def leer_entradas(self):
        return self._d["entradas"].copy()

    def guardar_entradas(self, df):
        self.guardado["entradas"] = df.copy()

    def leer_salidas(self):
        return self._d["salidas"].copy()

    def guardar_salidas(self, df):
        self.guardado["salidas"] = df.copy()

    def leer_estibas(self):
        return self._d["estibas"].copy()

    def guardar_estibas(self, df):
        self.guardado["estibas"] = df.copy()


class _FakeSBmov:
    """Stub de sb: registra los ids/df que recibe, devuelve nº borradas."""

    def __init__(self):
        self.calls = {}

    def eliminar_entradas_por_id(self, ids):
        self.calls["entradas"] = list(ids)
        return len(ids)

    def eliminar_salidas_por_id(self, ids):
        self.calls["salidas"] = list(ids)
        return len(ids)

    def eliminar_estibas_por_id(self, ids):
        self.calls["estibas"] = list(ids)
        return len(ids)

    def insertar_estibas(self, df):
        self.calls["insertar_estibas"] = df.copy()


def _df_mov():
    """Tres movimientos con Timestamp_registro único (la llave de borrado local)."""
    return pd.DataFrame({
        "Timestamp_registro": pd.to_datetime(
            ["2026-06-01 08:00", "2026-06-02 09:00", "2026-06-03 10:00"]),
        "Cantidad": [100.0, 200.0, 300.0],
    })


# ── borrado de movimientos: ruta Supabase (por id) ──────────────────
def test_eliminar_entradas_supabase_por_id():
    fake_sb = _FakeSBmov()
    filas = pd.DataFrame({"id": [10.0, 11.0], "Cantidad": [5.0, 6.0]})
    n = _con_backend("supabase", lambda: db.eliminar_entradas(filas), fake_sb=fake_sb)
    assert n == 2
    assert fake_sb.calls["entradas"] == [10.0, 11.0]


def test_eliminar_estibas_supabase_ignora_id_nulo():
    fake_sb = _FakeSBmov()
    filas = pd.DataFrame({"id": [7.0, None], "Cantidad": [1.0, 2.0]})
    n = _con_backend("supabase", lambda: db.eliminar_estibas(filas), fake_sb=fake_sb)
    assert n == 1                                   # el id None se descarta
    assert fake_sb.calls["estibas"] == [7.0]


# ── borrado de movimientos: ruta local (anti-join por Timestamp) ────
def test_eliminar_salidas_local_quita_por_timestamp():
    fake = _FakeAlmacen(salidas=_df_mov())
    filas = _df_mov().iloc[[1]]                      # borrar el del 2026-06-02
    n = _con_backend("local", lambda: db.eliminar_salidas(filas), fake_sp=fake)
    assert n == 1
    quedan = list(fake.guardado["salidas"]["Cantidad"])
    assert quedan == [100.0, 300.0]                  # se conservan los otros dos


def test_eliminar_entradas_local_timestamp_inexistente_no_reescribe():
    fake = _FakeAlmacen(entradas=_df_mov())
    filas = pd.DataFrame({"Timestamp_registro": pd.to_datetime(["2030-01-01 00:00"])})
    n = _con_backend("local", lambda: db.eliminar_entradas(filas), fake_sp=fake)
    assert n == 0
    assert "entradas" not in fake.guardado           # no se reescribió


def test_eliminar_movimientos_vacio_devuelve_cero():
    fake_sb = _FakeSBmov()
    assert _con_backend("supabase",
                        lambda: db.eliminar_entradas(pd.DataFrame()), fake_sb=fake_sb) == 0
    assert "entradas" not in fake_sb.calls           # ni siquiera consulta el backend


# ── alta de estibas devueltas ───────────────────────────────────────
def test_agregar_estibas_local_concatena_y_reescribe():
    previas = pd.DataFrame({"Cantidad": [3.0], "Proveedor": ["X"]})
    fake = _FakeAlmacen(estibas=previas)
    nuevas = pd.DataFrame({"Cantidad": [5.0], "Proveedor": ["Y"]})
    _con_backend("local", lambda: db.agregar_estibas(nuevas), fake_sp=fake)
    assert list(fake.guardado["estibas"]["Cantidad"]) == [3.0, 5.0]


def test_agregar_estibas_supabase_inserta():
    fake_sb = _FakeSBmov()
    nuevas = pd.DataFrame({"Cantidad": [5.0]})
    _con_backend("supabase", lambda: db.agregar_estibas(nuevas), fake_sb=fake_sb)
    assert list(fake_sb.calls["insertar_estibas"]["Cantidad"]) == [5.0]


def test_agregar_estibas_vacio_no_hace_nada():
    fake_sb = _FakeSBmov()
    _con_backend("supabase", lambda: db.agregar_estibas(pd.DataFrame()), fake_sb=fake_sb)
    assert fake_sb.calls == {}


# ── helper _con_id del conector Supabase (lo que rompió el bug del 3er arg) ──
def test_con_id_conserva_id_y_descarta_extras():
    import supabase_connector as sb
    from data_schema import normalizar_estibas
    crudo = [{"id": 5, "Fecha": "2026-06-22", "Cantidad": 9, "Proveedor": "P",
              "No_remision": "R", "Observaciones": "", "Timestamp_registro": None,
              "columna_basura": "x"}]
    df = sb._con_id(crudo, normalizar_estibas)
    assert int(df["id"].iloc[0]) == 5
    assert "columna_basura" not in df.columns        # normalizar reindexa al esquema
    assert float(df["Cantidad"].iloc[0]) == 9.0


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
