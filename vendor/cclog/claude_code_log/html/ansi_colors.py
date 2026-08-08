#!/usr/bin/env python3
"""ANSI escape code to HTML conversion.

This module provides utilities for converting terminal ANSI escape codes
to HTML with appropriate CSS classes for styling.
"""

import html
import re
from typing import Any


def _escape_html(text: str) -> str:
    """Escape HTML special characters in text.

    Also normalizes line endings (CRLF -> LF) to prevent double spacing in <pre> blocks.
    """
    # Normalize CRLF to LF to prevent double line breaks in HTML
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return html.escape(normalized)


def convert_ansi_to_html(text: str) -> str:
    """Convert ANSI escape codes to HTML spans with CSS classes.

    Supports:
    - Colors (30-37, 90-97 for foreground; 40-47, 100-107 for background)
    - RGB colors (38;2;r;g;b for foreground; 48;2;r;g;b for background)
    - Bold (1), Dim (2), Italic (3), Underline (4)
    - Reset (0, 39, 49, 22, 23, 24)
    - Strips cursor movement and screen manipulation codes
    """
    # First, strip cursor movement and screen manipulation codes
    # Common patterns: [1A (cursor up), [2K (erase line), [?25l (hide cursor), etc.
    cursor_patterns = [
        r"\x1b\[[0-9]*[ABCD]",  # Cursor movement (up, down, forward, back)
        r"\x1b\[[0-9]*[EF]",  # Cursor next/previous line
        r"\x1b\[[0-9]*[GH]",  # Cursor horizontal/home position
        r"\x1b\[[0-9;]*[Hf]",  # Cursor position
        r"\x1b\[[0-9]*[JK]",  # Erase display/line
        r"\x1b\[[0-9]*[ST]",  # Scroll up/down
        r"\x1b\[\?[0-9]*[hl]",  # Private mode set/reset (show/hide cursor, etc.)
        r"\x1b\[[0-9]*[PXYZ@]",  # Insert/delete operations
        r"\x1b\[=[0-9]*[A-Za-z]",  # Alternate character set
        r"\x1b\][0-9];[^\x07]*\x07",  # Operating System Command (OSC)
        r"\x1b\][0-9];[^\x1b]*\x1b\\",  # OSC with string terminator
    ]

    # Strip all cursor movement and screen manipulation codes
    for pattern in cursor_patterns:
        text = re.sub(pattern, "", text)

    # Also strip any remaining unhandled escape sequences that aren't color codes
    # This catches any we might have missed, but preserves \x1b[...m color codes
    text = re.sub(r"\x1b\[(?![0-9;]*m)[0-9;]*[A-Za-z]", "", text)

    result: list[str] = []
    segments: list[dict[str, Any]] = []

    # First pass: split text into segments with their styles
    last_end = 0
    current_fg = None
    current_bg = None
    current_bold = False
    current_dim = False
    current_italic = False
    current_underline = False
    current_rgb_fg = None
    current_rgb_bg = None

    for match in re.finditer(r"\x1b\[([0-9;]*)m", text):
        # Add text before this escape code
        if match.start() > last_end:
            segments.append(
                {
                    "text": text[last_end : match.start()],
                    "fg": current_fg,
                    "bg": current_bg,
                    "bold": current_bold,
                    "dim": current_dim,
                    "italic": current_italic,
                    "underline": current_underline,
                    "rgb_fg": current_rgb_fg,
                    "rgb_bg": current_rgb_bg,
                }
            )

        # Process escape codes (empty params = reset, same as code 0)
        code_blob = match.group(1)
        codes = code_blob.split(";") if code_blob else ["0"]
        if codes == [""]:
            codes = ["0"]
        i = 0
        while i < len(codes):
            code = codes[i]

            # Reset codes
            if code == "0":
                current_fg = None
                current_bg = None
                current_bold = False
                current_dim = False
                current_italic = False
                current_underline = False
                current_rgb_fg = None
                current_rgb_bg = None
            elif code == "39":
                current_fg = None
                current_rgb_fg = None
            elif code == "49":
                current_bg = None
                current_rgb_bg = None
            elif code == "22":
                current_bold = False
                current_dim = False
            elif code == "23":
                current_italic = False
            elif code == "24":
                current_underline = False

            # Style codes
            elif code == "1":
                current_bold = True
            elif code == "2":
                current_dim = True
            elif code == "3":
                current_italic = True
            elif code == "4":
                current_underline = True

            # Standard foreground colors
            elif code in ["30", "31", "32", "33", "34", "35", "36", "37"]:
                color_map = {
                    "30": "black",
                    "31": "red",
                    "32": "green",
                    "33": "yellow",
                    "34": "blue",
                    "35": "magenta",
                    "36": "cyan",
                    "37": "white",
                }
                current_fg = f"ansi-{color_map[code]}"
                current_rgb_fg = None

            # Standard background colors
            elif code in ["40", "41", "42", "43", "44", "45", "46", "47"]:
                color_map = {
                    "40": "black",
                    "41": "red",
                    "42": "green",
                    "43": "yellow",
                    "44": "blue",
                    "45": "magenta",
                    "46": "cyan",
                    "47": "white",
                }
                current_bg = f"ansi-bg-{color_map[code]}"
                current_rgb_bg = None

            # Bright foreground colors
            elif code in ["90", "91", "92", "93", "94", "95", "96", "97"]:
                color_map = {
                    "90": "bright-black",
                    "91": "bright-red",
                    "92": "bright-green",
                    "93": "bright-yellow",
                    "94": "bright-blue",
                    "95": "bright-magenta",
                    "96": "bright-cyan",
                    "97": "bright-white",
                }
                current_fg = f"ansi-{color_map[code]}"
                current_rgb_fg = None

            # Bright background colors
            elif code in ["100", "101", "102", "103", "104", "105", "106", "107"]:
                color_map = {
                    "100": "bright-black",
                    "101": "bright-red",
                    "102": "bright-green",
                    "103": "bright-yellow",
                    "104": "bright-blue",
                    "105": "bright-magenta",
                    "106": "bright-cyan",
                    "107": "bright-white",
                }
                current_bg = f"ansi-bg-{color_map[code]}"
                current_rgb_bg = None

            # RGB foreground color
            elif code == "38" and i + 1 < len(codes) and codes[i + 1] == "2":
                if i + 4 < len(codes):
                    r, g, b = codes[i + 2], codes[i + 3], codes[i + 4]
                    # Validate RGB values are numeric to avoid invalid CSS
                    if r.isdigit() and g.isdigit() and b.isdigit():
                        current_rgb_fg = f"color: rgb({r}, {g}, {b})"
                        current_fg = None
                    i += 4

            # RGB background color
            elif code == "48" and i + 1 < len(codes) and codes[i + 1] == "2":
                if i + 4 < len(codes):
                    r, g, b = codes[i + 2], codes[i + 3], codes[i + 4]
                    # Validate RGB values are numeric to avoid invalid CSS
                    if r.isdigit() and g.isdigit() and b.isdigit():
                        current_rgb_bg = f"background-color: rgb({r}, {g}, {b})"
                        current_bg = None
                    i += 4

            i += 1

        last_end = match.end()

    # Add remaining text
    if last_end < len(text):
        segments.append(
            {
                "text": text[last_end:],
                "fg": current_fg,
                "bg": current_bg,
                "bold": current_bold,
                "dim": current_dim,
                "italic": current_italic,
                "underline": current_underline,
                "rgb_fg": current_rgb_fg,
                "rgb_bg": current_rgb_bg,
            }
        )

    # Second pass: build HTML
    for segment in segments:
        if not segment["text"]:
            continue

        classes: list[str] = []
        styles: list[str] = []

        if segment["fg"]:
            classes.append(segment["fg"])
        if segment["bg"]:
            classes.append(segment["bg"])
        if segment["bold"]:
            classes.append("ansi-bold")
        if segment["dim"]:
            classes.append("ansi-dim")
        if segment["italic"]:
            classes.append("ansi-italic")
        if segment["underline"]:
            classes.append("ansi-underline")
        if segment["rgb_fg"]:
            styles.append(segment["rgb_fg"])
        if segment["rgb_bg"]:
            styles.append(segment["rgb_bg"])

        escaped_text = _escape_html(segment["text"])

        if classes or styles:
            attrs: list[str] = []
            if classes:
                attrs.append(f'class="{" ".join(classes)}"')
            if styles:
                attrs.append(f'style="{"; ".join(styles)}"')
            result.append(f"<span {' '.join(attrs)}>{escaped_text}</span>")
        else:
            result.append(escaped_text)

    return "".join(result)
