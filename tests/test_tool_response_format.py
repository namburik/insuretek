from shared.common.tools.format import normalize_tool_response_text


def test_normalize_strips_google_labelled_wrapper():
    """Google Gemini prefixes LangChain block metadata with its own function labels."""
    raw = (
        "get_merchant_response:\n"
        "output:\n"
        "id: lc_9db4d5ba-a91e-40ad-a431-3a20297613a2\n"
        "text:\n"
        '{\n    "merchant_id": "abc-123",\n    "account_name": "Acme"\n}'
    )
    result = normalize_tool_response_text(raw)
    assert "get_merchant_response" not in result
    assert "lc_9db4d5ba" not in result  # LangChain block ID stripped
    assert "text:" not in result
    assert "{" not in result
    assert "merchant_id: abc-123" in result
    assert "account_name: Acme" in result


def test_normalize_strips_json_code_fence():
    raw = '```json\n{"merchant_id": "abc-123", "name": "Acme Corp"}\n```'
    result = normalize_tool_response_text(raw)
    assert "{" not in result
    assert "}" not in result
    assert "merchant_id: abc-123" in result
    assert "name: Acme Corp" in result


def test_normalize_strips_bare_code_fence_with_plain_text():
    raw = "```\nsome plain response text\n```"
    result = normalize_tool_response_text(raw)
    assert result == "some plain response text"


def test_normalize_strips_nested_code_fence_json():
    raw = "```\n{\"status\": \"active\", \"count\": 5}\n```"
    result = normalize_tool_response_text(raw)
    assert "status: active" in result
    assert "count: 5" in result


def test_normalize_tool_response_text_handles_google_wrapper_with_trailing_commas():
    raw = '''id: lc_fb52efae-acd5-40b2-96aa-9c2fdbdf2808
text:
{
    "merchant": {
        "acceptance_channel": "CNP",
        "account_alias": "Paytek Payments",
    }
}'''
    normalized = normalize_tool_response_text(raw)

    # Single-key wrapper {"merchant": {...}} is unwrapped — no "merchant:" label emitted
    assert "merchant:" not in normalized
    assert "acceptance_channel: CNP" in normalized
    assert "account_alias: Paytek Payments" in normalized
    assert "{" not in normalized
    assert "}" not in normalized


def test_extract_result_content_prefers_assistant_message_and_strips_google_wrapper():
    from shared.common.infra.setup import extract_result_content

    result = {
        "messages": [
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": '''id: lc_433029bf-c24d-410d-b6cb-83afc98c6a09
text:
{
    "merchant": {
        "acceptance_channel": "CNP",
        "account_alias": "Paytek Payments",
    }
}''',
                    }
                ],
            }
        ]
    }

    normalized = extract_result_content(result)
    # Single-key wrapper {"merchant": {...}} is unwrapped — fields appear at top level
    assert "acceptance_channel: CNP" in normalized
    assert "account_alias: Paytek Payments" in normalized
    assert "lc_433029bf" not in normalized
    assert "text:" not in normalized
