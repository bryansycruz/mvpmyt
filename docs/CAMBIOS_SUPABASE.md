# 🔄 Cambios — Orientación a Supabase (sin eliminar SharePoint)

Este documento resume **qué se cambió y por qué** para que la app de Control de
Mampostería pueda guardar sus datos en **Supabase** (PostgreSQL gratuito) y
desplegarse fácilmente en **Streamlit Community Cloud**, **manteniendo SharePoint**
como alternativa.

---

## 🎯 Objetivo

- Añadir **Supabase** como backend de almacenamiento, con **prioridad**.
- **No eliminar SharePoint**: sigue disponible como respaldo/alternativa.
- Mantener el **Excel local** como modo demo (sin configurar nada).
- Que el cambio de backend sea **automático** según las credenciales presentes.

```
ANTES:   [Streamlit] → SharePoint (Excel)  → Power BI
                       ↘ Excel local (demo)

AHORA:   [Streamlit] → Supabase (Postgres) ← prioridad
                     → SharePoint (Excel)   ← si no hay Supabase
                     → Excel local (demo)   ← si no hay nada
```

La selección la decide [data_backend.py](data_backend.py) en este orden:

1. **Supabase** — si `SUPABASE_URL` y `SUPABASE_KEY` están en los secrets.
2. **SharePoint** — si están las credenciales de Azure/Graph.
3. **Excel local** — si no hay nada configurado (demo).

> Para volver a SharePoint basta con **borrar/comentar** las dos líneas de
> Supabase en `secrets.toml`. No se toca código.

---

## 📁 Archivos NUEVOS

| Archivo | Para qué sirve |
|---|---|
| [data_schema.py](data_schema.py) | **Modelo de datos único** (columnas, tipos y normalización) compartido por todos los backends. Evita duplicar lógica. |
| [supabase_connector.py](supabase_connector.py) | Backend **Supabase**: leer, insertar (append) y reemplazar registros. Misma interfaz que el conector de SharePoint. |
| [data_backend.py](data_backend.py) | **Selector/fachada** que elige el backend y expone `leer_datos`, `agregar_registros`, etc. Es lo único que importa `app.py`. |
| [supabase_schema.sql](supabase_schema.sql) | SQL para **crear la tabla** `registros_mamposteria` en Supabase. |
| [migrar_a_supabase.py](migrar_a_supabase.py) | Script para **subir el histórico** existente (Excel local o SharePoint) a Supabase. |

## ✏️ Archivos MODIFICADOS

| Archivo | Cambio |
|---|---|
| [app.py](app.py) | Ahora importa de `data_backend` (no directo de `sharepoint_connector`). El guardado usa `agregar_registros()` (append, eficiente en Supabase). El sidebar muestra el backend activo (Supabase / SharePoint / local). |
| [sharepoint_connector.py](sharepoint_connector.py) | Se movió el esquema/normalización a `data_schema.py`. **La lógica de SharePoint quedó intacta.** |
| [requirements.txt](requirements.txt) | Se añadió `supabase>=2.4.0`. |
| [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example) | Se añadió el bloque de credenciales de Supabase y se documentó la prioridad. |

> ✅ **Compatibilidad**: si hoy usas SharePoint, **todo sigue igual**. La app
> detecta que no hay Supabase y usa SharePoint como siempre.

---

## 🚀 Cómo activar Supabase (paso a paso)

### 1) Crear el proyecto y la tabla
1. Entra a [supabase.com](https://supabase.com) → **New project** (plan Free).
2. Cuando esté listo, ve a **SQL Editor → New query**.
3. Pega el contenido de [supabase_schema.sql](supabase_schema.sql) y pulsa **Run**.

### 2) Obtener las credenciales
En tu proyecto de Supabase: **Project Settings → API**.
- **Project URL** → `SUPABASE_URL`
- **API keys → `service_role`** → `SUPABASE_KEY`
  (recomendado para este MVP: omite RLS y vive solo del lado servidor).

### 3) Configurar los secrets
Edita `.streamlit/secrets.toml` (local) y añade:

```toml
SUPABASE_URL   = "https://xxxxxxxxxxxx.supabase.co"
SUPABASE_KEY   = "eyJhbGciOi...."          # service_role
SUPABASE_TABLE = "registros_mamposteria"   # opcional
```

> Puedes dejar también el bloque de SharePoint: mientras Supabase esté
> configurado, **manda Supabase**.

### 4) Instalar dependencias y ejecutar
```powershell
venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```
En el sidebar debe aparecer **🟢 Conectado a Supabase**.

### 5) (Opcional) Migrar el histórico que ya tienes
```powershell
python migrar_a_supabase.py            # desde el Excel local
python migrar_a_supabase.py --sharepoint   # desde SharePoint
```

---

## ☁️ Desplegar en Streamlit Community Cloud

1. Sube el repo a GitHub **sin** `secrets.toml` (ya está en `.gitignore`).
2. Entra a [share.streamlit.io](https://share.streamlit.io) → **New app** → elige el repo y `app.py`.
3. **Advanced settings → Secrets** → pega tus secrets (al menos las 2 líneas de Supabase):
   ```toml
   SUPABASE_URL = "https://xxxxxxxxxxxx.supabase.co"
   SUPABASE_KEY = "eyJhbGciOi...."
   ```
4. **Deploy**. La app quedará pública en una URL `*.streamlit.app`.

> 💡 **Por qué Supabase facilita el despliegue**: a diferencia de SharePoint
> (que exige una App Registration de Azure con consentimiento de admin), Supabase
> solo necesita dos valores (URL + key). Ideal para publicar rápido y a costo $0.

---

## 🔐 Notas de seguridad

- `secrets.toml` **nunca** se sube a Git (`.gitignore`).
- La clave `service_role` da acceso total: úsala **solo** en los secrets del
  servidor (Streamlit Cloud), nunca en código del navegador. En Streamlit los
  secrets viven del lado servidor, así que es seguro.
- Si prefieres usar la clave `anon`, habilita **RLS** con políticas
  (ver el bloque comentado en [supabase_schema.sql](supabase_schema.sql)).

---

## 🧠 Detalle técnico

- **`agregar_registros()` vs `guardar_datos()`**: la app ahora **añade** solo las
  filas nuevas (`INSERT` en Supabase), en lugar de reescribir todo el archivo.
  Es más rápido y evita condiciones de carrera. SharePoint/Excel mantienen el
  comportamiento original (reescribir el archivo completo).
- Los nombres de columna en la tabla van **entre comillas** y respetan
  mayúsculas para coincidir 1:1 con `data_schema.COLUMNAS`.
- El cliente de Supabase se importa de forma **perezosa**: si usas SharePoint y
  no tienes instalada la librería `supabase`, la app no falla.
