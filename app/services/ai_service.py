"""
AI Service - Tích hợp Gemini API với Rotating Key Manager
Chức năng: Chấm bài tự luận theo rubric, giải thích đáp án
"""
import httpx
import json
import logging
from typing import Optional
from .key_rotator import get_key_manager

logger = logging.getLogger(__name__)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent"

MAX_RETRIES = 3


async def _call_gemini(prompt: str) -> Optional[str]:
    """
    Gọi Gemini API với cơ chế tự động retry + xoay vòng key khi gặp lỗi.
    """
    manager = get_key_manager()

    for attempt in range(MAX_RETRIES):
        key = await manager.get_next_key()
        if not key:
            logger.error("Không còn API key khả dụng!")
            return None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{GEMINI_API_URL}?key={key}",
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {
                            "temperature": 0.2,
                            "maxOutputTokens": 1024,
                        }
                    }
                )

            if response.status_code == 200:
                await manager.report_success(key)
                data = response.json()
                try:
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    logger.error(f"Phản hồi API không đúng định dạng: {data}")
                    return None

            elif response.status_code == 429:
                await manager.report_failure(key, 429)
                logger.warning(f"Key bị rate-limit (429) - thử lại với key khác (lần {attempt+1})...")
                continue

            elif response.status_code in (400, 401, 403):
                await manager.report_failure(key, response.status_code)
                logger.error(f"Lỗi {response.status_code} từ API key - thử key khác...")
                continue

            else:
                logger.error(f"Lỗi API không xác định: {response.status_code} - {response.text}")
                return None

        except httpx.TimeoutException:
            logger.warning(f"Timeout khi gọi Gemini API (lần {attempt+1})")
            continue
        except Exception as e:
            logger.error(f"Lỗi không xác định khi gọi API: {e}")
            return None

    return None


async def grade_essay(
    question_text: str,
    student_answer: str,
    rubric: dict,
    sample_answer: str = ""
) -> dict:
    """
    Chấm bài tự luận theo rubric sử dụng Gemini AI.
    Trả về: điểm số, nhận xét chi tiết, gợi ý cải thiện.
    """
    max_score = rubric.get("max_score", 1.0)
    criteria_text = "\n".join([
        f"  - {c['name']}: {c['points']} điểm"
        for c in rubric.get("criteria", [])
    ])

    prompt = f"""Bạn là giáo viên chấm bài kiểm tra Toán lớp 10 tại Việt Nam. Hãy chấm bài tự luận sau đây một cách nghiêm túc, công bằng và chi tiết.

**CÂU HỎI:**
{question_text}

**ĐÁP ÁN MẪU/GỢI Ý:**
{sample_answer if sample_answer else "Không có đáp án mẫu, hãy tự đánh giá theo logic toán học."}

**TIÊU CHÍ CHẤM ĐIỂM (Tổng: {max_score} điểm):**
{criteria_text}

**BÀI LÀM CỦA HỌC SINH:**
{student_answer if student_answer.strip() else "[Học sinh không làm bài này]"}

Hãy trả lời theo đúng định dạng JSON sau (KHÔNG thêm markdown, chỉ JSON thuần):
{{
  "score": <số điểm đạt được, kiểu float, tối đa {max_score}>,
  "percentage": <phần trăm điểm đạt, kiểu int>,
  "criteria_scores": [
    {{"criterion": "<tên tiêu chí>", "earned": <điểm đạt>, "max": <điểm tối đa>, "comment": "<nhận xét ngắn>"}}
  ],
  "strengths": "<ưu điểm của bài làm (1-2 câu)>",
  "weaknesses": "<điểm cần cải thiện (1-2 câu)>",
  "suggestion": "<gợi ý cụ thể cho học sinh (1-2 câu)>",
  "overall_comment": "<nhận xét tổng thể (2-3 câu)>"
}}"""

    raw_response = await _call_gemini(prompt)

    if not raw_response:
        return {
            "score": 0.0,
            "percentage": 0,
            "criteria_scores": [],
            "strengths": "Không thể kết nối AI chấm điểm.",
            "weaknesses": "Vui lòng kiểm tra lại API key.",
            "suggestion": "Hệ thống sẽ thử lại sau.",
            "overall_comment": "Chưa thể chấm điểm tự luận tự động. Giáo viên cần chấm thủ công.",
            "ai_error": True
        }

    # Parse JSON từ response
    try:
        # Xử lý trường hợp AI trả về có markdown code block
        text = raw_response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        
        result = json.loads(text)
        # Đảm bảo điểm không vượt quá max
        result["score"] = min(float(result.get("score", 0)), max_score)
        result["percentage"] = int(result["score"] / max_score * 100)
        return result
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Không thể parse JSON từ AI response: {e}\nResponse: {raw_response[:500]}")
        return {
            "score": 0.0,
            "percentage": 0,
            "criteria_scores": [],
            "strengths": "",
            "weaknesses": "Lỗi khi phân tích kết quả AI.",
            "suggestion": "",
            "overall_comment": raw_response[:500],
            "ai_error": True
        }


async def check_ai_available() -> bool:
    """Kiểm tra xem có API key khả dụng không."""
    try:
        manager = get_key_manager()
        return manager.active_key_count > 0
    except RuntimeError:
        return False
