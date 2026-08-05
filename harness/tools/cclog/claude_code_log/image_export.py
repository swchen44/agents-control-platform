"""Image export utilities for Claude Code transcripts.

This module provides format-agnostic image export functionality that can be used
by both HTML and Markdown renderers.
"""

import base64
import binascii
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import ImageContent


def export_image(
    image: "ImageContent",
    mode: str,
    output_dir: Path | None = None,
    counter: int = 0,
) -> str | None:
    """Export image content and return the source URL/path.

    This is a format-agnostic function that handles image export logic
    and returns just the src. Callers format the result as HTML or Markdown.

    Args:
        image: ImageContent with base64-encoded image data
        mode: Export mode - "placeholder", "embedded", or "referenced"
        output_dir: Output directory for referenced images (required for "referenced" mode)
        counter: Image counter for generating unique filenames

    Returns:
        For "placeholder" mode: None (caller should render placeholder text)
        For "embedded" mode: data URL (e.g., "data:image/png;base64,...")
        For "referenced" mode: relative path (e.g., "images/image_0001.png")
        For unsupported mode: None
    """
    if mode == "placeholder":
        return None

    if mode == "embedded":
        return f"data:{image.source.media_type};base64,{image.source.data}"

    if mode == "referenced":
        if output_dir is None:
            return None

        try:
            # Create images subdirectory
            images_dir = output_dir / "images"
            images_dir.mkdir(exist_ok=True)

            # Generate filename based on media type
            ext = _get_extension(image.source.media_type)
            filename = f"image_{counter:04d}{ext}"
            filepath = images_dir / filename

            # Decode and write image
            image_data = base64.b64decode(image.source.data)
            filepath.write_bytes(image_data)

            return f"images/{filename}"
        except (OSError, binascii.Error, ValueError):
            # Graceful degradation: return None to trigger placeholder rendering
            # Covers: PermissionError (mkdir/write), disk full, malformed base64
            return None

    # Unsupported mode
    return None


def _get_extension(media_type: str) -> str:
    """Get file extension from media type."""
    ext_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }
    return ext_map.get(media_type, ".png")
