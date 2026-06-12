#  Control de Mampostería — Serrania Campestre

App **Streamlit** para registrar el trabajo diario de mampostería por oficial,
calcular indicadores (M², consumo de mortero, cumplimiento de meta, bloques
teóricos P.V./P.H. y desperdicio vs almacén) y guardar el histórico en la nube.
Soporta **3 backends** con selección automática:

```
[Navegador / celular] → [Streamlit] → ┌─ Supabase (PostgreSQL)   ← prioridad
                                       ├─ SharePoint (Excel) → Power BI
                                       └─ Excel local (demo)
```

El backend se elige solo según las credenciales en `secrets.toml`
(ver [data_backend.py](data_backend.py)):
**Supabase → SharePoint → Excel local**.

> 📄 ¿Migrando a Supabase? Lee **[docs/CAMBIOS_SUPABASE.md](docs/CAMBIOS_SUPABASE.md)**:
> documenta todos los cambios y el paso a paso para activarlo y desplegar.
> El login se documenta en **[docs/LOGIN_SUPABASE.md](docs/LOGIN_SUPABASE.md)**.

## Estructura

```
MVP_MyT/
├── app.py                      ← Aplicación principal (5 pantallas + login)
│                                  📋 Ingreso · 📊 Registros · 📈 Gráficas ·
│                                  📅 Cierres · 🧱 Materiales (desperdicio)
├── calculos.py                 ← Lógica de negocio pura (consumo, bloques
│                                  teóricos P.V./P.H., conciliación, catálogo)
├── data_schema.py              ← Modelo de datos compartido (columnas y tipos)
├── data_backend.py             ← Selector de backend (Supabase/SharePoint/local)
├── supabase_connector.py       ← Backend Supabase (PostgreSQL)
├── sharepoint_connector.py     ← Backend SharePoint (Microsoft Graph)
├── auth_supabase.py            ← Login con Supabase Auth (email + contraseña)
├── migrar_a_supabase.py        ← Migra el histórico existente a Supabase
├── test_calculos.py            ← Pruebas (correr: python test_calculos.py)
├── supabase_schema.sql         ← SQL: tablas, índices, RLS y endurecimiento
├── requirements.txt            ← Dependencias
├── README_DESPERDICIO.md       ← Manual del módulo de desperdicio (interno,
│                                  en .gitignore: no se publica en el repo)
├── docs/                       ← Documentación de soporte y notas internas
├── .streamlit/
│   ├── secrets.toml.example    ← Plantilla de credenciales (esta SÍ se versiona)
│   └── secrets.toml            ← Credenciales reales (en .gitignore, NO subir)
└── .gitignore
```

## Constantes del negocio

| Constante | Valor | Significado |
|---|---|---|
| `TEORICO_SAC_M2` | 0.84 | Meta de consumo (sacos por m²) |
| `KG_POR_SACO` | 42.5 | Kilogramos por saco de mortero |
| `UMBRAL_DESPERDICIO_PCT` | 7.0 | Semáforo de desperdicio de bloques (%) |
| `FACTOR_AJUSTE_BLOQUES` | 1.0 | Ajuste del teórico por cortes/trabas |

Un registro **cumple meta** cuando `Consumo_real_sac_m2 ≤ 0.84`. Los cuatro
valores son editables por el admin desde la app (panel  y catálogo).

## Módulo de desperdicio de bloques ( Materiales)

Calcula los **bloques teóricos por tipo** (perforación vertical/horizontal) de
cada muro a partir de Largo × Alto y # dovelas, y los concilia contra las
**remisiones de salida de almacén** para mostrar el **desperdicio %** por
piso/sector/tipo con semáforo — sin contar ladrillos en obra. El manual de uso
campo por campo (para la residente) está en `README_DESPERDICIO.md`.

## Puesta en marcha local

```powershell
# 1. Crear/activar entorno e instalar dependencias
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Configurar credenciales (elige Supabase y/o SharePoint)
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
#   …editar secrets.toml (ver opciones abajo)

# 3. Ejecutar
streamlit run app.py
```

Sin configurar nada, arranca en **modo local (demo)** guardando en un Excel.

## Configuración de Supabase (recomendado)

1. Crea un proyecto gratis en [supabase.com](https://supabase.com).
2. **SQL Editor → New query** → pega [supabase_schema.sql](supabase_schema.sql) → **Run**.
3. **Project Settings → API** → copia `Project URL` y la key `service_role`.
4. En `secrets.toml`:
   ```toml
   SUPABASE_URL = "https://xxxxxxxxxxxx.supabase.co"
   SUPABASE_KEY = "eyJhbGciOi...."
   ```
5. (Opcional) Migra el histórico existente: `python migrar_a_supabase.py`.

Detalle completo y notas de seguridad en **[docs/CAMBIOS_SUPABASE.md](docs/CAMBIOS_SUPABASE.md)**.

### Configuración editable (meta, kg/saco, proyecto)

El esquema crea también la tabla `config_app`. Con eso, un **administrador** puede
cambiar desde la barra lateral (panel **⚙️ Configuración**) la **meta teórica**,
los **kg por saco** y el **nombre del proyecto**, sin tocar el código. Los cambios
se guardan en Supabase y aplican para todos.

- Quién es admin se define con `CORREOS_ADMIN` en `secrets.toml` (si está vacío,
  se usa `CORREOS_AUTORIZADOS`; si ambos están vacíos, cualquier usuario lo es).
- El cambio aplica de inmediato a los indicadores que se calculan en vivo (colores,
  delta vs meta, línea de meta de las gráficas) y a los **registros nuevos**. El
  histórico ya guardado conserva el `Cumple_meta`/kg del momento en que se ingresó.
- Sin Supabase (SharePoint o modo local) los valores quedan fijos en el código.

## Configuración de Azure / SharePoint (resumen)

1. **Azure AD → App registrations → New registration** → nombre `app-mamposteria`.
2. Anotar `tenant_id` y `client_id`.
3. **Certificates & secrets** → crear `client_secret`.
4. **API permissions → Microsoft Graph → Application permissions**:
   `Files.ReadWrite.All` y `Sites.ReadWrite.All` → **Grant admin consent**.
5. En SharePoint, crear `Documentos/Mamposteria/` y subir un
   `datos_mamposteria.xlsx` vacío (o dejar que la app lo cree al primer guardado).

> El conector resuelve el sitio en dos pasos (dominio + nombre → `site_id` →
> drive), que es la forma fiable en Microsoft Graph.

## Despliegue en Streamlit Community Cloud

1. Subir el repo a GitHub (sin `secrets.toml`).
2. [share.streamlit.io](https://share.streamlit.io) → conectar repo → `app.py`.
3. **Advanced settings → Secrets** → pegar tus credenciales. Con **Supabase**
   bastan dos líneas (`SUPABASE_URL`, `SUPABASE_KEY`), lo que hace el despliegue
   mucho más simple que SharePoint (que requiere App Registration de Azure).
4. **Deploy**.

## Power BI

`Obtener datos → SharePoint / Excel` → sitio
`https://tuempresa.sharepoint.com/sites/Construccion` → `datos_mamposteria.xlsx`
→ hoja `Registros`. Configurar **actualización programada** en Power BI Service.
