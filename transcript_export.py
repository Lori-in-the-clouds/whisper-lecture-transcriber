"""Serialize completed transcripts without changing their live plain-text content."""

from collections.abc import Sequence
from html import escape


OUTPUT_MIME_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
}


def render_transcript(texts: Sequence[str], output_format: str = "txt", *,
                      merged: bool = False, title: str = "Transcript",
                      language: str = "auto") -> str:
    if output_format not in OUTPUT_MIME_TYPES:
        raise ValueError("Unsupported output format")
    texts = [text.strip() for text in texts]
    if output_format in {"txt", "md"}:
        sections = []
        for index, text in enumerate(texts, 1):
            heading = ""
            if merged:
                heading = (f"# Part {index}\n\n" if output_format == "md" else
                           f"-----------------\nPart {index}\n-----------------\n\n")
            sections.append(heading + text)
        return "\n\n".join(sections) + "\n"

    sections = []
    for index, text in enumerate(texts, 1):
        heading = f"<h1>Part {index}</h1>\n" if merged else ""
        sections.append(f'<section>\n{heading}<div class="transcript">{escape(text)}</div>\n</section>')
    lang = f' lang="{escape(language, quote=True)}"' if language != "auto" else ""
    content = "\n".join(sections)
    return f'''<!doctype html>
<html{lang}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
body {{ max-width: 800px; margin: 48px auto; padding: 0 24px; color: #1d1d1f;
       background: #fff; font: 17px/1.7 system-ui, sans-serif; }}
section + section {{ margin-top: 48px; }}
h1 {{ font-size: 1.7em; line-height: 1.2; break-after: avoid; }}
.transcript {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
@media print {{ body {{ max-width: none; margin: 0; padding: 0; font-size: 12pt; }} }}
</style>
</head>
<body>
{content}
</body>
</html>
'''
