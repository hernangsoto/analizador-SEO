# modules/app_auth_flow.py
from __future__ import annotations

import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from typing import Optional, Dict
from urllib.parse import urlencode
import requests
import streamlit as st
from google_auth_oauthlib.flow import Flow

from .auth import build_flow_web, SCOPES_OIDC, is_redirect_ready
from .utils import token_store

def _get_qp() -> Dict[str, str]:
    try:
        qp = dict(st.query_params)
    except Exception:
        qp = st.experimental_get_query_params()
    return {k: (v[0] if isinstance(v, list) else v) for k, v in qp.items()}

def _clear_qp():
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()

def step0_google_identity() -> Optional[dict]:
    """Paso 0 SOLO con botón (flujo web + redirect)."""
    st.subheader("0) Iniciar sesión con Google (identidad)")

    acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")

    if not is_redirect_ready(acct_for_dest):
        st.error(
            "Falta configurar el **redirect_uri** o el **cliente OAuth Web**.\n\n"
            "- secrets `[auth].redirect_uri` debe apuntar a tu app (p. ej., `https://<tu-app>.streamlit.app/`).\n"
            "- en `[accounts].<key>` definí `web_client_id` y `web_client_secret` (o sus alias)."
        )
        st.stop()

    if "oauth_oidc" not in st.session_state:
        flow = build_flow_web(acct_for_dest, SCOPES_OIDC)
        auth_url, state = flow.authorization_url(prompt="select_account", access_type="online")
        st.session_state["oauth_oidc"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
        }

    oo = st.session_state["oauth_oidc"]

    # ¿Volvemos de Google?
    qp = _get_qp()
    code = qp.get("code")
    state_in = qp.get("state")
    if code and state_in:
        expected = oo.get("state")
        # Si el state no coincide, en producción seguimos callados; en DEBUG puedes loguearlo si quieres.
        state_ok = (state_in == expected)
        flow: Flow = oo["flow"]
        # reconstruir exactamente la URL de redirección actual
        current_uri = flow.redirect_uri
        if "?" in current_uri:
            current_uri = current_uri.split("?", 1)[0]
        auth_response = f"{current_uri}?{urlencode(qp)}"
        if not state_ok and st.session_state.get("DEBUG"):
            st.info("Aviso DEBUG: state no coincide; continuando con flujo rehidratado.")

        try:
            flow.fetch_token(authorization_response=auth_response)
            creds = flow.credentials
            # userinfo
            resp = requests.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {creds.token}"},
                timeout=10,
            )
            info = resp.json() if resp.status_code == 200 else {}
            ident = {
                "name": info.get("name") or info.get("email") or "Invitado",
                "email": info.get("email") or "—",
                "picture": info.get("picture"),
            }
            st.session_state["_google_identity"] = ident
            _clear_qp()
            st.success(f"Identidad verificada: {ident['email']}")
            return ident
        except Exception as e:
            st.error(f"No se pudo verificar identidad: {e}")
            st.stop()

    # UI: SOLO botón
    st.markdown(
        f'<a href="{oo["auth_url"]}" target="_self"><button type="button">Continuar con Google</button></a>',
        unsafe_allow_html=True,
    )
    st.caption("Serás redirigido automáticamente a esta app después de otorgar permisos.")
    return st.session_state.get("_google_identity")


# -------- Logout (igual que antes) --------
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

def logout_screen(home_url: str | None = None):
    st.header("Cerrar sesión")
    ident = st.session_state.get("_google_identity") or {}
    current_email = ident.get("email") or "—"
    st.write(f"Usuario actual: **{current_email}**")

    revoke = st.checkbox("Revocar permisos de Google (Drive/Sheets y Search Console)", value=True)
    wipe_pkg = st.checkbox("Borrar caché del paquete externo (.ext_pkgs/)", value=False)

    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("🔒 Cerrar sesión y limpiar", type="primary"):
            if revoke:
                for key in ("creds_dest", "creds_src"):
                    data = st.session_state.get(key)
                    if isinstance(data, dict):
                        _revoke_google_token(data.get("token") or data.get("refresh_token"))

            try: st.cache_data.clear()
            except Exception: pass
            try: st.cache_resource.clear()
            except Exception: pass

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

            try:
                token_store.clear("creds_dest")
                token_store.clear("creds_src")
            except Exception:
                pass

            try:
                if hasattr(st, "logout"):
                    st.logout()
            except Exception:
                pass

            st.success("Sesión cerrada y caché limpiada.")
            home = home_url or "?"
            st.markdown(f"➡️ Volver a la app: [Inicio]({home})")
            st.stop()

    with c2:
        if st.button("Cancelar"):
            try:
                st.query_params.clear()
            except Exception:
                st.experimental_set_query_params()
            st.rerun()