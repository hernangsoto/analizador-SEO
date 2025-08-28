# modules/app_auth_flow.py
from __future__ import annotations

import os
from urllib.parse import urlencode
import streamlit as st

from .app_utils import get_qp, clear_qp
from .auth import (
    build_flow_web,
    SCOPES_OIDC, SCOPES_DRIVE, SCOPES_GSC,
    is_redirect_ready,
    fetch_userinfo,
    creds_to_dict,
    get_cached_personal_creds,
)

# ----- Paso 0: Login con Google (flujo WEB, sin copiar URL) -----
def step0_google_identity():
    """
    Muestra un único botón de 'Iniciar sesión con Google'.
    Usa cliente OAuth WEB configurado en [auth] con redirect_uri a la app pública.
    Pide scopes: OIDC + Drive + GSC (para que la cuenta personal ya quede lista).
    No mostramos mensajes verbosos; el aviso de 'state mismatch' solo aparece en DEBUG.
    """
    st.subheader("Inicia sesión con tu cuenta personal de Nomadic")

    # ¿Está listo el cliente web?
    if not is_redirect_ready():
        st.error(
            "No se pudo completar el inicio de sesión. "
            "Verificá el **cliente web** y el **redirect_uri** en `[auth]` de Secrets."
        )
        st.caption(
            "Requerido: `[auth].client_id`, `[auth].client_secret`, "
            "`[auth].redirect_uri` (URL pública de tu app, sin slash final)."
        )
        return None

    # Scopes para la cuenta personal: identidad + Drive + Search Console
    scopes = [*SCOPES_OIDC, *SCOPES_DRIVE, *SCOPES_GSC]

    # Preparar flujo solo una vez
    if "oauth_oidc" not in st.session_state:
        flow = build_flow_web(scopes)
        # ¡NO agregar include_granted_scopes para evitar 400!
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

    # Botón minimalista (abre en pestaña nueva)
    st.markdown(
        f'<a href="{oo["auth_url"]}" target="_blank">'
        '<button type="button">Iniciar sesión con Google</button>'
        '</a>',
        unsafe_allow_html=True,
    )

    # ¿Volvimos redirigidos con code/state?
    qp = get_qp()
    code = qp.get("code")
    state_in = qp.get("state")
    if isinstance(code, list): code = code[0] if code else None
    if isinstance(state_in, list): state_in = state_in[0] if state_in else None

    if code and state_in:
        expected = oo.get("state")
        if state_in != expected:
            # Solo mostramos detalle en DEBUG; si no, mensaje genérico.
            if st.session_state.get("DEBUG"):
                st.warning("Aviso: el 'state' no coincide (posible nueva pestaña).")
            st.error("No se pudo completar el login. Volvé a intentarlo (un solo click).")
            return None

        # Reconstruir la URL exacta de retorno (redirect_uri + query actual)
        current_url = f'{oo["redirect_uri"]}?{urlencode({k:(v[0] if isinstance(v,list) else v) for k,v in qp.items()}, doseq=True)}'

        try:
            flow = oo["flow"]
            flow.fetch_token(authorization_response=current_url)
            # Guardamos identidad básica
            info = fetch_userinfo(flow.credentials)
            st.session_state["_google_identity"] = info or {
                "name": "Invitado", "email": "—", "picture": None
            }
            # Limpiar code/state de la barra
            clear_qp()
            # Señal de OK para el usuario
            st.success(f"Sesión iniciada: {st.session_state['_google_identity'].get('email')}")
            return st.session_state["_google_identity"]
        except Exception as e:
            st.error("No se pudo completar el login. Volvé a intentarlo (un solo click).")
            if st.session_state.get("DEBUG"):
                st.caption(f"Detalle técnico: {e}")
            return None

    # Si ya teníamos identidad cacheada en otro render
    return st.session_state.get("_google_identity")


# ----- Pantalla de logout -----
def _revoke_google_token(token: str | None) -> None:
    if not token:
        return
    try:
        import requests
        requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": token},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
    except Exception:
        pass

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
                # Revocar tokens conocidos
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
            clear_qp()
            st.rerun()