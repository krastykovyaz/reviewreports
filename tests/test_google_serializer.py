"""Regression tests for a real bug found while testing the CV builder's LLM
extraction against a real Gemini model: Gemini's response_schema rejects
$defs/$ref/anyOf/additionalProperties/title/default outright ("Unknown
field for Schema: $defs", confirmed against the real API) - exactly what
pydantic's model_json_schema() emits for any model with a nested BaseModel
field (like CVProfile's Contact/Experience/Education) or an Optional field.
"""

from typing import List, Optional

from pydantic import BaseModel

from src.model.google.serializer import GoogleChatSerializer


class _Address(BaseModel):
    city: str
    country: Optional[str] = None


class _Person(BaseModel):
    name: str
    nickname: Optional[str] = None
    address: _Address
    tags: List[str]
    addresses: List[_Address] = []


def _assert_no_unsupported_keys(schema) -> None:
    """Walks actual schema structure only (properties' values, items) -
    not `properties`' own keys, which are arbitrary field names (e.g.
    Experience.title collides with the JSON-Schema metadata key "title")."""
    unsupported = {"$defs", "$ref", "anyOf", "oneOf", "allOf", "additionalProperties", "title", "default"}
    if not isinstance(schema, dict):
        return
    found = unsupported & schema.keys()
    assert not found, f"Unsupported key(s) survived: {found} in {schema}"
    for prop_schema in schema.get("properties", {}).values():
        _assert_no_unsupported_keys(prop_schema)
    if "items" in schema:
        _assert_no_unsupported_keys(schema["items"])


def test_serialize_response_format_strips_defs_and_refs():
    result = GoogleChatSerializer.serialize_response_format(_Person)
    _assert_no_unsupported_keys(result["response_schema"])
    assert result["response_mime_type"] == "application/json"


def test_serialize_response_format_inlines_nested_model():
    schema = GoogleChatSerializer.serialize_response_format(_Person)["response_schema"]
    address_schema = schema["properties"]["address"]
    assert address_schema["type"] == "object"
    assert address_schema["properties"]["city"] == {"type": "string"}


def test_serialize_response_format_inlines_nested_model_in_a_list():
    schema = GoogleChatSerializer.serialize_response_format(_Person)["response_schema"]
    addresses_schema = schema["properties"]["addresses"]
    assert addresses_schema["type"] == "array"
    assert addresses_schema["items"]["type"] == "object"
    assert addresses_schema["items"]["properties"]["city"] == {"type": "string"}


def test_serialize_response_format_converts_optional_to_nullable():
    schema = GoogleChatSerializer.serialize_response_format(_Person)["response_schema"]
    assert schema["properties"]["nickname"] == {"type": "string", "nullable": True}
    assert schema["properties"]["address"]["properties"]["country"] == {"type": "string", "nullable": True}


def test_serialize_response_format_preserves_required_and_array_items():
    schema = GoogleChatSerializer.serialize_response_format(_Person)["response_schema"]
    assert schema["required"] == ["name", "address", "tags"]
    assert schema["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}


def test_serialize_response_format_on_instance_matches_class():
    person = _Person(name="Alex", address=_Address(city="Paris"), tags=["a", "b"])
    class_schema = GoogleChatSerializer.serialize_response_format(_Person)
    instance_schema = GoogleChatSerializer.serialize_response_format(person)
    assert class_schema == instance_schema


def test_serialize_response_format_on_cv_profile_is_clean():
    # The actual schema that broke against a real Gemini call: CVProfile has
    # multiple nested BaseModel fields (Contact, List[Experience], List[Education]).
    from src.cv.schema import CVProfile

    result = GoogleChatSerializer.serialize_response_format(CVProfile)
    _assert_no_unsupported_keys(result["response_schema"])
    schema = result["response_schema"]
    assert schema["properties"]["contact"]["properties"]["email"] == {"type": "string"}
    assert schema["properties"]["experience"]["items"]["properties"]["organization"] == {"type": "string"}
    assert schema["required"] == ["name", "contact"]


def test_serialize_response_format_openai_style_dict_is_sanitized():
    # The chat-completions-style dict format (as built for OpenAI/DeepSeek)
    # carries the same $defs/$ref shape and must be sanitized the same way
    # when reused for a Gemini call.
    openai_style = {
        "type": "json_schema",
        "json_schema": {"name": "response", "strict": True, "schema": _Person.model_json_schema()},
    }
    result = GoogleChatSerializer.serialize_response_format(openai_style)
    _assert_no_unsupported_keys(result["response_schema"])
