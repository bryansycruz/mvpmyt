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
