# modules/app_auth_flow.py
from __future__ import annotations
from typing import Optional
from urllib.parse import urlencode
import requests
import streamlit as st

from .auth import build_flow_web
from .utils import token_store

try:
    from .app_utils import get_qp, clear_qp  # usa tus helpers si existen
except Exception:
    def get_qp() -> dict:
        try:
            return dict(st.query_params)
        except Exception:
            return st.experimental_get_query_params()
    def clear_qp():
        try:
            st.query_params.clear()
        except Exception:
            st.experimental_set_query_params()

__all__ = ["step0_google_identity", "logout_screen"]

def step0_google_identity() -> Optional[dict]:
    """
    PASO 0 con botón (sin pegar URL):
    - Usa un OAuth client de TIPO WEB (defínelo aparte en secrets).
    - Redirige a Google y vuelve a esta app con ?code=...&state=...
    """
    st.subheader("0) Iniciar sesión con Google (identidad)")

    # Permitimos separar el client de OIDC del resto:
    acct_key = st.secrets.get("oauth_oidc_key", st.secrets.get("oauth_app_key", "ACCESO"))

    # Crear flow + URL (una vez por sesión)
    if "oauth_oidc" not in st.session_state:
        flow = build_flow_web(acct_key, ["openid", "email", "profile"])
        auth_url, state = flow.authorization_url(
            prompt="select_account",
            access_type="online",
            include_granted_scopes="true",
        )
        st.session_state["oauth_oidc"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
            "account_key": acct_key,
        }

    oo = st.session_state["oauth_oidc"]

    # ¿Volvimos con code+state?
    qp = get_qp()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    state_in = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    if code and state_in:
        # Aviso de state mismatch SOLO en DEBUG (no bloquea)
        if state_in != oo.get("state") and st.session_state.get("DEBUG"):
            st.info("Aviso: el 'state' no coincide (posible nueva pestaña). "
                    "Usando el flujo rehidratado con el state recibido…")

        # Reconstruir la URL exacta que recibió tu redirect_uri
        auth_response = f"{flow.redirect_uri}?{urlencode({k: v[0] if isinstance(v, list) else v for k,v in qp.items()}, doseq=True)}"

        try:
            flow = oo["flow"]
            flow.fetch_token(authorization_response=auth_response)
            creds = flow.credentials

            # UserInfo OIDC
            r = requests.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {creds.token}"},
                timeout=10,
            )
            info = r.json() if r.status_code == 200 else {}
            ident = {
                "name": info.get("name") or info.get("email") or "Invitado",
                "email": info.get("email") or "—",
                "picture": info.get("picture"),
            }
            st.session_state["_google_identity"] = ident
            clear_qp()
            st.success(f"Identidad verificada: {ident['email']}")
            return ident
        except Exception as e:
            st.error(f"No se pudo verificar identidad: {e}")
            st.stop()

    # Primer render (sin code/state): mostrar botón que abre Google en la misma pestaña
    st.markdown(
        f'<a href="{oo["auth_url"]}" target="_self">'
        f'<button type="button">Iniciar sesión con mi cuenta de Nomadic</button>'
        f'</a>',
        unsafe_allow_html=True,
    )
    st.caption("Serás redirigido nuevamente a esta app después de otorgar permisos.")
    return st.session_state.get("_google_identity")


# -------- Logout (sin cambios relevantes) --------
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
            try: st.cache_data.clear()
            except Exception: pass
            try: st.cache_resource.clear()
            except Exception: pass
            if wipe_pkg:
                import shutil; shutil.rmtree(".ext_pkgs", ignore_errors=True)
            for k in ["_auth_bypass","_google_identity","oauth_oidc","oauth_dest","oauth_src",
                      "creds_dest","creds_src","step1_done","step2_done","step3_done",
                      "dest_folder_id","src_account_label","site_url_choice","last_file_id","DEBUG"]:
                st.session_state.pop(k, None)
            try:
                token_store.clear("creds_dest"); token_store.clear("creds_src")
            except Exception:
                pass
            try:
                if hasattr(st, "logout"): st.logout()
            except Exception:
                pass
            st.success("Sesión cerrada y caché limpiada.")
            st.markdown(f"➡️ Volver a la app: [{app_home}]({app_home})")
            st.stop()
    with col2:
        if st.button("Cancelar"):
            st.markdown(f"[Volver a la app]({app_home})")