# 🎙️ AI English Coach (本地多模态英语口语私教)

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![ONNX](https://img.shields.io/badge/ONNX_Runtime-1.19+-005CED.svg?logo=onnx&logoColor=white)](https://onnxruntime.ai/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**高性能 · 毫秒级低延迟 · 本地/云端异构多模态 AI 英语口语私教与教材精读系统**

[功能特性](#-核心亮点) • [快速启动](#-快速启动) • [快捷键一览](#-常用键盘快捷键) • [架构说明](#-系统架构) • [配置说明](#-核心配置说明) • [开源协议](#-开源协议)

</div>

---

## 🌟 核心亮点

1. **听觉与视觉解耦（智能板书机制）**：
   - **语音流 (`<voice>`)**：外教以纯正自然音色进行交流引导（1~3句短小精悍），不背课文、不长篇大论。
   - **右侧板书 (`<notes>`)**：语法修正、地道替换表达、重点生词发音及思维点拨实时以富文本白板呈现。
2. **CPU + GPU 极简异构分工（零额外显存占用）**：
   - **GPU**：独占运行本地大语言模型（如 `Qwen3.8-27B`、`MiniCPM` 或 `Ternary-Bonsai-2-27B`），提供极速推理与强大思维逻辑。
   - **CPU**：运行 `Silero VAD` + `SenseVoice-Small ONNX` + `Kokoro-82M ONNX`，整套语音流水线耗时 `<200ms`，**无需任何额外显存**。
3. **Cloud API 智能板书与长难句树状拆解 (Smart Whiteboard & Tree Dissection)**：
   - 原生集成 NVIDIA NIM（`meta/llama-3.2-11b-vision-instruct`）与本地大模型后备协同机制。
   - 自动生成长难句【核心骨架 (Core Skeleton)】、【多维语法树状透视图谱 (Syntax Hierarchy Tree)】、【顺读意群流 (Sense Groups / Reading Flow)】与重点单词短语精讲。
   - 内存 + SQLite (`data/coach_notes.db`) 两级持久化缓存与缓存投毒校验，秒级重用历史板书。
4. **DeepL + NVIDIA NIM 极速云端翻译与生词注解**：
   - 支持 `auto` / `deepl` / `nvidia` / `local` 四种引擎策略，自动识别 DeepL Pro 与 Free (`:fx`) 密钥。
   - 包含中文字符合规校验与自动回退重试，杜绝模型回显原句或幻觉。
   - 提供 `/api/translation/status` 端点实时诊断翻译服务就绪状态。
5. **具身跟打练习与生涯打字里程表 (Inline Shadow Typing & Lifetime Odometer)**：
   - 听完即练，在原文卡片内直接击键跟打，支持实时光标跳动、错误震动与 Combo 击键音效，建立手指肌肉记忆与语音的双向联结。
   - 汽车仪表盘级 Lifetime Typed Words 里程表，支持实时滚动翻字动画、击键反馈与历史练习词频统计。
6. **全屏沉浸精读模式与双屏竖屏协同 (Dual Screen & Portrait Sync)**：
   - 一键开启磨砂黑背景沉浸视图，字体自动放大两倍，支持键盘左右翻页、上下逐句切换。
   - 支持跨显示器联动：主屏全屏板书跟打练习，副屏打开 `/portrait` 竖屏查看教材原版 PDF，状态毫秒级实时同步。
7. **多媒体与自定义教材智能导入**：
   - **YouTube 视频/字幕导入**：支持一键解析 YouTube 链接，抓取中英双语字幕与音视频切片转换为精读教材。
   - **AI 定制练习册生成**：基于自定义主题或自由场景由大模型生成结构化练习册（支持无限制语言学与日常真实语境）。
   - **网页与文档提取**：支持一键导入长篇网页文章并自适应断句切片。
8. **间隔复习系统（Spaced Repetition / FSRS 风格调度）**：
   - 内置真正的记忆曲线调度器：每个复习条目独立维护难度 (difficulty)、记忆稳定性 (stability)、下次到期时间与遗忘次数。
   - 答对则间隔按 FSRS-4.5 公式拉长（默认目标留存率 90%），答错当天降级重学，而不是简单删除条目。
   - 复习队列按条目类型交错混排（单词 / 句子），并提供未来 14 天复习负载预测与「已掌握词条」真实掌握度估算。
   - 复习库自动从已有数据建档：错题本、生词释义缓存、跟打词频记录；功能词（a / of / the 等）会被自动过滤，避免垃圾条目。
   - 只有**回忆型练习**（填空 / 默写 / 听写）的成绩才会进入调度器；对照跟打属于抄写，不计入掌握度。

9. **四档练习模式：对照跟打 → 填空回忆 → 无提示默写 → 听写（Retrieval Practice）**：
   - **对照跟打**：原文全显，适合入门建立肌肉记忆。
   - **填空回忆**：按内容词长度自动挖空，强制回忆关键词。
   - **无提示默写**：只保留每个单词首字母，其余遮蔽，需真正回忆拼写。
   - **听写**：完全遮蔽，自动播放原句音频，仅凭听力写出整句。
   - 任何使用「👁 看答案」的回合都会被降级评分，成绩单如实反映是否真的回忆出来。快捷键 <kbd>Alt</kbd>+<kbd>M</kbd> 循环切换。

10. **音素级发音纠错诊断（Phonetic Error Diagnosis）**：
   - 发音评测不再用「字符串相似度」，而是转换到音素键后比对，因此 `think → sink`、`three → free`、`very → wary`、`ship → sheep`、`and → an` 这类**最小对立对**会被真正判错（旧版会给 90~96 分）。
   - 自动识别中式英语高发系统性错误并给出中文诊断：咬舌音 /θ/ /ð/ 缺失、/v/ 与 /w/ 混淆、/l/ 与 /n/ 混淆、长短元音不分、词尾辅音脱落、-ed 吞音等。
   - 同时**正确接受自然的连读弱读**：`want to → wanna`、`going to → gonna`、`have got to → gotta`、`kind of → kinda` 等不会被判错，因为这是更地道的口语。

12. **云端 API 加固（DeepL / NVIDIA NIM）**：
   - **自动重试 + 指数退避**：429 / 5xx / 超时 / 连接中断会在同一请求内重试（默认最多 3 次，带抖动），
     并尊重服务端 `Retry-After`。此前一次瞬时网络抖动就会**永久丢掉**该句的翻译或板书。
   - **熔断保护**：连续失败 3~4 次后暂停调用该服务（45~60 秒），期间直接走下一个引擎，
     不再让每个句子都白等一次超时；冷却后自动半开探测恢复。
   - **并发限流**：DeepL 最多 6 路、NVIDIA 最多 4 路并发，避免批量翻译时把服务商打到限流。
   - **失败分类**：区分「可重试」（429/5xx/超时）与「永久失败」（401/403/会话额度 456），
     永久失败不浪费重试预算。
   - **超时按用途调整**：连接 5 秒、DeepL 读取 20 秒、NVIDIA 读取 60 秒。
     实测 NVIDIA 生成整块板书需 **10~35 秒（中位约 21 秒）**，旧代码 15 秒超时会丢掉约一半请求。
   - **可观测性**：`/api/translation/status` 返回每个引擎的尝试数、成功率、P50 延迟、重试次数、
     熔断状态与并发峰值——不用再猜这两个 API 到底稳不稳。
   - **慢调用可见**：板书生成时显示实时计时与进度条，20 秒等待不再像卡死。
   - **专用翻译模型快通道**：接入 `nvidia/riva-translate-4b-instruct-v2`（实测 **1.2s** 中位，
     对比通用模型的 17s），DeepL 不可用时由它接管，比通用模型快约 14 倍。
     注意它是纯翻译模型：只接受「Translate to Simplified Chinese: …」前缀（**会忽略 system 消息**，
     否则会回显英文甚至输出挪威语/捷克语），也无法产出 JSON，因此释义与板书仍走通用模型。
   - **后台预生成板书**：NVIDIA 免费但慢（13~38s），因此换句时会自动把**后 2~3 句**排入后台队列
     提前生成。翻到下一句时板书已在缓存，实测 **0.0013s** 命中（未预生成需等 13~38s）。
     后台并发限制 2 路，可通开关 `ai_coach_notes_prefetch` 关闭。

13. **自适应复习与错题净化（Mistake Cleansing）**：
   - 自动沉淀生词库与语法错题本，采用认知重构启发式纠错，完成复练后一键净化消除。

---

## 🏗️ 系统架构

```text
 ┌────────────────┐         WebSocket (Full-Duplex)        ┌─────────────────────────┐
 │                │ ─────────────────────────────────────> │      FastAPI Server     │
 │  Web Audio UI  │   PCM Audio Stream (16kHz Mono)        │                         │
 │ (Browser / App)│                                        │  Silero VAD (ONNX)      │
 │                │ <───────────────────────────────────── │  SenseVoice ASR (ONNX)  │
 └────────────────┘         JSON Notes & Audio Stream      │  Kokoro TTS / Edge-TTS  │
        │                                                  └────────────┬────────────┘
        │ BroadcastChannel                                              │
        ▼                                                               │
 ┌────────────────┐                                ┌────────────────────┼────────────────────┐
 │ Portrait View  │                                │                    │                    │
 │ (Dual Screen)  │                                ▼                    ▼                    ▼
 └────────────────┘                       ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
                                          │ Local LLM Host  │  │   DeepL API     │  │   NVIDIA NIM    │
                                          │ (Qwen / Bonsai) │  │  (Translation)  │  │  (Smart Notes)  │
                                          └─────────────────┘  └─────────────────┘  └─────────────────┘
```

---

## 📂 项目结构

```text
english-coach/
├── .env.example              # 环境变量配置模板
├── start.sh                  # 一键启动脚本
├── launch-app.sh             # 桌面端无缝启动入口（支持 Omarchy / Chromium 应用模式）
├── scripts/
│   └── download_models.sh    # 本地轻量 ONNX 模型自动下载脚本
├── models/                   # 本地 ONNX 模型权重（运行脚本自动下载，不进仓库）
│   ├── silero_vad/           # Silero 人声断句检测 (~630 KB)
│   ├── sensevoice/           # SenseVoice-Small 多语种语音识别 (~230 MB)
│   └── kokoro/               # Kokoro-82M 中英双语本地语音合成 (~360 MB)
├── data/
│   ├── books/                # 内置教材与官方 PDF
│   ├── custom_books/         # 用户自定义教材与生成练习册 (本地存储，不进仓库)
│   ├── media/                # YouTube 与外部音视频多媒体缓存 (本地存储，不进仓库)
│   └── model-runtime/        # 模型运行时日志与状态锁文件
├── src/
│   ├── config.py             # 系统参数与环境变量配置
│   ├── server.py             # FastAPI + WebSocket 全双工流式服务与 REST API
│   ├── core/
│   │   ├── vad.py            # 人声停顿切片检测
│   │   ├── asr.py            # SenseVoice 语音转写
│   │   ├── tts.py            # Edge-TTS (高拟真首选) + Kokoro (离线备用)
│   │   ├── llm.py            # LLM 流式解析与板书分流引擎
│   │   ├── model_runtime.py  # 多模型生命周期管理 (Qwen / MiniCPM / Bonsai)
│   │   ├── notes_generator.py# 云端/本地智能板书与长难句树状拆解引擎
│   │   ├── translation.py    # DeepL & NVIDIA NIM / 本地翻译与生词注解服务
│   │   ├── documents.py      # PDF 教材解析与句子切割引擎
│   │   ├── web_importer.py   # 网页与外部文章内容抓取提取引擎
│   │   ├── youtube_importer.py # YouTube 媒体与字幕智能导入引擎
│   │   ├── ai_practice_generator.py # AI 定制主题练习册生成器
│   │   ├── notes_manager.py  # 板书与错题持久化、Anki 导出管理器
│   │   ├── progress.py       # 学习进度持久化存储
│   │   ├── srs.py            # 间隔复习调度器 (FSRS 风格记忆曲线、队列与负载预测)
│   │   ├── phonetics.py      # 音素键转换与中式英语系统性发音错误诊断
│   │   ├── evaluator.py      # 发音评测 (音素级) 与默写/听写评分
│   │   ├── json_utils.py     # 从大模型输出中稳健提取 JSON (平衡括号扫描)
│   │   ├── api_resilience.py # 云端 API 加固：重试/退避/熔断/并发限流/健康指标
│   │   ├── media_janitor.py  # 媒体目录生命周期管理 (残片回收、孤儿清理、用量统计)
│   │   └── gamification.py   # 连击打字、经验成长、里程表与错题消除机制
│   └── static/
│       ├── index.html        # 主控交互界面（语音流 + 智能板书 + 跟打框 + 里程表）
│       ├── portrait.html     # 竖屏教材阅读与双屏协同视图
│       ├── app.js            # 前端音频流采集、WebSocket 与交互控制
│       ├── model-controls.js # 运行时模型切换面板控制
│       └── style.css         # 现代化暗色主题样式
└── tests/                    # 全量单元测试套件 (413 项全绿)
```

---

## 🚀 快速启动

### 1. 克隆代码与准备环境

```bash
git clone https://github.com/cherish333/english-coach.git
cd english-coach

# 使用 uv 或 python venv 创建虚拟环境
uv venv --python 3.12 .venv
source .venv/bin/activate

# 安装 Python 依赖
pip install -r requirements.txt
```

### 2. 一键下载本地轻量模型（约 600MB）

运行内置的模型下载脚本，自动拉取 Silero VAD、SenseVoice 与 Kokoro 权重：

```bash
bash scripts/download_models.sh
```

### 3. 配置环境变量

复制配置文件模板：

```bash
cp .env.example .env
```

默认配置开箱即用。若使用外部 LLM 接口或云端翻译服务，可在 `.env` 中按需配置：
- **DeepL 翻译**：填入 `DEEPL_API_KEY`（免费版 `:fx` 亦可自动识别）
- **NVIDIA NIM 智能板书**：填入 `NVIDIA_API_KEY`，体验长难句树状剖析与生词精讲
- **本地 LLM**：配置 `QWEN_BASE_URL` 与 `QWEN_MODEL_NAME`（兼容任意 OpenAI 兼容端点）

### 4. 启动服务

```bash
bash start.sh
```

访问控制台：
- **横屏主控台**：`http://localhost:8765`
- **竖屏教材端**：`http://localhost:8765/portrait`

---

## ⌨️ 常用键盘快捷键

| 按键 / 组合键 | 触发场景 | 功能说明 |
| :--- | :--- | :--- |
| <kbd>←</kbd> / <kbd>→</kbd> | 全屏 / 教材模式 | 上一页 / 下一页快速翻书 |
| <kbd>↑</kbd> / <kbd>↓</kbd> | 全屏 / 教材模式 | 上一句 / 下一句快速聚焦 |
| <kbd>Space</kbd> | 非打字状态 | 按下开启麦克风录音，松开发送 |
| <kbd>Tab</kbd> | 任意状态 | 快速聚焦跟打练习区域 |
| <kbd>Enter</kbd> | 打字完成或空闲 | 快速推进到下一句讲解与跟打 |
| <kbd>Esc</kbd> | 全屏模式 | 退出全屏沉浸模式 |

---

## ⚙️ 核心配置说明

编辑 `.env` 文件自定义参数：

| 参数 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `SERVER_PORT` | `8765` | Web 服务端口 |
| `QWEN_BASE_URL` | `http://127.0.0.1:18020/v1` | 本地 Qwen LLM 后端 API 地址（兼容 OpenAI 标准） |
| `QWEN_MODEL_NAME`| `qwen3.8-27b` | 本地模型名称（如 `qwen3.8-27b`, `minicpm` 等） |
| `BONSAI_BASE_URL`| `http://127.0.0.1:18022/v1` | Bonsai 模型端点（可选） |
| `TRANSLATION_ENGINE`| `auto` | 翻译策略：`auto` (优先 DeepL/NVIDIA)、`deepl`、`nvidia`、`local` |
| `DEEPL_API_KEY` | *(可选)* | DeepL API 密钥（支持 Pro 或 Free 后缀 `:fx`） |
| `NVIDIA_API_KEY`| *(可选)* | NVIDIA NIM API 密钥，用于云端极速智能板书与树状拆解 |
| `NVIDIA_BASE_URL`| `https://integrate.api.nvidia.com/v1` | NVIDIA NIM 基础地址 |
| `NVIDIA_MODEL`  | `meta/llama-3.2-11b-vision-instruct` | NVIDIA NIM 通用模型（释义、板书） |
| `NVIDIA_TRANSLATE_MODEL` | `nvidia/riva-translate-4b-instruct-v2` | NVIDIA 专用翻译模型（快通道） |
| `NVIDIA_PREFETCH_ENABLED` | `true` | 后台预生成板书开关 |
| `NVIDIA_PREFETCH_AHEAD` | `2` | 预生成后几句 |
| `ENABLE_THINKING`| `false` | 关闭思考模式以获得最佳交互延迟 |
| `DEFAULT_VOICE`  | `af_maple` | 默认外教音色（支持 Edge-TTS 与本地 Kokoro） |
| `DEFAULT_SPEED`  | `1.0` | 默认语速（前端支持 0.7x ~ 1.3x 动态滑块调节） |
| `VOCABULARY_PDF` | `data/books/vocabulary_in_use.pdf` | 词汇教材路径（支持通过网页端自由上传 PDF） |
| `YOUTUBE_COOKIES_FILE` | `data/youtube_cookies.txt` | YouTube 导入可选 Cookie 路径 |

---

## 🧪 运行测试

项目内置了完整的单元测试与端到端稳定性测试用例（413 项测试通过）：

```bash
.venv/bin/pytest tests/
```

---

## 📄 开源协议

本项目采用 [MIT License](LICENSE) 开源协议。欢迎提交 PR、Issue 或分享你的学习体验！
