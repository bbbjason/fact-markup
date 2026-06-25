#!/usr/bin/env python3
"""Fetch, clean, and fact-mark Markdown articles.

This is the GitHub Actions version of the local fact-markup skill. It keeps the
same artifact contract: a cleaned Markdown source, a sibling `.judgments.jsonl`,
and a sibling `.fact-marked.md`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


BOILERPLATE_PATTERNS = [
    r"\bSPONSORED\b",
    r"\bPhoto credit\b",
    r"\bCopyright\b",
    r"\bAll rights reserved\b",
    r"\u8a02\u95b1",
    r"\u767b\u5165",
    r"\u5206\u4eab",
    r"\u63a8\u85a6\u6587\u7ae0",
    r"\u76f8\u95dc\u6587\u7ae0",
    r"\u5ee3\u544a",
]
LONG_CLAUSE_CHARS = 42
CLAUSE_SPLIT_RE = re.compile(r"([\uff0c\uff1b\uff1a])")
SENTENCE_SPLIT_RE = re.compile(r"([\u3002\uff01\uff1f!?])")


def is_heading_or_structural(line: str) -> bool:
    return line.startswith(("#", ">", "```"))


def is_metadata_or_footer(line: str) -> bool:
    text = line.strip()
    if re.match(r"^-\s*\d{4}/\d{2}/\d{2}$", text):
        return True
    if re.match(r"^\u6838\u7a3f\u7de8\u8f2f[:\uff1a]", text):
        return True
    if text.startswith("\u672c\u6587\u521d\u7a3f\u7531 INSIDE"):
        return True
    if "\u9ede\u64ca\u7acb\u523b\u6210\u70ba\u6703\u54e1" in text:
        return True
    if text in {"\u5ef6\u4f38\u95b1\u8b80\uff1a", "\u76f8\u95dc\u6587\u7ae0"}:
        return True
    return False


def is_tag_list(line: str, output: list[str]) -> bool:
    text = line.strip()
    if len(output) > 6:
        return False
    if not text.startswith("- "):
        return False
    if re.search(r"[\u3002\uff01\uff1f!?]", text):
        return False
    return "\u3001" in text or " \u3001 " in text


def slugify(value: str, fallback: str = "article") -> str:
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[\\/:*?\"<>|\s]+", "-", value.strip())
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:80] or fallback


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def request_url(url: str):
    import requests

    response = requests.get(url, timeout=30, headers={"User-Agent": "fact-markup-github-action/1.0"})
    response.raise_for_status()
    return response


def fetch_url(url: str) -> tuple[str, str | None, str | None]:
    import requests
    from bs4 import BeautifulSoup

    fallback_used = False
    try:
        response = request_url(url)
    except requests.RequestException:
        fallback_used = True
        response = request_url(f"https://r.jina.ai/http://{url}")

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg", "form", "nav", "footer", "aside"]):
        tag.decompose()

    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(" ", strip=True)

    author = None
    author_meta = soup.find(attrs={"name": re.compile(r"author", re.I)})
    if author_meta and author_meta.get("content"):
        author = author_meta["content"].strip()

    main = soup.find("article") or soup.find("main") or soup.body or soup
    lines: list[str] = []
    for node in main.find_all(["h1", "h2", "h3", "p", "li"], recursive=True):
        text = node.get_text(" ", strip=True)
        if not text:
            continue
        if node.name in {"h1", "h2", "h3"}:
            level = {"h1": "#", "h2": "##", "h3": "###"}[node.name]
            lines.append(f"{level} {text}")
        elif node.name == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)

    markdown = "\n\n".join(dedupe_adjacent(lines))
    if fallback_used and not markdown:
        markdown = response.text
    return markdown, title, author


def dedupe_adjacent(lines: Iterable[str]) -> list[str]:
    result: list[str] = []
    previous = None
    for line in lines:
        normalized = re.sub(r"\s+", " ", line).strip()
        if not normalized or normalized == previous:
            continue
        result.append(normalized)
        previous = normalized
    return result


def add_front_matter(markdown: str, *, title: str | None, source: str | None, author: str | None) -> str:
    fields = {
        "title": title or "",
        "source": source or "",
        "author": author or "",
        "captured": now_iso(),
    }
    front = ["---"]
    for key, value in fields.items():
        if value:
            escaped = str(value).replace('"', '\\"')
            front.append(f'{key}: "{escaped}"')
    front.append("---")
    return "\n".join(front) + "\n\n" + markdown.strip() + "\n"


def strip_front_matter(markdown: str) -> str:
    if markdown.startswith("---\n"):
        end = markdown.find("\n---", 4)
        if end != -1:
            return markdown[end + 4 :].lstrip()
    return markdown


def is_boilerplate(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if len(text) <= 3 and not re.search(r"\d|[\u4e00-\u9fff]", text):
        return True
    return any(re.search(pattern, text, re.I) for pattern in BOILERPLATE_PATTERNS)


def split_sentences(paragraph: str) -> list[str]:
    paragraph = re.sub(r"\s+", " ", paragraph).strip()
    if not paragraph:
        return []
    if is_heading_or_structural(paragraph):
        return [paragraph]

    parts = SENTENCE_SPLIT_RE.split(paragraph)
    pieces: list[str] = []
    for idx in range(0, len(parts), 2):
        sentence = parts[idx].strip()
        if not sentence:
            continue
        if idx + 1 < len(parts):
            sentence += parts[idx + 1]
        pieces.extend(split_long_clause(sentence))
    return pieces or [paragraph]


def split_long_clause(sentence: str) -> list[str]:
    if len(sentence) <= LONG_CLAUSE_CHARS:
        return [sentence]
    chunks = CLAUSE_SPLIT_RE.split(sentence)
    pieces: list[str] = []
    current = ""
    for idx in range(0, len(chunks), 2):
        part = chunks[idx].strip()
        if not part:
            continue
        if idx + 1 < len(chunks):
            part += chunks[idx + 1]
        if not current:
            current = part
        elif len(current) < LONG_CLAUSE_CHARS // 2:
            current += part
        else:
            pieces.append(current)
            current = part
    if current:
        pieces.append(current)
    return pieces or [sentence]


def append_text_unit(output: list[str], line: str) -> None:
    if line.startswith("\u300d") and output:
        output[-1] += "\u300d"
        line = line[1:].strip()
        if not line:
            return
    output.extend(split_sentences(line))


def clean_and_normalize(markdown: str) -> tuple[list[str], int, int]:
    body = strip_front_matter(markdown)
    original_lines = body.splitlines()
    output: list[str] = []
    removed = 0
    footer_mode = False
    for raw in original_lines:
        line = raw.strip()
        if not line:
            if output and output[-1] != "":
                output.append("")
            continue
        if line in {"\u5ef6\u4f38\u95b1\u8b80\uff1a", "\u76f8\u95dc\u6587\u7ae0"}:
            footer_mode = True
        if footer_mode or is_metadata_or_footer(line) or is_tag_list(line, output) or is_boilerplate(line):
            removed += 1
            continue
        append_text_unit(output, line)

    while output and output[0] == "":
        output.pop(0)
    while output and output[-1] == "":
        output.pop()
    output = collapse_blank_lines(output)
    return output, len(original_lines), removed


def collapse_blank_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        if line == "" and (not result or result[-1] == ""):
            continue
        result.append(line)
    while result and result[-1] == "":
        result.pop()
    return result


def classify_units(client, model: str, units: list[tuple[int, str]]) -> list[dict]:
    if not units:
        return []

    prompt = (
        "\u4f60\u662f\u6587\u7ae0 fact-markup \u5be9\u6838\u54e1\u3002"
        "\u8acb\u53ea\u8f38\u51fa JSONL\uff0c\u4e0d\u8981\u8f38\u51fa Markdown \u6216\u89e3\u91cb\u3002\n"
        "\u5c0d\u6bcf\u500b\u8f38\u5165\u55ae\u4f4d\u5224\u65b7 FACT \u6216 NONFACT\u3002"
        "FACT \u662f\u5177\u9ad4\u53ef\u67e5\u7684\u4e8b\u5be6\u63cf\u8ff0\uff1b"
        "NONFACT \u662f\u4fee\u8fad\u3001\u8a55\u50f9\u3001\u63a8\u6e2c\u3001"
        "\u7b56\u7565\u89e3\u8b80\u3001\u56e0\u679c\u8a6e\u91cb\u6216\u6295\u8cc7\u5f0f\u7d50\u8ad6\u3002\n"
        "\u8f38\u5165\u5df2\u7d93\u62c6\u6210\u7d30\u7c92\u5ea6\u55ae\u4f4d\uff0c\u8acb\u9010\u884c\u5224\u65b7\u3002"
        "\u82e5\u4e00\u884c\u4ecd\u540c\u6642\u542b\u6709\u4e8b\u5be6\u8207\u8a55\u50f9\u3001\u56e0\u679c\u63a8\u8ad6\u6216\u7b56\u7565\u89e3\u8b80\uff0c"
        "\u4ee5\u8a72\u884c\u4e3b\u8981\u8a9e\u610f\u5224\u65b7\uff0c\u4e0d\u8981\u56e0\u70ba\u6709\u6578\u5b57\u6216\u516c\u53f8\u540d\u5c31\u6a19\u6210 FACT\u3002\n"
        "\u6bcf\u7b46\u5fc5\u9808\u5305\u542b line_span, line_numbers, label, text, votes\u3002"
        "votes \u6070\u597d\u4e09\u7968\uff0c\u6bcf\u7968\u5305\u542b label \u8207\u7e41\u9ad4\u4e2d\u6587 reason\u3002"
        "text \u5fc5\u9808\u9010\u5b57\u7b49\u65bc\u8f38\u5165 text\u3002\n\n"
        + "\n".join(json.dumps({"line": line_no, "text": text}, ensure_ascii=False) for line_no, text in units)
    )

    response = client.chat.complete(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    raw = strip_code_fence(response_text(response).strip())
    records: list[dict] = []
    expected = {line_no: text for line_no, text in units}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        line_numbers = record.get("line_numbers") or []
        if len(line_numbers) != 1:
            raise ValueError(f"Expected one line number per record: {record}")
        line_no = int(line_numbers[0])
        if line_no not in expected:
            raise ValueError(f"Unexpected line number from model: {line_no}")
        votes = record.get("votes") or []
        if len(votes) != 3:
            raise ValueError(f"Expected exactly three votes on line {line_no}")
        label = record.get("label")
        if label not in {"FACT", "NONFACT"}:
            raise ValueError(f"Invalid label on line {line_no}: {label}")
        record["line_span"] = str(line_no)
        record["text"] = expected[line_no]
        records.append(record)

    seen = {int(record["line_numbers"][0]) for record in records}
    missing = sorted(set(expected) - seen)
    if missing:
        raise ValueError(f"Model omitted line numbers: {missing}")
    return sorted(records, key=lambda item: int(item["line_numbers"][0]))


def classify_all(text_lines: list[str], model: str, chunk_size: int) -> list[dict]:
    from mistralai.client import Mistral

    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY is required.")

    client = Mistral(api_key=api_key)
    units = [
        (idx + 1, line)
        for idx, line in enumerate(text_lines)
        if line.strip() and not line.lstrip().startswith(("#", "```"))
    ]
    records: list[dict] = []
    for start in range(0, len(units), chunk_size):
        records.extend(classify_units(client, model, units[start : start + chunk_size]))
    return records


def response_text(response) -> str:
    content = response.choices[0].message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for chunk in content:
            if isinstance(chunk, dict):
                parts.append(str(chunk.get("text", "")))
            else:
                parts.append(str(getattr(chunk, "text", "")))
        return "".join(parts)
    return str(content)


def strip_code_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def write_outputs(source_path: Path, text_lines: list[str], judgments: list[dict]) -> tuple[Path, Path]:
    labels = {int(record["line_numbers"][0]): record["label"] for record in judgments}
    marked_lines: list[str] = []
    for idx, line in enumerate(text_lines, start=1):
        if labels.get(idx) == "NONFACT" and line.strip():
            marked_lines.append(f"~~{line}~~")
        else:
            marked_lines.append(line)

    fact_path = source_path.with_suffix(".fact-marked.md")
    judgments_path = source_path.with_suffix(".judgments.jsonl")
    fact_path.write_text("\n".join(marked_lines).rstrip() + "\n", encoding="utf-8")
    judgments_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in judgments) + "\n",
        encoding="utf-8",
    )
    return fact_path, judgments_path


def resolve_source(args: argparse.Namespace) -> Path:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.input_file:
        return Path(args.input_file)

    if not args.url:
        raise SystemExit("Provide either --url or --input-file.")

    markdown, title, author = fetch_url(args.url)
    host = urlparse(args.url).netloc.replace("www.", "")
    date = dt.date.today().isoformat()
    name = f"{date}_{slugify(host)}_{slugify(title or 'article')}.md"
    source_path = output_dir / name
    source_path.write_text(
        add_front_matter(markdown, title=title, source=args.url, author=author),
        encoding="utf-8",
    )
    return source_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create fact-marked Markdown and JSONL judgments.")
    parser.add_argument("--url", help="Article URL to fetch.")
    parser.add_argument("--input-file", help="Existing Markdown file to process.")
    parser.add_argument("--output-dir", default="output", help="Output directory for fetched URLs.")
    parser.add_argument("--model", default=os.getenv("MISTRAL_MODEL", "mistral-medium-latest"))
    parser.add_argument("--chunk-size", type=int, default=60)
    args = parser.parse_args()

    source_path = resolve_source(args)
    markdown = source_path.read_text(encoding="utf-8-sig")
    text_lines, original_count, removed_count = clean_and_normalize(markdown)
    cleaned_path = source_path
    cleaned_path.write_text("\n".join(text_lines).rstrip() + "\n", encoding="utf-8")

    judgments = classify_all(text_lines, args.model, args.chunk_size)
    fact_path, judgments_path = write_outputs(cleaned_path, text_lines, judgments)
    nonfact_count = sum(1 for record in judgments if record["label"] == "NONFACT")

    print(f"source={cleaned_path}")
    print(f"fact_marked={fact_path}")
    print(f"judgments={judgments_path}")
    print(f"original_line_count={original_count}")
    print(f"removed_boilerplate_line_count={removed_count}")
    print(f"output_line_count={len(text_lines)}")
    print(f"marked_nonfact_line_count={nonfact_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
