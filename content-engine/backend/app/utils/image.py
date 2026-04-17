"""
app/utils/image.py — Image processing utilities.

Uses Pillow to convert base64 images to WebP and resize them.
"""
from __future__ import annotations

import base64
import io
from PIL import Image

def convert_to_webp(base64_data: str, max_width: int = 1200) -> bytes:
    """
    Convert a base64 encoded image (PNG/JPEG) to WebP and resize if necessary.

    Args:
        base64_data: Base64 encoded image data.
        max_width: Maximum width for the output image.

    Returns:
        Bytes of the converted WebP image.
    """
    image_data = base64.b64decode(base64_data)
    img = Image.open(io.BytesIO(image_data))

    # Convert to RGB if necessary (e.g., if it's RGBA and we want to save as WebP)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # Resize if width exceeds max_width
    if img.width > max_width:
        ratio = max_width / float(img.width)
        height = int(float(img.height) * float(ratio))
        img = img.resize((max_width, height), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    img.save(output, format="WEBP", quality=80, method=6)
    return output.getvalue()
