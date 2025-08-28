# modules/app_auth_flow.py
from __future__ import annotations

import os
import requests
from typing import Optional, Dict
import streamlit as st

# Importa SOLO lo que existe en tu modules/auth.py actual
from .auth import (
    build_flow_web,
    SCOPES_PERSONAL_FULL,
    fetch_userinfo,
    is_redirect_ready,
    creds_to_dict,
)

# ---------- helpers locales de query params ----------
def _get_qp() -> dict:
    try:
        return dict(st.query_params)
    except Exception:
        return st.experimental_get_query_params()

def _clear_qp():
    # limpia code/state de la URL tras el login
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()

# ---------- Paso 0: Login botón Google (cliente WEB) ----------
def step0_google_identity() -> Optional[Dict[str, str]]:
    """
    Paso 0 minimalista:
    - Un solo botón "Iniciar sesión con Google" (pestaña nueva).
    - Usa cliente WEB definido en [auth] (redirect_uri a tu app de Streamlit).
    - Pide OIDC + Drive/Sheets + GSC (SCOPES_PERSONAL_FULL).
    - Al volver con ?code&state, canjea token y guarda:
        st.session_state["creds_dest"] y st.session_state["_google_identity"]
    """
    st.subheader("Inicia sesión con tu cuenta personal de Nomadic")

    # Verifica que haya config WEB (client_id/secret/redirect_uri en [auth])
    if not is_redirect_ready():
        st.error(
            "No se pudo completar el inicio de sesión. Verificá el cliente web y el redirect_uri.\n\n"
            "Asegurate de tener en secrets:\n"
            "[auth].client_id, [auth].client_secret, [auth].redirect_uri\n"
            "Ej.: https://<tu-app>.streamlit.app"
        )
        return None

    # Construye (o reutiliza) el flow WEB y la URL de autorización
    if "oauth_oidc" not in st.session_state:
        flow = build_flow_web(SCOPES_PERSONAL_FULL)  # OIDC + Drive + GSC
        # No pasamos include_granted_scopes para evitar incompatibilidades
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
        )
        redirect_uri = (st.secrets.get("auth") or {}).get("redirect_uri", "")
        st.session_state["oauth_oidc"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
            "redirect_uri": redirect_uri,
        }

    oo = st.session_state["oauth_oidc"]

    # === UI minimalista: un solo botón que abre Google en pestaña nueva ===
    st.markdown(
        f'<a href="{oo["auth_url"]}" target="_blank" rel="noopener">'
        f'<button type="button">Iniciar sesión con Google</button></a>',
        unsafe_allow_html=True,
    )

    # === ¿Volvimos con code/state? Intercambiar por tokens ===
    qp = _get_qp()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    state_in = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    if code and state_in:
        expected_state = oo.get("state")
        # Mostrar el aviso SOLO en DEBUG si el state no coincide
        if state_in != expected_state and st.session_state.get("DEBUG"):
            st.info("Aviso (DEBUG): el 'state' recibido no coincide. Rehidratando flujo con el state devuelto…")

        # Reconstruir URL completa EXACTA (redirect_uri + query actual)
        from urllib.parse import urlencode
        current_url = f'{oo["redirect_uri"]}?{urlencode({k: v[0] if isinstance(v, list) else v for k, v in qp.items()}, doseq=True)}'

        try:
            # Si por algún motivo el flow no está, lo reconstruimos (rehidratación)
            flow = oo.get("flow")
            if flow is None:
                flow = build_flow_web(SCOPES_PERSONAL_FULL)
                st.session_state["oauth_oidc"]["flow"] = flow

            flow.fetch_token(authorization_response=current_url)
            creds = flow.credentials
            # Guardamos las credenciales personales (Drive/Sheets + GSC)
            st.session_state["creds_dest"] = creds_to_dict(creds)

            # Obtener identidad (nombre/email/foto)
            ident = fetch_userinfo(creds)
            if not ident:
                ident = {"name": "Invitado", "email": "—", "picture": None}
            st.session_state["_google_identity"] = ident

            # Limpiar code/state de la URL
            _clear_qp()
            return ident
        except Exception as e:
            st.error("No se pudo completar el login. Volvé a intentarlo (un solo click).")
            if st.session_state.get("DEBUG"):
                st.caption(f"Detalle técnico: {e}")
            return None

    # Si aún no hay identidad, devolvemos lo que hubiese en sesión (o None)
    return st.session_state.get("_google_identity")

# ---------- Logout ----------
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
        pass  # no hacemos ruido si falla

def logout_screen(app_home_url: str = "?"):
    st.header("Cerrar sesión")
    ident = st.session_state.get("_google_identity") or {}
    current_email = ident.get("email") or "—"
    st.write(f"Usuario actual: **{current_email}**")

    revoke = st.checkbox("Revocar permisos de Google (Drive/Sheets y Search Console)", value=True)
    wipe_pkg = st.checkbox("Borrar caché del paquete externo (.ext_pkgs/)", value=False)

    col1, col2 = st.columns([1,1])
    with col1:
        if st.button("🔒 Cerrar sesión y limpiar", type="primary"):
            # Revocar tokens (si se solicita)
            if revoke:
                for key in ("creds_dest", "creds_src"):
                    data = st.session_state.get(key)
                    if isinstance(data, dict):
                        _revoke_google_token(data.get("token") or data.get("refresh_token"))

            # Borrar cachés
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
                "site_url_choice", "last_file_id",
                "DEBUG",
            ]:
                st.session_state.pop(k, None)

            _clear_qp()
            st.success("Sesión cerrada y caché limpiada.")
            st.markdown(f"➡️ Volver a la app: [{app_home_url}]({app_home_url})")
            st.stop()

    with col2:
        if st.button("Cancelar"):
            _clear_qp()
            st.rerun()