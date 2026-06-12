# 🔐 Login con Supabase Auth (email + contraseña) — MVP

La app exige **iniciar sesión** antes de mostrar nada. El login usa **Supabase Auth**
(correo + contraseña), **costo $0 y sin Azure**. Supabase gestiona las cuentas y las
contraseñas (nosotros nunca las vemos). Los usuarios pueden **registrarse solos**.

## ¿Con qué entran?
Con un **correo + contraseña** que cada quien crea en la pestaña **"Registrarse"**.
Sirve cualquier correo (Outlook, Gmail, etc.) — no depende de Microsoft.

Quién puede entrar se controla con `CORREOS_AUTORIZADOS` en `secrets.toml`:
- **Vacío** (`""`) ⇒ entra cualquiera que se registre. (Bien para un MVP abierto.)
- Con correos ⇒ solo esos. Ej: `CORREOS_AUTORIZADOS = "juan@x.com, ana@y.com"`

---

## ✅ Lo que YA quedó hecho (en código)
- `auth_supabase.py` → registrar / iniciar sesión / cerrar sesión con Supabase Auth.
- `app.py` → `requerir_login()` bloquea la app + pantalla con pestañas
  **Iniciar sesión / Registrarse** + botón "Cerrar sesión" en el sidebar.
- Usa las **mismas** `SUPABASE_URL` / `SUPABASE_KEY` (clave anon) del backend de datos.

## 🛠️ Único ajuste recomendado en el panel de Supabase (1 clic)
Para que el MVP fluya sin pedir confirmación por correo en cada registro:

**Supabase → tu proyecto `mamposteria MyT` → Authentication → Sign In / Providers →
Email → desactiva "Confirm email" → Save.**

- Con eso, al **Registrarse** el usuario entra de inmediato.
- Si lo dejas activado (más seguro), tras registrarse recibe un correo y debe
  confirmar antes de iniciar sesión. El código maneja ambos casos.

> No hace falta nada más: ni Azure, ni dominios, ni tarjetas. Todo $0.

---

## ▶️ Probar local
```powershell
venv\Scripts\Activate.ps1
streamlit run app.py
```
Verás la pantalla de **Iniciar sesión / Registrarse**. Crea una cuenta y entra.

## ☁️ Desplegar en Streamlit Community Cloud
1. Sube el repo a GitHub (sin `secrets.toml`, ya está en `.gitignore`).
2. share.streamlit.io → New app → elige el repo y `app.py`.
3. **Advanced settings → Secrets**: pega tu `secrets.toml` (al menos las 2 líneas de
   Supabase + `CORREOS_AUTORIZADOS`).
4. Deploy. Funciona igual que en local (Supabase Auth no necesita redirect URIs).

---

## 🔗 Datos del proyecto Supabase
- La URL y el ref del proyecto están en el panel de Supabase (**Settings → API**)
  y en `secrets.toml` — no se documentan aquí porque este archivo se versiona.
- Tabla de datos: `registros_mamposteria` (RLS activado; ver endurecimiento en
  `supabase_schema.sql`).
- Las cuentas de usuario viven en **Authentication → Users** del panel de Supabase.

## 🗒️ Notas
- **SharePoint queda pendiente** (no es prioridad): su config sigue en `secrets.toml`
  pero NO se usa, porque Supabase tiene prioridad en `data_backend.py`.
- El acceso a datos usa la clave **anon**; el login solo valida la identidad. Para un
  MVP es suficiente. Si más adelante quieres atar cada registro a su usuario, se puede
  endurecer RLS con `auth.uid()` y pasar el token del usuario al cliente de datos.
