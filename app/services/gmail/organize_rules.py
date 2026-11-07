"""
Auto-Organize Rules
-------------------
Deterministic rules that map an email (sender, subject, Gmail category) to a
label path. Rules run first; only emails no rule can place are sent to the LLM.

Edit the tables below to add banks, job sites or noisy senders. Domains match
the sender's domain and all of its subdomains (``icicibank.com`` also matches
``custcomm.icicibank.com``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ----- Label names -----

LABEL_BANK_OFFERS = "Banks/Offers"
LABEL_BANK_ALERTS = "Banks/Alerts"
LABEL_BANK_OTHER_STATEMENTS = "Banks/Other/Statements"
LABEL_INVEST_STATEMENTS = "Investments/Statements"
LABEL_JOB_APPLICATIONS = "Jobs/Applications"
LABEL_JOB_ALERTS = "Jobs/Alerts"
LABEL_TO_DELETE = "To Delete"
LABEL_SHOPPING = "Shopping/Orders"
LABEL_TRAVEL = "Travel/Bookings"
LABEL_RIDES = "Travel/Rides"
LABEL_FOOD = "Food/Orders"
LABEL_PROPERTY = "Property"
LABEL_PAYMENTS = "Payments"
LABEL_SECURITY = "Security"
LABEL_GOVERNMENT = "Government & Tax"
LABEL_CRYPTO = "Investments/Crypto"

# Bump when the categories change: emails earlier scans left alone (and AI
# "other" answers) are then re-checked automatically, since they may now fit.
RULES_VERSION = 2

SAVINGS = "Savings"
CREDIT_CARD = "Credit Card"


def bank_label(bank: str, kind: str) -> str:
    return f"Banks/{bank}/{kind}"


# ----- Banks -----
# name -> (domains, credit-card-only issuer?)
BANKS: dict[str, tuple[list[str], bool]] = {
    "ICICI": (["icicibank.com", "icici.bank.in"], False),
    "AU": (["aubank.in"], False),
    "Kotak": (["kotak.com", "kotak.bank.in"], False),
    "IndusInd": (["indusind.com", "indusind.bank.in"], False),
    "HDFC": (["hdfcbank.com", "hdfcbank.net", "hdfcbank.bank.in"], False),
    "YES": (["yesbank.in", "yes.bank.in"], False),
    "IDFC FIRST": (["idfcfirstbank.com", "idfcfirst.bank.in"], False),
    "Axis": (["axisbank.com", "axis.bank.in", "axisbankmail.bank.in"], False),
    "SBI": (["sbi.co.in", "sbi.bank.in", "sbicard.com"], False),
    "Standard Chartered": (["sc.com"], False),
    "OneCard": (["getonecard.app"], True),
    "Scapia": (["scapia.cards"], True),
}

# Card-marketing senders that are not banks themselves -> Banks/Offers
CARD_MARKETERS = ["info.paytm.com", "banking2day.com"]

# Third parties that send bill reminders / payment confirmations -> Banks/Alerts
BANK_ALERT_SENDERS = ["protect@cred.club", "billreminder@kiwiclub.co.in", "amazonpay.in"]

# ----- Investments -----
INVESTMENT_DOMAINS = [
    "cdslstatement.com", "cdslindia.com", "nsdl.co.in", "camsonline.com",
    "kfintech.com", "groww.in", "upstox.com", "zerodha.com", "zerodha.net",
    "proteantech.in", "bseindia.in", "bseindia.com", "nseindia.com",
    "motilaloswal.com", "quant.in", "paytmmoney.com", "kuvera.in", "coin.zerodha.com",
    "miraeassetmf.co.in", "sbimf.com", "hdfcfund.com", "icicipruamc.com", "nipponindiaim.com",
]

# ----- Jobs -----
JOB_SITE_DOMAINS = [
    "glassdoor.com", "naukri.com", "naukrigulf.com", "monsterindia.com",
    "foundit.in", "hirist.tech", "hirist.com", "instahyre.com", "indeed.com",
    "wellfound.com", "cutshort.io", "seek.com.au", "shine.com", "jobleads.com",
    "talent500.co", "iimjobs.com", "ambitionbox.com", "apna.co", "jobscan.co",
    "mail-hackajob.com", "hackajob.com", "resumeworded.com",
]
# Company career-site marketing (hiring drives, "your next opportunity")
RE_CAREERS_SENDER = re.compile(r"(^|[.@])(careers?|careeralerts|recruiting|jobs|join)[.@]", re.I)
LINKEDIN_JOB_SENDERS = [
    "jobs-noreply@linkedin.com", "jobalerts-noreply@linkedin.com",
    "jobs-listings@linkedin.com",
]
# Applicant-tracking systems: mail from these is about an application you made
ATS_DOMAINS = [
    "myworkday.com", "successfactors.com", "successfactors.eu", "greenhouse.io",
    "greenhouse-mail.io", "lever.co", "hire.lever.co", "smartrecruiters.com",
    "icims.com", "taleo.net", "ashbyhq.com", "workablemail.com", "workable.com",
    "jobvite.com", "recruitee.com", "zohorecruit.com", "darwinbox.in", "keka.com",
]

# ----- Noise -----
# Senders that are always clutter (newsletters, social pings, impersonators)
NOISE_SENDERS = [
    "banking2day.com",  # uses "HDFC Bank credit card" display name; not HDFC
    "money.livemint.com", "money.hindustantimes.com", "medium.com",
    "minis.naukri.com", "academia-mail.com",
    "messages-noreply@linkedin.com", "notifications-noreply@linkedin.com",
    "updates-noreply@linkedin.com", "invitations@linkedin.com", "em.linkedin.com",
    "digest.groww.in", "campaigns.upstox.com", "quora.com",
    # Marketing-only address; bookings come from other easemytrip.com addresses
    "connect@easemytrip.com",
    "mailers.zomato.com",  # Zomato's marketing; orders come from zomato.com
]

# ----- Everyday categories -----
# Shops and couriers: order/shipping mail from these -> Shopping/Orders
SHOPPING_DOMAINS = [
    "amazon.in", "amazon.com", "flipkart.com", "myntra.com", "ajio.com", "meesho.com",
    "nykaa.com", "1mg.com", "pharmeasy.in", "netmeds.com", "bigbasket.com", "blinkit.com",
    "zeptonow.com", "croma.com", "tatacliq.com", "lenskart.com", "coolwinks.com",
    "snitch.co.in", "decathlon.in", "sharepal.in", "shopifyemail.com", "delhivery.com",
    "bluedart.com", "ekartlogistics.com", "shiprocket.in", "dunzo.in", "jiomart.com",
]
# Flights, trains, buses and stays -> Travel/Bookings
TRAVEL_DOMAINS = [
    "airbnb.com", "irctc.co.in", "airindia.com", "airindia.in", "goindigo.in",
    "akasaair.com", "spicejet.com", "airvistara.com", "makemytrip.com", "goibibo.com",
    "cleartrip.com", "easemytrip.com", "yatra.com", "ixigo.com", "agoda.com", "booking.com",
    "expedia.com", "trip.com", "redbus.in", "abhibus.com", "ksrtc.in", "oyorooms.com",
    "oyo.com", "staybloom.com", "treebo.com", "fabhotels.com", "zostel.com",
]
RIDE_DOMAINS = ["uber.com", "olacabs.com", "rapido.bike", "blablacar.com", "blu-smart.com"]
# Food delivery: order mail from these -> Food/Orders
FOOD_DOMAINS = [
    "zomato.com", "swiggy.in", "eatclub.in", "burgerking.in", "dominos.co.in",
    "foodpanda.in", "faasos.io", "box8.in", "mcdonaldsindia.com", "magicpin.in",
]
PROPERTY_DOMAINS = [
    "magicbricks.com", "nobroker.in", "housing.com", "99acres.com", "squareyards.com",
    "commonfloor.com", "nestaway.com", "makaan.com",
]
CRYPTO_DOMAINS = [
    "zebpay.com", "coindcx.com", "wazirx.com", "coinswitch.co", "binance.com",
    "mudrex.com", "bitbns.com",
]
# Any *.gov.in / *.nic.in sender -> Government & Tax
GOVERNMENT_DOMAINS = ["gov.in", "nic.in"]
PAYMENT_SENDERS = [
    "razorpay.com", "payu.in", "splitwise.com", "payments-noreply@google.com",
    "paypal.com", "phonepe.com", "cashfree.com", "billdesk.com", "mobikwik.com",
    "instamojo.com", "stripe.com", "paytm.com",
]
SECURITY_SENDERS = ["accounts.google.com"]
# Personal mailboxes: subject-only rules never apply to mail from these
FREEMAIL_DOMAINS = [
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "outlook.com",
    "hotmail.com", "live.com", "rediffmail.com", "icloud.com", "proton.me", "protonmail.com",
]

RE_SECURITY = re.compile(
    r"security (alert|update|incident|disclosure|advisory|notice|notification)|vulnerabilit|rotate credentials"
    r"|\b2fa\b|two-factor|password|\botp\b|one[- ]time (password|code)|verification code"
    r"|login code|verify your|confirm your email|new login|log-?in alert|sign-?in (attempt|alert)"
    r"|account (was )?(recovered|activated|suspended|locked)|new (mobile )?device"
    r"|authentication added",
    re.I,
)
RE_ORDER = re.compile(
    r"\border(s|ed)?\b|shipped|dispatched|out for delivery|delivered|shipment|arriving"
    r"|return (received|request|pickup)|refund|cancell?ed|installation",
    re.I,
)
RE_ORDER_SENDER = re.compile(r"order|ship|track|deliver|confirm|return", re.I)
RE_TRAVEL = re.compile(
    r"booking (confirm|id)|\bpnr\b|e-?ticket|boarding pass|itinerary|web check-?in"
    r"|reservation|flight|travel policy",
    re.I,
)
RE_PAYMENT = re.compile(
    r"\breceipt\b|\binvoice|payment (successful|received|confirm|failed|link)"
    r"|refund (successful|processed|initiated)|paid you|transaction (successful|failed)"
    r"|money (sent|received)",
    re.I,
)
RE_FORWARD_OR_REPLY = re.compile(r"^\s*(re|fw|fwd)\s*:", re.I)

# ----- Keep (never labelled, never sent to the LLM) -----
# Account and work mail no category above claims stays where it is.
KEEP_SENDERS = ["noreply@github.com", "notifications@github.com"]
RE_KEEP_SUBJECT = re.compile(
    r"security (alert|update|incident|disclosure|advisory)|vulnerabilit|rotate credentials|\b2fa\b"
    r"|two-factor|password|\botp\b|verification code|verify your|confirm your email|new login"
    r"|sign-?in (attempt|alert)|account (was )?(recovered|activated|suspended|locked)"
    r"|new (mobile )?device|\border(ed)?\b|shipped|out for delivery|delivered|shipment"
    r"|refund|invoice|receipt|payslip|salary|booking confirm|\bpnr\b|e-?ticket|policy (is )?issued"
    r"|warranty|terms of (service|use) (have been )?updated|e-voting",
    re.I,
)
# Marketing words: newsletters with an unsubscribe link and these words are clutter
RE_MARKETING = re.compile(
    r"\bsale\b|\boffers?\b|\bdeals?\b|% off|\bdiscount|cashback|coupon|webinar|\bnfo\b|\bipo\b"
    r"|book now|shop now|don.t miss|last chance|limited time|introducing|new in |🎁|🔥|⚡|✨",
    re.I,
)

# ----- Subject patterns -----
RE_STATEMENT = re.compile(r"\b(e-?statements?|statements?|estatement)\b", re.I)
RE_NOT_STATEMENT = re.compile(
    r"key fact statement|privacy statement|terms|access your .*statement instantly|make a statement",
    re.I,
)
RE_CARD = re.compile(r"credit\s*card|\bcard\b|\bcc\b", re.I)
RE_CARD_SENDER = re.compile(r"card|^cc[._]", re.I)
RE_BANK_ALERT = re.compile(
    r"transaction|debit(ed)?|credit(ed)? alert|alert|payment (received|successful|update|reminder)"
    r"|bill (is )?due|due (on|date)|standing instruction|\bimps\b|\bneft\b|\brtgs\b|\bupi\b"
    r"|\botp\b|authentication|maintenance|be alert|scam|fraud|security|safe(ty|guard)"
    r"|important (notice|information)|reward points? redeemed|refund",
    re.I,
)
RE_INVEST_STATEMENT = re.compile(
    r"statement|\bcas\b|holding|valuation|contract note|balance|transaction|contribution",
    re.I,
)
RE_JOB_APPLICATION = re.compile(
    r"you applied|applied for|application (successful|received|submitted|status|update)"
    r"|your application|thank you for (your )?(application|applying|your interest)"
    r"|interview (invitation|invite|confirmation|scheduled|reminder)|invitation.*interview"
    r"|shortlisted|screening (questionnaire|call)|assessment invitation|next steps"
    r"|offer letter|job opportunity",
    re.I,
)
RE_JOB_WORDS = re.compile(r"\bjobs?\b|hiring|opening|opportunit", re.I)


# ----- Email model -----


@dataclass
class EmailMeta:
    """The subset of an email the classifier looks at."""

    sender_email: str
    sender_name: str = ""
    subject: str = ""
    snippet: str = ""
    categories: set[str] = field(default_factory=set)  # e.g. {"promotions"}
    has_unsubscribe: bool = False

    @property
    def domain(self) -> str:
        return self.sender_email.rsplit("@", 1)[-1].lower() if "@" in self.sender_email else ""

    @property
    def local(self) -> str:
        return self.sender_email.split("@", 1)[0].lower()


def domain_matches(domain: str, bases: list[str]) -> bool:
    domain = domain.lower()
    return any(domain == b or domain.endswith("." + b) for b in bases)


def sender_matches(email: EmailMeta, entries: list[str]) -> bool:
    """Entries are full addresses (contain '@') or domains."""
    addr = email.sender_email.lower()
    for entry in entries:
        if "@" in entry:
            if addr == entry:
                return True
        elif domain_matches(email.domain, [entry]):
            return True
    return False


def find_bank(email: EmailMeta) -> Optional[tuple[str, bool]]:
    for name, (domains, card_only) in BANKS.items():
        if domain_matches(email.domain, domains):
            return name, card_only
    return None


def classify_everyday(email: EmailMeta) -> Optional[str]:
    """Shopping, travel, food, property, payments, security, government and crypto mail."""
    subject = email.subject or ""
    # Subject-only rules are for automated mail, not people writing or forwarding
    automated = not domain_matches(email.domain, FREEMAIL_DOMAINS) and not RE_FORWARD_OR_REPLY.search(subject)

    if sender_matches(email, SECURITY_SENDERS) or (automated and RE_SECURITY.search(subject)):
        return LABEL_SECURITY
    if domain_matches(email.domain, INVESTMENT_DOMAINS):
        return None  # statements and fund mail are handled by the investment rules

    if domain_matches(email.domain, RIDE_DOMAINS):
        return LABEL_RIDES
    if domain_matches(email.domain, TRAVEL_DOMAINS):
        return LABEL_TRAVEL
    if domain_matches(email.domain, PROPERTY_DOMAINS):
        return LABEL_PROPERTY
    if domain_matches(email.domain, CRYPTO_DOMAINS):
        return LABEL_CRYPTO
    if domain_matches(email.domain, GOVERNMENT_DOMAINS):
        return LABEL_GOVERNMENT
    is_order = bool(RE_ORDER.search(subject))
    if domain_matches(email.domain, FOOD_DOMAINS):
        return LABEL_FOOD if is_order else None
    if sender_matches(email, PAYMENT_SENDERS):
        return LABEL_PAYMENTS
    if domain_matches(email.domain, SHOPPING_DOMAINS):
        return LABEL_SHOPPING if is_order or RE_ORDER_SENDER.search(email.local) else None

    if automated:
        if RE_TRAVEL.search(subject):
            return LABEL_TRAVEL
        if RE_PAYMENT.search(subject):
            return LABEL_PAYMENTS
        if is_order:
            return LABEL_SHOPPING
    return None


# ----- Classifier -----


# Returned by classify_by_rules for mail that must be left alone (and not sent to the LLM)
KEEP = ""


def classify_by_rules(email: EmailMeta) -> Optional[str]:
    """Return a label path, KEEP to leave the email alone, or None when unsure (-> LLM)."""
    subject = email.subject or ""

    # 1. Known noise / impersonators
    if sender_matches(email, NOISE_SENDERS):
        return LABEL_TO_DELETE

    # 1b. Everyday mail (orders, trips, payments, security...), then protected mail.
    # Bank mail is excluded here so bank alerts still reach Banks/Alerts below.
    is_bankish = (
        find_bank(email) is not None
        or sender_matches(email, BANK_ALERT_SENDERS)
        or sender_matches(email, CARD_MARKETERS)
    )
    # A newsletter full of sales words ("Delivered next day ⚡") is an ad, not an order
    is_ad = email.has_unsubscribe and bool(RE_MARKETING.search(subject))
    if not is_bankish:
        if not is_ad and "promotions" not in email.categories:
            label = classify_everyday(email)
            if label:
                return label
        if sender_matches(email, KEEP_SENDERS) or (not is_ad and RE_KEEP_SUBJECT.search(subject)):
            return KEEP

    is_statement = bool(RE_STATEMENT.search(subject)) and not RE_NOT_STATEMENT.search(subject)
    bank = find_bank(email)

    # 2. Bank statements -> Banks/<Bank>/<Savings|Credit Card>
    if bank and is_statement and "promotions" not in email.categories:
        name, card_only = bank
        is_card = card_only or RE_CARD.search(subject) or RE_CARD_SENDER.search(email.local)
        return bank_label(name, CREDIT_CARD if is_card else SAVINGS)

    # 3. Investment statements; the rest of fund-house bulk mail (NFOs, results) is clutter
    if domain_matches(email.domain, INVESTMENT_DOMAINS):
        if RE_INVEST_STATEMENT.search(subject) and "promotions" not in email.categories:
            return LABEL_INVEST_STATEMENTS
        if email.has_unsubscribe or "promotions" in email.categories:
            return LABEL_TO_DELETE

    # 4. Job applications (before job alerts: job sites also send application receipts)
    is_job_sender = (
        domain_matches(email.domain, JOB_SITE_DOMAINS)
        or sender_matches(email, LINKEDIN_JOB_SENDERS)
    )
    if domain_matches(email.domain, ATS_DOMAINS):
        return LABEL_JOB_APPLICATIONS
    if RE_JOB_APPLICATION.search(subject) and "promotions" not in email.categories:
        if domain_matches(email.domain, ["ambitionbox.com"]):
            return LABEL_JOB_ALERTS  # "you applied to X - read reviews" is marketing
        if is_job_sender:
            return LABEL_JOB_APPLICATIONS
    # Recruiter messages relayed by Naukri: <hash>=@naukri.com
    if email.domain == "naukri.com" and "=" in email.local:
        return LABEL_JOB_APPLICATIONS

    # 5. Bank alerts & bill reminders
    if sender_matches(email, BANK_ALERT_SENDERS):
        return LABEL_BANK_ALERTS
    if bank and RE_BANK_ALERT.search(subject) and "promotions" not in email.categories:
        return LABEL_BANK_ALERTS

    # 6. Everything else from a bank or card marketer is an offer
    if bank or sender_matches(email, CARD_MARKETERS):
        return LABEL_BANK_OFFERS

    # 7. Job alerts
    if is_job_sender:
        return LABEL_JOB_ALERTS
    if email.domain == "linkedin.com" and RE_JOB_WORDS.search(subject):
        return LABEL_JOB_ALERTS
    if RE_CAREERS_SENDER.search(email.sender_email) and email.has_unsubscribe:
        return LABEL_JOB_ALERTS

    # 8. Gmail already says it's a promotion, or it's a bulk mail full of marketing words
    if "promotions" in email.categories:
        return LABEL_TO_DELETE
    if email.has_unsubscribe and RE_MARKETING.search(subject):
        return LABEL_TO_DELETE

    return None


# All labels this feature may create (bank labels are added dynamically)
STATIC_LABELS = [
    LABEL_BANK_OFFERS,
    LABEL_BANK_ALERTS,
    LABEL_BANK_OTHER_STATEMENTS,
    LABEL_INVEST_STATEMENTS,
    LABEL_JOB_APPLICATIONS,
    LABEL_JOB_ALERTS,
    LABEL_TO_DELETE,
    LABEL_SHOPPING,
    LABEL_TRAVEL,
    LABEL_RIDES,
    LABEL_FOOD,
    LABEL_PROPERTY,
    LABEL_PAYMENTS,
    LABEL_SECURITY,
    LABEL_GOVERNMENT,
    LABEL_CRYPTO,
]


def is_managed_label(name: str) -> bool:
    """True for labels this feature owns (used to skip already-organized mail)."""
    return name in STATIC_LABELS or (
        name.startswith("Banks/") and name.endswith((f"/{SAVINGS}", f"/{CREDIT_CARD}"))
    )
