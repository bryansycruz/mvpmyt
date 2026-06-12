"""
migrar_a_supabase.py — Carga inicial de datos a Supabase
─────────────────────────────────────────────────────────
Sube a la tabla de Supabase el histórico que ya tengas en el Excel local
(o en SharePoint). Útil para no empezar de cero al migrar.

Requisitos:
    - `.streamlit/secrets.toml` con SUPABASE_URL y SUPABASE_KEY configurados.
    - La tabla creada (ver supabase_schema.sql).

Uso (desde la carpeta del proyecto):

    python migrar_a_supabase.py            # migra desde el Excel local
    python migrar_a_supabase.py --sharepoint   # migra desde SharePoint
    python migrar_a_supabase.py --reemplazar   # borra la tabla antes de subir

Por defecto AÑADE (append). Con --reemplazar, vacía la tabla primero.
"""

import sys

import sharepoint_connector as sp
import supabase_connector as sb


def main() -> None:
    args = set(sys.argv[1:])
    usar_sharepoint = "--sharepoint" in args
    reemplazar = "--reemplazar" in args

    if not sb.disponible():
        print("✗ Supabase no está configurado en .streamlit/secrets.toml "
              "(faltan SUPABASE_URL / SUPABASE_KEY).")
        sys.exit(1)

    # Origen de los datos.
    if usar_sharepoint:
        if sp.modo_local():
            print("✗ Pediste --sharepoint pero no hay credenciales de SharePoint.")
            sys.exit(1)
        print("Leyendo histórico desde SharePoint…")
    else:
        print("Leyendo histórico desde el Excel local…")
    df = sp.leer_datos()  # SharePoint si está configurado, si no Excel local

    if df.empty:
        print("No hay registros en el origen. Nada que migrar.")
        return

    print(f"Se van a migrar {len(df)} registros a la tabla "
          f"'{sb._nombre_tabla()}' de Supabase"
          f"{' (REEMPLAZANDO el contenido actual)' if reemplazar else ' (append)'}…")

    if reemplazar:
        sb.guardar_datos(df)
    else:
        sb.insertar_registros(df)

    print("✓ Migración completada.")


if __name__ == "__main__":
    main()
