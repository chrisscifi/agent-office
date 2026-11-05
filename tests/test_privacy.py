from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agency.privacy import redact, scan  # noqa: E402

REAL = ("Guest: Anna Jensen, anna.jensen@gmail.com, +45 28 91 47 33, CPR 120389-1234. "
        "Pay to IBAN DK50 0040 0440 1162 43, card 4532 0151 1283 0366. Passport no. N1234567.")
EXAMPLES = ("Contact reception@yourhostel.com or guest@example.com, phone +45 00 00 00 00. Shift 2026-11-05 22:00, "
            "total 1,250.00 DKK, ISO 19650-2:2018, Uniclass Ss_20_05_65, test card 4242 4242 4242 4242, "
            "IBAN DK00 0000 0000 0000 00, =SUM(B2:B30), version 2.16.1.2727, date 05/11/2026.")


def test_finds_every_kind_of_real_personal_data():
    kinds = {k for k, _ in scan(REAL)}
    assert kinds == {"e-mail address", "CPR number", "IBAN / bank account", "payment card number",
                     "passport / ID number", "phone number"}


def test_ignores_example_data_and_ordinary_numbers():
    assert scan(EXAMPLES) == []


def test_redact_removes_personal_data_before_it_leaves_the_machine():
    out = redact(REAL)
    for secret in ("anna.jensen@gmail.com", "120389-1234", "28 91 47 33", "4532 0151", "N1234567", "DK50"):
        assert secret not in out
    assert "[email]" in out and "[cpr-number]" in out


def test_redact_keeps_example_data():
    assert redact(EXAMPLES) == EXAMPLES
