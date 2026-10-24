import requests
from typing import Tuple, List, Dict, Any


def fetch_linkedin_connections(access_token: str) -> Tuple[bool, List[Dict[str, Any]], str]:
    """
    Attempt to fetch real 1st-degree connections from LinkedIn API.
    Returns (success, connections_list, status_message).
    LinkedIn requires Enterprise Partner approval (r_1st_connections) to access this endpoint.
    If permissions are restricted (403), returns an honest explanation without throwing errors.
    """
    if not access_token:
        return False, [], "No access token provided."

    url = "https://api.linkedin.com/v2/connections"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": "2.0.0"
    }
    params = {
        "q": "viewer",
        "start": 0,
        "count": 50
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=8)

        if response.status_code == 200:
            data = response.json()
            raw_elements = data.get("elements", [])
            connections = []
            for item in raw_elements:
                connections.append({
                    "id": item.get("id") or item.get("entityUrn", ""),
                    "name": item.get("name") or f"{item.get('firstName', '')} {item.get('lastName', '')}".strip() or "LinkedIn Connection",
                    "headline": item.get("headline", "LinkedIn Member"),
                    "picture": item.get("picture"),
                    "source": "real_linkedin"
                })
            return True, connections, "Real connections loaded from LinkedIn API."

        if response.status_code in (401, 403):
            return (
                False,
                [],
                "LinkedIn restricts live Connections API access to approved Enterprise Partner applications (r_1st_connections). Standard OpenID applications cannot download connection lists directly."
            )

        return False, [], f"LinkedIn Connections API returned HTTP {response.status_code}."

    except requests.RequestException as e:
        return False, [], f"Network error when querying LinkedIn Connections API: {str(e)}"

