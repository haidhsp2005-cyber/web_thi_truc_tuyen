"""
Database - Quản lý CSDL Đa Nền Tảng (Neon PostgreSQL Cloud + SQLite Local Fallback)
Tích hợp In-Memory Exam Cache siêu tốc cho 100 - 200 thí sinh đồng thời
"""
import json
import sqlite3
import os
import re
import base64
import io
import copy
import hashlib
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DATA_DIR_ENV = os.getenv("DATA_DIR")
if DATA_DIR_ENV:
    EXAMS_DIR = Path(DATA_DIR_ENV)
else:
    EXAMS_DIR = Path(__file__).parent.parent / "data"

EXAMS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = EXAMS_DIR
DB_PATH = EXAMS_DIR / "exam_db.sqlite" 
PASSWORD_SALT = "longcang_exam_salt_2026"

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL and (DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")))

# ===================== POSTGRESQL CONNECTION POOL =====================
_PG_POOL = None
if USE_POSTGRES:
    try:
        import psycopg2
        from psycopg2.pool import ThreadedConnectionPool
        from psycopg2.extras import execute_values
        
        # Kết nối tới Neon PostgreSQL Singapore với connection pool đa luồng
        _PG_POOL = ThreadedConnectionPool(minconn=2, maxconn=25, dsn=DATABASE_URL)
        logger.info("Đã khởi tạo ThreadedConnectionPool kết nối Neon PostgreSQL thành công.")
    except Exception as e:
        logger.error(f"Không thể khởi tạo kết nối Neon PostgreSQL: {e}. Hệ thống sẽ dùng SQLite fallback.")
        USE_POSTGRES = False


class PooledPgConnection:
    """Wrapper cho kết nối từ ThreadedConnectionPool của PostgreSQL."""
    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool
        self._closed = False

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def execute(self, *args, **kwargs):
        cur = self._conn.cursor()
        cur.execute(*args, **kwargs)
        return cur

    def close(self):
        if not self._closed:
            try:
                if self._pool:
                    self._pool.putconn(self._conn)
            except Exception:
                pass
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


def get_connection():
    """Lấy kết nối CSDL (PostgreSQL nếu có cấu hình, SQLite nếu không)."""
    global USE_POSTGRES, _PG_POOL
    if USE_POSTGRES and _PG_POOL:
        for _ in range(3):
            raw_conn = None
            try:
                raw_conn = _PG_POOL.getconn()
                if getattr(raw_conn, 'closed', 0) != 0:
                    _PG_POOL.putconn(raw_conn, close=True)
                    continue
                with raw_conn.cursor() as cur:
                    cur.execute("SELECT 1;")
                return PooledPgConnection(raw_conn, _PG_POOL)
            except Exception as e:
                try:
                    if raw_conn:
                        _PG_POOL.putconn(raw_conn, close=True)
                except Exception:
                    pass
                logger.debug(f"Kết nối Neon bị gián đoạn ({e}), đang tái tạo...")

        try:
            import psycopg2
            raw_conn = psycopg2.connect(DATABASE_URL)
            return PooledPgConnection(raw_conn, None)
        except Exception as e2:
            logger.error(f"Kết nối Neon thất bại ({e2}), fallback sang SQLite!")
    
    # SQLite fallback
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return conn


# ===================== IN-MEMORY EXAM CACHE =====================
# Lưu trữ đề thi trên RAM để phản hồi trong 0.0001s, chống nghẽn 100% khi 200 thí sinh vào thi
_EXAM_CACHE: Dict[str, dict] = {}


def get_cached_exam(exam_id: str) -> Optional[dict]:
    """Lấy đề thi từ bộ nhớ RAM siêu tốc."""
    if not exam_id:
        return None
    clean = str(exam_id).strip()
    clean_no_ext = clean.replace(".json", "")
    
    cand_keys = [clean, clean_no_ext, f"{clean_no_ext}.json"]
    if not clean_no_ext.startswith("exam_"):
        cand_keys.append(f"exam_{clean_no_ext}")
        cand_keys.append(f"exam_{clean_no_ext}.json")
    else:
        stripped = clean_no_ext.replace("exam_", "", 1)
        cand_keys.append(stripped)
        cand_keys.append(f"{stripped}.json")
        
    for k in cand_keys:
        if k in _EXAM_CACHE:
            return copy.deepcopy(_EXAM_CACHE[k])
    return None


def set_cached_exam(exam_id: str, data: dict):
    """Ghi đề thi vào bộ nhớ RAM."""
    if not exam_id or not isinstance(data, dict):
        return
    clean = str(exam_id).strip()
    clean_no_ext = clean.replace(".json", "")
    copied = copy.deepcopy(data)
    _EXAM_CACHE[clean] = copied
    _EXAM_CACHE[clean_no_ext] = copied
    _EXAM_CACHE[f"{clean_no_ext}.json"] = copied


def invalidate_cached_exam(exam_id: str):
    """Xóa đề thi khỏi bộ nhớ RAM khi đề bị xóa."""
    if not exam_id:
        return
    clean = str(exam_id).strip()
    clean_no_ext = clean.replace(".json", "")
    for k in [clean, clean_no_ext, f"{clean_no_ext}.json", f"exam_{clean_no_ext}"]:
        _EXAM_CACHE.pop(k, None)


# ===================== TIỆN ÍCH MẬT KHẨU & MATH =====================

def hash_password(password: str) -> str:
    """Băm mật khẩu an toàn với salt cố định."""
    raw = f"{PASSWORD_SALT}:{password.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_exam_published(val) -> bool:
    """Kiểm tra một đề thi có đang ở trạng thái xuất bản hay không."""
    if val is None:
        return True
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "0", "off", "no", "chua_xuat_ban")
    return bool(val)


def convert_bytes_to_base64_data_uri(image_bytes: bytes, ext: str = "png") -> str:
    """Chuyển đổi dữ liệu ảnh thành chuỗi Base64 Data URI."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        max_dim = 1200
        if img.width > max_dim or img.height > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            
        out_buf = io.BytesIO()
        fmt = img.format if img.format else "PNG"
        if fmt.upper() in ("JPEG", "JPG"):
            img.convert("RGB").save(out_buf, format="JPEG", quality=85, optimize=True)
            mime = "image/jpeg"
        elif fmt.upper() == "WEBP":
            img.save(out_buf, format="WEBP", quality=85)
            mime = "image/webp"
        else:
            img.save(out_buf, format="PNG", optimize=True)
            mime = "image/png"
            
        b64 = base64.b64encode(out_buf.getvalue()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        clean_ext = ext.lstrip(".").lower()
        mime = "image/png"
        if clean_ext in ("jpg", "jpeg"):
            mime = "image/jpeg"
        elif clean_ext == "svg":
            mime = "image/svg+xml"
        elif clean_ext == "webp":
            mime = "image/webp"
        b64 = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime};base64,{b64}"


def file_url_to_base64(url: str, static_dir: Path = None) -> str:
    """Nếu URL là đường dẫn tĩnh cục bộ /static/uploads/..., chuyển thành Data URI base64."""
    if not url or not isinstance(url, str):
        return url
    if url.startswith("data:image/"):
        return url
    if url.startswith("/static/"):
        if static_dir is None:
            static_dir = Path(__file__).parent.parent / "static"
        rel_path = url[len("/static/"):].lstrip("/\\")
        local_file = static_dir / rel_path
        if local_file.exists():
            try:
                data = local_file.read_bytes()
                ext = local_file.suffix.lower()
                return convert_bytes_to_base64_data_uri(data, ext)
            except Exception as e:
                logger.warning(f"Lỗi đọc ảnh tĩnh sang base64 {local_file}: {e}")
    return url


def heal_question_images(q: dict) -> bool:
    """Chuyển đổi toàn bộ đường dẫn ảnh cục bộ sang Base64 để nhúng trọn vẹn vào file JSON và CSDL."""
    if not isinstance(q, dict):
        return False
    modified = False
    img = q.get("image")
    if img and isinstance(img, str) and img.startswith("/static/"):
        new_img = file_url_to_base64(img)
        if new_img != img:
            q["image"] = new_img
            modified = True

    items = q.get("items")
    if isinstance(items, dict):
        for item_key, item_val in items.items():
            if isinstance(item_val, dict) and item_val.get("image") and str(item_val["image"]).startswith("/static/"):
                new_item_img = file_url_to_base64(item_val["image"])
                if new_item_img != item_val["image"]:
                    item_val["image"] = new_item_img
                    modified = True

    text = q.get("text", "")
    if text and "/static/" in text:
        def _repl_img(m):
            return f"[IMAGE: {file_url_to_base64(m.group(1).strip())}]"
        new_text = re.sub(r'\[IMAGE:\s*(/static/[^\]]+)\]', _repl_img, text)
        def _repl_md_img(m):
            alt = m.group(1)
            u = m.group(2).strip()
            return f"![{alt}]({file_url_to_base64(u)})"
        new_text = re.sub(r'!\[([^\]]*)\]\((/static/[^)]+)\)', _repl_md_img, new_text)
        if new_text != text:
            q["text"] = new_text
            modified = True

    return modified


def heal_exam_data(exam: dict) -> bool:
    """Tự động kiểm tra và chữa lành các câu hỏi bị kẹt phương án trong bảng HTML/text."""
    if not isinstance(exam, dict) or "parts" not in exam:
        return False
    from app.services.export_service import clean_math_for_print
    modified = False

    # 1. Phần I
    p1 = exam.get("parts", {}).get("part1", {})
    if isinstance(p1, dict) and "questions" in p1 and isinstance(p1["questions"], list):
        for q in p1["questions"]:
            if not isinstance(q, dict):
                continue
            text = q.get("text", "")
            if text:
                new_text = clean_math_for_print(text)
                if new_text != text:
                    q["text"] = new_text
                    text = new_text
                    modified = True

            if heal_question_images(q):
                modified = True

            opts = q.get("options") or {}
            for k, v in list(opts.items()):
                if isinstance(v, str) and v:
                    cleaned_v = clean_math_for_print(v)
                    if cleaned_v != v:
                        opts[k] = cleaned_v
                        modified = True

            has_valid_opts = any(v and str(v).strip() for v in opts.values())
            if not has_valid_opts and text:
                clean = re.sub(r'<div[^>]*class="[^"]*overflow-x-auto[^"]*"[^>]*>', '\n', text, flags=re.I)
                clean = re.sub(r'</?(?:table|tbody|tr|div)[^>]*>', '\n', clean, flags=re.I)
                clean = re.sub(r'<td[^>]*>', '  ', clean, flags=re.I)
                clean = re.sub(r'</td>', '  ', clean, flags=re.I)

                opt_regex = r'(?:^|[\s\n>]|(?<=\}\}))(?:(?P<bracket>[\(\[])(?P<key_b>[A-Da-d])[\)\]]|(?P<key_plain>[A-Da-d])[\.\)\:\/\-])\s*'
                matches = list(re.finditer(opt_regex, clean))
                if len(matches) >= 2:
                    new_opts = {"A": "", "B": "", "C": "", "D": ""}
                    for idx, m in enumerate(matches):
                        key = (m.group("key_b") or m.group("key_plain")).upper()
                        c_start = m.end()
                        c_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(clean)
                        val = clean[c_start:c_end].strip()
                        val = re.sub(r'<[^>]+>', '', val).strip()
                        new_opts[key] = val
                    q["options"] = new_opts

                    table_idx = text.find('<div class="overflow-x-auto')
                    if table_idx == -1:
                        table_idx = text.find('<table')
                    if table_idx != -1:
                        q["text"] = text[:table_idx].strip()
                    else:
                        first_pos = matches[0].start()
                        q["text"] = clean[:first_pos].strip()
                    modified = True

    # 2. Phần II
    p2 = exam.get("parts", {}).get("part2", {})
    if isinstance(p2, dict) and "questions" in p2 and isinstance(p2["questions"], list):
        for q in p2["questions"]:
            if not isinstance(q, dict):
                continue
            text = q.get("text", "")
            if text:
                new_text = clean_math_for_print(text)
                if new_text != text:
                    q["text"] = new_text
                    text = new_text
                    modified = True

            if heal_question_images(q):
                modified = True

            items = q.get("items") or {}
            for k, it in list(items.items()):
                if isinstance(it, dict) and "text" in it:
                    it_txt = it["text"]
                    if isinstance(it_txt, str) and it_txt:
                        cleaned_it = clean_math_for_print(it_txt)
                        if cleaned_it != it_txt:
                            it["text"] = cleaned_it
                            modified = True
                elif isinstance(it, str) and it:
                    cleaned_it = clean_math_for_print(it)
                    if cleaned_it != it:
                        items[k] = cleaned_it
                        modified = True

            has_valid_items = any(
                (isinstance(v, dict) and v.get("text", "").strip()) or (isinstance(v, str) and v.strip())
                for v in items.values()
            )
            if not has_valid_items and text:
                clean = re.sub(r'<div[^>]*class="[^"]*overflow-x-auto[^"]*"[^>]*>', '\n', text, flags=re.I)
                clean = re.sub(r'</?(?:table|tbody|tr|div)[^>]*>', '\n', clean, flags=re.I)
                clean = re.sub(r'<td[^>]*>', '  ', clean, flags=re.I)
                clean = re.sub(r'</td>', '  ', clean, flags=re.I)

                item_regex = r'(?:^|[\s\n>]|(?<=\}\}))(?:(?P<bracket>[\(\[])(?P<key_b>[a-d])[\)\]]|(?P<key_plain>[a-d])[\.\)\:\/\-])\s*'
                matches = list(re.finditer(item_regex, clean))
                if len(matches) >= 2:
                    new_items = {}
                    for idx, m in enumerate(matches):
                        key = (m.group("key_b") or m.group("key_plain")).lower()
                        c_start = m.end()
                        c_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(clean)
                        val = clean[c_start:c_end].strip()
                        val = re.sub(r'<[^>]+>', '', val).strip()
                        new_items[key] = {"text": val, "answer": True}
                    q["items"] = new_items

                    table_idx = text.find('<div class="overflow-x-auto')
                    if table_idx == -1:
                        table_idx = text.find('<table')
                    if table_idx != -1:
                        q["text"] = text[:table_idx].strip()
                    else:
                        first_pos = matches[0].start()
                        q["text"] = clean[:first_pos].strip()
                    modified = True

    # 3. Phần III & IV
    for part_name in ("part3", "part4"):
        p = exam.get("parts", {}).get(part_name, {})
        if isinstance(p, dict) and "questions" in p and isinstance(p["questions"], list):
            for q in p["questions"]:
                if isinstance(q, dict):
                    if heal_question_images(q):
                        modified = True
                    text = q.get("text", "")
                    if text:
                        new_text = clean_math_for_print(text)
                        if new_text != text:
                            q["text"] = new_text
                            modified = True

    return modified


# ===================== KHỞI TẠO CSDL & PRE-WARM CACHE =====================

def init_db():
    """Khởi tạo database và nạp sẵn toàn bộ đề thi vào bộ nhớ RAM (pre-warm)."""
    global USE_POSTGRES
    
    if USE_POSTGRES:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS exams (
                    id VARCHAR(100) PRIMARY KEY,
                    title VARCHAR(255),
                    subject VARCHAR(100),
                    grade VARCHAR(50),
                    data_json TEXT,
                    created_by VARCHAR(100) DEFAULT '',
                    is_online_exam BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_exams_subject ON exams(subject);
                CREATE INDEX IF NOT EXISTS idx_exams_created_by ON exams(created_by);

                CREATE TABLE IF NOT EXISTS submissions (
                    id VARCHAR(100) PRIMARY KEY,
                    student_name VARCHAR(255),
                    student_class VARCHAR(100),
                    exam_id VARCHAR(100),
                    started_at VARCHAR(100),
                    submitted_at VARCHAR(100),
                    duration_seconds INTEGER DEFAULT 0,
                    answers_json TEXT,
                    result_json TEXT,
                    status VARCHAR(50) DEFAULT 'submitted',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_submissions_exam_id ON submissions(exam_id);
                CREATE INDEX IF NOT EXISTS idx_submissions_student_class ON submissions(student_class);
                CREATE INDEX IF NOT EXISTS idx_submissions_submitted_at ON submissions(submitted_at DESC);

                CREATE TABLE IF NOT EXISTS admin_users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(100) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    full_name VARCHAR(255) DEFAULT '',
                    role VARCHAR(50) DEFAULT 'teacher',
                    is_protected INTEGER DEFAULT 0,
                    subject VARCHAR(100) DEFAULT '',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_admin_users_username ON admin_users(LOWER(username));

                CREATE TABLE IF NOT EXISTS config (
                    key VARCHAR(100) PRIMARY KEY,
                    value TEXT
                );
            """)
            conn.commit()
            
            # Đảm bảo tài khoản admin luôn tồn tại
            c.execute("SELECT id FROM admin_users WHERE LOWER(username) = 'admin'")
            if not c.fetchone():
                c.execute("""
                    INSERT INTO admin_users (username, password_hash, full_name, role, is_protected, subject)
                    VALUES ('admin', %s, 'Quản trị viên mặc định', 'admin', 1, '')
                """, (hash_password("Longcang2026@"),))
                conn.commit()

            # Pre-warm In-Memory Cache từ PostgreSQL
            c.execute("SELECT id, data_json FROM exams")
            rows = c.fetchall()
            for eid, dj in rows:
                if eid and dj:
                    try:
                        d = json.loads(dj)
                        set_cached_exam(eid, d)
                    except Exception:
                        pass
            conn.close()
            logger.info(f"Khởi tạo Neon PostgreSQL hoàn tất. Đã nạp {len(rows)} đề thi vào RAM cache.")
            return
        except Exception as e:
            logger.error(f"Lỗi khởi tạo Neon PostgreSQL: {e}. Fallback SQLite...")
            USE_POSTGRES = False

    # Khởi tạo SQLite fallback
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id TEXT PRIMARY KEY,
            student_name TEXT NOT NULL,
            student_class TEXT NOT NULL,
            exam_id TEXT NOT NULL,
            started_at TEXT,
            submitted_at TEXT DEFAULT (datetime('now')),
            duration_seconds INTEGER DEFAULT 0,
            answers_json TEXT,
            result_json TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    c.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("""
        CREATE TABLE IF NOT EXISTS exams (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            subject TEXT,
            grade TEXT,
            data_json TEXT NOT NULL,
            created_by TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT,
            role TEXT DEFAULT 'teacher',
            is_protected INTEGER DEFAULT 0,
            subject TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    
    # Nạp cache từ SQLite
    try:
        c.execute("SELECT id, data_json FROM exams")
        for eid, dj in c.fetchall():
            if eid and dj:
                try:
                    set_cached_exam(eid, json.loads(dj))
                except Exception:
                    pass
    except Exception:
        pass
    conn.close()
    logger.info("Khởi tạo SQLite fallback hoàn tất.")


# ===================== QUẢN LÝ ĐỀ THI (EXAMS) =====================

_TEACHER_NAME_CACHE: dict = {}

def get_teacher_display_name(username: str) -> str:
    """Tra cứu họ và tên đầy đủ của giáo viên từ bảng admin_users (có RAM cache)."""
    global _TEACHER_NAME_CACHE
    if not username:
        return "GV THPT Long Cang"
    u = str(username).strip().lower()
    if u in ("admin", "root", "system", "quản trị viên mặc định", "quản trị viên"):
        return "GV THPT Long Cang"
    if u in _TEACHER_NAME_CACHE:
        val = _TEACHER_NAME_CACHE[u]
        if val.lower() in ("admin", "quản trị viên mặc định", "quản trị viên"):
            return "GV THPT Long Cang"
        return val
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT username, full_name FROM admin_users")
        for row in c.fetchall():
            if row[0] and row[1]:
                _TEACHER_NAME_CACHE[str(row[0]).strip().lower()] = str(row[1]).strip()
        conn.close()
    except Exception:
        pass
    val = _TEACHER_NAME_CACHE.get(u, username)
    if val.lower() in ("admin", "quản trị viên mặc định", "quản trị viên"):
        return "GV THPT Long Cang"
    return val


def _wrap_exam(d: dict) -> dict:
    if d and isinstance(d, dict):
        if "is_published" not in d:
            d["is_published"] = True
        if not d.get("department_name"):
            d["department_name"] = "Sở GD&ĐT Tây Ninh"
        if not d.get("school_name"):
            d["school_name"] = "Trường THPT Long Cang"
        if not d.get("author_name"):
            author = (d.get("author") or "").strip()
            creator = (d.get("created_by") or "").strip()
            if author and author.lower() not in ("admin", "root", "system"):
                d["author_name"] = author
            elif creator:
                d["author_name"] = get_teacher_display_name(creator)
            else:
                d["author_name"] = "GV THPT Long Cang"
    return d


def load_exam(exam_id: str = "exam_001", fallback_to_any: bool = False) -> Optional[dict]:
    """Tải đề thi siêu tốc (RAM Cache -> Database -> Disk)."""
    if not exam_id:
        if not fallback_to_any:
            return None
        exam_id = "exam_001"

    exam_id = str(exam_id).strip()

    # 1. Bộ nhớ RAM Cache (0.0001s, không tốn tài nguyên DB)
    cached = get_cached_exam(exam_id)
    if cached:
        return _wrap_exam(cached)

    # 2. Truy vấn Database (PostgreSQL hoặc SQLite)
    try:
        conn = get_connection()
        c = conn.cursor()
        param = exam_id
        if USE_POSTGRES:
            c.execute("SELECT id, data_json FROM exams WHERE id = %s", (param,))
            row = c.fetchone()
            if not row and not exam_id.startswith("exam_"):
                c.execute("SELECT id, data_json FROM exams WHERE id = %s", (f"exam_{exam_id}",))
                row = c.fetchone()
            if not row:
                c.execute("SELECT id, data_json FROM exams WHERE title = %s LIMIT 1", (exam_id,))
                row = c.fetchone()
        else:
            c.execute("SELECT id, data_json FROM exams WHERE id = ?", (param,))
            row = c.fetchone()
            if not row and not exam_id.startswith("exam_"):
                c.execute("SELECT id, data_json FROM exams WHERE id = ?", (f"exam_{exam_id}",))
                row = c.fetchone()
            if not row:
                c.execute("SELECT id, data_json FROM exams WHERE title = ? LIMIT 1", (exam_id,))
                row = c.fetchone()
        conn.close()

        if row and row[1]:
            real_id = row[0]
            exam_data = json.loads(row[1])
            heal_exam_data(exam_data)
            set_cached_exam(real_id, exam_data)
            return _wrap_exam(exam_data)
    except Exception as ex:
        logger.warning(f"Lỗi đọc đề {exam_id} từ CSDL: {ex}")

    # 3. Quét từ file JSON đĩa
    path = EXAMS_DIR / f"{exam_id}.json"
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
                set_cached_exam(exam_id, d)
                return _wrap_exam(d)
        except Exception:
            pass

    for f in sorted(EXAMS_DIR.glob("*.json")):
        if f.name.endswith(".sqlite") or f.name.startswith("exam_db"):
            continue
        try:
            with open(f, encoding="utf-8") as jf:
                d = json.load(jf)
                if d.get("id") == exam_id or f.stem == exam_id or d.get("title") == exam_id:
                    set_cached_exam(exam_id, d)
                    return _wrap_exam(d)
        except Exception:
            continue

    # 4. Fallback nếu được yêu cầu
    if fallback_to_any:
        if _EXAM_CACHE:
            first_key = next(iter(_EXAM_CACHE))
            return _wrap_exam(copy.deepcopy(_EXAM_CACHE[first_key]))

    return None


def save_exam_record(exam_data: dict):
    """Lưu đề thi đồng thời vào CSDL (Neon/SQLite), file JSON và cập nhật RAM Cache."""
    exam_id = exam_data.get("id")
    if not exam_id:
        return

    created_by = (exam_data.get("created_by") or "").strip()
    if not created_by:
        created_by = "admin"
    exam_data["created_by"] = created_by

    # 1. Ghi file JSON cục bộ
    try:
        json_path = EXAMS_DIR / f"{exam_id}.json"
        json_path.write_text(json.dumps(exam_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Lỗi ghi file JSON đề thi: {e}")

    # 2. Cập nhật RAM Cache ngay lập tức
    set_cached_exam(exam_id, exam_data)

    # 3. Ghi vào CSDL (PostgreSQL hoặc SQLite)
    try:
        conn = get_connection()
        c = conn.cursor()
        title = exam_data.get("title", "")
        subject = exam_data.get("subject", "")
        grade = str(exam_data.get("grade", ""))
        dj = json.dumps(exam_data, ensure_ascii=False)
        is_on = bool(exam_data.get("is_online_exam", False))

        if USE_POSTGRES:
            c.execute("""
                INSERT INTO exams (id, title, subject, grade, data_json, created_by, is_online_exam, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    subject = EXCLUDED.subject,
                    grade = EXCLUDED.grade,
                    data_json = EXCLUDED.data_json,
                    created_by = EXCLUDED.created_by,
                    is_online_exam = EXCLUDED.is_online_exam,
                    updated_at = CURRENT_TIMESTAMP
            """, (exam_id, title, subject, grade, dj, created_by, is_on))
        else:
            c.execute("""
                INSERT OR REPLACE INTO exams (id, title, subject, grade, data_json, created_by, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """, (exam_id, title, subject, grade, dj, created_by))
            
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Lỗi lưu đề thi vào CSDL: {e}")


def delete_exam_record(exam_id: str) -> bool:
    """Xóa đề thi khỏi CSDL, file JSON và xóa khỏi RAM Cache."""
    invalidate_cached_exam(exam_id)
    deleted = False

    # Xóa file JSON
    for cand in [EXAMS_DIR / f"{exam_id}.json", EXAMS_DIR / f"{exam_id.replace('exam_', '')}.json"]:
        if cand.exists():
            try:
                cand.unlink()
                deleted = True
            except Exception:
                pass

    # 3. Xóa Neon PostgreSQL (nếu có)
    if USE_POSTGRES:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute("DELETE FROM exams WHERE id = %s OR id = %s", (exam_id, f"exam_{exam_id}"))
            if c.rowcount > 0:
                deleted = True
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Lỗi xóa đề khỏi Neon: {e}")

    # 4. ĐỒNG BỘ 100%: Xóa luôn trong SQLite cục bộ để giải phóng đĩa và không bị lệch dữ liệu dự phòng
    if DB_PATH.exists():
        try:
            sq_conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
            c_sq = sq_conn.cursor()
            c_sq.execute("DELETE FROM exams WHERE id = ? OR id = ?", (exam_id, f"exam_{exam_id}"))
            if not USE_POSTGRES and c_sq.rowcount > 0:
                deleted = True
            sq_conn.commit()
            sq_conn.close()
        except Exception:
            pass

    return deleted


# ===================== QUẢN LÝ BÀI THI (SUBMISSIONS) =====================

def save_submission(submission_data: dict, result_data: dict = None):
    """Lưu bài nộp của học sinh vào CSDL (PostgreSQL / SQLite)."""
    conn = get_connection()
    c = conn.cursor()

    is_online = bool(submission_data.get("is_online_exam", False))
    switch_count = int(submission_data.get("screen_switch_count", 0)) if is_online else 0
    switch_viols = submission_data.get("switch_violations", []) if is_online else []

    answers = {
        "part1": submission_data.get("part1_answers", {}),
        "part2": submission_data.get("part2_answers", {}),
        "part3": submission_data.get("part3_answers", {}),
        "part4_question_id": submission_data.get("part4_question_id"),
        "part4_answer": submission_data.get("part4_answer", ""),
        "is_online_exam": is_online,
        "screen_switch_count": switch_count,
        "switch_violations": switch_viols,
    }

    if result_data:
        result_data["is_online_exam"] = is_online
        result_data["screen_switch_count"] = switch_count
        result_data["switch_violations"] = switch_viols
        if submission_data.get("exam_data"):
            result_data["exam_data"] = submission_data["exam_data"]
        if submission_data.get("bank_seed"):
            result_data["bank_seed"] = submission_data["bank_seed"]
        if submission_data.get("is_bank"):
            result_data["is_bank"] = submission_data["is_bank"]

    sub_id = submission_data["submission_id"]
    s_name = submission_data["student_name"].strip()
    s_class = str(submission_data["student_class"]).strip().upper()
    e_id = submission_data.get("exam_id", "exam_001")
    start_at = submission_data.get("started_at", "")
    sub_at = submission_data.get("submitted_at", datetime.now().isoformat())
    dur = submission_data.get("duration_seconds", 0)
    ans_j = json.dumps(answers, ensure_ascii=False)
    res_j = json.dumps(result_data, ensure_ascii=False) if result_data else None
    st = "graded" if result_data else "pending"
    sc_dict = result_data.get("scores", {}) if result_data else {}
    sc_j = json.dumps(sc_dict, ensure_ascii=False)

    saved_to_pg = False
    if USE_POSTGRES:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute("""
                INSERT INTO submissions 
                (id, student_name, student_class, exam_id, started_at, submitted_at, duration_seconds, answers_json, result_json, status, scores_json, is_online_exam, screen_switch_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    student_name = EXCLUDED.student_name,
                    student_class = EXCLUDED.student_class,
                    exam_id = EXCLUDED.exam_id,
                    started_at = EXCLUDED.started_at,
                    submitted_at = EXCLUDED.submitted_at,
                    duration_seconds = EXCLUDED.duration_seconds,
                    answers_json = EXCLUDED.answers_json,
                    result_json = EXCLUDED.result_json,
                    status = EXCLUDED.status,
                    scores_json = EXCLUDED.scores_json,
                    is_online_exam = EXCLUDED.is_online_exam,
                    screen_switch_count = EXCLUDED.screen_switch_count
            """, (sub_id, s_name, s_class, e_id, start_at, sub_at, dur, ans_j, res_j, st, sc_j, is_online, switch_count))
            conn.commit()
            conn.close()
            saved_to_pg = True
        except Exception as e:
            logger.critical(f"LỖI LƯU BÀI VÀO NEON POSTGRESQL ({e})! Tự động fallback lưu sang SQLite để bảo vệ bài thi...")
            try:
                # Ghi nhận cảnh báo để Super Admin thấy
                sq_conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
                sq_conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('storage_alert', ?)", 
                               (f"CSDL ngoài (Neon) gặp sự cố ({str(e)[:100]}). Hệ thống đã tự động chuyển sang lưu bài thi {sub_id} vào SQLite dự phòng.",))
                sq_conn.commit()
                sq_conn.close()
            except Exception:
                pass

    if not saved_to_pg:
        # Fallback lưu vào SQLite đảm bảo 100% không mất bài thi của học sinh
        try:
            sq_conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
            c_sq = sq_conn.cursor()
            c_sq.execute("""
                INSERT OR REPLACE INTO submissions 
                (id, student_name, student_class, exam_id, started_at, submitted_at, duration_seconds, answers_json, result_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (sub_id, s_name, s_class, e_id, start_at, sub_at, dur, ans_j, res_j, st))
            sq_conn.commit()
            sq_conn.close()
            logger.info(f"Đã lưu bài thi {sub_id} an toàn vào SQLite dự phòng.")
        except Exception as sq_err:
            logger.critical(f"Lỗi nghiêm trọng khi lưu bài vào SQLite: {sq_err}")


def get_submission(submission_id: str) -> Optional[dict]:
    """Lấy chi tiết một bài thi theo submission_id."""
    conn = get_connection()
    c = conn.cursor()
    if USE_POSTGRES:
        c.execute("SELECT id, student_name, student_class, exam_id, started_at, submitted_at, duration_seconds, answers_json, result_json, status FROM submissions WHERE id = %s", (submission_id,))
    else:
        c.execute("SELECT id, student_name, student_class, exam_id, started_at, submitted_at, duration_seconds, answers_json, result_json, status FROM submissions WHERE id = ?", (submission_id,))
    
    row = c.fetchone()
    conn.close()

    if not row:
        return None

    result = {
        "submission_id": row[0],
        "student_name": row[1],
        "student_class": row[2],
        "exam_id": row[3],
        "started_at": row[4],
        "submitted_at": row[5],
        "duration_seconds": row[6],
        "status": row[9],
        "is_online_exam": False,
        "screen_switch_count": 0,
        "switch_violations": [],
    }

    if row[7]:
        try:
            ans_data = json.loads(row[7])
            result["answers"] = ans_data
            result["part4_answer"] = ans_data.get("part4_answer", "")
            result["part4_question_id"] = ans_data.get("part4_question_id")
            if "is_online_exam" in ans_data:
                result["is_online_exam"] = bool(ans_data["is_online_exam"])
            if "screen_switch_count" in ans_data:
                result["screen_switch_count"] = int(ans_data["screen_switch_count"])
            if "switch_violations" in ans_data:
                result["switch_violations"] = ans_data["switch_violations"]
        except Exception:
            pass

    if row[8]:
        try:
            result_data = json.loads(row[8])
            result.update(result_data)
            if "is_online_exam" in result_data:
                result["is_online_exam"] = bool(result_data["is_online_exam"])
            if "screen_switch_count" in result_data:
                result["screen_switch_count"] = int(result_data["screen_switch_count"])
            if "switch_violations" in result_data:
                result["switch_violations"] = result_data["switch_violations"]
        except Exception:
            pass

    return result


def get_all_submissions(exam_id: str = None, student_class: str = None, submission_date: str = None) -> List[dict]:
    """Lấy danh sách bài nộp phục vụ bảng quản trị và thống kê."""
    conn = get_connection()
    c = conn.cursor()

    # Bản đồ metadata đề thi
    exam_meta_map = {}
    try:
        if USE_POSTGRES:
            c.execute("SELECT id, is_online_exam, title, subject, created_by FROM exams")
        else:
            c.execute("SELECT id, 0, title, subject, created_by FROM exams")
        for row_e in c.fetchall():
            exam_meta_map[row_e[0]] = {
                "is_online_exam": bool(row_e[1]),
                "title": (row_e[2] or "").strip(),
                "subject": (row_e[3] or "").strip(),
                "created_by": (row_e[4] or "").strip(),
            }
    except Exception:
        pass

    if USE_POSTGRES:
        query = "SELECT id, student_name, student_class, exam_id, submitted_at, duration_seconds, status, scores_json, is_online_exam, screen_switch_count FROM submissions"
    else:
        query = "SELECT id, student_name, student_class, exam_id, submitted_at, duration_seconds, result_json, status, answers_json FROM submissions"
    conditions = []
    params = []

    placeholder = "%s" if USE_POSTGRES else "?"

    if exam_id and exam_id.strip() and exam_id.strip() != "all":
        conditions.append(f"exam_id = {placeholder}")
        params.append(exam_id.strip())
    if student_class and student_class.strip() and student_class.strip() != "all":
        conditions.append(f"UPPER(student_class) = UPPER({placeholder})")
        params.append(student_class.strip())
    if submission_date and submission_date.strip() and submission_date.strip() != "all":
        conditions.append(f"submitted_at LIKE {placeholder}")
        params.append(f"{submission_date.strip()}%")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY submitted_at DESC"
    c.execute(query, tuple(params))
    rows = c.fetchall()
    conn.close()

    results = []
    for row in rows:
        item = {
            "submission_id": row[0],
            "student_name": row[1],
            "student_class": row[2],
            "exam_id": row[3],
            "submitted_at": row[4],
            "duration_seconds": row[5],
            "status": row[6] if USE_POSTGRES else row[7],
            "scores": {},
            "is_online_exam": False,
            "screen_switch_count": 0,
            "switch_violations": [],
        }

        if USE_POSTGRES:
            if row[7]:
                try:
                    item["scores"] = json.loads(row[7]) or {}
                except Exception:
                    pass
            item["is_online_exam"] = bool(row[8]) if len(row) > 8 and row[8] is not None else False
            item["screen_switch_count"] = int(row[9]) if len(row) > 9 and row[9] is not None else 0
        else:
            if row[6]:
                try:
                    res_d = json.loads(row[6])
                    item["scores"] = res_d.get("scores", {}) or {}
                    if "is_online_exam" in res_d:
                        item["is_online_exam"] = bool(res_d["is_online_exam"])
                    if "screen_switch_count" in res_d:
                        item["screen_switch_count"] = int(res_d["screen_switch_count"])
                    if "switch_violations" in res_d:
                        item["switch_violations"] = res_d["switch_violations"]
                except Exception:
                    pass

            if len(row) > 8 and row[8]:
                try:
                    ans_d = json.loads(row[8])
                    if not item["is_online_exam"] and "is_online_exam" in ans_d:
                        item["is_online_exam"] = bool(ans_d["is_online_exam"])
                    if item["screen_switch_count"] == 0 and "screen_switch_count" in ans_d:
                        item["screen_switch_count"] = int(ans_d["screen_switch_count"])
                    if not item["switch_violations"] and "switch_violations" in ans_d:
                        item["switch_violations"] = ans_d["switch_violations"]
                except Exception:
                    pass

        meta = exam_meta_map.get(item["exam_id"], {})
        item["exam_title"] = meta.get("title", "")
        item["subject"] = meta.get("subject", "")
        item["exam_created_by"] = meta.get("created_by", "")
        if not item["is_online_exam"] and meta.get("is_online_exam"):
            item["is_online_exam"] = True

        results.append(item)

    return results


def delete_submission(submission_id: str, allowed_exam_ids: List[str] = None) -> bool:
    """Xóa một bài nộp của học sinh (đồng bộ xóa cả trên Neon và SQLite cục bộ)."""
    deleted = False

    # 1. Xóa Neon PostgreSQL (nếu có)
    if USE_POSTGRES:
        try:
            conn = get_connection()
            c = conn.cursor()
            if allowed_exam_ids is not None:
                if not allowed_exam_ids:
                    conn.close()
                    return False
                c.execute("DELETE FROM submissions WHERE id = %s AND exam_id = ANY(%s)", (submission_id, allowed_exam_ids))
            else:
                c.execute("DELETE FROM submissions WHERE id = %s", (submission_id,))
            deleted = c.rowcount > 0
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Lỗi xóa bài trên Neon: {e}")

    # 2. ĐỒNG BỘ 100%: Xóa luôn trong SQLite cục bộ
    if DB_PATH.exists():
        try:
            sq_conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
            c_sq = sq_conn.cursor()
            if allowed_exam_ids is not None:
                if allowed_exam_ids:
                    ph = ",".join("?" for _ in allowed_exam_ids)
                    c_sq.execute(f"DELETE FROM submissions WHERE id = ? AND exam_id IN ({ph})", (submission_id, *allowed_exam_ids))
            else:
                c_sq.execute("DELETE FROM submissions WHERE id = ?", (submission_id,))
            if not USE_POSTGRES:
                deleted = c_sq.rowcount > 0
            sq_conn.commit()
            sq_conn.close()
        except Exception:
            pass

    return deleted


def delete_all_submissions(exam_id: str = None, exam_ids: List[str] = None) -> int:
    """Xóa tất cả bài nộp (hoặc theo đề thi) - đồng bộ xóa cả trên Neon và SQLite."""
    count = 0

    # 1. Xóa Neon PostgreSQL
    if USE_POSTGRES:
        try:
            conn = get_connection()
            c = conn.cursor()
            if exam_ids is not None:
                if exam_ids:
                    c.execute("DELETE FROM submissions WHERE exam_id = ANY(%s)", (exam_ids,))
                    count = c.rowcount
            elif exam_id and exam_id.strip() and exam_id.strip() != "all":
                c.execute("DELETE FROM submissions WHERE exam_id = %s", (exam_id.strip(),))
                count = c.rowcount
            else:
                c.execute("DELETE FROM submissions")
                count = c.rowcount
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Lỗi xóa tất cả bài trên Neon: {e}")

    # 2. ĐỒNG BỘ 100%: Xóa luôn trong SQLite cục bộ & giải phóng dung lượng
    if DB_PATH.exists():
        try:
            sq_conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
            c_sq = sq_conn.cursor()
            if exam_ids is not None:
                if exam_ids:
                    ph = ",".join("?" for _ in exam_ids)
                    c_sq.execute(f"DELETE FROM submissions WHERE exam_id IN ({ph})", tuple(exam_ids))
            elif exam_id and exam_id.strip() and exam_id.strip() != "all":
                c_sq.execute("DELETE FROM submissions WHERE exam_id = ?", (exam_id.strip(),))
            else:
                c_sq.execute("DELETE FROM submissions")
            if not USE_POSTGRES:
                count = c_sq.rowcount
            sq_conn.commit()
            # Nén và giải phóng dung lượng đĩa SQLite
            sq_conn.execute("VACUUM")
            sq_conn.close()
        except Exception:
            pass

    return count


def delete_submissions_batch(submission_ids: List[str], allowed_exam_ids: List[str] = None) -> int:
    """Xóa hàng loạt bài nộp đã chọn qua checkbox (đồng bộ xóa cả trên Neon và SQLite)."""
    clean_ids = [str(sid).strip() for sid in submission_ids if str(sid).strip()]
    if not clean_ids:
        return 0

    count = 0
    # 1. Xóa trên Neon PostgreSQL
    if USE_POSTGRES:
        try:
            conn = get_connection()
            c = conn.cursor()
            if allowed_exam_ids is not None:
                if allowed_exam_ids:
                    c.execute("DELETE FROM submissions WHERE id = ANY(%s) AND exam_id = ANY(%s)", (clean_ids, allowed_exam_ids))
                    count = c.rowcount
            else:
                c.execute("DELETE FROM submissions WHERE id = ANY(%s)", (clean_ids,))
                count = c.rowcount
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Lỗi xóa hàng loạt trên Neon: {e}")

    # 2. ĐỒNG BỘ 100%: Xóa luôn trên SQLite cục bộ
    if DB_PATH.exists():
        try:
            sq_conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
            c_sq = sq_conn.cursor()
            id_ph = ",".join("?" for _ in clean_ids)
            if allowed_exam_ids is not None:
                if allowed_exam_ids:
                    ex_ph = ",".join("?" for _ in allowed_exam_ids)
                    c_sq.execute(f"DELETE FROM submissions WHERE id IN ({id_ph}) AND exam_id IN ({ex_ph})", (*clean_ids, *allowed_exam_ids))
            else:
                c_sq.execute(f"DELETE FROM submissions WHERE id IN ({id_ph})", tuple(clean_ids))
            if not USE_POSTGRES:
                count = c_sq.rowcount
            sq_conn.commit()
            sq_conn.close()
        except Exception:
            pass

    return count


# ===================== QUẢN LÝ CẤU HÌNH (CONFIG) =====================

def get_config(key: str, default=None):
    """Lấy giá trị cấu hình theo key."""
    conn = get_connection()
    c = conn.cursor()
    if USE_POSTGRES:
        c.execute("SELECT value FROM config WHERE key = %s", (key,))
    else:
        c.execute("SELECT value FROM config WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row[0])
        except Exception:
            return row[0]
    return default


def set_config(key: str, value):
    """Thiết lập giá trị cấu hình theo key."""
    conn = get_connection()
    c = conn.cursor()
    val_str = json.dumps(value, ensure_ascii=False)
    if USE_POSTGRES:
        c.execute("""
            INSERT INTO config (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (key, val_str))
    else:
        c.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, val_str))
    conn.commit()
    conn.close()


def get_database_storage_info() -> dict:
    """Kiểm tra dung lượng CSDL ngoài (Neon PostgreSQL) và SQLite dự phòng."""
    info = {
        "engine": "PostgreSQL (Neon Cloud)" if USE_POSTGRES else "SQLite (Local)",
        "is_postgres": USE_POSTGRES,
        "pg_size_bytes": 0,
        "pg_size_pretty": "0 MB",
        "pg_limit_bytes": 512 * 1024 * 1024, # 512 MB Free Tier của Neon
        "pg_usage_percent": 0.0,
        "sqlite_size_bytes": 0,
        "sqlite_size_pretty": "0 MB",
        "sqlite_submissions_count": 0,
        "has_storage_warning": False,
        "warning_message": "",
        "storage_alert": ""
    }
    
    # 1. Kích thước SQLite
    if DB_PATH.exists():
        try:
            sz = DB_PATH.stat().st_size
            info["sqlite_size_bytes"] = sz
            info["sqlite_size_pretty"] = f"{round(sz / (1024 * 1024), 2)} MB"
            
            sq_conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
            c_sq = sq_conn.cursor()
            c_sq.execute("SELECT COUNT(*) FROM submissions")
            info["sqlite_submissions_count"] = c_sq.fetchone()[0]
            
            c_sq.execute("SELECT value FROM config WHERE key = 'storage_alert'")
            row_alert = c_sq.fetchone()
            if row_alert and row_alert[0]:
                info["storage_alert"] = str(row_alert[0])
            sq_conn.close()
        except Exception:
            pass

    # 2. Kích thước PostgreSQL (Neon)
    if USE_POSTGRES:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute("SELECT pg_database_size(current_database()), pg_size_pretty(pg_database_size(current_database()))")
            row = c.fetchone()
            if row:
                info["pg_size_bytes"] = int(row[0])
                info["pg_size_pretty"] = str(row[1])
                pct = round((info["pg_size_bytes"] / info["pg_limit_bytes"]) * 100, 1)
                info["pg_usage_percent"] = pct
                
                if pct >= 80.0:
                    info["has_storage_warning"] = True
                    info["warning_message"] = f"Dung lượng CSDL Neon đã đạt {pct}% ({info['pg_size_pretty']}/512 MB). Đề nghị Super Admin thông báo giáo viên xóa bớt các bài thi cũ!"
            conn.close()
        except Exception as e:
            info["has_storage_warning"] = True
            info["warning_message"] = f"Lỗi kiểm tra dung lượng Neon: {e}"
            
    if info.get("storage_alert"):
        info["has_storage_warning"] = True
        info["warning_message"] = info["storage_alert"]

    return info


def clear_storage_alert() -> bool:
    """Xóa thông báo sự cố dung lượng sau khi Super Admin đã xử lý."""
    try:
        if DB_PATH.exists():
            sq_conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
            sq_conn.execute("DELETE FROM config WHERE key = 'storage_alert'")
            sq_conn.commit()
            sq_conn.close()
        return True
    except Exception:
        return False


# ===================== QUẢN LÝ TÀI KHOẢN (ADMIN & GIÁO VIÊN) =====================

def get_user_by_username(username: str) -> Optional[dict]:
    """Tìm thông tin tài khoản theo username."""
    if not username:
        return None
    conn = get_connection()
    c = conn.cursor()
    uname = username.strip().lower()

    if USE_POSTGRES:
        c.execute("""
            SELECT id, username, password_hash, full_name, role, is_protected, created_at, updated_at, subject
            FROM admin_users WHERE LOWER(username) = %s
        """, (uname,))
    else:
        c.execute("""
            SELECT id, username, password_hash, full_name, role, is_protected, created_at, updated_at, subject
            FROM admin_users WHERE LOWER(username) = ?
        """, (uname,))

    row = c.fetchone()
    conn.close()
    if not row:
        return None

    return {
        "id": row[0],
        "username": row[1],
        "password_hash": row[2],
        "full_name": row[3] or "",
        "role": row[4] or "teacher",
        "is_protected": bool(row[5]),
        "created_at": str(row[6]),
        "updated_at": str(row[7]),
        "subject": row[8] or ""
    }


def get_all_users() -> List[dict]:
    """Lấy danh sách tất cả tài khoản người dùng."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, username, full_name, role, is_protected, created_at, updated_at, subject
        FROM admin_users ORDER BY is_protected DESC, id ASC
    """)
    rows = c.fetchall()
    conn.close()

    users = []
    for r in rows:
        users.append({
            "id": r[0],
            "username": r[1],
            "full_name": r[2] or "",
            "role": r[3] or "teacher",
            "is_protected": bool(r[4]),
            "created_at": str(r[5]),
            "updated_at": str(r[6]),
            "subject": r[7] or ""
        })
    return users


def create_user(username: str, password: str, full_name: str = "", role: str = "teacher", subject: str = "") -> dict:
    """Tạo tài khoản người dùng mới."""
    clean_username = username.strip()
    if not clean_username or len(clean_username) < 3:
        raise ValueError("Tên đăng nhập phải có ít nhất 3 ký tự!")
    if not password or len(password) < 6:
        raise ValueError("Mật khẩu phải có ít nhất 6 ký tự!")

    if clean_username.lower() == "admin":
        raise ValueError("Tên đăng nhập 'admin' là tài khoản mặc định của hệ thống!")

    conn = get_connection()
    c = conn.cursor()
    if USE_POSTGRES:
        c.execute("SELECT id FROM admin_users WHERE LOWER(username) = %s", (clean_username.lower(),))
    else:
        c.execute("SELECT id FROM admin_users WHERE LOWER(username) = ?", (clean_username.lower(),))
        
    if c.fetchone():
        conn.close()
        raise ValueError(f"Tên đăng nhập '{clean_username}' đã tồn tại trong hệ thống!")

    p_hash = hash_password(password)
    user_id = None

    if USE_POSTGRES:
        c.execute("""
            INSERT INTO admin_users (username, password_hash, full_name, role, is_protected, subject)
            VALUES (%s, %s, %s, %s, 0, %s)
            RETURNING id
        """, (clean_username, p_hash, full_name.strip(), role.strip(), subject.strip()))
        user_id = c.fetchone()[0]
    else:
        c.execute("""
            INSERT INTO admin_users (username, password_hash, full_name, role, is_protected, subject, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, datetime('now'), datetime('now'))
        """, (clean_username, p_hash, full_name.strip(), role.strip(), subject.strip()))
        user_id = c.lastrowid

    conn.commit()
    conn.close()

    return {
        "id": user_id,
        "username": clean_username,
        "full_name": full_name.strip(),
        "role": role,
        "is_protected": False,
        "subject": subject.strip()
    }


def update_user_password(username: str, new_password: str) -> bool:
    """Cập nhật mật khẩu mới cho người dùng."""
    clean_username = username.strip()
    if not new_password or len(new_password) < 6:
        raise ValueError("Mật khẩu mới phải có ít nhất 6 ký tự!")

    user = get_user_by_username(clean_username)
    if not user:
        raise ValueError(f"Không tìm thấy tài khoản '{clean_username}'!")

    p_hash = hash_password(new_password)
    conn = get_connection()
    c = conn.cursor()

    if USE_POSTGRES:
        c.execute("""
            UPDATE admin_users SET password_hash = %s, updated_at = CURRENT_TIMESTAMP
            WHERE LOWER(username) = %s
        """, (p_hash, clean_username.lower()))
    else:
        c.execute("""
            UPDATE admin_users SET password_hash = ?, updated_at = datetime('now')
            WHERE LOWER(username) = ?
        """, (p_hash, clean_username.lower()))

    conn.commit()
    conn.close()
    return True


def delete_user(username: str) -> bool:
    """Xóa tài khoản người dùng."""
    clean_username = username.strip().lower()
    if clean_username == "admin":
        raise ValueError("Tài khoản mặc định 'admin' được bảo vệ và KHÔNG THỂ XÓA!")

    user = get_user_by_username(clean_username)
    if not user:
        raise ValueError(f"Không tìm thấy tài khoản '{username}' để xóa!")
    if user.get("is_protected"):
        raise ValueError(f"Tài khoản '{user['username']}' là tài khoản được bảo vệ và không thể xóa!")

    if USE_POSTGRES:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute("DELETE FROM admin_users WHERE LOWER(username) = %s", (clean_username,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Lỗi xóa user trên Neon: {e}")

    # ĐỒNG BỘ 100%: Xóa luôn trên SQLite
    if DB_PATH.exists():
        try:
            sq_conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
            sq_conn.execute("DELETE FROM admin_users WHERE LOWER(username) = ?", (clean_username,))
            sq_conn.commit()
            sq_conn.close()
        except Exception:
            pass

    return True


def update_user_role(username: str, role: str) -> bool:
    """Thay đổi vai trò tài khoản (admin hoặc teacher)."""
    clean_username = username.strip().lower()
    clean_role = role.strip().lower()
    if clean_role not in ["admin", "teacher"]:
        raise ValueError("Vai trò hợp lệ chỉ bao gồm 'admin' hoặc 'teacher'!")

    if clean_username == "admin":
        raise ValueError("Tài khoản 'admin' là tài khoản tối cao, không thể đổi vai trò!")

    user = get_user_by_username(clean_username)
    if not user:
        raise ValueError(f"Không tìm thấy tài khoản '{username}'!")
    if user.get("is_protected"):
        raise ValueError(f"Tài khoản '{user['username']}' là tài khoản được bảo vệ, không thể đổi vai trò!")

    conn = get_connection()
    c = conn.cursor()
    if USE_POSTGRES:
        c.execute("UPDATE admin_users SET role = %s, updated_at = CURRENT_TIMESTAMP WHERE LOWER(username) = %s", (clean_role, clean_username))
    else:
        c.execute("UPDATE admin_users SET role = ?, updated_at = datetime('now') WHERE LOWER(username) = ?", (clean_role, clean_username))
    conn.commit()
    conn.close()
    return True


# ===================== SAO LƯU & KHÔI PHỤC CSDL =====================

def export_full_backup(subject: str = None, owner: str = None) -> dict:
    """Xuất file JSON sao lưu toàn bộ hoặc theo giáo viên."""
    conn = get_connection()
    c = conn.cursor()

    clean_owner = owner.strip().lower() if owner and str(owner).strip() else None
    clean_sub = subject.strip().lower() if subject and str(subject).strip() else None

    # 1. Đề thi
    exams_map = {}
    c.execute("SELECT id, title, subject, grade, data_json, created_by FROM exams")
    for r in c.fetchall():
        try:
            ex = json.loads(r[4])
            ex_sub = (r[2] or ex.get("subject") or "").strip().lower()
            ex_owner = (r[5] or ex.get("created_by") or "").strip().lower()
            if clean_owner and ex_owner != clean_owner:
                continue
            if clean_sub and not clean_owner and ex_sub != clean_sub:
                continue
            exams_map[r[0]] = ex
        except Exception:
            pass

    allowed_eids = set(exams_map.keys())

    # 2. Bài thi
    c.execute("SELECT id, student_name, student_class, exam_id, started_at, submitted_at, duration_seconds, answers_json, result_json, status FROM submissions")
    sub_rows = c.fetchall()
    submissions = []
    for r in sub_rows:
        if (clean_owner or clean_sub) and r[3] not in allowed_eids:
            continue
        submissions.append({
            "id": r[0], "student_name": r[1], "student_class": r[2], "exam_id": r[3],
            "started_at": r[4], "submitted_at": r[5], "duration_seconds": r[6],
            "answers_json": r[7], "result_json": r[8], "status": r[9]
        })

    # 3. Cấu hình
    config = {}
    if not clean_owner and not clean_sub:
        c.execute("SELECT key, value FROM config")
        config = dict(c.fetchall())

    # 4. Tài khoản
    users = []
    if not clean_owner and not clean_sub:
        c.execute("SELECT id, username, password_hash, full_name, role, is_protected, subject, created_at, updated_at FROM admin_users")
        for r in c.fetchall():
            users.append({
                "id": r[0], "username": r[1], "password_hash": r[2], "full_name": r[3],
                "role": r[4], "is_protected": r[5], "subject": r[6] or "", "created_at": str(r[7]), "updated_at": str(r[8])
            })

    conn.close()

    scope_str = f"owner:{owner}" if clean_owner else (f"subject:{subject}" if clean_sub else "full")
    return {
        "version": "2026.1",
        "exported_at": datetime.now().isoformat(),
        "scope": scope_str,
        "owner": owner if clean_owner else None,
        "subject": subject if clean_sub else None,
        "exams": list(exams_map.values()),
        "submissions": submissions,
        "users": users,
        "config": config
    }


def import_full_backup(backup: dict, allowed_subject: str = None, allowed_owner: str = None) -> dict:
    """Khôi phục dữ liệu từ file sao lưu JSON."""
    conn = get_connection()
    c = conn.cursor()

    restored_exams = 0
    restored_subs = 0
    restored_users = 0
    skipped_other_exams = 0

    clean_owner = allowed_owner.strip().lower() if allowed_owner and str(allowed_owner).strip() else None
    clean_sub = allowed_subject.strip().lower() if allowed_subject and str(allowed_subject).strip() else None

    # 1. Phục hồi đề thi
    exams = backup.get("exams", [])
    valid_eids = set()
    for e in exams:
        eid = e.get("id")
        if not eid:
            continue
        ex_owner = (e.get("created_by") or "").strip().lower()
        ex_sub = (e.get("subject") or "").strip().lower()
        if clean_owner and ex_owner and ex_owner != clean_owner:
            skipped_other_exams += 1
            continue
        if clean_sub and not clean_owner and ex_sub != clean_sub:
            skipped_other_exams += 1
            continue

        target_owner = allowed_owner if clean_owner else (e.get("created_by") or "admin")
        e["created_by"] = target_owner
        valid_eids.add(eid)

        save_exam_record(e)
        restored_exams += 1

    # 2. Phục hồi bài thi
    submissions = backup.get("submissions", [])
    for s in submissions:
        sid = s.get("id")
        eid = s.get("exam_id")
        if not sid or not eid:
            continue
        if (clean_owner or clean_sub) and eid not in valid_eids:
            continue

        dur = s.get("duration_seconds")
        try:
            dur = int(dur) if dur is not None else 0
        except Exception:
            dur = 0

        aj = s.get("answers_json")
        if isinstance(aj, (dict, list)):
            aj = json.dumps(aj, ensure_ascii=False)
        rj = s.get("result_json")
        if isinstance(rj, (dict, list)):
            rj = json.dumps(rj, ensure_ascii=False)

        if USE_POSTGRES:
            c.execute("""
                INSERT INTO submissions (id, student_name, student_class, exam_id, started_at, submitted_at, duration_seconds, answers_json, result_json, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    student_name = EXCLUDED.student_name,
                    student_class = EXCLUDED.student_class,
                    exam_id = EXCLUDED.exam_id,
                    started_at = EXCLUDED.started_at,
                    submitted_at = EXCLUDED.submitted_at,
                    duration_seconds = EXCLUDED.duration_seconds,
                    answers_json = EXCLUDED.answers_json,
                    result_json = EXCLUDED.result_json,
                    status = EXCLUDED.status
            """, (
                sid, s.get("student_name", ""), s.get("student_class", ""), eid,
                s.get("started_at", ""), s.get("submitted_at", ""), dur, aj, rj,
                s.get("status", "submitted")
            ))
        else:
            c.execute("""
                INSERT OR REPLACE INTO submissions (id, student_name, student_class, exam_id, started_at, submitted_at, duration_seconds, answers_json, result_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sid, s.get("student_name", ""), s.get("student_class", ""), eid,
                s.get("started_at", ""), s.get("submitted_at", ""), dur, aj, rj,
                s.get("status", "submitted")
            ))
        restored_subs += 1

    # 3. Phục hồi tài khoản
    if not clean_owner and not clean_sub:
        users = backup.get("users", [])
        for u in users:
            try:
                uname = (u.get("username") or "").strip()
                phash = u.get("password_hash")
                if not uname or not phash:
                    continue
                if USE_POSTGRES:
                    c.execute("""
                        INSERT INTO admin_users (username, password_hash, full_name, role, is_protected, subject)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (username) DO UPDATE SET
                            password_hash = EXCLUDED.password_hash,
                            full_name = EXCLUDED.full_name,
                            role = EXCLUDED.role,
                            subject = EXCLUDED.subject,
                            updated_at = CURRENT_TIMESTAMP
                    """, (uname, phash, u.get("full_name", ""), u.get("role", "teacher"), 1 if u.get("is_protected") else 0, u.get("subject", "")))
                else:
                    c.execute("SELECT id, is_protected FROM admin_users WHERE LOWER(username) = LOWER(?)", (uname,))
                    ex_u = c.fetchone()
                    if ex_u:
                        c.execute("""
                            UPDATE admin_users SET password_hash = ?, full_name = ?, role = ?, subject = ?, updated_at = datetime('now')
                            WHERE id = ?
                        """, (phash, u.get("full_name", ""), u.get("role", "teacher"), u.get("subject", ""), ex_u[0]))
                    else:
                        c.execute("""
                            INSERT INTO admin_users (username, password_hash, full_name, role, is_protected, subject, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                        """, (uname, phash, u.get("full_name", ""), u.get("role", "teacher"), 1 if u.get("is_protected") else 0, u.get("subject", "")))
                restored_users += 1
            except Exception:
                pass

    conn.commit()
    conn.close()

    scope_str = f"owner:{allowed_owner}" if clean_owner else (f"subject:{allowed_subject}" if clean_sub else "full")
    return {
        "restored_exams": restored_exams,
        "restored_submissions": restored_subs,
        "restored_users": restored_users,
        "skipped_other_exams": skipped_other_exams,
        "scope": scope_str
    }


def export_users_backup() -> dict:
    """Sao lưu danh sách tài khoản giáo viên."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, username, password_hash, full_name, role, is_protected, subject, created_at, updated_at 
        FROM admin_users ORDER BY is_protected DESC, id ASC
    """)
    users = []
    for r in c.fetchall():
        users.append({
            "id": r[0], "username": r[1], "password_hash": r[2], "full_name": r[3] or "",
            "role": r[4] or "teacher", "is_protected": bool(r[5]), "subject": r[6] or "",
            "created_at": str(r[7]), "updated_at": str(r[8])
        })
    conn.close()
    return {
        "version": "2026.1",
        "type": "users_backup",
        "exported_at": datetime.now().isoformat(),
        "total": len(users),
        "users": users
    }


def import_users_backup(backup: dict) -> dict:
    """Khôi phục danh sách tài khoản từ file sao lưu."""
    users = backup.get("users", [])
    if not isinstance(users, list):
        raise ValueError("Dữ liệu sao lưu tài khoản không hợp lệ!")

    conn = get_connection()
    c = conn.cursor()
    restored = 0
    updated = 0
    skipped = 0

    for u in users:
        uname = (u.get("username") or "").strip()
        phash = u.get("password_hash")
        if not uname or not phash:
            skipped += 1
            continue

        try:
            if USE_POSTGRES:
                c.execute("""
                    INSERT INTO admin_users (username, password_hash, full_name, role, is_protected, subject)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (username) DO UPDATE SET
                        password_hash = EXCLUDED.password_hash,
                        full_name = EXCLUDED.full_name,
                        role = EXCLUDED.role,
                        subject = EXCLUDED.subject,
                        updated_at = CURRENT_TIMESTAMP
                """, (uname, phash, u.get("full_name", ""), u.get("role", "teacher"), 1 if u.get("is_protected") else 0, u.get("subject", "")))
                restored += 1
            else:
                c.execute("SELECT id, is_protected FROM admin_users WHERE LOWER(username) = LOWER(?)", (uname,))
                row = c.fetchone()
                if row:
                    c.execute("""
                        UPDATE admin_users SET password_hash = ?, full_name = ?, role = ?, subject = ?, updated_at = datetime('now')
                        WHERE id = ?
                    """, (phash, u.get("full_name", ""), u.get("role", "teacher"), u.get("subject", ""), row[0]))
                    updated += 1
                else:
                    c.execute("""
                        INSERT INTO admin_users (username, password_hash, full_name, role, is_protected, subject, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                    """, (uname, phash, u.get("full_name", ""), u.get("role", "teacher"), 1 if u.get("is_protected") else 0, u.get("subject", "")))
                    restored += 1
        except Exception as e:
            logger.warning(f"Lỗi khôi phục tài khoản {uname}: {e}")
            skipped += 1

    conn.commit()
    conn.close()
    return {
        "restored": restored,
        "updated": updated,
        "skipped": skipped,
        "total": len(users)
    }
