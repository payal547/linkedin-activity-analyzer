import requests
from typing import Tuple, List, Dict, Any


def fetch_linkedin_activities(access_token: str, author_urn: str = None) -> Tuple[bool, List[Dict[str, Any]], str]:
    """
    Attempt to fetch real activity/posts from LinkedIn API.
    Standard OpenID Connect does not grant member activity feed access.
    Returns (success, activities_list, status_message).
    """
    if not access_token:
        return False, [], "No access token provided."

    url = "https://api.linkedin.com/rest/posts"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": "202401",
        "X-Restli-Protocol-Version": "2.0.0"
    }
    params = {"q": "author", "count": 50}
    if author_urn:
        params["author"] = author_urn

    try:
        response = requests.get(url, headers=headers, params=params, timeout=8)
        if response.status_code == 200:
            data = response.json()
            elements = data.get("elements", [])
            activities = []
            for item in elements:
                activities.append({
                    "id": item.get("id", ""),
                    "date": (item.get("publishedAt") or "")[:10],
                    "type": "post",
                    "text": item.get("commentary", "")[:500],
                    "likes": item.get("likesCount", 0),
                    "comments": item.get("commentsCount", 0),
                    "shares": item.get("sharesCount", 0),
                    "source": "real_linkedin"
                })
            return True, activities, "Real activities loaded from LinkedIn API."

        return (
            False,
            [],
            "LinkedIn member activity access requires approved member social permissions (r_member_social). Standard OpenID scopes only provide basic profile access."
        )
    except requests.RequestException as e:
        return False, [], f"Network error when querying LinkedIn Activity API: {str(e)}"

