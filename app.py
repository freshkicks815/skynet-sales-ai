from __future__ import annotations

import psycopg2
from psycopg2.extras import DictCursor
import json
import os
import requests
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from lead_finder import discover_leads
from design_engine import build_concept, extract_website
from media_engine import discover_business_assets
from brand_intelligence import PLATFORM_ORDER, build_brand_report, normalize_url, platform_from_url

from flask import Flask, Response, flash, make_response, redirect, render_template, request, session, url_for
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

app = Flask(__name__)
app.secret_key = os.getenv("SKYNET_SECRET_KEY", "local-development-key-change-before-production")


class DatabaseConnection:
    """Small compatibility wrapper so the existing app can use PostgreSQL safely."""

    def __init__(self) -> None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not configured.")
        self.conn = psycopg2.connect(DATABASE_URL)

    @staticmethod
    def _sql(query: str) -> str:
        # The existing app uses SQLite-style ? placeholders. PostgreSQL uses %s.
        return query.replace("?", "%s")

    def execute(self, query: str, params=()):
        cur = self.conn.cursor(cursor_factory=DictCursor)
        cur.execute(self._sql(query), params)
        return cur

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.conn.close()
        return False


def get_db() -> DatabaseConnection:
    return DatabaseConnection()


def init_db() -> None:
    statements = [
        """CREATE TABLE IF NOT EXISTS leads (
            id SERIAL PRIMARY KEY,
            business_name TEXT NOT NULL, contact_name TEXT, phone TEXT, email TEXT,
            industry TEXT, city TEXT, website_status TEXT NOT NULL DEFAULT 'No website found',
            products INTEGER NOT NULL DEFAULT 0, needs_ecommerce INTEGER NOT NULL DEFAULT 0,
            notes TEXT, status TEXT NOT NULL DEFAULT 'New', recommended_plan TEXT,
            quote_amount DOUBLE PRECISION, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS activities (
            id SERIAL PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
            activity_type TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS social_profiles (
            id SERIAL PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
            platform TEXT NOT NULL, url TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Confirmed',
            source TEXT, updated_at TEXT NOT NULL, UNIQUE(lead_id, platform)
        )""",
        """CREATE TABLE IF NOT EXISTS pricing_plans (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL,
            monthly_price DOUBLE PRECISION NOT NULL DEFAULT 0, yearly_price DOUBLE PRECISION NOT NULL DEFAULT 0,
            min_products INTEGER NOT NULL DEFAULT 0, max_products INTEGER,
            features_json TEXT NOT NULL DEFAULT '[]', display_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS lead_sales_profiles (
            lead_id INTEGER PRIMARY KEY REFERENCES leads(id), website_score INTEGER NOT NULL DEFAULT 50,
            google_score INTEGER NOT NULL DEFAULT 50, social_score INTEGER NOT NULL DEFAULT 0,
            brand_score INTEGER NOT NULL DEFAULT 40, opportunity_score INTEGER NOT NULL DEFAULT 50,
            recommendation_confidence INTEGER NOT NULL DEFAULT 75,
            payment_recommendation TEXT NOT NULL DEFAULT 'Monthly', recommendation_reason TEXT,
            selected_plan TEXT, selected_billing TEXT, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS website_concepts (
            id SERIAL PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id),
            concept_json TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
    ]
    with get_db() as conn:
        for statement in statements:
            conn.execute(statement)

        defaults = [
            ("Basic", "Informational website for your business", 5.0, 50.0, 0, 0,
             ["Custom informational website", "Mobile-friendly design", "Contact form", "Google Maps", "Basic SEO", "Launch support"], 1),
            ("E-Comm 1", "E-commerce website with up to 10 products", 20.0, 200.0, 1, 10,
             ["Everything in Basic", "Up to 10 products", "Shopping cart", "Secure checkout", "Payment processing", "Order confirmations"], 2),
            ("E-Comm 2", "E-commerce website with 11-30 products", 40.0, 400.0, 11, 30,
             ["Everything in E-Comm 1", "11-30 products", "Product categories", "Inventory management", "Coupon codes", "Featured products", "Enhanced product search"], 3),
        ]
        for row in defaults:
            conn.execute(
                """INSERT INTO pricing_plans
                (name, description, monthly_price, yearly_price, min_products, max_products,
                 features_json, display_order, is_active, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT (name) DO NOTHING""",
                (*row[:6], json.dumps(row[6]), row[7], datetime.now().isoformat(timespec="seconds")),
            )


def money(value: float | int | None) -> str:
    value = float(value or 0)
    return f"${value:,.2f}"


app.jinja_env.filters["money"] = money


def get_active_plans(conn: DatabaseConnection | None = None) -> list[dict[str, Any]]:
    owns = conn is None
    conn = conn or get_db()
    rows = conn.execute(
        "SELECT * FROM pricing_plans WHERE is_active = 1 ORDER BY display_order, id"
    ).fetchall()
    plans = []
    for row in rows:
        item = dict(row)
        try:
            item["features"] = json.loads(item.get("features_json") or "[]")
        except json.JSONDecodeError:
            item["features"] = []
        plans.append(item)
    if owns:
        conn.close()
    return plans


def recommend_plan(needs_ecommerce: bool, products: int, plans: list[dict[str, Any]]) -> dict[str, Any]:
    active = [p for p in plans if p.get("is_active", 1)]
    if not active:
        return {"name": "Custom", "monthly_price": 0, "yearly_price": 0, "reason": "No active plans configured.", "confidence": 50}
    if not needs_ecommerce:
        plan = next((p for p in active if p["name"].lower() == "basic"), active[0])
        reason = "The business needs a professional informational website without an online product catalog."
        confidence = 94
    else:
        plan = next((p for p in active if p["min_products"] <= products and (p["max_products"] is None or products <= p["max_products"])), None)
        if plan:
            reason = f"The {products}-product catalog fits this plan's product range."
            confidence = 97
        else:
            plan = active[-1]
            reason = f"The business has {products} products, which exceeds the standard catalog limits. Use {plan['name']} as the base and prepare a custom scope."
            confidence = 88
    result = dict(plan)
    result.update(reason=reason, confidence=confidence)
    return result


def calculate_sales_profile(lead: Any, social_count: int = 0) -> dict[str, Any]:
    website_text = (lead["website_status"] or "").lower()
    has_site = "http" in website_text or ("website" in website_text and "no website" not in website_text)
    website_score = 64 if has_site else 24
    social_score = min(100, social_count * 17)
    google_score = 72 if lead["phone"] else 55
    brand_score = round((website_score + social_score + google_score) / 3)
    opportunity = max(10, min(100, round(100 - website_score * .45 + social_score * .20 + google_score * .15)))
    payment = "Yearly" if google_score >= 70 and social_score >= 50 else "Monthly"
    return {
        "website_score": website_score,
        "google_score": google_score,
        "social_score": social_score,
        "brand_score": brand_score,
        "opportunity_score": opportunity,
        "payment_recommendation": payment,
    }


def calculate_quote(
    needs_ecommerce: bool,
    products: int,
    extra_pages: int = 0,
    logo: bool = False,
    seo: bool = False,
    booking: bool = False,
    custom_features: int = 0,
) -> dict[str, Any]:
    """
    Pricing rules based on the current Skynet Sites plans.

    Basic: $50/year
    E-Comm 1: $200/year, up to 10 products
    E-Comm 2: $400/year, 11-30 products

    For 31+ products, the system creates a custom quote:
      $400 base + $8 per product above 30.
    Optional add-ons are configurable below.
    """
    breakdown: list[dict[str, Any]] = []

    if not needs_ecommerce:
        plan = "Basic"
        total = 50.0
        reason = "Informational business website."
        breakdown.append({"item": "Basic annual website plan", "amount": 50.0})
    elif products <= 10:
        plan = "E-Comm 1"
        total = 200.0
        reason = f"E-commerce website with {products} product(s), within the 10-product limit."
        breakdown.append({"item": "E-Comm 1 annual plan", "amount": 200.0})
    elif products <= 30:
        plan = "E-Comm 2"
        total = 400.0
        reason = f"E-commerce website with {products} products, within the 11-30 product range."
        breakdown.append({"item": "E-Comm 2 annual plan", "amount": 400.0})
    else:
        plan = "Custom E-Commerce"
        extra_products = products - 30
        extra_product_cost = extra_products * 8.0
        total = 400.0 + extra_product_cost
        reason = (
            f"Custom store with {products} products. "
            f"The first 30 are covered by the E-Comm 2 base; "
            f"{extra_products} additional products are quoted at $8 each."
        )
        breakdown.append({"item": "E-Comm 2 custom base", "amount": 400.0})
        breakdown.append(
            {
                "item": f"{extra_products} additional products × $8",
                "amount": extra_product_cost,
            }
        )

    add_ons = [
        ("Additional pages", max(extra_pages, 0) * 50.0),
        ("Logo design", 75.0 if logo else 0.0),
        ("Basic SEO setup", 150.0 if seo else 0.0),
        ("Booking system", 200.0 if booking else 0.0),
        ("Custom feature allowance", max(custom_features, 0) * 100.0),
    ]

    for item, amount in add_ons:
        if amount:
            breakdown.append({"item": item, "amount": amount})
            total += amount

    return {
        "plan": plan,
        "total": round(total, 2),
        "reason": reason,
        "breakdown": breakdown,
    }


def build_call_script(lead: Any) -> str:
    business = lead["business_name"]
    industry = lead["industry"] or "business"
    city = lead["city"] or "your area"
    website_status = lead["website_status"]

    return (
        f"Hello, may I speak with the person who handles marketing for {business}?\n\n"
        f"My name is Donnie with Skynet Sites. I was researching {industry} businesses "
        f"in {city}, and {website_status.lower()}. "
        "We build affordable business websites that help customers find your services, "
        "contact you, and purchase or book online.\n\n"
        "Our informational plan starts at $50 per year, and our online-store plans start "
        "at $200 per year. Would you be open to a quick, no-pressure website preview or quote?\n\n"
        "If interested: Great. What would you want customers to be able to do on the website?\n"
        "If not interested: I understand. May I send you a short example by email for future reference?"
    )



def retell_config() -> dict[str, str]:
    return {
        "api_key": os.getenv("RETELL_API_KEY", "").strip(),
        "agent_id": os.getenv("RETELL_AGENT_ID", "").strip(),
        "from_number": os.getenv("RETELL_FROM_NUMBER", "").strip(),
    }


def place_mandy_call(lead: Any) -> dict[str, Any]:
    cfg = retell_config()
    missing = [name for name, value in cfg.items() if not value]
    if missing:
        raise RuntimeError("Retell is not configured. Missing: " + ", ".join(missing))

    phone = (lead["phone"] or "").strip()
    if not phone:
        raise RuntimeError("This lead does not have a phone number.")

    # Retell requires E.164 numbers. Keep a leading + as-is; normalize 10-digit US numbers.
    digits = re.sub(r"\D", "", phone)
    if phone.startswith("+"):
        to_number = "+" + digits
    elif len(digits) == 10:
        to_number = "+1" + digits
    elif len(digits) == 11 and digits.startswith("1"):
        to_number = "+" + digits
    else:
        raise RuntimeError("Lead phone number must be a valid US/E.164 number before calling.")

    lead_context = {
        "lead_id": str(lead["id"]),
        "business_name": lead["business_name"] or "",
        "contact_name": lead["contact_name"] or "",
        "industry": lead["industry"] or "",
        "city": lead["city"] or "",
        "website_status": lead["website_status"] or "",
        "recommended_plan": lead["recommended_plan"] or "",
        "notes": lead["notes"] or "",
    }
    body = {
        "from_number": cfg["from_number"],
        "to_number": to_number,
        "override_agent_id": cfg["agent_id"],
        "override_agent_version": "latest_published",
        "honor_internal_dnc": True,
        "metadata": {"source": "Skynet Sales AI", "lead_id": str(lead["id"])},
        "retell_llm_dynamic_variables": lead_context,
        "idempotency_key": f"skynet-lead-{lead['id']}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
    }
    response = requests.post(
        "https://api.retellai.com/v2/create-phone-call",
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    try:
        data = response.json()
    except ValueError:
        data = {"raw": response.text[:500]}
    if not response.ok:
        message = data.get("message") or data.get("error") or data.get("raw") or f"HTTP {response.status_code}"
        raise RuntimeError(f"Retell rejected the call: {message}")
    return data


@app.route("/")
def dashboard():
    with get_db() as conn:
        leads = conn.execute(
            "SELECT * FROM leads ORDER BY id DESC LIMIT 20"
        ).fetchall()
        stats = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'New' THEN 1 ELSE 0 END) AS new_count,
                SUM(CASE WHEN status = 'Follow-up' THEN 1 ELSE 0 END) AS follow_up_count,
                SUM(CASE WHEN status = 'Interested' THEN 1 ELSE 0 END) AS interested_count,
                SUM(CASE WHEN status = 'Won' THEN 1 ELSE 0 END) AS won_count,
                COALESCE(SUM(CASE WHEN status = 'Won' THEN quote_amount ELSE 0 END), 0) AS revenue
            FROM leads
            """
        ).fetchone()

    return render_template("dashboard.html", leads=leads, stats=stats)


@app.route("/leads/new", methods=["GET", "POST"])
def new_lead():
    if request.method == "POST":
        business_name = request.form.get("business_name", "").strip()
        if not business_name:
            flash("Business name is required.", "error")
            return render_template("lead_form.html")

        needs_ecommerce = request.form.get("needs_ecommerce") == "yes"
        products = int(request.form.get("products") or 0)

        quote = calculate_quote(
            needs_ecommerce=needs_ecommerce,
            products=products,
        )

        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO leads (
                    business_name, contact_name, phone, email, industry, city,
                    website_status, products, needs_ecommerce, notes, status,
                    recommended_plan, quote_amount, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
                """,
                (
                    business_name,
                    request.form.get("contact_name", "").strip(),
                    request.form.get("phone", "").strip(),
                    request.form.get("email", "").strip(),
                    request.form.get("industry", "").strip(),
                    request.form.get("city", "").strip(),
                    request.form.get("website_status", "No website found").strip(),
                    products,
                    int(needs_ecommerce),
                    request.form.get("notes", "").strip(),
                    "New",
                    quote["plan"],
                    quote["total"],
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            lead_id = cursor.fetchone()[0]

        flash("Lead saved and initial quote created.", "success")
        return redirect(url_for("lead_detail", lead_id=lead_id))

    return render_template("lead_form.html")


@app.route("/leads/<int:lead_id>")
def lead_detail(lead_id: int):
    with get_db() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        activities = conn.execute(
            "SELECT * FROM activities WHERE lead_id = ? ORDER BY id DESC",
            (lead_id,),
        ).fetchall()

    if not lead:
        return "Lead not found", 404

    basic_quote = calculate_quote(
        needs_ecommerce=bool(lead["needs_ecommerce"]),
        products=int(lead["products"] or 0),
    )

    return render_template(
        "lead_detail.html",
        lead=lead,
        activities=activities,
        quote=basic_quote,
        call_script=build_call_script(lead),
    )



@app.route("/leads/<int:lead_id>/call-with-mandy", methods=["POST"])
def call_with_mandy(lead_id: int):
    with get_db() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        return "Lead not found", 404
    if (lead["status"] or "").strip() == "Do Not Call":
        flash("This lead is marked Do Not Call. Mandy did not place a call.", "error")
        return redirect(url_for("lead_detail", lead_id=lead_id))

    try:
        result = place_mandy_call(lead)
        call_id = result.get("call_id", "unknown")
        with get_db() as conn:
            conn.execute(
                "INSERT INTO activities (lead_id, activity_type, details, created_at) VALUES (?, ?, ?, ?)",
                (lead_id, "Mandy AI Call", f"Outbound call started via Retell. Call ID: {call_id}", datetime.now().isoformat(timespec="seconds")),
            )
            if lead["status"] == "New":
                conn.execute("UPDATE leads SET status = 'Contacted' WHERE id = ?", (lead_id,))
        flash(f"Mandy started an outbound call to {lead['business_name']}. Retell Call ID: {call_id}", "success")
    except (RuntimeError, requests.RequestException) as exc:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO activities (lead_id, activity_type, details, created_at) VALUES (?, ?, ?, ?)",
                (lead_id, "Mandy AI Call Error", str(exc), datetime.now().isoformat(timespec="seconds")),
            )
        flash(str(exc), "error")
    return redirect(url_for("lead_detail", lead_id=lead_id))


@app.route("/leads/<int:lead_id>/quote", methods=["POST"])
def custom_quote(lead_id: int):
    with get_db() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()

    if not lead:
        return "Lead not found", 404

    quote = calculate_quote(
        needs_ecommerce=request.form.get("needs_ecommerce") == "yes",
        products=int(request.form.get("products") or 0),
        extra_pages=int(request.form.get("extra_pages") or 0),
        logo=request.form.get("logo") == "yes",
        seo=request.form.get("seo") == "yes",
        booking=request.form.get("booking") == "yes",
        custom_features=int(request.form.get("custom_features") or 0),
    )

    with get_db() as conn:
        conn.execute(
            """
            UPDATE leads
            SET products = ?, needs_ecommerce = ?, recommended_plan = ?, quote_amount = ?
            WHERE id = ?
            """,
            (
                int(request.form.get("products") or 0),
                int(request.form.get("needs_ecommerce") == "yes"),
                quote["plan"],
                quote["total"],
                lead_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO activities (lead_id, activity_type, details, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                lead_id,
                "Quote",
                f'{quote["plan"]} quote created for {money(quote["total"])}',
                datetime.now().isoformat(timespec="seconds"),
            ),
        )

    return render_template("quote_result.html", lead=lead, quote=quote)


@app.route("/leads/<int:lead_id>/status", methods=["POST"])
def update_status(lead_id: int):
    status = request.form.get("status", "New")
    allowed = {"New", "Contacted", "Interested", "Follow-up", "Won", "Lost", "Do Not Call"}
    if status not in allowed:
        flash("Invalid status.", "error")
        return redirect(url_for("lead_detail", lead_id=lead_id))

    with get_db() as conn:
        conn.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
        conn.execute(
            """
            INSERT INTO activities (lead_id, activity_type, details, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                lead_id,
                "Status",
                f"Status changed to {status}",
                datetime.now().isoformat(timespec="seconds"),
            ),
        )

    flash("Lead status updated.", "success")
    return redirect(url_for("lead_detail", lead_id=lead_id))


@app.route("/leads/<int:lead_id>/activity", methods=["POST"])
def add_activity(lead_id: int):
    details = request.form.get("details", "").strip()
    activity_type = request.form.get("activity_type", "Note").strip()

    if details:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO activities (lead_id, activity_type, details, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    lead_id,
                    activity_type,
                    details,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    return redirect(url_for("lead_detail", lead_id=lead_id))


@app.route("/leads/<int:lead_id>/sales-intelligence", methods=["GET", "POST"])
def sales_intelligence(lead_id: int):
    with get_db() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            return "Lead not found", 404
        social_count = conn.execute("SELECT COUNT(*) FROM social_profiles WHERE lead_id = ?", (lead_id,)).fetchone()[0]
        plans = get_active_plans(conn)
        recommended = recommend_plan(bool(lead["needs_ecommerce"]), int(lead["products"] or 0), plans)
        profile = calculate_sales_profile(lead, social_count)
        if request.method == "POST":
            profile["website_score"] = max(0, min(100, int(request.form.get("website_score") or profile["website_score"])))
            profile["google_score"] = max(0, min(100, int(request.form.get("google_score") or profile["google_score"])))
            profile["social_score"] = max(0, min(100, int(request.form.get("social_score") or profile["social_score"])))
            profile["brand_score"] = max(0, min(100, int(request.form.get("brand_score") or profile["brand_score"])))
            profile["opportunity_score"] = max(0, min(100, int(request.form.get("opportunity_score") or profile["opportunity_score"])))
            selected_plan = request.form.get("selected_plan") or recommended["name"]
            selected_billing = request.form.get("selected_billing") or profile["payment_recommendation"]
            reason = request.form.get("recommendation_reason", "").strip() or recommended["reason"]
            confidence = max(0, min(100, int(request.form.get("recommendation_confidence") or recommended["confidence"])))
            conn.execute("""INSERT INTO lead_sales_profiles
                (lead_id, website_score, google_score, social_score, brand_score, opportunity_score,
                 recommendation_confidence, payment_recommendation, recommendation_reason,
                 selected_plan, selected_billing, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lead_id) DO UPDATE SET website_score=excluded.website_score,
                 google_score=excluded.google_score, social_score=excluded.social_score,
                 brand_score=excluded.brand_score, opportunity_score=excluded.opportunity_score,
                 recommendation_confidence=excluded.recommendation_confidence,
                 payment_recommendation=excluded.payment_recommendation,
                 recommendation_reason=excluded.recommendation_reason,
                 selected_plan=excluded.selected_plan, selected_billing=excluded.selected_billing,
                 updated_at=excluded.updated_at""",
                (lead_id, profile["website_score"], profile["google_score"], profile["social_score"],
                 profile["brand_score"], profile["opportunity_score"], confidence, selected_billing,
                 reason, selected_plan, selected_billing, datetime.now().isoformat(timespec="seconds")))
            chosen = next((p for p in plans if p["name"] == selected_plan), recommended)
            amount = chosen["monthly_price"] if selected_billing == "Monthly" else chosen["yearly_price"]
            conn.execute("UPDATE leads SET recommended_plan=?, quote_amount=? WHERE id=?", (selected_plan, amount, lead_id))
            flash("Sales recommendation saved.", "success")
            return redirect(url_for("sales_intelligence", lead_id=lead_id))
        saved = conn.execute("SELECT * FROM lead_sales_profiles WHERE lead_id = ?", (lead_id,)).fetchone()
    if saved:
        profile.update(dict(saved))
        recommended = next((dict(p, reason=profile.get("recommendation_reason") or recommended["reason"], confidence=profile.get("recommendation_confidence") or recommended["confidence"]) for p in plans if p["name"] == profile.get("selected_plan")), recommended)
    return render_template("sales_intelligence.html", lead=lead, plans=plans, recommended=recommended, profile=profile)


@app.route("/leads/<int:lead_id>/proposal", methods=["GET", "POST"])
def proposal(lead_id: int):
    with get_db() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            return "Lead not found", 404
        plans = get_active_plans(conn)
        sales = conn.execute("SELECT * FROM lead_sales_profiles WHERE lead_id = ?", (lead_id,)).fetchone()
        social_count = conn.execute("SELECT COUNT(*) FROM social_profiles WHERE lead_id = ?", (lead_id,)).fetchone()[0]
    profile = dict(sales) if sales else calculate_sales_profile(lead, social_count)
    recommended = recommend_plan(bool(lead["needs_ecommerce"]), int(lead["products"] or 0), plans)
    selected_name = profile.get("selected_plan") or lead["recommended_plan"] or recommended["name"]
    selected = next((p for p in plans if p["name"] == selected_name), recommended)
    billing = profile.get("selected_billing") or profile.get("payment_recommendation") or "Monthly"
    return render_template("proposal.html", lead=lead, plans=plans, selected=selected, billing=billing,
                           profile=profile, today=datetime.now().strftime("%B %d, %Y"))


@app.route("/plans", methods=["GET", "POST"])
def pricing_manager():
    if request.method == "POST":
        plan_id = int(request.form.get("plan_id") or 0)
        features = [x.strip() for x in request.form.get("features", "").splitlines() if x.strip()]
        with get_db() as conn:
            conn.execute("""UPDATE pricing_plans SET name=?, description=?, monthly_price=?, yearly_price=?,
                min_products=?, max_products=?, features_json=?, display_order=?, is_active=?, updated_at=? WHERE id=?""",
                (request.form.get("name", "").strip(), request.form.get("description", "").strip(),
                 float(request.form.get("monthly_price") or 0), float(request.form.get("yearly_price") or 0),
                 int(request.form.get("min_products") or 0),
                 int(request.form["max_products"]) if request.form.get("max_products", "").strip() else None,
                 json.dumps(features), int(request.form.get("display_order") or 0),
                 int(request.form.get("is_active") == "yes"), datetime.now().isoformat(timespec="seconds"), plan_id))
        flash("Pricing plan updated everywhere in Skynet Sales AI.", "success")
        return redirect(url_for("pricing_manager"))
    return render_template("pricing_manager.html", plans=get_active_plans() + get_inactive_plans())


def get_inactive_plans() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM pricing_plans WHERE is_active = 0 ORDER BY display_order, id").fetchall()
    result=[]
    for r in rows:
        d=dict(r)
        try: d["features"]=json.loads(d.get("features_json") or "[]")
        except json.JSONDecodeError: d["features"]=[]
        result.append(d)
    return result



@app.route("/finder", methods=["GET", "POST"])
def lead_finder():
    if request.method == "POST":
        # Read the values from this exact submission. Clear the previous search
        # before contacting Google so stale results can never be shown as if
        # they belonged to the new city/category.
        city = request.form.get("city", "").strip()
        category = request.form.get("category", "").strip()
        use_demo = request.form.get("use_demo") == "yes"

        session["finder_results"] = []
        session["finder_meta"] = {
            "city": city,
            "category": category,
            "source": "",
        }
        session.modified = True

        if not city or not category:
            flash("Enter both a city and a business category.", "error")
            return redirect(url_for("lead_finder"))

        try:
            fresh_results, source = discover_leads(city, category, use_demo=use_demo)
            session["finder_results"] = fresh_results
            session["finder_meta"] = {
                "city": city,
                "category": category,
                "source": source,
            }
            session.modified = True

            if source == "demo":
                flash(
                    f"Demo results loaded for {category} in {city}. Add a Google Places API key for live businesses.",
                    "success",
                )
            else:
                flash(
                    f"Found {len(fresh_results)} {category} businesses in {city} using Google Places.",
                    "success",
                )
        except RuntimeError as exc:
            flash(str(exc) + " Add the key to settings.json, save it, and restart the app.", "error")
        except requests.HTTPError as exc:
            details = ""
            if exc.response is not None:
                try:
                    payload = exc.response.json()
                    details = payload.get("error", {}).get("message", "")
                except (ValueError, AttributeError):
                    details = exc.response.text[:500]
            flash(f"Google Places rejected the request: {details or str(exc)}", "error")
        except requests.RequestException as exc:
            flash(f"Could not reach Google Places: {exc}", "error")

        # POST/Redirect/GET prevents accidental form resubmission and guarantees
        # the page reloads from the newly saved search instead of old variables.
        return redirect(url_for("lead_finder"))

    results = session.get("finder_results", [])
    search_meta = session.get("finder_meta", {})
    response = make_response(render_template(
        "lead_finder.html",
        results=results,
        search_meta=search_meta,
    ))
    # Prevent browser/proxy caching from restoring an older search page.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/finder/import/<int:result_index>", methods=["POST"])
def import_discovered_lead(result_index: int):
    results = session.get("finder_results", [])
    if result_index < 0 or result_index >= len(results):
        flash("That discovered lead is no longer available. Run the search again.", "error")
        return redirect(url_for("lead_finder"))

    item = results[result_index]
    address = item.get("address", "")
    city = address.split(",")[-2].strip() if address.count(",") >= 1 else address

    with get_db() as conn:
        duplicate = conn.execute(
            """
            SELECT id FROM leads
            WHERE lower(business_name) = lower(?) AND lower(phone) = lower(?)
            LIMIT 1
            """,
            (item.get("business_name", ""), item.get("phone", "")),
        ).fetchone()

        if duplicate:
            flash("This business is already in your CRM.", "error")
            return redirect(url_for("lead_detail", lead_id=duplicate["id"]))

        quote = calculate_quote(needs_ecommerce=False, products=0)
        cursor = conn.execute(
            """
            INSERT INTO leads (
                business_name, contact_name, phone, email, industry, city,
                website_status, products, needs_ecommerce, notes, status,
                recommended_plan, quote_amount, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
            """,
            (
                item.get("business_name", ""),
                "",
                item.get("phone", ""),
                "",
                item.get("category", ""),
                city,
                item.get("website_status", "No website found"),
                0,
                0,
                (
                    f'Opportunity score: {item.get("opportunity_score", 0)}/100. '
                    f'Address: {item.get("address", "")}. '
                    f'Website: {item.get("website") or "None listed"}. '
                    f'Rating: {item.get("rating", 0)} '
                    f'({item.get("review_count", 0)} reviews). '
                    f'{item.get("reason", "")}'
                ),
                "New",
                quote["plan"],
                quote["total"],
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        lead_id = cursor.fetchone()[0]

    flash("Business imported into your CRM.", "success")
    return redirect(url_for("lead_detail", lead_id=lead_id))



@app.route("/leads/<int:lead_id>/brand-intelligence", methods=["GET", "POST"])
def brand_intelligence(lead_id: int):
    with get_db() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        saved = conn.execute(
            "SELECT * FROM social_profiles WHERE lead_id = ? ORDER BY platform",
            (lead_id,),
        ).fetchall()

    if not lead:
        return "Lead not found", 404

    detected_website = extract_website(lead["notes"] or "")
    website_url = request.form.get("website_url", "").strip() if request.method == "POST" else detected_website

    if request.method == "POST" and request.form.get("action") == "save":
        with get_db() as conn:
            for platform in PLATFORM_ORDER:
                field = "social_" + platform.lower().replace(" ", "_")
                url = normalize_url(request.form.get(field, ""))
                if url:
                    detected_platform = platform_from_url(url)
                    if detected_platform and detected_platform != platform:
                        flash(f"{platform} field contains a {detected_platform} URL.", "error")
                        continue
                    conn.execute(
                        """
                        INSERT INTO social_profiles (lead_id, platform, url, status, source, updated_at)
                        VALUES (?, ?, ?, 'Confirmed', 'Manually confirmed', ?)
                        ON CONFLICT(lead_id, platform) DO UPDATE SET
                            url=excluded.url, status='Confirmed', source='Manually confirmed', updated_at=excluded.updated_at
                        """,
                        (lead_id, platform, url, datetime.now().isoformat(timespec="seconds")),
                    )
                else:
                    conn.execute("DELETE FROM social_profiles WHERE lead_id = ? AND platform = ?", (lead_id, platform))
            conn.execute(
                "INSERT INTO activities (lead_id, activity_type, details, created_at) VALUES (?, ?, ?, ?)",
                (lead_id, "Brand Intelligence", "Updated confirmed social media profiles.", datetime.now().isoformat(timespec="seconds")),
            )
        flash("Social profiles saved.", "success")
        return redirect(url_for("brand_intelligence", lead_id=lead_id))

    report = build_brand_report(
        business_name=lead["business_name"],
        city=lead["city"] or "",
        website_url=website_url,
        saved_profiles=[dict(row) for row in saved],
    )
    return render_template(
        "brand_intelligence.html",
        lead=lead, report=report, website_url=website_url, platforms=PLATFORM_ORDER,
    )


@app.route("/leads/<int:lead_id>/concept/new", methods=["GET", "POST"])
def new_website_concept(lead_id: int):
    with get_db() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()

    if not lead:
        return "Lead not found", 404

    detected_website = extract_website(lead["notes"] or "")

    if request.method == "POST":
        website_url = request.form.get("website_url", "").strip()
        concept = build_concept(
            business_name=lead["business_name"],
            industry=request.form.get("industry", "").strip() or (lead["industry"] or ""),
            city=request.form.get("city", "").strip() or (lead["city"] or ""),
            website_status=lead["website_status"],
            website_url=website_url,
            tone=request.form.get("tone", "").strip(),
            primary_goal=request.form.get("primary_goal", "").strip(),
        )
        concept["assets"] = discover_business_assets(
            business_name=lead["business_name"],
            city=request.form.get("city", "").strip() or (lead["city"] or ""),
            website_url=website_url,
        )
        with get_db() as conn:
            saved_social = conn.execute(
                "SELECT platform, url FROM social_profiles WHERE lead_id = ? ORDER BY platform",
                (lead_id,),
            ).fetchall()
        merged_social = {item["platform"]: item for item in concept["assets"].get("social_links", [])}
        for item in saved_social:
            merged_social[item["platform"]] = {"platform": item["platform"], "url": item["url"]}
        concept["assets"]["social_links"] = list(merged_social.values())

        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO website_concepts (lead_id, concept_json, created_at)
                VALUES (?, ?, ?) RETURNING id
                """,
                (
                    lead_id,
                    json.dumps(concept),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            concept_id = cursor.fetchone()[0]
            conn.execute(
                """
                INSERT INTO activities (lead_id, activity_type, details, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    lead_id,
                    "Website Concept",
                    f'Generated a {concept["industry_label"]} homepage concept.',
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

        return redirect(url_for("website_concept", lead_id=lead_id, concept_id=concept_id))

    return render_template(
        "concept_form.html",
        lead=lead,
        detected_website=detected_website,
    )


@app.route("/leads/<int:lead_id>/concepts/<int:concept_id>")
def website_concept(lead_id: int, concept_id: int):
    with get_db() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        row = conn.execute(
            "SELECT * FROM website_concepts WHERE id = ? AND lead_id = ?",
            (concept_id, lead_id),
        ).fetchone()

    if not lead or not row:
        return "Website concept not found", 404

    concept = json.loads(row["concept_json"])
    return render_template(
        "website_concept.html",
        lead=lead,
        concept=concept,
        concept_id=concept_id,
        created_at=row["created_at"],
    )



@app.route("/media/google-photo")
def google_photo():
    photo_name = request.args.get("name", "").strip()
    if not re.fullmatch(r"places/[^/]+/photos/[^/]+", photo_name):
        return "Invalid photo reference", 400
    from lead_finder import load_google_api_key
    api_key = load_google_api_key()
    if not api_key:
        return "Google Places key is not configured", 503
    try:
        media_response = requests.get(
            f"https://places.googleapis.com/v1/{photo_name}/media",
            params={"maxWidthPx": 1200, "maxHeightPx": 900, "skipHttpRedirect": "false", "key": api_key},
            timeout=25,
        )
        media_response.raise_for_status()
    except requests.RequestException:
        return "Photo unavailable", 502
    return Response(media_response.content, content_type=media_response.headers.get("Content-Type", "image/jpeg"), headers={"Cache-Control": "private, max-age=1800"})

@app.route("/retell/webhook", methods=["POST"])
def retell_webhook():
    data = request.get_json(silent=True) or {}

    event = data.get("event", "")
    call = data.get("call", {}) or {}

    call_id = call.get("call_id", "")
    disconnection_reason = call.get("disconnection_reason", "")
    call_status = call.get("call_status", "")

    metadata = call.get("metadata", {}) or {}
    dynamic_vars = call.get("retell_llm_dynamic_variables", {}) or {}

    # Sales AI sends lead_id to Retell in both metadata
    # and retell_llm_dynamic_variables.
    lead_id = metadata.get("lead_id") or dynamic_vars.get("lead_id")

    if not lead_id:
        return {"ok": True, "ignored": "No lead_id"}, 200

    try:
        lead_id = int(lead_id)
    except (TypeError, ValueError):
        return {"ok": True, "ignored": "Invalid lead_id"}, 200

    # For now, record only the events we need.
    if event != "call_ended":
        return {"ok": True, "ignored": event}, 200

    # Translate Retell's final call result into something useful
    # in the Sales AI Activity Notes.
    reason = (disconnection_reason or "").lower()

    no_answer_reasons = {
        "dial_no_answer",
        "dial_busy",
        "dial_failed",
        "voicemail_reached",
        "machine_detected",
    }

    if reason in no_answer_reasons:
        activity_details = f"Mandy AI Result: No Answer ({disconnection_reason})"
        new_status = "Follow-up"
    else:
        activity_details = (
            f"Mandy AI Result: Call ended"
            f" | Reason: {disconnection_reason or 'Not provided'}"
            f" | Retell status: {call_status or 'Not provided'}"
        )
        new_status = None

    with get_db() as conn:
        lead = conn.execute(
            "SELECT id, status FROM leads WHERE id = ?",
            (lead_id,),
        ).fetchone()

        if not lead:
            return {"ok": True, "ignored": "Lead not found"}, 200

        # Do not overwrite a Do Not Call status.
        if new_status and lead["status"] != "Do Not Call":
            conn.execute(
                "UPDATE leads SET status = ? WHERE id = ?",
                (new_status, lead_id),
            )

        conn.execute(
            """
            INSERT INTO activities
            (lead_id, activity_type, details, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                lead_id,
                "Mandy AI Result",
                activity_details,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )

        conn.commit()

    return {
        "ok": True,
        "event": event,
        "lead_id": lead_id,
        "call_id": call_id,
        "result": activity_details,
    }, 200
init_db()

if __name__ == "__main__":
    app.run(debug=True)
