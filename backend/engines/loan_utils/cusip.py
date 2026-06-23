import random
import string

_COUNTRY_PREFIX = {
    "GB": "GB", "US": "US", "NO": "NO", "AT": "AT", "FR": "FR",
    "IT": "IT", "CH": "CH", "AU": "AU", "CA": "CA", "SG": "SG",
}
_BODY_CHARS = string.ascii_uppercase + string.digits


def _char_value(c):
    if c.isdigit():
        return int(c)
    if c.isalpha():
        return ord(c.upper()) - ord("A") + 10
    if c == "*":
        return 36
    if c == "@":
        return 37
    if c == "#":
        return 38
    raise ValueError(f"Invalid CUSIP character: {c!r}")


def check_digit(first_eight):
    if len(first_eight) != 8:
        raise ValueError("CUSIP base must be exactly 8 characters")
    total = 0
    for i, c in enumerate(first_eight):
        v = _char_value(c)
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return (10 - (total % 10)) % 10


def generate_cusip(country_code):
    prefix = _COUNTRY_PREFIX.get((country_code or "").upper(), "XX")
    body = "".join(random.choice(_BODY_CHARS) for _ in range(6))
    base = prefix + body
    return base + str(check_digit(base))


def generate_roll_cusips(country_code, max_count=3):
    n = random.randint(1, max_count)
    return ",".join(generate_cusip(country_code) for _ in range(n))
