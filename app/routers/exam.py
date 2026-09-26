"""
API Routers - Exam endpoints
"""
import uuid
import logging
from datetime import datetime, timezone, timedelta

VIETNAM_TZ = timezone(timedelta(hours=7))
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from typing import Optional
import io

from ..models import StudentInfo, ExamSubmission
from ..database import (
    load_exam, save_submission, get_submission, get_all_submissions
)
from ..services.grading_service import (
    grade_part1, grade_part2, grade_part3, calculate_total_score
)
from ..services.ai_service import grade_essay, check_ai_available
from ..services.export_service import export_class_results_excel, export_result_html

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/exam", tags=["exam"])


@router.get("/current")
async def get_current_exam(exam_id: str = "exam_001"):
    """Lấy nội dung đề thi (đã ẩn đáp án)."""
    exam = load_exam(exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Không tìm thấy đề thi!")
    
    # Loại bỏ đáp án trước khi gửi cho học sinh
    safe_exam = {
        "id": exam["id"],
        "title": exam["title"],
        "subject": exam["subject"],
        "grade": exam["grade"],
        "duration_minutes": exam["duration_minutes"],
        "scoring": exam["scoring"],
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
                "options": q.get("options", {})
            } for q in p1.get("questions", [])
        ]
    }
    
    # Phần II: Ẩn đáp án từng ý
    p2 = exam.get("parts", {}).get("part2", {})
    safe_exam["parts"]["part2"] = {
        "name": p2.get("name", "Phần II: Trắc nghiệm Đúng / Sai"),
        "instruction": p2.get("instruction", "Trong mỗi câu, xét tính Đúng (Đ) hoặc Sai (S) của mỗi ý (a), (b), (c), (d)."),
        "questions": [
            {
                "id": q.get("id"),
                "text": q.get("text", ""),
                "items": {
                    k: {"text": v.get("text", "")} for k, v in q.get("items", {}).items()
                }
            } for q in p2.get("questions", [])
        ]
    }
    
    # Phần III: Ẩn đáp án
    p3 = exam.get("parts", {}).get("part3", {})
    safe_exam["parts"]["part3"] = {
        "name": p3.get("name", "Phần III: Trả lời ngắn"),
        "instruction": p3.get("instruction", "Điền đáp án vào ô trống. Chỉ ghi kết quả (số hoặc biểu thức đơn giản nhất)."),
        "questions": [
            {"id": q.get("id"), "text": q.get("text", "")} for q in p3.get("questions", [])
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
                "text": q.get("text", "")
            } for q in p4.get("questions", [])
        ]
    }
    
    return safe_exam


@router.post("/submit")
async def submit_exam(data: dict, background_tasks: BackgroundTasks):
    """Nhận bài nộp, chấm điểm phần I-III ngay, đẩy chấm AI tự luận vào background."""
    submission_id = str(uuid.uuid4())[:12].upper()
    
    exam_id = data.get("exam_id", "exam_001")
    exam = load_exam(exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Không tìm thấy đề thi!")
    
    student_name = data.get("student_name", "").strip()
    student_class = data.get("student_class", "").strip()
    if not student_name:
        raise HTTPException(status_code=400, detail="Vui lòng nhập họ tên!")
    
    # Chấm điểm tức thì
    p1_answers = data.get("part1_answers", {})
    p2_answers = data.get("part2_answers", {})
    p3_answers = data.get("part3_answers", {})
    p4_question_id = data.get("part4_question_id")
    p4_answer = data.get("part4_answer", "")
    
    p1_result = grade_part1(exam, p1_answers)
    p2_result = grade_part2(exam, p2_answers)
    p3_result = grade_part3(exam, p3_answers)
    
    # Phần IV ban đầu chấm 0, sẽ cập nhật sau khi AI chấm xong
    p4_result = {"score": 0.0, "status": "pending", "skipped": not bool(p4_answer.strip())}
    
    scores = calculate_total_score(p1_result, p2_result, p3_result, 0.0, exam)
    
    client_submitted = data.get("submitted_at")
    if not client_submitted or not isinstance(client_submitted, str):
        client_submitted = datetime.now(VIETNAM_TZ).isoformat()
    
    submission_data = {
        "submission_id": submission_id,
        "student_name": student_name,
        "student_class": student_class,
        "exam_id": exam_id,
        "started_at": data.get("started_at", ""),
        "submitted_at": client_submitted,
        "duration_seconds": data.get("duration_seconds", 0),
        "part1_answers": p1_answers,
        "part2_answers": p2_answers,
        "part3_answers": p3_answers,
        "part4_question_id": p4_question_id,
        "part4_answer": p4_answer,
    }
    
    result_data = {
        "scores": scores,
        "part1_result": p1_result,
        "part2_result": p2_result,
        "part3_result": p3_result,
        "part4_result": p4_result,
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


@router.get("/export/excel")
async def export_excel(exam_id: str = "exam_001"):
    """Xuất bảng điểm toàn lớp ra Excel."""
    submissions = get_all_submissions(exam_id)
    exam = load_exam(exam_id)
    title = exam.get("title", "Bảng điểm") if exam else "Bảng điểm"
    
    excel_bytes = export_class_results_excel(submissions, title)
    
    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=bangdiem_{exam_id}.xlsx"}
    )
