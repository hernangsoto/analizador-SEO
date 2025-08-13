import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import gscwrapper
import datetime, time
import pandas as pd
import base64, re



# Constants
SEARCH_TYPES = ["web", "image", "video", "news", "discover", "googleNews"]
DATE_RANGE_OPTIONS = [
    'Últimos 7 días',
    'Últimos 30  días',
    'Últimos 3 meses',
    'Últimos 6 meses',
    'Últimos 12 meses',
    'Últimos 16 meses',
    'Elegir fechas'
]
DEVICE_OPTIONS = ["Todos", "desktop", "mobile", "tablet"]
BASE_DIMENSIONS = ["page", "query", "country", "date", "device", "hour"]
MAX_ROWS = 1_000_000
DF_PREVIEW_ROWS = 100

def setup_streamlit():
    """
    Configures Streamlit's page settings and displays the app title and markdown information.
    Sets the page layout, title, and markdown content with links and app description.
    """
    st.set_page_config(page_title="Simple GSC Api Connector")
    st.title("Simple GSC Api Connector")
    url = "https://github.com/searchsolved/search-solved-public-seo/tree/main/search-console/streamlit-simple-gsc-connector"
    st.caption(f"[Código original por Lee Foot](url)")
    st.caption(("[Actualizado por Damián Taubaso](https://www.linkedin.com/in/dtaubaso/)"))
    # https://i.pinimg.com/1200x/b4/28/5b/b4285b927f370a8407ed8415a11f8c91.jpg
    st.divider()


def init_session_state():
    """
    Initialises or updates the Streamlit session state variables for property selection,
    search type, date range, dimensions, and device type.
    """
    if 'selected_property' not in st.session_state:
        st.session_state.selected_property = None
    if 'selected_search_type' not in st.session_state:
        st.session_state.selected_search_type = 'web'
    if 'selected_date_range' not in st.session_state:
        st.session_state.selected_date_range = 'Últimos 7 días'
    if 'start_date' not in st.session_state:
        st.session_state.start_date = datetime.date.today() - datetime.timedelta(days=7)
    if 'end_date' not in st.session_state:
        st.session_state.end_date = datetime.date.today()
    if 'selected_dimensions' not in st.session_state:
        st.session_state.selected_dimensions = ['page', 'query']
    if 'selected_device' not in st.session_state:
        st.session_state.selected_device = 'Todos'
    if 'custom_start_date' not in st.session_state:
        st.session_state.custom_start_date = datetime.date.today() - datetime.timedelta(days=7)
    if 'custom_end_date' not in st.session_state:
        st.session_state.custom_end_date = datetime.date.today()



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
            "redirect_uris": [st.secrets['REDIRECT_URI']],
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
        redirect_uri=client_config["installed"]["redirect_uris"][0],
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


def list_gsc_properties(credentials):
    """
    Lists all Google Search Console properties accessible with the given credentials.
    Returns a list of property URLs or a message if no properties are found.
    """
    service = build('webmasters', 'v3', credentials=credentials)
    site_list = service.sites().list().execute()
    return [site['siteUrl'] for site in site_list.get('siteEntry', [])] or ["No se encontraron propiedades"]


def fetch_gsc_data(webproperty, search_type, start_date, end_date, dimensions, device_type=None):
    """
    Fetches Google Search Console data for a specified property, date range, dimensions, and device type.
    Handles errors and returns the data as a DataFrame.
    """

    start_date = start_date.strftime("%Y-%m-%d") 
    end_date = end_date.strftime("%Y-%m-%d") 

    data_state = "final"
    if "hour" in dimensions:
        data_state = "hourly_all"

    query = (webproperty.query.range(start_date, end_date)
             .search_type(search_type).dimensions(dimensions).data_state(data_state))

    if device_type and device_type != 'Todos':
        query = query.filter('device', device_type.lower(), 'equals')

    try:
        return (query.limit(MAX_ROWS).get()).df
    except Exception as e:
        show_error(e)
        return pd.DataFrame()


def fetch_data_loading(webproperty, search_type, start_date, end_date, dimensions, device_type=None):
    """
    Fetches Google Search Console data with a loading indicator. Utilises 'fetch_gsc_data' for data retrieval.
    Returns the fetched data as a DataFrame.
    """
    with st.spinner('Extrayendo data...'):
        return fetch_gsc_data(webproperty, search_type, start_date, end_date, dimensions, device_type)


# -------------
# Utility Functions
# -------------

def update_dimensions(selected_search_type):
    """
    Updates and returns the list of dimensions based on the selected search type.
    """
    if selected_search_type == 'discover':
        discover_dimensions = [dimension for dimension in BASE_DIMENSIONS if dimension not in ['query', 'device']]
        return discover_dimensions
    elif selected_search_type == 'googleNews':
        return [dimension for dimension in BASE_DIMENSIONS if dimension != 'query']
    else:
        return BASE_DIMENSIONS

def calc_date_range(selection, custom_start=None, custom_end=None):
    """
    Calculates the date range based on the selected range option.
    Returns the start and end dates for the specified range.
    """
    range_map = {
        'Últimos 7 días': 7,
        'Últimos 30  días': 30,
        'Últimos 3 meses': 90,
        'Últimos 6 meses': 180,
        'Últimos 12 meses': 365,
        'Últimos 16 meses': 480
    }
    today = datetime.date.today()
    if selection == 'Elegir fechas':
        if custom_start and custom_end:
            return custom_start, custom_end
        else:
            return today - datetime.timedelta(days=7), today
    return today - datetime.timedelta(days=range_map.get(selection, 0)), today


def show_error(e):
    """
    Displays an error message in the Streamlit app.
    Formats and shows the provided error 'e'.
    """
    st.error(f"Ocurrió un error: {e}")


def property_change():
    """
    Updates the 'selected_property' in the Streamlit session state.
    Triggered on change of the property selection.
    """
    st.session_state.selected_property = st.session_state['selected_property_selector']

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

def show_search_type_selector():
    """
    Displays a dropdown selector for choosing the search type.
    Returns the selected search type.
    """
    return st.selectbox(
        "Seleccione tipo de búsqueda (chequee que la propiedad no se haya modificado):",
        SEARCH_TYPES,
        index=SEARCH_TYPES.index(st.session_state.selected_search_type),
        key='search_type_selector'
    )

def show_device_selector():
    """
    Displays a dropdown selector for choosing the device.
    Returns the selected device.
    """
    # Asegúrate de que el valor predeterminado sea válido
    default_index = DEVICE_OPTIONS.index(st.session_state.selected_device) \
        if st.session_state.selected_device in DEVICE_OPTIONS else 0

    # Muestra el selector
    selected_device = st.selectbox(
        "Seleccione dispositivo:",
        DEVICE_OPTIONS,
        index=default_index,
        key='device_selector'
    )

    # Actualiza el estado
    st.session_state.selected_device = selected_device
    return selected_device


def show_date_range_selector():
    """
    Displays a dropdown selector for choosing the date range.
    Returns the selected date range option.
    """
    return st.selectbox(
        "Seleccione el rango de fechas:",
        DATE_RANGE_OPTIONS,
        index=DATE_RANGE_OPTIONS.index(st.session_state.selected_date_range),
        key='date_range_selector'
    )


def show_custom_date_inputs():
    """
    Displays date input fields for custom date range selection.
    Updates session state with the selected dates.
    """
    st.session_state.custom_start_date = st.date_input("Fecha inicio", st.session_state.custom_start_date)
    st.session_state.custom_end_date = st.date_input("Fecha fin", st.session_state.custom_end_date)


# def show_dimensions_selector(search_type):
#     """
#     Displays a multi-select box for choosing dimensions based on the selected search type.
#     Returns the selected dimensions.
#     """
#     available_dimensions = update_dimensions(search_type)
    
#     # Filtra las dimensiones seleccionadas que no están en las opciones disponibles
#     default_dimensions = [
#         dim for dim in st.session_state.selected_dimensions 
#         if dim in available_dimensions
#     ]
    
#     # Muestra el selector con valores por defecto válidos
#     selected_dimensions = st.multiselect(
#         "Seleccione Dimensiones:",
#         available_dimensions,
#         default=default_dimensions,
#         key='dimensions_selector'
#     )
    
#     # Actualiza las dimensiones seleccionadas en el estado
#     st.session_state.selected_dimensions = selected_dimensions
    
#     return selected_dimensions



def show_dimensions_selector(search_type):
    """
    Displays a multi-select box for choosing dimensions based on the selected search type.
    Prevents selecting both 'hour' and 'date' at the same time.
    Returns the selected dimensions.
    """
    available_dimensions = update_dimensions(search_type)
    
    # Aplica la lógica para evitar seleccionar 'hour' y 'date' al mismo tiempo
    current_selection = st.session_state.get('selected_dimensions', [])

    # Elimina 'date' si ya está 'hour' seleccionado
    if 'hour' in current_selection:
        filtered_dimensions = [dim for dim in available_dimensions if dim != 'date']
    # Elimina 'hour' si ya está 'date' seleccionado
    elif 'date' in current_selection:
        filtered_dimensions = [dim for dim in available_dimensions if dim != 'hour']
    else:
        filtered_dimensions = available_dimensions

    # Filtra las dimensiones seleccionadas que todavía están en las opciones
    default_dimensions = [dim for dim in current_selection if dim in filtered_dimensions]

    # Muestra el multiselect
    selected_dimensions = st.multiselect(
        'Seleccione Dimensiones (no se pueden seleccionar "hour" y "date" a la vez):',
        filtered_dimensions,
        default=default_dimensions,
        key='dimensions_selector'
    )

    # Actualiza el estado
    st.session_state.selected_dimensions = selected_dimensions

    return selected_dimensions


def show_fetch_data_button(webproperty, search_type, start_date, end_date, selected_dimensions, device_type):
    """
    Displays a button to fetch data based on selected parameters.
    Shows the report DataFrame and download link upon successful data fetching.
    """
    report = None
    if st.button("Extraer Data"):
        with st.spinner("Procesando..."):
            report = fetch_data_loading(webproperty, search_type, start_date, end_date, selected_dimensions, device_type)

        if report is not None:
            show_dataframe(report)
            with st.spinner("Generando CSV..."):
                download_csv(report, webproperty)
            st.write("")
            st.caption("Proceso completado ✅")


# -------------
# File & Download Operations
# -------------

def show_dataframe(report):
    """
    Shows a preview of the first 100 rows of the report DataFrame in an expandable section.
    """
    with st.expander(f"Mostrar las primeras {DF_PREVIEW_ROWS} filas"):
        st.dataframe(report.head(DF_PREVIEW_ROWS))


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
    href = f'<a href="data:file/csv;base64,{b64_csv}" download="gsc_report_{property_name}_{int(time.time())}.csv">Descargar como CSV</a>'
    st.markdown(href, unsafe_allow_html=True)

def main():
    """
    The main function for the Streamlit application.
    Handles the app setup, authentication, UI components, and data fetching logic.
    """
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
            device = None
            webproperty = show_property_selector(properties, account)
            search_type = show_search_type_selector()
            if search_type != 'discover':
                device = show_device_selector()
            date_range_selection = show_date_range_selector()
            if date_range_selection == 'Elegir fechas':
                show_custom_date_inputs()
                start_date, end_date = st.session_state.custom_start_date, st.session_state.custom_end_date
            else:
                start_date, end_date = calc_date_range(date_range_selection)

            selected_dimensions = show_dimensions_selector(search_type)
            show_fetch_data_button(webproperty, search_type, start_date, end_date, selected_dimensions, device)


if __name__ == "__main__":
    main()
