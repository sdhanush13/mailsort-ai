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
]

# ----- Keep (never labelled, never sent to the LLM) -----
# Orders, deliveries, payments, bookings and account/security mail stay where they are.
KEEP_SENDERS = [
    "order-update@amazon.in", "shipment-tracking@amazon.in", "auto-confirm@amazon.in",
    "return@amazon.in", "shopifyemail.com", "delhivery.com", "razorpay.com", "payu.in",
    "irctc.co.in", "accounts.google.com", "payments-noreply@google.com",
    "noreply@github.com", "notifications@github.com", "splitwise.com",
]
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


# ----- Classifier -----


# Returned by classify_by_rules for mail that must be left alone (and not sent to the LLM)
KEEP = ""


def classify_by_rules(email: EmailMeta) -> Optional[str]:
    """Return a label path, KEEP to leave the email alone, or None when unsure (-> LLM)."""
    subject = email.subject or ""

    # 1. Known noise / impersonators
    if sender_matches(email, NOISE_SENDERS):
        return LABEL_TO_DELETE

    # 1b. Orders, payments, bookings, account & security mail: never touch.
    # Bank mail is excluded here so bank alerts still reach Banks/Alerts below.
    if find_bank(email) is None and not sender_matches(email, BANK_ALERT_SENDERS):
        if sender_matches(email, KEEP_SENDERS) or RE_KEEP_SUBJECT.search(subject):
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
]


def is_managed_label(name: str) -> bool:
    """True for labels this feature owns (used to skip already-organized mail)."""
    return name in STATIC_LABELS or (
        name.startswith("Banks/") and name.endswith((f"/{SAVINGS}", f"/{CREDIT_CARD}"))
    )
