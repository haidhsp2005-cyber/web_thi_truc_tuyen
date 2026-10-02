"""
Bank Service - Quản lý Ngân hàng câu hỏi & Bốc đề ngẫu nhiên theo từng học sinh
Chuẩn GDPT 2026:
- Tự động bốc ngẫu nhiên số lượng câu hỏi từ Ngân hàng đề (vd: bốc 30 câu từ 100 câu).
- Khóa đề theo mã hạt giống (seed) của học sinh để chống gian lận và giữ ổn định khi F5 / tải lại trang.
- Tự động chuẩn hóa thang điểm chuẩn 10.0 cho đề thi được bốc.
"""
import copy
import random
import hashlib
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


def get_student_seed(student_name: str, student_class: str, exam_id: str, client_seed: str = "") -> int:
    """
    Sinh hạt giống ngẫu nhiên (seed) duy nhất và ổn định cho từng học sinh.
    Đảm bảo:
    - Cùng một học sinh trong cùng ca thi sẽ LUÔN nhận được đúng bộ câu hỏi cũ kể cả khi F5 / mất mạng.
    - Hai học sinh khác nhau sẽ nhận hai bộ câu hỏi ngẫu nhiên hoàn toàn khác nhau.
    """
    name_clean = (student_name or "").strip().upper()
    class_clean = (student_class or "").strip().upper()
    eid_clean = (exam_id or "").strip()
    
    if client_seed and client_seed.strip():
        raw = f"{client_seed.strip()}_{name_clean}_{class_clean}_{eid_clean}"
    else:
        raw = f"{name_clean}_{class_clean}_{eid_clean}"
        
    md5_hash = hashlib.md5(raw.encode("utf-8")).hexdigest()
    # Lấy 8 ký tự đầu chuyển thành số nguyên 32-bit dương
    return int(md5_hash[:8], 16)


def is_bank_exam(exam_data: Optional[Dict[str, Any]]) -> bool:
    """Kiểm tra xem đề thi này có đang bật chế độ Ngân hàng đề (bốc ngẫu nhiên) hay không."""
    if not exam_data or not isinstance(exam_data, dict):
        return False
    if exam_data.get("is_bank"):
        return True
    cfg = exam_data.get("bank_config")
    if isinstance(cfg, dict) and cfg.get("enabled"):
        return True
    return False


def normalize_bank_config(exam_data: Dict[str, Any]) -> Dict[str, Any]:
    """Lấy và chuẩn hóa cấu hình ngân hàng câu hỏi của đề thi."""
    cfg = exam_data.get("bank_config") or {}
    if not isinstance(cfg, dict):
        cfg = {}
        
    parts = exam_data.get("parts") or {}
    p1_total = len(parts.get("part1", {}).get("questions", []))
    p2_total = len(parts.get("part2", {}).get("questions", []))
    p3_total = len(parts.get("part3", {}).get("questions", []))
    p4_total = len(parts.get("part4", {}).get("questions", []))
    bank_total_qs = p1_total + p2_total + p3_total + p4_total

    enabled = bool(cfg.get("enabled", exam_data.get("is_bank", False)))
    
    # Số câu rút cho từng phần
    p1_draw = int(cfg.get("part1_draw") or 0)
    p2_draw = int(cfg.get("part2_draw") or 0)
    p3_draw = int(cfg.get("part3_draw") or 0)
    p4_draw = int(cfg.get("part4_draw") or 0)
    
    total_draw = int(cfg.get("total_draw") or 0)
    
    # Nếu chưa cài đặt chi tiết từng phần nhưng có total_draw (ví dụ: rút 30 câu từ 100 câu)
    if total_draw > 0 and (p1_draw + p2_draw + p3_draw + p4_draw == 0) and bank_total_qs > 0:
        # Tự động chia theo tỷ lệ số câu có sẵn trong từng phần
        ratio = min(1.0, total_draw / bank_total_qs)
        p1_draw = max(1, round(p1_total * ratio)) if p1_total > 0 else 0
        p2_draw = max(1, round(p2_total * ratio)) if p2_total > 0 else 0
        p4_draw = min(p4_total, round(p4_total * ratio)) if p4_total > 0 else 0
        # Phần III nhận số câu còn lại để vừa đúng total_draw
        p3_draw = max(0, total_draw - (p1_draw + p2_draw + p4_draw))
        if p3_total > 0 and p3_draw > p3_total:
            p3_draw = p3_total
    
    # Giới hạn không vượt quá số câu thực có trong ngân hàng
    p1_draw = min(p1_draw, p1_total)
    p2_draw = min(p2_draw, p2_total)
    p3_draw = min(p3_draw, p3_total)
    p4_draw = min(p4_draw, p4_total)
    
    actual_total = p1_draw + p2_draw + p3_draw + p4_draw
    if actual_total == 0 and bank_total_qs > 0:
        actual_total = bank_total_qs
        p1_draw, p2_draw, p3_draw, p4_draw = p1_total, p2_total, p3_total, p4_total

    return {
        "enabled": enabled,
        "draw_mode": cfg.get("draw_mode", "per_student"),
        "total_draw": actual_total,
        "part1_draw": p1_draw,
        "part2_draw": p2_draw,
        "part3_draw": p3_draw,
        "part4_draw": p4_draw,
        "bank_total_qs": bank_total_qs,
        "p1_total": p1_total,
        "p2_total": p2_total,
        "p3_total": p3_total,
        "p4_total": p4_total
    }


def generate_student_exam_from_bank(exam_data: Dict[str, Any], seed: int) -> Dict[str, Any]:
    """
    Rút ngẫu nhiên một bộ câu hỏi từ Ngân hàng đề thi theo hạt giống (seed) của học sinh.
    Đầu ra là một đề thi hoàn chỉnh (chuẩn cấu trúc GDPT 2026), sẵn sàng để hiển thị và chấm điểm.
    """
    cfg = normalize_bank_config(exam_data)
    if not cfg["enabled"]:
        return exam_data

    drawn_exam = copy.deepcopy(exam_data)
    rng = random.Random(seed)

    parts = drawn_exam.get("parts") or {}
    
    # 1. Rút câu hỏi Phần I
    p1 = parts.get("part1") or {}
    p1_qs = p1.get("questions") or []
    if cfg["part1_draw"] > 0 and p1_qs:
        drawn_p1_qs = rng.sample(p1_qs, min(cfg["part1_draw"], len(p1_qs)))
        p1["questions"] = drawn_p1_qs
    
    # 2. Rút câu hỏi Phần II
    p2 = parts.get("part2") or {}
    p2_qs = p2.get("questions") or []
    if cfg["part2_draw"] > 0 and p2_qs:
        drawn_p2_qs = rng.sample(p2_qs, min(cfg["part2_draw"], len(p2_qs)))
        p2["questions"] = drawn_p2_qs

    # 3. Rút câu hỏi Phần III
    p3 = parts.get("part3") or {}
    p3_qs = p3.get("questions") or []
    if cfg["part3_draw"] > 0 and p3_qs:
        drawn_p3_qs = rng.sample(p3_qs, min(cfg["part3_draw"], len(p3_qs)))
        p3["questions"] = drawn_p3_qs

    # 4. Rút câu hỏi Phần IV (nếu có)
    p4 = parts.get("part4") or {}
    p4_qs = p4.get("questions") or []
    if cfg["part4_draw"] > 0 and p4_qs:
        drawn_p4_qs = rng.sample(p4_qs, min(cfg["part4_draw"], len(p4_qs)))
        p4["questions"] = drawn_p4_qs

    # 5. Tự động tính toán lại Thang điểm (Scoring) để luôn đảm bảo tổng 10.0 điểm
    c1 = len(p1.get("questions") or [])
    c2 = len(p2.get("questions") or [])
    c3 = len(p3.get("questions") or [])
    c4 = len(p4.get("questions") or [])

    scoring = drawn_exam.get("scoring") or {}
    
    # Định mức điểm cơ sở chuẩn GDPT 2026:
    p1_per_q = scoring.get("part1_per_question", 0.25) or 0.25
    p2_per_q = scoring.get("part2_per_question", 1.0) or 1.0
    p3_per_q = scoring.get("part3_per_question", 0.5) or 0.5
    p4_per_q = scoring.get("part4_per_question", 1.0) or 1.0

    raw_sum = (c1 * p1_per_q) + (c2 * p2_per_q) + (c3 * p3_per_q) + (c4 * p4_per_q)
    
    if raw_sum > 0:
        factor = 10.0 / raw_sum
        p1_per_q = round(p1_per_q * factor, 3)
        p2_per_q = round(p2_per_q * factor, 3)
        p3_per_q = round(p3_per_q * factor, 3) if c3 > 0 else 0.0
        p4_per_q = round(p4_per_q * factor, 3) if c4 > 0 else 0.0
    
    rubric = {
        "1_correct": round(p2_per_q * 0.1, 2),
        "2_correct": round(p2_per_q * 0.25, 2),
        "3_correct": round(p2_per_q * 0.5, 2),
        "4_correct": round(p2_per_q, 2)
    }

    p1_total = round(c1 * p1_per_q, 2)
    p2_total = round(c2 * p2_per_q, 2)
    p3_total = round(c3 * p3_per_q, 2)
    p4_total = round(c4 * p4_per_q, 2)
    total = round(p1_total + p2_total + p3_total + p4_total, 2)
    
    # Cân đối sai số làm tròn nếu lệch 0.01 - 0.05 để tròn đúng 10.0 điểm
    diff = round(10.0 - total, 2)
    if abs(diff) > 0 and c1 > 0:
        p1_total = round(p1_total + diff, 2)
        total = 10.0

    drawn_exam["scoring"] = {
        "part1_per_question": p1_per_q,
        "part1_total": p1_total,
        "part2_per_question": p2_per_q,
        "part2_rubric": rubric,
        "part2_total": p2_total,
        "part3_per_question": p3_per_q,
        "part3_total": p3_total,
        "part4_per_question": p4_per_q,
        "part4_total": p4_total,
        "total": total
    }

    # Đánh dấu đã bốc từ ngân hàng
    drawn_exam["is_bank_drawn"] = True
    drawn_exam["bank_seed"] = seed
    drawn_exam["drawn_question_count"] = c1 + c2 + c3 + c4

    logger.info(
        f"Đã bốc đề ngẫu nhiên từ Ngân hàng '{exam_data.get('title')}' (seed={seed}): "
        f"P1={c1}, P2={c2}, P3={c3}, P4={c4} | Tổng={c1+c2+c3+c4} câu hỏi."
    )
    return drawn_exam
