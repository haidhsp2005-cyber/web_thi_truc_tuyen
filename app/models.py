"""
Pydantic Models - Định nghĩa cấu trúc dữ liệu
"""
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
from datetime import datetime


class StudentInfo(BaseModel):
    name: str
    student_class: str
    exam_id: str = "exam_001"


class ExamSubmission(BaseModel):
    submission_id: str
    student_name: str
    student_class: str
    exam_id: str
    started_at: str
    submitted_at: str
    duration_seconds: int
    
    # Câu trả lời từng phần
    part1_answers: Dict[str, str] = {}        # {"p1_q1": "A", ...}
    part2_answers: Dict[str, Any] = {}         # {"p2_q1_a": true, ...}
    part3_answers: Dict[str, str] = {}         # {"p3_q1": "17", ...}
    part4_question_id: Optional[str] = None   # Học sinh chọn câu nào
    part4_answer: Optional[str] = None         # Bài tự luận


class SubmissionResult(BaseModel):
    submission_id: str
    student_name: str
    student_class: str
    submitted_at: str
    duration_seconds: int
    scores: dict
    part1_result: dict
    part2_result: dict
    part3_result: dict
    part4_result: dict


class APIKeyRequest(BaseModel):
    api_key: str


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = ""
    role: str = "teacher"


class ChangePasswordRequest(BaseModel):
    username: str
    old_password: Optional[str] = None
    new_password: str


class DeleteUserRequest(BaseModel):
    username: str


class ExamConfig(BaseModel):
    exam_id: str
    duration_minutes: int = 45
    is_active: bool = True

