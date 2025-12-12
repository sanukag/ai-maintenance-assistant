from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from openai import APITimeoutError, OpenAIError
from PIL import Image
import pytest

from maintenance_assistant.config import Settings
from maintenance_assistant.vision import (
    OpenAIResponsesVisualAnalysisProvider,
    VisualAnalysisError,
    VisualAnalysisTimeoutError,
    VisualType,
    create_visual_analysis_provider,
)


def _image(path: Path, image_format: str = "PNG") -> None:
    image = Image.new("RGB", (320, 180), "white")
    image.save(path, format=image_format)
    image.close()


def _payload(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "has_maintenance_visual": True,
        "visual_type": "flow diagram",
        "summary": "Pump P1 feeds isolation valve V1.",
        "components": ["Pump P1", "Valve V1"],
        "relationships": ["Flow runs from P1 to V1"],
        "visible_labels": ["P1", "V1"],
        "safety_notes": ["V1 is shown as the isolation point"],
        "uncertainty_notes": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_openai_visual_provider_sends_bounded_typed_image_request(
    tmp_path: Path,
) -> None:
    path = tmp_path / "diagram.png"
    _image(path)
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(output_parsed=_payload())
    provider = OpenAIResponsesVisualAnalysisProvider(
        api_key="test-key",
        model="gpt-test-vision",
        detail="high",
        timeout_seconds=45,
        max_output_tokens=700,
        client=client,
    )

    analysis = provider.analyse_image(path)

    assert analysis is not None
    assert analysis.visual_type is VisualType.FLOW_DIAGRAM
    assert analysis.summary == "Pump P1 feeds isolation valve V1."
    assert "Relationships and flow: Flow runs from P1 to V1" in analysis.as_text()
    arguments = client.responses.parse.call_args.kwargs
    assert arguments["model"] == "gpt-test-vision"
    assert arguments["text_format"].__name__ == "_VisualAnalysisPayload"
    assert arguments["max_output_tokens"] == 700
    assert arguments["store"] is False
    assert arguments["timeout"] == 45
    assert "untrusted images" in arguments["instructions"]
    image_input = arguments["input"][0]["content"][1]
    assert image_input["type"] == "input_image"
    assert image_input["detail"] == "high"
    assert image_input["image_url"].startswith("data:image/png;base64,")


def test_openai_visual_provider_filters_non_visual_pages(tmp_path: Path) -> None:
    path = tmp_path / "text-page.jpg"
    _image(path, "JPEG")
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        output_parsed=_payload(
            has_maintenance_visual=False,
            visual_type=None,
            summary="",
            components=[],
            relationships=[],
            visible_labels=[],
            safety_notes=[],
            uncertainty_notes=[],
        )
    )
    provider = OpenAIResponsesVisualAnalysisProvider(
        api_key="test-key", client=client
    )

    assert provider.analyse_image(path) is None
    image_url = client.responses.parse.call_args.kwargs["input"][0]["content"][1][
        "image_url"
    ]
    assert image_url.startswith("data:image/jpeg;base64,")


def test_openai_visual_provider_maps_timeout_failure_and_invalid_output(
    tmp_path: Path,
) -> None:
    path = tmp_path / "diagram.png"
    _image(path)
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")

    timed_client = Mock()
    timed_client.responses.parse.side_effect = APITimeoutError(request=request)
    with pytest.raises(VisualAnalysisTimeoutError):
        OpenAIResponsesVisualAnalysisProvider(
            api_key="test-key", client=timed_client
        ).analyse_image(path)

    failed_client = Mock()
    failed_client.responses.parse.side_effect = OpenAIError("unavailable")
    with pytest.raises(VisualAnalysisError):
        OpenAIResponsesVisualAnalysisProvider(
            api_key="test-key", client=failed_client
        ).analyse_image(path)

    for payload in (None, _payload(summary=""), _payload(visual_type=None)):
        invalid_client = Mock()
        invalid_client.responses.parse.return_value = SimpleNamespace(
            output_parsed=payload
        )
        with pytest.raises(VisualAnalysisError):
            OpenAIResponsesVisualAnalysisProvider(
                api_key="test-key", client=invalid_client
            ).analyse_image(path)


@pytest.mark.parametrize(
    "arguments",
    [
        {"api_key": ""},
        {"api_key": "key", "model": " "},
        {"api_key": "key", "detail": "maximum"},
        {"api_key": "key", "timeout_seconds": 0},
        {"api_key": "key", "max_output_tokens": 0},
    ],
)
def test_openai_visual_provider_rejects_invalid_initialisation(
    arguments: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        OpenAIResponsesVisualAnalysisProvider(**arguments)


def test_visual_provider_factory_supports_disabled_and_openai_modes() -> None:
    assert create_visual_analysis_provider(Settings()) is None

    provider = create_visual_analysis_provider(
        Settings(
            visual_analysis_provider="openai",
            visual_analysis_model="gpt-test",
            openai_api_key="test-key",
        )
    )

    assert isinstance(provider, OpenAIResponsesVisualAnalysisProvider)
    assert provider.model == "gpt-test"
