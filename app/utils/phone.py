"""Phone-number normalization shared by every SowTrust channel."""
import re


_NIGERIAN_E164 = re.compile(r"^\+234[789]\d{9}$")


def normalize_phone(raw: str | None) -> str | None:
    """Return a Nigerian mobile number in E.164 form or ``None``.

    Accepted examples: ``08012345678``, ``2348012345678`` and
    ``+2348012345678``. Formatting spaces, dashes and parentheses are
    ignored, but extensions and malformed numbers are rejected.
    """
    if not raw:
        return None

    value = re.sub(r"[\s\-()]", "", str(raw).strip())
    if value.startswith("00"):
        value = "+" + value[2:]
    elif value.startswith("0"):
        value = "+234" + value[1:]
    elif value.startswith("234"):
        value = "+" + value
    elif len(value) == 10 and value[0] in "789":
        value = "+234" + value

    return value if _NIGERIAN_E164.fullmatch(value) else None


def mask_phone(raw: str | None) -> str:
    """Mask a phone number for user-facing confirmations and logs."""
    phone = normalize_phone(raw)
    if not phone:
        return "invalid number"
    return f"{phone[:4]}***{phone[-4:]}"
