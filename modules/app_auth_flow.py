# modules/app_auth_flow.py
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit, parse_qs

import requests
import streamlit as st
from google.oauth2.credentials import Credentials

from .auth import build_flow
from .utils import token_store


__all__ = ["step0_google_identity", "logout_screen"]


def step0_google_identity() -> Optional[dict]:
    """
    PASO 0 (versión clásica): login con Google OIDC usando flujo manual.
    - Muestra link a Google
    - El usuario pega aquí la URL http://localhost/?code=...&state=...
    - Validamos 'state', canjeamos el 'code' y consultamos /userinfo
    - Guardamos identidad en st.session_state["_google_identity"]
    """
    st.subheader("0) Iniciar sesión con Google (identidad)")

    acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")

    # Crear flow + URL de autorización una sola vez
    if "oauth_oidc" not in st.session_state:
        flow = build_flow(acct_for_dest, ["openid", "email", "profile"])
        auth_url, state = flow.authorization_url(
            prompt="select_account",
            access_type="online",
            include_granted_scopes="true",  # string, no bool
        )
        st.session_state["oauth_oidc"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
            "account_key": acct_for_dest,
        }

    oo = st.session_state["oauth_oidc"]

    # UI: link + expander con la URL
    st.markdown(f"🔗 **Paso A (identidad):** [Iniciar sesión con Google]({oo['auth_url']})")
    with st.expander("Ver/copiar URL de autorización (identidad)"):
        st.code(oo["auth_url"])

    # Campo para pegar la URL de redirección
    url = st.text_input(
        "🔑 Paso B (identidad): pegá la URL completa (http://localhost/?code=...&state=...)",
        key="auth_response_url_oidc",
        placeholder="http://localhost/?code=...&state=...",
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Verificar identidad", type="primary", key="btn_oidc_connect"):
            if not url.strip():
                st.error("Pegá la URL completa de redirección (incluye code y state).")
                st.stop()

            # Validar 'state' ANTES de fetch_token
            try:
                qs = parse_qs(urlsplit(url.strip()).query)
                returned_state = (qs.get("state") or [""])[0]
            except Exception:
                returned_state = ""
            expected_state = oo.get("state")
            if not returned_state or returned_state != expected_state:
                st.error("CSRF Warning: el 'state' devuelto no coincide con el generado.")
                st.stop()

            # Intercambiar code por tokens y pedir userinfo
            try:
                flow = oo["flow"]
                flow.fetch_token(authorization_response=url.strip())
                creds = flow.credentials
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
                st.success(f"Identidad verificada: {ident['email']}")
                return ident
            except Exception as e:
                st.error(f"No se pudo verificar identidad: {e}")
                st.stop()

    with col2:
        if st.button("Reiniciar Paso 0", key="btn_reset_oidc"):
            st.session_state.pop("oauth_oidc", None)
            st.session_state.pop("_google_identity", None)
            st.rerun()

    return st.session_state.get("_google_identity")


# ------------------------------
# Pantalla de LOGOUT (sin cambios)
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


def logout_screen(app_home: str) -> None:
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
                    data = st.session_state.get(key)
                    if isinstance(data, dict):
                        _revoke_google_token(data.get("token") or data.get("refresh_token"))

            # Limpiar caches
            try: st.cache_data.clear()
            except Exception: pass
            try: st.cache_resource.clear()
            except Exception: pass

            # Borrar paquete externo (opcional)
            if wipe_pkg:
                import shutil
                shutil.rmtree(".ext_pkgs", ignore_errors=True)

            # Limpiar session_state relevante
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

            # Limpiar token_store
            try:
                token_store.clear("creds_dest")
                token_store.clear("creds_src")
            except Exception:
                pass

            # Intentar cerrar sesión de Streamlit (si aplica)
            try:
                if hasattr(st, "logout"):
                    st.logout()
            except Exception:
                pass

            st.success("Sesión cerrada y caché limpiada.")
            st.markdown(f"➡️ Volver a la app: [{app_home}]({app_home})")
            st.stop()

    with col2:
        if st.button("Cancelar"):
            # Volver a home sin limpiar nada
            st.markdown(f"[Volver a la app]({app_home})")