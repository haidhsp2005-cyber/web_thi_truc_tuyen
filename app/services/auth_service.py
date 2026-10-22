"""
Authentication Service - Quản lý đăng nhập quyền Admin và Giáo viên
Tối ưu bộ nhớ RAM cache siêu tốc (0.0001s, 0 database queries lặp)
"""
import os
import time
import hashlib
import logging
from typing import Optional, Tuple
from fastapi import Request, HTTPException, status
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Longcang2026@").strip()
SESSION_SECRET = os.getenv("SECRET_KEY", "secret_longcang_2026_exam_salt").strip()

# ===================== RAM CACHE TOKEN & USERS =====================
_AUTH_TOKEN_CACHE = {}
_USERS_CACHE = {"data": None, "time": 0.0}

def invalidate_auth_cache():
    """Xóa cache xác thực khi có người dùng thay đổi mật khẩu hoặc thêm tài khoản."""
    global _AUTH_TOKEN_CACHE, _USERS_CACHE
    _AUTH_TOKEN_CACHE.clear()
    _USERS_CACHE["data"] = None
    _USERS_CACHE["time"] = 0.0

def get_cached_users():
    """Tải danh sách người dùng kèm hash mật khẩu với bộ đệm RAM (30s) chỉ tốn 1 query duy nhất."""
    global _USERS_CACHE
    now = time.time()
    if _USERS_CACHE["data"] is not None and (now - _USERS_CACHE["time"] < 30.0):
        return _USERS_CACHE["data"]
    try:
        from ..database import get_all_users
        users = get_all_users(include_hash=True)
        _USERS_CACHE["data"] = users
        _USERS_CACHE["time"] = now
        return users
    except Exception as e:
        logger.warning(f"Lỗi load danh sách người dùng: {e}")
        return _USERS_CACHE["data"] or []


def get_user_token(username: str, password_hash: str) -> str:
    """Tạo token phiên đăng nhập cho một tài khoản cụ thể."""
    raw = f"{username.lower()}:{password_hash}:{SESSION_SECRET}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_admin_token() -> str:
    """Tạo token phiên đăng nhập admin mặc định."""
    users = get_cached_users()
    for u in users:
        if u.get("username", "").lower() == "admin":
            return get_user_token("admin", u.get("password_hash", ""))
    from ..database import hash_password
    return get_user_token("admin", hash_password("Longcang2026@"))


def verify_admin_credentials(username: str, password: str) -> Tuple[bool, Optional[dict]]:
    """Kiểm tra tên đăng nhập và mật khẩu từ cơ sở dữ liệu admin_users."""
    if not username or not password:
        return False, None
    
    clean_username = username.strip().lower()
    from ..database import hash_password
    p_hash = hash_password(password)

    users = get_cached_users()
    for u in users:
        if u.get("username", "").lower() == clean_username:
            if u.get("password_hash") == p_hash:
                clean_u = {k: v for k, v in u.items() if k != "password_hash"}
                return True, clean_u

    # Fallback cho tài khoản admin mặc định
    if clean_username == "admin" and password.strip() == "Longcang2026@":
        return True, {
            "username": "admin",
            "full_name": "Quản trị viên mặc định",
            "role": "admin",
            "is_protected": True
        }
        
    return False, None


def get_current_user_from_request(request: Request) -> Optional[dict]:
    """Lấy thông tin người dùng từ cookie admin_token hoặc Authorization header (0.0001s qua RAM)."""
    token = request.cookies.get("admin_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            
    if not token:
        return None

    # Kiểm tra trực tiếp trên RAM Cache (0.00001s, không tốn tài nguyên DB)
    if token in _AUTH_TOKEN_CACHE:
        return _AUTH_TOKEN_CACHE[token]

    # Quét nhanh trên danh sách user đã nạp sẵn RAM (chỉ 1 query gộp)
    users = get_cached_users()
    for u in users:
        uname = u.get("username", "")
        phash = u.get("password_hash", "")
        if uname and phash:
            tok = get_user_token(uname, phash)
            clean_u = {k: v for k, v in u.items() if k != "password_hash"}
            _AUTH_TOKEN_CACHE[tok] = clean_u
            if tok == token:
                return clean_u
            
    return None


def is_authenticated_admin(request: Request) -> bool:
    """Kiểm tra xem request hiện tại đã đăng nhập quyền admin/giáo viên chưa."""
    return get_current_user_from_request(request) is not None


def require_admin(request: Request):
    """Dependency / guard yêu cầu đăng nhập."""
    if not is_authenticated_admin(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bạn chưa đăng nhập quyền Quản trị! Vui lòng đăng nhập để tiếp tục."
        )
