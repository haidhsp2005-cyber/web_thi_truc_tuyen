"""
Main FastAPI Application
Hệ thống Kiểm tra Trực tuyến - Xoay vòng API Key Gemini
"""
import os
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from .database import init_db, get_config, set_config, load_exam
from .services.key_rotator import init_key_manager
from .services.auth_service import is_authenticated_admin
from .routers import exam as exam_router
from .routers import admin as admin_router
from .routers import exam_builder as exam_builder_router

# Load biến môi trường
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Khởi tạo database và API key manager khi ứng dụng bắt đầu."""
    init_db()
    logger.info("✅ Database khởi tạo thành công.")
    
    env_keys_str = os.getenv("GEMINI_API_KEYS", "")
    env_keys = [k.strip() for k in env_keys_str.split(",") if k.strip() and k.strip() not in ("key1_cua_ban","key2_cua_ban","key3_cua_ban")]
    db_keys = get_config("api_keys", [])
    all_keys = list(dict.fromkeys(env_keys + db_keys))
    
    if all_keys:
        init_key_manager(all_keys)
        logger.info(f"✅ Đã load {len(all_keys)} API key(s) vào Key Rotator.")
    else:
        logger.warning("⚠️  Chưa có API key Gemini! Vào trang Admin để thêm key.")
        try:
            init_key_manager(["placeholder_key_please_add_real_key"])
        except:
            pass
    
    yield
    logger.info("Ứng dụng đang tắt...")


def read_html(filename: str) -> str:
    """Đọc file HTML trực tiếp — tránh lỗi Jinja2 cache trên Python 3.14."""
    path = TEMPLATES_DIR / filename
    return path.read_text(encoding="utf-8")


# Tạo ứng dụng
app = FastAPI(
    title="Hệ thống Kiểm tra Trực tuyến",
    description="Kiểm tra online với API Key xoay vòng - Chuẩn GDPT 2025",
    version="1.0.0",
    lifespan=lifespan
)

# Mount static files
STATIC_DIR.mkdir(exist_ok=True)
(STATIC_DIR / "js").mkdir(exist_ok=True)
(STATIC_DIR / "css").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Include routers
app.include_router(exam_router.router)
app.include_router(admin_router.router)
app.include_router(exam_builder_router.router)


# ===================== HTML Routes =====================

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(content=read_html("index.html"))


@app.get("/exam", response_class=HTMLResponse)
async def exam_page():
    return HTMLResponse(content=read_html("exam.html"))


@app.get("/result", response_class=HTMLResponse)
async def result_page():
    return HTMLResponse(content=read_html("result.html"))


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    """Trang đăng nhập Admin."""
    if is_authenticated_admin(request):
        return RedirectResponse(url="/admin", status_code=303)
    return HTMLResponse(content=read_html("login.html"))


@app.get("/admin/logout")
async def admin_logout_page():
    """Đăng xuất Admin."""
    resp = RedirectResponse(url="/admin/login", status_code=303)
    resp.delete_cookie(key="admin_token", path="/")
    return resp


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Trang quản trị (yêu cầu đăng nhập)."""
    if not is_authenticated_admin(request):
        return RedirectResponse(url="/admin/login?next=/admin", status_code=303)
    return HTMLResponse(content=read_html("admin.html"))


@app.get("/create-exam", response_class=HTMLResponse)
async def create_exam_page(request: Request):
    """Trang tạo đề thi (yêu cầu đăng nhập)."""
    if not is_authenticated_admin(request):
        return RedirectResponse(url="/admin/login?next=/create-exam", status_code=303)
    return HTMLResponse(content=read_html("create_exam.html"))


@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "Hệ thống đang hoạt động!"}
