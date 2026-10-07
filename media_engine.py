from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from lead_finder import load_google_api_key

GOOGLE_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
SOCIAL_DOMAINS = {
    "facebook.com": "Facebook", "instagram.com": "Instagram",
    "tiktok.com": "TikTok", "youtube.com": "YouTube", "youtu.be": "YouTube",
    "x.com": "X", "twitter.com": "X", "linkedin.com": "LinkedIn",
    "pinterest.com": "Pinterest",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}


def _clean_url(value: str) -> str:
    value = html.unescape((value or "").strip())
    return "https:" + value if value.startswith("//") else value


def _social_platform(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for domain, label in SOCIAL_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return label
    return ""


def inspect_public_website(website_url: str) -> dict[str, Any]:
    result = {"social_links": [], "website_images": [], "error": ""}
    if not website_url:
        return result
    try:
        response = requests.get(website_url, headers=HEADERS, timeout=12, allow_redirects=True)
        response.raise_for_status()
        if "html" not in response.headers.get("Content-Type", "").lower():
            return result
        body = response.text[:1500000]
        base_url = response.url
        hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', body, flags=re.I)
        seen_social = set()
        for href in hrefs:
            absolute = urljoin(base_url, _clean_url(href))
            platform = _social_platform(absolute)
            if platform and absolute not in seen_social:
                seen_social.add(absolute)
                result["social_links"].append({"platform": platform, "url": absolute})
        patterns = [
            r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, body, flags=re.I)
            if match:
                result["website_images"].append({"url": urljoin(base_url, _clean_url(match.group(1))), "source": "Official website", "attribution": ""})
                break
        image_matches = re.findall(r'<img[^>]+(?:src|data-src)\s*=\s*["\']([^"\']+)["\'][^>]*>', body, flags=re.I)
        seen_images = {x["url"] for x in result["website_images"]}
        for raw_url in image_matches:
            image_url = urljoin(base_url, _clean_url(raw_url))
            low = image_url.lower()
            if image_url in seen_images or low.endswith((".svg", ".gif")) or any(t in low for t in ("logo", "icon", "pixel", "spinner")):
                continue
            seen_images.add(image_url)
            result["website_images"].append({"url": image_url, "source": "Official website", "attribution": ""})
            if len(result["website_images"]) >= 4:
                break
    except requests.RequestException as exc:
        result["error"] = str(exc)
    return result


def get_google_place_assets(business_name: str, city: str, max_photos: int = 6) -> dict[str, Any]:
    result = {"place_id": "", "google_maps_uri": "", "photos": [], "error": ""}
    api_key = load_google_api_key()
    if not api_key:
        result["error"] = "Google Places key is not configured."
        return result
    payload = {"textQuery": f"{business_name} in {city}".strip(), "pageSize": 1}
    headers = {
        "Content-Type": "application/json", "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id,places.displayName,places.googleMapsUri,places.photos",
    }
    try:
        response = requests.post(GOOGLE_TEXT_SEARCH_URL, json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        places = response.json().get("places", [])
        if not places: return result
        place = places[0]
        result["place_id"] = place.get("id", "")
        result["google_maps_uri"] = place.get("googleMapsUri", "")
        for photo in (place.get("photos") or [])[:max_photos]:
            attributions = []
            for author in photo.get("authorAttributions") or []:
                if author.get("displayName"):
                    attributions.append({"name": author.get("displayName", ""), "uri": author.get("uri", "")})
            if photo.get("name"):
                result["photos"].append({"photo_name": photo["name"], "source": "Google Maps", "attributions": attributions})
    except requests.RequestException as exc:
        result["error"] = str(exc)
    return result


def discover_business_assets(business_name: str, city: str, website_url: str) -> dict[str, Any]:
    website = inspect_public_website(website_url)
    google = get_google_place_assets(business_name, city)
    gallery = []
    for photo in google["photos"]:
        gallery.append({"kind": "google", "photo_name": photo["photo_name"], "source": photo["source"], "attributions": photo["attributions"]})
    for image in website["website_images"]:
        gallery.append({"kind": "external", "url": image["url"], "source": image["source"], "attributions": []})
    return {
        "social_links": website["social_links"], "gallery": gallery[:8],
        "google_maps_uri": google["google_maps_uri"], "place_id": google["place_id"],
        "warnings": [w for w in (website["error"], google["error"]) if w],
    }
