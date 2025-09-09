# modules/ga4_admin.py
from __future__ import annotations
from typing import List, Dict

def ensure_admin_client(creds):
    """
    Devuelve un cliente de Google Analytics Admin API usando credenciales OAuth (usuario o installed app).
    """
    try:
        from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
        from google.api_core.client_options import ClientOptions
    except Exception as e:
        raise RuntimeError(
            "Falta el paquete 'google-analytics-admin'. Instalalo con: pip install google-analytics-admin"
        ) from e

    # El cliente acepta 'credentials=' directamente
    client = AnalyticsAdminServiceClient(credentials=creds, client_options=ClientOptions())
    return client


def list_account_property_summaries(admin_client) -> List[Dict]:
    """
    Retorna una lista plana de propiedades GA4 accesibles por el usuario:
    [{account_name, account, property, property_display_name, property_id}]
    """
    out: List[Dict] = []
    pager = admin_client.list_account_summaries()
    for acc in pager:
        acc_name   = acc.display_name or ""
        acc_res    = acc.account or ""           # "accounts/XXXX"
        for p in acc.property_summaries:
            prop_res = p.property or ""          # "properties/NNNNNNNNN"
            prop_id  = prop_res.split("/")[-1] if prop_res else ""
            out.append({
                "account_name": acc_name,
                "account": acc_res,
                "property": prop_res,
                "property_id": prop_id,
                "property_display_name": p.display_name or prop_id,
            })
    return out
