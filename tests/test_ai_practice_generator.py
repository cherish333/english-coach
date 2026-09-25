"""Tests for AI Custom Practice Generator module and API endpoints."""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from src.server import app, document_library
from src.core.ai_practice_generator import (
    clean_json_text,
    parse_llm_practice_output,
    build_ai_practice_pdf,
    AI_PRACTICE_PRESETS,
    generate_ai_practice_document,
)

client = TestClient(app)


def test_ai_practice_presets_structure():
    """Verify that presets list is well-formed with necessary fields."""
    assert len(AI_PRACTICE_PRESETS) >= 5
    for preset in AI_PRACTICE_PRESETS:
        assert "id" in preset
        assert "icon" in preset
        assert "label" in preset
        assert "prompt" in preset
        assert "default_difficulty" in preset
        assert "default_count" in preset
        assert len(preset["prompt"]) > 5


def test_presets_api_endpoint():
    """Verify GET /api/ai-practice/presets returns presets correctly."""
    resp = client.get("/api/ai-practice/presets")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "presets" in data
    assert len(data["presets"]) == len(AI_PRACTICE_PRESETS)


def test_clean_json_text_various_formats():
    """Test clean_json_text strips think tags, markdown code blocks, and isolates JSON."""
    # 1. With <think> tags and ```json
    text1 = """<think>Let me formulate 5 business sentences.</think>
    ```json
    {
      "title": "AI定制：商务谈判",
      "sentences": [{"text": "We should meet tomorrow.", "translation": "我们明天应该开会。"}]
    }
    ```"""
    cleaned1 = clean_json_text(text1)
    assert "<think>" not in cleaned1
    assert "```" not in cleaned1
    assert cleaned1.startswith("{") and cleaned1.endswith("}")

    # 2. Raw JSON without code blocks
    text2 = '{"title": "Test Title", "sentences": []}'
    cleaned2 = clean_json_text(text2)
    assert cleaned2 == text2


def test_parse_llm_practice_output_clean_json():
    """Test parse_llm_practice_output parses clean structured JSON."""
    sample_json = json.dumps({
        "title": "AI定制：科技创业精选",
        "description": "围绕硅谷科技创新与团队协作的高频表达",
        "sentences": [
            {
                "text": "Before pushing code to production, every engineer must submit a pull request.",
                "translation": "在将代码推送到生产环境之前，每位工程师都必须提交拉取请求。",
                "focus": "PR规范与生产发布"
            },
            {
                "text": "The startup secured seed funding from several prominent venture capital firms.",
                "translation": "这家初创公司从几家知名风投机构获得了种子轮融资。",
                "focus": "种子轮与风险投资"
            }
        ]
    })
    title, desc, sentences = parse_llm_practice_output(sample_json, fallback_prompt="科技创业")
    assert title == "AI定制：科技创业精选"
    assert "硅谷科技创新" in desc
    assert len(sentences) == 2
    assert sentences[0]["text"] == "Before pushing code to production, every engineer must submit a pull request."
    assert "拉取请求" in sentences[0]["translation"]
    assert sentences[0]["focus"] == "PR规范与生产发布"


def test_parse_llm_practice_output_fallback_lines():
    """Test parse_llm_practice_output gracefully falls back to numbered lines if LLM omits JSON."""
    raw_text = """
    1. Continuous integration ensures code quality throughout development.
       翻译：持续集成确保整个开发过程中的代码质量。
    2. Microservices architecture allows independent deployment of services.
       中文：微服务架构允许服务的独立部署。
    """
    title, desc, sentences = parse_llm_practice_output(raw_text, fallback_prompt="软件架构")
    assert len(sentences) == 2
    assert "Continuous integration ensures" in sentences[0]["text"]
    assert "持续集成" in sentences[0]["translation"]
    assert "Microservices architecture allows" in sentences[1]["text"]
    assert "微服务" in sentences[1]["translation"]


def test_build_ai_practice_pdf_and_extraction():
    """Test building AI practice PDF and extracting sentences through DocumentLibrary."""
    sentences = [
        {
            "text": "Artificial intelligence empowers developers to automate repetitive workflows.",
            "translation": "人工智能使开发者能够将重复性工作流程自动化。",
            "focus": "自动化工作流"
        },
        {
            "text": "Writing unit tests provides immediate feedback and prevents regression bugs.",
            "translation": "编写单元测试可提供即时反馈并防止回归错误。",
            "focus": "单元测试与回归"
        }
    ]
    pdf_bytes, pages, ann = build_ai_practice_pdf(
        title="AI定制：测试与质量保证",
        description="现代软件工程自动化测试核心理念",
        sentences=sentences,
        sentences_per_page=5,
    )

    assert len(pdf_bytes) > 1000
    assert pdf_bytes.startswith(b"%PDF")
    assert pages == 1
    assert len(ann) == 2
    assert ann[0]["page"] == 1

    # Test integration with DocumentLibrary
    doc_info = document_library.add_custom_document(
        filename="test_ai_qa.pdf",
        content=pdf_bytes,
        title="AI定制：测试与质量保证",
        description="现代软件工程自动化测试核心理念",
        sentence_timestamps=ann,
    )
    doc_id = doc_info["id"]

    try:
        extracted = document_library.get_page_sentences(doc_id, 1)
        assert len(extracted) == 2
        assert extracted[0]["text"] == sentences[0]["text"]
        assert extracted[1]["text"] == sentences[1]["text"]
    finally:
        document_library.delete_custom_document(doc_id)


def test_generate_ai_practice_endpoint_mocked():
    """Test POST /api/ai-practice/generate with mocked LLM response."""
    mock_llm_content = json.dumps({
        "title": "AI定制：商务洽谈核心表达",
        "description": "跨国贸易合作与合同条款磋商",
        "sentences": [
            {
                "text": "We are willing to consider your proposal provided that delivery terms are guaranteed.",
                "translation": "只要交货条件得到保证，我们愿意考虑贵方的提议。",
                "focus": "条件让步句型"
            },
            {
                "text": "Could we arrange a brief conference call tomorrow to align on project milestones?",
                "translation": "我们明天能否安排一次简短的电话会议以对齐项目里程碑？",
                "focus": "里程碑对齐"
            }
        ]
    })

    mock_choice = MagicMock()
    mock_choice.message.content = mock_llm_content
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]

    with patch("src.core.ai_practice_generator.RoutedClient") as MockClient:
        instance = MockClient.return_value
        instance.chat.completions.create = AsyncMock(return_value=mock_resp)

        payload = {
            "prompt": "出2句关于商务谈判的英语句子",
            "count": 2,
            "difficulty": "intermediate",
            "title": "AI定制：商务洽谈核心表达"
        }
        resp = client.post("/api/ai-practice/generate", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["title"] == "AI定制：商务洽谈核心表达"
        doc_id = data["document"]["id"]

        try:
            # Verify document exists in document library
            spec = document_library.specs.get(doc_id)
            assert spec is not None
            assert spec.title == "AI定制：商务洽谈核心表达"

            # Verify sentences endpoint
            sent_resp = client.get(f"/api/documents/{doc_id}/pages/1/sentences")
            assert sent_resp.status_code == 200
            sents_data = sent_resp.json()["sentences"]
            assert len(sents_data) == 2
            assert sents_data[0]["text"] == "We are willing to consider your proposal provided that delivery terms are guaranteed."
            # Verify translation was cached and attached
            assert "交货条件" in sents_data[0].get("translation", "")
        finally:
            document_library.delete_custom_document(doc_id)


def test_cjk_pdf_no_mojibake_with_emojis():
    """Verify that sentences and metadata with emojis/astral characters do not produce shifted CID mojibake."""
    import pymupdf
    from src.core.ai_practice_generator import build_ai_practice_pdf, sanitize_cjk_pdf_text

    test_sentences = [
        {
            "text": "You're such a mess.",
            "translation": "🇨🇳 你真是个一团糟。🚀",
            "focus": "Such a mess ✨"
        },
        {
            "text": "That's not my idea.",
            "translation": "那不是我的主意。💡",
            "focus": "Not my idea"
        }
    ]

    pdf_bytes, total_pages, ann = build_ai_practice_pdf(
        title="AI定制：日常表达 🔥",
        description="通过日常高频句子进行跟打与精读 🎯",
        sentences=test_sentences,
    )

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page_text = doc[0].get_text()

    # Verify pure English practice sentences in PDF body
    assert "You're such a mess." in page_text
    assert "That's not my idea." in page_text
    assert "AI定制：日常表达" in page_text

    # Verify no translations or test point clutter in the PDF body
    assert "译文:" not in page_text
    assert "考点:" not in page_text
    assert "你真是个一团糟" not in page_text

    # Verify no shifted CID mojibake
    assert "爨" not in page_text
    assert "臱" not in page_text
    assert "檠" not in page_text
    assert "碉" not in page_text
    doc.close()

    # Verify sidecar metadata keeps translation and focus intact for UI auto-translation
    assert ann[0]["translation"] == "你真是个一团糟。"
    assert ann[0]["focus"] == "Such a mess"
    assert ann[1]["translation"] == "那不是我的主意。"
