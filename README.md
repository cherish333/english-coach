# 🎙️ AI English Coach (本地多模态英语口语私教)

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![ONNX](https://img.shields.io/badge/ONNX_Runtime-1.19+-005CED.svg?logo=onnx&logoColor=white)](https://onnxruntime.ai/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**高性能 · 毫秒级低延迟 · 纯本地多模态 AI 英语口语私教与教材精读系统**

[功能特性](#-核心亮点) • [快速启动](#-快速启动) • [快捷键一览](#-常用键盘快捷键) • [架构说明](#-系统架构) • [开源协议](#-开源协议)

</div>

---

## 🌟 核心亮点

1. **听觉与视觉解耦（智能板书机制）**：
   - **语音流 (`<voice>`)**：外教以纯正自然音色进行交流引导（1~3句短小精悍），不背课文、不长篇大论。
   - **右侧板书 (`<notes>`)**：语法修正、地道替换表达、重点生词发音及思维点拨实时以富文本白板呈现。
2. **CPU + GPU 极简异构分工（零额外显存占用）**：
   - **GPU**：独占运行本地大语言模型（如 `Qwen3.8-27B` 或 `MiniCPM`），提供极速推理与强大思维逻辑。
   - **CPU**：运行 `Silero VAD` + `SenseVoice-Small ONNX` + `Kokoro-82M ONNX`，整套语音流水线耗时 `<200ms`，**无需任何额外显存**。
3. **具身跟打练习（Inline Shadow Typing）**：
   - 听完即练，在原文卡片内直接击键跟打，支持实时光标跳动、错误震动与 Combo 击键音效，建立手指肌肉记忆与语音的双向联结。
4. **全屏沉浸精读模式**：
   - 一键开启磨砂黑背景沉浸视图，字体自动放大两倍，支持键盘左右翻页、上下逐句切换。
5. **双屏与竖屏协同（BroadcastChannel）**：
   - 支持跨显示器联动：主屏全屏板书跟打练习，副屏打开 `/portrait` 竖屏查看教材原版 PDF，状态毫秒级实时同步。
6. **自适应复习与错题净化（Mistake Cleansing）**：
   - 自动沉淀生词库与语法错题本，采用认知重构启发式纠错，完成复练后一键净化消除。

---

## 🏗️ 系统架构

```text
 ┌────────────────┐         WebSocket (Full-Duplex)        ┌─────────────────┐
 │                │ ─────────────────────────────────────> │  FastAPI Server │
 │  Web Audio UI  │   PCM Audio Stream (16kHz Mono)        │                 │
 │ (Browser / App)│                                        │  Silero VAD     │
 │                │ <───────────────────────────────────── │  SenseVoice ASR │
 └────────────────┘         JSON Notes & Audio Stream      └────────┬────────┘
        │                                                           │
        │ BroadcastChannel                                          │
        ▼                                                           ▼
 ┌────────────────┐                                        ┌─────────────────┐
 │ Portrait View  │                                        │ Local LLM Host  │
 │ (Dual Screen)  │                                        │ (vLLM/llama.cpp)│
 └────────────────┘                                        └─────────────────┘
```

---

## 📂 项目结构

```text
english-coach/
├── .env.example          # 配置模板
├── start.sh              # 一键启动脚本
├── launch-app.sh         # 桌面端无缝启动入口（支持 Omarchy / Chromium 应用模式）
├── scripts/
│   └── download_models.sh# 本地轻量 ONNX 模型自动下载脚本
├── models/               # 本地 ONNX 模型权重（运行脚本自动下载，不进仓库）
│   ├── silero_vad/       # Silero 人声断句检测 (~630 KB)
│   ├── sensevoice/       # SenseVoice-Small 多语种语音识别 (~230 MB)
│   └── kokoro/           # Kokoro-82M 中英双语本地语音合成 (~360 MB)
├── src/
│   ├── config.py         # 系统参数与环境变量配置
│   ├── server.py         # FastAPI + WebSocket 全双工流式服务
│   ├── core/
│   │   ├── vad.py        # 人声停顿切片检测
│   │   ├── asr.py        # SenseVoice 语音转写
│   │   ├── tts.py        # Edge-TTS (高拟真首选) + Kokoro (离线备用)
│   │   ├── llm.py        # LLM 流式解析与板书分流引擎
│   │   ├── documents.py  # PDF 教材解析与句子切割引擎
│   │   ├── progress.py   # 学习进度持久化存储
│   │   └── gamification.py # 连击打字、经验成长与错题消除机制
│   └── static/
│       ├── index.html    # 主控交互界面（语音流 + 智能板书 + 跟打框）
│       ├── portrait.html # 竖屏教材阅读视图
│       ├── app.js        # 前端音频流采集、WebSocket 与快捷键驱动
│       └── style.css     # 现代化暗色主题样式
└── tests/                # 全量单元测试套件
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

默认配置开箱即用。若使用外部 LLM 接口，可在 `.env` 中修改 `QWEN_BASE_URL` 与 `QWEN_API_KEY`（兼容任意 OpenAI 兼容端点）。

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
| `QWEN_BASE_URL` | `http://127.0.0.1:18020/v1` | LLM 后端 API 地址（兼容 OpenAI 标准） |
| `QWEN_MODEL_NAME`| `qwen3.8-27b` | 模型名称（如 `qwen3.8-27b`, `minicpm` 等） |
| `ENABLE_THINKING`| `false` | 关闭思考模式以获得最佳交互延迟 |
| `DEFAULT_VOICE`  | `zh-CN-XiaoxiaoNeural` | 默认音色（支持微软云端超拟真或 Kokoro 本地离线）|
| `DEFAULT_SPEED`  | `1.0` | 默认语速（前端支持 0.7x ~ 1.3x 动态滑块调节） |
| `VOCABULARY_PDF` | `data/books/vocabulary_in_use.pdf` | 词汇教材路径（支持通过网页端自由上传 PDF） |

---

## 🧪 运行测试

项目内置了完整的单元测试与端到端稳定性测试用例：

```bash
pytest tests/
```

---

## 📄 开源协议

本项目采用 [MIT License](LICENSE) 开源协议。欢迎提交 PR、Issue 或分享你的学习体验！
