from __future__ import annotations

import re
from typing import Any


INDUSTRY_TEMPLATES: dict[str, dict[str, Any]] = {
    "restaurant": {
        "label": "Restaurant",
        "goal": "Increase reservations, calls, and online orders",
        "audience": "Local diners, families, takeout customers, and visitors",
        "palette": ["#141414", "#b91c1c", "#f5e6cf"],
        "style": "Warm, appetizing, modern, and mobile-first",
        "hero_title": "Fresh flavor. Local tradition.",
        "hero_text": "Made with quality ingredients and served with genuine hospitality.",
        "primary_cta": "Order Online",
        "secondary_cta": "View Menu",
        "sections": [
            ("Featured Menu", "Show best-selling dishes with strong food photography and clear prices."),
            ("Order Online", "Give customers a fast path to pickup or delivery."),
            ("Customer Reviews", "Display social proof from satisfied local customers."),
            ("Gallery", "Highlight food, dining space, staff, and signature items."),
            ("Visit Us", "Include hours, address, map, phone number, and parking details."),
        ],
    },
    "dentist": {
        "label": "Dental Practice",
        "goal": "Generate appointment requests and build patient trust",
        "audience": "Families, new patients, emergency patients, and insured customers",
        "palette": ["#0f3557", "#2aa7a1", "#eef9fb"],
        "style": "Clean, calming, trustworthy, and accessible",
        "hero_title": "Comfortable care for every smile.",
        "hero_text": "Friendly dental care, clear treatment options, and convenient appointment scheduling.",
        "primary_cta": "Book an Appointment",
        "secondary_cta": "Call the Office",
        "sections": [
            ("Services", "Explain preventive, restorative, cosmetic, and emergency services."),
            ("Meet the Team", "Introduce doctors and staff with welcoming profiles."),
            ("Insurance & Financing", "Make payment options easy to understand."),
            ("Patient Reviews", "Feature confidence-building patient testimonials."),
            ("Appointment Request", "Offer a short, mobile-friendly booking form."),
        ],
    },
    "roofing": {
        "label": "Roofing Contractor",
        "goal": "Generate inspection requests and estimate leads",
        "audience": "Homeowners, property managers, and storm-damage customers",
        "palette": ["#17202a", "#d97706", "#f4f1ea"],
        "style": "Strong, reliable, local, and conversion-focused",
        "hero_title": "Protect your home with confidence.",
        "hero_text": "Reliable roof repair, replacement, and storm-damage service from a local team.",
        "primary_cta": "Request a Free Estimate",
        "secondary_cta": "Call Now",
        "sections": [
            ("Roofing Services", "Present repair, replacement, inspection, and emergency services."),
            ("Before & After", "Show proof of workmanship with project comparisons."),
            ("Why Choose Us", "Highlight licensing, warranties, experience, and service area."),
            ("Customer Reviews", "Build credibility with local homeowner feedback."),
            ("Estimate Form", "Capture name, phone, address, service need, and preferred contact time."),
        ],
    },
    "auto": {
        "label": "Auto Repair",
        "goal": "Increase calls, appointment bookings, and repeat service",
        "audience": "Local drivers, fleet owners, and customers needing urgent repairs",
        "palette": ["#101820", "#e63946", "#f1f3f5"],
        "style": "Bold, dependable, practical, and easy to navigate",
        "hero_title": "Honest service. Reliable repairs.",
        "hero_text": "Professional maintenance and repair that keeps local drivers moving.",
        "primary_cta": "Book Service",
        "secondary_cta": "Call the Shop",
        "sections": [
            ("Popular Services", "List diagnostics, brakes, oil changes, tires, and major repairs."),
            ("Why Drivers Choose Us", "Show certifications, warranties, experience, and transparent service."),
            ("Special Offers", "Feature coupons or seasonal maintenance promotions."),
            ("Customer Reviews", "Use strong local feedback to reduce uncertainty."),
            ("Schedule Service", "Provide a simple appointment request form."),
        ],
    },
    "law": {
        "label": "Law Office",
        "goal": "Generate qualified consultation requests",
        "audience": "People seeking clear guidance and confidential legal help",
        "palette": ["#151b26", "#9a7b3f", "#f4f0e7"],
        "style": "Professional, confident, discreet, and authoritative",
        "hero_title": "Clear guidance when it matters most.",
        "hero_text": "Experienced legal representation with responsive communication and personal attention.",
        "primary_cta": "Request a Consultation",
        "secondary_cta": "View Practice Areas",
        "sections": [
            ("Practice Areas", "Clearly organize the legal matters the firm handles."),
            ("Why Choose the Firm", "Emphasize experience, communication, and client-centered service."),
            ("Attorney Profiles", "Build trust with professional biographies and credentials."),
            ("Client Testimonials", "Add approved testimonials or representative success stories."),
            ("Confidential Contact", "Offer a secure-looking consultation request form."),
        ],
    },
    "salon": {
        "label": "Salon & Beauty",
        "goal": "Increase bookings and showcase services",
        "audience": "Local clients seeking hair, beauty, grooming, or wellness services",
        "palette": ["#241b22", "#c08497", "#fbf1f4"],
        "style": "Stylish, welcoming, visual, and booking-focused",
        "hero_title": "Your look. Your confidence.",
        "hero_text": "Professional beauty services in a welcoming space designed around you.",
        "primary_cta": "Book Now",
        "secondary_cta": "View Services",
        "sections": [
            ("Services & Pricing", "Make treatments and starting prices easy to browse."),
            ("Featured Work", "Show a polished portfolio or transformation gallery."),
            ("Meet the Team", "Introduce stylists, specialties, and booking links."),
            ("Client Reviews", "Highlight customer experiences and results."),
            ("Book an Appointment", "Provide a direct booking call to action."),
        ],
    },
    "general": {
        "label": "Local Business",
        "goal": "Generate calls, messages, and qualified customer inquiries",
        "audience": "Local customers comparing trusted service providers",
        "palette": ["#111827", "#2563eb", "#f3f4f6"],
        "style": "Modern, professional, clear, and conversion-focused",
        "hero_title": "Professional service you can count on.",
        "hero_text": "A clear, customer-friendly website that makes it easy to understand services and take action.",
        "primary_cta": "Request a Quote",
        "secondary_cta": "Explore Services",
        "sections": [
            ("Services", "Explain the main services with concise customer-focused benefits."),
            ("Why Choose Us", "Highlight experience, responsiveness, quality, and local credibility."),
            ("Customer Reviews", "Add social proof and build confidence."),
            ("Service Area", "Clarify locations served and how customers can get help."),
            ("Contact", "Provide phone, email, hours, and a short inquiry form."),
        ],
    },
}


def template_key(industry: str) -> str:
    """Classify a lead without accidental substring matches.

    The previous implementation treated any occurrence of ``bar`` as a
    restaurant signal. That caused searches such as "barber shops" or the
    common misspelling "barbour shops" to be classified as restaurants.
    This version normalizes punctuation, checks the most-specific industries
    first, and uses whole-word/phrase matching.
    """
    value = re.sub(r"[^a-z0-9]+", " ", (industry or "").lower()).strip()
    padded = f" {value} "

    matches = [
        # Specific service categories must be checked before broad food terms.
        ("salon", [
            "barber", "barbers", "barbershop", "barber shop",
            "barbour", "barbour shop",  # common search misspelling
            "hair salon", "salon", "beauty salon", "beauty",
            "haircut", "hair cut", "grooming", "spa", "nail salon",
        ]),
        ("dentist", ["dentist", "dental", "orthodontist", "orthodontic"]),
        ("roofing", ["roofer", "roofing", "roof contractor", "contractor", "construction", "masonry", "siding"]),
        ("auto", ["auto repair", "automotive", "mechanic", "car repair", "tire shop", "collision repair"]),
        ("law", ["law office", "law firm", "lawyer", "attorney", "legal services"]),
        ("restaurant", [
            "restaurant", "pizza", "pizzeria", "food", "cafe", "coffee shop",
            "bakery", "diner", "takeout", "take out", "grill", "pub",
            "sports bar", "cocktail bar", "wine bar",
        ]),
    ]

    for key, terms in matches:
        for term in terms:
            normalized_term = re.sub(r"[^a-z0-9]+", " ", term.lower()).strip()
            if f" {normalized_term} " in padded:
                return key
    return "general"


def extract_website(notes: str) -> str:
    match = re.search(r"Website:\s*(https?://\S+)", notes or "", re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).rstrip(".,")


def current_site_findings(website_status: str, website_url: str) -> list[dict[str, str]]:
    status = (website_status or "").lower()
    if not website_url or "no website" in status:
        return [
            {"state": "bad", "title": "No dedicated website confirmed", "detail": "Customers may rely on directories or social media to learn about the business."},
            {"state": "bad", "title": "No owned conversion path", "detail": "There is no clear website flow for quotes, appointments, orders, or inquiries."},
            {"state": "warn", "title": "Brand visibility is limited", "detail": "Searchers may see competitors before finding complete business information."},
            {"state": "warn", "title": "Trust must come from third parties", "detail": "The business does not fully control how its services and reputation are presented."},
        ]
    return [
        {"state": "warn", "title": "Website requires a visual review", "detail": "Check mobile layout, typography, imagery, navigation, and calls to action."},
        {"state": "warn", "title": "Conversion opportunities may be missing", "detail": "Confirm that visitors can quickly call, book, order, or request a quote."},
        {"state": "warn", "title": "Local SEO can be improved", "detail": "Review service keywords, page titles, location content, and structured information."},
        {"state": "good", "title": "Existing web presence", "detail": "The current website provides a foundation for a stronger redesign or conversion upgrade."},
    ]


def build_concept(
    business_name: str,
    industry: str,
    city: str,
    website_status: str,
    website_url: str = "",
    tone: str = "",
    primary_goal: str = "",
) -> dict[str, Any]:
    key = template_key(industry)
    base = INDUSTRY_TEMPLATES[key]
    palette = base["palette"]

    chosen_style = tone.strip() or base["style"]
    chosen_goal = primary_goal.strip() or base["goal"]
    location = city.strip() or "the local area"

    horizons_prompt = f"""Create a polished, professional, mobile-first website homepage for {business_name}, a {base['label'].lower()} serving {location}.

Primary business goal:
{chosen_goal}

Target audience:
{base['audience']}

Visual direction:
{chosen_style}

Color palette:
- Primary: {palette[0]}
- Accent: {palette[1]}
- Light/background: {palette[2]}

Hero section:
- Headline: "{base['hero_title']}"
- Supporting text: "{base['hero_text']}"
- Primary call to action: "{base['primary_cta']}"
- Secondary call to action: "{base['secondary_cta']}"

Required homepage sections:
""" + "\n".join(f"- {title}: {description}" for title, description in base["sections"]) + """

Build requirements:
- Strong mobile layout
- Clear phone and contact actions
- Accessible contrast and readable typography
- Fast-loading structure
- Local SEO-friendly headings
- Professional footer with business information, hours, service area, and contact links
- Subtle animations only; prioritize speed and clarity
- Do not use filler text such as lorem ipsum
"""

    return {
        "template_key": key,
        "industry_label": base["label"],
        "business_name": business_name,
        "city": location,
        "goal": chosen_goal,
        "audience": base["audience"],
        "style": chosen_style,
        "palette": palette,
        "hero_title": base["hero_title"],
        "hero_text": base["hero_text"],
        "primary_cta": base["primary_cta"],
        "secondary_cta": base["secondary_cta"],
        "sections": [
            {"title": title, "description": description}
            for title, description in base["sections"]
        ],
        "website_url": website_url,
        "before_findings": current_site_findings(website_status, website_url),
        "horizons_prompt": horizons_prompt,
    }
