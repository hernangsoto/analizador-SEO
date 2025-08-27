# modules/app_auth_flow.py
from __future__ import annotations

import os
from typing import Optional, Dict
from urllib.parse import urlencode

import requests
import streamlit as st

# Usamos helpers del módulo auth
from .auth import (
    build_flow_web,
    is_redirect_ready,
    SCOPES_PERSONAL_FULL,
    fetch_userinfo,
    creds_to_dict,
)
from .utils import token_store

# ------------------------------
# Helpers locales de query params
# ------------------------------
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

# ------------------------------
# Paso 0: Login con Google (web)
# Pide OIDC + Drive/Sheets + Search Console
# ------------------------------
def step0_google_identity() -> Optional[Dict[str, str]]:
    """
    Autentica la cuenta PERSONAL con un cliente OAuth **web** y solicita:
      - OIDC (openid, email, profile)
      - Drive/Sheets
      - Search Console (readonly)

    • No se muestra campo para pegar URL.
    • Si el 'state' no coincide, lo reintentamos silenciosamente; solo
      en modo DEBUG mostramos el aviso.
    • Al finalizar, guarda las credenciales en session_state y token_store
      como 'creds_dest' y marca step1_done=True para saltear el viejo Paso 1.
    """
    st.subheader("0) Iniciar sesión con Google ↩︎")

    acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")
    if not is_redirect_ready(acct_for_dest):
        st.error(
            "Falta configurar el redirect_uri o el cliente OAuth Web.\n"
            "En `[auth]` definí client_id, client_secret y redirect_uri que apunte a esta app."
        )
        return None

    # Inicializar flujo (una sola vez por sesión)
    if "oauth_oidc" not in st.session_state:
        flow = build_flow_web(account_key=acct_for_dest, scopes=SCOPES_PERSONAL_FULL)
        auth_url, state = flow.authorization_url(
            prompt="select_account consent",
            access_type="offline",
            include_granted_scopes="true",  # string, no bool
        )
        st.session_state["oauth_oidc"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
            "redirect_uri": (st.secrets.get("auth") or {}).get("redirect_uri"),
            "account_key": acct_for_dest,
        }

    oo = st.session_state["oauth_oidc"]

    # ¿Volvimos de Google con code+state?
    qp = _get_qp()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    state_in = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    if code and state_in:
        # Reconstruir la URL EXACTA que Google llamó (redirect_uri + QS actual)
        current_url = f"{oo['redirect_uri']}?{urlencode({k: (v[0] if isinstance(v, list) else v) for k,v in qp.items()}, doseq=True)}"

        try:
            # Intento normal con el flow guardado
            flow = oo["flow"]
            flow.fetch_token(authorization_response=current_url)
        except Exception as e:
            # Si hay problema de 'state', rehidratamos el flow y reintentamos
            if "state" in str(e).lower():
                if st.session_state.get("DEBUG"):
                    st.warning("Aviso (DEBUG): el 'state' no coincidió; rehidratando flujo con el state recibido…")
                # Crear un flow fresco con los mismos scopes y redirect
                flow = build_flow_web(account_key=oo["account_key"], scopes=SCOPES_PERSONAL_FULL)
                flow.redirect_uri = oo["redirect_uri"]
                flow.fetch_token(authorization_response=current_url)
            else:
                st.error("No se pudo completar el login. Volvé a intentarlo (un solo click).")
                if st.button("Reiniciar Paso 0", key="btn_restart_step0"):
                    st.session_state.pop("oauth_oidc", None)
                    st.session_state.pop("_google_identity", None)
                    _clear_qp()
                    st.rerun()
                st.stop()

        # Tokens OK → guardar
        creds = flow.credentials
        ident = fetch_userinfo(creds) or {}
        st.session_state["_google_identity"] = ident

        data = creds_to_dict(creds)
        st.session_state["creds_dest"] = data
        token_store.save("creds_dest", data)

        # Marcar como listo el viejo Paso 1 (Drive/Sheets)
        st.session_state["step1_done"] = True

        # Limpiar la URL de code/state
        _clear_qp()

        st.success(f"Sesión iniciada: {ident.get('email','(sin email)')}")
        return ident

    # UI inicial: un botón que redirige a Google en la MISMA pestaña
    st.markdown(
        f'<a href="{oo["auth_url"]}" target="_self"><button type="button">Continuar con Google</button></a>',
        unsafe_allow_html=True
    )

    # Botón de reinicio del paso (por si quedó mal un estado previo)
    if st.button("Reiniciar Paso 0", key="btn_reset_oidc"):
        st.session_state.pop("oauth_oidc", None)
        st.session_state.pop("_google_identity", None)
        _clear_qp()
        st.rerun()

    return st.session_state.get("_google_identity")

# ------------------------------
# Pantalla de LOGOUT (sin cambios funcionales)
# ------------------------------
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
        pass

def logout_screen(app_home: str):
    st.header("Cerrar sesión")
    ident = st.session_state.get("_google_identity") or {}
    current_email = ident.get("email") or "—"
    st.write(f"Usuario actual: **{current_email}**")

    revoke = st.checkbox("Revocar permisos de Google (Drive/Sheets y Search Console)", value=True)
    wipe_pkg = st.checkbox("Borrar caché del paquete externo (.ext_pkgs/)", value=False)

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("🔒 Cerrar sesión y limpiar", type="primary"):
            if revoke:
                for key in ("creds_dest", "creds_src"):
                    data = st.session_state.get(key) or {}
                    if isinstance(data, dict):
                        _revoke_google_token(data.get("token") or data.get("refresh_token"))

            try: st.cache_data.clear()
            except Exception: pass
            try: st.cache_resource.clear()
            except Exception: pass

            if wipe_pkg:
                import shutil
                shutil.rmtree(".ext_pkgs", ignore_errors=True)

            for k in [
                "_auth_bypass","_google_identity",
                "oauth_oidc","oauth_dest","oauth_src",
                "creds_dest","creds_src",
                "step1_done","step2_done","step3_done",
                "dest_folder_id","src_account_label",
                "site_url_choice","last_file_id","last_file_kind",
                "DEBUG",
            ]:
                st.session_state.pop(k, None)

            try:
                token_store.clear("creds_dest")
                token_store.clear("creds_src")
            except Exception:
                pass

            st.success("Sesión cerrada y caché limpiada.")
            st.markdown(f"➡️ Volver a la app: [{app_home}]({app_home})", unsafe_allow_html=True)
            st.stop()

    with col2:
        if st.button("Cancelar"):
            _clear_qp()
            st.rerun()