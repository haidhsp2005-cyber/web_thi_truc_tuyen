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

"""
AI Service - Tích hợp Gemini API với Rotating Key Manager
Chức năng: Chấm bài tự luận đa môn chuẩn GDPT 2026 (Toán, Ngữ văn, KHTN, Sử, Địa, Ngoại ngữ...)
"""
import httpx
import json
import logging
import re
from typing import Optional, List
from .key_rotator import get_key_manager

logger = logging.getLogger(__name__)

# Danh sách model Gemini ưu tiên (tự động thử model tiếp theo nếu model trước không hỗ trợ)
CANDIDATE_MODELS = [
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-2.0-flash",
]

MAX_RETRIES = 3


async def _call_gemini(prompt: str) -> Optional[str]:
    """
    Gọi Gemini API với cơ chế tự động retry + xoay vòng key khi gặp lỗi.
    Hỗ trợ luân chuyển model tự động nếu model đang gọi bị lỗi hoặc giới hạn.
    """
    manager = get_key_manager()

    for attempt in range(MAX_RETRIES):
        key = await manager.get_next_key()
        if not key:
            logger.error("Không còn API key khả dụng!")
            return None

        # Thử lần lượt các model phổ biến
        for model_name in CANDIDATE_MODELS:
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
            try:
                async with httpx.AsyncClient(timeout=45.0) as client:
                    response = await client.post(
                        endpoint,
                        json={
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {
                                "temperature": 0.2,
                                "maxOutputTokens": 1500,
                            }
                        }
                    )

                if response.status_code == 200:
                    await manager.report_success(key)
                    data = response.json()
                    try:
                        return data["candidates"][0]["content"]["parts"][0]["text"]
                    except (KeyError, IndexError):
                        logger.error(f"Phản hồi API không đúng định dạng ({model_name}): {data}")
                        continue

                elif response.status_code == 429:
                    await manager.report_failure(key, 429)
                    logger.warning(f"Key bị rate-limit (429) trên model {model_name} - thử key khác...")
                    break  # chuyển sang key tiếp theo

                elif response.status_code in (400, 401, 403):
                    await manager.report_failure(key, response.status_code)
                    logger.error(f"Lỗi {response.status_code} từ API key trên {model_name} - thử key khác...")
                    break  # chuyển sang key tiếp theo

                elif response.status_code == 404:
                    # Model không khả dụng với key này, thử model dự phòng tiếp theo
                    continue

                else:
                    logger.warning(f"Model {model_name} trả mã {response.status_code}: {response.text[:200]}")
                    continue

            except httpx.TimeoutException:
                logger.warning(f"Timeout (45s) khi gọi Gemini API với model {model_name}")
                continue
            except Exception as e:
                logger.error(f"Lỗi kết nối khi gọi model {model_name}: {e}")
                continue

    return None


def _detect_subject_type(subject: str) -> str:
    """Phân loại nhóm môn học để áp dụng hướng dẫn chấm sư phạm phù hợp."""
    s = (subject or "").strip().lower()
    if any(k in s for k in ("văn", "ngữ văn", "tiếng việt", "văn học")):
        return "van_hoc"
    if any(k in s for k in ("toán", "vật lí", "vật lý", "hóa", "sinh", "khtn", "khoa học tự nhiên", "tin học", "tin")):
        return "tu_nhien"
    if any(k in s for k in ("sử", "lịch sử", "địa", "địa lí", "địa lý", "gdcd", "công dân", "kinh tế và pháp luật", "gk&pl", "triết")):
        return "xa_hoi"
    if any(k in s for k in ("anh", "tiếng anh", "ngoại ngữ", "pháp", "trung")):
        return "ngoai_ngu"
    return "chung"


async def grade_essay(
    question_text: str,
    student_answer: str,
    rubric: dict,
    sample_answer: str = "",
    subject: str = "Toán học",
    grade: str = "12"
) -> dict:
    """
    Chấm bài tự luận đa môn học (Toán, Văn, KHTN, Sử, Địa, Ngoại ngữ...)
    chuẩn Chương trình Giáo dục Phổ thông (GDPT 2026).
    Trả về: điểm số, phần trăm, điểm từng tiêu chí, ưu điểm, nhược điểm, gợi ý và nhận xét chi tiết.
    """
    max_score = float(rubric.get("max_score", 1.0))
    if max_score <= 0:
        max_score = 1.0

    subject_clean = (subject or "Toán học").strip()
    grade_clean = f"lớp {grade}" if str(grade).strip() else "THPT/THCS"
    subj_type = _detect_subject_type(subject_clean)

    # Nếu có tiêu chí sẵn từ đề thi
    criteria_list = rubric.get("criteria", [])
    if criteria_list:
        criteria_text = "\n".join([
            f"  - {c.get('name', 'Tiêu chí')}: {c.get('points', 0)} điểm ({c.get('desc', '')})"
            for c in criteria_list
        ])
    else:
        criteria_text = ""

    # Hướng dẫn chuyên môn sư phạm riêng biệt theo từng phân môn
    if subj_type == "van_hoc":
        pedagogical_guide = f"""**HƯỚNG DẪN CHẤM MÔN NGỮ VĂN ({grade_clean}):**
1. Đọc hiểu / Nghị luận xã hội / Nghị luận văn học:
   - Trả lời đúng trọng tâm câu hỏi; nắm vững vấn đề nghị luận; xác định đúng ngữ liệu và thông điệp.
   - Triển khai luận điểm rõ ràng, lập luận chặt chẽ, lí lẽ thuyết phục; dẫn chứng thực tế hoặc văn học xác đáng.
   - Bố cục đoạn/bài mạch lạc, diễn đạt trong sáng, giàu hình ảnh, cảm xúc chân thành, liên hệ mở rộng sâu sắc.
   - Không mắc lỗi chính tả, dùng từ, ngữ pháp tiếng Việt.
2. NGUYÊN TẮC CÔNG TÂM & KHUYẾN KHÍCH SÁNG TẠO:
   - TUYỆT ĐỐI KHÔNG bắt bẻ học sinh phải dùng từ ngữ rập khuôn giống hệt đáp án mẫu.
   - Trân trọng suy nghĩ độc lập, cảm nhận chân thực và góc nhìn mới mẻ nếu hợp lý và có tính nhân văn.
   - Đánh giá theo thang điểm: Xuất sắc (90-100% điểm), Khá (70-85%), Trung bình (50-65%), Chưa đạt (<50%)."""

    elif subj_type == "tu_nhien":
        pedagogical_guide = f"""**HƯỚNG DẪN CHẤM CÁC MÔN KHOA HỌC TỰ NHIÊN / TOÁN ({grade_clean}):**
1. Chấm theo từng bước giải (Step-by-step):
   - Đặt điều kiện, lập luận bài toán, chọn hệ quy chiếu / viết phương trình / mô hình đúng hướng: cho điểm phần đầu.
   - Các bước biến đổi trung gian, áp dụng định lý / định luật / công thức khoa học chính xác: cho điểm phần thân.
   - Kết quả cuối cùng chính xác, có đơn vị đo lường chuẩn và kết luận: cho điểm trọn vẹn.
2. TÔN TRỌNG CÁCH GIẢI KHÁC NHAU:
   - Nếu học sinh giải bằng phương pháp khác đáp án mẫu nhưng đúng bản chất khoa học và ra đáp số chính xác, VẪN CHO ĐIỂM TỐI ĐA.
   - Nếu tính toán nhầm số liệu ở bước cuối nhưng các bước lập luận trên đúng, vẫn cho phần lớn số điểm của các bước trên."""

    elif subj_type == "xa_hoi":
        pedagogical_guide = f"""**HƯỚNG DẪN CHẤM CÁC MÔN KHOA HỌC XÃ HỘI ({subject_clean} {grade_clean}):**
1. Đánh giá kiến thức cốt lõi: Nêu đúng sự kiện, nhân vật, mốc thời gian, vị trí địa lý hoặc khái niệm cơ bản.
2. Khả năng phân tích & Liên hệ: Giải thích được nguyên nhân, ý nghĩa, bài học kinh nghiệm hoặc liên hệ thực tiễn bản thân/địa phương.
3. Trình bày khoa học, luận điểm rõ ràng, lời văn chuẩn mực."""

    elif subj_type == "ngoai_ngu":
        pedagogical_guide = f"""**HƯỚNG DẪN CHẤM MÔN NGOẠI NGỮ ({subject_clean} {grade_clean}):**
1. Task Achievement: Trả lời đầy đủ các yêu cầu của đề bài.
2. Coherence & Cohesion: Tính liên kết câu, đoạn văn mạch lạc, sử dụng từ nối hợp lý.
3. Lexical Resource: Từ vựng phong phú, sử dụng đúng ngữ cảnh.
4. Grammatical Range & Accuracy: Đa dạng cấu trúc ngữ pháp, ít lỗi sai ảnh hưởng đến ý nghĩa."""

    else:
        pedagogical_guide = f"""**HƯỚNG DẪN CHẤM MÔN {subject_clean.upper()} ({grade_clean}):**
1. Trả lời đúng trọng tâm câu hỏi, kiến thức chính xác, lập luận logic.
2. Chấm linh hoạt theo từng ý đúng của học sinh, khuyến khích sự hiểu bài và vận dụng thực tiễn."""

    # Nếu không có rubric thì bổ sung tiêu chí mặc định linh hoạt
    if not criteria_text:
        if subj_type == "van_hoc":
            criteria_text = f"""- Đúng yêu cầu, xác định trúng vấn đề nghị luận / câu hỏi đọc hiểu: {max_score * 0.4:.2f} điểm
- Luận điểm rõ ràng, dẫn chứng & lí lẽ thuyết phục, cảm thụ sâu sắc: {max_score * 0.4:.2f} điểm
- Diễn đạt trong sáng, cảm xúc chân thực, đúng chính tả & sáng tạo: {max_score * 0.2:.2f} điểm"""
        elif subj_type == "tu_nhien":
            criteria_text = f"""- Lập luận, phương pháp và công thức đúng hướng: {max_score * 0.4:.2f} điểm
- Các bước biến đổi, tính toán trung gian chính xác: {max_score * 0.4:.2f} điểm
- Kết quả chính xác, đúng đơn vị và kết luận rõ ràng: {max_score * 0.2:.2f} điểm"""
        else:
            criteria_text = f"""- Trả lời đúng trọng tâm, nắm vững kiến thức cơ bản: {max_score * 0.6:.2f} điểm
- Phân tích, liên hệ thực tiễn và trình bày mạch lạc: {max_score * 0.4:.2f} điểm"""

    prompt = f"""Bạn là giáo viên giàu kinh nghiệm chấm bài kiểm tra môn {subject_clean} ({grade_clean}) tại trường THPT/THCS ở Việt Nam.
Hãy đọc kỹ câu hỏi, hướng dẫn chấm và bài làm của học sinh dưới đây để chấm điểm một cách công tâm, chuẩn xác và có tính sư phạm khích lệ cao.

{pedagogical_guide}

**ĐỀ BÀI CÂU HỎI ({subject_clean}):**
{question_text}

**ĐÁP ÁN MẪU / HƯỚNG DẪN CHẤM GỢI Ý:**
{sample_answer if sample_answer else "Không có hướng dẫn chi tiết. Hãy tự đánh giá theo chuẩn kiến thức khoa học và thẩm mỹ của môn học."}

**THANG ĐIỂM & TIÊU CHÍ (TỔNG: {max_score} ĐIỂM):**
{criteria_text}

**BÀI LÀM CỦA HỌC SINH:**
{student_answer if student_answer.strip() else "[Học sinh không làm bài này - bài làm để trống]"}

**QUY TẮC PHẢN HỒI:**
- Nếu học sinh để trống hoặc không trả lời: Cho 0.0 điểm.
- Trả về DUY NHẤT một đối tượng JSON hợp lệ (không kèm theo văn bản giải thích nào ngoài cặp ngoặc nhọn {{ }}):
{{
  "score": <số điểm đạt được, kiểu float từ 0.0 đến {max_score}>,
  "percentage": <phần trăm điểm từ 0 đến 100, kiểu int>,
  "criteria_scores": [
    {{"criterion": "<tên tiêu chí/bước>", "earned": <điểm đạt float>, "max": <điểm tối đa float>, "comment": "<nhận xét ngắn bước này>"}}
  ],
  "strengths": "<ưu điểm nổi bật của bài làm, 1-2 câu sư phạm>",
  "weaknesses": "<điểm cần hoàn thiện hoặc lưu ý sửa đổi nếu có, 1-2 câu>",
  "suggestion": "<lời khuyên cụ thể giúp học sinh tiến bộ trong những bài sau, 1-2 câu>",
  "overall_comment": "<nhận xét tổng thể khách quan, chân thành và khích lệ, 2-3 câu>"
}}"""

    raw_response = await _call_gemini(prompt)

    if not raw_response:
        return {
            "score": 0.0,
            "percentage": 0,
            "criteria_scores": [],
            "strengths": "Không thể kết nối AI chấm điểm.",
            "weaknesses": "Vui lòng kiểm tra lại API key trong cấu hình Admin.",
            "suggestion": "Giáo viên có thể chấm trực tiếp hoặc bấm Chấm lại bằng AI.",
            "overall_comment": "Hệ thống chưa thể chấm tự luận tự động lúc này. Thầy/Cô có thể vào trang Quản lý để xem bài và cho điểm trực tiếp.",
            "ai_error": True,
            "graded_by": "pending"
        }

    # Bóc tách JSON siêu bền bằng Regex
    try:
        clean_text = raw_response.strip()
        # Tìm khối { ... } đầu tiên và bao quát nhất
        match = re.search(r'\{[\s\S]*\}', clean_text)
        if match:
            clean_text = match.group(0)

        result = json.loads(clean_text)
        
        # Đảm bảo điểm số hợp lệ
        raw_sc = float(result.get("score", 0))
        earned_score = max(0.0, min(raw_sc, max_score))
        earned_score = round(earned_score, 2)

        result["score"] = earned_score
        result["percentage"] = int(round((earned_score / max_score) * 100)) if max_score > 0 else 0
        result["max_score"] = max_score
        result["graded_by"] = "ai"
        result["ai_error"] = False

        if "overall_comment" not in result or not result["overall_comment"]:
            result["overall_comment"] = "Bài làm đã được AI ghi nhận và đánh giá."

        return result

    except Exception as e:
        logger.error(f"Lỗi phân tích JSON từ AI response: {e}\nResponse: {raw_response[:500]}")
        return {
            "score": 0.0,
            "percentage": 0,
            "criteria_scores": [],
            "strengths": "",
            "weaknesses": "Lỗi định dạng khi phân tích kết quả AI.",
            "suggestion": "Giáo viên có thể xem nguyên văn bài làm và chấm trực tiếp.",
            "overall_comment": raw_response[:400] if raw_response else "Lỗi đọc kết quả AI.",
            "ai_error": True,
            "graded_by": "pending"
        }


async def check_ai_available() -> bool:
    """Kiểm tra xem có API key khả dụng không."""
    try:
        manager = get_key_manager()
        return manager.active_key_count > 0
    except RuntimeError:
        return False



async def check_ai_available() -> bool:
    """Kiểm tra xem có API key khả dụng không."""
    try:
        manager = get_key_manager()
        return manager.active_key_count > 0
    except RuntimeError:
        return False
