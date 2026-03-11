import html
import re

_FENCED_CODE_RE = re.compile(r"```(?:[^\n`]*)\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def format_for_telegram(text: str) -> str:
    placeholders: dict[str, str] = {}

    def store(replacement: str) -> str:
        token = f"@@TG{len(placeholders)}@@"
        placeholders[token] = replacement
        return token

    def replace_fenced(match: re.Match[str]) -> str:
        code = html.escape(match.group(1).strip("\n"))
        return store(f"<pre><code>{code}</code></pre>")

    def replace_inline(match: re.Match[str]) -> str:
        code = html.escape(match.group(1))
        return store(f"<code>{code}</code>")

    text = text.replace("\r\n", "\n")
    text = _FENCED_CODE_RE.sub(replace_fenced, text)
    text = _INLINE_CODE_RE.sub(replace_inline, text)
    text = html.escape(text)
    text = _LINK_RE.sub(
        lambda match: (
            f'<a href="{html.escape(match.group(2), quote=True)}">{match.group(1)}</a>'
        ),
        text,
    )
    text = _BOLD_RE.sub(lambda match: f"<b>{match.group(1)}</b>", text)

    for token, replacement in placeholders.items():
        text = text.replace(token, replacement)

    return text
