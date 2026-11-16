"""Conservative, explainable rules for recruiter-email status classification."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Classification:
    status: str
    confidence: float
    reason: str


RULES: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("rejected", ("not moving forward", "will not be moving forward", "decided not to proceed", "other candidates", "unfortunately"), 0.95),
    ("offer", ("we are pleased to offer", "formal offer", "offer letter"), 0.98),
    ("interviewing", ("schedule an interview", "schedule a call", "interview invitation", "calendly.com", "meet with the team"), 0.9),
    ("screening", ("phone screen", "recruiter screen", "initial screen", "move forward with your application"), 0.85),
    ("recruiter_contact", ("recruiter", "reaching out about", "your background", "opportunity at"), 0.8),
    ("applied", ("thank you for applying", "application received", "received your application"), 0.9),
    ("waiting", ("still under consideration", "still reviewing", "update you soon"), 0.8),
)

# These are signals for whether it is worth retrieving and retaining an email's
# body. They are not a claim that a message changed an application status.
RECRUITING_DOMAINS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkday.com", "smartrecruiters.com",
    "icims.com", "jobvite.com", "workablemail.com",
)
RECRUITING_SUBJECT_TERMS = (
    "application", "interview", "recruiter", "job opportunity", "phone screen",
    "next steps", "thanks for applying", "online submission", "hiring process",
)
MARKETING_SUBJECT_TERMS = (
    "limited time offer", "chase offers", "bonus points", "statement credit",
    "cardholder", "wealth management", "dining offer", "popular in your network",
    "podcast interview", "new jobs similar", "job alert",
)


def is_recruiting_candidate(
    sender: str, subject: str, known_companies: tuple[str, ...]
) -> tuple[bool, str]:
    """Use metadata-only signals to decide whether the full body is warranted."""
    sender_lower = sender.lower()
    subject_lower = subject.lower()
    for term in MARKETING_SUBJECT_TERMS:
        if term in subject_lower:
            return False, f"Subject matched marketing/newsletter exclusion: {term!r}"
    for domain in RECRUITING_DOMAINS:
        if domain in sender_lower:
            return True, f"Sender matched recruiting platform domain: {domain}"
    for company in known_companies:
        if len(company) >= 3 and company.lower() in f"{sender_lower}\n{subject_lower}":
            return True, f"Sender or subject matched tracked company: {company}"
    for term in RECRUITING_SUBJECT_TERMS:
        if term in subject_lower:
            return True, f"Subject matched recruiting term: {term!r}"
    return False, "No recruiting sender, company, or subject signal"


def classify_email(subject: str, body: str) -> Classification:
    """Classify an email without sending its contents to a third party."""
    text = f"{subject}\n{body}".lower()
    for status, phrases, confidence in RULES:
        for phrase in phrases:
            if phrase in text:
                return Classification(status, confidence, f"Matched phrase: {phrase!r}")
    return Classification("needs_review", 0.0, "No high-confidence rule matched")
