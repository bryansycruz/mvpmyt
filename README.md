# Control de Mampostería

Aplicación web (**Streamlit**) para llevar el control diario de la mampostería de
una obra: cuántos **m²** pega cada oficial, cuánto **mortero** consume y cómo va el
avance frente a las **metas**. Reemplaza el Excel manual por una captura guiada,
indicadores automáticos y reportes descargables, con los datos guardados en la nube
y acceso por usuario.

> Pensada para residentes de obra y administración: registrar el trabajo del día y
> ver de un vistazo el rendimiento, el consumo de material y el cumplimiento de metas.

## ¿Qué permite hacer?

| Pantalla | Para qué sirve |
|---|---|
| 📋 **Ingreso de datos** | Registrar los muros del día por oficial: largo, alto, m², sacos de mortero y dovelas. Un mismo registro puede agrupar varios muros que comparten los sacos. Calcula el consumo en vivo. |
| 📈 **Gráficas** | M² y consumo por oficial, sacos gastados, evolución diaria y presencia de mamposteros. Con **filtros por mampostero** para comparar a quien quieras. |
| 📅 **Cierres** | Cierre **diario** y **semanal**, **comparación entre semanas/meses**, y el **cumplimiento de metas** de la semana (m² por piso y por mampostero) con gráficos y descarga a Excel. |
| 🧱 **Materiales** | Movimientos de almacén (entradas/salidas), inventario y **desperdicio de bloque**: compara lo que se pegó (teórico) contra lo entregado por almacén. |
| 🎯 **Last Planner** | Plan semanal de trabajo *(en construcción)*. |
| 📊 **Registros** | Histórico completo, con búsqueda y exportación. |

Además: **inicio de sesión** por correo, **roles de administrador** (editar metas,
catálogo y eliminar registros) y trabajo **multiusuario** sobre los mismos datos.

## Indicadores y metas

| Indicador | Valor por defecto | Significado |
|---|---|---|
| Meta de consumo | 0.84 sac/m² | Cumple si el mortero usado por m² es menor o igual |
| Kg por saco | 42.5 kg | Para convertir sacos a kilogramos |
| Meta por piso | 800 m²/semana | Avance esperado de cada piso por semana |
| Meta por mampostero | 10 m²/día | Rendimiento esperado por día trabajado |

Las metas son **configurables** desde la app (panel de administrador); no hay que
tocar el código.

## ¿Cómo guarda los datos?

El almacenamiento se elige **automáticamente** según la configuración disponible:

```text
[Navegador / celular] → [Streamlit] → ┌─ Supabase (base de datos)   ← preferido
                                       ├─ SharePoint (Excel)
                                       └─ Excel local (modo demo)
```

Sin configurar nada, arranca en **modo local (demo)** guardando en un Excel, ideal
para probar.

## Puesta en marcha (local)

```powershell
# 1. Entorno e instalación
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. (Opcional) credenciales para usar la nube
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
#   …completar secrets.toml con tus datos

# 3. Ejecutar
streamlit run app.py
```

## Despliegue y seguridad

La guía paso a paso para publicar la app (Streamlit Cloud + base de datos), junto
con el **checklist de seguridad** (no subir credenciales, control de acceso por
correo, protección de la base de datos), está en **[DESPLIEGUE.md](DESPLIEGUE.md)**.

> Las credenciales reales viven solo en `.streamlit/secrets.toml`, que **no se sube
> al repositorio** (está en `.gitignore`). Usa `secrets.toml.example` como plantilla.

## Estructura del proyecto

```text
├── app.py                  ← Aplicación principal (pantallas + login)
├── calculos.py             ← Lógica de negocio (consumo, bloques teóricos, metas)
├── data_schema.py          ← Modelo de datos (columnas y tipos)
├── data_backend.py         ← Selector de almacenamiento (nube o local)
├── supabase_connector.py   ← Conector de base de datos
├── sharepoint_connector.py ← Conector de SharePoint/Excel
├── auth_supabase.py        ← Inicio de sesión por correo
├── migrar_a_supabase.py    ← Migración del histórico a la base de datos
├── supabase_schema.sql     ← Tablas, índices y seguridad de la base de datos
├── test_calculos.py        ← Pruebas de la lógica de negocio
├── test_data_backend.py    ← Pruebas del borrado de registros
├── requirements.txt        ← Dependencias
├── DESPLIEGUE.md           ← Guía de despliegue y seguridad
└── .streamlit/
    └── secrets.toml.example← Plantilla de credenciales (sin datos reales)
```

## Pruebas

```powershell
python test_calculos.py
python test_data_backend.py
```
