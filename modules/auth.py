# modules/auth.py
from __future__ import annotations

from typing import Optional, List, Dict, Tuple
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from urllib.parse import urlsplit, parse_qs
import requests
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
import streamlit as st

from .utils import debug_log, token_store

# =============================
# Scopes
# =============================
SCOPES_OIDC: List[str] = ["openid", "email", "profile"]

SCOPES_DRIVE: List[str] = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SCOPES_GSC: List[str] = [
    "https://www.googleapis.com/auth/webmasters.readonly",
]

SCOPES_PERSONAL_FULL: List[str] = [*SCOPES_OIDC, *SCOPES_DRIVE, *SCOPES_GSC]


# =============================
# Helpers comunes
# =============================
def _creds_to_dict(creds: Credentials) -> dict:
    return {
        "token": creds.token,
        "refresh_token": getattr(creds, "refresh_token", None),
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if getattr(creds, "scopes", None) else None,
    }

def creds_to_dict(creds: Credentials) -> dict:
    return _creds_to_dict(creds)


# =============================
# Cliente OAuth "installed" (copy/paste a http://localhost)
# — Usado en Paso 1 (Drive/Sheets) y Paso 2 (GSC)
# =============================
def build_flow(account_key: str, scopes: List[str]) -> Flow:
    """
    Crea un flujo OAuth2 tipo 'installed' tomando credenciales de
    st.secrets['accounts'][account_key]. Redirect: http://localhost
    """
    try:
        acc = st.secrets["accounts"][account_key]
    except Exception:
        st.error(f"No encontré credenciales en st.secrets['accounts']['{account_key}'].")
        st.stop()

    client_secrets = {
        "installed": {
            "client_id": acc["client_id"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": acc["client_secret"],
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = Flow.from_client_config(client_secrets, scopes=scopes)
    flow.redirect_uri = "http://localhost"
    return flow


# =============================
# Paso 0 (botón Google) — cliente OAuth WEB
# =============================
def _get_web_oauth_config(account_key: Optional[str] = None) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
    """
    Devuelve (client_id, client_secret, redirect_uri, source_label)
    Busca primero en [auth] y, si no alcanza, intenta en [accounts][account_key] con
    claves web_client_id, web_client_secret, web_redirect_uri (alias opcionales).
    """
    # Preferir [auth] (flujo personal)
    auth = st.secrets.get("auth", {}) or {}
    cid = auth.get("client_id")
    csec = auth.get("client_secret")
    ruri = auth.get("redirect_uri")
    if cid and csec and ruri:
        return cid, csec, ruri, "auth"

    # Fallback: mirar dentro de accounts[account_key] (si se pide explícito)
    if account_key:
        try:
            acc = (st.secrets.get("accounts") or {}).get(account_key) or {}
            cid2 = acc.get("web_client_id") or acc.get("client_id")
            csec2 = acc.get("web_client_secret") or acc.get("client_secret")
            ruri2 = acc.get("web_redirect_uri") or ruri  # usa redirect global si no hay uno específico
            if cid2 and csec2 and ruri2:
                return cid2, csec2, ruri2, f"accounts.{account_key}"
        except Exception:
            pass

    return None, None, None, ""


def is_redirect_status(account_key: Optional[str] = None) -> Tuple[bool, str]:
    """
    Devuelve (ok, msg) verificando si hay config suficiente para el flujo Web.
    """
    cid, csec, ruri, src = _get_web_oauth_config(account_key)
    if cid and csec and ruri:
        return True, ""
    return (
        False,
        "Falta configurar el redirect_uri o el cliente OAuth Web. "
        "Define en secrets [auth] client_id, client_secret y redirect_uri; "
        "o en [accounts].<key> usa web_client_id / web_client_secret / web_redirect_uri."
    )


def is_redirect_ready(account_key: Optional[str] = None) -> bool:
    """
    API compatible con app_auth_flow: devuelve SOLO bool y acepta account_key.
    """
    ok, _ = is_redirect_status(account_key)
    return ok


def build_flow_web(*args, **kwargs) -> Flow:
    """
    Compatible con ambos usos:
      - build_flow_web(scopes)
      - build_flow_web(account_key, scopes)

    Si existen credenciales en [auth], las usa. Si no, intenta con
    [accounts][account_key] (web_client_id/secret/redirect_uri).
    """
    # Parseo flexible de parámetros
    account_key: Optional[str] = None
    scopes: List[str] = []

    if len(args) == 1 and isinstance(args[0], list):
        scopes = args[0]
    elif len(args) >= 2 and isinstance(args[1], list):
        account_key = args[0]
        scopes = args[1]
    else:
        scopes = kwargs.get("scopes", [])
        account_key = kwargs.get("account_key")

    cid, csec, ruri, src = _get_web_oauth_config(account_key)
    if not (cid and csec and ruri):
        raise RuntimeError(
            "Falta configurar el redirect_uri o el cliente OAuth Web.\n"
            "secrets [auth].redirect_uri debe apuntar a tu app (p. ej., https://<tu-app>.streamlit.app/).\n"
            "en [accounts].<key> definí web_client_id y web_client_secret (o sus alias)."
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


def fetch_userinfo(creds: Credentials) -> Dict[str, str]:
    """
    Devuelve {name, email, picture} desde el endpoint OIDC /userinfo.
    """
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
    except Exception as e:
        debug_log("[fetch_userinfo] fallo userinfo", str(e))
    return info


# =============================
# Cache de credenciales
# =============================
def get_cached_personal_creds() -> Optional[Credentials]:
    """
    Devuelve Credentials de la CUENTA PERSONAL (Drive/Sheets) si está cacheado.
    """
    # Preferir construir Credentials directamente desde el token_store
    creds = token_store.as_credentials("creds_dest")
    if creds:
        return creds

    # Fallback por compatibilidad: leer dict crudo si existe en session_state
    data = token_store.load("creds_dest")
    if not data:
        data = st.session_state.get("creds_dest")
    if data:
        try:
            return Credentials(**data)
        except Exception as e:
            debug_log("[get_cached_personal_creds] no pude construir Credentials desde dict", str(e))
    return None


# =============================
# OAuth PERSONAL (Drive/Sheets) — PASO 1 (installed)
# =============================
def pick_destination_oauth():
    """
    OAuth para la cuenta PERSONAL (Drive/Sheets) con flujo 'installed' (localhost).
    - Genera la URL una sola vez por sesión.
    - Verifica el 'state' devuelto en la URL pegada.
    - Permite 'Reiniciar Paso 1' si hay mismatch.
    """
    st.subheader("1) Conectar Google PERSONAL (Drive/Sheets)")

    acct_for_dest = st.secrets.get("oauth_app_key", "ACCESO")

    # Si cambia la app key (raro), resetea flujo
    if st.session_state.get("oauth_dest", {}).get("account_key") != acct_for_dest:
        st.session_state.pop("oauth_dest", None)

    # Construye el flow y auth_url solo si no existe ya
    if "oauth_dest" not in st.session_state:
        from .utils import build_flow_drive  # helper con scopes de Drive/Sheets
        flow = build_flow_drive(acct_for_dest)
        # ⚠️ Sin include_granted_scopes para evitar 400 por bool capitalizado
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
        )
        st.session_state["oauth_dest"] = {
            "account_key": acct_for_dest,
            "flow": flow,
            "auth_url": auth_url,
            "state": state,              # ← guardamos el state generado
        }

    od = st.session_state["oauth_dest"]

    # --- Lanzadores seguros (misma pestaña o nueva) para evitar iframes ---
    st.write("🔗 **Paso A (personal):** Autorizar Drive/Sheets")
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("Abrir en esta pestaña", key="btn_dest_launch"):
            st.session_state["_oauth_launch_url_dest"] = od["auth_url"]
            st.rerun()
    with c2:
        st.link_button("Abrir en pestaña nueva", od["auth_url"])

    # Redirección en top window (evita 'accounts.google.com ha rechazado la conexión')
    if st.session_state.get("_oauth_launch_url_dest") == od["auth_url"]:
        st.markdown(f'<script>window.top.location.href="{od["auth_url"]}";</script>', unsafe_allow_html=True)
        st.stop()

    with st.expander("Ver/copiar URL de autorización (personal)"):
        st.code(od["auth_url"])

    url = st.text_input(
        "🔑 Paso B (personal): pegá la URL completa (http://localhost/?code=...&state=...)",
        key="auth_response_url_dest",
        placeholder="http://localhost/?code=...&state=...",
    )

    col1, col2 = st.columns([1, 1])
    creds = None

    with col1:
        if st.button("Conectar Google PERSONAL", type="primary", key="btn_connect_dest"):
            if not url.strip():
                st.error("Pegá la URL completa de redirección (incluye code y state).")
                st.stop()

            # --- Validar 'state' explícitamente antes de fetch_token ---
            try:
                qs = parse_qs(urlsplit(url.strip()).query)
                returned_state = (qs.get("state") or [""])[0]
            except Exception:
                returned_state = ""

            if not returned_state:
                st.error("La URL pegada no contiene parámetro 'state'. Verificá que sea la URL completa.")
                st.stop()

            expected_state = od.get("state")
            if returned_state != expected_state:
                if st.session_state.get("DEBUG"):
                    st.warning(
                        "CSRF Warning: el 'state' devuelto **no coincide** con el generado.\n\n"
                        f"state esperado: `{expected_state}`\n"
                        f"state recibido: `{returned_state}`"
                    )
                else:
                    st.error("No se pudo completar la autorización. Reiniciá el Paso 1 e intentá nuevamente.")
                st.stop()

            # Si el state coincide, procedemos a intercambiar el code por tokens
            try:
                flow: Flow = od["flow"]
                flow.fetch_token(authorization_response=url.strip())
                creds = flow.credentials
                data = _creds_to_dict(creds)
                # Guardar en ambos para consistencia
                st.session_state["creds_dest"] = data
                token_store.save("creds_dest", data)
                st.success("Cuenta PERSONAL conectada.")
            except Exception as e:
                st.error("No se pudo conectar la cuenta PERSONAL. Reintentá autorización y pegá la URL completa.")
                st.caption(f"Detalle técnico: {e}")
                st.stop()

    with col2:
        if st.button("Reiniciar Paso 1", key="btn_reset_dest"):
            # Limpia solo lo relacionado al flujo personal
            st.session_state.pop("oauth_dest", None)
            st.session_state.pop("creds_dest", None)
            token_store.clear("creds_dest")
            st.success("Restaurado. Volvé a hacer clic en 'Autorizar Drive/Sheets'.")
            st.stop()

    # Rehidratar desde cache si ya está autenticado
    if not creds and st.session_state.get("creds_dest"):
        try:
            creds = Credentials(**st.session_state["creds_dest"])
        except Exception:
            creds = token_store.as_credentials("creds_dest")

    return creds


# =============================
# OAuth FUENTE (Search Console) — PASO 2 (installed)
# =============================
def pick_source_oauth() -> Optional[Credentials]:
    """
    Autentica la cuenta FUENTE para Search Console (ACCESO o ACCESO_MEDIOS),
    validando también el parámetro 'state' para evitar CSRF.
    """
    st.subheader("2) Conectar cuenta de Search Console (fuente de datos)")

    acct = st.radio(
        "Cuenta SC:",
        options=["ACCESO", "ACCESO_MEDIOS"],
        captions=["Usar client_id de Acceso", "Usar client_id de Acceso Medios"],
        horizontal=True,
        key="acct_choice_sc",
    )

    # Reset si cambia la cuenta
    if st.session_state.get("oauth_src", {}).get("account") != acct:
        st.session_state.pop("oauth_src", None)

    # Construye flow + auth_url si no existe
    if "oauth_src" not in st.session_state:
        flow = build_flow(acct, SCOPES_GSC)
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
        )
        st.session_state["oauth_src"] = {
            "account": acct,
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
        }

    osrc = st.session_state["oauth_src"]

    # --- Lanzadores seguros (misma pestaña o nueva) ---
    st.write("🔗 **Paso A (fuente):** Autorizar acceso a Search Console")
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("Abrir en esta pestaña", key="btn_src_launch"):
            st.session_state["_oauth_launch_url_src"] = osrc["auth_url"]
            st.rerun()
    with c2:
        st.link_button("Abrir en pestaña nueva", osrc["auth_url"])

    if st.session_state.get("_oauth_launch_url_src") == osrc["auth_url"]:
        st.markdown(f'<script>window.top.location.href="{osrc["auth_url"]}";</script>', unsafe_allow_html=True)
        st.stop()

    with st.expander("Ver/copiar URL de autorización (fuente)"):
        st.code(osrc["auth_url"])

    url = st.text_input(
        "🔑 Paso B (fuente): pegá la URL completa (http://localhost/?code=...&state=...)",
        key="auth_response_url_src",
        placeholder="http://localhost/?code=...&state=...",
    )

    col1, col2 = st.columns([1, 1])
    creds = None

    with col1:
        if st.button("Conectar Search Console", type="secondary"):
            if not url.strip():
                st.error("Pegá la URL completa de redirección (incluye code y state).")
                st.stop()

            # --- Validar 'state' explícitamente antes de fetch_token ---
            try:
                qs = parse_qs(urlsplit(url.strip()).query)
                returned_state = (qs.get("state") or [""])[0]
            except Exception:
                returned_state = ""

            if not returned_state:
                st.error("La URL pegada no contiene parámetro 'state'. Verificá que sea la URL completa.")
                st.stop()

            expected_state = osrc.get("state")
            if returned_state != expected_state:
                if st.session_state.get("DEBUG"):
                    st.warning(
                        "CSRF Warning: el 'state' devuelto **no coincide** con el generado.\n\n"
                        f"state esperado: `{expected_state}`\n"
                        f"state recibido: `{returned_state}`"
                    )
                else:
                    st.error("No se pudo completar la autorización. Reiniciá el Paso 2 e intentá nuevamente.")
                st.stop()

            try:
                flow: Flow = osrc["flow"]
                flow.fetch_token(authorization_response=url.strip())
                creds = flow.credentials
                data = _creds_to_dict(creds)
                token_store.save("creds_src", data)
                st.success("Cuenta SC conectada.")
            except Exception as e:
                st.error("No se pudo conectar Search Console. Reintentá autorización y pegá la URL completa.")
                st.caption(f"Detalle técnico: {e}")

    with col2:
        if st.button("Reiniciar Paso 2", key="btn_reset_src"):
            st.session_state.pop("oauth_src", None)
            token_store.clear("creds_src")
            st.success("Restaurado. Volvé a hacer clic en 'Autorizar acceso a Search Console'.")
            st.stop()

    # Rehidratar si ya guardamos en este mismo run
    if not creds:
        creds_dict = token_store.load("creds_src")
        if creds_dict:
            try:
                creds = Credentials(**creds_dict)
            except Exception as e:
                debug_log("[pick_source_oauth] no pude construir Credentials desde dict", str(e))
                creds = None

    return creds