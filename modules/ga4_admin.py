# modules/ga4_admin.py
from __future__ import annotations
from typing import List, Dict

from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
from google.api_core.exceptions import PermissionDenied, GoogleAPICallError


def build_admin_client(credentials) -> AnalyticsAdminServiceClient:
    return AnalyticsAdminServiceClient(credentials=credentials)


def list_account_property_summaries(admin_client: AnalyticsAdminServiceClient) -> List[Dict]:
    """
    Devuelve una lista de dicts:
      account_name, account, property_id, property_display_name
    """
    try:
        pager = admin_client.list_account_summaries()
    except PermissionDenied as e:
        raise PermissionError("GA4_ADMIN_PERMISSION") from e
    except GoogleAPICallError as e:
        raise RuntimeError(f"GA4_ADMIN_OTHER: {e.message}") from e
    except Exception as e:
        raise RuntimeError(f"GA4_ADMIN_UNEXPECTED: {e}") from e

    out: List[Dict] = []
    for acc in pager:
        acc_name = acc.display_name or acc.account or "—"
        for p in acc.property_summaries:
            prop_id = (p.property or "").split("/")[-1]
            out.append({
                "account_name": acc_name,
                "account": acc.account,
                "property_id": prop_id,
                "property_display_name": p.display_name or p.property,
            })
    return out