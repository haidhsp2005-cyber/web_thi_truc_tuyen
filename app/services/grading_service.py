"""
Grading Service - Chấm điểm chuẩn GDPT 2025
Phần I: TNKQ 20 câu (0.25đ/câu)
Phần II: Đúng/Sai 4 câu (barem 0.1 - 0.25 - 0.5 - 0.5đ)
Phần III: Trả lời ngắn 6 câu (0.33đ/câu)
Phần IV: Tự luận AI chấm (1đ)
"""
import unicodedata
import re
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _normalize_answer(answer: str) -> str:
    """Chuẩn hóa chuỗi đáp án: bỏ dấu cách thừa, chuyển thường, bỏ dấu tiếng Việt nếu là số."""
    if not answer:
        return ""
    ans = answer.strip().lower()
    # Nếu là số, loại bỏ khoảng trắng và dấu phẩy phân cách hàng nghìn
    ans_no_space = ans.replace(" ", "").replace(",", ".")
    return ans_no_space


def _is_numeric_close(student: str, correct: str, tolerance: float = 0.01) -> bool:
    """Kiểm tra đáp án số với dung sai."""
    try:
        s_val = float(student.replace(",", "."))
        c_val = float(correct.replace(",", "."))
        return abs(s_val - c_val) <= tolerance
    except ValueError:
        return False


def grade_part1(exam_data: dict, student_answers: dict) -> dict:
    """
    Chấm Phần I: 20 câu TNKQ (0.25 điểm/câu)
    student_answers: {"p1_q1": "A", "p1_q2": "C", ...}
    """
    questions = exam_data["parts"]["part1"]["questions"]
    per_q = exam_data["scoring"]["part1_per_question"]
    total_max = exam_data["scoring"]["part1_total"]

    results = []
    score = 0.0

    for q in questions:
        qid = q["id"]
        correct = q["answer"].strip().upper()
        student = (student_answers.get(qid) or "").strip().upper()
        is_correct = student == correct

        if is_correct:
            score += per_q

        results.append({
            "id": qid,
            "text": q["text"],
            "options": q["options"],
            "student_answer": student,
            "correct_answer": correct,
            "is_correct": is_correct,
            "points_earned": per_q if is_correct else 0,
            "points_max": per_q,
            "explanation": q.get("explanation", "")
        })

    return {
        "score": round(score, 2),
        "max_score": total_max,
        "correct_count": sum(1 for r in results if r["is_correct"]),
        "total_count": len(results),
        "details": results
    }


def grade_part2(exam_data: dict, student_answers: dict) -> dict:
    """
    Chấm Phần II: 4 câu Đúng/Sai
    Barem chuẩn Bộ GD&ĐT 2025:
      1/4 ý đúng = 0.1 điểm
      2/4 ý đúng = 0.25 điểm
      3/4 ý đúng = 0.5 điểm
      4/4 ý đúng = 0.5 điểm (max của mỗi câu là 0.5đ)
    Tổng 4 câu = 2.0 điểm max
    
    student_answers: {"p2_q1_a": true/false, "p2_q1_b": true/false, ...}
    """
    questions = exam_data["parts"]["part2"]["questions"]
    rubric = exam_data["scoring"]["part2_rubric"]
    total_max = exam_data["scoring"]["part2_total"]

    results = []
    total_score = 0.0

    for q in questions:
        qid = q["id"]
        items = q["items"]
        item_results = []
        correct_count = 0

        for item_key, item_data in items.items():
            field_key = f"{qid}_{item_key}"
            student_val = student_answers.get(field_key)
            correct_val = item_data["answer"]

            # Xử lý kiểu dữ liệu (có thể là string "true"/"false" từ form)
            if isinstance(student_val, str):
                student_val = student_val.lower() in ("true", "1", "đúng", "d")
            elif student_val is None:
                student_val = None  # Chưa trả lời

            is_correct = student_val == correct_val if student_val is not None else False
            if is_correct:
                correct_count += 1

            item_results.append({
                "key": item_key,
                "text": item_data["text"],
                "student_answer": student_val,
                "correct_answer": correct_val,
                "is_correct": is_correct,
                "explanation": item_data.get("explanation", "")
            })

        # Tính điểm theo barem
        rubric_key = f"{correct_count}_correct"
        q_score = rubric.get(rubric_key, 0.0)
        total_score += q_score
        per_q_max = rubric.get("4_correct", round(total_max / len(questions), 2) if questions else 0.5)

        results.append({
            "id": qid,
            "text": q["text"],
            "items": item_results,
            "correct_count": correct_count,
            "total_items": len(items),
            "score": q_score,
            "max_score": per_q_max
        })

    return {
        "score": round(total_score, 2),
        "max_score": total_max,
        "details": results
    }


def grade_part3(exam_data: dict, student_answers: dict) -> dict:
    """
    Chấm Phần III: 6 câu Trả lời ngắn
    student_answers: {"p3_q1": "17", "p3_q2": "54", ...}
    """
    questions = exam_data["parts"]["part3"]["questions"]
    total_max = exam_data["scoring"]["part3_total"]
    per_q = exam_data["scoring"]["part3_per_question"]

    results = []
    score = 0.0

    for q in questions:
        qid = q["id"]
        student_raw = (student_answers.get(qid) or "").strip()
        student_norm = _normalize_answer(student_raw)

        correct_answers = [_normalize_answer(a) for a in q.get("accepted_answers", [q["answer"]])]
        tolerance = q.get("tolerance", 0.01)

        is_correct = False
        # So khớp trực tiếp
        if student_norm in correct_answers:
            is_correct = True
        else:
            # So khớp số với dung sai
            for ca in correct_answers:
                if _is_numeric_close(student_norm, ca, tolerance):
                    is_correct = True
                    break

        earned = per_q if is_correct else 0.0
        if is_correct:
            score += per_q

        results.append({
            "id": qid,
            "text": q["text"],
            "student_answer": student_raw,
            "correct_answer": q["answer"],
            "accepted_answers": q.get("accepted_answers", [q["answer"]]),
            "is_correct": is_correct,
            "points_earned": round(earned, 3),
            "points_max": per_q,
            "explanation": q.get("explanation", "")
        })

    return {
        "score": round(score, 2),
        "max_score": total_max,
        "correct_count": sum(1 for r in results if r["is_correct"]),
        "total_count": len(results),
        "details": results
    }


def calculate_total_score(p1: dict, p2: dict, p3: dict, p4_score: float, exam_data: dict) -> dict:
    """Tổng hợp điểm toàn bài và quy đổi về thang 10."""
    p4_max = exam_data["scoring"]["part4_total"]
    total_earned = p1["score"] + p2["score"] + p3["score"] + p4_score
    total_max = p1["max_score"] + p2["max_score"] + p3["max_score"] + p4_max
    percentage = round(total_earned / total_max * 100, 1) if total_max > 0 else 0

    # Xếp loại
    if total_earned >= 8.5:
        rank = "Giỏi"
        rank_color = "green"
    elif total_earned >= 7.0:
        rank = "Khá"
        rank_color = "blue"
    elif total_earned >= 5.0:
        rank = "Trung bình"
        rank_color = "yellow"
    else:
        rank = "Yếu"
        rank_color = "red"

    return {
        "part1_score": p1["score"],
        "part2_score": p2["score"],
        "part3_score": p3["score"],
        "part4_score": round(p4_score, 2),
        "total_score": round(total_earned, 2),
        "total_max": total_max,
        "percentage": percentage,
        "rank": rank,
        "rank_color": rank_color
    }
