from app.gateway.gateway import LLMGateway
from app.gateway.mock import MockProvider
from app.rag.vision import describe_image, mime_for_file, parse_image_description


def test_parse_json_description():
    text = '{"caption": "A red sunset over the sea.", "tags": ["sunset", "sea"]}'
    result = parse_image_description(text)
    assert result["caption"] == "A red sunset over the sea."
    assert result["tags"] == ["sunset", "sea"]


def test_parse_non_json_falls_back_to_caption():
    result = parse_image_description("A plain description with no JSON.")
    assert result["caption"] == "A plain description with no JSON."
    assert result["tags"] == []


def test_parse_caps_tag_count():
    text = '{"caption": "x", "tags": ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l"]}'
    assert len(parse_image_description(text)["tags"]) == 10


def test_mime_for_file():
    assert mime_for_file("pic.png") == "image/png"
    assert mime_for_file("pic.JPG") == "image/jpeg"
    assert mime_for_file("pic.webp") == "image/webp"
    assert mime_for_file("noext") == "image/png"


async def test_describe_image_via_mock_gateway():
    gw = LLMGateway(clients={"mock": MockProvider()})
    result = await describe_image(gw, "aGVsbG8=", "image/png")
    assert result["caption"]
    assert result["tags"]
