"""Chunking strategies (blueprint §9).

M2: fixed-size windows with overlap, preferring sentence/newline boundaries.
Semantic chunking arrives with the context-engineering phase.
"""


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """Split text into fixed-size chunks (characters) with overlap.

    The window end is nudged back to the nearest `. ` or newline boundary when
    one exists in the second half of the window, so chunks rarely split mid-sentence.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)
    min_overlap = max(1, overlap)
    while start < n:
        end = min(start + chunk_size, n)
        if end < n:
            boundary = text.rfind(". ", start + chunk_size // 2, end)
            if boundary == -1:
                boundary = text.rfind("\n", start + chunk_size // 2, end)
            if boundary != -1:
                end = boundary + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def count_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Used for context budgeting."""
    return max(1, len(text) // 4)
