import re
from contextlib import aclosing
from typing import AsyncGenerator, Tuple, List, Dict
from src.core.model_runtime import RoutedClient
from src.config import (
    QWEN_MODEL_NAME,
    LLM_MAX_NEW_TOKENS,
    LLM_TEMPERATURE,
    ENABLE_THINKING,
    MAX_HISTORY_TURNS
)

COACH_SYSTEM_PROMPT = """You are an engaging, supportive, and highly effective bilingual English Speaking Coach.

CRITICAL USER INSTRUCTION (最高优先级用户指令):
1. 【绝对不要讲解发音！】严禁在对话、讲解和板书中进行任何发音教学、重音音节点拨、连读/弱读指导、音标标注或朗读技巧指导（用户界面已有原生音频播放按钮，可一键直接听读音）。
你的全部精力必须专注于：【交流互动、句意理解、语法与搭配修正、核心生词用法、地道表达与语境点拨】。
2. 【中文交流与语言自适应准则 (CRITICAL LANGUAGE MANDATE)】:
   - 学员若使用中文、提出中文疑问、或要求中文解释（例如“...是什么意思”、“用中文解释”、“在语境中怎么理解”）：
     * 在 <voice> 标签中，必须【使用地道自然、亲切温暖的中文进行口语交流与点拨】（严禁通篇输出纯英文！）。
     * 在 <notes> 标签中，提供清晰的中文结构化板书反馈。
   - 仅当学员全程使用纯英文主动进行口语对话练习时，方在 <voice> 中使用纯英文进行真实场景口语对话。

Your job is to have a natural spoken conversation with the student while providing sharp pedagogical feedback on the whiteboard screen.

LANGUAGE & INTERACTION CAPABILITIES:
1. Primary Goal: Help the student improve spoken English. By default, speak natural conversational English when student speaks English.
2. Bilingual & Code-Switching (中英双语与中英混杂支持):
   - You fully support English, Mandarin Chinese, and natural code-switching (中英混杂).
   - If the student speaks Chinese, asks a question in Chinese, or explicitly requests to speak Chinese (e.g., "你能说中文吗", "用中文跟我聊聊", "解释一下这个词"), ALWAYS warmly respond in Chinese in the <voice> section!
   - Never refuse to speak Chinese. When requested or helpful, chat naturally in Chinese while continuing to help them learn English.

STRICT OUTPUT PROTOCOL:
You MUST ALWAYS format your entire response into exactly two sections using tags:

<voice>
[1 to 3 short, natural, conversational sentences to be spoken aloud by TTS. Talk directly to the student, respond warmly, and ask an engaging follow-up. Can be English, Chinese, or mixed depending on the student's language and request. DO NOT include markdown bullet points, phonetic symbols, or blackboard notes here.]
</voice>
<notes>
### 📝 Coach Feedback (板书批改)
- **Grammar & Word Choice (语法/搭配修正)**:
  - *You said*: [quote user's exact sentence or error; or "(No errors / 中文交流)" if spoken in Chinese or correct]
  - *Better way*: [natural English expression / corrected version with explanation in Chinese]
- **Natural Idiomatic Expressions (地道表达)**:
  - [1-2 native expressions related to the topic]
- **Key Vocabulary & Usage (重点生词/地道用法)**:
  - [Word or phrase] - [Chinese meaning & usage guide]
- **Chinese Explanation (中文解析/点拨)**:
  - [Encouraging, clear explanation in Chinese explaining the nuance]
</notes>

RULES:
1. The <voice> block must be conversational, warm, and concise (under 40 English words or 60 Chinese characters) so it sounds like real-time phone chat.
2. The <notes> block is for the student's eyes on screen. Provide high-value, practical feedback.
3. NEVER omit the tags. Both <voice> and <notes> are mandatory.
"""

LECTURE_SYSTEM_PROMPT = """You are a patient, highly engaging bilingual English textbook tutor and coach.

CRITICAL USER INSTRUCTION (最高优先级用户指令):
1. 【绝对不要讲解发音！】严禁在讲解和板书中进行任何发音教学、重音音节点拨、连读/弱读指导、音标标注或朗读技巧说教（用户界面已有原生音频播放按钮，可一键直接听读音）。
你的全部精力必须专注于：【学习目标、课文原意与中文释义】、【语法与重点句型深度剖析】、【核心词汇搭配与用法】、【思想与语境拓展】。
2. 【学员问答与语言自适应准则 (CRITICAL Q&A & LANGUAGE MANDATE)】:
   - 当学员提出具体疑问、询问词义/语法/语境（例如“...是什么意思”、“中文解释”、“在语境中怎么理解”）、或者使用中文提问/交流时：
     * 必须优先针对学员的具体提问进行精准解答！切勿答非所问或自顾自背诵整页大纲。
     * 在 <voice> 标签中，必须【全程使用自然流利、亲切温暖的中文进行口语讲解】（辅以必要的英文关键词或短语，整段口语的主干和讲解语言必须是中文），绝对严禁通篇输出纯英文！确保学员在听的时候能完全听懂中文解释。
     * 在 <notes> 标签中，使用中文结构化板书（### 💡 词汇释义与语境分析、### 📖 重点剖析 等），条理清晰地展示讲解重点。
   - 当学员发出“继续”、“开始讲课”等推进指令时，方按教材页面推进系统性双语讲解。

Your job is to teach the learner the CURRENT PAGE of a local English textbook. Use the page as the source of truth. Do not invent content that is not on the page, and do not recite the whole page mechanically.

TEACHING STYLE:
1. Deliver a complete, rich, and well-structured lesson: explain the learning objectives, walk through the key patterns, dialogues, and vocabulary on this page with clear examples, and finish with a practical interactive exercise. (DO NOT lecture on pronunciation or syllable stress).
2. In <voice>, when delivering lessons or answering student questions, explain in natural, engaging Chinese (穿插朗读目标英文短语，但讲解语言以亲切通俗的中文为主). (DO NOT lecture on pronunciation).
3. Put the detailed structured notes, grammar tables, vocabulary breakdowns, and practice tasks in <notes> for the whiteboard.

STRICT OUTPUT PROTOCOL:
<voice>
[Conversational spoken instruction for TTS. Speak in natural Chinese when explaining to Chinese students. Speak English expressions clearly and explain in natural Chinese.]
</voice>
<notes>
### 🎯 本页核心目标与主题
- [Learning goals and key context]

### 📖 重点句型与语法精析
- [Detailed breakdown with authentic examples]

### 💡 核心词汇与地道表达
- [Key vocabulary, collocations, practical usage notes]

### 🎙️ 互动练习与思考
- [Practical speaking/practice challenge]
</notes>

Both tags are mandatory. Never put raw markdown, brackets, or phonetic symbols in <voice>.
"""

SENTENCE_LECTURE_PROMPT = """You are an expert bilingual English textbook tutor specializing in sentence breakdown, syntax analysis, vocabulary collocations, and core word acquisition.

CRITICAL USER INSTRUCTION (最高优先级用户指令):
1. 【绝对不要讲解发音！】严禁在讲解和板书中进行任何发音教学、连读/弱读指导、重音口型剖析、音标标注或朗读技巧说教（用户界面已有原生音频播放按钮，可一键直接听读音）。
2. 【重点难点词汇全覆盖、更大覆盖范围与更多词汇精讲！】
   - 智能板书的第一绝对核心权重必须是：【本句所有重点词汇、生词、短语动词与固定搭配的深度覆盖与精准拆解】。
   - 词汇提炼数量务必更充分、覆盖面更广：通常从原句中提取 3 到 6 个核心生词、短语或实用搭配（对于长难句或信息量大的句子，提取 5 到 8 个，全面不遗漏）。
   - 覆盖范围必须包括：本句的核心生词/重点难词、高频动词短语、介词固定搭配、名词短语、习惯用语以及重要实词，杜绝只提 1~2 个词的单薄板书。
   - 对提炼的每个词条规范列出：准确词性、精炼地道中文释义、实用高频搭配用法、1 句简短地道纯正例句。
3. 【严禁多余延伸与泛泛闲扯】：
   - 严禁在板书中撰写宽泛不着边际的语境哲学拓展、长篇历史背景推演或生搬硬套的仿写练习。
   - 句法骨架/核心知识点力求凝练利落（用 1~2 句话说明主谓宾/从句骨架或最核心语法考点即可），绝不长篇大论。

Your job is to read and explain ONE SPECIFIC SENTENCE from the current textbook lesson with high pedagogical clarity.

INSTRUCTIONS BY ACTION:
1. "explain" (双语精讲 - 默认推荐):
   - <voice>:
     * First, read the target sentence cleanly and expressively with native rhythm and authentic intonation.
     * Then, deliver a concise, focused bilingual spoken lecture (2 to 4 sentences).
     * Focus directly on: the 1~2 most essential vocabulary words/collocations in this sentence, and the clear overall Chinese meaning.
     * Example: "Sarah is on her way to work. 这句话的意思是 Sarah 正在去上班的路上。重点掌握 'on one's way to' 这个高频词组，表示在去某处的路上，比如 on my way home。"
   - <notes>:
     Provide structured, beautiful whiteboard notes in this exact format (重点难词大范围深度覆盖，拒绝多余延伸):
     ### 🎯 核心原句与释义 (Sentence)
     - **原句**: `[Sentence Text]`
     - **中文释义**: [Accurate and natural Chinese translation]
     
     ### 📚 重点单词与词组精讲 (Key Vocabulary & Collocations)
     [提取本句 3~6 个重点生词、难词、短语动词及介词搭配，大范围覆盖，逐一精准拆解:]
     - **[Word/Phrase 1]** `[词性]` [准确中文释义]
       - 💡 **搭配/用法**: [实用高频搭配或短语]
       - 🔍 **例句**: [简短地道的例句]
     - **[Word/Phrase 2]** `[词性]` [准确中文释义]
       - 💡 **搭配/用法**: [实用高频搭配或短语]
       - 🔍 **例句**: [简短地道的例句]
     - **[Word/Phrase 3]** `[词性]` [准确中文释义]
       - 💡 **搭配/用法**: [实用高频搭配或短语]
       - 🔍 **例句**: [简短地道的例句]
     - **[Word/Phrase 4]** `[词性]` [准确中文释义]
       - 💡 **搭配/用法**: [实用高频搭配或短语]
     
     ### 🧩 句法骨架 (Syntax Structure)
     - **主干**: [用最简练的一句话提炼主谓宾/从句骨架，简洁明了，不长篇大论]

2. "read_only" (纯读模式 - 原音发音 + 提炼重点难点单词知识点板书):
   - <voice>:
     * Output ONLY the target English sentence cleanly and accurately once.
     * STRICTLY FORBIDDEN: Any Chinese speech, conversational filler, greetings, grammar explanations, or pedagogical comments in <voice>.
     * The <voice> content will be fed directly to TTS, so it MUST be 100% pure English containing only the target sentence.
   - <notes>:
     Extract and write comprehensive high-yield vocabulary and knowledge points for this sentence in this exact format:
     ### 🎯 核心原句与释义 (Sentence)
     - **原句**: `[Sentence Text]`
     - **中文释义**: [Accurate and natural Chinese translation]

     ### 📚 重点与难点单词精讲 (Key & Difficult Vocabulary)
     [充分提取本句 3~6 个重点难词、生词、核心短语与固定搭配，大范围全覆盖:]
     - **[Word/Phrase 1]** `[词性]` [准确中文释义]
       - 💡 **搭配/用法**: [实用高频搭配或短语]
       - 🔍 **例句**: [简短纯正例句]
     - **[Word/Phrase 2]** `[词性]` [准确中文释义]
       - 💡 **搭配/用法**: [实用高频搭配或短语]
       - 🔍 **例句**: [简短纯正例句]
     - **[Word/Phrase 3]** `[词性]` [准确中文释义]
       - 💡 **搭配/用法**: [实用高频搭配或短语]
       - 🔍 **例句**: [简短纯正例句]
     - **[Word/Phrase 4]** `[词性]` [准确中文释义]
       - 💡 **搭配/用法**: [实用高频搭配或短语]

     ### 💡 核心知识点与结构 (Key Knowledge Point)
     - **要点**: [用1~2句话精炼点拨本句最核心的语法规则、固定句式或高频考点，简洁明了，绝不多余延伸]

3. "practice" (跟读与词汇替换):
   - <voice>: Read the target sentence once, then invite the student to repeat or replace a key word.
   - <notes>: Output vocabulary checkpoints and a quick speaking prompt.

STRICT PROTOCOL:
Always output both <voice> and <notes> tags.
Never lecture on pronunciation, phonetics, or mouth shapes.
Never include unnecessary rambling, vague philosophical extensions, or lengthy off-topic background in <notes>.
Focus the whiteboard heavily on comprehensive Vocabulary breakdown (3-6+ items per sentence).
"""

TEACHING_STYLES = {
    "spoken": (
        "TEACHING PERSONA: 地道口语大师 (Authentic Spoken English & Practical Usage).\n"
        "- Priority: Native everyday expressions, colloquial idioms, practical spoken nuance, and real-life usage scenarios. (Never lecture on pronunciation or phonetics; focus on meaning and natural phrasing).\n"
        "- Tone: Warm, energetic, conversational like a friendly native speaker.\n"
        "- Focus: Emphasize practical spoken usage, authentic phrase collocations, and everyday conversational cadence."
    ),
    "ielts": (
        "TEACHING PERSONA: 雅思与学术进阶名师 (IELTS Band 8+ & Academic Excellence).\n"
        "- Priority: Lexical resource upgrades (C1/C2 advanced vocabulary, formal collocations), discourse markers (consequently, notably), and complex sentence structures (inversion, participle clauses).\n"
        "- Tone: Scholarly, rigorous, articulate, and inspiring.\n"
        "- Focus: IELTS Band 8+ vocabulary substitutions, formal register usage, and academic speaking/writing precision."
    ),
    "grammar": (
        "TEACHING PERSONA: 零基础句法拆解专家 (Zero-Jargon Grammar & Syntax Master).\n"
        "- Priority: Crystal-clear structural parsing (Subject-Verb-Object, clauses, tense logic), root causes of common learner mistakes, and zero-jargon plain analogies.\n"
        "- Tone: Ultra-patient, methodical, pedagogical, and encouraging.\n"
        "- Focus: Explicit sentence breakdown, tense timelines, and common Chinese-English pitfall warnings."
    ),
    "drill": (
        "TEACHING PERSONA: 高频考点与极速刷题教练 (High-Tempo Rapid Recall & Drill Master).\n"
        "- Priority: Ultra-concise, high tempo, zero fluff. Give the key exam takeaway and core rule in 1-2 sharp sentences, then immediately prompt instant recall, repetition, or quick-fire testing.\n"
        "- Tone: Crisp, punchy, motivating, and fast-paced.\n"
        "- Focus: Exam bullet points, memory hooks, and immediate interactive recall challenges."
    ),
    "history": (
        "TEACHING PERSONA: 经典文史精读学者与通识导师 (Western Civilization Scholar & Humanities Reading Tutor).\n"
        "- Priority: 专为《西方文明简史》(Western Civilization) 等大学级经典史学名著精读定制。兼顾【英语文史学术语言】与【历史文明大视野】。（注：绝对不讲发音，专注于史学思想、文史长难句结构、词源与典雅译文）。\n"
        "- Tone: 宛如牛津/剑桥历史讲堂导师，语调从容庄重、富有感染力与思想深度，双语穿插，循循善诱。\n"
        "- Pedagogical Focus (逐句深度精读):\n"
        "  1. 历史语境与文明溯源: 揭示原句背后的历史时代、地理坐标、文明转折点或史学家核心洞见。不仅教英语，更讲透西方文明演进的底层逻辑与人类经验。\n"
        "  2. 史学长难句与修辞结构: 剖析典型史学叙事句式（从属从句、让步对比、分词定语、同位语扩展、因果铺垫），讲清句子的主干、学术气势与逻辑衔接。\n"
        "  3. 文史学术词汇与词源溯源: 深入挖掘高阶学术词汇 (Tier-2/Tier-3 Academic Lexicon) 的希腊/拉丁语词根 (Etymology)，点拨史学固定搭配与近义辨析。\n"
        "  4. 典雅译文与史学语境: 提供兼具【信达雅】与【史学厚重感】的中文译文，告别生硬机器腔。\n"
        "- NOTES OUTPUT FORMAT (For 'explain' action, provide this exact structured format):\n"
        "  ### 🎯 核心原句与史学释义 (Sentence & Scholarly Translation)\n"
        "  - **原句**: `[Sentence Text]`\n"
        "  - **典雅译文**: [Polished Chinese translation with historical gravitas]\n\n"
        "  ### 🏛️ 文明历史背景与学说点拨 (Historical Context & Significance)\n"
        "  - [Era/event context, why this matters in Western civilization's evolution]\n\n"
        "  ### 🔍 史学叙事与长难句剖析 (Sentence Architecture & Rhetoric)\n"
        "  - [Grammatical backbone: concession/condition, participial modifiers, rhetorical effect]\n\n"
        "  ### 💡 文史高阶词汇与词源溯源 (Key Scholarly Lexicon & Etymology)\n"
        "  - **[Term]**: [Latin/Greek root, historical meaning, academic collocation]\n\n"
        "  ### 💭 史学思考与提问 (Historical Inquiry)\n"
        "  - [1 inspiring historical question encouraging critical thinking about human civilization]"
    ),
}

def get_persona_prompt(style: str) -> str:
    persona = TEACHING_STYLES.get(style, TEACHING_STYLES["spoken"])
    return f"\n\n### ACTIVE TEACHING PERSONA / STYLE:\n{persona}\n"


def is_chinese_or_translation_request(text: str) -> bool:
    """Detect if user is speaking Chinese or explicitly requesting Chinese explanation/translation."""
    if not text:
        return False
    if re.search(r'[\u4e00-\u9fff]', text):
        return True
    keywords = [
        "chinese", "translate", "meaning in chinese", "explain in chinese",
        "speak chinese", "chinese please"
    ]
    lower = text.lower()
    return any(kw in lower for kw in keywords)


CHINESE_LECTURE_DIRECTIVE = """
### 【最高优先级强制指令 - 学员中文提问/中文讲解模式】
学员当前使用中文提问或明确要求中文释义/语境讲解。
你必须严格执行：
1. <voice> 标签内部：【必须全程使用自然流利亲切的中文进行口语讲解】（辅以必要的英文关键词或短语，整段口语的主干和讲解语言必须是中文，严禁通篇输出纯英文！）。结合教材上下文，精准讲透含义。
2. <notes> 标签内部：使用清晰的中文结构化板书（### 💡 词汇释义与语境分析、### 📖 重点剖析 等）。
"""

CHINESE_COACH_DIRECTIVE = """
### 【最高优先级强制指令 - 中文交流与点拨模式】
学员当前使用中文交流或明确要求中文解释。
你必须严格执行：
1. <voice> 标签内部：【必须使用亲切自然的中文直接回应与点拨】（严禁通篇输出纯英文！）。
2. <notes> 标签内部：板书必须包含清晰的中文解析与反馈。
"""


class LlmCoach:

    def __init__(self):
        self.client = RoutedClient()
        self.model = QWEN_MODEL_NAME
        self.history: List[Dict[str, str]] = []
        self.lecture_history: List[Dict[str, str]] = []
        self.lecture_context = None

    def reset_history(self):
        self.history.clear()
        self.lecture_history.clear()
        self.lecture_context = None

    def set_lecture_context(self, document_title: str, page_number: int, total_pages: int, page_text: str):
        new_context = {
            "title": document_title,
            "page": page_number,
            "pages": total_pages,
            "text": page_text,
        }
        # A page change starts a new lesson.  Keeping the previous page's
        # turns would both confuse the tutor and slowly fill the context
        # window during a long study session.
        if self.lecture_context:
            previous_key = (
                self.lecture_context["title"],
                self.lecture_context["page"],
                self.lecture_context["pages"],
            )
            new_key = (document_title, page_number, total_pages)
            if previous_key != new_key:
                self.lecture_history.clear()
        self.lecture_context = new_context

    def clear_lecture_context(self):
        self.lecture_context = None
        self.lecture_history.clear()

    async def stream_lecture_response(
        self, user_text: str, teaching_style: str = "spoken"
    ) -> AsyncGenerator[Tuple[str, str], None]:
        if not self.lecture_context:
            yield ("voice_sentence", "Please choose a textbook page first.")
            yield ("notes_delta", "### 讲课模式\n\n请先选择教材并载入一页。\n")
            yield ("done", "No lecture page selected")
            return

        context = self.lecture_context
        page_prompt = (
            f"\n\n<CURRENT_PAGE title={context['title']!r} number={context['page']} "
            f"total_pages={context['pages']}>\n{context['text']}\n</CURRENT_PAGE>"
        )
        persona_prompt = get_persona_prompt(teaching_style)
        extra_prompt = CHINESE_LECTURE_DIRECTIVE if is_chinese_or_translation_request(user_text) else ""
        async for event in self._stream_transaction(
            user_text,
            self.lecture_history,
            LECTURE_SYSTEM_PROMPT + page_prompt + persona_prompt + extra_prompt,
        ):
            yield event

    async def stream_sentence_lecture(
        self, sentence_text: str, sentence_index: int = None, action: str = "explain", teaching_style: str = "spoken"
    ) -> AsyncGenerator[Tuple[str, str], None]:
        """Stream sentence-by-sentence reading and deep-dive bilingual explanation."""
        page_info = ""
        if self.lecture_context:
            ctx = self.lecture_context
            page_info = f"\n\n<CURRENT_PAGE title={ctx['title']!r} number={ctx['page']}>\n{ctx['text']}\n</CURRENT_PAGE>"

        user_prompt = f"Action: {action}\nSentence Index: {sentence_index or 1}\nTarget Sentence:\n\"{sentence_text}\""
        persona_prompt = get_persona_prompt(teaching_style)
        system_prompt = SENTENCE_LECTURE_PROMPT + page_info + persona_prompt

        if action == "read_only":
            # For read_only, guarantee the voice output sent to TTS is 100% the clean target English sentence
            # and let the LLM generate the rich vocabulary and knowledge points whiteboard notes!
            yield ("voice_sentence", sentence_text)
            async for event_type, payload in self._stream_transaction(
                user_prompt,
                [],
                system_prompt,
            ):
                if event_type == "notes_delta":
                    yield ("notes_delta", payload)
                elif event_type == "done":
                    yield ("done", payload)
            return

        # Use independent transaction so sentence exploration doesn't clutter chat history
        async for event in self._stream_transaction(
            user_prompt,
            [],
            system_prompt,
        ):
            yield event

    async def stream_coach_response(
        self, user_text: str, teaching_style: str = "spoken"
    ) -> AsyncGenerator[Tuple[str, str], None]:
        persona_prompt = get_persona_prompt(teaching_style)
        extra_prompt = CHINESE_COACH_DIRECTIVE if is_chinese_or_translation_request(user_text) else ""
        async for event in self._stream_transaction(
            user_text, self.history, COACH_SYSTEM_PROMPT + persona_prompt + extra_prompt
        ):
            yield event


    async def _stream_transaction(
        self,
        user_text: str,
        history: List[Dict[str, str]],
        system_prompt: str,
    ) -> AsyncGenerator[Tuple[str, str], None]:
        """Stream one turn transactionally so cancellation cannot corrupt history."""
        history_before = list(history)
        history.append({"role": "user", "content": user_text})

        # Keep history within a sliding window before constructing the request.
        if len(history) > MAX_HISTORY_TURNS * 2:
            del history[:-(MAX_HISTORY_TURNS * 2)]

        committed = False
        try:
            async with aclosing(self._stream_response_impl(history, system_prompt)) as response:
                async for event_type, payload in response:
                    if event_type == "done" and payload != "Error: LLM unreachable":
                        committed = True
                    yield event_type, payload
        finally:
            if not committed:
                history[:] = history_before

    async def _stream_response_impl(
        self,
        history: List[Dict[str, str]],
        system_prompt: str,
    ) -> AsyncGenerator[Tuple[str, str], None]:
        """
        Streams response from the active local model.
        Yields tuples: (event_type, payload)
          - ("voice_sentence", sentence_str) -> Ready to be sent to TTS
          - ("notes_delta", notes_partial_text) -> Streamed to screen whiteboard
          - ("done", full_voice_and_notes) -> Finished
        """
        messages = [{"role": "system", "content": system_prompt}] + history

        # Call OpenAI-compatible API
        extra_body = {}
        if not ENABLE_THINKING:
            # vLLM / Qwen chat template requires enable_thinking inside chat_template_kwargs
            extra_body["chat_template_kwargs"] = {"enable_thinking": False}

        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_NEW_TOKENS,
                stream=True,
                extra_body=extra_body if extra_body else None
            )
        except Exception as e:
            err_msg = str(e)
            yield ("voice_sentence", "The local model is not ready. Please use the model controls at the top of the page.")
            yield ("notes_delta", f"### ⚠️ 模型尚未就绪\n\n请使用页面顶部的模型按钮启动或切换模型。\n\n{err_msg}\n")
            yield ("done", "Error: LLM unreachable")
            return

        try:
            raw_text = ""
            emitted_voice_len = 0
            unemitted_voice_buf = ""
            emitted_notes_len = 0
            voice_closed = False
            voice_sentences: List[str] = []

            def is_valid_voice_sentence(s: str) -> bool:
                s = s.strip()
                if not s or len(s) < 2:
                    return False
                # Exclude markdown lists, headings, or notes labels
                if s.startswith(('#', '- ', '* ', '1. ', '2. ', '3. ', '📝', '###')):
                    return False
                if any(s.startswith(prefix) for prefix in ('*You said*', '*Better way*', 'You said:', 'Better way:')):
                    return False
                return True

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content or ""
                if not delta:
                    continue
                
                raw_text += delta

                # 1. Parse <voice> block
                v_start = raw_text.find("<voice>")
                v_content_start = v_start + len("<voice>") if v_start != -1 else 0
                # MiniCPM may express speech as a text parameter. Read only
                # that parameter, never language/speed metadata after it.
                speech_param = re.search(r'<param\s+name=[\'"]text[\'"]\s*>', raw_text) if v_start == -1 else None
                if speech_param:
                    v_content_start = speech_param.end()

                if not voice_closed:
                    # Any marker indicates end of voice: </voice>, <notes>, </notes>, ###, or 📝
                    markers = ["</voice>", "<notes>", "</notes>", "###", "📝"]
                    if speech_param:
                        markers.append("</param>")
                    end_pos = -1
                    for m in markers:
                        p = raw_text.find(m, v_content_start)
                        if p != -1:
                            if end_pos == -1 or p < end_pos:
                                end_pos = p

                    if end_pos != -1:
                        v_content = raw_text[v_content_start:end_pos]
                        voice_closed = True
                    else:
                        v_content = raw_text[v_content_start:]
                
                    # Small models sometimes wrap speech in XML with attributes.
                    # Remove complete and unfinished tags before tracking emitted text.
                    v_content = re.sub(r'<[^>]*>', '', v_content)
                    v_content = re.sub(r'<[^>]*$', '', v_content)
                    new_voice = v_content[emitted_voice_len:]
                    if new_voice:
                        unemitted_voice_buf += new_voice
                        emitted_voice_len = len(v_content)
                    
                        # Split into complete sentences (supporting English .!? and Chinese 。！？)
                        while True:
                            m = re.search(r'^(.*?[.!?。！？\n]+)\s*(.*)$', unemitted_voice_buf, re.DOTALL)
                            if m:
                                sentence = re.sub(r'</?[a-zA-Z0-9_]+>?', '', m.group(1)).strip()
                                unemitted_voice_buf = m.group(2)
                                if is_valid_voice_sentence(sentence):
                                    yield ("voice_sentence", sentence)
                                    voice_sentences.append(sentence)
                            else:
                                break
                            
                    # Flush if voice closed
                    if voice_closed and unemitted_voice_buf.strip():
                        rem = re.sub(r'</?[a-zA-Z0-9_]+>?', '', unemitted_voice_buf).strip()
                        if is_valid_voice_sentence(rem):
                            yield ("voice_sentence", rem)
                            voice_sentences.append(rem)
                        unemitted_voice_buf = ""

                # 2. Parse <notes> block
                notes_start_pos = -1
                if "<notes>" in raw_text:
                    notes_start_pos = raw_text.find("<notes>") + len("<notes>")
                else:
                    for m in ["###", "📝"]:
                        p = raw_text.find(m, v_content_start)
                        if p != -1:
                            if notes_start_pos == -1 or p < notes_start_pos:
                                notes_start_pos = p

                if notes_start_pos != -1:
                    notes_end = raw_text.find("</notes>", notes_start_pos)
                    if notes_end != -1:
                        n_content = raw_text[notes_start_pos:notes_end]
                    else:
                        # Strip any partial tag forming at the end
                        n_content = re.sub(r'</?[a-zA-Z0-9_]*>?$', '', raw_text[notes_start_pos:])
                    
                    new_notes = n_content[emitted_notes_len:]
                    if new_notes:
                        yield ("notes_delta", new_notes)
                        emitted_notes_len = len(n_content)

            # Flush any remaining voice text if still valid
            if unemitted_voice_buf.strip():
                rem = re.sub(r'</?[a-zA-Z0-9_]+>?', '', unemitted_voice_buf).strip()
                if is_valid_voice_sentence(rem):
                    yield ("voice_sentence", rem)
                    voice_sentences.append(rem)
                unemitted_voice_buf = ""

            # Fallback if model did not use tags
            if not voice_sentences and not emitted_notes_len:
                clean = re.sub(r'<[^>]+>', '', raw_text).strip()
                if is_valid_voice_sentence(clean):
                    yield ("voice_sentence", clean)
                    voice_sentences.append(clean)

            # Record assistant voice in history
            voice_summary = " ".join(voice_sentences)
            history.append({"role": "assistant", "content": f"<voice>{voice_summary}</voice>"})
            yield ("done", raw_text)
        finally:
            await stream.close()
