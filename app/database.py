"""
Database - Lưu trữ bài thi và cấu hình dùng SQLite + JSON
"""
import json
import sqlite3
import os
import re
import base64
import io
import hashlib
import logging
from datetime import datetime
from typing import Optional, List
from pathlib import Path

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


def hash_password(password: str) -> str:
    """Băm mật khẩu an toàn với salt cố định."""
    raw = f"{PASSWORD_SALT}:{password.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_exam_published(val) -> bool:
    """Kiểm tra một đề thi có đang ở trạng thái xuất bản (True) hay không. Mặc định đề cũ chưa có cờ là True."""
    if val is None:
        return True
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "0", "off", "no", "chua_xuat_ban")
    return bool(val)


def get_connection() -> sqlite3.Connection:
    """Lấy kết nối SQLite tối ưu hóa đa luồng, hỗ trợ hàng trăm thí sinh nộp bài cùng lúc."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return conn


def init_db():
    """Khởi tạo database SQLite."""
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
    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS exams (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            subject TEXT,
            grade TEXT,
            data_json TEXT NOT NULL,
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
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Migration: thêm cột subject cho admin_users
    try:
        c.execute("ALTER TABLE admin_users ADD COLUMN subject TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass
    # Migration: thêm cột created_by cho exams
    try:
        c.execute("ALTER TABLE exams ADD COLUMN created_by TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass

    # Khởi tạo hoặc cập nhật tài khoản mặc định: admin / Longcang2026@ (bất khả xâm phạm)
    c.execute("SELECT id FROM admin_users WHERE LOWER(username) = 'admin'")
    row = c.fetchone()
    if not row:
        c.execute("""
            INSERT INTO admin_users (username, password_hash, full_name, role, is_protected, created_at, updated_at)
            VALUES ('admin', ?, 'Quản trị viên mặc định', 'admin', 1, datetime('now'), datetime('now'))
        """, (hash_password("Longcang2026@"),))
        logger.info("Đã tạo tài khoản admin mặc định: admin (is_protected=1)")
    else:
        # Đảm bảo tài khoản admin luôn có cờ is_protected = 1
        c.execute("UPDATE admin_users SET is_protected = 1, role = 'admin' WHERE LOWER(username) = 'admin'")

    # Đồng bộ các file đề thi JSON sẵn có trong EXAMS_DIR vào bảng exams trong SQLite
    try:
        for f in sorted(EXAMS_DIR.glob("*.json")):
            try:
                with open(f, encoding="utf-8") as jf:
                    data = json.load(jf)
                    eid = data.get("id") or f.stem
                    if eid:
                        c.execute("SELECT id FROM exams WHERE id = ?", (eid,))
                        if not c.fetchone():
                            c.execute("""
                                INSERT INTO exams (id, title, subject, grade, data_json, created_by, created_at, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                            """, (
                                eid,
                                data.get("title", ""),
                                data.get("subject", ""),
                                str(data.get("grade", "")),
                                json.dumps(data, ensure_ascii=False),
                                data.get("created_by", "")
                            ))
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"Lỗi khi đồng bộ đề thi vào SQLite: {e}")

    # Migration: Cập nhật và gán chính xác người tạo created_by cho từng đề thi hiện có
    try:
        c.execute("SELECT id, data_json, created_by FROM exams")
        for eid, dj, c_by in c.fetchall():
            owner = (c_by or "").strip()
            if not owner and dj:
                try:
                    parsed_dj = json.loads(dj)
                    owner = (parsed_dj.get("created_by") or "").strip()
                except Exception:
                    pass
            # Phân định chủ sở hữu chuẩn xác cho các đề thi hiện hữu
            if not owner:
                if eid in ("exam_41baf78d", "exam_67a6b4b7"):
                    owner = "hongquy"
                elif eid == "exam_ef2cc220":
                    owner = "haidhsp2005"
                elif eid == "exam_26b69ee2":
                    owner = "gv_mythuat"
                elif eid == "exam_33da9fe3":
                    owner = "gv_qp"
                else:
                    owner = "admin"
            if owner != (c_by or "").strip():
                c.execute("UPDATE exams SET created_by = ? WHERE id = ?", (owner, eid))
                json_path = EXAMS_DIR / f"{eid}.json"
                if json_path.exists():
                    try:
                        jf_data = json.loads(json_path.read_text(encoding="utf-8"))
                        jf_data["created_by"] = owner
                        json_path.write_text(json.dumps(jf_data, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception:
                        pass
        conn.commit()
    except Exception as e:
        logger.warning(f"Lỗi migration created_by cho đề thi: {e}")

    conn.commit()
    conn.close()
    logger.info("Database initialized.")


def save_submission(submission_data: dict, result_data: dict = None):
    """Lưu bài nộp vào database."""
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
    
    c.execute("""
        INSERT OR REPLACE INTO submissions 
        (id, student_name, student_class, exam_id, started_at, submitted_at, duration_seconds, answers_json, result_json, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        submission_data["submission_id"],
        submission_data["student_name"].strip(),
        str(submission_data["student_class"]).strip().upper(),
        submission_data.get("exam_id", "exam_001"),
        submission_data.get("started_at", ""),
        submission_data.get("submitted_at", datetime.now().isoformat()),
        submission_data.get("duration_seconds", 0),
        json.dumps(answers, ensure_ascii=False),
        json.dumps(result_data, ensure_ascii=False) if result_data else None,
        "graded" if result_data else "pending"
    ))
    conn.commit()
    conn.close()


def get_submission(submission_id: str) -> Optional[dict]:
    """Lấy kết quả bài thi theo ID."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,))
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

    if row[7]:  # answers_json
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
        except:
            pass

    if row[8]:  # result_json
        try:
            result_data = json.loads(row[8])
            result.update(result_data)
            if "is_online_exam" in result_data:
                result["is_online_exam"] = bool(result_data["is_online_exam"])
            if "screen_switch_count" in result_data:
                result["screen_switch_count"] = int(result_data["screen_switch_count"])
            if "switch_violations" in result_data:
                result["switch_violations"] = result_data["switch_violations"]
        except:
            pass
            
    # Fallback kiểm tra đề thi gốc nếu chưa xác định được is_online_exam
    if not result["is_online_exam"] and result.get("exam_id"):
        exam_obj = load_exam(result["exam_id"])
        if exam_obj:
            result["is_online_exam"] = bool(exam_obj.get("is_online_exam", False))
    
    return result


def get_all_submissions(exam_id: str = None, student_class: str = None) -> List[dict]:
    """Lấy tất cả bài nộp (cho admin), hỗ trợ lọc linh hoạt theo Đề thi và theo Lớp."""
    conn = get_connection()
    c = conn.cursor()
    
    # Bản đồ exam_id -> is_online_exam dự phòng
    exam_online_map = {}
    try:
        c.execute("SELECT id, is_online_exam FROM exams")
        for row_e in c.fetchall():
            exam_online_map[row_e[0]] = bool(row_e[1])
    except Exception:
        pass
    
    query = "SELECT id, student_name, student_class, exam_id, submitted_at, duration_seconds, result_json, status, answers_json FROM submissions"
    conditions = []
    params = []
    
    if exam_id and exam_id.strip():
        conditions.append("exam_id = ?")
        params.append(exam_id.strip())
    if student_class and student_class.strip():
        conditions.append("UPPER(student_class) = UPPER(?)")
        params.append(student_class.strip())
        
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
            "status": row[7],
            "scores": {},
            "is_online_exam": False,
            "screen_switch_count": 0,
            "switch_violations": [],
        }
        
        # 1. Trích xuất từ result_json
        if row[6]:
            try:
                result_data = json.loads(row[6])
                item["scores"] = result_data.get("scores", {}) or {}
                if "is_online_exam" in result_data:
                    item["is_online_exam"] = bool(result_data["is_online_exam"])
                if "screen_switch_count" in result_data:
                    item["screen_switch_count"] = int(result_data["screen_switch_count"])
                if "switch_violations" in result_data:
                    item["switch_violations"] = result_data["switch_violations"]
            except:
                item["scores"] = {}
                
        # 2. Dự phòng kiểm tra answers_json
        if len(row) > 8 and row[8]:
            try:
                ans_data = json.loads(row[8])
                if not item["is_online_exam"] and "is_online_exam" in ans_data:
                    item["is_online_exam"] = bool(ans_data["is_online_exam"])
                if item["screen_switch_count"] == 0 and "screen_switch_count" in ans_data:
                    item["screen_switch_count"] = int(ans_data["screen_switch_count"])
                if not item["switch_violations"] and "switch_violations" in ans_data:
                    item["switch_violations"] = ans_data["switch_violations"]
            except:
                pass
                
        # 3. Dự phòng đối chiếu theo exam_id
        if not item["is_online_exam"] and item["exam_id"] in exam_online_map:
            item["is_online_exam"] = exam_online_map[item["exam_id"]]
            
        results.append(item)
    
    return results


def delete_submission(submission_id: str, allowed_exam_ids: List[str] = None) -> bool:
    """Xóa một bài nộp theo submission_id, có thể ràng buộc theo allowed_exam_ids."""
    conn = get_connection()
    c = conn.cursor()
    if allowed_exam_ids is not None:
        if not allowed_exam_ids:
            conn.close()
            return False
        placeholders = ",".join("?" for _ in allowed_exam_ids)
        c.execute(f"DELETE FROM submissions WHERE id = ? AND exam_id IN ({placeholders})", (submission_id, *allowed_exam_ids))
    else:
        c.execute("DELETE FROM submissions WHERE id = ?", (submission_id,))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def delete_all_submissions(exam_id: str = None, exam_ids: List[str] = None) -> int:
    """Xóa bài nộp (có thể lọc theo exam_id hoặc danh sách exam_ids theo phân quyền môn học)."""
    conn = get_connection()
    c = conn.cursor()
    if exam_id:
        c.execute("DELETE FROM submissions WHERE exam_id = ?", (exam_id,))
    elif exam_ids is not None:
        if not exam_ids:
            conn.close()
            return 0
        placeholders = ",".join("?" for _ in exam_ids)
        c.execute(f"DELETE FROM submissions WHERE exam_id IN ({placeholders})", tuple(exam_ids))
    else:
        c.execute("DELETE FROM submissions")
    count = c.rowcount
    conn.commit()
    conn.close()
    return count


def convert_bytes_to_base64_data_uri(image_bytes: bytes, ext: str = "png") -> str:
    """Chuyển đổi dữ liệu ảnh thành chuỗi Base64 Data URI, tự động tối ưu hóa kích thước nếu quá lớn."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        
        # Nếu ảnh quá lớn, resize lại để giữ file JSON gọn nhẹ (tối đa 1200px chiều dài/rộng)
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
    """Nếu URL là đường dẫn tĩnh cục bộ /static/uploads/..., chuyển thành Data URI base64 để nhúng thẳng vào JSON."""
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

    # Đối với phần II: kiểm tra ảnh của từng ý a, b, c, d
    items = q.get("items")
    if isinstance(items, dict):
        for item_key, item_val in items.items():
            if isinstance(item_val, dict) and item_val.get("image") and str(item_val["image"]).startswith("/static/"):
                new_item_img = file_url_to_base64(item_val["image"])
                if new_item_img != item_val["image"]:
                    item_val["image"] = new_item_img
                    modified = True

    # Đối với text có ảnh nhúng inline [IMAGE: /static/...] hoặc ![](...)
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
    """Tự động kiểm tra và chữa lành các câu hỏi bị kẹt phương án trong bảng HTML/text,
    hoặc chứa lỗi ngoặc nhọn KaTeX mồ côi (như 200{ m^{2}), chuẩn hóa hệ phương trình và lượng giác.
    Trả về True nếu có sửa đổi dữ liệu."""
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
            # Sửa ngoặc nhọn mồ côi và chuẩn hóa công thức KaTeX
            if text:
                new_text = clean_math_for_print(text)
                if new_text != text:
                    q["text"] = new_text
                    text = new_text
                    modified = True

            # Chuyển đổi đường dẫn ảnh tĩnh sang Base64 để lưu vĩnh viễn trong JSON
            if heal_question_images(q):
                modified = True

            opts = q.get("options") or {}
            # Chuẩn hóa công thức trong các phương án A, B, C, D
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

            # Chuyển đổi đường dẫn ảnh tĩnh sang Base64 để lưu vĩnh viễn trong JSON
            if heal_question_images(q):
                modified = True

            items = q.get("items") or {}
            # Chuẩn hóa công thức trong các mệnh đề con a, b, c, d
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


def load_exam(exam_id: str = "exam_001", fallback_to_any: bool = False) -> Optional[dict]:
    """Tải đề thi từ SQLite database hoặc file JSON (tìm theo filename hoặc thuộc tính id)."""
    if not exam_id:
        if not fallback_to_any:
            return None
        exam_id = "exam_001"

    def _wrap_exam(d):
        if d and isinstance(d, dict) and "is_published" not in d:
            d["is_published"] = True
        return d

    # 1. Thử tìm trong SQLite database (nhanh nhất và không phụ thuộc disk)
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT id, data_json FROM exams WHERE id = ?", (exam_id,))
        row = c.fetchone()
        if not row:
            if not exam_id.startswith("exam_"):
                c.execute("SELECT id, data_json FROM exams WHERE id = ?", (f"exam_{exam_id}",))
                row = c.fetchone()
            else:
                stripped = exam_id.replace("exam_", "", 1)
                c.execute("SELECT id, data_json FROM exams WHERE id = ?", (stripped,))
                row = c.fetchone()
        if not row:
            c.execute("SELECT id, data_json FROM exams WHERE title = ? LIMIT 1", (exam_id,))
            row = c.fetchone()
        conn.close()
        if row and row[1]:
            real_id = row[0]
            exam_data = json.loads(row[1])
            if heal_exam_data(exam_data):
                try:
                    conn2 = get_connection()
                    c2 = conn2.cursor()
                    c2.execute("UPDATE exams SET data_json = ?, updated_at = datetime('now') WHERE id = ?", 
                               (json.dumps(exam_data, ensure_ascii=False), real_id))
                    conn2.commit()
                    conn2.close()
                    logger.info(f"Đã tự động chữa lành và cập nhật đề thi {real_id} vào SQLite.")
                except Exception as update_err:
                    logger.warning(f"Lỗi cập nhật lại đề thi {real_id}: {update_err}")
            return _wrap_exam(exam_data)
    except Exception as ex:
        logger.warning(f"Lỗi đọc đề {exam_id} từ SQLite: {ex}")

    # 2. Thử theo tên file trực tiếp
    path = EXAMS_DIR / f"{exam_id}.json"
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return _wrap_exam(json.load(f))
        except Exception:
            pass

    # 3. Quét các file JSON trong EXAMS_DIR để so khớp theo id hoặc title
    for f in sorted(EXAMS_DIR.glob("*.json")):
        if f.name.endswith(".sqlite") or f.name.startswith("exam_db"):
            continue
        try:
            with open(f, encoding="utf-8") as jf:
                data = json.load(jf)
                if data.get("id") == exam_id or f.stem == exam_id or data.get("title") == exam_id:
                    return _wrap_exam(data)
        except Exception:
            continue

    # 4. CHỈ fallback khi fallback_to_any=True (ví dụ cứu hộ học sinh khi đề bị lỗi)
    if fallback_to_any:
        try:
            conn = get_connection()
            c = conn.cursor()
            c.execute("SELECT id, data_json FROM exams ORDER BY id DESC LIMIT 1")
            row = c.fetchone()
            conn.close()
            if row and row[1]:
                return _wrap_exam(json.loads(row[1]))
        except Exception:
            pass

        fallback = EXAMS_DIR / "exam_toan_12_101.json"
        if not fallback.exists():
            fallback = EXAMS_DIR / "sample_exam.json"
        if fallback.exists():
            try:
                with open(fallback, encoding="utf-8") as f:
                    return _wrap_exam(json.load(f))
            except Exception:
                pass

    return None


def get_config(key: str, default=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row[0])
        except:
            return row[0]
    return default


def set_config(key: str, value):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, json.dumps(value)))
    conn.commit()
    conn.close()


# ===================== QUẢN LÝ TÀI KHOẢN (ADMIN / GIÁO VIÊN) =====================

def get_user_by_username(username: str) -> Optional[dict]:
    """Tìm tài khoản theo tên đăng nhập (không phân biệt hoa thường)."""
    if not username:
        return None
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, username, password_hash, full_name, role, is_protected, created_at, updated_at, subject
        FROM admin_users WHERE LOWER(username) = LOWER(?)
    """, (username.strip(),))
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
        "created_at": row[6],
        "updated_at": row[7],
        "subject": row[8] or ""
    }


def get_all_users() -> List[dict]:
    """Lấy danh sách tất cả tài khoản (ẩn hash mật khẩu)."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, username, full_name, role, is_protected, created_at, updated_at, subject
        FROM admin_users ORDER BY is_protected DESC, created_at ASC
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
            "created_at": r[5],
            "updated_at": r[6],
            "subject": r[7] or ""
        })
    return users


def create_user(username: str, password: str, full_name: str = "", role: str = "teacher", subject: str = "") -> dict:
    """Tạo tài khoản giáo viên mới."""
    clean_username = username.strip()
    if not clean_username or len(clean_username) < 3:
        raise ValueError("Tên đăng nhập phải có ít nhất 3 ký tự!")
    if not password or len(password) < 6:
        raise ValueError("Mật khẩu phải có ít nhất 6 ký tự!")
    
    clean_username_lower = clean_username.lower()
    if clean_username_lower == "admin":
        raise ValueError("Tên đăng nhập 'admin' là tài khoản mặc định của hệ thống!")

    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM admin_users WHERE LOWER(username) = LOWER(?)", (clean_username_lower,))
    if c.fetchone():
        conn.close()
        raise ValueError(f"Tên đăng nhập '{clean_username}' đã tồn tại trong hệ thống!")

    p_hash = hash_password(password)
    c.execute("""
        INSERT INTO admin_users (username, password_hash, full_name, role, is_protected, subject, created_at, updated_at)
        VALUES (?, ?, ?, ?, 0, ?, datetime('now'), datetime('now'))
    """, (clean_username, p_hash, full_name.strip(), role.strip(), subject.strip()))
    user_id = c.lastrowid
    conn.commit()
    conn.close()

    logger.info(f"Đã tạo tài khoản mới: {clean_username} (role={role})")
    return {
        "id": user_id,
        "username": clean_username,
        "full_name": full_name.strip(),
        "role": role,
        "is_protected": False,
        "subject": subject.strip()
    }


def update_user_password(username: str, new_password: str) -> bool:
    """Đổi mật khẩu cho một tài khoản (kể cả admin)."""
    clean_username = username.strip()
    if not new_password or len(new_password) < 6:
        raise ValueError("Mật khẩu mới phải có ít nhất 6 ký tự!")

    user = get_user_by_username(clean_username)
    if not user:
        raise ValueError(f"Không tìm thấy tài khoản '{clean_username}'!")

    p_hash = hash_password(new_password)
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE admin_users SET password_hash = ?, updated_at = datetime('now')
        WHERE LOWER(username) = LOWER(?)
    """, (p_hash, clean_username))
    conn.commit()
    conn.close()

    logger.info(f"Đã cập nhật mật khẩu cho tài khoản: {clean_username}")
    return True


def delete_user(username: str) -> bool:
    """Xóa một tài khoản giáo viên. Không cho phép xóa tài khoản admin mặc định."""
    clean_username = username.strip().lower()
    if clean_username == "admin":
        raise ValueError("Tài khoản mặc định 'admin' được bảo vệ và KHÔNG THỂ XÓA!")

    user = get_user_by_username(clean_username)
    if not user:
        raise ValueError(f"Không tìm thấy tài khoản '{username}' để xóa!")

    if user.get("is_protected"):
        raise ValueError(f"Tài khoản '{user['username']}' là tài khoản được bảo vệ và không thể xóa!")

    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM admin_users WHERE LOWER(username) = LOWER(?)", (clean_username,))
    conn.commit()
    conn.close()

    logger.info(f"Đã xóa tài khoản: {username}")
    return True


def update_user_role(username: str, role: str) -> bool:
    """Thay đổi vai trò người dùng (admin hoặc teacher). Không cho phép thay đổi tài khoản admin mặc định."""
    clean_username = username.strip().lower()
    clean_role = role.strip().lower()
    if clean_role not in ["admin", "teacher"]:
        raise ValueError("Vai trò hợp lệ chỉ bao gồm 'admin' (Quản trị viên) hoặc 'teacher' (Giáo viên)!")

    if clean_username == "admin":
        raise ValueError("Tài khoản mặc định 'admin' là tài khoản hệ thống cao nhất, không thể đổi vai trò!")

    user = get_user_by_username(clean_username)
    if not user:
        raise ValueError(f"Không tìm thấy tài khoản '{username}' để đổi vai trò!")

    if user.get("is_protected"):
        raise ValueError(f"Tài khoản '{user['username']}' là tài khoản được bảo vệ, không thể đổi vai trò!")

    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE admin_users SET role = ?, updated_at = datetime('now') WHERE LOWER(username) = LOWER(?)", (clean_role, clean_username))
    conn.commit()
    conn.close()

    logger.info(f"Đã cập nhật vai trò của '{username}' thành '{clean_role}'")
    return True


def save_exam_record(exam_data: dict):
    """Lưu đề thi đồng thời vào cả file JSON và bảng exams trong SQLite."""
    exam_id = exam_data.get("id")
    if not exam_id:
        return

    # Bảo toàn created_by nếu exam_data bị thiếu nhưng database hoặc file cũ đã có
    created_by = (exam_data.get("created_by") or "").strip()
    if not created_by:
        try:
            conn_chk = get_connection()
            c_chk = conn_chk.cursor()
            c_chk.execute("SELECT created_by FROM exams WHERE id = ?", (exam_id,))
            r_chk = c_chk.fetchone()
            if r_chk and r_chk[0]:
                created_by = r_chk[0].strip()
            conn_chk.close()
        except Exception:
            pass
        if not created_by:
            json_path = EXAMS_DIR / f"{exam_id}.json"
            if json_path.exists():
                try:
                    old_j = json.loads(json_path.read_text(encoding="utf-8"))
                    created_by = (old_j.get("created_by") or "").strip()
                except Exception:
                    pass
    if not created_by:
        created_by = "admin"
    exam_data["created_by"] = created_by

    # Lưu ra file JSON
    try:
        json_path = EXAMS_DIR / f"{exam_id}.json"
        json_path.write_text(json.dumps(exam_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Lỗi ghi file JSON đề thi: {e}")

    # Lưu vào SQLite
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            INSERT OR REPLACE INTO exams (id, title, subject, grade, data_json, created_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            exam_id,
            exam_data.get("title", ""),
            exam_data.get("subject", ""),
            str(exam_data.get("grade", "")),
            json.dumps(exam_data, ensure_ascii=False),
            created_by
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Lỗi lưu đề thi vào SQLite: {e}")


def delete_exam_record(exam_id: str) -> bool:
    """Xóa đề thi khỏi cả SQLite database và tệp JSON trên đĩa."""
    deleted_any = False
    
    # 1. Xóa khỏi bảng SQLite exams
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
        if c.rowcount > 0:
            deleted_any = True
        c.execute("DELETE FROM exams WHERE id = ?", (f"exam_{exam_id}",))
        if c.rowcount > 0:
            deleted_any = True
        c.execute("DELETE FROM exams WHERE LOWER(id) = LOWER(?)", (exam_id,))
        if c.rowcount > 0:
            deleted_any = True
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Lỗi khi xóa đề {exam_id} khỏi SQLite: {e}")

    # 2. Xóa các file JSON trên đĩa
    candidate_dirs = [EXAMS_DIR]
    if DATA_DIR not in candidate_dirs:
        candidate_dirs.append(DATA_DIR)

    for d in candidate_dirs:
        # Xóa theo tên trực tiếp
        for fname in [f"{exam_id}.json", f"exam_{exam_id}.json"]:
            p = d / fname
            if p.exists():
                try:
                    p.unlink()
                    deleted_any = True
                except Exception as ex:
                    logger.warning(f"Lỗi xóa file {p}: {ex}")

        # Quét các file json nếu có "id" trùng với exam_id hoặc f.stem trùng
        try:
            for f in d.glob("*.json"):
                if f.name.endswith(".sqlite") or f.name.startswith("exam_db"):
                    continue
                if f.stem in (exam_id, f"exam_{exam_id}"):
                    try:
                        f.unlink()
                        deleted_any = True
                    except Exception:
                        pass
                    continue
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if data.get("id") == exam_id or data.get("id") == f"exam_{exam_id}":
                        f.unlink()
                        deleted_any = True
                except Exception:
                    pass
        except Exception:
            pass

    return deleted_any


def export_full_backup(subject: str = None, owner: str = None) -> dict:
    """
    Xuất file JSON sao lưu.
    - Nếu không có owner và subject (Super Admin): Xuất toàn bộ hệ thống (Đề thi + Bài thi + Tài khoản + Cấu hình).
    - Nếu có owner (Giáo viên): CHỈ xuất đề thi và bài thi do chính tài khoản giáo viên đó tạo.
    """
    conn = get_connection()
    c = conn.cursor()

    clean_sub = subject.strip().lower() if subject and str(subject).strip() else None
    clean_owner = owner.strip().lower() if owner and str(owner).strip() else None

    # 1. Lấy đề thi
    exams_map = {}
    try:
        c.execute("SELECT id, title, subject, grade, data_json, created_by FROM exams")
        for r in c.fetchall():
            try:
                ex = json.loads(r[4])
                ex_sub = (ex.get("subject") or r[2] or "").strip().lower()
                ex_owner = (r[5] or ex.get("created_by") or "").strip().lower()
                if clean_owner and ex_owner != clean_owner:
                    continue
                if clean_sub and not clean_owner and ex_sub != clean_sub:
                    continue
                exams_map[r[0]] = ex
            except:
                pass
    except:
        pass

    for f in EXAMS_DIR.glob("*.json"):
        try:
            with open(f, encoding="utf-8") as jf:
                d = json.load(jf)
                eid = d.get("id") or f.stem
                if eid and eid not in exams_map:
                    ex_sub = (d.get("subject") or "").strip().lower()
                    ex_owner = (d.get("created_by") or "").strip().lower()
                    if clean_owner and ex_owner != clean_owner:
                        continue
                    if clean_sub and not clean_owner and ex_sub != clean_sub:
                        continue
                    exams_map[eid] = d
        except:
            pass

    allowed_eids = set(exams_map.keys())

    # 2. Lấy tất cả bài thi (nếu là giáo viên thì chỉ lấy bài thuộc đề thi của giáo viên đó)
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

    # 3. Lấy cấu hình (chỉ Super Admin mới sao lưu cấu hình)
    config = {}
    if not clean_owner and not clean_sub:
        c.execute("SELECT key, value FROM config")
        config = dict(c.fetchall())

    # 4. Lấy tài khoản (chỉ Super Admin mới sao lưu tài khoản)
    users = []
    if not clean_owner and not clean_sub:
        c.execute("SELECT id, username, password_hash, full_name, role, is_protected, subject, created_at, updated_at FROM admin_users")
        for r in c.fetchall():
            users.append({
                "id": r[0], "username": r[1], "password_hash": r[2], "full_name": r[3],
                "role": r[4], "is_protected": r[5], "subject": r[6] or "", "created_at": r[7], "updated_at": r[8]
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
    """
    Khôi phục dữ liệu từ file JSON sao lưu.
    - Nếu không có allowed_owner/allowed_subject (Super Admin): Khôi phục toàn bộ (Đề thi + Bài thi + Tài khoản + Cấu hình).
    - Nếu có allowed_owner (Giáo viên):
      + CHỈ phục hồi các đề thi thuộc sở hữu allowed_owner (hoặc gán created_by = allowed_owner).
      + CHỈ phục hồi các bài thi học sinh của các đề thuộc quyền quản lý của giáo viên đó.
      + BỎ QUA tài khoản và cấu hình hệ thống (không cho phép giáo viên can thiệp vào tài khoản khác).
    """
    conn = get_connection()
    c = conn.cursor()

    restored_exams = 0
    restored_subs = 0
    restored_users = 0
    skipped_other_exams = 0

    clean_owner = allowed_owner.strip().lower() if allowed_owner and str(allowed_owner).strip() else None
    clean_sub = allowed_subject.strip().lower() if allowed_subject and str(allowed_subject).strip() else None

    # 1. Khôi phục đề thi
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
        try:
            # Ghi ra file JSON
            (EXAMS_DIR / f"{eid}.json").write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")
            # Ghi vào SQLite
            c.execute("""
                INSERT OR REPLACE INTO exams (id, title, subject, grade, data_json, created_by, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                eid, e.get("title", ""), e.get("subject", ""), str(e.get("grade", "")),
                json.dumps(e, ensure_ascii=False), target_owner
            ))
            restored_exams += 1
        except Exception as ex:
            logger.warning(f"Lỗi khôi phục đề {eid}: {ex}")

    # Nếu là giáo viên, nạp thêm các đề thi hiện có sẵn trong DB của giáo viên đó
    if clean_owner:
        c.execute("SELECT id FROM exams WHERE LOWER(created_by) = ?", (clean_owner,))
        for r in c.fetchall():
            valid_eids.add(r[0])
    elif clean_sub:
        c.execute("SELECT id FROM exams WHERE LOWER(subject) = ?", (clean_sub,))
        for r in c.fetchall():
            valid_eids.add(r[0])

    # 2. Khôi phục bài thi học sinh
    submissions = backup.get("submissions", [])
    for s in submissions:
        sub_eid = s.get("exam_id")
        if (clean_owner or clean_sub) and sub_eid not in valid_eids:
            continue
        try:
            c.execute("""
                INSERT OR REPLACE INTO submissions 
                (id, student_name, student_class, exam_id, started_at, submitted_at, duration_seconds, answers_json, result_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                s["id"], s["student_name"], str(s["student_class"]).upper(), s["exam_id"],
                s.get("started_at"), s.get("submitted_at"), s.get("duration_seconds", 0),
                s.get("answers_json"), s.get("result_json"), s.get("status", "graded")
            ))
            restored_subs += 1
        except Exception as ex:
            logger.warning(f"Lỗi khôi phục bài thi {s.get('id')}: {ex}")

    # 3. Khôi phục tài khoản (CHỈ Super Admin mới được khôi phục)
    if not clean_owner and not clean_sub:
        users = backup.get("users", [])
        for u in users:
            try:
                uname = (u.get("username") or "").strip()
                p_hash = u.get("password_hash")
                if not uname or not p_hash:
                    continue
                c.execute("SELECT id, is_protected FROM admin_users WHERE LOWER(username) = LOWER(?)", (uname,))
                existing = c.fetchone()
                if existing:
                    if not existing[1] and uname.lower() != "admin":
                        c.execute("""
                            UPDATE admin_users SET password_hash = ?, full_name = ?, role = ?, subject = ?, updated_at = datetime('now')
                            WHERE id = ?
                        """, (p_hash, u.get("full_name", ""), u.get("role", "teacher"), u.get("subject", ""), existing[0]))
                else:
                    c.execute("""
                        INSERT INTO admin_users (username, password_hash, full_name, role, is_protected, subject, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                    """, (uname, p_hash, u.get("full_name", ""), u.get("role", "teacher"), 1 if u.get("is_protected") else 0, u.get("subject", "")))
                restored_users += 1
            except Exception:
                pass

        # 4. Khôi phục cấu hình
        cfg = backup.get("config", {})
        for k, v in cfg.items():
            try:
                c.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (k, v))
            except:
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
    """Sao lưu riêng danh sách tài khoản giáo viên và quản trị."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, username, password_hash, full_name, role, is_protected, subject, created_at, updated_at 
        FROM admin_users ORDER BY is_protected DESC, created_at ASC
    """)
    users = []
    for r in c.fetchall():
        users.append({
            "id": r[0], "username": r[1], "password_hash": r[2], "full_name": r[3] or "",
            "role": r[4] or "teacher", "is_protected": bool(r[5]), "subject": r[6] or "",
            "created_at": r[7], "updated_at": r[8]
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
    """Khôi phục danh sách tài khoản từ file sao lưu JSON."""
    users = backup.get("users", [])
    if not isinstance(users, list):
        raise ValueError("Dữ liệu sao lưu tài khoản không hợp lệ (không tìm thấy danh sách users)!")

    conn = get_connection()
    c = conn.cursor()
    restored = 0
    updated = 0
    skipped = 0

    for u in users:
        uname = (u.get("username") or "").strip()
        p_hash = u.get("password_hash")
        if not uname or not p_hash:
            skipped += 1
            continue

        try:
            c.execute("SELECT id, is_protected FROM admin_users WHERE LOWER(username) = LOWER(?)", (uname,))
            row = c.fetchone()
            if row:
                uid, is_protected = row[0], row[1]
                if is_protected or uname.lower() == "admin":
                    c.execute("""
                        UPDATE admin_users SET full_name = ?, updated_at = datetime('now')
                        WHERE id = ?
                    """, (u.get("full_name") or "Quản trị viên", uid))
                else:
                    c.execute("""
                        UPDATE admin_users 
                        SET password_hash = ?, full_name = ?, role = ?, subject = ?, updated_at = datetime('now')
                        WHERE id = ?
                    """, (p_hash, u.get("full_name", ""), u.get("role", "teacher"), u.get("subject", ""), uid))
                updated += 1
            else:
                c.execute("""
                    INSERT INTO admin_users (username, password_hash, full_name, role, is_protected, subject, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """, (
                    uname, p_hash, u.get("full_name", ""), u.get("role", "teacher"),
                    1 if u.get("is_protected") else 0, u.get("subject", "")
                ))
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
