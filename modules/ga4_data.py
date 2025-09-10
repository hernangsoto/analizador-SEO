# modules/ga4_data.py
from __future__ import annotations
from google.analytics.data_v1beta import BetaAnalyticsDataClient


def build_data_client(credentials) -> BetaAnalyticsDataClient:
    return BetaAnalyticsDataClient(credentials=credentials)