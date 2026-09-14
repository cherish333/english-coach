import os
import re
import json
import base64
import asyncio
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
import numpy as np
import datetime
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Body, UploadFile, File, Form, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response


from src.config import (
    SERVER_HOST,
    SERVER_PORT,
    PROJECT_ROOT,
    DEFAULT_VOICE,
    DEFAULT_SPEED,
    SILERO_VAD_MODEL,
    SENSEVOICE_DIR,
    KOKORO_MODEL,
    KOKORO_VOICES,
)
from src.core.vad import VadProcessor
from src.core.asr import AsrProcessor
from src.core.tts import TtsProcessor
from src.core.llm import LlmCoach, TEACHING_STYLES
from src.core.documents import DocumentError, DocumentLibrary
from src.core.notes_manager import NotesManager
from src.core.gamification import GamificationManager
from src.core.evaluator import evaluate_pronunciation
from src.core.translation import translation_service
from src.core.progress import ProgressManager
from src.core.web_importer import process_web_or_text_import
from pydantic import BaseModel
from typing import Literal
from src.core.model_runtime import runtime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("english_coach")

# ASR and TTS models are expensive, so share their immutable model instances.

# VAD state is deliberately *not* shared: it contains per-session audio state.
asr_processor = None
tts_processor = None
processor_init_lock = threading.Lock()

# The application is intentionally single-user.  A reconnecting browser tab
# takes over the session and the previous socket is closed cleanly.
active_websocket = None
active_websocket_lock = asyncio.Lock()

# Bound audio work so cancellation/reconnects cannot create an unbounded number
# of CPU workers.  A single worker per pipeline is sufficient for one user and
# prevents ASR/TTS from oversubscribing the CPU.
asr_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")
tts_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")
document_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="documents")
document_library = DocumentLibrary()

MAX_AUDIO_FRAME_BYTES = 1024 * 1024
MAX_TEXT_INPUT_CHARS = 4000
MIN_TTS_SPEED = 0.5
MAX_TTS_SPEED = 2.0


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await runtime.close()
    # Do not wait for a cancelled inference call forever during shutdown. Any
    # already-running native inference is allowed to finish in its worker.
    asr_executor.shutdown(wait=False, cancel_futures=True)
    tts_executor.shutdown(wait=False, cancel_futures=True)
    document_executor.shutdown(wait=False, cancel_futures=True)
    document_library.close()


app = FastAPI(title="AI English Coach", version="1.0.0", lifespan=lifespan)

@app.middleware("http")
async def add_no_cache_header(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path in ("/", "/portrait"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Static files
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def get_asr():
    global asr_processor
    if asr_processor is None:
        with processor_init_lock:
            if asr_processor is None:
                asr_processor = AsrProcessor()
    return asr_processor

def get_tts():
    global tts_processor
    if tts_processor is None:
        with processor_init_lock:
            if tts_processor is None:
                tts_processor = TtsProcessor(executor=tts_executor)
    return tts_processor


class ModelSelection(BaseModel):
    model: Literal["qwen", "minicpm"]


def require_local_model_control(request: Request):
    # Process control is only available to same-origin loopback browsers.
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "模型控制仅允许本机访问。")
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
        raise HTTPException(403, "无效的本机地址。")
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "禁止跨站启动或停止模型。")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "禁止跨站模型控制。")


@app.get("/api/models/status")
async def model_status():
    return await runtime.status()


@app.post("/api/models/select", status_code=202)
async def select_model(selection: ModelSelection, request: Request):
    require_local_model_control(request)
    try:
        runtime.request(selection.model)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"accepted": True}


@app.post("/api/models/stop", status_code=202)
async def stop_models(request: Request):
    require_local_model_control(request)
    try:
        runtime.request(None)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"accepted": True}


@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/api/health")
async def health_check():
    model_files = {
        "vad": SILERO_VAD_MODEL,
        "asr": SENSEVOICE_DIR / "model.int8.onnx",
        "asr_tokens": SENSEVOICE_DIR / "tokens.txt",
        "tts": KOKORO_MODEL,
        "tts_voices": KOKORO_VOICES,
    }
    files_ready = all(path.is_file() for path in model_files.values())
    model_state = await runtime.status()
    qwen_status = "ready" if model_state["active"] and not model_state["busy"] else "unreachable"
    return JSONResponse({
        "status": "ready" if files_ready and qwen_status == "ready" else "degraded",
        "qwen": "ready" if model_state["active"] == "qwen" and not model_state["busy"] else "unreachable",
        "llm": model_state,
        "vad": "available" if model_files["vad"].is_file() else "missing",
        "asr": "loaded" if asr_processor is not None else "available" if model_files["asr"].is_file() and model_files["asr_tokens"].is_file() else "missing",
        "tts": "loaded" if tts_processor is not None else "available" if model_files["tts"].is_file() and model_files["tts_voices"].is_file() else "missing",
        "active_session": active_websocket is not None,
    })

@app.get("/api/voices")
async def list_voices():
    try:
        tts = get_tts()
        voices = tts.get_available_voices()
        catalog = tts.get_voice_catalog()
        return {"voices": voices, "catalog": catalog, "default": DEFAULT_VOICE, "speed": DEFAULT_SPEED}
    except Exception as e:
        return {"voices": ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "af_maple"], "default": DEFAULT_VOICE, "error": str(e)}


@app.get("/api/tts")
async def tts_endpoint(
    text: str = Query(..., min_length=1, max_length=3000),
    voice: Optional[str] = Query(None),
    speed: Optional[float] = Query(None),
):
    """Synthesize text into speech audio and return as streaming audio response."""
    text_val = text.strip()
    if not text_val:
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    try:
        tts = get_tts()
        if not voice:
            # Default to Edge-TTS: Jenny for English, Xiaoxiao for Chinese/bilingual
            has_chinese = bool(re.search(r'[\u4e00-\u9fff]', text_val))
            target_voice = "zh-CN-XiaoxiaoNeural" if has_chinese else "en-US-JennyNeural"
        elif voice in tts.get_available_voices():
            target_voice = voice
        else:
            target_voice = DEFAULT_VOICE
        target_speed = speed if speed and MIN_TTS_SPEED <= speed <= MAX_TTS_SPEED else DEFAULT_SPEED
        audio_bytes, mime_type = await tts.generate_audio(text_val, target_voice, target_speed)
        if not audio_bytes:
            raise HTTPException(status_code=500, detail="Failed to synthesize audio")
        return Response(
            content=audio_bytes,
            media_type=mime_type,
            headers={
                "Cache-Control": "public, max-age=86400",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("TTS synthesis failed")
        raise HTTPException(status_code=500, detail=f"TTS synthesis failed: {exc}") from exc


@app.post("/api/tts")
async def tts_post_endpoint(payload: dict = Body(...)):
    """POST variant for synthesizing text into speech audio."""
    text_val = str(payload.get("text", "")).strip()
    if not text_val:
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    voice = payload.get("voice")
    speed = payload.get("speed")
    try:
        speed_val = float(speed) if speed is not None else None
    except (ValueError, TypeError):
        speed_val = None
    return await tts_endpoint(text=text_val, voice=voice, speed=speed_val)


@app.get("/api/documents")
async def list_documents():
    loop = asyncio.get_running_loop()
    documents = await loop.run_in_executor(document_executor, document_library.list_documents)
    return {"documents": documents}


@app.post("/api/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(None),
    description: str = Form(None)
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    
    MAX_PDF_BYTES = 50 * 1024 * 1024  # 50MB limit
    content = await file.read(MAX_PDF_BYTES + 1)
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Maximum PDF upload size is 50MB.")
    if len(content) < 100:
        raise HTTPException(status_code=400, detail="The uploaded file is empty or corrupted.")
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Invalid PDF file format header.")

    loop = asyncio.get_running_loop()
    try:
        doc_info = await loop.run_in_executor(
            document_executor,
            document_library.add_custom_document,
            file.filename,
            content,
            title,
            description,
        )
        return {"success": True, "document": doc_info}
    except DocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to upload custom document")
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {exc}")


class UrlImportRequest(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None
    raw_text: Optional[str] = None


@app.post("/api/documents/import-url")
async def import_url_document(req: UrlImportRequest):
    try:
        final_title, pdf_bytes, desc, total_pages, filename = await process_web_or_text_import(
            url=req.url,
            raw_text=req.raw_text,
            custom_title=req.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to fetch or parse web article")
        raise HTTPException(status_code=500, detail=f"抓取网页文章失败: {exc}")

    loop = asyncio.get_running_loop()
    try:
        doc_info = await loop.run_in_executor(
            document_executor,
            document_library.add_custom_document,
            filename,
            pdf_bytes,
            final_title,
            desc,
        )
        return {"success": True, "document": doc_info}
    except DocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to save imported document")
        raise HTTPException(status_code=500, detail=f"保存文章教材失败: {exc}")


@app.delete("/api/documents/{document_id}")
async def delete_document(document_id: str):
    loop = asyncio.get_running_loop()
    try:
        success = await loop.run_in_executor(
            document_executor,
            document_library.delete_custom_document,
            document_id
        )
        return {"success": success}
    except DocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to delete custom document")
        raise HTTPException(status_code=500, detail=str(exc))



@app.get("/api/documents/{document_id}/pages/{page_number}")
async def get_document_page(document_id: str, page_number: int):
    loop = asyncio.get_running_loop()
    try:
        page = await loop.run_in_executor(
            document_executor, document_library.get_page, document_id, page_number
        )
        return page
    except DocumentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to read document page")
        raise HTTPException(status_code=500, detail="Failed to read document page") from exc


@app.get("/api/documents/{document_id}/units")
async def get_document_units(document_id: str):
    loop = asyncio.get_running_loop()
    try:
        units = await loop.run_in_executor(document_executor, document_library.get_units, document_id)
        return {"id": document_id, "units": units}
    except DocumentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to detect document units")
        raise HTTPException(status_code=500, detail="Failed to detect document units") from exc


@app.get("/api/documents/{document_id}/pages/{page_number}/image")
async def get_document_page_image(document_id: str, page_number: int):
    loop = asyncio.get_running_loop()
    try:
        image = await loop.run_in_executor(
            document_executor, document_library.render_page, document_id, page_number
        )
        return Response(content=image, media_type="image/png", headers={"Cache-Control": "no-store"})
    except DocumentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to render document page")
        raise HTTPException(status_code=500, detail="Failed to render document page") from exc


@app.get("/api/documents/{document_id}/pages/{page_number}/sentences")
async def get_document_page_sentences(document_id: str, page_number: int):
    loop = asyncio.get_running_loop()
    try:
        sentences = await loop.run_in_executor(
            document_executor, document_library.get_page_sentences, document_id, page_number
        )
        for s in sentences:
            cached = translation_service.get_cached(s.get("text", ""))
            if cached:
                s["translation"] = cached
        return {"id": document_id, "page": page_number, "sentences": sentences}
    except DocumentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to extract page sentences")
        raise HTTPException(status_code=500, detail="Failed to extract page sentences") from exc


@app.post("/api/translate")
async def translate_endpoint(payload: dict = Body(...)):
    """Translate single or batch sentences to Chinese with caching."""
    try:
        text = payload.get("text")
        texts = payload.get("texts")
        if text is not None:
            if not isinstance(text, str) or not text.strip():
                return {"original": "", "translation": ""}
            translation = await translation_service.translate_sentence(text)
            return {"original": text, "translation": translation}
        elif texts is not None:
            if not isinstance(texts, list):
                raise HTTPException(status_code=400, detail="'texts' must be a list of strings")
            safe_texts = [str(t).strip() for t in texts if isinstance(t, str) and t.strip()]
            translations = await translation_service.translate_batch(safe_texts)
            return {"translations": translations}
        else:
            raise HTTPException(status_code=400, detail="Must provide 'text' string or 'texts' list")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Translation endpoint failed")
        raise HTTPException(status_code=500, detail="Failed to generate translation") from exc


@app.post("/api/sentence/word-glosses")
async def word_glosses_endpoint(payload: dict = Body(...)):
    """Generate or retrieve concise contextual Chinese glosses for each word in a sentence."""
    try:
        sentence = payload.get("sentence") or payload.get("text") or ""
        if not isinstance(sentence, str) or not sentence.strip():
            return {"sentence": "", "glosses": {}}
        glosses = await translation_service.get_sentence_word_glosses(sentence)
        return {"sentence": sentence.strip(), "glosses": glosses}
    except Exception as exc:
        logger.exception("Word glosses endpoint failed")
        raise HTTPException(status_code=500, detail="Failed to generate word glosses") from exc


@app.post("/api/system/shutdown")
async def system_shutdown_endpoint():
    """Cleanly and completely terminate the server and related background processes."""
    def _do_shutdown():
        import time, subprocess
        time.sleep(0.4)
        stop_script = PROJECT_ROOT / "stop-app.sh"
        if stop_script.is_file():
            subprocess.run(["bash", str(stop_script)])
        else:
            subprocess.run(["systemctl", "--user", "stop", "english-coach.service"])

    import threading
    threading.Thread(target=_do_shutdown, daemon=True).start()
    return {"status": "shutting_down", "message": "Application is shutting down"}


@app.get("/api/notes")
async def list_notes_endpoint(limit: int = 50, document_id: str = None, only_mistakes: bool = False):
    try:
        try:
            parsed_limit = int(limit)
        except (ValueError, TypeError):
            parsed_limit = 50
        safe_limit = max(1, min(parsed_limit, 200))
        notes = NotesManager.list_notes(limit=safe_limit, document_id=document_id, only_mistakes=only_mistakes)
        return {"notes": notes}
    except Exception as exc:
        logger.exception("Failed to list notes")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/notes/save")
async def save_note_endpoint(payload: dict = Body(...)):
    try:
        saved = NotesManager.save_note(
            notes_markdown=payload.get("notes_markdown", ""),
            voice_text=payload.get("voice_text", ""),
            document_id=payload.get("document_id"),
            document_title=payload.get("document_title"),
            unit_number=payload.get("unit_number"),
            page_number=payload.get("page_number"),
            sentence_index=payload.get("sentence_index"),
            sentence_text=payload.get("sentence_text"),
            title=payload.get("title"),
            category=payload.get("category", "lecture"),
            is_mistake=1 if payload.get("is_mistake") else 0,
            mistake_type=payload.get("mistake_type", ""),
        )
        return {"success": True, "note": saved}
    except Exception as exc:
        logger.exception("Failed to save note")
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/notes/mistake")
async def save_mistake_endpoint(payload: dict = Body(...)):
    try:
        saved = NotesManager.save_mistake(
            sentence_text=payload.get("sentence_text", ""),
            mistake_type=payload.get("mistake_type", "typing"),
            mistake_detail=payload.get("mistake_detail", ""),
            document_id=payload.get("document_id"),
            document_title=payload.get("document_title"),
            page_number=payload.get("page_number"),
            sentence_index=payload.get("sentence_index"),
            voice_text=payload.get("voice_text", ""),
            notes_markdown=payload.get("notes_markdown", ""),
        )
        return {"success": True, "note": saved}
    except Exception as exc:
        logger.exception("Failed to record mistake")
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/notes/{note_id}")
async def delete_note_endpoint(note_id: int):
    try:
        ok = NotesManager.delete_note(note_id)
        return {"success": ok}
    except Exception as exc:
        logger.exception("Failed to delete note")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/notes/export")
async def export_notes_endpoint(document_id: str = None):
    try:
        md = NotesManager.export_notes_markdown(document_id=document_id)
        filename = f"english_coach_notes_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        return Response(
            content=md,
            media_type="text/markdown",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as exc:
        logger.exception("Failed to export notes")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/notes/export/anki")
async def export_anki_endpoint(document_id: str = None, only_mistakes: bool = False):
    try:
        tsv_data = NotesManager.export_anki_tsv(document_id=document_id, only_mistakes=only_mistakes)
        filename = f"anki_cards_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        return Response(
            content=tsv_data,
            media_type="text/tab-separated-values; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as exc:
        logger.exception("Failed to export Anki flashcards")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/game/status")
async def get_game_status_endpoint():
    try:
        return GamificationManager.get_status()
    except Exception as exc:
        logger.exception("Failed to get game status")
        raise HTTPException(status_code=500, detail=str(exc))


VALID_GAME_ACTIONS = {
    "typing_completed", "shadow_typing", "word_typed", "pronunciation_evaluated",
    "pronunciation_s_rank", "dialogue_sent", "sentence_read", "mistake_cleansed"
}


@app.get("/api/game/odometer")
async def get_game_odometer_endpoint():
    try:
        return GamificationManager.get_odometer_stats()
    except Exception as exc:
        logger.exception("Failed to get odometer stats")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/game/odometer/record")
async def record_odometer_words_endpoint(payload: dict = Body(...)):
    try:
        raw_words = payload.get("words")
        if not raw_words:
            single_word = payload.get("word")
            if single_word:
                raw_words = [single_word]
            else:
                raw_words = []
        elif isinstance(raw_words, str):
            raw_words = [raw_words]
        elif not isinstance(raw_words, list):
            raw_words = []

        document_id = payload.get("document_id")
        sentence_index = payload.get("sentence_index")

        result = GamificationManager.record_words_typed(
            words=raw_words,
            document_id=document_id,
            sentence_index=sentence_index
        )
        return result
    except Exception as exc:
        logger.exception("Failed to record odometer words")
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/game/action")
async def record_game_action_endpoint(payload: dict = Body(...)):
    try:
        action_type = payload.get("action_type", "")
        if not action_type or action_type not in VALID_GAME_ACTIONS:
            raise HTTPException(status_code=400, detail=f"Invalid or unsupported action_type: {action_type}")

        try:
            score = max(0.0, min(float(payload.get("score", 0.0)), 100.0))
        except (ValueError, TypeError):
            score = 0.0

        try:
            combo = max(0, min(int(payload.get("combo", 0)), 500))
        except (ValueError, TypeError):
            combo = 0

        try:
            words_count = max(0, min(int(payload.get("words_count", 0)), 500))
        except (ValueError, TypeError):
            words_count = 0

        sentence_text = payload.get("sentence_text")
        if isinstance(sentence_text, str) and words_count == 0:
            words_count = max(1, len(sentence_text.split()))

        increment_words = bool(payload.get("increment_words", True))
        words_list = payload.get("words_list")
        if not isinstance(words_list, list):
            words_list = None
        document_id = payload.get("document_id")
        sentence_index = payload.get("sentence_index")

        result = GamificationManager.record_action(
            action_type=action_type,
            score=score,
            words_count=words_count,
            combo=combo,
            increment_words=increment_words,
            words_list=words_list,
            document_id=document_id,
            sentence_index=sentence_index,
            sentence_text=sentence_text
        )
        return {"success": True, "result": result}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to record game action")
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/game/mistakes/rush")
async def get_mistakes_rush_endpoint(limit: int = 30):
    try:
        try:
            safe_limit = max(1, min(int(limit), 100))
        except (ValueError, TypeError):
            safe_limit = 30
        mistakes = GamificationManager.get_mistakes_rush(limit=safe_limit)
        return {"mistakes": mistakes}
    except Exception as exc:
        logger.exception("Failed to get mistakes rush")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/game/mistakes/cleanse/{note_id}")
async def cleanse_mistake_endpoint(note_id: int):
    try:
        res = GamificationManager.cleanse_mistake(note_id=note_id)
        return res
    except Exception as exc:
        logger.exception("Failed to cleanse mistake")
        raise HTTPException(status_code=500, detail=str(exc))

@app.get("/api/progress")
async def get_progress_endpoint():
    try:
        return ProgressManager.get_progress()
    except Exception as exc:
        logger.exception("Failed to get learning progress")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/progress")
async def save_progress_endpoint(payload: dict = Body(...)):
    try:
        doc_id = payload.get("document_id")
        page = payload.get("page")
        sentence_index = payload.get("sentence_index")
        teaching_style = payload.get("teaching_style")
        progress = ProgressManager.save_progress(
            document_id=doc_id,
            page=page,
            sentence_index=sentence_index,
            teaching_style=teaching_style,
        )
        return {"success": True, "progress": progress}
    except Exception as exc:
        logger.exception("Failed to save learning progress")
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/portrait")
async def portrait_view():
    portrait_html = PROJECT_ROOT / "src" / "static" / "portrait.html"
    if portrait_html.is_file():
        return FileResponse(portrait_html)
    return FileResponse(PROJECT_ROOT / "src" / "static" / "index.html")


@app.post("/api/window/portrait_to_screen")
async def portrait_to_screen_endpoint():
    """Ensure portrait reader window is moved and focused on the portrait display (DP-1 in Hyprland)."""
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return {"status": "skipped", "reason": "not_in_hyprland"}
    try:
        # Check and place portrait window on DP-1
        for _ in range(4):
            proc = await asyncio.create_subprocess_exec(
                "hyprctl", "repl",
                'for _, w in ipairs(hl.get_windows()) do if string.find(w.title, "竖屏教材阅读器") then hl.dispatch(hl.dsp.focus({ window = "address:" .. w.address })); if w.monitor.name ~= "DP-1" then hl.dispatch(hl.dsp.window.move({ monitor = "DP-1" })) end; return "focused on " .. w.monitor.name end end',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
            res = stdout.decode("utf-8").strip() if stdout else ""
            if "focused on" in res:
                return {"status": "ok", "result": res}
            await asyncio.sleep(0.25)
    except Exception as exc:
        logger.debug(f"Hyprland window positioning: {exc}")
    return {"status": "ok"}



@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    global active_websocket

    await websocket.accept()

    # There is intentionally one active browser session.  This prevents a
    # stale reconnect or a second tab from competing for the same user-facing
    # audio/UI state.
    async with active_websocket_lock:
        previous_websocket = active_websocket
        active_websocket = websocket
    if previous_websocket is not None and previous_websocket is not websocket:
        try:
            await previous_websocket.close(code=4001, reason="Replaced by a newer session")
        except Exception:
            logger.debug("Previous WebSocket was already closed", exc_info=True)

    logger.info("New WebSocket client connected")

    # VAD owns mutable audio state and must be per WebSocket, even though the
    # application is single-user.  A browser reconnect must never inherit
    # half an utterance from the old connection.
    try:
        vad = VadProcessor()
        asr = get_asr()
        tts = get_tts()
        llm = LlmCoach()
    except Exception as exc:
        logger.exception("Failed to initialize audio session")
        try:
            await websocket.close(code=1011, reason="Local audio models failed to initialize")
        finally:
            async with active_websocket_lock:
                if active_websocket is websocket:
                    active_websocket = None
        return

    active_voice = DEFAULT_VOICE
    active_speed = DEFAULT_SPEED
    saved_prog = ProgressManager.get_progress()
    active_teaching_style = saved_prog.get("teaching_style", "spoken")
    active_action_mode = "explain"
    active_target_sentence = ""
    active_sentence_index = None
    active_sentence_action = "explain"
    current_task = None
    turn_generation = 0
    send_lock = asyncio.Lock()
    lecture_context = None
    is_transcribing = False

    def is_turn_busy() -> bool:
        return is_transcribing or bool(current_task and not current_task.done())

    def is_active_connection() -> bool:
        return active_websocket is websocket

    async def send_event(data: dict, turn_id: int = None) -> bool:
        """Serialize all outgoing frames and suppress stale-turn messages."""
        if not is_active_connection():
            return False
        if turn_id is not None:
            data = {**data, "turn_id": turn_id}

        try:
            async with send_lock:
                # The turn can be invalidated while waiting for the send lock.
                if not is_active_connection() or (turn_id is not None and turn_id != turn_generation):
                    return False
                await websocket.send_json(data)
            return True
        except (WebSocketDisconnect, RuntimeError):
            return False

    async def cancel_current_turn():
        """Cancel and drain the current task before starting another turn."""
        nonlocal current_task
        task = current_task
        if task is None:
            return
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Coach task failed while being cancelled")
        if current_task is task:
            current_task = None

    async def set_lecture_context(document_id, page_number, sentence_index=None):
        """Load and activate one textbook page before any lecture turn."""
        nonlocal lecture_context, turn_generation
        try:
            page_number = int(page_number)
        except (TypeError, ValueError):
            await send_event({"type": "error", "message": "Page number must be an integer."})
            return False

        try:
            loop = asyncio.get_running_loop()
            page = await loop.run_in_executor(
                document_executor, document_library.get_page, document_id, page_number
            )
            sentences = await loop.run_in_executor(
                document_executor, document_library.get_page_sentences, document_id, page_number
            )
            for s in sentences:
                cached = translation_service.get_cached(s.get("text", ""))
                if cached:
                    s["translation"] = cached
        except DocumentError as exc:
            await send_event({"type": "error", "message": str(exc)})
            return False
        except Exception:
            logger.exception("Failed to load lecture page")
            await send_event({"type": "error", "message": "Failed to load lecture page."})
            return False

        await cancel_current_turn()
        turn_generation += 1
        page["sentences"] = sentences
        lecture_context = page
        ProgressManager.save_progress(
            document_id=page["id"],
            page=page["page"],
            sentence_index=sentence_index,
        )
        llm.set_lecture_context(
            page["title"], page["page"], page["pages"], page["text"]
        )
        await send_event({
            "type": "lecture_context",
            "document_id": page["id"],
            "title": page["title"],
            "page": page["page"],
            "pages": page["pages"],
            "has_text": page["has_text"],
            "image_url": f"/api/documents/{page['id']}/pages/{page['page']}/image",
            "sentences": sentences,
        }, turn_generation)
        return True

    async def start_turn(
        user_text: str,
        lecture_mode: bool = None,
        sentence_mode: bool = False,
        sentence_index: int = None,
        sentence_action: str = "explain",
    ):
        """Cancel the previous turn, then atomically start a new one."""
        nonlocal current_task, turn_generation, active_target_sentence, active_sentence_index, active_sentence_action
        if not is_active_connection():
            return

        if sentence_mode:
            active_target_sentence = user_text
            active_sentence_index = sentence_index
            active_sentence_action = sentence_action
            stream_method = lambda text: llm.stream_sentence_lecture(
                sentence_text=text,
                sentence_index=sentence_index,
                action=sentence_action,
                teaching_style=active_teaching_style,
            )
        else:
            if lecture_mode is None:
                lecture_mode = lecture_context is not None
            stream_method = (
                (lambda text: llm.stream_lecture_response(text, teaching_style=active_teaching_style))
                if lecture_mode
                else (lambda text: llm.stream_coach_response(text, teaching_style=active_teaching_style))
            )

        await cancel_current_turn()
        turn_generation += 1
        turn_id = turn_generation
        event_payload = {"type": "user_transcript", "text": user_text}
        if sentence_mode:
            event_payload["sentence_index"] = sentence_index
            event_payload["sentence_action"] = sentence_action
        if not await send_event(event_payload, turn_id):
            return

        turn_voice = active_voice
        if sentence_mode and sentence_action == "read_only":
            if not turn_voice or turn_voice in ("af_maple", "bf_vale", "zf_001", "zm_009"):
                turn_voice = "en-US-JennyNeural"

        current_task = asyncio.create_task(
            handle_coach_turn(
                llm,
                tts,
                user_text,
                turn_voice,
                active_speed,
                turn_id,
                is_active_connection,
                lambda: turn_generation == turn_id,
                send_event,
                stream_method,
                target_sentence=user_text if sentence_mode else None,
            )
        )

    async def process_user_speech():
        nonlocal is_transcribing
        speech_audio = vad.get_speech_audio()
        if len(speech_audio) > 16000 * 0.25:  # At least 250ms
            logger.info(f"Processing speech: {len(speech_audio)} samples ({len(speech_audio)/16000:.2f}s)")
            await send_event({"type": "status", "text": "Transcribing speech..."})
            is_transcribing = True
            try:
                loop = asyncio.get_running_loop()
                user_text = await loop.run_in_executor(
                    asr_executor, asr.transcribe, speech_audio, 16000
                )
            except asyncio.CancelledError:
                is_transcribing = False
                raise
            except Exception as exc:
                is_transcribing = False
                logger.exception("ASR failed")
                await send_event({"type": "error", "message": f"Speech recognition failed: {exc}"})
                await send_event({"type": "status", "text": "Listening..."})
                return

            if not is_active_connection():
                is_transcribing = False
                return
            if user_text:
                logger.info(f"User speech transcribed: '{user_text}'")
                if active_action_mode in ("read_only", "continuous_read"):
                    is_transcribing = False
                    logger.info("Ignoring ambient speech in read_only mode")
                    await send_event({"type": "status", "text": "纯读模式（已屏蔽麦克风）"})
                    return
                if active_sentence_action == "practice" and active_target_sentence:
                    eval_result = evaluate_pronunciation(active_target_sentence, user_text)
                    await send_event({"type": "pronunciation_eval", "eval": eval_result}, turn_generation)
                    if eval_result.get("score", 100) < 80:
                        try:
                            NotesManager.save_mistake(
                                sentence_text=active_target_sentence,
                                mistake_type="pronunciation",
                                mistake_detail=f"得分 {eval_result['score']}分，识别: '{user_text}'",
                                document_id=lecture_context.get("id") if lecture_context else None,
                                document_title=lecture_context.get("title") if lecture_context else None,
                                page_number=lecture_context.get("page") if lecture_context else None,
                                sentence_index=active_sentence_index,
                            )
                        except Exception:
                            logger.exception("Failed to auto-save pronunciation mistake")
                try:
                    await start_turn(user_text)
                finally:
                    is_transcribing = False
            else:
                is_transcribing = False
                logger.info("Speech transcribed to empty string")
                if not is_turn_busy():
                    await send_event({"type": "status", "text": "Listening..."})
        else:
            if not is_turn_busy():
                await send_event({"type": "status", "text": "Listening..."})


    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            
            # Handle binary audio frame from microphone (16kHz PCM16)
            if "bytes" in message and message["bytes"]:
                raw_bytes = message["bytes"]
                if len(raw_bytes) > MAX_AUDIO_FRAME_BYTES:
                    await send_event({"type": "error", "message": "Audio frame is too large."})
                    continue
                if len(raw_bytes) % 2:
                    await send_event({"type": "error", "message": "Audio frame must contain complete PCM16 samples."})
                    continue
                samples = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                utterance_done = vad.accept_waveform(samples)
                
                if utterance_done:
                    logger.info("VAD detected end of utterance from audio stream")
                    await process_user_speech()

            # Handle JSON control events
            elif "text" in message and message["text"]:
                try:
                    data = json.loads(message["text"])
                except (TypeError, json.JSONDecodeError):
                    await send_event({"type": "error", "message": "Invalid JSON control message."})
                    continue
                if not isinstance(data, dict):
                    await send_event({"type": "error", "message": "Control message must be a JSON object."})
                    continue
                msg_type = data.get("type")

                if msg_type == "audio_end":
                    logger.info("Received audio_end signal from client")
                    vad.flush()
                    if not is_turn_busy():
                        await process_user_speech()

                elif msg_type == "set_lecture_context":
                    document_id = data.get("document_id", data.get("doc_id"))
                    if not isinstance(document_id, str) or not document_id:
                        await send_event({"type": "error", "message": "A document_id is required."})
                        continue
                    await set_lecture_context(document_id, data.get("page", 1), data.get("sentence_index"))

                elif msg_type == "lecture_request":
                    if lecture_context is None:
                        await send_event({"type": "error", "message": "Please load a textbook page first."})
                        continue
                    action = data.get("action", "explain")
                    commands = {
                        "explain": "请按步骤讲解当前教材页：先说明学习目标，再解释重点内容和例句，最后给我一个简短练习。",
                        "practice": "请只围绕当前教材页带我做练习，一次问我一个问题，先不要直接给答案。",
                        "review": "请复习当前教材页的核心内容，指出最容易犯的错误，并给我一个记忆方法。",
                    }
                    if action == "ask":
                        request = data.get("text", "")
                        if not isinstance(request, str) or not request.strip():
                            await send_event({"type": "error", "message": "Lecture question cannot be empty."})
                            continue
                        request = request.strip()
                    elif action in commands:
                        request = commands[action]
                    else:
                        await send_event({"type": "error", "message": "Unknown lecture action."})
                        continue
                    if len(request) > MAX_TEXT_INPUT_CHARS:
                        await send_event({"type": "error", "message": f"Lecture request is limited to {MAX_TEXT_INPUT_CHARS} characters."})
                        continue
                    await start_turn(request, lecture_mode=True)

                elif msg_type == "lecture_sentence":
                    sentence_val = data.get("text")
                    if not isinstance(sentence_val, str) or not sentence_val.strip():
                        await send_event({"type": "error", "message": "Sentence text cannot be empty and must be a string."})
                        continue
                    sentence_text = sentence_val.strip()
                    try:
                        sentence_index = int(data.get("sentence_index", 1))
                    except (ValueError, TypeError):
                        sentence_index = 1
                    action = str(data.get("action", "explain"))
                    logger.info(f"Triggering lecture sentence: index={sentence_index}, action={action}")
                    if lecture_context:
                        ProgressManager.save_progress(
                            document_id=lecture_context.get("id"),
                            page=lecture_context.get("page"),
                            sentence_index=sentence_index,
                        )
                    await start_turn(
                        sentence_text,
                        sentence_mode=True,
                        sentence_index=sentence_index,
                        sentence_action=action,
                    )

                elif msg_type == "save_note":
                    try:
                        saved = NotesManager.save_note(
                            notes_markdown=data.get("notes_markdown", ""),
                            voice_text=data.get("voice_text", ""),
                            document_id=data.get("document_id") or (lecture_context.get("id") if lecture_context else None),
                            document_title=data.get("document_title") or (lecture_context.get("title") if lecture_context else None),
                            unit_number=data.get("unit_number"),
                            page_number=data.get("page_number") or (lecture_context.get("page") if lecture_context else None),
                            sentence_index=data.get("sentence_index"),
                            sentence_text=data.get("sentence_text"),
                            title=data.get("title"),
                            category=data.get("category", "lecture"),
                        )
                        await send_event({"type": "note_saved", "note": saved})
                    except Exception as exc:
                        await send_event({"type": "error", "message": f"Failed to save note: {exc}"})

                elif msg_type == "clear_lecture_context":
                    logger.info("Leaving lecture mode")
                    turn_generation += 1
                    await cancel_current_turn()
                    lecture_context = None
                    llm.clear_lecture_context()
                    await send_event({"type": "lecture_context", "active": False}, turn_generation)

                elif msg_type == "text_input":
                    text_value = data.get("text", "")
                    if not isinstance(text_value, str):
                        await send_event({"type": "error", "message": "Text input must be a string."})
                        continue
                    text = text_value.strip()
                    if text:
                        logger.info(f"Received text input: '{text}'")
                        if len(text) > MAX_TEXT_INPUT_CHARS:
                            await send_event({"type": "error", "message": f"Text input is limited to {MAX_TEXT_INPUT_CHARS} characters."})
                            continue
                        await start_turn(text)

                elif msg_type == "interrupt":
                    logger.info("Received interrupt signal")
                    turn_generation += 1
                    await cancel_current_turn()
                    vad.reset()
                    await send_event({"type": "interrupted"}, turn_generation)

                elif msg_type == "reset_session":
                    logger.info("Resetting conversation session")
                    turn_generation += 1
                    await cancel_current_turn()
                    llm.reset_history()
                    vad.reset()
                    lecture_context = None
                    await send_event({"type": "session_reset"}, turn_generation)

                elif msg_type == "update_settings":
                    requested_voice = data.get("voice", active_voice)
                    requested_speed = data.get("speed", active_speed)
                    if not isinstance(requested_voice, str):
                        await send_event({"type": "error", "message": "Voice must be a string."})
                        continue
                    if requested_voice not in tts.get_available_voices():
                        await send_event({"type": "error", "message": f"Unknown voice: {requested_voice}"})
                        continue
                    try:
                        requested_speed = float(requested_speed)
                    except (TypeError, ValueError):
                        await send_event({"type": "error", "message": "Speed must be a number."})
                        continue
                    if not math.isfinite(requested_speed) or not MIN_TTS_SPEED <= requested_speed <= MAX_TTS_SPEED:
                        await send_event({"type": "error", "message": f"Speed must be between {MIN_TTS_SPEED} and {MAX_TTS_SPEED}."})
                        continue
                    active_voice = requested_voice
                    active_speed = requested_speed
                    logger.info(f"Updated settings: voice={active_voice}, speed={active_speed}")
                    await send_event({"type": "settings_updated", "voice": active_voice, "speed": active_speed})

                elif msg_type == "set_teaching_style":
                    style = data.get("style", "spoken")
                    if style in TEACHING_STYLES:
                        active_teaching_style = style
                        ProgressManager.save_progress(teaching_style=active_teaching_style)
                        logger.info(f"Updated teaching style: {active_teaching_style}")
                        await send_event({"type": "teaching_style_updated", "style": active_teaching_style})

                elif msg_type == "set_action_mode":
                    mode = str(data.get("mode", "explain"))
                    active_action_mode = mode
                    logger.info(f"Updated active action mode: {active_action_mode}")
                    await send_event({"type": "action_mode_updated", "mode": active_action_mode})

                elif msg_type == "evaluate_speech":
                    raw_target = data.get("target_text")
                    target_txt = str(raw_target) if isinstance(raw_target, str) and raw_target else (active_target_sentence or "")
                    raw_spoken = data.get("spoken_text")
                    spoken_txt = str(raw_spoken) if isinstance(raw_spoken, str) else ""
                    eval_result = evaluate_pronunciation(target_txt, spoken_txt)
                    await send_event({"type": "pronunciation_eval", "eval": eval_result}, turn_generation)


    except (WebSocketDisconnect, RuntimeError):
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        await cancel_current_turn()
        async with active_websocket_lock:
            if active_websocket is websocket:
                active_websocket = None

async def handle_coach_turn(
    llm: LlmCoach,
    tts: TtsProcessor,
    user_text: str,
    voice: str,
    speed: float,
    turn_id: int,
    is_active_connection,
    is_current_turn,
    send_event,
    stream_method,
    target_sentence: Optional[str] = None,
):
    """Streams Qwen output, splits <voice> for TTS, and pushes <notes> to the whiteboard."""
    try:
        if not is_active_connection() or not is_current_turn():
            return
        await send_event({"type": "status", "text": "Coach is thinking..."}, turn_id)
        logger.info(f"Streaming coach turn for: '{user_text}'")

        full_voice_sentences = []
        full_notes_deltas = []

        async for event_type, payload in stream_method(user_text):
            if not is_active_connection() or not is_current_turn():
                return
            if event_type == "voice_sentence":
                sentence = payload.strip()
                if sentence:
                    full_voice_sentences.append(sentence)
                    logger.info(f"Synthesizing voice chunk: '{sentence}'")
                    audio_bytes, mime_type = await tts.generate_audio(sentence, voice, speed)
                    if not is_active_connection() or not is_current_turn():
                        return
                    if audio_bytes:
                        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                        await send_event({
                            "type": "voice_audio",
                            "sentence": sentence,
                            "audio": audio_b64,
                            "mime_type": mime_type,
                        }, turn_id)
            elif event_type == "notes_delta":
                full_notes_deltas.append(payload)
                await send_event({
                    "type": "notes_delta",
                    "delta": payload
                }, turn_id)
            elif event_type == "done":
                logger.info("Coach turn completed.")
                turn_complete_payload = {
                    "type": "turn_complete",
                    "voice_text": " ".join(full_voice_sentences),
                    "notes_markdown": "".join(full_notes_deltas),
                }
                if target_sentence:
                    turn_complete_payload["sentence_text"] = target_sentence
                await send_event(turn_complete_payload, turn_id)
                await send_event({"type": "status", "text": "Listening..."}, turn_id)

    except asyncio.CancelledError:
        logger.info("Coach turn cancelled (interrupted).")
        raise
    except Exception as e:
        logger.error(f"Error during coach turn: {e}", exc_info=True)
        if is_active_connection() and is_current_turn():
            await send_event({"type": "error", "message": str(e)}, turn_id)
            await send_event({"type": "status", "text": "Listening..."}, turn_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.server:app", host=SERVER_HOST, port=SERVER_PORT, reload=False)
