"""
API Routers - Exam endpoints
"""
import uuid
import logging
from datetime import datetime, timezone, timedelta

VIETNAM_TZ = timezone(timedelta(hours=7))
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from typing import Optional
import io
import re
import unicodedata
import urllib.parse

from ..models import StudentInfo, ExamSubmission
from ..database import (
    load_exam, save_submission, get_submission, get_all_submissions, heal_exam_data, is_exam_published
)
from ..services.grading_service import (
    grade_part1, grade_part2, grade_part3, calculate_total_score
)
from ..services.ai_service import grade_essay, check_ai_available
from ..services.export_service import export_class_results_excel, export_result_html

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/exam", tags=["exam"])


@router.get("/current")
async def get_current_exam(
    request: Request,
    exam_id: str = "exam_001",
    student_name: str = "",
    student_class: str = "",
    seed: str = ""
):
    """Lấy nội dung đề thi (đã ẩn đáp án). Nếu là đề ngân hàng thì bốc ngẫu nhiên riêng cho từng học sinh."""
    exam = load_exam(exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Không tìm thấy đề thi!")
    
    if not is_exam_published(exam.get("is_published")):
        raise HTTPException(
            status_code=403, 
            detail="Đề thi này đang ở trạng thái CHƯA XUẤT BẢN. Không thể vào làm bài thi!"
        )

    heal_exam_data(exam)

    # Nếu là đề ngân hàng, bốc ngẫu nhiên theo hạt giống (seed) của học sinh
    from ..services.bank_service import is_bank_exam, generate_student_exam_from_bank, get_student_seed
    if is_bank_exam(exam):
        numeric_seed = None
        if seed:
            try:
                numeric_seed = int(seed)
            except ValueError:
                pass
        if numeric_seed is None:
            numeric_seed = get_student_seed(student_name, student_class, exam_id, client_seed=seed)
        exam = generate_student_exam_from_bank(exam, numeric_seed)
    
    # Loại bỏ đáp án trước khi gửi cho học sinh
    safe_exam = {
        "id": exam["id"],
        "title": exam["title"],
        "subject": exam["subject"],
        "grade": exam["grade"],
        "duration_minutes": exam["duration_minutes"],
        "scoring": exam["scoring"],
        "is_online_exam": bool(exam.get("is_online_exam", False)),
        "is_bank": bool(exam.get("is_bank", False)) or bool(exam.get("is_bank_drawn", False)),
        "bank_seed": exam.get("bank_seed"),
        "drawn_question_count": exam.get("drawn_question_count"),
        "parts": {}
    }
    
    # Phần I: Ẩn đáp án
    p1 = exam.get("parts", {}).get("part1", {})
    safe_exam["parts"]["part1"] = {
        "name": p1.get("name", "Phần I: Trắc nghiệm nhiều lựa chọn"),
        "instruction": p1.get("instruction", "Chọn đáp án đúng duy nhất (A, B, C hoặc D) cho mỗi câu sau."),
        "questions": [
            {
                "id": q.get("id"),
                "text": q.get("text", ""),
                "options": q.get("options", {}),
                "image": q.get("image") or ""
            } for q in p1.get("questions", [])
        ]
    }
    
    # Phần II: Ẩn đáp án từng ý
    p2 = exam.get("parts", {}).get("part2", {})
    safe_exam["parts"]["part2"] = {
        "name": p2.get("name", "Phần II: Trắc nghiệm Đúng / Sai"),
        "instruction": p2.get("instruction", "Trong mỗi câu, xét tính Đúng (Đ) hoặc Sai (S) của mỗi ý (A), (B), (C), (D)."),
        "questions": [
            {
                "id": q.get("id"),
                "text": q.get("text", ""),
                "items": {
                    k: {
                        "text": v.get("text", "") if isinstance(v, dict) else str(v),
                        "image": v.get("image") if isinstance(v, dict) else None
                    } for k, v in q.get("items", {}).items()
                },
                "image": q.get("image") or ""
            } for q in p2.get("questions", [])
        ]
    }
    
    # Phần III: Ẩn đáp án
    p3 = exam.get("parts", {}).get("part3", {})
    safe_exam["parts"]["part3"] = {
        "name": p3.get("name", "Phần III: Trả lời ngắn"),
        "instruction": p3.get("instruction", "Điền đáp án vào ô trống. Chỉ ghi kết quả (số hoặc biểu thức đơn giản nhất)."),
        "questions": [
            {
                "id": q.get("id"),
                "text": q.get("text", ""),
                "image": q.get("image") or ""
            } for q in p3.get("questions", [])
        ]
    }
    
    # Phần IV: Giữ nguyên (không có đáp án lộ)
    p4 = exam.get("parts", {}).get("part4", {})
    safe_exam["parts"]["part4"] = {
        "name": p4.get("name", "Phần IV: Tự luận tự chọn"),
        "instruction": p4.get("instruction", "Chọn MỘT trong các câu dưới đây để làm. Trình bày đầy đủ các bước giải."),
        "questions": [
            {
                "id": q.get("id"),
                "title": q.get("title", ""),
                "text": q.get("text", ""),
                "image": q.get("image") or ""
            } for q in p4.get("questions", [])
        ]
    }
    
    return JSONResponse(
        content=safe_exam,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    )


@router.post("/submit")
async def submit_exam(data: dict, background_tasks: BackgroundTasks):
    """Nhận bài nộp, chấm điểm phần I-III ngay, đẩy chấm AI tự luận vào background."""
    submission_id = str(uuid.uuid4())[:12].upper()
    
    exam_id = data.get("exam_id", "exam_001")
    exam = load_exam(exam_id)
    
    # Phục hồi đề thi từ dữ liệu client nếu máy chủ vừa khởi động lại / redeploy
    if not exam and data.get("exam_data"):
        exam = data["exam_data"]
        try:
            from ..database import save_exam_record
            save_exam_record(exam)
            logger.info(f"Đã tự động khôi phục đề thi '{exam.get('title')}' ({exam_id}) từ bài nộp của học sinh!")
        except Exception as save_err:
            logger.warning(f"Lỗi lưu lại đề thi phục hồi: {save_err}")
    
    if not exam and data.get("exam_title"):
        title = data.get("exam_title").strip()
        exam = load_exam(title)

    if not exam:
        raise HTTPException(status_code=404, detail="Không tìm thấy đề thi trên máy chủ!")
    
    # Kiểm tra trạng thái xuất bản
    if not is_exam_published(exam.get("is_published")):
        raise HTTPException(
            status_code=403, 
            detail="Đề thi này đang ở trạng thái CHƯA XUẤT BẢN. Không thể nộp bài!"
        )
    
    student_name = data.get("student_name", "").strip().upper()
    student_class = data.get("student_class", "").strip().upper()
    if not student_name:
        raise HTTPException(status_code=400, detail="Vui lòng nhập họ tên!")
    
    # Nếu là đề ngân hàng, tái tạo chính xác bộ câu hỏi của học sinh này theo seed để chấm
    from ..services.bank_service import is_bank_exam, generate_student_exam_from_bank, get_student_seed
    student_exam = exam
    if is_bank_exam(exam):
        client_seed = str(data.get("seed") or data.get("bank_seed") or "")
        numeric_seed = None
        if client_seed:
            try:
                numeric_seed = int(client_seed)
            except ValueError:
                pass
        if numeric_seed is None:
            numeric_seed = get_student_seed(student_name, student_class, exam_id, client_seed=client_seed)
        student_exam = generate_student_exam_from_bank(exam, numeric_seed)
    
    # Chấm điểm tức thì dựa trên bộ câu hỏi của học sinh
    p1_answers = data.get("part1_answers", {})
    p2_answers = data.get("part2_answers", {})
    p3_answers = {str(k): str(v).strip().upper() for k, v in (data.get("part3_answers") or {}).items() if v is not None}
    p4_question_id = data.get("part4_question_id")
    p4_answer = data.get("part4_answer", "")
    
    p1_result = grade_part1(student_exam, p1_answers)
    p2_result = grade_part2(student_exam, p2_answers)
    p3_result = grade_part3(student_exam, p3_answers)
    
    # Phần IV ban đầu chấm 0, sẽ cập nhật sau khi AI chấm xong
    p4_result = {"score": 0.0, "status": "pending", "skipped": not bool(p4_answer.strip())}
    
    scores = calculate_total_score(p1_result, p2_result, p3_result, 0.0, student_exam)
    
    client_submitted = data.get("submitted_at")
    if not client_submitted or not isinstance(client_submitted, str):
        client_submitted = datetime.now(VIETNAM_TZ).isoformat()
    
    is_online_exam = bool(exam.get("is_online_exam", False))
    
    submission_data = {
        "submission_id": submission_id,
        "student_name": student_name,
        "student_class": student_class,
        "exam_id": exam_id,
        "is_online_exam": is_online_exam,
        "started_at": data.get("started_at", ""),
        "submitted_at": client_submitted,
        "duration_seconds": data.get("duration_seconds", 0),
        "screen_switch_count": int(data.get("screen_switch_count", 0)) if is_online_exam else 0,
        "switch_violations": data.get("switch_violations", []) if is_online_exam else [],
        "part1_answers": p1_answers,
        "part2_answers": p2_answers,
        "part3_answers": p3_answers,
        "part4_question_id": p4_question_id,
        "part4_answer": p4_answer,
        "is_bank": bool(student_exam.get("is_bank") or student_exam.get("is_bank_drawn")),
        "bank_seed": student_exam.get("bank_seed"),
        "exam_data": student_exam,
    }
    
    result_data = {
        "scores": scores,
        "is_online_exam": is_online_exam,
        "part1_result": p1_result,
        "part2_result": p2_result,
        "part3_result": p3_result,
        "part4_result": p4_result,
        "screen_switch_count": submission_data["screen_switch_count"],
        "switch_violations": submission_data["switch_violations"],
        "submitted_at": submission_data["submitted_at"],
        "duration_seconds": submission_data["duration_seconds"],
    }
    
    save_submission(submission_data, result_data)
    
    # Chấm AI tự luận trong background
    if p4_answer.strip() and p4_question_id:
        background_tasks.add_task(
            _grade_essay_background,
            submission_id, exam, p4_question_id, p4_answer,
            submission_data, result_data
        )
    
    return {
        "success": True,
        "submission_id": submission_id,
        "student_name": student_name,
        "student_class": student_class,
        "exam_id": exam_id,
        "is_online_exam": is_online_exam,
        "started_at": submission_data["started_at"],
        "submitted_at": submission_data["submitted_at"],
        "duration_seconds": submission_data["duration_seconds"],
        "status": "graded",
        "scores": scores,
        "part1_result": p1_result,
        "part2_result": p2_result,
        "part3_result": p3_result,
        "part4_result": p4_result,
        "part4_grading": "pending" if p4_answer.strip() else "skipped",
        "message": "Bài đã được chấm xong! Điểm tự luận sẽ cập nhật sau vài giây."
    }


async def _grade_essay_background(
    submission_id: str, exam: dict, question_id: str,
    student_answer: str, submission_data: dict, result_data: dict
):
    """Chấm bài tự luận trong background bằng AI."""
    try:
        # Tìm câu hỏi tự luận
        p4_questions = exam["parts"]["part4"]["questions"]
        q = next((q for q in p4_questions if q["id"] == question_id), None)
        if not q:
            return
        
        rubric = q.get("rubric", {"max_score": 1.0, "criteria": []})
        sample_answer = rubric.get("sample_answer", "")
        
        ai_result = await grade_essay(
            question_text=q["text"],
            student_answer=student_answer,
            rubric=rubric,
            sample_answer=sample_answer
        )
        
        p4_score = ai_result.get("score", 0.0)
        ai_result["status"] = "graded"
        
        # Cập nhật điểm tổng
        new_scores = calculate_total_score(
            result_data["part1_result"],
            result_data["part2_result"],
            result_data["part3_result"],
            p4_score,
            exam
        )
        
        result_data["part4_result"] = ai_result
        result_data["scores"] = new_scores
        
        save_submission(submission_data, result_data)
        logger.info(f"Chấm tự luận hoàn tất: {submission_id} - Điểm AI: {p4_score}")
    
    except Exception as e:
        logger.error(f"Lỗi chấm tự luận background {submission_id}: {e}")


@router.get("/result/{submission_id}")
async def get_result(submission_id: str):
    """Lấy kết quả bài thi đã chấm."""
    result = get_submission(submission_id)
    if not result:
        raise HTTPException(status_code=404, detail="Không tìm thấy kết quả bài thi!")
    return result


@router.get("/result/{submission_id}/print", response_class=HTMLResponse)
async def get_result_print(submission_id: str):
    """Trả về HTML phiếu kết quả để in/xuất PDF."""
    result = get_submission(submission_id)
    if not result:
        raise HTTPException(status_code=404, detail="Không tìm thấy kết quả bài thi!")
    
    exam = load_exam(result.get("exam_id", "exam_001"))
    if not exam:
        raise HTTPException(status_code=404, detail="Không tìm thấy đề thi!")
    
    html = export_result_html(result, exam)
    return HTMLResponse(content=html)


@router.get("/result/{submission_id}/status")
async def get_grading_status(submission_id: str):
    """Kiểm tra trạng thái chấm điểm (dùng để poll khi AI đang chấm)."""
    result = get_submission(submission_id)
    if not result:
        raise HTTPException(status_code=404, detail="Không tìm thấy!")
    
    p4 = result.get("part4_result", {})
    return {
        "submission_id": submission_id,
        "status": result.get("status", "pending"),
        "p4_status": p4.get("status", "pending"),
        "scores": result.get("scores", {}),
    }


def to_ascii_slug(text: str) -> str:
    """Chuyển đổi chuỗi tiếng Việt có dấu thành chuỗi ASCII an toàn không dấu cho tên file."""
    if not text:
        return ""
    text = text.replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_text = re.sub(r"[^\w\s-]", "", ascii_text).strip()
    return re.sub(r"[-\s]+", "_", ascii_text)


@router.get("/export/excel")
async def export_excel(
    request: Request,
    exam_id: Optional[str] = None,
    student_class: Optional[str] = None,
    subject: Optional[str] = None
):
    """Xuất bảng điểm toàn lớp ra Excel có bộ lọc linh hoạt theo Môn thi, Đề thi và theo Lớp."""
    try:
        from ..services.auth_service import get_current_user_from_request
        from .exam_builder import _list_exam_files
        current_user = get_current_user_from_request(request)
        is_super_admin = bool(
            current_user and (current_user.get('is_protected') or current_user.get('username', '').lower() == 'admin')
        )

        clean_class = student_class.strip().upper() if student_class and student_class.strip() else None
        clean_exam_id = exam_id.strip() if exam_id and exam_id.strip() else None
        clean_subject = subject.strip() if subject and subject.strip() and subject.strip() != "all" else None
        
        submissions = get_all_submissions(exam_id=clean_exam_id, student_class=clean_class)
        all_exams = _list_exam_files()

        # Lọc theo quyền giáo viên (chỉ Super Admin mới xuất điểm tất cả các môn)
        if current_user and not is_super_admin:
            accessible_ids = {e['id'] for e in _list_exam_files(current_user)}
            submissions = [s for s in submissions if s.get('exam_id') in accessible_ids]
        elif clean_subject:
            sub_exam_ids = {e['id'] for e in all_exams if (e.get('subject') or '').strip().lower() == clean_subject.lower()}
            submissions = [s for s in submissions if s.get('exam_id') in sub_exam_ids]

        exam = load_exam(clean_exam_id) if clean_exam_id else None
        title = exam.get("title", "BẢNG ĐIỂM KIỂM TRA TRỰC TUYẾN") if exam else "BẢNG ĐIỂM TỔNG HỢP KIỂM TRA TRỰC TUYẾN"
        if clean_subject and not clean_exam_id:
            title += f" - MÔN {clean_subject.upper()}"
        if clean_class:
            title += f" - LỚP {clean_class}"
        
        excel_bytes = export_class_results_excel(submissions, title)
        
        # Tạo tên file an toàn chuẩn RFC 5987 (tránh lỗi Header Unicode latin-1 của Starlette/Uvicorn)
        fn_ascii_parts = ["bangdiem"]
        fn_utf8_parts = ["bangdiem"]
        if clean_subject:
            fn_ascii_parts.append(to_ascii_slug(clean_subject).lower())
            fn_utf8_parts.append(clean_subject.replace(" ", "_"))
        if clean_exam_id:
            fn_ascii_parts.append(to_ascii_slug(clean_exam_id))
            fn_utf8_parts.append(clean_exam_id)
        if clean_class:
            fn_ascii_parts.append(to_ascii_slug(clean_class))
            fn_utf8_parts.append(clean_class)
            
        ascii_filename = "_".join(fn_ascii_parts) + ".xlsx"
        utf8_filename = "_".join(fn_utf8_parts) + ".xlsx"
        encoded_utf8 = urllib.parse.quote(utf8_filename)
        
        content_disposition = f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{encoded_utf8}'
        
        return StreamingResponse(
            io.BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": content_disposition}
        )
    except Exception as e:
        logger.error(f"Lỗi xuất bảng điểm Excel: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Lỗi khi xuất bảng điểm Excel: {str(e)}")
