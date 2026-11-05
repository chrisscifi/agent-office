"""Privacy guard: find (and scrub) personal data so it never ends up in products or in prompts to AI services.

Detects: e-mail addresses, phone numbers, Danish CPR numbers, IBANs, payment card numbers, and
long ID/passport-style numbers next to words like 'passport'. Obvious example data is allowed
(example.com/.org/.net, +45 00 00 00 00, 'Guest Name' etc.).
"""
import re

ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "example.dk", "domain.com", "yourcompany.com",
                         "yourhostel.com", "company.com", "email.com")

EMAIL = re.compile(r"\b[\w.+-]+@([\w-]+\.)+[a-zA-Z]{2,}\b")
PHONE = re.compile(r"(?<![\w/.:-])(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,4}\d{2,4}(?![\w/.:-])")
CPR = re.compile(r"\b(0[1-9]|[12]\d|3[01])(0[1-9]|1[0-2])\d{2}-?\d{4}\b")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b")
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
PASSPORT = re.compile(r"(?i)\b(passport|pas|паспорт|pașaport|id card|id no\.?)\b[^\n]{0,20}?\b([A-Z]{0,2}\d{6,9})\b")


def _luhn(num):
    digits = [int(d) for d in re.sub(r"\D", "", num)]
    if len(digits) < 13 or len(set(digits)) <= 2:   # 0000…, 1111… are examples, not cards
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _fake_phone(s):
    digits = re.sub(r"\D", "", s)
    core = digits[-8:]
    return len(set(core)) <= 2 or core in ("12345678", "87654321") or "0000" in core


def _phone_like(s):
    digits = re.sub(r"\D", "", s)
    if not 8 <= len(digits) <= 15:
        return False
    if re.fullmatch(r"(19|20)\d{2}[\s./-]?\d{1,2}[\s./-]?\d{1,2}", s.strip()):  # dates
        return False
    if re.search(r"\d+[.,]\d{2}$", s.strip()) and " " not in s.strip():         # money like 1234.50
        return False
    return s.strip().startswith("+") or bool(re.search(r"\d{2}[\s.-]\d{2}", s))


def scan(text):
    """Return a list of (kind, sample) for personal data that looks real."""
    found = []
    for m in EMAIL.finditer(text):
        if not m.group(0).lower().endswith(ALLOWED_EMAIL_DOMAINS):
            found.append(("e-mail address", m.group(0)))
    for m in CPR.finditer(text):
        found.append(("CPR number", m.group(0)))
    for m in IBAN.finditer(text):
        if not re.search(r"0{6,}", m.group(0).replace(" ", "")):
            found.append(("IBAN / bank account", m.group(0)))
    for m in CARD.finditer(text):
        if _luhn(m.group(0)) and not re.fullmatch(r"[\d ]*?(4242 ?4242|4111 ?1111)[\d ]*", m.group(0)):
            found.append(("payment card number", m.group(0)))
    for m in PASSPORT.finditer(text):
        found.append(("passport / ID number", m.group(2)))
    masked = text
    for kind, sample in found:   # don't count bank/card/CPR digits a second time as phone numbers
        masked = masked.replace(sample, " ")
    for m in PHONE.finditer(masked):
        s = m.group(0)
        if _phone_like(s) and not _fake_phone(s) and not CPR.fullmatch(s.strip()):
            found.append(("phone number", s.strip()))
    seen, out = set(), []
    for kind, sample in found:
        if sample not in seen:
            seen.add(sample)
            out.append((kind, sample))
    return out


def redact(text):
    """Replace personal data with neutral placeholders before text leaves the laptop."""
    if not text:
        return text
    text = EMAIL.sub(lambda m: m.group(0) if m.group(0).lower().endswith(ALLOWED_EMAIL_DOMAINS) else "[email]", text)
    text = CPR.sub("[cpr-number]", text)
    text = IBAN.sub(lambda m: m.group(0) if re.search(r"0{6,}", m.group(0).replace(" ", "")) else "[iban]", text)
    text = CARD.sub(lambda m: "[card-number]" if _luhn(m.group(0)) else m.group(0), text)
    text = PASSPORT.sub(lambda m: m.group(0).replace(m.group(2), "[id-number]"), text)
    text = PHONE.sub(lambda m: "[phone]" if _phone_like(m.group(0)) and not _fake_phone(m.group(0)) else m.group(0), text)
    return text


NETWORK_CALLS = re.compile(r"\b(urllib|requests|http\.client|socket|WebClient|HttpClient|smtplib|ftplib)\b")
