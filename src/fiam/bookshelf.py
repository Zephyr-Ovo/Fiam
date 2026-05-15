"""Bookshelf — epub ingestion, navigation, and annotation for AI co-reading.

An epub is parsed into an indexed structure: chapters → paragraphs, each with
a stable ID. The AI navigates via tools (toc, read, search, next/prev,
annotate). Annotations are shared between human and AI readers.

Storage layout under bookshelf_dir (typically home/bookshelf/):
  books/<book_id>/meta.json      — title, author, language, chapter count
  books/<book_id>/chapters.json  — [{id, title, paragraphs: [{id, text}]}]
  books/<book_id>/annotations.jsonl — {paragraph_id, author, text, ts}
  books/<book_id>/source.epub    — original file (optional copy)
  cursor.json                    — {book_id, chapter_idx, paragraph_idx}
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Paragraph:
    id: str
    text: str


@dataclass(frozen=True)
class Chapter:
    id: str
    title: str
    paragraphs: tuple[Paragraph, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "paragraphs": [{"id": p.id, "text": p.text} for p in self.paragraphs],
        }


@dataclass(frozen=True)
class BookMeta:
    book_id: str
    title: str
    author: str
    language: str
    chapter_count: int


def _stable_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index:04d}"


def parse_epub(epub_path: Path) -> tuple[BookMeta, list[Chapter]]:
    """Parse an epub file into chapters and paragraphs."""
    import ebooklib
    from bs4 import BeautifulSoup
    from ebooklib import epub

    book = epub.read_epub(str(epub_path), options={"ignore_ncx": False})

    title = book.get_metadata("DC", "title")
    title_str = title[0][0] if title else epub_path.stem
    creator = book.get_metadata("DC", "creator")
    author_str = creator[0][0] if creator else ""
    lang = book.get_metadata("DC", "language")
    lang_str = lang[0][0] if lang else ""

    raw = epub_path.read_bytes()
    book_id = hashlib.sha256(raw).hexdigest()[:16]

    spine_ids = [item_id for item_id, _ in book.spine]
    items_by_id = {item.get_id(): item for item in book.get_items()}

    chapters: list[Chapter] = []
    ch_idx = 0
    for spine_id in spine_ids:
        item = items_by_id.get(spine_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        html = item.get_content().decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")

        heading = soup.find(re.compile(r"^h[1-3]$"))
        ch_title = heading.get_text(strip=True) if heading else f"Chapter {ch_idx + 1}"

        ch_id = _stable_id("ch", ch_idx)
        paragraphs: list[Paragraph] = []
        p_idx = 0
        for el in soup.find_all(["p", "blockquote", "li"]):
            text = el.get_text(strip=True)
            if not text or len(text) < 2:
                continue
            p_id = f"{ch_id}_p{p_idx:04d}"
            paragraphs.append(Paragraph(id=p_id, text=text))
            p_idx += 1

        if paragraphs:
            chapters.append(Chapter(
                id=ch_id,
                title=ch_title,
                paragraphs=tuple(paragraphs),
            ))
            ch_idx += 1

    meta = BookMeta(
        book_id=book_id,
        title=str(title_str),
        author=str(author_str),
        language=str(lang_str),
        chapter_count=len(chapters),
    )
    return meta, chapters


class Bookshelf:
    """Persistent bookshelf with navigation cursor and annotations."""

    def __init__(self, shelf_dir: Path) -> None:
        self._dir = shelf_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _book_dir(self, book_id: str) -> Path:
        return self._dir / "books" / book_id

    def _cursor_path(self) -> Path:
        return self._dir / "cursor.json"

    def ingest(self, epub_path: Path, *, copy_source: bool = False) -> BookMeta:
        meta, chapters = parse_epub(epub_path)
        bdir = self._book_dir(meta.book_id)
        bdir.mkdir(parents=True, exist_ok=True)

        meta_dict = {
            "book_id": meta.book_id,
            "title": meta.title,
            "author": meta.author,
            "language": meta.language,
            "chapter_count": meta.chapter_count,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }
        (bdir / "meta.json").write_text(json.dumps(meta_dict, ensure_ascii=False, indent=2), encoding="utf-8")
        (bdir / "chapters.json").write_text(
            json.dumps([ch.to_dict() for ch in chapters], ensure_ascii=False),
            encoding="utf-8",
        )
        if copy_source:
            import shutil
            shutil.copy2(epub_path, bdir / "source.epub")
        return meta

    def list_books(self) -> list[dict[str, Any]]:
        books_dir = self._dir / "books"
        if not books_dir.exists():
            return []
        out = []
        for d in sorted(books_dir.iterdir()):
            meta_path = d / "meta.json"
            if meta_path.exists():
                try:
                    out.append(json.loads(meta_path.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError):
                    continue
        return out

    def _load_chapters(self, book_id: str) -> list[dict[str, Any]]:
        path = self._book_dir(book_id) / "chapters.json"
        if not path.exists():
            raise FileNotFoundError(f"book not found: {book_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def toc(self, book_id: str) -> list[dict[str, Any]]:
        chapters = self._load_chapters(book_id)
        return [
            {"index": i, "id": ch["id"], "title": ch["title"], "paragraphs": len(ch["paragraphs"])}
            for i, ch in enumerate(chapters)
        ]

    def read(self, book_id: str, chapter_idx: int, start: int = 0, count: int = 20) -> dict[str, Any]:
        chapters = self._load_chapters(book_id)
        if chapter_idx < 0 or chapter_idx >= len(chapters):
            raise IndexError(f"chapter index {chapter_idx} out of range (0-{len(chapters)-1})")
        ch = chapters[chapter_idx]
        paras = ch["paragraphs"][start:start + count]
        self._save_cursor(book_id, chapter_idx, start + len(paras))
        return {
            "book_id": book_id,
            "chapter": {"index": chapter_idx, "id": ch["id"], "title": ch["title"]},
            "paragraphs": paras,
            "range": {"start": start, "end": start + len(paras), "total": len(ch["paragraphs"])},
        }

    def read_next(self, count: int = 20) -> dict[str, Any]:
        cursor = self._load_cursor()
        if not cursor:
            raise RuntimeError("no reading position — use book_read first")
        book_id = cursor["book_id"]
        ch_idx = cursor["chapter_idx"]
        p_idx = cursor["paragraph_idx"]
        chapters = self._load_chapters(book_id)
        ch = chapters[ch_idx]
        if p_idx >= len(ch["paragraphs"]):
            if ch_idx + 1 < len(chapters):
                ch_idx += 1
                p_idx = 0
            else:
                return {"end_of_book": True, "book_id": book_id}
        return self.read(book_id, ch_idx, p_idx, count)

    def read_prev(self, count: int = 20) -> dict[str, Any]:
        cursor = self._load_cursor()
        if not cursor:
            raise RuntimeError("no reading position — use book_read first")
        book_id = cursor["book_id"]
        ch_idx = cursor["chapter_idx"]
        p_idx = cursor["paragraph_idx"]
        start = max(0, p_idx - count * 2)
        if start == 0 and p_idx == 0 and ch_idx > 0:
            ch_idx -= 1
            chapters = self._load_chapters(book_id)
            total = len(chapters[ch_idx]["paragraphs"])
            start = max(0, total - count)
        return self.read(book_id, ch_idx, start, count)

    def search(self, book_id: str, query: str, *, max_results: int = 10) -> list[dict[str, Any]]:
        chapters = self._load_chapters(book_id)
        query_lower = query.lower()
        results: list[dict[str, Any]] = []
        for ch_idx, ch in enumerate(chapters):
            for p_idx, p in enumerate(ch["paragraphs"]):
                if query_lower in p["text"].lower():
                    results.append({
                        "chapter": {"index": ch_idx, "id": ch["id"], "title": ch["title"]},
                        "paragraph_index": p_idx,
                        "paragraph_id": p["id"],
                        "text": p["text"][:300],
                    })
                    if len(results) >= max_results:
                        return results
        return results

    def annotate(self, book_id: str, paragraph_id: str, text: str, *, author: str = "ai") -> dict[str, Any]:
        bdir = self._book_dir(book_id)
        if not bdir.exists():
            raise FileNotFoundError(f"book not found: {book_id}")
        record = {
            "paragraph_id": paragraph_id,
            "author": author,
            "text": text,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        ann_path = bdir / "annotations.jsonl"
        with ann_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def get_annotations(self, book_id: str, *, chapter_id: str | None = None) -> list[dict[str, Any]]:
        ann_path = self._book_dir(book_id) / "annotations.jsonl"
        if not ann_path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in ann_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if chapter_id and not str(record.get("paragraph_id", "")).startswith(chapter_id):
                continue
            out.append(record)
        return out

    def _save_cursor(self, book_id: str, chapter_idx: int, paragraph_idx: int) -> None:
        self._cursor_path().write_text(json.dumps({
            "book_id": book_id,
            "chapter_idx": chapter_idx,
            "paragraph_idx": paragraph_idx,
            "ts": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False), encoding="utf-8")

    def _load_cursor(self) -> dict[str, Any] | None:
        path = self._cursor_path()
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
