from __future__ import annotations

import os
import json
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Any

import requests


GOOGLE_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / "settings.json"

def load_google_api_key() -> str:
    try:
        if SETTINGS_PATH.exists():
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            key = str(data.get("google_places_api_key", "")).strip()
            if key and key != "PASTE_YOUR_NEW_KEY_HERE":
                return key
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return os.getenv("GOOGLE_PLACES_API_KEY", "").strip()


@dataclass
class DiscoveredLead:
    external_id: str
    business_name: str
    address: str
    phone: str
    website: str
    rating: float
    review_count: int
    category: str
    opportunity_score: int
    website_status: str
    reason: str
    photo_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_opportunity(
    website: str,
    rating: float,
    review_count: int,
    category: str,
) -> tuple[int, str, str]:
    score = 20
    reasons: list[str] = []

    if not website:
        score += 45
        website_status = "No website found"
        reasons.append("No dedicated website was returned by the business-data provider.")
    elif "facebook.com" in website.lower() or "instagram.com" in website.lower():
        score += 30
        website_status = "Social media only"
        reasons.append("The listed online presence appears to be a social-media page.")
    else:
        score += 5
        website_status = "Website listed"
        reasons.append("A website is listed and can be reviewed for redesign opportunities.")

    if review_count >= 100:
        score += 15
        reasons.append("The business has strong review volume.")
    elif review_count >= 25:
        score += 8
        reasons.append("The business has an established review history.")

    if rating >= 4.5:
        score += 10
        reasons.append("The business has a strong customer rating.")
    elif rating >= 4.0:
        score += 5

    high_value_terms = {
        "contractor", "plumber", "roof", "hvac", "dentist", "law",
        "auto", "salon", "restaurant", "medical", "real estate"
    }
    if any(term in category.lower() for term in high_value_terms):
        score += 10
        reasons.append("The category commonly benefits from lead generation or online booking.")

    return min(score, 100), website_status, " ".join(reasons)


def _normalize_google_place(place: dict[str, Any], category: str) -> DiscoveredLead:
    display_name = place.get("displayName") or {}
    website = place.get("websiteUri") or ""
    rating = float(place.get("rating") or 0)
    review_count = int(place.get("userRatingCount") or 0)
    score, website_status, reason = score_opportunity(
        website=website,
        rating=rating,
        review_count=review_count,
        category=category,
    )

    return DiscoveredLead(
        external_id=place.get("id") or "",
        business_name=display_name.get("text") or "Unknown Business",
        address=place.get("formattedAddress") or "",
        phone=place.get("nationalPhoneNumber") or "",
        website=website,
        rating=rating,
        review_count=review_count,
        category=category,
        opportunity_score=score,
        website_status=website_status,
        reason=reason,
        photo_count=len(place.get("photos") or []),
    )


def search_google_places(city: str, category: str, limit: int = 15) -> list[dict[str, Any]]:
    api_key = load_google_api_key()
    if not api_key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is not configured.")

    payload = {
        "textQuery": f"{category} in {city}",
        "pageSize": max(1, min(limit, 20)),
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.formattedAddress,"
            "places.nationalPhoneNumber,places.websiteUri,places.rating,"
            "places.userRatingCount,places.photos"
        ),
    }

    response = requests.post(
        GOOGLE_TEXT_SEARCH_URL,
        json=payload,
        headers=headers,
        timeout=25,
    )
    response.raise_for_status()

    places = response.json().get("places", [])
    leads = [_normalize_google_place(place, category) for place in places]
    leads.sort(key=lambda lead: lead.opportunity_score, reverse=True)
    return [lead.to_dict() for lead in leads]


def demo_search(city: str, category: str) -> list[dict[str, Any]]:
    """
    Local sample results let the entire workflow be tested before an API key is added.
    These are fictional businesses and are clearly marked as demo records.
    """
    samples = [
        {
            "external_id": "demo-1",
            "business_name": f"Demo {category.title()} One",
            "address": f"101 Main Street, {city}",
            "phone": "(555) 010-1001",
            "website": "",
            "rating": 4.8,
            "review_count": 146,
        },
        {
            "external_id": "demo-2",
            "business_name": f"Demo {category.title()} Two",
            "address": f"220 Market Street, {city}",
            "phone": "(555) 010-1002",
            "website": "https://facebook.com/example",
            "rating": 4.6,
            "review_count": 63,
        },
        {
            "external_id": "demo-3",
            "business_name": f"Demo {category.title()} Three",
            "address": f"45 Commerce Drive, {city}",
            "phone": "(555) 010-1003",
            "website": "https://example.com",
            "rating": 4.2,
            "review_count": 31,
        },
    ]

    leads = []
    for sample in samples:
        score, website_status, reason = score_opportunity(
            website=sample["website"],
            rating=sample["rating"],
            review_count=sample["review_count"],
            category=category,
        )
        sample.update(
            category=category,
            opportunity_score=score,
            website_status=website_status,
            reason=reason + " Demo record—verify all details before outreach.",
        )
        leads.append(sample)

    leads.sort(key=lambda lead: lead["opportunity_score"], reverse=True)
    return leads


def discover_leads(city: str, category: str, use_demo: bool = False) -> tuple[list[dict[str, Any]], str]:
    if use_demo:
        return demo_search(city, category), "demo"
    return search_google_places(city, category), "google"
