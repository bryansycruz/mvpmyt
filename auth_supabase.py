"""
auth_supabase.py — Login de la app con Supabase Auth (email + contraseña)
─────────────────────────────────────────────────────────────────────────
$0 y SIN Azure: Supabase gestiona las cuentas. Los usuarios pueden registrarse
ellos mismos. Aquí solo se valida la IDENTIDAD; el acceso a los DATOS sigue
usando la clave anon (ver `supabase_connector.py`).

Reutiliza las mismas credenciales de los secrets:
    SUPABASE_URL = "https://xxxx.supabase.co"
    SUPABASE_KEY = "eyJhbGciOi..."   # clave anon

Interfaz:
    disponible()                 -> bool
    registrar(email, password)   -> (ok: bool, mensaje: str, necesita_confirmar: bool)
    iniciar_sesion(email, pwd)   -> (ok: bool, payload: str)
                                    payload = correo si ok, o mensaje de error si no.
"""

import streamlit as st


def disponible() -> bool:
    """True si hay URL y KEY de Supabase configurados."""
    try:
        url = str(st.secrets["SUPABASE_URL"]).strip()
        key = str(st.secrets["SUPABASE_KEY"]).strip()
    except Exception:
        return False
    return bool(url) and bool(key) and url.startswith("http")


def _cliente_auth():
    """Cliente NUEVO por operación (a propósito NO se cachea): la sesión de
    Supabase Auth vive en el objeto cliente y no debe compartirse entre
    distintos usuarios/sesiones de Streamlit.

    Usa `SUPABASE_ANON_KEY` si está configurada (recomendado cuando
    `SUPABASE_KEY` es la service_role para los DATOS): el login solo necesita
    la clave pública de auth, nunca la privilegiada. Sin esa clave extra,
    cae a `SUPABASE_KEY` (comportamiento original)."""
    from supabase import create_client  # import perezoso

    try:
        key = str(st.secrets["SUPABASE_ANON_KEY"]).strip()
    except Exception:
        key = ""
    if not key:
        key = str(st.secrets["SUPABASE_KEY"]).strip()
    return create_client(str(st.secrets["SUPABASE_URL"]).strip(), key)


def registrar(email: str, password: str):
    """Crea una cuenta nueva. Devuelve (ok, mensaje, necesita_confirmar).

    Si la confirmación de correo está DESACTIVADA en el proyecto, el alta
    devuelve sesión y el usuario puede entrar de inmediato (necesita_confirmar=False).
    Si está ACTIVADA, debe confirmar por correo antes de iniciar sesión.
    """
    email = (email or "").strip().lower()
    if not email or not password:
        return False, "Escribe correo y contraseña.", False
    try:
        res = _cliente_auth().auth.sign_up({"email": email, "password": password})
    except Exception as e:
        return False, _traducir_error(str(e)), False
    if getattr(res, "session", None):
        return True, "Cuenta creada. ¡Ya puedes entrar!", False
    return True, "Cuenta creada. Revisa tu correo para confirmarla y luego inicia sesión.", True


def iniciar_sesion(email: str, password: str):
    """Valida credenciales. Devuelve (ok, payload): payload = correo si ok,
    o el mensaje de error si falla."""
    email = (email or "").strip().lower()
    if not email or not password:
        return False, "Escribe correo y contraseña."
    try:
        res = _cliente_auth().auth.sign_in_with_password(
            {"email": email, "password": password}
        )
    except Exception as e:
        return False, _traducir_error(str(e))
    if getattr(res, "user", None):
        return True, email
    return False, "No se pudo iniciar sesión. Verifica tus datos."


def _traducir_error(msg: str) -> str:
    """Convierte los errores técnicos de Supabase a mensajes claros en español."""
    m = (msg or "").lower()
    if "invalid login" in m or "invalid_credentials" in m:
        return "Correo o contraseña incorrectos."
    if "already" in m and "regist" in m:
        return "Ese correo ya está registrado. Inicia sesión."
    if "email not confirmed" in m or "not confirmed" in m:
        return "Debes confirmar tu correo antes de entrar (revisa tu bandeja de entrada)."
    if "password" in m and ("6" in m or "should be" in m or "weak" in m):
        return "La contraseña debe tener al menos 6 caracteres."
    if "email" in m and ("invalid" in m or "valid" in m):
        return "El correo no es válido."
    if "rate limit" in m or "too many" in m:
        return "Demasiados intentos. Espera un momento e inténtalo de nuevo."
    return f"Error: {msg}"
