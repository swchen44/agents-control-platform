"""Factory for assistant transcript entries.

This module handles creation of AssistantTranscriptEntry content into MessageContent
subclasses:
- AssistantTextMessage: Claude's text responses
- ThinkingMessage: Extended thinking blocks
"""

from typing import Optional

from ..models import (
    AssistantTextMessage,
    ContentItem,
    MessageMeta,
    ThinkingContent,
    ThinkingMessage,
    UsageInfo,
)


# =============================================================================
# Token Usage Formatting
# =============================================================================


def format_token_usage(usage: UsageInfo) -> str:
    """Format token usage information as a display string.

    Args:
        usage: UsageInfo object with token counts.

    Returns:
        Formatted string like "Input: 100 | Output: 50 | Cache Read: 25"
    """
    token_parts = [
        f"Input: {usage.input_tokens}",
        f"Output: {usage.output_tokens}",
    ]
    if usage.cache_creation_input_tokens:
        token_parts.append(f"Cache Creation: {usage.cache_creation_input_tokens}")
    if usage.cache_read_input_tokens:
        token_parts.append(f"Cache Read: {usage.cache_read_input_tokens}")
    return " | ".join(token_parts)


# =============================================================================
# Message Creation Functions
# =============================================================================


def create_assistant_message(
    meta: MessageMeta,
    items: list[ContentItem],
    usage: Optional[UsageInfo] = None,
) -> Optional[AssistantTextMessage]:
    """Create AssistantTextMessage from content items.

    Creates AssistantTextMessage from text/image content items.

    Args:
        meta: Message metadata.
        items: List of text/image content items (no tool_use, tool_result, thinking).
        usage: Optional token usage info to format and attach.

    Returns:
        AssistantTextMessage if items is non-empty, None otherwise.
    """
    # Create AssistantTextMessage directly from items
    # (empty text already filtered by chunk_message_content)
    if items:
        return AssistantTextMessage(
            meta,
            items=items,  # type: ignore[arg-type]
            token_usage=format_token_usage(usage) if usage else None,
        )
    return None


def create_thinking_message(
    meta: MessageMeta,
    thinking: ThinkingContent,
    usage: Optional[UsageInfo] = None,
) -> ThinkingMessage:
    """Create ThinkingMessage from ThinkingContent.

    Args:
        meta: Message metadata.
        thinking: ThinkingContent with thinking text and optional signature.
        usage: Optional token usage info to format and attach.

    Returns:
        ThinkingMessage containing the thinking text and optional signature.
    """
    return ThinkingMessage(
        meta,
        thinking=thinking.thinking.strip(),
        signature=thinking.signature,
        token_usage=format_token_usage(usage) if usage else None,
    )
