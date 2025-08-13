# app.py
import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import datetime, time, os
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import pandas as pd
import base64, re, traceback
import logging
import numpy as np
import warnings
warnings.simplefilter("ignore", category=FutureWarning)

# 👇 reemplazo de gscwrapper
try:
    import searchconsole  # pip install searchconsole
except ModuleNotFoundError as e:
    st.stop()
    raise RuntimeError(
        "Falta el paquete 'searchconsole'. Añadilo a requirements.txt (searchconsole==0.1.12) y vuelve a desplegar."
    )

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ------------- Constantes -------------
DATE_RANGE_OPTIONS = {'Últimos 3 meses': 3, 'Últimos 6 meses': 6, 'Últimos 12 meses': 12}
DF_PREVIEW_ROWS = 100
MAX_ROWS = 1_000_000

# ------------- UI base -------------
def setup_streamlit():
    st.set_page_config(page_title="GSC | Evergreen Queries y Pages", page_icon="🟩")
    st.title("🟩 GSC | Evergreen Queries y Pages")
    st.write("Esta app permite ver páginas o queries que se mantienen con clicks e impresiones a lo largo del tiempo.")
    st.write("Toma rangos a mes cerrado de 3, 6 o 12 meses.")
    st.write("""La tabla final muestra clicks e impresiones por mes, los totales, 
             la cantidad de meses con data (clicks e impresiones para cada mes) y la cantidad de días con data.""")
    st.caption("[Creado por Damián Taubaso](https://www.linkedin.com/in/dtaubaso/)")
    st.divider()

def init_session_state():
    if 'selected_property' not in st.session_state:
        st.session_state.selected_property = None
    if 'selected_date_range' not in st.session_state:
        st.session_state.selected_date_range = 'Últimos 3 meses'
    if 'start_date' not in st.session_state:
        st.session_state.start_date = datetime.date.today() - datetime.timedelta(days=90)
    if 'end_date' not in st.session_state:
        st.session_state.end_date = datetime.date.today()
    if 'selected_search_type' not in st.session_state:
        st.session_state.selected_search_type = 'web'
    if 'selected_dimension' not in st.session_state:
        st.session_state.selected_dimension = 'query'
    if 'brand_term' not in st.session_state:
        st.session_state.brand_term = None

# ------------- Auth Google -------------
def load_config():
    client_id = st.secrets.get('CLIENT_ID') or os.getenv('CLIENT_ID')
    client_secret = st.secrets.get('CLIENT_SECRET') or os.getenv('CLIENT_SECRET')
    redirect_uri = st.secrets.get('REDIRECT_URI') or os.getenv('REDIRECT_URI')
    if not (client_id and client_secret and redirect_uri):
        st.stop()
        raise RuntimeError("Faltan CLIENT_ID / CLIENT_SECRET / REDIRECT_URI en secrets.")

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://accounts.google.com/o/oauth2/token",
            "redirect_uris": [redirect_uri],
        }
    }
    return client_config, redirect_uri

def init_oauth_flow(client_config, redirect_uri):
    scopes = ["https://www.googleapis.com/auth/webmasters"]
    return Flow.from_client_config(client_config, scopes=scopes, redirect_uri=redirect_uri)

def google_auth(client_config, redirect_uri):
    if "auth_flow" not in st.session_state:
        st.session_state.auth_flow = init_oauth_flow(client_config, redirect_uri)
        auth_url, _ = st.session_state.auth_flow.authorization_url(
            prompt="consent", access_type="offline", include_granted_scopes="true"
        )
        st.session_state.auth_url = auth_url
    return st.session_state.auth_flow, st.session_state.auth_url

# 🔁 Reemplazo: autenticación con 'searchconsole'
def auth_search_console(client_config, credentials):
    token = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
        "id_token": getattr(credentials, "id_token", None),
    }
    # devuelve un 'account' que permite account[site_url] y .query...
    return searchconsole.authenticate(client_config=client_config, credentials=token)

# ------------- Data -------------
def list_gsc_properties(credentials):
    service = build('webmasters', 'v3', credentials=credentials)
    site_list = service.sites().list().execute()
    return [site['siteUrl'] for site in site_list.get('siteEntry', [])] or ["No se encontraron propiedades"]

def fetch_query_page(webproperty, start_date, end_date, selected_search_type, selected_dimension):
    try:
        query = (
            webproperty
            .query
            .range(start_date, end_date)
            .dimensions([selected_dimension, "date"])
            .search_type(selected_search_type)
        )
        df = (query.limit(MAX_ROWS).get()).df
        if df.empty:
            raise Exception("No hay Dataframe. Revise sus datos.")
        return df
    except Exception as e:
        logging.error(traceback.format_exc())
        st.error(e)
        return pd.DataFrame()

def get_evergreen(webproperty, start_date, end_date, selected_search_type, selected_dimension, brand_term=None):
    if selected_search_type == 'discover':
        selected_dimension = 'page'
    df = fetch_query_page(webproperty, start_date, end_date, selected_search_type, selected_dimension)
    if df.empty:
        raise Exception("No hay Dataframe")

    if brand_term and selected_dimension=='query':
        brand_term = "|".join(list(map(str.strip, brand_term.split(','))))
        df = df[~df['query'].str.contains(brand_term)]

    df = df.sort_values('date').reset_index(drop=True)
    df['q_days'] = df.groupby(selected_dimension)[selected_dimension].transform('count')
    df['mes_anio'] = df['date'].apply(lambda x: re.findall('(.*-.*)-', x)[0])
    df = df.groupby([selected_dimension, 'mes_anio', 'q_days']).sum(numeric_only=True).reset_index()
    df_mes = pd.pivot_table(df, values=['clicks', 'impressions'], index=[selected_dimension],
                            columns=['mes_anio'], aggfunc=np.sum, margins=True, margins_name='total', fill_value=0)
    df_mes = df_mes.stack(0).unstack().sort_index(axis=1, level=0)
    df_mes.columns = ['_'.join(col) for col in df_mes.columns]
    mes_cols = [col for col in df_mes.columns if re.search(r'\d{4}-\d{2}', col) and not col.startswith('total')]
    df_mes['count'] = df_mes[mes_cols].astype(bool).sum(axis=1)
    q_days_series = df[[selected_dimension, 'q_days']].drop_duplicates().set_index(selected_dimension)
    df_mes = df_mes.join(q_days_series)
    df_mes = df_mes.sort_values(by=['q_days', 'count', 'total_clicks', 'total_impressions'],
                                 ascending=[False, False, False, False])
    df_mes = df_mes.reset_index()
    if 0 in df_mes.index:
        df_mes.drop(0, inplace=True)
    df_mes.dropna(inplace=True)
    return df_mes

# ------------- Utilidades -------------
def property_change():
    st.session_state.selected_property = st.session_state['selected_property_selector']

def get_dates(meses=3):
    hoy = date.today()
    fecha_fin = date(hoy.year, hoy.month, 1) - timedelta(days=1)
    fecha_inicio = fecha_fin - relativedelta(months=meses-1)
    fecha_inicio = date(fecha_inicio.year, fecha_inicio.month, 1)
    return fecha_inicio.strftime("%Y-%m-%d"), fecha_fin.strftime("%Y-%m-%d")

def elapsed_time_text(elapsed_time):
    if elapsed_time >= 60:
        minutes = int(elapsed_time // 60); seconds = int(elapsed_time % 60)
        return f"{minutes} minutos y {seconds} segundos"
    return f"{elapsed_time:.2f} segundos"

def show_date_range_selector():
    selected_text = st.selectbox("Selecciona el rango de fechas:", list(DATE_RANGE_OPTIONS.keys()), key='date_range_selector')
    return DATE_RANGE_OPTIONS[selected_text]

def show_brand_term_input():
    brand_term = st.text_input("Ingrese los términos de marca separados por coma (recomendado):")
    st.session_state.brand_term = brand_term
    return brand_term

def show_dimensions_selector():
    selected_dimension = st.radio("Elegir la dimensión ('query' no funciona en Discover):",
                                  ["query", "page"], horizontal=True, index=1)
    st.session_state.selected_dimension = selected_dimension
    return selected_dimension

def show_search_type_selector():
    selected_search_type = st.radio("Elegir Web o Discover:", ["web", "discover"], horizontal=True, index=1)
    st.session_state.selected_search_type = selected_search_type
    return selected_search_type

def show_dataframe(report):
    with st.expander(f"Mostrar las primeras {DF_PREVIEW_ROWS} filas"):
        st.dataframe(report.head(DF_PREVIEW_ROWS))

def show_property_selector(properties, account):
    selected_property = st.selectbox(
        "Seleccione una propiedad de Search Console:",
        properties,
        index=properties.index(st.session_state.selected_property) if st.session_state.selected_property in properties else 0,
        key='selected_property_selector',
        on_change=property_change
    )
    return account[selected_property]

def show_fetch_data_button(webproperty, start_date, end_date, selected_search_type, selected_dimension, brand_term):
    report = None
    if st.button("Obtener Evergreen"):
        start_time = time.time()
        with st.spinner("Procesando datos (esto puede tardar unos minutos)..."):
            report = get_evergreen(webproperty, start_date, end_date, selected_search_type, selected_dimension, brand_term)
        if report is not None:
            show_dataframe(report)
            with st.spinner("Generando CSV..."):
                download_csv(report, webproperty)
            st.write("")
            elapsed_time = time.time() - start_time
            st.caption(f"Proceso completado en {elapsed_time_text(elapsed_time)} ✅")

def extract_full_domain(input_string):
    match = re.search(r"(?:https?://(?:www\.)?|sc-domain:)([\w\-\.]+)\.([\w\-]+)", input_string)
    if match:
        full_domain = match.group(1) + match.group(2)
        return full_domain.replace('.', '_')
    return ""

def download_csv(report, webproperty):
    csv = report.to_csv(index=False, encoding='utf-8')
    property_name = extract_full_domain(webproperty.url)
    b64_csv = base64.b64encode(csv.encode()).decode()
    href = f"""<a href="data:file/csv;base64,{b64_csv}" download="evergreen_report_{property_name}_{int(time.time())}.csv">Descargar como CSV</a>"""
    st.markdown(href, unsafe_allow_html=True)

# ------------- MAIN -------------
def main():
    setup_streamlit()

    # Pide nombre primero
    nombre = st.text_input("Tu nombre")
    if nombre:
        st.session_state["nombre"] = nombre

    client_config, redirect_uri = load_config()
    auth_flow, auth_url = google_auth(client_config, redirect_uri)

    # Si volvemos de Google con ?code=...
    qp = getattr(st, "query_params", None) or st.experimental_get_query_params()
    code = None
    if qp and "code" in qp and not st.session_state.get('credentials'):
        code = qp["code"][0] if isinstance(qp["code"], list) else qp["code"]

    if code and not st.session_state.get('credentials'):
        with st.spinner("Intercambiando código por tokens..."):
            try:
                st.session_state.auth_flow.fetch_token(code=code)
                st.session_state.credentials = st.session_state.auth_flow.credentials
                try:
                    st.query_params.clear()
                except Exception:
                    st.experimental_set_query_params()
                st.rerun()
            except Exception as e:
                st.error(f"Error al autenticar: {e}")

    if not st.session_state.get('credentials'):
        if not nombre:
            st.info("Ingresá tu nombre para continuar.")
        else:
            st.write(f"¡Hola **{nombre}**! Iniciá sesión con Google para continuar.")
            if st.button("🔓 Autentificarse con Google", type="primary", use_container_width=True):
                # Redirige en la misma pestaña para preservar session_state (state PKCE del Flow)
                st.markdown(f"""<script>window.location.href = "{auth_url}";</script>""", unsafe_allow_html=True)
                st.stop()

        with st.expander("Detalles técnicos (ayuda)"):
            st.code(f"REDIRECT_URI: {redirect_uri}\nCLIENT_ID: {client_config['installed']['client_id']}\nScopes: https://www.googleapis.com/auth/webmasters")

    else:
        st.success(f"¡Hola {st.session_state.get('nombre','')}! Estás autenticado.")
        init_session_state()
        account = auth_search_console(client_config, st.session_state.credentials)
        properties = list_gsc_properties(st.session_state.credentials)

        if properties:
            webproperty = show_property_selector(properties, account)
            date_range_selection = show_date_range_selector()
            start_date, end_date = get_dates(date_range_selection)
            brand_term = show_brand_term_input()
            selected_search_type = show_search_type_selector()
            selected_dimension = show_dimensions_selector()
            show_fetch_data_button(webproperty, start_date, end_date, selected_search_type, selected_dimension, brand_term)

        if st.button("Cerrar sesión"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            try:
                st.query_params.clear()
            except Exception:
                st.experimental_set_query_params()
            st.success("Sesión cerrada.")
            st.rerun()

if __name__ == "__main__":
    main()
