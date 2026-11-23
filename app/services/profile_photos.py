"""Validate and shrink account pictures before storing them with the account."""
import io
import math
import secrets
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from .accounts import ValidationError

MAX_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 12_000_000
PHOTO_SIZE = 384


def _crop_box(image, crop):
    """Browser coordinates are fractions of the EXIF-oriented source image."""
    keys = ("crop_x", "crop_y", "crop_size")
    if crop is None or not any(key in crop for key in keys):
        return None
    try:
        x, y, size = (float(crop[key]) for key in keys)
        if not all(math.isfinite(value) for value in (x, y, size)):
            raise ValueError
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < size <= 1):
            raise ValueError
        width, height = image.size
        side = min(width, height) * size
        left, top = width * x, height * y
        # Allow only floating-point rounding at the image boundary.
        if side < 1 or left + side > width + .001 or top + side > height + .001:
            raise ValueError
        left, top = min(left, width - side), min(top, height - side)
        return left, top, left + side, top + side
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValidationError({"photo": "Choose the picture again and adjust its crop."}) from None


def save(conn, user_id, stream, crop=None):
    data = stream.read(MAX_BYTES + 1)
    if not data:
        raise ValidationError({"photo": "Choose a picture first."})
    if len(data) > MAX_BYTES:
        raise ValidationError({"photo": "Choose a picture smaller than 2 MB."})
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValidationError({"photo": "Use a JPG, PNG or WEBP picture."})
                if source.width * source.height > MAX_PIXELS:
                    raise ValidationError({"photo": "Crop or resize your picture to 12 megapixels or less."})
                source.load()
                oriented = ImageOps.exif_transpose(source)
                # Flatten transparency and drop EXIF/GPS and other uploaded metadata.
                box = _crop_box(oriented, crop)
                square = (oriented.resize((PHOTO_SIZE, PHOTO_SIZE), Image.Resampling.LANCZOS, box=box)
                          if box else ImageOps.fit(oriented, (PHOTO_SIZE, PHOTO_SIZE), Image.Resampling.LANCZOS))
                rgba = square.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                output = io.BytesIO()
                background.save(output, format="JPEG", quality=85)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValidationError({"photo": "That picture could not be read. Try another JPG, PNG or WEBP."}) from None

    with conn:
        conn.execute("""INSERT INTO profile_photos (user_id, image, revision) VALUES (?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET image=excluded.image, revision=excluded.revision""",
                     (user_id, output.getvalue(), secrets.token_hex(16)))


def remove(conn, user_id):
    with conn:
        conn.execute("DELETE FROM profile_photos WHERE user_id = ?", (user_id,))
