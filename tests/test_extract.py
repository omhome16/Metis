from pathlib import Path

from app.workers.ingest import extract_text


def test_extract_txt(tmp_path: Path):
    f = tmp_path / "note.txt"
    f.write_text("Hello, Metis!\nSecond line.", encoding="utf-8")
    assert extract_text("txt", str(f)) == "Hello, Metis!\nSecond line."


def test_extract_markdown(tmp_path: Path):
    f = tmp_path / "doc.md"
    f.write_text("# Title\n\nSome **bold** content.", encoding="utf-8")
    assert extract_text("md", str(f)) == "# Title\n\nSome **bold** content."


def test_extract_image_returns_empty(tmp_path: Path):
    f = tmp_path / "pic.png"
    f.write_bytes(b"\x89PNG")
    assert extract_text("image", str(f)) == ""


def test_extract_missing_file_raises(tmp_path: Path):
    import pytest

    with pytest.raises(FileNotFoundError):
        extract_text("txt", str(tmp_path / "missing.txt"))
