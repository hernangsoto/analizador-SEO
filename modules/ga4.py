# modules/ga4.py
from __future__ import annotations

def ensure_data_client(creds):
    """
    Devuelve un cliente de Google Analytics Data API (v1) con OAuth de usuario/installed app.
    """
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        # Nota: si preferís v1 estable en tu entorno, podés usar:
        # from google.analytics.data_v1 import AnalyticsDataClient
        # y reemplazar BetaAnalyticsDataClient por AnalyticsDataClient.
    except Exception as e:
        raise RuntimeError(
            "Falta 'google-analytics-data'. Instalalo con: pip install google-analytics-data"
        ) from e

    client = BetaAnalyticsDataClient(credentials=creds)
    return client
