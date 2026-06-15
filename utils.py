from __future__ import annotations

import re
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Iterable, Sequence
from urllib.parse import parse_qs, urlparse

CHANNEL_ID_RE = re.compile(r"^(UC[\w-]{20,}|[A-Za-z0-9_-]{24})$")
PLAYLIST_ID_RE = re.compile(r"^([A-Za-z0-9_-]{10,}|LL|LM|WL)$")
HANDLE_RE = re.compile(r"@([A-Za-z0-9._-]{2,})")

@dataclass(slots=True)
class ParsedTarget:
    raw: str
    id: str
    kind: str

def chunked(items: Sequence[str], size: int) -> list[list[str]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]

def normalize_input(value: str) -> str:
    return value.strip()

def parse_playlist_identifier(value: str) -> str:
    value = normalize_input(value)
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        query = parse_qs(parsed.query)
        if "list" in query and query["list"]:
            return query["list"][0]
        path = parsed.path.rstrip("/")
        if path:
            candidate = path.split("/")[-1]
            if candidate:
                return candidate
    return value.split("?")[0].split("&")[0]

def parse_channel_identifier(value: str) -> str:
    value = normalize_input(value)
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        path = parsed.path.strip("/")
        query = parse_qs(parsed.query)
        if "channel_id" in query and query["channel_id"]:
            return query["channel_id"][0]
        if path.startswith("channel/"):
            return path.split("/")[-1]
        if path.startswith("@"):
            return path.split("/")[-1]
        if path.startswith("user/"):
            return path.split("/")[-1]
        if path.startswith("c/"):
            return path.split("/")[-1]
    if value.startswith("@"):
        return value[1:]
    return value.split("?")[0].split("&")[0]

def extract_youtube_handle(value: str) -> str | None:
    match = HANDLE_RE.search(value)
    if match:
        return match.group(1)
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        path = parsed.path.strip("/")
        if path.startswith("@"):
            return path[1:]
    if value.startswith("@"):
        return value[1:]
    return None

def text_pages(items: Iterable[str], per_page: int = 10) -> list[str]:
    page: list[str] = []
    pages: list[str] = []
    for item in items:
        page.append(item)
        if len(page) == per_page:
            pages.append("\n".join(page))
            page = []
    if page:
        pages.append("\n".join(page))
    return pages

async def async_batched[T](iterator: AsyncIterator[T], batch_size: int) -> AsyncIterator[list[T]]:
    batch = []
    async for item in iterator:
        batch.append(item)
        if len(batch) == batch_size:
            yield batch
            batch = []
    if batch:
        yield batch

UTC = timezone.utc

def utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()