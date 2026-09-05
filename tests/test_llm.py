import pytest

from brand_serp import config as C
from brand_serp import llm
from brand_serp.llm import accept, build_prompt, parse_llm_payload, to_classification
from brand_serp.models import Classification, PageEvidence, SerpResult


def test_parse_valid_payload_with_markdown_fence():
    text = '```json\n{"category":"affiliate","confidence":0.82,' \
           '"evidence":["btag link"],"explanation":"monetised link"}\n```'
    payload = parse_llm_payload(text)
    assert payload["category"] == "affiliate" and payload["confidence"] == 0.82


def test_parse_rejects_unknown_category_and_broken_json():
    assert parse_llm_payload('{"category":"partner","confidence":0.9}') is None
    assert parse_llm_payload("no json here") is None
    assert parse_llm_payload('{"category":"affiliate",') is None
    assert parse_llm_payload("") is None


def test_accept_requires_confidence_and_evidence():
    assert not accept({"category": "affiliate", "confidence": 0.5, "evidence": ["x"],
                       "explanation": ""})
    assert not accept({"category": "competitor", "confidence": 0.9, "evidence": [],
                       "explanation": ""})
    assert accept({"category": "affiliate", "confidence": 0.9, "evidence": ["x"],
                   "explanation": ""})
    assert accept({"category": "unknown", "confidence": 0.2, "evidence": [],
                   "explanation": ""})
    assert not accept(None)


def test_to_classification_marks_source_and_keeps_explanation():
    c = to_classification({"category": "affiliate", "confidence": 0.9,
                           "evidence": ["btag"], "explanation": "links to brand with btag"}, [])
    assert c.classifier == "llm" and c.category == C.AFFILIATE
    assert "btag" in c.explanation


def test_prompt_contains_domain_and_official_list():
    r = SerpResult(3, "https://casino.nl/starcasino", "t", "s")
    prompt = build_prompt(r, PageEvidence(url=r.url, fetched=True, outbound=["https://x.nl"]))
    assert "casino.nl" in prompt and "starcasino.nl" in prompt


# --------------------------------------------------------------- providers
class FakeSettings:
    def __init__(self, **kw):
        self.use_llm = True
        self.llm_provider = "anthropic"
        self.llm_model = "test-model"
        self.anthropic_api_key = None
        self.gemini_api_key = None
        self.http_timeout = 5.0
        self.__dict__.update(kw)


@pytest.fixture
def captured(monkeypatch):
    """Replace the single networking function and record what it was given."""
    calls = []
    response = {}

    def fake_post(url, headers, payload, timeout):
        calls.append({"url": url, "headers": headers, "payload": payload,
                      "timeout": timeout})
        if isinstance(response.get("body"), Exception):
            raise response["body"]
        return response.get("body", {})

    monkeypatch.setattr(llm, "_post_json", fake_post)
    return calls, response


ANTHROPIC_OK = {"content": [{"type": "text", "text": '{"category":"affiliate",'
                             '"confidence":0.9,"evidence":["btag"],"explanation":"x"}'}]}
GEMINI_OK = {"candidates": [{"content": {"parts": [
    {"text": '{"category":"affiliate","confidence":0.9,"evidence":["btag"],'
             '"explanation":"x"}'}]}}]}


def unknown_result():
    r = SerpResult(7, "https://casinonieuws.nl/starcasino", "t", "s")
    return r, Classification(C.UNKNOWN, 0.3, "thin evidence", [])


def test_gemini_request_shape(captured):
    calls, response = captured
    response["body"] = GEMINI_OK
    r, current = unknown_result()
    out = llm.refine(r, None, current,
                     FakeSettings(llm_provider="gemini", gemini_api_key="k",
                                  llm_model="gemini-2.5-flash"))
    assert out.category == C.AFFILIATE and out.classifier == "llm"

    call = calls[0]
    assert call["url"].endswith("/models/gemini-2.5-flash:generateContent")
    assert call["headers"]["x-goog-api-key"] == "k"
    assert call["payload"]["system_instruction"]["parts"][0]["text"] == llm.SYSTEM_PROMPT
    assert call["payload"]["contents"][0]["role"] == "user"
    config = call["payload"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["properties"]["category"]["enum"] == list(C.CATEGORIES)


def test_anthropic_request_shape_is_unchanged(captured):
    calls, response = captured
    response["body"] = ANTHROPIC_OK
    r, current = unknown_result()
    out = llm.refine(r, None, current,
                     FakeSettings(anthropic_api_key="k", llm_model="claude-sonnet-4-6"))
    assert out.category == C.AFFILIATE

    call = calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == "k"
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert call["payload"]["messages"][0]["role"] == "user"


@pytest.mark.parametrize("settings", [
    FakeSettings(use_llm=False, anthropic_api_key="k"),          # fallback disabled
    FakeSettings(anthropic_api_key=None),                        # no key
    FakeSettings(llm_provider="gemini", gemini_api_key=None),    # no key for provider
    FakeSettings(llm_provider="openai", anthropic_api_key="k"),  # unknown provider
    FakeSettings(llm_provider="", anthropic_api_key="k"),
])
def test_provider_is_skipped_when_unusable(captured, settings):
    calls, _ = captured
    assert llm.select_provider(settings) is None
    r, current = unknown_result()
    assert llm.refine(r, None, current, settings) is current
    assert calls == []  # nothing hit the network


def test_wrong_key_for_the_selected_provider_is_not_used(captured):
    """An Anthropic key must not enable the Gemini path."""
    calls, _ = captured
    settings = FakeSettings(llm_provider="gemini", anthropic_api_key="k")
    assert llm.select_provider(settings) is None
    assert calls == []


@pytest.mark.parametrize("body", [
    RuntimeError("connection reset"),        # network / auth / quota failure
    {},                                      # empty response
    {"candidates": []},                      # Gemini blocked the request
    {"candidates": [{"content": {}}]},       # no parts (e.g. token budget spent)
    {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]},
])
def test_gemini_failures_degrade_to_the_rule_based_result(captured, body):
    _, response = captured
    response["body"] = body
    r, current = unknown_result()
    out = llm.refine(r, None, current,
                     FakeSettings(llm_provider="gemini", gemini_api_key="k"))
    assert out is current
    assert out.category == C.UNKNOWN


def test_low_confidence_gemini_verdict_is_rejected(captured):
    _, response = captured
    response["body"] = {"candidates": [{"content": {"parts": [{"text":
        '{"category":"competitor","confidence":0.5,"evidence":["x"],"explanation":"y"}'}]}}]}
    r, current = unknown_result()
    assert llm.refine(r, None, current,
                      FakeSettings(llm_provider="gemini", gemini_api_key="k")) is current


def test_resolved_results_never_reach_the_llm(captured):
    calls, response = captured
    response["body"] = GEMINI_OK
    r, _ = unknown_result()
    decided = Classification(C.COMPETITOR, 0.8, "rules were sure", [])
    assert llm.refine(r, None, decided,
                      FakeSettings(llm_provider="gemini", gemini_api_key="k")) is decided
    assert calls == []


# ------------------------------------------------------------------ settings
def test_settings_pick_the_default_model_for_the_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    settings = C.Settings.from_env()
    assert settings.llm_provider == "gemini"
    assert settings.llm_model == C.DEFAULT_LLM_MODELS["gemini"]


def test_explicit_model_overrides_the_provider_default(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "GEMINI")   # case-insensitive
    monkeypatch.setenv("LLM_MODEL", "gemini-2.5-pro")
    settings = C.Settings.from_env()
    assert settings.llm_provider == "gemini"
    assert settings.llm_model == "gemini-2.5-pro"


def test_google_api_key_is_accepted_as_an_alias(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "from-google-var")
    assert C.Settings.from_env().gemini_api_key == "from-google-var"


def test_provider_defaults_to_anthropic(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    settings = C.Settings.from_env()
    assert settings.llm_provider == "anthropic"
    assert settings.llm_model == C.DEFAULT_LLM_MODELS["anthropic"]
