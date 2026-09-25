"""
Authentication Service - Quản lý đăng nhập quyền Admin và Giáo viên
"""
import os
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


def get_user_token(username: str, password_hash: str) -> str:
    """Tạo token phiên đăng nhập cho một tài khoản cụ thể."""
    raw = f"{username.lower()}:{password_hash}:{SESSION_SECRET}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_admin_token() -> str:
    """Tạo token phiên đăng nhập admin mặc định."""
    from ..database import get_user_by_username, hash_password
    admin_user = get_user_by_username("admin")
    p_hash = admin_user["password_hash"] if admin_user else hash_password("Longcang2026@")
    return get_user_token("admin", p_hash)


def verify_admin_credentials(username: str, password: str) -> Tuple[bool, Optional[dict]]:
    """Kiểm tra tên đăng nhập và mật khẩu từ cơ sở dữ liệu admin_users."""
    if not username or not password:
        return False, None
    
    clean_username = username.strip()
    from ..database import get_user_by_username, hash_password
    user = get_user_by_username(clean_username)
    if user:
        if user["password_hash"] == hash_password(password):
            return True, user
    
    # Fallback cho tài khoản admin mặc định
    if clean_username.lower() == "admin" and password.strip() == "Longcang2026@":
        return True, {
            "username": "admin",
            "full_name": "Quản trị viên mặc định",
            "role": "admin",
            "is_protected": True
        }
        
    return False, None


def get_current_user_from_request(request: Request) -> Optional[dict]:
    """Lấy thông tin người dùng từ cookie admin_token hoặc Authorization header."""
    token = request.cookies.get("admin_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            
    if not token:
        return None

    # Kiểm tra token mặc định admin
    if token == get_admin_token():
        return {
            "username": "admin",
            "full_name": "Quản trị viên mặc định",
            "role": "admin",
            "is_protected": True
        }

    # Quét danh sách người dùng trong SQLite
    from ..database import get_all_users, get_user_by_username
    users = get_all_users()
    for u in users:
        full_u = get_user_by_username(u["username"])
        if full_u and get_user_token(full_u["username"], full_u["password_hash"]) == token:
            return u
            
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
