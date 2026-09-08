"""Render the Markdown assignment report as a print-ready PDF."""

from __future__ import annotations

import argparse
from pathlib import Path

import markdown
from weasyprint import HTML


STYLES = r"""
@page {
  size: Letter;
  margin: 0.82in 0.78in 0.78in;

  @top-right {
    content: "CS 422 · Assignment 1";
    color: #667085;
    font-family: "DejaVu Sans", sans-serif;
    font-size: 8pt;
  }

  @bottom-center {
    content: "Page " counter(page) " of " counter(pages);
    color: #667085;
    font-family: "DejaVu Sans", sans-serif;
    font-size: 8pt;
  }
}

@page :first {
  @top-right { content: none; }
}

html {
  color: #1f2937;
  font-family: "DejaVu Serif", serif;
  font-size: 11pt;
  line-height: 1.52;
}

body { margin: 0; }

h1, h2, h3, h4 {
  color: #172554;
  font-family: "DejaVu Sans", sans-serif;
  break-after: avoid;
  line-height: 1.2;
}

h1 {
  border-bottom: 2px solid #1d4ed8;
  font-size: 21pt;
  margin: 0 0 17pt;
  padding: 0 0 11pt;
  text-align: center;
}

h2 {
  border-bottom: 1px solid #bfdbfe;
  font-size: 15.5pt;
  margin: 20pt 0 9pt;
  padding-bottom: 4pt;
}

h3 { font-size: 12.5pt; margin: 16pt 0 6pt; }
h4 { font-size: 11pt; margin: 12pt 0 5pt; }

p { margin: 0 0 9pt; orphans: 3; widows: 3; }
strong { color: #111827; }

a {
  color: #1d4ed8;
  text-decoration: none;
  overflow-wrap: anywhere;
}

hr {
  border: 0;
  border-top: 1px solid #cbd5e1;
  margin: 14pt 0;
}

table {
  border-collapse: collapse;
  font-family: "DejaVu Sans", sans-serif;
  font-size: 8.5pt;
  line-height: 1.38;
  margin: 9pt 0 13pt;
  width: 100%;
}

thead { display: table-header-group; }
tr { break-inside: avoid; }

th {
  background: #dbeafe;
  color: #172554;
  font-weight: 700;
  text-align: left;
}

th, td {
  border: 0.6pt solid #94a3b8;
  padding: 5pt 6pt;
  vertical-align: top;
  overflow-wrap: anywhere;
}

tbody tr:nth-child(even) { background: #f8fafc; }

code {
  background: #f1f5f9;
  border-radius: 2pt;
  font-family: "DejaVu Sans Mono", monospace;
  font-size: 9pt;
  padding: 0.5pt 2pt;
}

pre {
  background: #f1f5f9;
  border-left: 3pt solid #3b82f6;
  break-inside: avoid;
  line-height: 1.42;
  margin: 9pt 0 12pt;
  padding: 8pt 10pt;
  white-space: pre-wrap;
}

pre code { background: transparent; padding: 0; }

ul, ol { margin: 5pt 0 10pt 20pt; padding: 0; }
li { margin-bottom: 4pt; }

img {
  display: block;
  height: auto;
  margin: 9pt auto 5pt;
  max-height: 5.9in;
  max-width: 100%;
  object-fit: contain;
}

blockquote {
  border-left: 3pt solid #60a5fa;
  color: #475569;
  margin: 8pt 0;
  padding: 2pt 10pt;
}
"""


def render(source: Path, output: Path) -> None:
    body = markdown.markdown(
        source.read_text(encoding="utf-8"),
        extensions=["extra", "sane_lists"],
        output_format="html5",
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="author" content="CS 422 Assignment 1 group">
  <title>CS 422 Assignment 1 — Network Latencies, Ping &amp; Traceroute</title>
  <style>{STYLES}</style>
</head>
<body>{body}</body>
</html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=document, base_url=str(source.parent.resolve())).write_pdf(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, default=Path("report.md"))
    parser.add_argument("-o", "--output", type=Path, default=Path("report.pdf"))
    args = parser.parse_args()
    render(args.source.resolve(), args.output.resolve())
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
