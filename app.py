import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import datetime, time, os
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import pandas as pd
import base64, re, traceback
import gscwrapper, logging
import numpy as np
import warnings
warnings.simplefilter("ignore", category=FutureWarning)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # No hace nada si dotenv no está instalado



# -------------
# Constants
# -------------

DATE_RANGE_OPTIONS = {
    'Últimos 3 meses': 3,
    'Últimos 6 meses': 6,
    'Últimos 12 meses': 12,
}


DF_PREVIEW_ROWS = 100

MAX_ROWS = 1_000_000


# -------------
# Streamlit App Configuration
# -------------

def setup_streamlit():
    """
    Configures Streamlit's page settings and displays the app title and markdown information.
    Sets the page layout, title, and markdown content with links and app description.
    """
    st.set_page_config(page_title="GSC | Evergreen Queries y Pages", page_icon="🟩")
    st.title("🟩 GSC | Evergreen Queries y Pages")
    st.write()
    st.write("Esta app permite ver páginas o queries que se mantienen con clicks e impresiones a lo largo del tiempo.")
    st.write("Toma rangos a mes cerrado de 3, 6 o 12 meses.")
    st.write("""La tabla final muestra clicks e impresiones por mes, los totales, 
             la cantidad de meses con data (clicks e impresiones para cada mes) y la cantidad de días con data.""")
             
             
    st.caption(f"[Creado por Damián Taubaso](https://www.linkedin.com/in/dtaubaso/)")
    st.divider()


def init_session_state():
    """
    Initialises or updates the Streamlit session state variables for property selection,
    search type, date range, dimensions, and device type.
    """
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

# -------------
# Google Authentication Functions
# -------------

def load_config():
    """
    Loads the Google API client configuration from Streamlit secrets.
    Returns a dictionary with the client configuration for OAuth.
    """
    client_config = {
        "installed": {
            "client_id": st.secrets['CLIENT_ID'],
            "client_secret": st.secrets['CLIENT_SECRET'],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://accounts.google.com/o/oauth2/token",
            "redirect_uris": st.secrets['REDIRECT_URI'],
        }
    }
    return client_config

def init_oauth_flow(client_config):
    """
    Initialises the OAuth flow for Google API authentication using the client configuration.
    Sets the necessary scopes and returns the configured Flow object.
    """
    scopes = ["https://www.googleapis.com/auth/webmasters"]
    return Flow.from_client_config(
        client_config,
        scopes=scopes,
        redirect_uri=client_config["installed"]["redirect_uris"],
    )

def google_auth(client_config):
    """
    Starts the Google authentication process using OAuth.
    Generates and returns the OAuth flow and the authentication URL.
    """
    flow = init_oauth_flow(client_config)
    auth_url, _ = flow.authorization_url(prompt="consent")
    return flow, auth_url


def auth_search_console(client_config, credentials):
    """
    Authenticates the user with the Google Search Console API using provided credentials.
    Returns an authenticated searchconsole client.
    """
    token = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
        "id_token": getattr(credentials, "id_token", None),
    }
    return gscwrapper.generate_auth(client_config=client_config, credentials=token)


# -------------
# Data Fetching Functions
# -------------


def list_gsc_properties(credentials):
    """
    Lists all Google Search Console properties accessible with the given credentials.
    Returns a list of property URLs or a message if no properties are found.
    """
    service = build('webmasters', 'v3', credentials=credentials)
    site_list = service.sites().list().execute()
    return [site['siteUrl'] for site in site_list.get('siteEntry', [])] or ["No se encontraron propiedades"]


def fetch_query_page(webproperty, start_date, end_date, selected_search_type, selected_dimension):
    """
    Fetches Google Search Console data for a specified property, date range, and device type.
    Handles errors and returns the data as a DataFrame.
    """

    try:
        query = webproperty.query.range(start_date, end_date).dimensions([selected_dimension, "date"]).search_type(selected_search_type)
        
        df = (query.limit(MAX_ROWS).get()).df
    
        if df.empty:
            raise Exception("No hay Dataframe. Revise sus datos.")
        return df
    
    except Exception as e:
        logging.error(traceback.format_exc())
        st.error(e)
        return pd.DataFrame()

def get_evergreen(webproperty, start_date, end_date, selected_search_type, selected_dimension, brand_term=None):
    
    """
    Fetch Search Console data and rearrange it to show evergreen queries or pages
    """
    if selected_search_type == 'discover':
        selected_dimension = 'page'

    df = fetch_query_page(webproperty, start_date, end_date, selected_search_type, selected_dimension)
    if df.empty:
        raise Exception("No hay Dataframe")

    # filtra brand term

    if brand_term and selected_dimension=='query':
        brand_term = "|".join(list(map(str.strip, brand_term.split(','))))
        df = df[~df['query'].str.contains(brand_term)]
    
    # ordenar por dia
    df = df.sort_values('date').reset_index(drop=True)
    df['q_days'] = df.groupby(selected_dimension)[selected_dimension].transform('count')
    # crear una columna solo con mes y año
    df['mes_anio'] = df['date'].apply(lambda x: re.findall('(.*-.*)-', x)[0])
    # agrupar urls
    df = df.groupby([selected_dimension, 'mes_anio', 'q_days']).sum(numeric_only=True).reset_index()
    # pivotear por mes
    df_mes = pd.pivot_table(df, values=['clicks', 'impressions'], index=[selected_dimension],
                            columns=['mes_anio'], aggfunc=np.sum, margins=True, margins_name='total', fill_value=0)
    # Reorganizar las columnas para que los meses sean el nivel primario y los valores sean subcolumnas
    df_mes = df_mes.stack(0).unstack().sort_index(axis=1, level=0)
    
    # Aplanar los nombres de columnas para evitar problemas al hacer join()
    df_mes.columns = ['_'.join(col) for col in df_mes.columns]
    
    # Calcular "count" sumando solo las columnas correspondientes a meses
    mes_cols = [col for col in df_mes.columns if re.search(r'\d{4}-\d{2}', col) and not col.startswith('total')]
    df_mes['count'] = df_mes[mes_cols].astype(bool).sum(axis=1)
    
    # Agregar la columna "q_days" a df_mes
    q_days_series = df[[selected_dimension, 'q_days']].drop_duplicates().set_index(selected_dimension)
    df_mes = df_mes.join(q_days_series)
    
    # Ordenar por la cuenta y los clicks totales
    df_mes = df_mes.sort_values(by=['q_days', 'count', 'total_clicks', 'total_impressions'],
                                 ascending=[False, False, False, False])
    # Resetear index para que la dimensión quede en una columna propia y no como index
    df_mes = df_mes.reset_index()
    df_mes.drop(0, inplace=True)
    df_mes.dropna(inplace=True)
    return df_mes

# -------------
# Utility Functions
# -------------

def property_change():
    """
    Updates the 'selected_property' in the Streamlit session state.
    Triggered on change of the property selection.
    """
    st.session_state.selected_property = st.session_state['selected_property_selector']

def get_dates(meses = 3):
    hoy = date.today()
    fecha_fin = date(hoy.year, hoy.month, 1) - timedelta(days=1)  # Último día del mes anterior
    fecha_inicio = fecha_fin - relativedelta(months=meses-1)
    fecha_inicio = date(fecha_inicio.year, fecha_inicio.month, 1)  # Primer día del mes de inicio
    
    return fecha_inicio.strftime("%Y-%m-%d"), fecha_fin.strftime("%Y-%m-%d")

def elapsed_time_text(elapsed_time):
    if elapsed_time >= 60:
        minutes = int(elapsed_time // 60)
        seconds = int(elapsed_time % 60)
        return f"{minutes} minutos y {seconds} segundos"
    else:
        return f"{elapsed_time:.2f} segundos"

# -------------
# Streamlit UI Components
# -------------

def show_date_range_selector():
    """
    Displays a dropdown selector for choosing the date range.
    Returns the selected date range option.
    """

    selected_text = st.selectbox("Selecciona el rango de fechas:", 
                                 list(DATE_RANGE_OPTIONS.keys()), 
                                      key='date_range_selector')
    return DATE_RANGE_OPTIONS[selected_text]




def show_brand_term_input():
    """
    Displays text input fields for brand terms.
    Updates session state with the terms.
    """
    brand_term = st.text_input("Ingrese los términos de marca separados por coma (recomendado):")

    st.session_state.brand_term = brand_term
    
    return brand_term


def show_dimensions_selector():
    """
    Displays a radio selector for choosing the dimensions.
    """
    selected_dimension = st.radio("Elegir la dimensión ('query' no funciona en Discover):",
                       ["query", "page"], horizontal = True, index=1)
    
    st.session_state.selected_dimension = selected_dimension

    return selected_dimension

def show_search_type_selector():
    """
    Displays a radio selector for choosing the search type.
    """
    selected_search_type = st.radio("Elegir Web o Discover:",
                       ["web", "discover"], horizontal = True, index=1)
    
    st.session_state.selected_search_type = selected_search_type

    return selected_search_type

def show_dataframe(report):
    """
    Shows a preview of the first 100 rows of the report DataFrame in an expandable section.
    """
    with st.expander(f"Mostrar las primeras {DF_PREVIEW_ROWS} filas"):
        st.dataframe(report.head(DF_PREVIEW_ROWS))



def show_property_selector(properties, account):
    """
    Displays a dropdown selector for Google Search Console properties.
    Returns the selected property's webproperty object.
    """
    selected_property = st.selectbox(
        "Seleccione una propiedad de Search Console:",
        properties,
        index=properties.index(
            st.session_state.selected_property) if st.session_state.selected_property in properties else 0,
        key='selected_property_selector',
        on_change=property_change
    )
    return account[selected_property]

def show_fetch_data_button(webproperty, start_date, end_date, selected_search_type, selected_dimension, brand_term):
    """
    Displays a button to fetch data based on selected parameters.
    Shows the report DataFrame and download link upon successful data fetching.
    """
    report = None

    if st.button("Obtener Evergreen"):
        start_time = time.time()
        with st.spinner("Procesando datos (esto puede tardar unos minutos)..."):  # Spinner real mientras se ejecuta la función
            report = get_evergreen(webproperty, start_date, end_date, selected_search_type, selected_dimension, brand_term)

        if report is not None:
            show_dataframe(report)  # Puede tener un st.expander() sin problema
            with st.spinner("Generando CSV..."):
                download_csv(report, webproperty)
            st.write("")
            elapsed_time = time.time() - start_time
            time_text = elapsed_time_text(elapsed_time)
            st.caption(f"Proceso completado en {time_text} ✅")


# -------------
# File & Download Operations
# -------------

def extract_full_domain(input_string):
    # Expresión regular para capturar todos los segmentos del dominio
    match = re.search(r"(?:https?://(?:www\.)?|sc-domain:)([\w\-\.]+)\.([\w\-]+)", input_string)
    if match:
        # Quitar los puntos y concatenar todas las partes
        full_domain = match.group(1) + match.group(2)
        return full_domain.replace('.', '_')
    return ""

def download_csv(report, webproperty):
    """
    Generates and displays a download link for the report DataFrame in CSV format.
    """
    csv = report.to_csv(index=False, encoding='utf-8')
    property_name = extract_full_domain(webproperty.url)
    b64_csv = base64.b64encode(csv.encode()).decode()
    href = f"""<a href="data:file/csv;base64,{b64_csv}" download="evergreen_report_{property_name}_{int(time.time())}.csv">
    Descargar como CSV</a>"""
    st.markdown(href, unsafe_allow_html=True)

# -------------
# Main Streamlit App Function
# -------------

def main():
    """
    The main function for the Streamlit application.
    Handles the app setup, authentication, UI components, and data fetching logic.
    """
    start_time = time.time()
    setup_streamlit()
    client_config = load_config()
    st.session_state.auth_flow, st.session_state.auth_url = google_auth(client_config)
    auth_code = None
    if "code" in st.query_params:
        auth_code = st.query_params['code']
    if auth_code and not st.session_state.get('credentials'):
        st.session_state.auth_flow.fetch_token(code=auth_code)
        st.session_state.credentials = st.session_state.auth_flow.credentials

    if not st.session_state.get('credentials'):
        if st.button("Autentificarse con Google"):
            # Open the authentication URL
            st.write('Ingrese al siguiente link:')
            st.write(f'[Google Sign-In]({st.session_state.auth_url})')
            st.write('No se guardarán sus datos')
    else:
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


if __name__ == "__main__":
    main()
