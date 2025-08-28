# modules/app_auth_flow.py
from __future__ import annotations

import os
from typing import Optional, Dict
import requests
import streamlit as st
from google_auth_oauthlib.flow import Flow

# ----- Scopes completos para el Paso 0 (OIDC + Drive/Sheets + GSC)
SCOPES_PERSONAL_FULL = [
    "openid", "email", "profile",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

# ---------- helpers de query params ----------
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

# ---------- helpers de OAuth WEB ----------
def _get_web_oauth_config() -> tuple[Optional[str], Optional[str], Optional[str]]:
    auth = st.secrets.get("auth", {}) or {}
    cid = auth.get("client_id")
    csec = auth.get("client_secret")
    ruri = auth.get("redirect_uri")
    return cid, csec, ruri

def _build_flow_web(scopes: list[str]) -> Flow:
    cid, csec, ruri = _get_web_oauth_config()
    if not (cid and csec and ruri):
        raise RuntimeError(
            "No se pudo completar el inicio de sesión. Verificá el cliente web y el redirect_uri.\n"
            "Necesitás [auth].client_id, [auth].client_secret y [auth].redirect_uri en Secrets."
        )
    client_secrets = {
        "web": {
            "client_id": cid,
            "client_secret": csec,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [ruri],
        }
    }
    flow = Flow.from_client_config(client_secrets, scopes=scopes)
    flow.redirect_uri = ruri
    return flow

def _creds_to_dict_web(creds) -> dict:
    # Aseguramos guardar client_id/secret desde [auth] para los refresh
    cid, csec, _ = _get_web_oauth_config()
    return {
        "token": creds.token,
        "refresh_token": getattr(creds, "refresh_token", None),
        "token_uri": getattr(creds, "token_uri", "https://oauth2.googleapis.com/token"),
        "client_id": cid,
        "client_secret": csec,
        "scopes": list(getattr(creds, "scopes", SCOPES_PERSONAL_FULL)),
    }

def _fetch_userinfo(creds) -> Dict[str, str]:
    info: Dict[str, str] = {}
    try:
        resp = requests.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            j = resp.json()
            info = {
                "name": j.get("name") or j.get("email") or "Invitado",
                "email": j.get("email") or "—",
                "picture": j.get("picture"),
            }
    except Exception:
        pass
    return info

# ---------- Paso 0: Login con botón Google (web) ----------
def step0_google_identity() -> Optional[Dict[str, str]]:
    """
    UI minimalista del Paso 0:
      • Título + botón "Iniciar sesión con Google" (abre pestaña nueva).
      • Al volver con ?code&state, canjea token y guarda:
          - st.session_state["creds_dest"] (Drive/Sheets + GSC)
          - st.session_state["_google_identity"]
    """
    st.subheader("Inicia sesión con tu cuenta personal de Nomadic")

    # Pre-chequeo de config web
    cid, csec, ruri = _get_web_oauth_config()
    if not (cid and csec and ruri):
        st.error(
            "No se pudo completar el inicio de sesión. Verificá el cliente web y el redirect_uri.\n\n"
            "Asegurate de definir en Secrets:\n"
            "  [auth]\n"
            "  client_id = \"...\"\n"
            "  client_secret = \"...\"\n"
            "  redirect_uri = \"https://<tu-app>.streamlit.app\"\n"
        )
        return None

    # Construir o reutilizar flow + URL de autorización
    if "oauth_oidc" not in st.session_state:
        flow = _build_flow_web(SCOPES_PERSONAL_FULL)
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
        )
        st.session_state["oauth_oidc"] = {
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
            "redirect_uri": ruri,
        }

    oo = st.session_state["oauth_oidc"]

    # Botón único (pestaña nueva)
    st.markdown(
        f'<a href="{oo["auth_url"]}" target="_blank" rel="noopener">'
        f'<button type="button">Iniciar sesión con Google</button></a>',
        unsafe_allow_html=True,
    )

    # ¿Volvimos con code/state?
    qp = _get_qp()
    code = qp.get("code", [None])[0] if isinstance(qp.get("code"), list) else qp.get("code")
    state_in = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    if code and state_in:
        # Reconstruir la URL de redirección exacta para fetch_token
        from urllib.parse import urlencode
        current_url = f'{oo["redirect_uri"]}?{urlencode({k: v[0] if isinstance(v, list) else v for k, v in qp.items()}, doseq=True)}'

        # Rehidratación: si el flow no está (pestaña nueva) o el state no coincide, reconstruimos sin ruido.
        if st.session_state.get("DEBUG") and state_in != oo.get("state"):
            st.info("Aviso (DEBUG): state recibido distinto al generado; rehidratando flujo…")

        flow = oo.get("flow") or _build_flow_web(SCOPES_PERSONAL_FULL)
        try:
            flow.fetch_token(authorization_response=current_url)
            creds = flow.credentials

            # Guardar credenciales personales (Drive/Sheets + GSC) y la identidad
            st.session_state["creds_dest"] = _creds_to_dict_web(creds)
            ident = _fetch_userinfo(creds) or {"name": "Invitado", "email": "—", "picture": None}
            st.session_state["_google_identity"] = ident

            # Limpiar los query params feos
            _clear_qp()
            return ident
        except Exception as e:
            st.error("No se pudo completar el login. Volvé a intentarlo (un solo click).")
            if st.session_state.get("DEBUG"):
                st.caption(f"Detalle técnico: {e}")
            return None

    # Aún sin code/state: devolvemos lo que haya en sesión (o None)
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
        pass

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
                import shutil
                shutil.rmtree(".ext_pkgs", ignore_errors=True)

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