"""
Admin Router - Quản lý API keys và xem kết quả học sinh
"""
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from fastapi.responses import HTMLResponse
from ..database import (
    get_all_submissions, get_submission, get_config, set_config, load_exam,
    delete_submission, delete_all_submissions,
    get_all_users, create_user, update_user_password, delete_user,
    get_user_by_username, hash_password, export_full_backup, import_full_backup
)
import io, json
from datetime import datetime
from fastapi import UploadFile, File
from fastapi.responses import StreamingResponse
from ..services.key_rotator import get_key_manager, init_key_manager
from ..services.auth_service import (
    verify_admin_credentials, get_admin_token, get_user_token,
    is_authenticated_admin, require_admin, get_current_user_from_request
)
from ..services.export_service import (
    export_student_exam_print_html,
    export_clean_exam_print_html,
    export_exam_answers_print_html
)
from ..models import (
    APIKeyRequest, AdminLoginRequest,
    CreateUserRequest, ChangePasswordRequest, DeleteUserRequest
)
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


# ===================== AUTHENTICATION & USERS =====================

@router.post("/login")
async def admin_login(req: AdminLoginRequest, response: Response):
    """Xác thực tài khoản Admin/Giáo viên và thiết lập cookie phiên."""
    is_valid, user_data = verify_admin_credentials(req.username, req.password)
    if is_valid and user_data:
        p_hash = user_data.get("password_hash") or hash_password(req.password)
        token = get_user_token(user_data["username"], p_hash)
        response.set_cookie(
            key="admin_token",
            value=token,
            max_age=86400 * 7,  # 7 ngày
            httponly=True,
            samesite="lax",
            path="/"
        )
        logger.info(f"Đăng nhập thành công: {req.username} (role={user_data.get('role', 'teacher')})")
        return {
            "success": True,
            "message": f"Đăng nhập thành công! Chào mừng {user_data.get('full_name') or user_data['username']}",
            "redirect": "/admin",
            "user": {
                "username": user_data["username"],
                "full_name": user_data.get("full_name", ""),
                "role": user_data.get("role", "teacher"),
                "is_protected": user_data.get("is_protected", False),
                "subject": user_data.get("subject", "")
            }
        }
    
    logger.warning(f"Đăng nhập thất bại với username: {req.username}")
    raise HTTPException(status_code=401, detail="Tên đăng nhập hoặc mật khẩu không chính xác!")


@router.post("/logout")
@router.get("/logout")
async def admin_logout(response: Response):
    """Đăng xuất tài khoản Admin và xoá cookie."""
    response.delete_cookie(key="admin_token", path="/")
    return {"success": True, "message": "Đã đăng xuất thành công!", "redirect": "/admin/login"}


@router.get("/me")
async def get_current_admin(request: Request):
    """Kiểm tra trạng thái đăng nhập của Admin / Giáo viên."""
    user = get_current_user_from_request(request)
    return {
        "logged_in": user is not None,
        "user": user
    }


@router.get("/users")
async def list_users():
    """Lấy danh sách tất cả tài khoản quản trị và giáo viên."""
    users = get_all_users()
    return {"success": True, "users": users, "total": len(users)}


@router.post("/users/create")
async def create_new_user(req: CreateUserRequest):
    """Tạo tài khoản giáo viên mới."""
    try:
        new_u = create_user(
            username=req.username,
            password=req.password,
            full_name=req.full_name or "",
            role=req.role or "teacher",
            subject=req.subject or ""
        )
        return {"success": True, "message": f"Tạo tài khoản giáo viên '{new_u['username']}' thành công! Môn phụ trách: {new_u.get('subject','')}", "user": new_u}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Lỗi khi tạo user: {e}")
        raise HTTPException(status_code=500, detail="Lỗi máy chủ khi tạo tài khoản!")


@router.post("/users/change-password")
async def change_password(req: ChangePasswordRequest, request: Request):
    """
    Đổi mật khẩu tài khoản (kể cả admin và giáo viên).
    - Yêu cầu mật khẩu hiện tại (old_password) để đảm bảo an toàn.
    """
    clean_username = req.username.strip()
    if not clean_username:
        raise HTTPException(status_code=400, detail="Vui lòng nhập tên tài khoản!")
    
    if not req.new_password or len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu mới phải có ít nhất 6 ký tự!")

    # Kiểm tra old_password
    current_user = get_current_user_from_request(request)
    is_admin = current_user and current_user.get("role") == "admin"
    
    # Nếu đổi mật khẩu từ trang đăng nhập hoặc không phải admin đổi hộ người khác -> bắt buộc xác thực old_password
    if not (is_admin and current_user["username"].lower() != clean_username.lower()):
        if not req.old_password:
            raise HTTPException(status_code=400, detail="Vui lòng nhập mật khẩu hiện tại để xác thực!")
        is_valid, _ = verify_admin_credentials(clean_username, req.old_password)
        if not is_valid:
            raise HTTPException(status_code=401, detail="Mật khẩu hiện tại không chính xác!")

    try:
        update_user_password(clean_username, req.new_password)
        return {"success": True, "message": f"Đổi mật khẩu cho tài khoản '{clean_username}' thành công!"}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Lỗi khi đổi mật khẩu: {e}")
        raise HTTPException(status_code=500, detail="Lỗi máy chủ khi đổi mật khẩu!")


@router.post("/users/delete")
@router.delete("/users/{username}")
async def remove_user(request: Request, username: str = None, req: DeleteUserRequest = None):
    """
    Xóa tài khoản giáo viên.
    CHỈ CÓ TÀI KHOẢN ADMIN MỚI CÓ QUYỀN XÓA TÀI KHOẢN.
    KHÔNG BAO GIỜ CHO PHÉP XÓA TÀI KHOẢN ADMIN MẶC ĐỊNH.
    """
    current_user = get_current_user_from_request(request)
    if not current_user or (current_user.get("role") != "admin" and current_user.get("username", "").lower() != "admin"):
        raise HTTPException(
            status_code=403,
            detail="Quyền bị từ chối: Chỉ có tài khoản Quản trị viên (admin) mới có quyền xóa tài khoản!"
        )

    target = ""
    if req and req.username:
        target = req.username.strip()
    elif username:
        target = username.strip()

    if not target:
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp tên tài khoản cần xóa!")

    if target.lower() == "admin":
        raise HTTPException(status_code=403, detail="Tài khoản 'admin' là tài khoản mặc định và KHÔNG THỂ XÓA!")

    try:
        delete_user(target)
        return {"success": True, "message": f"Đã xóa tài khoản '{target}' thành công!"}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Lỗi khi xóa user: {e}")
        raise HTTPException(status_code=500, detail="Lỗi máy chủ khi xóa tài khoản!")



# ===================== QUẢN LÝ API KEYS =====================

@router.get("/keys/status")
async def get_keys_status():
    """Lấy trạng thái tất cả API keys."""
    try:
        manager = get_key_manager()
        return {
            "keys": manager.get_status(),
            "active_count": manager.active_key_count,
            "total_count": manager.total_key_count
        }
    except RuntimeError:
        return {"keys": [], "active_count": 0, "total_count": 0, "message": "Chưa có API key nào!"}


@router.post("/keys/add")
async def add_api_key(req: APIKeyRequest):
    """Thêm API key mới vào pool."""
    key = req.api_key.strip()
    if not key or len(key) < 10:
        raise HTTPException(status_code=400, detail="API key không hợp lệ!")
    
    try:
        manager = get_key_manager()
        manager.add_key(key)
    except RuntimeError:
        # Chưa có manager, tạo mới
        init_key_manager([key])
    
    # Lưu vào config
    existing = get_config("api_keys", [])
    if key not in existing:
        existing.append(key)
        set_config("api_keys", existing)
    
    return {"success": True, "message": f"Đã thêm key thành công! Tổng: {len(existing)} key(s)."}


@router.delete("/keys/remove")
async def remove_api_key(key_suffix: str):
    """Xóa API key khỏi pool."""
    try:
        manager = get_key_manager()
        manager.remove_key(key_suffix)
        
        existing = get_config("api_keys", [])
        existing = [k for k in existing if not k.endswith(key_suffix)]
        set_config("api_keys", existing)
        
        return {"success": True, "message": "Đã xóa key!"}
    except RuntimeError:
        raise HTTPException(status_code=500, detail="Key Manager chưa khởi tạo!")


@router.get("/submissions")
async def get_submissions(request: Request, exam_id: str = None, student_class: str = None, subject: str = None):
    """Lấy danh sách bài nộp — admin thấy tất cả, giáo viên chỉ thấy bài nộp đề của môn mình. Hỗ trợ lọc theo môn, đề thi và lớp."""
    from ..routers.exam_builder import _list_exam_files
    current_user = get_current_user_from_request(request)
    clean_eid = exam_id.strip() if exam_id and exam_id.strip() and exam_id.strip() != "all" else None
    clean_cls = student_class.strip().upper() if student_class and student_class.strip() and student_class.strip() != "all" else None
    clean_sub = subject.strip().lower() if subject and subject.strip() and subject.strip() != "all" else None

    submissions = get_all_submissions(exam_id=clean_eid, student_class=clean_cls)
    all_exams = _list_exam_files()

    # Lọc theo quyền giáo viên
    if current_user and current_user.get('role') != 'admin' and current_user.get('username', '').lower() != 'admin':
        accessible_ids = {e['id'] for e in _list_exam_files(current_user)}
        submissions = [s for s in submissions if s.get('exam_id') in accessible_ids]

    # Lọc theo môn học nếu có tham số subject
    if clean_sub:
        subject_exam_ids = {e['id'] for e in all_exams if (e.get('subject') or '').strip().lower() == clean_sub}
        submissions = [s for s in submissions if s.get('exam_id') in subject_exam_ids]

    return {"submissions": submissions, "total": len(submissions)}


@router.delete("/submissions/{submission_id}")
async def remove_submission(submission_id: str):
    """Xóa một bài nộp của học sinh khỏi danh sách."""
    success = delete_submission(submission_id)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài nộp cần xóa!")
    return {"success": True, "message": f"Đã xóa bài nộp của thí sinh ({submission_id}) thành công!"}


@router.delete("/submissions/clear/all")
async def clear_all_submissions(exam_id: str = None):
    """Xóa toàn bộ bài nộp (reset phòng thi)."""
    count = delete_all_submissions(exam_id)
    return {"success": True, "message": f"Đã xóa toàn bộ {count} bài thi thành công!", "count": count}


@router.get("/stats")
async def get_stats(request: Request, exam_id: str = None, student_class: str = None, subject: str = None):
    """Thống kê nhanh cho dashboard — admin thấy tất cả, giáo viên chỉ thấy đề của môn mình. Hỗ trợ lọc theo môn, đề thi và lớp."""
    from ..routers.exam_builder import _list_exam_files
    current_user = get_current_user_from_request(request)
    clean_eid = exam_id.strip() if exam_id and exam_id.strip() and exam_id.strip() != "all" else None
    clean_cls = student_class.strip().upper() if student_class and student_class.strip() and student_class.strip() != "all" else None
    clean_sub = subject.strip().lower() if subject and subject.strip() and subject.strip() != "all" else None

    submissions = get_all_submissions(exam_id=clean_eid, student_class=clean_cls)
    all_exams = _list_exam_files()

    # Lọc theo quyền giáo viên
    if current_user and current_user.get('role') != 'admin' and current_user.get('username', '').lower() != 'admin':
        accessible_ids = {e['id'] for e in _list_exam_files(current_user)}
        submissions = [s for s in submissions if s.get('exam_id') in accessible_ids]

    # Lọc theo môn học nếu có tham số subject
    if clean_sub:
        subject_exam_ids = {e['id'] for e in all_exams if (e.get('subject') or '').strip().lower() == clean_sub}
        submissions = [s for s in submissions if s.get('exam_id') in subject_exam_ids]

    if not submissions:
        return {
            "total_submissions": 0,
            "graded": 0,
            "average_score": 0,
            "max_score": 0,
            "min_score": 0,
            "rank_distribution": {},
            "exam_id": clean_eid,
            "student_class": clean_cls,
            "subject": clean_sub
        }

    graded = [s for s in submissions if s.get("scores")]
    scores = [s["scores"].get("total_score", 0) for s in graded if s.get("scores") and "total_score" in s["scores"]]
    ranks = [s["scores"].get("rank", "Chưa xếp loại") for s in graded if s.get("scores")]

    rank_dist = {}
    for r in ranks:
        rank_dist[r] = rank_dist.get(r, 0) + 1

    return {
        "total_submissions": len(submissions),
        "graded": len(graded),
        "average_score": round(sum(scores) / len(scores), 2) if scores else 0,
        "max_score": max(scores) if scores else 0,
        "min_score": min(scores) if scores else 0,
        "rank_distribution": rank_dist,
        "exam_id": clean_eid,
        "student_class": clean_cls
    }


# ===================== SAO LƯU & PHỤC HỒI CSDL TOÀN DIỆN =====================

@router.get("/backup/download")
async def download_full_backup(request: Request):
    """Tải toàn bộ cơ sở dữ liệu hệ thống (Đề thi + Bài thi thí sinh + Tài khoản) về máy tính cá nhân."""
    if not is_authenticated_admin(request):
        raise HTTPException(401, "Yêu cầu đăng nhập tài khoản Quản trị / Giáo viên!")
    
    backup_data = export_full_backup()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"sao_luu_toan_bo_he_thong_{timestamp}.json"
    
    content_bytes = json.dumps(backup_data, ensure_ascii=False, indent=2).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(content_bytes),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/backup/restore")
async def restore_full_backup(request: Request, file: UploadFile = File(...)):
    """Khôi phục toàn bộ hệ thống từ file sao lưu JSON tải lên từ máy tính cá nhân."""
    if not is_authenticated_admin(request):
        raise HTTPException(401, "Yêu cầu đăng nhập tài khoản Quản trị / Giáo viên!")
    
    try:
        content = await file.read()
        backup_data = json.loads(content.decode("utf-8"))
        res = import_full_backup(backup_data)
        return {
            "success": True,
            "message": f"Khôi phục thành công! Đã phục hồi {res['restored_exams']} đề thi, {res['restored_submissions']} bài làm học sinh.",
            "details": res
        }
    except Exception as e:
        logger.error(f"Lỗi khôi phục sao lưu: {e}")
        raise HTTPException(400, f"File sao lưu không hợp lệ: {str(e)}")


# ===================== CÁC ENDPOINT IN ẤN (GIÁO VIÊN & ADMIN) =====================

@router.get("/print/submission/{submission_id}", response_class=HTMLResponse)
async def print_student_submission(submission_id: str):
    """
    In toàn bộ bài thi của học sinh:
    Bao gồm thông tin học sinh, câu hỏi, lựa chọn của học sinh, đáp án đúng của đề, ký hiệu Đúng/Sai, điểm số từng phần và nhận xét.
    """
    sub = get_submission(submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài nộp của học sinh!")
    
    exam = load_exam(sub.get("exam_id", "exam_001"))
    if not exam:
        raise HTTPException(status_code=404, detail="Không tìm thấy đề thi tương ứng!")
        
    html = export_student_exam_print_html(sub, exam)
    return HTMLResponse(content=html)


@router.get("/print/exam/{exam_id}", response_class=HTMLResponse)
async def print_clean_exam(exam_id: str):
    """
    In đề thi giấy cho học sinh:
    Bao gồm tiêu đề, khung điền thông tin học sinh/SBD, nội dung đề bài (KHÔNG CÓ ĐÁP ÁN ĐỎ) để photocopy/phát cho học sinh.
    """
    exam = load_exam(exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Không tìm thấy đề thi!")
        
    html = export_clean_exam_print_html(exam)
    return HTMLResponse(content=html)


@router.get("/print/answers/{exam_id}", response_class=HTMLResponse)
async def print_exam_answers(exam_id: str):
    """
    In ma trận đáp án gốc và hướng dẫn chấm bài thi:
    Bao gồm bảng ma trận đáp án Phần I, Phần II, Phần III và toàn bộ đề thi có đánh dấu đỏ đáp án chuẩn.
    """
    exam = load_exam(exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Không tìm thấy đề thi!")
        
    html = export_exam_answers_print_html(exam)
    return HTMLResponse(content=html)

