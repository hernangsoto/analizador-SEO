# modules/app_auth_flow.py
from __future__ import annotations

from typing import Optional, Dict
from urllib.parse import urlencode

import streamlit as st
import requests

# usamos los helpers/constantes ya existentes en tu proyecto
from .auth import (
    build_flow_web,           # cliente OAuth WEB
    SCOPES_PERSONAL_FULL,     # openid + email + profile + drive + sheets + gsc
    fetch_userinfo,           # obtiene {name,email,picture}
    is_redirect_ready,        # chequea que [auth] tenga client_id/client_secret/redirect_uri
)
from .app_utils import get_qp, clear_qp


def _simple_error(msg: str):
    st.error(msg)
    c1, _ = st.columns([1, 4])
    with c1:
        if st.button("Reiniciar Paso 0", key="btn_restart_oidc"):
            for k in ("oauth_oidc_web", "_google_identity"):
                st.session_state.pop(k, None)
            clear_qp()
            st.rerun()


def step0_google_identity() -> Optional[Dict[str, str]]:
    """
    Paso 0 (WEB flow, sin copiar URL):
    - Un único botón 'Iniciar sesión con Google'
    - Abre en pestaña nueva el consentimiento
    - Vuelve con code/state a redirect_uri y completa el login automáticamente
    - Guarda identidad en st.session_state["_google_identity"]
    """
    st.subheader("0) Inicia sesión con tu cuenta personal de Nomadic")

    # Verificar que tengamos config web en [auth]
    if not is_redirect_ready():
        _simple_error(
            "No se pudo completar el inicio de sesión. Verificá el cliente web y el redirect_uri."
        )
        return None

    # Construir flow + auth_url una sola vez
    if "oauth_oidc_web" not in st.session_state:
        flow = build_flow_web(SCOPES_PERSONAL_FULL)
        # ⚠️ MUY IMPORTANTE: include_granted_scopes como **string** "true"
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
            include_granted_scopes="true",
        )
        st.session_state["oauth_oidc_web"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
            "redirect_uri": flow.redirect_uri,
        }

    oo = st.session_state["oauth_oidc_web"]

    # Si volvimos desde Google (code/state en la URL)
    qp = get_qp()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    state_in = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    if code and state_in:
        # Validar state (si no coincide, mostramos solo en modo DEBUG)
        expected_state = oo.get("state")
        if state_in != expected_state:
            if st.session_state.get("DEBUG"):
                st.warning("Aviso: el 'state' no coincide. Reintentá el login (un solo clic).")
            _simple_error("No se pudo completar el login. Volvé a intentarlo (un solo click).")
            return None

        # Reconstruimos la URL EXACTA de redirección con los params actuales
        current_url = f"{oo['redirect_uri']}?{urlencode({k: v[0] if isinstance(v, list) else v for k, v in qp.items()}, doseq=True)}"

        try:
            flow = oo["flow"]
            flow.fetch_token(authorization_response=current_url)
            creds = flow.credentials
            ident = fetch_userinfo(creds)
            if not ident:
                _simple_error("No se pudo obtener la identidad del usuario.")
                return None

            # Guardar identidad y limpiar query params
            st.session_state["_google_identity"] = ident
            clear_qp()

            # UI minimalista de bienvenida
            st.success(f"Sesión iniciada: {ident.get('email', '—')}")
            return ident

        except Exception as e:
            _simple_error("No se pudo completar el login. Volvé a intentarlo (un solo click).")
            return None

    # --- UI: un solo botón que abre el flujo en pestaña nueva ---
    st.markdown(
        f'''
        <a href="{oo["auth_url"]}" target="_blank" rel="noopener">
            <button type="button">Iniciar sesión con Google</button>
        </a>
        ''',
        unsafe_allow_html=True
    )
    st.caption("Se abrirá en otra pestaña. Al finalizar, volverás automáticamente y continuamos.")

    # Devolver identidad si ya estaba en sesión (p. ej., tras un rerun)
    return st.session_state.get("_google_identity")


# --------------------------------------------------------------------
# Pantalla de LOGOUT (sin cambios funcionales — por compatibilidad)
# --------------------------------------------------------------------
def logout_screen(app_home: str = "?"):
    st.header("Cerrar sesión")
    ident = st.session_state.get("_google_identity") or {}
    current_email = ident.get("email") or "—"
    st.write(f"Usuario actual: **{current_email}**")

    revoke = st.checkbox("Revocar permisos de Google (Drive/Sheets y Search Console)", value=True)

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("🔒 Cerrar sesión y limpiar", type="primary"):
            try:
                # Limpiamos todo lo sensible
                for k in [
                    "_auth_bypass", "_google_identity",
                    "oauth_oidc_web", "oauth_oidc",
                    "oauth_dest", "oauth_src",
                    "creds_dest", "creds_src",
                    "step1_done", "step2_done", "step3_done",
                    "dest_folder_id", "src_account_label",
                    "site_url_choice", "last_file_id", "last_file_kind",
                    "sc_account_choice",
                ]:
                    st.session_state.pop(k, None)
                try:
                    st.cache_data.clear()
                except Exception:
                    pass
                try:
                    st.cache_resource.clear()
                except Exception:
                    pass
                st.success("Sesión cerrada y caché limpiada.")
                st.markdown(f"➡️ Volver al inicio: [{app_home}]({app_home})")
                st.stop()
            except Exception:
                st.error("No se pudo limpiar completamente la sesión. Refrescá la página e intentá de nuevo.")

    with col2:
        if st.button("Cancelar"):
            clear_qp()
            st.rerun()