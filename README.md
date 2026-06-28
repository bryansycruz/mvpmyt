# Control de Mampostería — MyT

Aplicación web (**Streamlit**) para el control diario de la mampostería de una obra:
cuántos **m²** pega cada oficial, cuánto **mortero (sacos)** consume, cuántos **bloques
por tipo** se gastan y cómo va el avance frente a las **metas**. Reemplaza el Excel
manual por una captura guiada, indicadores automáticos y reportes descargables, con los
datos guardados en la nube y acceso por usuario.

> Pensada para residentes de obra y administración: registrar el trabajo del día y ver
> de un vistazo el rendimiento, el consumo de material y el cumplimiento de metas.

---

## ¿Qué permite hacer?

| Pantalla | Para qué sirve |
|---|---|
| 📋 **Ingreso de datos** | Registrar los muros del día por oficial: largo, alto, m², sacos de mortero, dovelas y bloques. Un registro agrupa varios muros que comparten los sacos. Sección **➕ Muros mixtos** para muros con dos tipologías. Calcula el consumo en vivo. |
| 📈 **Control** | Gráficas: m² y consumo por oficial, sacos gastados, **tendencia de consumo por día/semana** y **presencia de mamposteros** (cuenta días como oficial **y** como ayudante). Con filtros por mampostero. |
| 📅 **Cierres** | Cierre **diario** y **semanal**, comparación entre periodos y **cumplimiento de metas**, con gráficos y descarga a Excel. |
| 🧱 **Materiales** | Movimientos de almacén (entradas/salidas), inventario y **desperdicio de bloque**: lo pegado (teórico) vs lo entregado por almacén. |
| 🧮 **Calculadora** | Simulador (no guarda nada): **un muro**, **Simular apto** (varios muros → bloques por tipo en teórico / con factor / con desperdicio final, **descargable a Excel y PDF**) y rendimiento por junta. |
| 📦 **Resumen ladrillos** | Bloques por tipo y por apto, para estimar el pedido. |
| 📊 **Registros** | Histórico completo, con búsqueda y exportación a Excel. |

Además: **inicio de sesión** por correo, **roles de administrador** (editar metas y
catálogo, eliminar registros) y trabajo **multiusuario** sobre los mismos datos.

---

## Indicadores y metas

| Indicador | Valor por defecto | Significado |
|---|---|---|
| Meta de consumo | 0.84 sac/m² | Cumple si el mortero usado por m² es menor o igual |
| Kg por saco | 42.5 kg | Para convertir sacos a kilogramos |
| Meta por piso | 800 m²/semana | Avance esperado de cada piso por semana |
| Meta por mampostero | 10 m²/día | Rendimiento esperado por día trabajado |

Todas las metas y el catálogo de bloques son **configurables desde la app** (panel de
administrador); no hay que tocar el código.

---

## ¿Cómo guarda los datos?

El almacenamiento se elige **automáticamente** según lo que esté configurado:

```text
[Navegador / celular] → [Streamlit] → ┌─ Supabase (base de datos)   ← preferido
                                       ├─ SharePoint (Excel)
                                       └─ Excel local (modo demo)
```

Sin configurar nada, arranca en **modo local (demo)** guardando en un Excel, ideal para
probar.

---

## Puesta en marcha (local)

```powershell
# 1. Entorno e instalación
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. (Opcional) credenciales para usar la nube
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
#   …completar secrets.toml con tus datos (ver plantilla)

# 3. Ejecutar
streamlit run app.py
```

> Si lanzas la app sin activar el entorno (`venv`), asegúrate de usar el Python del
> proyecto: `.\venv\Scripts\streamlit.exe run app.py`.

---

## Seguridad (checklist)

Las **credenciales reales nunca se suben al repositorio**: viven solo en
`.streamlit/secrets.toml` (en `.gitignore`) o, en producción, en
**Streamlit Cloud → Settings → Secrets**. Usa `secrets.toml.example` como plantilla.

Antes de publicar la app:

- [ ] **Lista blanca de acceso:** define `CORREOS_AUTORIZADOS` (quién puede entrar).
      Vacío = **cualquiera** puede registrarse y ver/editar todos los datos.
- [ ] **Administradores:** define `CORREOS_ADMIN` (quién edita config y borra registros).
      Vacío = cualquier usuario autenticado sería admin.
- [ ] **Base de datos (Supabase):** en producción usa `SUPABASE_KEY = service_role`
      (solo del lado servidor) + `SUPABASE_ANON_KEY = anon` para el login, y endurece las
      políticas **RLS** (ver `supabase_schema.sql`, sección *ENDURECIMIENTO*).
- [ ] **Nunca** pegues claves, URLs reales, IPs ni correos internos en el código, los
      commits o el README.
- [ ] `.gitignore` ya excluye `secrets.toml`, `.env`, datos de obra (`*.xlsx`, `*.docx`),
      documentación interna y logs.

---

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
├── supabase_schema.sql     ← Tablas, índices y seguridad (RLS) de la base de datos
├── test_calculos.py        ← Pruebas de la lógica de negocio
├── test_data_backend.py    ← Pruebas del borrado de registros
├── requirements.txt        ← Dependencias
└── .streamlit/
    └── secrets.toml.example ← Plantilla de credenciales (sin datos reales)
```

---

## Pruebas

```powershell
python test_calculos.py
python test_data_backend.py
```
