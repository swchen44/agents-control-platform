"""mistune inline-parser plugins shared between the HTML and Markdown
output paths.

Houses two related plugins (issue #156):

- ``make_sha_plugin`` — turns plain ``7c2e6f6``-shaped tokens into
  commit links when a caller-supplied resolver returns a URL.
- ``make_codespan_sha_plugin`` — wraps ``​`5baac35`​`` codespans
  in commit links, emitting ``<a href="…"><code>5baac35</code></a>``
  so explicit codespan-quoted SHAs become links too.

Both plugins are no-ops for SHAs the resolver can't map to a URL
(typical of in-flight local-only commits), so the rendered transcript
doesn't sprout broken links.

## Why a separate module

Both ``html/utils.py`` (the HTML mistune pipelines) and
``markdown/renderer.py`` (the Markdown output's tag-protecting
mistune pipeline) need to register the same plugin. Keeping the
factory here avoids the cross-import that would otherwise be needed.

## Why an inline-parser plugin (not a renderer monkey-patch)

The in-project ``_create_pygments_plugin`` precedent in
``html/utils.py`` monkey-patches ``md.renderer.block_code`` — that's
the right shape for *block*-level transformations. SHA detection is
*inline* (it has to fire mid-paragraph, inside ``*…*`` and ``**…**``,
but not inside ``` `…` ``` or fenced code), and mistune's inline
parser already provides exactly that surface via
``md.inline.register``. See
https://mistune.lepture.com/en/latest/advanced.html#create-plugins.

## Word-boundary heuristic

The default regex ``r"\\b[0-9a-f]{7,40}\\b"`` matches 7-to-40-char
lowercase hex runs at word boundaries. False-positive shapes worth
noting:

- ``0xdeadbeef`` style hex literals — the leading ``0x`` is consumed
  by ``\\b`` so the ``deadbeef`` portion does match. The resolver
  rejects unreachable SHAs, so these render as plain text in
  practice.
- 7+ char bash variable names that happen to be all hex would match
  syntactically but again, the resolver gate prevents bogus links.

The conservative pattern is fine for now; tighten if real-world
false-positive volume becomes an issue.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional


# Word-bounded run of 7-40 lowercase hex chars. Mirrors the standard
# git short-SHA shape (``git config --global core.abbrev`` defaults to
# 7); 40 is the full SHA-1 length. The resolver gate filters
# false-positives that this loose pattern lets through.
SHA_PATTERN = r"\b[0-9a-f]{7,40}\b"


# Tight ``\`sha\``` shape for the codespan-wrapped variant. Single
# backticks only: the realistic transcript form. Multi-backtick
# codespans wrapping a bare SHA (``\`\`sha\`\``` etc.) and the
# CommonMark strip-one-space form (``\` sha \``) are not handled —
# extend with explicit alternation if real-world data warrants it.
# We deliberately avoid backreferences here: mistune concatenates
# rule patterns and renumbers groups, so ``\1`` would refer to a
# different (and surprising) group at parse time.
CODESPAN_SHA_PATTERN = r"`[0-9a-f]{7,40}`"


def make_sha_plugin(resolve: Callable[[str], Optional[str]]) -> Any:
    """Build a mistune plugin that links resolvable git commit SHAs.

    The plugin emits a stock ``"link"`` token, so it works with both
    ``mistune.HTMLRenderer`` and the project's ``MarkdownRenderer``
    without per-renderer registration. ``resolve`` is the only
    customisation point — it returns the URL to link to, or ``None``
    to leave the SHA unchanged. Wrap it in ``functools.lru_cache``
    upstream; ``parse_sha_link`` calls it once per match.

    Args:
        resolve: callable mapping a candidate SHA to a target URL or
            ``None`` for "leave as plain text".

    Returns:
        A function suitable for the ``plugins=[...]`` list passed to
        ``mistune.create_markdown``.
    """

    def parse_sha_link(inline: Any, m: re.Match[str], state: Any) -> int:
        sha = m.group(0)
        pos = m.end()
        # Don't nest links: if we're already inside a [text](url)
        # token, drop straight through to plain-text emission. Mirrors
        # the guard in mistune's bundled ``url`` plugin.
        if getattr(state, "in_link", False):
            inline.process_text(sha, state)
            return pos
        url = resolve(sha)
        if url is None:
            # Resolver said "no" (no remote, not reachable, etc.) —
            # render as plain text exactly as it appeared.
            inline.process_text(sha, state)
            return pos
        state.append_token(
            {
                "type": "link",
                "children": [{"type": "text", "raw": sha}],
                "attrs": {"url": url},
            }
        )
        return pos

    def plugin(md: Any) -> None:
        # The outer ``Any`` return type on ``make_sha_plugin`` is a
        # deliberate hand-off to mistune's ``PluginRef`` interface,
        # which expects a positional-or-keyword ``md`` parameter.
        # Typing this closure more tightly produces a parameter-kind
        # mismatch with strict checkers (pyright/ty) at the
        # ``create_markdown(plugins=[…])`` call site.
        #
        # ``register`` appends to ``DEFAULT_RULES``, so built-in
        # inline rules (``link``, ``auto_link``, ``codespan``, …) win
        # on overlap. That's what we want: an explicit
        # ``[abc1234](url)`` stays a single link, and a SHA inside
        # ``` `…` ``` stays code (handled by the *separate*
        # ``make_codespan_sha_plugin`` below, which fires *before*
        # the built-in ``codespan`` rule).
        md.inline.register("sha_link", SHA_PATTERN, parse_sha_link)

    return plugin


def make_codespan_sha_plugin(resolve: Callable[[str], Optional[str]]) -> Any:
    """Build a mistune plugin that links codespan-wrapped SHAs.

    Where ``make_sha_plugin`` handles bare prose tokens (``abc1234``),
    this plugin handles the explicit codespan form (``​`abc1234`​``)
    that authors often use to typographically distinguish commit
    references. The emitted token is a stock ``link`` wrapping a
    stock ``codespan`` child, so the rendered HTML is
    ``<a href="…"><code>abc1234</code></a>`` — the SHA stays
    monospaced and gains the link.

    Must fire *before* mistune's built-in ``codespan`` rule
    (``register(…, before="codespan")``); otherwise the default
    codespan consumes the input first and our plugin never sees it.

    Args:
        resolve: same contract as ``make_sha_plugin``.
    """

    def parse_codespan_sha(inline: Any, m: re.Match[str], state: Any) -> int:
        # mistune concatenates rule patterns into one combined regex
        # and renumbers capturing groups, so ``m.group(N)`` for N>0
        # is unreliable. ``m.group(0)`` always holds the full literal
        # match (here: ``​`abc1234`​``).
        raw = m.group(0)
        sha = raw.strip("`")
        pos = m.end()
        if getattr(state, "in_link", False):
            # Already inside ``[…](url)``: don't double-wrap, but
            # preserve the codespan formatting so
            # ``​[`abc1234`](url)​`` still renders as
            # ``<a><code>abc1234</code></a>``. Falling through with
            # ``return None`` would skip 1 char and lose the codespan
            # tagging that mistune's default would have emitted.
            state.append_token({"type": "codespan", "raw": sha})
            return pos
        url = resolve(sha)
        if url is None:
            # Resolver said "no": emit the unchanged codespan, exactly
            # as mistune's default would.
            state.append_token({"type": "codespan", "raw": sha})
            return pos
        state.append_token(
            {
                "type": "link",
                "attrs": {"url": url},
                "children": [{"type": "codespan", "raw": sha}],
            }
        )
        return pos

    def plugin(md: Any) -> None:
        # ``before="codespan"`` is load-bearing: mistune's built-in
        # codespan rule is greedy on ``​`…`​`` shapes and
        # would consume our input first if we registered after it.
        md.inline.register(
            "codespan_sha",
            CODESPAN_SHA_PATTERN,
            parse_codespan_sha,
            before="codespan",
        )

    return plugin


def linkify_shas_in_text(text: str, resolve: Callable[[str], Optional[str]]) -> str:
    """Substitute resolvable SHAs in ``text`` with Markdown links.

    Used by the Markdown output path where text bodies are emitted
    directly (no mistune render) — e.g.
    ``MarkdownRenderer.format_AssistantTextMessage``. Mirrors the
    HTML side's plugin behaviour:

    - SHAs inside fenced code blocks (``` ``` ``` / ``~~~``) are skipped.
    - SHAs inside indented code blocks (4-space / tab leading) are skipped.
    - SHAs *embedded* inside inline code spans alongside other text
      (e.g. ``` `git show abc1234` ```) are skipped — we won't rewrite
      arbitrary code fragments. *Single-backtick* codespans whose body
      is exactly a SHA (e.g. ``` `abc1234` ```) get the codespan
      preserved and wrapped in a link: ``[`abc1234`](url)``. Mirrors
      ``make_codespan_sha_plugin`` on the HTML side.
    - SHAs inside an existing Markdown link's ``[text]`` or ``(url)``
      span are skipped — so an already-linked SHA isn't double-wrapped.
    - Everything else: resolver-confirmed SHAs become ``[sha](url)``;
      unresolved SHAs pass through verbatim.

    Implementation is a hand-rolled tokenizer rather than a mistune
    pass: it has to *preserve* unmatched delimiters and rebuild the
    text exactly, which mistune's HTML-emitting renderer won't do.
    The same predicate set the HTML plugin gets for free
    (``parse_emphasis`` recursing into nested rules, ``codespan``
    being raw) we have to enforce explicitly here.
    """
    if not text:
        return text
    return "".join(_linkify_block_tokens(text, resolve))


def _replace_shas(text: str, resolve: Callable[[str], Optional[str]]) -> str:
    """Apply ``SHA_PATTERN`` substitution to a prose span."""

    def _sub(m: re.Match[str]) -> str:
        sha = m.group(0)
        url = resolve(sha)
        if url is None:
            return sha
        return f"[{sha}]({url})"

    return re.sub(SHA_PATTERN, _sub, text)


def _linkify_block_tokens(
    text: str, resolve: Callable[[str], Optional[str]]
) -> list[str]:
    """Walk *text* line-by-line, yielding prose-substituted / code-skipped pieces.

    Handles block-level skips (fenced + indented code); per-prose-line
    inline tokenization is delegated to ``_linkify_inline``.
    """
    out: list[str] = []
    lines = text.split("\n")
    in_fence = False
    fence_marker: str = ""  # ``` or ~~~ run that opened the current fence
    for idx, line in enumerate(lines):
        # Newline rejoin: every line except the last gets its trailing
        # ``\n`` re-emitted as a separate token.
        suffix = "\n" if idx < len(lines) - 1 else ""

        stripped_left = line.lstrip(" ")
        indent = len(line) - len(stripped_left)

        # CommonMark allows up to 3 leading spaces before a fence; ≥4
        # leading spaces is an indented-code line, not a fence.
        if in_fence:
            # Close on matching fence (run of same char, ≥ opener length).
            if (
                indent <= 3
                and stripped_left.startswith(fence_marker)
                and stripped_left.rstrip().rstrip(fence_marker[0]) == ""
            ):
                in_fence = False
                fence_marker = ""
            out.append(line + suffix)
            continue

        if indent <= 3 and (
            stripped_left.startswith("```") or stripped_left.startswith("~~~")
        ):
            ch = stripped_left[0]
            run = 0
            while run < len(stripped_left) and stripped_left[run] == ch:
                run += 1
            in_fence = True
            fence_marker = ch * run
            out.append(line + suffix)
            continue

        if indent >= 4:
            # Indented code block: skip substitution for this line.
            out.append(line + suffix)
            continue

        out.append(_linkify_inline(line, resolve) + suffix)
    return out


def _linkify_inline(line: str, resolve: Callable[[str], Optional[str]]) -> str:
    """Apply SHA substitution within a single prose line.

    Spans owned by an existing Markdown link (``[text](url)``) are
    opaque. Matched backtick runs are opaque *unless* their body is
    exactly a resolvable SHA and the opener is a single backtick, in
    which case the span ``​`abc1234`​`` is rewritten to
    ``[​`abc1234`​](url)`` — mirroring the
    ``make_codespan_sha_plugin`` behaviour on the HTML side. Anything
    else is treated as prose and passed through ``_replace_shas``.
    """
    parts: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == "`":
            # Match opening run length.
            j = i
            while j < n and line[j] == "`":
                j += 1
            open_len = j - i
            close = _find_matching_backticks(line, j, open_len)
            if close is not None:
                span = line[i : close + open_len]
                # Special-case: single-backtick codespan whose body
                # is exactly a resolvable SHA → wrap the codespan in
                # a link so [`abc1234`](url) renders as a monospaced
                # commit link. Multi-backtick spans, spans with
                # surrounding whitespace, and any non-SHA body fall
                # through to the opaque emit.
                if open_len == 1:
                    body = line[j:close]
                    if re.fullmatch(SHA_PATTERN, body) is not None:
                        url = resolve(body)
                        if url is not None:
                            parts.append(f"[{span}]({url})")
                            i = close + open_len
                            continue
                # Whole span (including the closing run) is opaque.
                parts.append(span)
                i = close + open_len
                continue
            # Unmatched run: treat as plain prose chars.
            parts.append(_replace_shas(line[i:j], resolve))
            i = j
            continue
        if ch == "[":
            link_end = _try_match_md_link(line, i)
            if link_end is not None:
                # Whole [text](url) span is opaque — both halves are
                # already either user text we mustn't double-tag or a
                # link target the resolver shouldn't rewrite.
                parts.append(line[i:link_end])
                i = link_end
                continue
            # ``[`` that doesn't open a link is a literal prose char;
            # emit it directly and advance, otherwise the prose
            # accumulator below would stop on the same ``[`` and spin.
            parts.append("[")
            i += 1
            continue
        # Accumulate prose until the next significant delimiter.
        k = i
        while k < n and line[k] != "`" and line[k] != "[":
            k += 1
        parts.append(_replace_shas(line[i:k], resolve))
        i = k
    return "".join(parts)


def _find_matching_backticks(text: str, start: int, count: int) -> Optional[int]:
    """Find a run of exactly ``count`` backticks at or after ``start``.

    Returns the start index of the closing run, or ``None`` if no
    matching run exists in the rest of the line.
    """
    i = start
    n = len(text)
    while i < n:
        if text[i] != "`":
            i += 1
            continue
        j = i
        while j < n and text[j] == "`":
            j += 1
        if j - i == count:
            return i
        i = j
    return None


def _try_match_md_link(text: str, start: int) -> Optional[int]:
    """Try to match a Markdown ``[text](url)`` link starting at ``start``.

    Returns the index just past the closing ``)``, or ``None`` if the
    span doesn't form a valid link. Doesn't try to handle reference
    links / footnotes / images-with-titles — those are uncommon in the
    prose this helper sees (assistant / user message bodies). The
    inline image shape ``![alt](url)`` is matched as a link starting
    at the ``[`` (the leading ``!`` is in the surrounding prose
    chunk), which is the same opaque outcome we want.
    """
    if start >= len(text) or text[start] != "[":
        return None
    n = len(text)
    i = start + 1
    depth = 1
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if depth != 0 or i + 1 >= n or text[i + 1] != "(":
        return None
    j = i + 2
    paren = 1
    while j < n:
        c = text[j]
        if c == "\\" and j + 1 < n:
            j += 2
            continue
        if c == "(":
            paren += 1
        elif c == ")":
            paren -= 1
            if paren == 0:
                return j + 1
        j += 1
    return None
