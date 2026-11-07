"""
LLM Classifier
--------------
Fallback classifier for emails the rules can't place. Talks to any
OpenAI-compatible chat endpoint (Ollama, Groq, Gemini, OpenRouter...).

Only sender, subject and Gmail's short snippet are sent - never the body.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from typing import Optional

from app.core import settings
from app.services.gmail.organize_rules import (
    LABEL_BANK_ALERTS,
    LABEL_BANK_OFFERS,
    LABEL_CRYPTO,
    LABEL_FOOD,
    LABEL_GOVERNMENT,
    LABEL_INVEST_STATEMENTS,
    LABEL_JOB_ALERTS,
    LABEL_JOB_APPLICATIONS,
    LABEL_PAYMENTS,
    LABEL_PROPERTY,
    LABEL_RIDES,
    LABEL_SECURITY,
    LABEL_SHOPPING,
    LABEL_TO_DELETE,
    LABEL_TRAVEL,
    EmailMeta,
)

logger = logging.getLogger(__name__)

# Short category keys keep small models accurate; mapped back to labels below.
CATEGORIES: dict[str, Optional[str]] = {
    "bank_offer": LABEL_BANK_OFFERS,
    "bank_alert": LABEL_BANK_ALERTS,
    "investment_statement": LABEL_INVEST_STATEMENTS,
    "job_application": LABEL_JOB_APPLICATIONS,
    "job_alert": LABEL_JOB_ALERTS,
    "shopping_order": LABEL_SHOPPING,
    "travel_booking": LABEL_TRAVEL,
    "ride": LABEL_RIDES,
    "food_order": LABEL_FOOD,
    "property": LABEL_PROPERTY,
    "payment": LABEL_PAYMENTS,
    "security": LABEL_SECURITY,
    "government": LABEL_GOVERNMENT,
    "crypto": LABEL_CRYPTO,
    "promotion": LABEL_TO_DELETE,
    "other": None,
}

SYSTEM_PROMPT = """You sort emails for a user in India into folders. Reply with JSON only: {"category": "<key>"}.

Keys:
- bank_offer: marketing from a bank or credit card issuer (loans, EMI offers, card upgrades, cashback, FD rates).
- bank_alert: bank or card transaction alerts, payment received, bill due reminders.
- investment_statement: a statement or report of the user's OWN mutual fund, demat, NPS or broker holdings. Fund marketing, NFOs, factsheets, webinars and market news are NOT statements (use promotion).
- job_application: about a job the user applied for, or a real recruiter writing personally about a specific role: application received, interview invite or confirmation, assessment, offer. Must look legitimate: named company, specific role, no fees, nothing too good to be true.
- job_alert: automated job recommendations, job-board newsletters, hiring-drive announcements.
- shopping_order: an order the user placed with a shop: order confirmed, shipped, out for delivery, delivered, return, refund.
- travel_booking: the user's own flight, train, bus or hotel booking, ticket, PNR, itinerary, check-in or schedule change.
- ride: a cab, bike-taxi or carpool trip receipt or ride notice (Uber, Ola, Rapido, BlaBlaCar).
- food_order: a food delivery order confirmation or delivery update (Zomato, Swiggy, restaurants).
- property: property listings, rentals, house-visit requests, real-estate portals.
- payment: a payment receipt, invoice, payment link, money sent or received, split bills (not from a bank).
- security: login alerts, new device, password, OTP or verification codes, 2FA, vulnerabilities, credential rotation.
- government: mail from a government department: income tax, Aadhaar, passport, EPF, GST.
- crypto: updates from a crypto exchange about the user's account (not promotions).
- promotion: ads, sales, discounts, newsletters, product announcements, marketing, social-network notifications, scams.
- other: everything else, including personal or work mail, payslips, insurance policies, service notices and terms updates.

Marketing from a shop, airline, food app or exchange is promotion, not its order/booking key.

Rules: if unsure, answer other - a wrong "promotion" may get an important email deleted. A job email that asks for money, uses a free-mail address for a big company, or is vague about the company is promotion."""


def is_enabled() -> bool:
    return bool(settings.llm_base_url)


def _post(path: str, payload: Optional[dict], timeout: float) -> dict:
    url = settings.llm_base_url.rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - user-configured endpoint
        return json.loads(resp.read().decode())


def check_llm() -> dict:
    """Report whether the configured LLM endpoint answers and has the model."""
    if not is_enabled():
        return {"enabled": False, "ok": False, "model": None, "error": "LLM_BASE_URL not set"}
    try:
        data = _post("/models", None, timeout=5)
        models = [m.get("id") for m in data.get("data", [])]
        ok = settings.llm_model in models or not models
        err = None if ok else f"Model '{settings.llm_model}' not found. Available: {', '.join(models[:8])}"
        return {"enabled": True, "ok": ok, "model": settings.llm_model, "error": err}
    except Exception as e:
        return {"enabled": True, "ok": False, "model": settings.llm_model, "error": f"Cannot reach LLM: {e}"}


def parse_category(text: str) -> Optional[str]:
    """Extract a category key from the model's reply; None if unusable."""
    try:
        value = json.loads(text).get("category", "")
    except (json.JSONDecodeError, AttributeError):
        match = re.search(r'"category"\s*:\s*"([a-z_]+)"', text) or re.search(
            r"\b(" + "|".join(CATEGORIES) + r")\b", text
        )
        value = match.group(1) if match else ""
    value = str(value).strip().lower()
    return value if value in CATEGORIES else None


def classify_with_llm(email: EmailMeta) -> tuple[Optional[str], Optional[str]]:
    """Return (label or None, error or None)."""
    user = (
        f"From: {email.sender_name} <{email.sender_email}>\n"
        f"Subject: {email.subject[:200]}\n"
        f"Gmail tab: {', '.join(sorted(email.categories)) or 'none'}\n"
        f"Has unsubscribe link: {'yes' if email.has_unsubscribe else 'no'}\n"
        f"Snippet: {email.snippet[:300]}"
    )
    payload = {
        "model": settings.llm_model,
        "temperature": 0,
        "max_tokens": 30,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
    }
    text = ""
    for attempt in range(2):  # one retry for transient network blips
        try:
            data = _post("/chat/completions", payload, timeout=settings.llm_timeout)
            text = data["choices"][0]["message"]["content"] or ""
            break
        except urllib.error.HTTPError as e:
            return None, f"LLM HTTP {e.code}"
        except Exception as e:
            if attempt == 1:
                return None, f"LLM error: {e}"
            time.sleep(2)

    key = parse_category(text)
    if key is None:
        logger.debug("Unparseable LLM reply: %r", text)
        return None, None
    # Small models over-use the job folders; require some job signal to accept them.
    if key in ("job_application", "job_alert") and not RE_JOB_SIGNAL.search(
        f"{email.sender_email} {email.sender_name} {email.subject} {email.snippet}"
    ):
        return None, None
    return CATEGORIES[key], None


RE_JOB_SIGNAL = re.compile(
    r"\bjobs?\b|interview|recruit|hiring|career|candidate|applica|resume|\bcv\b|\brole\b|position|opening|offer letter",
    re.I,
)
