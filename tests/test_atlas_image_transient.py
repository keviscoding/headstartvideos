from core.atlas_llm import is_atlas_image_transient_error


def test_parse_upstream_html_is_transient():
    assert is_atlas_image_transient_error(
        "failed to parse upstream response: invalid character '<' looking for beginning of value"
    )


def test_busy_message_is_transient():
    assert is_atlas_image_transient_error("Image provider is busy — please try again in a moment.")


def test_normal_errors_not_transient():
    assert not is_atlas_image_transient_error("empty prompt")
    assert not is_atlas_image_transient_error("ATLASCLOUD_KEY not set")
