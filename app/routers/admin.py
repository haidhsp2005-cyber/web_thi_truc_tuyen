"""
Admin Router - Quản lý API keys và xem kết quả học sinh
"""
from typing import Optional, List, Dict
from fastapi import APIRouter, HTTPException, Request, Response, Depends, Query
from fastapi.responses import HTMLResponse
from ..database import (
    get_all_submissions, get_submission, get_config, set_config, load_exam,
    delete_submission, delete_all_submissions,
    get_all_users, create_user, update_user_password, delete_user, update_user_role,
    get_user_by_username, hash_password, export_full_backup, import_full_backup,
    export_users_backup, import_users_backup
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
    export_class_submissions_print_html,
    export_clean_exam_print_html,
    export_exam_answers_print_html
)
from ..models import (
    APIKeyRequest, AdminLoginRequest,
    CreateUserRequest, ChangePasswordRequest, DeleteUserRequest, UpdateRoleRequest
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
async def list_users(request: Request):
    """
    Lấy danh sách tài khoản.
    - Quản trị viên (admin): Thấy toàn bộ danh sách tài khoản (admin + tất cả giáo viên).
    - Giáo viên (không phải admin): Để bảo mật, chỉ thấy đúng tài khoản của chính mình.
    """
    current_user = get_current_user_from_request(request)
    users = get_all_users()
    if not current_user:
        return {"success": True, "users": [], "total": 0}

    is_super_admin = bool(
        current_user.get("is_protected") or current_user.get("username", "").lower() == "admin"
    )
    if not is_super_admin:
        my_username = (current_user.get("username") or "").lower()
        users = [u for u in users if (u.get("username") or "").lower() == my_username]

    return {"success": True, "users": users, "total": len(users)}


@router.post("/users/create")
async def create_new_user(req: CreateUserRequest, request: Request):
    """Tạo tài khoản giáo viên mới (chỉ thực hiện được khi đã đăng nhập vào Trang Quản Trị)."""
    current_user = get_current_user_from_request(request)
    if not current_user:
        raise HTTPException(
            status_code=401,
            detail="Vui lòng đăng nhập vào trang quản trị để tạo tài khoản giáo viên mới!"
        )

    is_super_admin = bool(
        current_user and (current_user.get("is_protected") or current_user.get("username", "").lower() == "admin")
    )
    is_admin = current_user.get("role") == "admin" or is_super_admin
    if not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Chỉ có tài khoản Quản trị viên mới có quyền tạo tài khoản giáo viên mới!"
        )

    # Giáo viên được ủy quyền quản trị chỉ có thể tạo tài khoản với vai trò Giáo viên
    user_role = (req.role or "teacher") if is_super_admin else "teacher"

    try:
        new_u = create_user(
            username=req.username,
            password=req.password,
            full_name=req.full_name or "",
            role=user_role,
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
    - Yêu cầu người dùng phải đăng nhập vào trang quản trị.
    - Yêu cầu mật khẩu hiện tại (old_password) để đảm bảo an toàn.
    """
    current_user = get_current_user_from_request(request)
    if not current_user:
        raise HTTPException(
            status_code=401,
            detail="Vui lòng đăng nhập vào trang quản trị để thực hiện đổi mật khẩu!"
        )

    clean_username = req.username.strip()
    if not clean_username:
        raise HTTPException(status_code=400, detail="Vui lòng nhập tên tài khoản!")
    
    if not req.new_password or len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu mới phải có ít nhất 6 ký tự!")

    is_admin = current_user.get("role") == "admin" or current_user.get("username", "").lower() == "admin"
    
    # Giáo viên chỉ được đổi mật khẩu cho chính mình; admin có thể đổi mật khẩu cho bất kỳ ai
    if not is_admin and current_user.get("username", "").lower() != clean_username.lower():
        raise HTTPException(
            status_code=403,
            detail="Bạn chỉ có thể đổi mật khẩu cho tài khoản của chính mình!"
        )

    # Nếu không phải admin đổi hộ người khác -> bắt buộc xác thực old_password
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
    is_super_admin = bool(
        current_user and (current_user.get("is_protected") or current_user.get("username", "").lower() == "admin")
    )
    if not is_super_admin:
        raise HTTPException(
            status_code=403,
            detail="Quyền bị từ chối: Chỉ có tài khoản Quản trị viên chính (admin) mới có quyền xóa tài khoản!"
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

    # Kiểm tra tài khoản đích
    target_u = get_user_by_username(target)
    if not target_u:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản cần xóa!")
    if target_u.get("is_protected") or target_u.get("username", "").lower() == "admin":
        raise HTTPException(status_code=403, detail="Tài khoản Quản trị viên hệ thống là bất khả xâm phạm và KHÔNG THỂ XÓA!")
    # Người dùng được phân quyền quản trị không thể xóa tài khoản quản trị khác
    if current_user.get("username", "").lower() != "admin" and target_u.get("role") == "admin":
        raise HTTPException(status_code=403, detail="Bạn không có quyền xóa tài khoản của Quản trị viên khác!")

    try:
        delete_user(target)
        return {"success": True, "message": f"Đã xóa tài khoản '{target}' thành công!"}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Lỗi khi xóa user: {e}")
        raise HTTPException(status_code=500, detail="Lỗi máy chủ khi xóa tài khoản!")


@router.post("/users/update-role")
async def change_user_role(req: UpdateRoleRequest, request: Request):
    """
    Phân quyền vai trò người dùng (Giáo viên hoặc Quản trị viên).
    CHỈ CÓ TÀI KHOẢN ADMIN CHÍNH (admin) MỚI CÓ QUYỀN ĐỔI VAI TRÒ.
    """
    current_user = get_current_user_from_request(request)
    if not current_user or current_user.get("username", "").lower() != "admin":
        raise HTTPException(
            status_code=403,
            detail="Chỉ Quản trị viên chính (admin) mới có quyền phân quyền vai trò tài khoản!"
        )

    try:
        update_user_role(req.username, req.role)
        role_vn = "Quản trị viên" if req.role.lower() == "admin" else "Giáo viên"
        return {"success": True, "message": f"Đã chuyển vai trò tài khoản '{req.username}' thành '{role_vn}' thành công!"}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Lỗi khi đổi vai trò user: {e}")
        raise HTTPException(status_code=500, detail="Lỗi máy chủ khi cập nhật vai trò!")


@router.get("/users/backup/download")
async def download_users_backup(request: Request):
    """
    Tải file JSON sao lưu danh sách tài khoản (chỉ dành cho Quản trị viên).
    """
    current_user = get_current_user_from_request(request)
    if not current_user or (current_user.get("role") != "admin" and current_user.get("username", "").lower() != "admin"):
        raise HTTPException(status_code=403, detail="Chỉ có Quản trị viên (admin) mới có quyền sao lưu tài khoản!")

    backup_data = export_users_backup()
    content_bytes = json.dumps(backup_data, ensure_ascii=False, indent=2).encode("utf-8")
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"sao_luu_tai_khoan_{now_str}.json"

    return StreamingResponse(
        io.BytesIO(content_bytes),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-cache, no-store, must-revalidate"
        }
    )


@router.post("/users/backup/restore")
async def restore_users_backup(request: Request, file: UploadFile = File(...)):
    """
    Khôi phục danh sách tài khoản từ file sao lưu JSON (chỉ dành cho Quản trị viên).
    """
    current_user = get_current_user_from_request(request)
    if not current_user or (current_user.get("role") != "admin" and current_user.get("username", "").lower() != "admin"):
        raise HTTPException(status_code=403, detail="Chỉ có Quản trị viên (admin) mới có quyền khôi phục tài khoản!")

    try:
        content = await file.read()
        backup_data = json.loads(content.decode("utf-8"))
        res = import_users_backup(backup_data)
        msg = f"Khôi phục tài khoản thành công: Thêm mới {res['restored']} tài khoản, cập nhật {res['updated']} tài khoản!"
        return {
            "success": True,
            "message": msg,
            "details": res
        }
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="File tải lên không phải định dạng JSON hợp lệ!")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Lỗi khi khôi phục tài khoản: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi máy chủ khi khôi phục tài khoản: {str(e)}")


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

    # Lọc theo quyền giáo viên (chỉ Super Admin mặc định mới thấy tất cả môn)
    is_super_admin = bool(
        current_user and (current_user.get('is_protected') or current_user.get('username', '').lower() == 'admin')
    )
    if current_user and not is_super_admin:
        accessible_ids = {e['id'] for e in _list_exam_files(current_user)}
        submissions = [s for s in submissions if s.get('exam_id') in accessible_ids]

    # Lọc theo môn học nếu có tham số subject
    if clean_sub:
        subject_exam_ids = {e['id'] for e in all_exams if (e.get('subject') or '').strip().lower() == clean_sub}
        submissions = [s for s in submissions if s.get('exam_id') in subject_exam_ids]

    return {"submissions": submissions, "total": len(submissions)}


@router.delete("/submissions/{submission_id}")
async def remove_submission(submission_id: str, request: Request):
    """Xóa một bài nộp của học sinh khỏi danh sách (tự động kiểm tra phân quyền môn học của giáo viên)."""
    from ..routers.exam_builder import _list_exam_files
    current_user = get_current_user_from_request(request)

    is_super_admin = bool(
        current_user and (current_user.get('is_protected') or current_user.get('username', '').lower() == 'admin')
    )
    allowed_exam_ids = None
    if current_user and not is_super_admin:
        allowed_exam_ids = list({e['id'] for e in _list_exam_files(current_user)})

    success = delete_submission(submission_id, allowed_exam_ids=allowed_exam_ids)
    if not success:
        raise HTTPException(
            status_code=403 if allowed_exam_ids is not None else 404,
            detail="Không tìm thấy bài nộp hoặc bạn không có quyền xóa bài thi của môn học khác!"
        )
    return {"success": True, "message": f"Đã xóa bài nộp của thí sinh ({submission_id}) thành công!"}


@router.delete("/submissions/clear/all")
async def clear_all_submissions(request: Request, exam_id: str = None, subject: str = None):
    """
    Xóa bài nộp theo đề thi hoặc theo môn học của giáo viên.
    TỰ ĐỘNG BẢO VỆ: Nếu là giáo viên, BẮT BUỘC chỉ được xóa các bài nộp thuộc môn học mình phụ trách.
    Tuyệt đối không bao giờ làm ảnh hưởng đến bài nộp của các môn học khác!
    """
    from ..routers.exam_builder import _list_exam_files
    current_user = get_current_user_from_request(request)

    is_super_admin = bool(
        current_user and (current_user.get('is_protected') or current_user.get('username', '').lower() == 'admin')
    )

    clean_eid = exam_id.strip() if exam_id and exam_id.strip() and exam_id.strip() != "all" else None
    clean_sub = subject.strip().lower() if subject and subject.strip() and subject.strip() != "all" else None

    all_exams = _list_exam_files()

    if current_user and not is_super_admin:
        # Giáo viên: BẮT BUỘC chỉ xóa trong phạm vi đề thi môn của mình
        accessible_exams = _list_exam_files(current_user)
        accessible_ids = [e['id'] for e in accessible_exams]

        if clean_eid:
            if clean_eid not in accessible_ids:
                raise HTTPException(status_code=403, detail="Bạn không có quyền xóa bài thi của môn học khác!")
            count = delete_all_submissions(exam_id=clean_eid)
        elif clean_sub:
            sub_ids = [e['id'] for e in accessible_exams if (e.get('subject') or '').strip().lower() == clean_sub]
            count = delete_all_submissions(exam_ids=sub_ids)
        else:
            # Xóa toàn bộ bài nộp trong phạm vi các đề thuộc môn giáo viên phụ trách
            count = delete_all_submissions(exam_ids=accessible_ids)

        teacher_name = current_user.get('full_name') or current_user.get('username')
        teacher_subject = current_user.get('subject') or "môn phụ trách"
        return {
            "success": True,
            "message": f"Đã xóa {count} bài thi thuộc môn {teacher_subject} của giáo viên {teacher_name}! (Không ảnh hưởng đến môn khác)",
            "count": count
        }

    else:
        # Super Admin: có thể xóa theo đề, môn, hoặc toàn bộ
        if clean_eid:
            count = delete_all_submissions(exam_id=clean_eid)
        elif clean_sub:
            sub_ids = [e['id'] for e in all_exams if (e.get('subject') or '').strip().lower() == clean_sub]
            count = delete_all_submissions(exam_ids=sub_ids)
        else:
            count = delete_all_submissions()

        return {"success": True, "message": f"Đã xóa {count} bài thi thành công!", "count": count}


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

    # Lọc theo quyền giáo viên (chỉ Super Admin mặc định mới thấy tất cả môn)
    is_super_admin = bool(
        current_user and (current_user.get('is_protected') or current_user.get('username', '').lower() == 'admin')
    )
    if current_user and not is_super_admin:
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
        if backup_data.get("type") == "users_backup" or ("users" in backup_data and "exams" not in backup_data and "submissions" not in backup_data):
            res_u = import_users_backup(backup_data)
            return {
                "success": True,
                "message": f"Khôi phục tài khoản thành công: Thêm mới {res_u['restored']} tài khoản, cập nhật {res_u['updated']} tài khoản!",
                "details": res_u
            }

        res = import_full_backup(backup_data)
        return {
            "success": True,
            "message": f"Khôi phục thành công! Đã phục hồi {res['restored_exams']} đề thi, {res['restored_submissions']} bài làm học sinh, {res.get('restored_users', 0)} tài khoản.",
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
    
    exam = sub.get("exam_data") or load_exam(sub.get("exam_id", "exam_001"))
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


@router.get("/print/submissions/class", response_class=HTMLResponse)
async def print_class_submissions(
    request: Request,
    student_class: Optional[str] = Query(None),
    exam_id: Optional[str] = Query(None),
    subject: Optional[str] = Query(None)
):
    """
    In toàn bộ bài thi đã nộp của tất cả học sinh theo Lớp (hoặc theo đề thi / bộ lọc hiện tại):
    Tự động phân trang chuẩn A4 (mỗi học sinh 1 trang in riêng biệt) để in ra giấy hoặc lưu thành 1 file PDF trọn bộ.
    """
    from ..routers.exam_builder import _list_exam_files
    current_user = get_current_user_from_request(request)
    clean_eid = exam_id.strip() if exam_id and exam_id.strip() and exam_id.strip() != "all" else None
    clean_cls = student_class.strip().upper() if student_class and student_class.strip() and student_class.strip() != "all" else None
    clean_sub = subject.strip().lower() if subject and subject.strip() and subject.strip() != "all" else None

    submissions = get_all_submissions(exam_id=clean_eid, student_class=clean_cls)
    all_exams = _list_exam_files()

    # Lọc theo quyền giáo viên (chỉ Super Admin mặc định mới thấy tất cả môn)
    is_super_admin = bool(
        current_user and (current_user.get('is_protected') or current_user.get('username', '').lower() == 'admin')
    )
    if current_user and not is_super_admin:
        accessible_ids = {e['id'] for e in _list_exam_files(current_user)}
        submissions = [s for s in submissions if s.get('exam_id') in accessible_ids]

    # Lọc theo môn học nếu có tham số subject
    if clean_sub:
        subject_exam_ids = {e['id'] for e in all_exams if (e.get('subject') or '').strip().lower() == clean_sub}
        submissions = [s for s in submissions if s.get('exam_id') in subject_exam_ids]

    if not submissions:
        return HTMLResponse(
            content="""<!DOCTYPE html><html lang='vi'><head><meta charset='UTF-8'><title>Không có bài nộp</title>
            <script src='https://cdn.tailwindcss.com'></script></head>
            <body class='p-8 text-center bg-gray-50 flex items-center justify-center min-h-screen'>
              <div class='bg-white p-6 rounded-2xl shadow border max-w-md'>
                <p class='text-4xl mb-3'>📋</p>
                <h2 class='font-bold text-lg text-gray-800 mb-2'>Không có bài nộp nào phù hợp!</h2>
                <p class='text-sm text-gray-600 mb-4'>Chưa có học sinh nào nộp bài thuộc lớp hoặc bộ lọc được chọn.</p>
                <button onclick='window.close()' class='px-4 py-2 bg-blue-600 text-white rounded-xl text-sm font-semibold'>Đóng tab</button>
              </div>
            </body></html>""",
            status_code=200
        )

    # 1. Nạp đầy đủ thông tin bài nộp (bao gồm cả nội dung câu hỏi, bài làm và kết quả từng phần)
    full_submissions = []
    for s in submissions:
        sub_id = s.get("submission_id") or s.get("id")
        full_sub = get_submission(sub_id) if sub_id else None
        if full_sub:
            full_submissions.append(full_sub)
        else:
            full_submissions.append(s)
    submissions = full_submissions

    # 2. Nạp thông tin các đề thi tương ứng (từ SQLite hoặc file JSON)
    exams_cache = {}
    for s in submissions:
        eid = s.get("exam_id")
        if eid and eid not in exams_cache:
            e = load_exam(eid)
            if not e:
                for cand in all_exams:
                    if cand.get("id") == eid or cand.get("file") == eid:
                        e = load_exam(cand.get("id")) or cand
                        break
            if e:
                exams_cache[eid] = e

    html = export_class_submissions_print_html(
        submissions=submissions,
        exams_cache=exams_cache,
        class_name=clean_cls or "Tất cả các lớp",
        exam_id=clean_eid,
        subject=clean_sub
    )
    return HTMLResponse(content=html)

