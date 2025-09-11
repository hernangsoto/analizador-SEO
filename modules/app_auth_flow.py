# modules/app_auth_flow.py
from __future__ import annotations

from typing import Optional, Dict
import requests
import streamlit as st
from google_auth_oauthlib.flow import Flow

# Guardado cross-pestaña
from .utils import token_store

DOCS_SCOPE = "https://www.googleapis.com/auth/documents"

# Scopes para Paso 0 (OIDC + Drive/Sheets + GSC)
SCOPES_PERSONAL_FULL = [
    "openid", "email", "profile",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
    DOCS_SCOPE,
]

def _get_scopes_for_step0() -> list[str]:
    """Permite sobreescribir scopes desde secrets; si no hay, usa los por defecto."""
    cfg = (st.secrets.get("auth") or {})
    custom = cfg.get("scopes") or []
    scopes = custom if custom else SCOPES_PERSONAL_FULL
    # dedup conservando orden
    seen, out = set(), []
    for s in scopes:
        if s not in seen:
            out.append(s); seen.add(s)
    return out

# ---- helpers de query params
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

# ---- helpers OAuth WEB
def _get_web_oauth_config() -> tuple[Optional[str], Optional[str], Optional[str]]:
    auth = st.secrets.get("auth", {}) or {}
    return auth.get("client_id"), auth.get("client_secret"), auth.get("redirect_uri")

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
    cid, csec, _ = _get_web_oauth_config()
    asked_scopes = _get_scopes_for_step0()
    got_scopes = list(getattr(creds, "scopes", []) or asked_scopes)
    return {
        "token": creds.token,
        "refresh_token": getattr(creds, "refresh_token", None),
        "token_uri": getattr(creds, "token_uri", "https://oauth2.googleapis.com/token"),
        "client_id": cid,
        "client_secret": csec,
        "scopes": got_scopes,
    }

def _fetch_userinfo(creds) -> Dict[str, str]:
    info: Dict[str, str] = {}
    try:
        r = requests.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=10,
        )
        if r.status_code == 200:
            j = r.json()
            info = {
                "name": j.get("name") or j.get("email") or "Invitado",
                "email": j.get("email") or "—",
                "picture": j.get("picture"),
            }
    except Exception:
        pass
    return info

# ---- PASO 0
def step0_google_identity() -> Optional[Dict[str, str]]:
    st.subheader("Inicia sesión con tu cuenta personal de Nomadic")

    cid, csec, ruri = _get_web_oauth_config()
    if not (cid and csec and ruri):
        st.error(
            "No se pudo completar el inicio de sesión. Verificá el cliente web y el redirect_uri.\n\n"
            "Secrets esperados:\n[auth]\nclient_id=...\nclient_secret=...\nredirect_uri=https://<tu-app>.streamlit.app"
        )
        return None

    # Construir una sola vez la URL de autorización
    if "oauth_oidc" not in st.session_state:
        flow = _build_flow_web(_get_scopes_for_step0())
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
            include_granted_scopes=True,  # ⬅️ permite pedir scopes incrementales
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

    # ¿volvimos con ?code&
    qp = _get_qp()
    code  = qp.get("code",  [None])[0] if isinstance(qp.get("code"), list)  else qp.get("code")
    state = qp.get("state", [None])[0] if isinstance(qp.get("state"), list) else qp.get("state")

    if code and state:
        # Usar SIEMPRE fetch_token(code=...) (evita problemas de URL exacta)
        flow = oo.get("flow") or _build_flow_web(SCOPES_PERSONAL_FULL)
        try:
            flow.fetch_token(code=code)
            creds = flow.credentials

            data = _creds_to_dict_web(creds)
            st.session_state["creds_dest"] = data
            token_store.save("creds_dest", data)  # comparte con otras pestañas

            ident = _fetch_userinfo(creds) or {"name": "Invitado", "email": "—", "picture": None}
            st.session_state["_google_identity"] = ident

            _clear_qp()
            return ident
        except Exception as e:
            # Mostrar SIEMPRE el detalle para diagnosticar (lo verás debajo del cartel rojo)
            st.error("No se pudo completar el login. Volvé a intentarlo (un solo click).")
            st.caption(f"Detalle técnico: {e}")
            return None

    # Sin code: devolver identidad si ya existe
    return st.session_state.get("_google_identity")

# ---- Logout
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

    c1, c2 = st.columns([1,1])
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

            try:
                token_store.clear("creds_dest")
                token_store.clear("creds_src")
            except Exception:
                pass

            _clear_qp()
            st.success("Sesión cerrada y caché limpiada.")
            st.markdown(f"➡️ Volver a la app: [{app_home_url}]({app_home_url})")
            st.stop()

    with c2:
        if st.button("Cancelar"):
            _clear_qp()
            st.rerun()
