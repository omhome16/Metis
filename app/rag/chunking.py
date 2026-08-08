"""Chunking (blueprint §9).

Paragraph-preserving chunks: text is first split on blank lines into paragraphs,
short paragraphs are merged up to a target size, and oversized paragraphs are cut
at sentence boundaries with a carried-over overlap. This keeps definitions and
arguments intact instead of splitting mid-sentence or mid-word.
"""

import re

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n")
    return re.sub(r"[ \t]+", " ", text).strip()


def _split_paragraphs(text: str) -> list[str]:
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    # reflow single-newline paragraphs (PDF extraction leaves hard breaks mid-sentence)
    out: list[str] = []
    for para in paras:
        if "\n" in para and len(para) < 4000:
            lines = para.split("\n")
            joined = lines[0]
            for line in lines[1:]:
                line = line.strip()
                if not line:
                    continue
                # join unless the previous line already ended a sentence
                if joined and not joined.endswith((".", "!", "?", ":", ";", '"')):
                    joined += " "
                joined += line
            para = joined
        out.append(para)
    return out


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_END.split(text)
    return [p.strip() for p in parts if p.strip()]


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    """Split text into paragraph-preserving chunks (~chunk_size chars) with overlap.

    Short paragraphs are grouped to reach the target size; long paragraphs are cut
    at sentence boundaries. The last sentence of a cut carries into the next chunk.
    """
    text = _clean(text)
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    units: list[str] = []
    for para in _split_paragraphs(text):
        if len(para) <= chunk_size:
            units.append(para)
            continue
        # oversized paragraph → sentence-cut
        sentences = _split_sentences(para)
        if len(sentences) <= 1:
            units.append(para)
            continue
        buf = ""
        for sent in sentences:
            if buf and len(buf) + len(sent) + 1 > chunk_size:
                units.append(buf.strip())
                buf = sent
            else:
                buf = f"{buf} {sent}" if buf else sent
        if buf.strip():
            units.append(buf.strip())

    # merge small units up to the target size
    merged: list[str] = []
    for unit in units:
        if merged and len(merged[-1]) + len(unit) + 2 <= chunk_size:
            merged[-1] = f"{merged[-1]}\n\n{unit}"
        else:
            merged.append(unit)

    # apply overlap between consecutive merged units (carry the tail sentence)
    chunks: list[str] = []
    prev_tail = ""
    for unit in merged:
        piece = f"{prev_tail}{unit}" if prev_tail else unit
        if len(piece) > chunk_size:
            tail_start = max(len(piece) - overlap, 0)
            prev_tail = _split_sentences(piece[tail_start:])[0] + " " if piece[tail_start:] else ""
        else:
            prev_tail = ""
        chunks.append(piece)
    return chunks


def count_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Used for context budgeting."""
    return max(1, len(text) // 4)
