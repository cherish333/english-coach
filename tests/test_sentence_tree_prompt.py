import pytest
from src.core.llm import (
    SENTENCE_LECTURE_PROMPT,
    LECTURE_SYSTEM_PROMPT,
    COACH_SYSTEM_PROMPT,
    TEACHING_STYLES,
    get_persona_prompt,
    LlmCoach,
)


def test_sentence_lecture_prompt_tree_dissection_spec():
    """Verify that SENTENCE_LECTURE_PROMPT contains the full Sentence Tree Dissection specification."""
    # 1. Core terminology and model name
    assert "主干枝叶·树状分层长难句拆解规范" in SENTENCE_LECTURE_PROMPT
    assert "Sentence Tree Dissection" in SENTENCE_LECTURE_PROMPT

    # 2. Key components in Syntax Structure section
    assert "### 🧩 句法骨架 (Syntax Structure)" in SENTENCE_LECTURE_PROMPT
    assert "【核心骨架 (Core Skeleton)】" in SENTENCE_LECTURE_PROMPT
    assert "【多维树状透视图谱 (Syntax Hierarchy Tree)】" in SENTENCE_LECTURE_PROMPT
    assert "【顺读意群流 (Sense Groups / Reading Flow)】" in SENTENCE_LECTURE_PROMPT

    # 3. Hierarchy tree details: tree branch symbols and logic question prompts
    assert "├─" in SENTENCE_LECTURE_PROMPT
    assert "└─" in SENTENCE_LECTURE_PROMPT
    assert "Level 1" in SENTENCE_LECTURE_PROMPT
    assert "Level 2" in SENTENCE_LECTURE_PROMPT
    assert "哪个？" in SENTENCE_LECTURE_PROMPT
    assert "何时？" in SENTENCE_LECTURE_PROMPT

    # 4. Reading flow requirements: left-to-right, no backward translation
    assert "从左至右自然顺读" in SENTENCE_LECTURE_PROMPT or "自左向右" in SENTENCE_LECTURE_PROMPT
    assert "告别回视倒译" in SENTENCE_LECTURE_PROMPT

    # 5. Voice instructions: guide tutor to highlight core backbone naturally
    assert "核心主干其实就是一句" in SENTENCE_LECTURE_PROMPT
    assert "积木" in SENTENCE_LECTURE_PROMPT

    # 6. Backward compatibility of output protocol tags
    assert "<voice>" in SENTENCE_LECTURE_PROMPT
    assert "</voice>" in SENTENCE_LECTURE_PROMPT
    assert "<notes>" in SENTENCE_LECTURE_PROMPT
    assert "</notes>" in SENTENCE_LECTURE_PROMPT
    assert "绝对不要讲解发音" in SENTENCE_LECTURE_PROMPT
    assert "中文释义" in SENTENCE_LECTURE_PROMPT


def test_lecture_system_prompt_tree_dissection_spec():
    """Verify that LECTURE_SYSTEM_PROMPT incorporates Sentence Tree Dissection."""
    assert "主干枝叶·树状分层长难句拆解规范" in LECTURE_SYSTEM_PROMPT
    assert "Sentence Tree Dissection" in LECTURE_SYSTEM_PROMPT
    assert "核心骨架 (Core Skeleton)" in LECTURE_SYSTEM_PROMPT
    assert "多维树状透视图谱 (Syntax Hierarchy Tree)" in LECTURE_SYSTEM_PROMPT
    assert "顺读意群流" in LECTURE_SYSTEM_PROMPT
    assert "核心主干其实就是一句" in LECTURE_SYSTEM_PROMPT

    # Tag compatibility
    assert "<voice>" in LECTURE_SYSTEM_PROMPT
    assert "</voice>" in LECTURE_SYSTEM_PROMPT
    assert "<notes>" in LECTURE_SYSTEM_PROMPT
    assert "</notes>" in LECTURE_SYSTEM_PROMPT


def test_grammar_persona_incorporates_tree_model():
    """Verify that grammar teaching persona specifically highlights the tree dissection model."""
    grammar_persona = TEACHING_STYLES["grammar"]
    assert "主干枝叶·树状分层长难句拆解模型" in grammar_persona
    assert "Core Skeleton + Syntax Hierarchy Tree + Sense Groups Flow" in grammar_persona
    assert "tree hierarchy visualization" in grammar_persona


@pytest.mark.anyio
async def test_llm_coach_prompt_integration():
    """Verify that LlmCoach integrates the updated prompts correctly."""
    coach = LlmCoach()
    coach.set_lecture_context(
        document_title="Sample Document",
        page_number=1,
        total_pages=10,
        page_text="The ancient library contains priceless manuscripts.",
    )

    # Check persona prompt generation
    spoken_persona = get_persona_prompt("spoken")
    grammar_persona = get_persona_prompt("grammar")
    assert "ACTIVE TEACHING PERSONA" in spoken_persona
    assert "主干枝叶·树状分层长难句拆解模型" in grammar_persona


def test_anki_export_preserves_syntax_tree_formatting():
    """Verify that NotesManager.export_anki_tsv converts fenced syntax tree code blocks into <pre> elements."""
    from src.core.notes_manager import NotesManager

    raw_notes = (
        "### 🎯 核心原句与释义 (Sentence)\n"
        "- **原句**: `The ancient library, which was built in the third century, contains priceless manuscripts.`\n"
        "- **中文释义**: 这座建于三世纪的古代图书馆藏有无价手稿。\n\n"
        "### 🧩 句法骨架 (Syntax Structure)\n"
        "- **【核心骨架 (Core Skeleton)】**: `The ancient library contains priceless manuscripts.`\n"
        "- **【多维树状透视图谱 (Syntax Hierarchy Tree)】**:\n"
        "```text\n"
        "[The ancient library] (主干·主语)\n"
        "└─ [which was built in the third century] ← [定语从句 (Level 1)] 回答逻辑问题：哪个？\n"
        "   └─ [in the third century] ← [时间状语 (Level 2)] 回答逻辑问题：何时？\n"
        "[contains] (主干·动词)\n"
        "[priceless manuscripts] (主干·宾语)\n"
        "```\n"
        "- **【顺读意群流 (Sense Groups / Reading Flow)】**:\n"
        "  `[The ancient library]` → `[which was built in the third century]` → `[contains priceless manuscripts]`\n"
    )

    test_note = {
        "id": 1,
        "sentence_text": "The ancient library, which was built in the third century, contains priceless manuscripts.",
        "title": "句研: The ancient library...",
        "notes_markdown": raw_notes,
        "voice_text": "这句话虽然长，但核心主干其实就是一句：The ancient library contains priceless manuscripts。",
        "document_id": "test_doc",
        "page_number": 1,
        "is_mistake": 0,
    }

    tsv_output = NotesManager.export_anki_tsv(notes=[test_note])
    lines = tsv_output.strip().split("\n")
    # Must contain header lines + exactly 1 TSV record line
    assert len(lines) == 4
    record_line = lines[3]
    fields = record_line.split("\t")
    assert len(fields) == 3

    back_field = fields[1]
    # Check that <pre> tag was generated and raw ```text did not leak as broken code
    assert "<pre style=" in back_field
    assert "</pre>" in back_field
    assert "<code></code>`text" not in back_field
    assert "which was built in the third century" in back_field
    assert "【核心骨架 (Core Skeleton)】" in back_field
    assert "【多维树状透视图谱 (Syntax Hierarchy Tree)】" in back_field


@pytest.mark.anyio
async def test_stream_sentence_lecture_parsing_dissection_response():
    """Verify that LlmCoach streams voice sentences and notes containing syntax tree properly."""
    import unittest.mock as mock
    from src.core.llm import LlmCoach

    coach = LlmCoach()

    mock_llm_response = (
        "<voice>The ancient library contains priceless manuscripts. "
        "这句话虽然长，但核心主干其实就是一句：The ancient library contains priceless manuscripts。 "
        "中间的 which was built... 就像积木一样补充修饰。</voice>\n"
        "<notes>\n"
        "### 🧩 句法骨架 (Syntax Structure)\n"
        "- **【核心骨架 (Core Skeleton)】**: `The ancient library contains priceless manuscripts.`\n"
        "- **【多维树状透视图谱 (Syntax Hierarchy Tree)】**:\n"
        "```text\n"
        "[The ancient library]\n"
        "└─ [which was built in the third century] ← [定语从句 (Level 1)]\n"
        "```\n"
        "- **【顺读意群流 (Sense Groups / Reading Flow)】**:\n"
        "  `[The ancient library]` → `[contains manuscripts]`\n"
        "</notes>"
    )

    class FakeDelta:
        def __init__(self, content):
            self.content = content

    class FakeChoice:
        def __init__(self, content):
            self.delta = FakeDelta(content)

    class FakeChunk:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    async def fake_generator():
        # Split into small streaming chunks
        for i in range(0, len(mock_llm_response), 25):
            yield FakeChunk(mock_llm_response[i:i+25])

    class FakeStream:
        def __aiter__(self):
            return fake_generator()
        async def close(self):
            pass

    with mock.patch.object(coach.client.chat.completions, "create", new_callable=mock.AsyncMock, return_value=FakeStream()):
        events = []
        async for ev_type, payload in coach.stream_sentence_lecture(
            "The ancient library, which was built in the third century, contains priceless manuscripts.",
            action="explain"
        ):
            events.append((ev_type, payload))

    voice_sentences = [p for t, p in events if t == "voice_sentence"]
    notes_chunks = [p for t, p in events if t == "notes_delta"]
    done_events = [p for t, p in events if t == "done"]

    assert len(voice_sentences) >= 2
    assert any("核心主干其实就是一句" in s for s in voice_sentences)
    assert len(notes_chunks) > 0
    full_notes = "".join(notes_chunks)
    assert "### 🧩 句法骨架 (Syntax Structure)" in full_notes
    assert "【核心骨架 (Core Skeleton)】" in full_notes
    assert "【多维树状透视图谱 (Syntax Hierarchy Tree)】" in full_notes
    assert "【顺读意群流 (Sense Groups / Reading Flow)】" in full_notes
    assert len(done_events) == 1
