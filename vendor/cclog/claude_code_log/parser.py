#!/usr/bin/env python3
"""Parse and extract data from Claude transcript JSONL files.

This module provides utility functions for parsing transcript data:
- extract_text_content: Extract text from content items
- parse_timestamp: Parse ISO timestamps

For transcript entry and content item creation, see factories/.
"""

from datetime import datetime
from typing import Optional

from .models import ContentItem, TextContent


def extract_text_content(content: Optional[list[ContentItem]]) -> str:
    """Extract text content from Claude message content structure."""
    if not content:
        return ""
    return "\n".join(item.text for item in content if isinstance(item, TextContent))


def parse_timestamp(timestamp_str: str) -> Optional[datetime]:
    """Parse ISO timestamp to datetime object."""
    try:
        return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
