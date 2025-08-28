# modules/app_auth_flow.py
from __future__ import annotations

import streamlit as st
from urllib.parse import urlencode
import requests

# ========= Helpers locales de query params =========
def _get_qp() -> dict:
    try:
        return dict(st.query_params)
    except Exception:
        return st.experimental_get_query_params()

def _clear_qp():
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()

# ========= Scopes (locales; no dependemos de modules.auth) =========
SCOPES_OIDC  = ["openid", "email", "profile"]
SCOPES_DRIVE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SCOPES_GSC   = ["https://www.googleapis.com/auth/webmasters.readonly"]

# Intentamos importar el creador de flujo WEB; si no está, avisamos en UI
try:
    from .auth import build_flow_web as _build_flow_web  # type: ignore
except Exception:
    _build_flow_web = None  # lo detectamos en tiempo de ejecución


def _is_redirect_ready() -> bool:
    """Hay client_id, client_secret y redirect_uri en [auth]?"""
    auth = st.secrets.get("auth", {}) or {}
    return bool(auth.get("client_id") and auth.get("client_secret") and auth.get("redirect_uri"))


def _fetch_userinfo(token: str) -> dict:
    """OIDC userinfo -> {name,email,picture} (silencioso ante errores)."""
    try:
        r = requests.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if r.status_code == 200:
            j = r.json()
            return {
                "name": j.get("name") or j.get("email") or "Invitado",
                "email": j.get("email") or "—",
                "picture": j.get("picture"),
            }
    except Exception:
        pass
    return {}


# =================================================================
# PASO 0 — Iniciar sesión con Google (flujo WEB, sin copiar URL)
# =================================================================
def step0_google_identity():
    """
    UI minimalista:
      - Título: iniciar sesión con cuenta personal de Nomadic
      - Un único botón 'Iniciar sesión con Google' (abre en pestaña nueva)
      - Procesa el retorno ?code=&state= automáticamente
      - Pide OIDC + Drive + Search Console (cuenta personal ya queda lista)
    """
    st.subheader("Inicia sesión con tu cuenta personal de Nomadic")

    if not _is_redirect_ready():
        st.error(
            "No se pudo completar el inicio de sesión. "
            "Verificá el **cliente web** y el **redirect_uri** en `[auth]` de Secrets."
        )
        st.caption("Requerido: `[auth].client_id`, `[auth].client_secret`, `[auth].redirect_uri`.")
        return None

    if _build_flow_web is None:
        st.error(
            "Falta `build_flow_web` en `modules/auth.py`. "
            "Actualizá ese módulo con la versión que define el flujo OAuth WEB."
        )
        return None

    scopes = [*SCOPES_OIDC, *SCOPES_DRIVE, *SCOPES_GSC]

    # Preparar flujo solo una vez
    if "oauth_oidc" not in st.session_state:
        flow = _build_flow_web(scopes)
        # ¡No uses include_granted_scopes para evitar el 400 del screenshot!
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
        )
        st.session_state["oauth_oidc"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
            "redirect_uri": flow.redirect_uri,
        }

    oo = st.session_state["oauth_oidc"]

    # Botón minimalista (abre Google en pestaña nueva)
    st.markdown(
        f'<a href="{oo["auth_url"]}" target="_blank">'
        '<button type="button">Iniciar sesión con Google</button></a>',
        unsafe_allow_html=True,
    )

    # Si volvemos redirigidos con ?code=&state=, completar login
    qp = _get_qp()
    code = qp.get("code"); state_in = qp.get("state")
    if isinstance(code, list): code = code[0] if code else None
    if isinstance(state_in, list): state_in = state_in[0] if state_in else None

    if code and state_in:
        expected = oo.get("state")
        if state_in != expected:
            # Solo detalle en DEBUG; UX corta en modo normal
            if st.session_state.get("DEBUG"):
                st.warning("Aviso: el 'state' no coincide (posible nueva pestaña).")
            st.error("No se pudo completar el login. Volvé a intentarlo (un solo click).")
            return None

        # Reconstruir la URL exacta de retorno (redirect_uri + query actual)
        qdict = {k: (v[0] if isinstance(v, list) else v) for k, v in qp.items()}
        current_url = f'{oo["redirect_uri"]}?{urlencode(qdict, doseq=True)}'

        try:
            flow = oo["flow"]
            flow.fetch_token(authorization_response=current_url)
            info = _fetch_userinfo(flow.credentials.token)
            st.session_state["_google_identity"] = info or {
                "name": "Invitado", "email": "—", "picture": None
            }
            # Limpiar code/state de la URL para dejar prolijo
            _clear_qp()
            st.success(f"Sesión iniciada: {st.session_state['_google_identity'].get('email')}")
            return st.session_state["_google_identity"]
        except Exception as e:
            if st.session_state.get("DEBUG"):
                st.error(f"Fallo de OAuth: {e}")
            else:
                st.error("No se pudo completar el login. Volvé a intentarlo (un solo click).")
            return None

    # Si ya teníamos identidad cacheada en otro render
    return st.session_state.get("_google_identity")


# =================================================================
# Pantalla de LOGOUT: revoca tokens, borra cachés y limpia sesión
# =================================================================
def _revoke_google_token(token: str | None) -> None:
    if not token:
        return
    try:
        requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": token},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
    except Exception:
        pass  # silencioso

def logout_screen(app_home: str | None = None):
    st.header("Cerrar sesión")
    ident = st.session_state.get("_google_identity") or {}
    current_email = ident.get("email") or "—"
    st.write(f"Usuario actual: **{current_email}**")

    revoke = st.checkbox("Revocar permisos de Google (Drive/Sheets y Search Console)", value=True)
    wipe_pkg = st.checkbox("Borrar caché del paquete externo (.ext_pkgs/)", value=False)

    col1, col2 = st.columns([1,1])
    with col1:
        if st.button("🔒 Cerrar sesión y limpiar", type="primary"):
            if revoke:
                for key in ("creds_dest", "creds_src"):
                    data = st.session_state.get(key)
                    if isinstance(data, dict):
                        _revoke_google_token(data.get("token") or data.get("refresh_token"))

            # Limpiar caches de Streamlit
            try: st.cache_data.clear()
            except Exception: pass
            try: st.cache_resource.clear()
            except Exception: pass

            # Borrar paquete externo (opcional)
            if wipe_pkg:
                import shutil
                shutil.rmtree(".ext_pkgs", ignore_errors=True)

            # Limpiar session_state
            for k in [
                "_auth_bypass", "_google_identity",
                "oauth_oidc", "oauth_dest", "oauth_src",
                "creds_dest", "creds_src",
                "step1_done", "step2_done", "step3_done",
                "dest_folder_id", "src_account_label",
                "site_url_choice", "last_file_id", "last_file_kind",
                "DEBUG",
            ]:
                st.session_state.pop(k, None)

            st.success("Sesión cerrada y caché limpiada.")
            if app_home:
                st.markdown(f"➡️ Volver a la app: [{app_home}]({app_home})")
            st.stop()

    with col2:
        if st.button("Cancelar"):
            _clear_qp()
            st.rerun()