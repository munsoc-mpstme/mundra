import io
import os

import qrcode

qr_folder = os.path.join(os.path.dirname(__file__), "qrcodes")

# The delegate's food badge QR: "DELEGO1:<delegate id>:<diet>", where diet is V (veg),
# N (non-veg), J (jain) or - (not set). The diet is in the code so a scanner can show it
# with no connection; the server still uses the registered preference when it records a
# plate. Day and meal are not in the code: they depend on when it is scanned, so the
# scanner works them out from its own clock (see GET /food/context).
BADGE_PREFIX = "DELEGO1"
_DIET_CODES = {"veg": "V", "non_veg": "N", "jain": "J"}
_CODE_DIETS = {code: diet for diet, code in _DIET_CODES.items()}


def badge_payload(delegate_id: str, food_preference: str | None) -> str:
    return f"{BADGE_PREFIX}:{delegate_id}:{_DIET_CODES.get(food_preference, '-')}"


def parse_badge(raw: str) -> tuple[str, str | None]:
    """The (delegate id, diet) in a scanned code. A plain id (an old badge) has no diet."""
    raw = raw.strip()
    parts = raw.split(":")
    if len(parts) == 3 and parts[0] == BADGE_PREFIX:
        return parts[1], _CODE_DIETS.get(parts[2].upper())
    return raw, None


def qr_png(data: str) -> bytes:
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return buf.getvalue()


def generate_qr(id: str):
    qr = qrcode.QRCode(version=2, box_size=7, border=1)
    qr.add_data(id)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(f"{qr_folder}/{id}.jpg")
