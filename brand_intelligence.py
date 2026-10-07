from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus, urlparse

import requests

from media_engine import SOCIAL_DOMAINS, HEADERS, inspect_public_website

PLATFORM_ORDER = ["Facebook", "Instagram", "TikTok", "YouTube", "LinkedIn", "X", "Pinterest"]
PLATFORM_HOSTS = {
    "Facebook": "facebook.com",
    "Instagram": "instagram.com",
    "TikTok": "tiktok.com",
    "YouTube": "youtube.com",
    "LinkedIn": "linkedin.com",
    "X": "x.com",
    "Pinterest": "pinterest.com",
}


def normalize_url(url: str) -> str:
    value = html.unescape((url or "").strip())
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    return value


def platform_from_url(url: str) -> str:
    host = urlparse(normalize_url(url)).netloc.lower().removeprefix("www.")
    for domain, label in SOCIAL_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return label
    return ""


def _looks_relevant(text: str, business_name: str, city: str) -> bool:
    haystack = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    business_tokens = [t for t in re.sub(r"[^a-z0-9 ]", " ", business_name.lower()).split() if len(t) > 2]
    city_tokens = [t for t in re.sub(r"[^a-z0-9 ]", " ", city.lower()).split() if len(t) > 2]
    business_match = any(token in haystack for token in business_tokens[:4])
    city_match = not city_tokens or any(token in haystack for token in city_tokens[:2])
    return business_match and city_match


def search_public_social_candidates(business_name: str, city: str, platform: str) -> list[dict[str, str]]:
    """Best-effort public discovery. Results stay 'possible' until manually confirmed."""
    host = PLATFORM_HOSTS[platform]
    query = f'site:{host} "{business_name}" "{city}"'
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    candidates: list[dict[str, str]] = []
    try:
        response = requests.get(url, headers=HEADERS, timeout=12)
        response.raise_for_status()
        body = response.text[:1_000_000]
        matches = re.findall(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            body,
            flags=re.I | re.S,
        )
        for href, title_html in matches:
            title = re.sub(r"<[^>]+>", " ", html.unescape(title_html))
            href = html.unescape(href)
            if host not in href.lower() or not _looks_relevant(title + " " + href, business_name, city):
                continue
            candidates.append({"url": href, "title": " ".join(title.split())})
            if len(candidates) >= 3:
                break
    except requests.RequestException:
        pass
    return candidates


def build_brand_report(
    business_name: str,
    city: str,
    website_url: str,
    saved_profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    website_data = inspect_public_website(website_url)
    saved_profiles = saved_profiles or []

    confirmed: dict[str, dict[str, Any]] = {}
    possible: dict[str, list[dict[str, str]]] = {}

    for item in website_data.get("social_links", []):
        confirmed[item["platform"]] = {
            "platform": item["platform"],
            "url": item["url"],
            "status": "Confirmed",
            "source": "Linked from official website",
        }

    for item in saved_profiles:
        platform = item.get("platform") or platform_from_url(item.get("url", ""))
        if platform and item.get("url"):
            confirmed[platform] = {
                "platform": platform,
                "url": item["url"],
                "status": item.get("status", "Confirmed"),
                "source": item.get("source", "Manually confirmed"),
            }

    for platform in PLATFORM_ORDER:
        if platform not in confirmed:
            candidates = search_public_social_candidates(business_name, city, platform)
            if candidates:
                possible[platform] = candidates

    presence_count = len(confirmed)
    possible_count = len(possible)
    score = min(100, presence_count * 14 + possible_count * 4 + (12 if website_url else 0))

    if presence_count >= 5:
        maturity = "Strong multi-platform presence"
    elif presence_count >= 3:
        maturity = "Established social presence"
    elif presence_count >= 1:
        maturity = "Limited but usable social presence"
    else:
        maturity = "No confirmed social presence"

    recommendations: list[str] = []
    if "TikTok" in confirmed:
        recommendations.append("Feature selected TikTok videos or embeds in a vertical-video section after client approval.")
    elif "TikTok" in possible:
        recommendations.append("Review the possible TikTok profile and confirm that it belongs to the business.")
    else:
        recommendations.append("Ask whether the business has a TikTok account under a different name or handle.")
    if "Instagram" in confirmed:
        recommendations.append("Use approved Instagram photography to keep the website visually consistent with the existing brand.")
    if "Facebook" in confirmed:
        recommendations.append("Carry current Facebook business information, events, and community messaging into the website plan.")
    if presence_count:
        recommendations.append("Match the website colors, tone, and content style to the strongest confirmed social channel.")
    else:
        recommendations.append("Build the website as the primary brand hub and include a simple social launch plan in the proposal.")

    rows = []
    for platform in PLATFORM_ORDER:
        if platform in confirmed:
            rows.append(confirmed[platform])
        elif platform in possible:
            rows.append({
                "platform": platform,
                "url": possible[platform][0]["url"],
                "status": "Possible",
                "source": "Public search candidate — needs confirmation",
                "candidates": possible[platform],
            })
        else:
            rows.append({"platform": platform, "url": "", "status": "Not found", "source": ""})

    return {
        "score": score,
        "maturity": maturity,
        "profiles": rows,
        "confirmed_count": presence_count,
        "possible_count": possible_count,
        "recommendations": recommendations,
        "website_error": website_data.get("error", ""),
    }
