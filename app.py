import streamlit as st
import requests
import pandas as pd
from datetime import datetime, date, timedelta
from urllib.parse import urlparse

# Google APIs
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
import gspread
from gspread_dataframe import set_with_dataframe

# --- OAuthlib settings para despliegues sin HTTPS en el redirect (localhost)
import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # permite http://localhost en el authorization_response
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")   # tolera diferencias de orden/espacios en scopes

# =============================
# Configuración base
# =============================
DEBUG_DEFAULT = bool(st.secrets.get("debug", False))
st.set_page_config(layout="wide", page_title="Análisis SEO", page_icon="📊")
st.title("Análisis SEO – GSC ➜ Google Sheets")

# Switch de depuración visible tras login
st.session_state.setdefault("DEBUG", DEBUG_DEFAULT)


# ---------------------------
# Helpers UI
# ---------------------------

def get_user():
    return getattr(st, "user", getattr(st, "experimental_user", None))

def get_first_name(full_name: str | None) -> str:
    if not full_name:
        return "👋"
    return full_name.split()[0]

def sidebar_user_info(user):
    with st.sidebar:
        with st.container():
            c1, c2 = st.columns([1, 3])
            with c1:
                if getattr(user, "picture", None):
                    try:
                        r = requests.get(user.picture, timeout=5)
                        if r.status_code == 200:
                            st.image(r.content, width=96)
                        else:
                            st.warning("No se pudo cargar la imagen.")
                    except Exception as e:
                        st.warning(f"Error al cargar la imagen: {e}")
                else:
                    st.info("Sin imagen de perfil.")
            with c2:
                st.header("Información del usuario", anchor=False)
                st.write(f"**Nombre:** {getattr(user, 'name', '—')}")
                st.write(f"**Correo:** {getattr(user, 'email', '—')}")
        st.divider()
        st.button(":material/logout: Cerrar sesión", on_click=st.logout, use_container_width=True)

# ---------------------------
# OAuth y clientes
# ---------------------------

SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def build_flow(account_key: str) -> Flow:
    """Crea un flujo OAuth2 a partir de st.secrets para la cuenta elegida."""
    try:
        acc = st.secrets["accounts"][account_key]
    except Exception:
        st.error("No encontré las credenciales en st.secrets['accounts'][…].")
        st.stop()
    client_secrets = {
        "installed": {
            "client_id": acc["client_id"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": acc["client_secret"],
            "redirect_uris": ["http://localhost"],  # Copia/pega la URL completa luego
        }
    }
    flow = Flow.from_client_config(client_secrets, scopes=SCOPES)
    flow.redirect_uri = "http://localhost"
    return flow

def ensure_google_clients(creds: Credentials):
    """Construye clientes con credenciales ya validadas."""
    sc = build("searchconsole", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    gs = gspread.authorize(creds)
    return sc, drive, gs


def debug_log(msg: str, data: dict | str | None = None):
    """Log simple en modo debug (muestra bloques legibles en la app)."""
    if st.session_state.get("DEBUG"):
        st.info(msg)
        if data is not None:
            try:
                import json
                st.code(json.dumps(data, indent=2, ensure_ascii=False))
            except Exception:
                st.code(str(data))


def get_google_identity(drive) -> dict:
    """Devuelve displayName y email de la cuenta de Google autenticada."""
    try:
        me = drive.about().get(fields="user(displayName,emailAddress)").execute()
        return me.get("user", {})
    except Exception as e:
        debug_log("No pude leer identidad de Google Drive", str(e))
        return {}

def parse_drive_id_from_any(s: str | None) -> str | None:
    """Extrae un ID de Drive desde URL o lo devuelve si ya es un ID.
    Acepta URLs de archivo (/d/<ID>) o de carpeta (/folders/<ID>)."""
    if not s:
        return None
    s = s.strip()
    if "/folders/" in s:
        try:
            return s.split("/folders/")[1].split("?")[0].split("/")[0]
        except Exception:
            return None
    if "/d/" in s:
        try:
            return s.split("/d/")[1].split("/")[0]
        except Exception:
            return None
    # ID plano (heurística simple)
    if all(ch not in s for ch in ["/", "?", " "]) and len(s) >= 10:
        return s
    return None


def pick_destination(drive, identity: dict | None):
    """UI para elegir carpeta destino opcional (en la CUENTA autenticada)."""
    st.subheader("Destino de la copia (opcional)")
    me_email = (identity or {}).get("emailAddress")
    if me_email:
        st.caption(f"Se creará en la cuenta conectada: **{me_email}**. Si no elegís carpeta, irá a **Mi unidad**.")
    else:
        st.caption("Se creará en la cuenta conectada. Si no elegís carpeta, irá a Mi unidad.")

    folder_in = st.text_input(
        "Carpeta destino (URL o ID, opcional)",
        placeholder="https://drive.google.com/drive/folders/<FOLDER_ID> o ID",
        key="dest_folder_input",
    )
    folder_id = None
    folder_ok = False
    if folder_in:
        folder_id = parse_drive_id_from_any(folder_in)
        if not folder_id:
            st.error("No pude extraer un ID de carpeta de ese valor. Probá pegar la URL de la carpeta o el ID plano.")
        else:
            try:
                meta = (
                    drive.files()
                    .get(fileId=folder_id, fields="id,name,mimeType,driveId", supportsAllDrives=True)
                    .execute()
                )
                if meta.get("mimeType") == "application/vnd.google-apps.folder":
                    folder_ok = True
                    st.success(f"Usaremos la carpeta: **{meta.get('name','(sin nombre)')}**")
                    debug_log("Carpeta destino", meta)
                else:
                    st.error("El ID proporcionado no es una carpeta de Google Drive.")
            except Exception as e:
                st.error("No pude acceder a esa carpeta con esta cuenta. Verificá permisos.")
                debug_log("Error verificando carpeta destino", str(e))
    if folder_ok:
        st.session_state["dest_folder_id"] = folder_id
    return st.session_state.get("dest_folder_id")

# ---------------------------
# Funciones GSC (adaptadas del Colab)
# ---------------------------

def _fetch_all_rows(service, site_url, body, page_size=25000):
    all_rows, start = [], 0
    while True:
        page_body = dict(body)
        page_body["rowLimit"] = page_size
        if start:
            page_body["startRow"] = start
        resp = service.searchanalytics().query(siteUrl=site_url, body=page_body).execute()
        batch = resp.get("rows", [])
        if not batch:
            break
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return all_rows

def consultar_datos(service, site_url, fecha_inicio, fecha_fin, tipo_dato, pais=None, seccion_filtro=None):
    seccion_frag = seccion_filtro.strip("/") if seccion_filtro else None
    body = {"startDate": str(fecha_inicio), "endDate": str(fecha_fin), "dimensions": ["page"]}
    body["type"] = "discover" if tipo_dato == "discover" else "web"
    if tipo_dato == "discover":
        body["dataState"] = "all"
    filters = []
    if seccion_frag:
        filters.append({"dimension": "page", "operator": "contains", "expression": f"/{seccion_frag}"})
    if pais:
        filters.append({"dimension": "country", "operator": "equals", "expression": pais})
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]
    rows = _fetch_all_rows(service, site_url, body)
    if not rows:
        return pd.DataFrame(columns=["url", "clicks", "impressions", "ctr", "position"])
    df = pd.DataFrame([
        {
            "url": r["keys"][0],
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": r.get("ctr", 0.0),
            "position": r.get("position", 0.0),
        }
        for r in rows
    ])
    return df

def consultar_por_pais(service, site_url, fecha_inicio, fecha_fin, tipo_dato, seccion_filtro=None):
    seccion_frag = seccion_filtro.strip("/") if seccion_filtro else None
    body = {"startDate": str(fecha_inicio), "endDate": str(fecha_fin), "dimensions": ["country"]}
    body["type"] = "discover" if tipo_dato == "discover" else "web"
    if tipo_dato == "discover":
        body["dataState"] = "all"
    filters = []
    if seccion_frag:
        filters.append({"dimension": "page", "operator": "contains", "expression": f"/{seccion_frag}"})
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]
    rows = _fetch_all_rows(service, site_url, body, page_size=250)
    if not rows:
        return pd.DataFrame(columns=["country", "clicks", "impressions"])
    df = pd.DataFrame([
        {"country": r.get("keys", [None])[0], "clicks": r.get("clicks", 0), "impressions": r.get("impressions", 0)}
        for r in rows
    ])
    return df.groupby("country", as_index=False)[["clicks", "impressions"]].sum().sort_values("clicks", ascending=False)

# ---------------------------
# Exportar a Google Sheets
# ---------------------------

def get_template_id(kind: str, account_key: str | None = None) -> str | None:
    """Obtiene el template ID desde secrets. Admite por cuenta y global.
    Prioridad: templates[<account_key>][<kind>] -> templates[<kind>]
    """
    troot = st.secrets.get("templates", {})
    if account_key and isinstance(troot.get(account_key), dict):
        return troot[account_key].get(kind) or troot.get(kind)
    return troot.get(kind)


def verify_template_access(drive, template_id: str) -> dict | None:
    """Lee metadatos (y permisos si DEBUG) del template para verificar acceso.
    Si la API de Drive no está habilitada en el proyecto de tu client_id,
    muestra un mensaje claro para habilitarla.
    """
    try:
        meta = (
            drive.files()
            .get(
                fileId=template_id,
                fields="id,name,parents,mimeType,owners(displayName,emailAddress),webViewLink,driveId",
                supportsAllDrives=True,
            )
            .execute()
        )
        if st.session_state.get("DEBUG"):
            try:
                perms = (
                    drive.permissions()
                    .list(fileId=template_id, fields="permissions(emailAddress,role,type)", supportsAllDrives=True)
                    .execute()
                )
                meta["_permissions"] = perms.get("permissions", [])
            except Exception as e:
                meta["_permissions_error"] = str(e)
        return meta
    except HttpError as e:
        msg = str(e)
        if "accessNotConfigured" in msg or "has not been used in project" in msg:
            st.error("""La API de Google Drive **no está habilitada** en el proyecto de tu OAuth client.

➡️ Entrá a **Google Cloud Console** del proyecto de tu *client_id* y habilitá:
- **Google Drive API**
- **Google Sheets API**
- **Search Console API** (Webmasters)

Luego reintentá la autorización (Paso A y Paso B).""")
            st.caption("Tip: si tu app está en modo *Testing*, agregá tu email como *Test user* en la pantalla de consentimiento.")
            debug_log("HttpError accessNotConfigured", msg)
            st.stop()
        debug_log("HttpError al leer metadatos del template", msg)
        return None
    except Exception as e:
        debug_log("Error al leer metadatos del template", str(e))
        return None


def copy_template_and_open(drive, gsclient, template_id: str, title: str, dest_folder_id: str | None = None):
    # Pre-chequeo de acceso para dar mensajes claros
    meta = verify_template_access(drive, template_id)
    if not meta:
        raise RuntimeError(
            "No tengo acceso al template de Google Sheets especificado. Verificá que el ID sea correcto y que la cuenta de Google autenticada tenga permiso de lectura."
        )
    # Mostrar info útil para depurar (no sensible)
    owners = ", ".join([o.get("displayName") or o.get("emailAddress", "?") for o in meta.get("owners", [])]) or "(desconocido)"
    st.caption(f"Template detectado: **{meta.get('name','(sin nombre)')}** – Propietario(s): {owners}")
    debug_log("Metadatos del template", meta)

    # Armar body de copia; si se eligió carpeta, validarla y usarla como parent
    body = {"name": title}
    if dest_folder_id:
        try:
            folder_meta = (
                drive.files()
                .get(fileId=dest_folder_id, fields="id,name,mimeType,driveId", supportsAllDrives=True)
                .execute()
            )
            if folder_meta.get("mimeType") != "application/vnd.google-apps.folder":
                raise RuntimeError("El ID de destino no es una carpeta de Google Drive.")
            body["parents"] = [dest_folder_id]
            st.caption(f"Destino: carpeta **{folder_meta.get('name','(sin nombre)')}**")
            debug_log("Destino carpeta", folder_meta)
        except Exception as e:
            debug_log("Error validando carpeta destino", str(e))
            raise RuntimeError("No tengo acceso a la carpeta destino con esta cuenta. Compartila o elegí otra.")
    else:
        st.caption("Destino: **Mi unidad** (raíz)")

    try:
        new_file = (
            drive.files()
            .copy(fileId=template_id, body=body, supportsAllDrives=True)
            .execute()
        )
        debug_log("Resultado de la copia", new_file)
        sid = new_file["id"]
        view_link = f"https://docs.google.com/spreadsheets/d/{sid}"
        debug_log("Link del nuevo archivo", {"webViewLink": view_link})
        sheet = gsclient.open_by_key(sid)
        return sheet, sid
    except Exception as e:
        debug_log("Excepción al copiar template", str(e))
        raise RuntimeError(f"Falló la copia del template (ID={template_id}). Detalle: {e}")

def safe_set_df(ws, df: pd.DataFrame | None, include_header=True):
    # Evitar 'df or pd.DataFrame()' porque la verdad de un DataFrame es ambigua en pandas
    if df is None:
        df = pd.DataFrame()
    else:
        df = df.copy()
    df = df.astype(object).where(pd.notnull(df), "")
    ws.clear()
    set_with_dataframe(ws, df, include_column_header=include_header)

# ---------------------------
# Vistas / UI principal
# ---------------------------

LAG_DAYS_DEFAULT = 3

def login_screen():
    st.header("Esta aplicación es privada.")
    st.subheader("Por favor, inicia sesión.")
    st.button(":material/login: Iniciar sesión con Google", on_click=st.login)

# PATCH: OAuth con Flow persistido + authorization_response (URL completa) y sin include_granted_scopes

def pick_account_and_oauth():
    st.subheader("1) Elegí con qué cuenta autenticarte (GSC/Sheets/Drive)")
    acct = st.radio(
        "Cuenta:",
        options=["ACCESO", "ACCESO_MEDIOS"],
        captions=["Credenciales de la cuenta Acceso", "Credenciales de la cuenta Acceso Medios"],
        horizontal=True,
        key="acct_choice",
    )

    # Si cambia la cuenta, reiniciamos flujo previo
    if "oauth" in st.session_state and st.session_state["oauth"].get("account") != acct:
        st.session_state.pop("oauth")

    # Crear o reutilizar el Flow y la auth_url
    if "oauth" not in st.session_state:
        flow = build_flow(acct)
        # Opcional: si definís el correo en secrets, forzamos sugerencia de cuenta correcta
        # [accounts.ACCESO]
        # client_id = "..."
        # client_secret = "..."
        # login_hint = "tu_correo_de_acceso@ejemplo.com"
        acc_meta = st.secrets.get("accounts", {}).get(acct, {})
        auth_url, state = flow.authorization_url(
            prompt="consent select_account",
            access_type="offline",
            include_granted_scopes="false",  # evita "Scope has changed"
            login_hint=acc_meta.get("login_hint"),
        )
        st.session_state["oauth"] = {
            "account": acct,
            "flow": flow,
            "auth_url": auth_url,
            "state": state,
        }

    oauth = st.session_state["oauth"]
    st.markdown(f"🔗 **Paso A:** [Autorizar acceso en Google]({oauth['auth_url']})")
    # Mostrar también la URL en texto para copiarla y abrirla en otro navegador/perfil
    with st.expander("Ver/copiar URL de autorización"):
        st.code(oauth["auth_url"])

    # Pedir la URL completa de redirección
    auth_response_url = st.text_input(
        "🔑 Paso B: Pegá aquí la URL completa después de autorizar (http://localhost/…)",
        placeholder="http://localhost/?code=...&scope=...&state=...",
        key="auth_response_url",
    )

    creds = None
    if st.button("Conectar Google", type="primary"):
        if not auth_response_url.strip():
            st.error("Pegá la URL completa de redirección (incluye code y state).")
            st.stop()
        try:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(auth_response_url.strip())
            q = parse_qs(parsed.query)
            code = (q.get("code") or [None])[0]
            state_from_callback = (q.get("state") or [None])[0]
            if not code or not state_from_callback:
                st.error("La URL no contiene parámetros 'code' y 'state'. Volvé a autorizar y pegá la URL completa.")
                st.stop()
            # Validar que el 'state' coincida con el del Flow
            expected_state = st.session_state["oauth"].get("state")
            if state_from_callback != expected_state:
                # Forzar regenerar flujo para evitar usar un state viejo
                st.session_state.pop("oauth", None)
                st.error("El parámetro 'state' no coincide (parece ser una URL antigua). Hacé clic en 'Autorizar' de nuevo y usá esa nueva URL.")
                st.stop()
            # Usar el mismo Flow almacenado (con state + code_verifier intactos)
            flow: Flow = st.session_state["oauth"]["flow"]
            flow.fetch_token(authorization_response=auth_response_url.strip())
            creds = flow.credentials
            st.session_state["creds"] = creds_to_dict(creds)
            st.success("Autenticación exitosa.")
        except Exception as e:
            st.session_state.pop("oauth", None)
            st.error("No se pudo intercambiar el código por tokens. Reintentá: Autorizar → pegar la URL completa actual.")
            st.caption(f"Detalle técnico (debug): {e}")

    # Si ya hay credenciales, reconstruimos Credentials
    if not creds and st.session_state.get("creds"):
        creds = Credentials(**st.session_state["creds"])
    return creds

def creds_to_dict(creds: Credentials):
    return {
        "token": creds.token,
        "refresh_token": getattr(creds, "refresh_token", None),
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }

def pick_site(sc_service):
    st.subheader("2) Elegí el sitio a trabajar (Search Console)")
    try:
        site_list = sc_service.sites().list().execute()
        sites = site_list.get("siteEntry", [])
    except Exception as e:
        st.error(f"Error al obtener sitios: {e}")
        st.stop()
    verified = [s for s in sites if s.get("permissionLevel") != "siteUnverifiedUser"]
    if not verified:
        st.error("No se encontraron sitios verificados en esta cuenta.")
        st.stop()
    site_map = {s["siteUrl"]: s["siteUrl"] for s in verified}
    site_url = st.selectbox("Sitio verificado:", list(site_map.keys()))
    return site_url

def pick_analysis():
    st.subheader("3) Elegí el tipo de análisis")
    opciones = {
        "1. Análisis de entidades (🚧 próximamente)": "1",
        "2. Análisis de tráfico general (🚧 próximamente)": "2",
        "3. Análisis de secciones (🚧 próximamente)": "3",
        "4. Análisis de impacto de Core Update ✅": "4",
        "5. Análisis de tráfico evergreen ✅": "5",
    }
    key = st.radio("Tipos disponibles:", list(opciones.keys()), index=3)
    return opciones[key]

def params_for_core_update():
    st.markdown("#### Parámetros (Core Update)")
    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, LAG_DAYS_DEFAULT)
    fecha_inicio = st.date_input("¿Cuándo inició el Core Update? (YYYY-MM-DD)")
    termino = st.radio("¿El Core Update ya terminó?", ["sí", "no"], horizontal=True)
    fecha_fin = None
    if termino == "sí":
        fecha_fin = st.date_input("¿Cuándo finalizó el Core Update? (YYYY-MM-DD)")
    tipo = st.selectbox("Datos a analizar", ["Search", "Discover", "Ambos"], index=2)
    pais_choice = st.selectbox("¿Filtrar por país? (ISO-3)", ["Todos", "ARG", "MEX", "ESP", "USA", "COL", "PER", "CHL", "URY"], index=0)
    pais = None if pais_choice == "Todos" else pais_choice
    seccion = st.text_input("¿Limitar a una sección? (path, ej: /vida/)", value="") or None
    return lag_days, fecha_inicio, termino, fecha_fin, tipo, pais, seccion

def compute_core_windows(lag_days, fecha_inicio: date, termino: str, fecha_fin: date | None):
    hoy_util = date.today() - timedelta(days=lag_days)
    if termino == "sí" and fecha_fin:
        dias_analisis = max((hoy_util - fecha_fin).days, 1)
        pre_ini = fecha_inicio - timedelta(days=dias_analisis)
        pre_fin = fecha_inicio - timedelta(days=1)
        post_ini = fecha_fin + timedelta(days=1)
        post_fin = fecha_fin + timedelta(days=dias_analisis)
    else:
        dias_analisis = max((hoy_util - fecha_inicio).days, 1)
        pre_ini = fecha_inicio - timedelta(days=dias_analisis)
        pre_fin = fecha_inicio - timedelta(days=1)
        post_ini = fecha_inicio
        post_fin = hoy_util
    return pre_ini, pre_fin, post_ini, post_fin

def params_for_evergreen():
    st.markdown("#### Parámetros (Evergreen)")
    st.caption("Se usa el período más amplio posible de **meses completos** (hasta 16) en Search.")
    lag_days = st.number_input("Lag de datos (para evitar días incompletos)", 0, 7, LAG_DAYS_DEFAULT)
    pais_choice = st.selectbox("¿Filtrar por país? (ISO-3)", ["Todos", "ARG", "MEX", "ESP", "USA", "COL", "PER", "CHL", "URY"], index=0)
    pais = None if pais_choice == "Todos" else pais_choice
    seccion = st.text_input("¿Limitar a una sección? (path, ej: /vida/)", value="") or None
    incluir_diario = st.checkbox("Incluir análisis diario por URL (lento)", value=False)
    # Ventana de 16 meses completos
    hoy_util = date.today() - timedelta(days=lag_days)
    end_month_first_day = (pd.Timestamp(hoy_util.replace(day=1)) - pd.offsets.MonthBegin(1))
    end_month_last_day = (end_month_first_day + pd.offsets.MonthEnd(0))
    start_month_first_day = (end_month_first_day - pd.DateOffset(months=15))
    start_date = start_month_first_day.date()
    end_date = end_month_last_day.date()
    st.info(f"Ventana mensual: {start_date} → {end_date}")
    return lag_days, pais, seccion, incluir_diario, start_date, end_date

# ---------------------------
# Ejecución de análisis
# ---------------------------

def run_core_update(sc_service, drive, gsclient, site_url, params, dest_folder_id=None):
    lag_days, f_ini, termino, f_fin, tipo, pais, seccion = params
    pre_ini, pre_fin, post_ini, post_fin = compute_core_windows(lag_days, f_ini, termino, f_fin)

    tipos = [("Search", "web"), ("Discover", "discover")] if tipo == "Ambos" else [
        ("Search", "web") if tipo == "Search" else ("Discover", "discover")
    ]

    dom = urlparse(site_url).netloc.replace("www.", "")
    nombre_medio = dom
    nombre_analisis = "Análisis de impacto de Core Update"
    title = f"{nombre_medio} - {nombre_analisis} - {date.today()}"

    template_id = get_template_id("core_update", st.session_state.get("oauth", {}).get("account"))
    if not template_id:
        st.error("No se configuró el ID de template para 'core_update' en st.secrets.")
        st.stop()
    sh, sid = copy_template_and_open(drive, gsclient, template_id, title, dest_folder_id)

    # Exportar datos Pre/Post + por país básicos
    for tipo_nombre, tipo_val in tipos:
        df_pre = consultar_datos(sc_service, site_url, pre_ini, pre_fin, tipo_val, pais=pais, seccion_filtro=seccion)
        df_post = consultar_datos(sc_service, site_url, post_ini, post_fin, tipo_val, pais=pais, seccion_filtro=seccion)
        # Renombrar columnas como en el Colab
        if not df_pre.empty:
            df_pre = df_pre.rename(columns={"position": "posición", "impressions": "impresiones", "clicks": "clics"})
            ws_pre = _ensure_ws(sh, f"{tipo_nombre} | Pre Core Update")
            safe_set_df(ws_pre, df_pre)
        if not df_post.empty:
            df_post = df_post.rename(columns={"position": "posición", "impressions": "impresiones", "clicks": "clics"})
            ws_post = _ensure_ws(sh, f"{tipo_nombre} | Post Core Update")
            safe_set_df(ws_post, df_post)
        # País
        df_pre_p = consultar_por_pais(sc_service, site_url, pre_ini, pre_fin, tipo_val, seccion_filtro=seccion)
        df_post_p = consultar_por_pais(sc_service, site_url, post_ini, post_fin, tipo_val, seccion_filtro=seccion)
        dfp = df_pre_p.merge(df_post_p, on="country", how="outer", suffixes=("_pre", "_post")).fillna(0)
        if not dfp.empty:
            ws_tp = _ensure_ws(sh, f"{tipo_nombre} | Tráfico por país")
            safe_set_df(ws_tp, dfp)

    # Configuración
    cfg = pd.DataFrame([
        ("Sitio Analizado", site_url),
        ("Tipo de análisis", ", ".join([t[0] for t in tipos])),
        ("Periodo Core Update", f"{f_ini} a {f_fin or ''}".strip()),
        ("Periodo Pre Core Update", f"{pre_ini} a {pre_fin}"),
        ("Periodo Post Core Update", f"{post_ini} a {post_fin}"),
        ("Sección", seccion or "Todo el sitio"),
        ("País", pais or "Todos"),
    ], columns=["Configuración", "Valor"])
    ws_cfg = _ensure_ws(sh, "Configuracion")
    safe_set_df(ws_cfg, cfg)

    return sid

def run_evergreen(sc_service, drive, gsclient, site_url, params, dest_folder_id=None):
    lag_days, pais, seccion, incluir_diario, start_date, end_date = params

    dom = urlparse(site_url).netloc.replace("www.", "")
    nombre_medio = dom
    nombre_analisis = "Análisis de tráfico evergreen"
    title = f"{nombre_medio} - {nombre_analisis} - {date.today()}"

    template_id = get_template_id("evergreen", st.session_state.get("oauth", {}).get("account"))
    if not template_id:
        st.error("No se configuró el ID de template para 'evergreen' en st.secrets.")
        st.stop()
    sh, sid = copy_template_and_open(drive, gsclient, template_id, title, dest_folder_id)

    # Mensual por página (Search/web)
    monthly = fetch_gsc_monthly_by_page(sc_service, site_url, start_date, end_date, country_iso3=pais, section_path=seccion)
    ws_month = _ensure_ws(sh, "Search | Datos mensuales")
    safe_set_df(ws_month, monthly)

    # Totales diarios del sitio (Search/web)
    daily_tot = fetch_site_daily_totals(sc_service, site_url, start_date, end_date, country_iso3=pais, section_path=seccion)
    ws_total = _ensure_ws(sh, "Search | Diario total")
    safe_set_df(ws_total, daily_tot)

    # (Opcional) Diario por URL – puede ser muy pesado, se deja fuera por defecto
    if incluir_diario:
        df_daily = fetch_gsc_daily_evergreen(sc_service, site_url, start_date, end_date, country_iso3=pais, section_path=seccion)
        ws_daily = _ensure_ws(sh, "Search | Datos diarios")
        safe_set_df(ws_daily, df_daily)

    # Configuración
    cfg = pd.DataFrame([
        ("Sitio Analizado", site_url),
        ("Ventana mensual", f"{start_date} a {end_date}"),
        ("Sección", seccion or "Todo el sitio"),
        ("País", pais or "Todos"),
        ("Incluye diario por URL", "Sí" if incluir_diario else "No"),
    ], columns=["Configuración", "Valor"])
    ws_cfg = _ensure_ws(sh, "Configuracion")
    safe_set_df(ws_cfg, cfg)

    return sid

# ====== Funciones Evergreen auxiliares ======

def month_range(start_date, end_date):
    cur = pd.Timestamp(start_date).replace(day=1)
    endm = pd.Timestamp(end_date).replace(day=1)
    while cur <= endm:
        yield cur.date(), (cur + pd.offsets.MonthEnd(0)).date()
        cur = (cur + pd.offsets.MonthBegin(1))

def fetch_gsc_monthly_by_page(service, site_url, start_dt, end_dt, country_iso3=None, section_path=None):
    frames = []
    for m_start, m_end in month_range(start_dt, end_dt):
        body = {
            "startDate": str(m_start),
            "endDate": str(m_end),
            "dimensions": ["page"],
            "type": "web",
            "aggregationType": "auto",
        }
        filters = []
        if country_iso3:
            filters.append({"dimension": "country", "operator": "equals", "expression": country_iso3})
        if section_path:
            filters.append({"dimension": "page", "operator": "contains", "expression": section_path})
        if filters:
            body["dimensionFilterGroups"] = [{"filters": filters}]
        rows = _fetch_all_rows(service, site_url, body)
        if rows:
            df = pd.DataFrame([
                {
                    "page": r["keys"][0],
                    "month": pd.to_datetime(m_start),
                    "clicks": r.get("clicks", 0),
                    "impressions": r.get("impressions", 0),
                }
                for r in rows
            ])
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["page", "month", "clicks", "impressions"]) 

def fetch_site_daily_totals(service, site_url, start_dt, end_dt, country_iso3=None, section_path=None):
    body = {"startDate": str(start_dt), "endDate": str(end_dt), "dimensions": ["date"], "type": "web"}
    filters = []
    if country_iso3:
        filters.append({"dimension": "country", "operator": "equals", "expression": country_iso3})
    if section_path:
        filters.append({"dimension": "page", "operator": "contains", "expression": section_path})
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]
    rows = _fetch_all_rows(service, site_url, body, page_size=5000)
    df = pd.DataFrame([
        {"date": pd.to_datetime(r["keys"][0]).date(), "clicks": r.get("clicks", 0), "impressions": r.get("impressions", 0)}
        for r in rows
    ]) if rows else pd.DataFrame(columns=["date", "clicks", "impressions"])
    if not df.empty:
        df["ctr"] = (df["clicks"] / df["impressions"]).fillna(0)
    return df

def fetch_gsc_daily_evergreen(service, site_url, start_dt, end_dt, country_iso3=None, section_path=None, page_size=25000):
    rows_all, start_row = [], 0
    body = {
        "startDate": str(start_dt),
        "endDate": str(end_dt),
        "dimensions": ["page", "date"],
        "rowLimit": page_size,
        "type": "web",
        "aggregationType": "auto",
    }
    filters = []
    if country_iso3:
        filters.append({"dimension": "country", "operator": "equals", "expression": country_iso3})
    if section_path:
        filters.append({"dimension": "page", "operator": "contains", "expression": section_path})
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]
    while True:
        body["startRow"] = start_row
        resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
        rows = resp.get("rows", [])
        if not rows:
            break
        for r in rows:
            rows_all.append({
                "page": r["keys"][0],
                "date": pd.to_datetime(r["keys"][1]),
                "clicks": r.get("clicks", 0),
                "impressions": r.get("impressions", 0),
                "ctr": r.get("ctr", 0.0),
                "position": r.get("position", 0.0),
            })
        if len(rows) < page_size:
            break
        start_row += page_size
    df = pd.DataFrame(rows_all)
    if not df.empty:
        df["date"] = df["date"].dt.date
    return df

# ---------------------------
# Utilidad: asegurar hoja
# ---------------------------

def _ensure_ws(sheet, title):
    try:
        return sheet.worksheet(title)
    except Exception:
        return sheet.add_worksheet(title=title, rows=2000, cols=26)

# ---------------------------
# App
# ---------------------------

user = get_user()
if not user or not getattr(user, "is_logged_in", False):
    login_screen()
    st.stop()

# Sidebar info
sidebar_user_info(user)

# Modo debug opcional (muestra metadatos de Drive, permisos, etc.)
st.checkbox("🔧 Modo debug (Drive/GSC)", key="DEBUG")

# Paso 1: OAuth con selección de cuenta
creds = pick_account_and_oauth()
if not creds:
    st.stop()

# Construir clientes
sc_service, drive_service, gs_client = ensure_google_clients(creds)
# Mostrar identidad de la cuenta de Google conectada
_me = get_google_identity(drive_service)
if _me:
    st.caption(f"Google conectado como: **{_me.get('displayName','?')}** ({_me.get('emailAddress','?')})")
else:
    st.caption("No se pudo determinar el correo de la cuenta de Google conectada.")

# Seleccionar carpeta destino opcional (en la CUENTA autenticada)
dest_folder_id = pick_destination(drive_service, _me)

# Paso 2: elegir sitio
site_url = pick_site(sc_service)

# Paso 3: elegir análisis
analisis = pick_analysis()

# Paso 4: parámetros y ejecución
if analisis == "4":
    params = params_for_core_update()
    if st.button("🚀 Ejecutar análisis de Core Update", type="primary"):
        sid = run_core_update(sc_service, drive_service, gs_client, site_url, params, dest_folder_id)
        st.success("¡Listo! Tu documento está creado.")
        st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
elif analisis == "5":
    params = params_for_evergreen()
    if st.button("🌲 Ejecutar análisis Evergreen", type="primary"):
        sid = run_evergreen(sc_service, drive_service, gs_client, site_url, params, dest_folder_id)
        st.success("¡Listo! Tu documento está creado.")
        st.markdown(f"➡️ **Abrir Google Sheets**: https://docs.google.com/spreadsheets/d/{sid}")
else:
    st.info("Las opciones 1, 2 y 3 aún no están disponibles en esta versión.")
