"""
Database - Lưu trữ bài thi và cấu hình dùng SQLite + JSON
"""
import json
import sqlite3
import os
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
DB_PATH = EXAMS_DIR / "exam_db.sqlite" 
PASSWORD_SALT = "longcang_exam_salt_2026"


def hash_password(password: str) -> str:
    """Băm mật khẩu an toàn với salt cố định."""
    raw = f"{PASSWORD_SALT}:{password.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
                                INSERT INTO exams (id, title, subject, grade, data_json, created_at, updated_at)
                                VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                            """, (
                                eid,
                                data.get("title", ""),
                                data.get("subject", ""),
                                str(data.get("grade", "")),
                                json.dumps(data, ensure_ascii=False)
                            ))
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"Lỗi khi đồng bộ đề thi vào SQLite: {e}")

    conn.commit()
    conn.close()
    logger.info("Database initialized.")


def save_submission(submission_data: dict, result_data: dict = None):
    """Lưu bài nộp vào database."""
    conn = get_connection()
    c = conn.cursor()
    
    answers = {
        "part1": submission_data.get("part1_answers", {}),
        "part2": submission_data.get("part2_answers", {}),
        "part3": submission_data.get("part3_answers", {}),
        "part4_question_id": submission_data.get("part4_question_id"),
        "part4_answer": submission_data.get("part4_answer", ""),
    }
    
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
    }

    if row[7]:  # answers_json
        try:
            ans_data = json.loads(row[7])
            result["answers"] = ans_data
            result["part4_answer"] = ans_data.get("part4_answer", "")
            result["part4_question_id"] = ans_data.get("part4_question_id")
        except:
            pass

    if row[8]:  # result_json
        result_data = json.loads(row[8])
        result.update(result_data)
    
    return result


def get_all_submissions(exam_id: str = None, student_class: str = None) -> List[dict]:
    """Lấy tất cả bài nộp (cho admin), hỗ trợ lọc linh hoạt theo Đề thi và theo Lớp."""
    conn = get_connection()
    c = conn.cursor()
    
    query = "SELECT id, student_name, student_class, exam_id, submitted_at, duration_seconds, result_json, status FROM submissions"
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
        }
        if row[6]:
            try:
                result_data = json.loads(row[6])
                item["scores"] = result_data.get("scores", {})
            except:
                item["scores"] = {}
        results.append(item)
    
    return results


def delete_submission(submission_id: str) -> bool:
    """Xóa một bài nộp theo submission_id."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM submissions WHERE id = ?", (submission_id,))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def delete_all_submissions(exam_id: str = None) -> int:
    """Xóa tất cả bài nộp (có thể lọc theo exam_id)."""
    conn = get_connection()
    c = conn.cursor()
    if exam_id:
        c.execute("DELETE FROM submissions WHERE exam_id = ?", (exam_id,))
    else:
        c.execute("DELETE FROM submissions")
    count = c.rowcount
    conn.commit()
    conn.close()
    return count


def load_exam(exam_id: str = "exam_001") -> Optional[dict]:
    """Tải đề thi từ file JSON (tìm theo filename hoặc thuộc tính id)."""
    if not exam_id:
        exam_id = "exam_001"

    # 1. Thử theo tên file trực tiếp
    path = EXAMS_DIR / f"{exam_id}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # 2. Quét các file JSON trong EXAMS_DIR để so khớp theo id
    for f in sorted(EXAMS_DIR.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as jf:
                data = json.load(jf)
                if data.get("id") == exam_id:
                    return data
        except Exception:
            continue

    # 3. Fallback đề mẫu nếu có
    fallback = EXAMS_DIR / "exam_toan_12_101.json"
    if not fallback.exists():
        fallback = EXAMS_DIR / "sample_exam.json"
    if fallback.exists():
        with open(fallback, encoding="utf-8") as f:
            return json.load(f)
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
        SELECT id, username, password_hash, full_name, role, is_protected, created_at, updated_at
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
        "updated_at": row[7]
    }


def get_all_users() -> List[dict]:
    """Lấy danh sách tất cả tài khoản (ẩn hash mật khẩu)."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, username, full_name, role, is_protected, created_at, updated_at
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
            "updated_at": r[6]
        })
    return users


def create_user(username: str, password: str, full_name: str = "", role: str = "teacher") -> dict:
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
        INSERT INTO admin_users (username, password_hash, full_name, role, is_protected, created_at, updated_at)
        VALUES (?, ?, ?, ?, 0, datetime('now'), datetime('now'))
    """, (clean_username, p_hash, full_name.strip(), role.strip()))
    user_id = c.lastrowid
    conn.commit()
    conn.close()

    logger.info(f"Đã tạo tài khoản mới: {clean_username} (role={role})")
    return {
        "id": user_id,
        "username": clean_username,
        "full_name": full_name.strip(),
        "role": role,
        "is_protected": False
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


def save_exam_record(exam_data: dict):
    """Lưu đề thi đồng thời vào cả file JSON và bảng exams trong SQLite."""
    exam_id = exam_data.get("id")
    if not exam_id:
        return
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
            INSERT OR REPLACE INTO exams (id, title, subject, grade, data_json, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
        """, (
            exam_id,
            exam_data.get("title", ""),
            exam_data.get("subject", ""),
            str(exam_data.get("grade", "")),
            json.dumps(exam_data, ensure_ascii=False)
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Lỗi lưu đề thi vào SQLite: {e}")


def export_full_backup() -> dict:
    """
    Xuất toàn bộ hệ thống (Đề thi + Bài thi thí sinh + Tài khoản + Cấu hình)
    thành một file JSON sao lưu hoàn chỉnh để lưu trữ vĩnh viễn trên máy tính cá nhân.
    """
    conn = get_connection()
    c = conn.cursor()

    # 1. Lấy tất cả bài thi
    c.execute("SELECT id, student_name, student_class, exam_id, started_at, submitted_at, duration_seconds, answers_json, result_json, status FROM submissions")
    sub_rows = c.fetchall()
    submissions = []
    for r in sub_rows:
        submissions.append({
            "id": r[0], "student_name": r[1], "student_class": r[2], "exam_id": r[3],
            "started_at": r[4], "submitted_at": r[5], "duration_seconds": r[6],
            "answers_json": r[7], "result_json": r[8], "status": r[9]
        })

    # 2. Lấy tất cả đề thi (từ SQLite + các file JSON trong EXAMS_DIR)
    exams_map = {}
    try:
        c.execute("SELECT id, title, subject, grade, data_json FROM exams")
        for r in c.fetchall():
            try:
                exams_map[r[0]] = json.loads(r[4])
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
                    exams_map[eid] = d
        except:
            pass

    # 3. Lấy cấu hình
    c.execute("SELECT key, value FROM config")
    config = dict(c.fetchall())

    # 4. Lấy tài khoản
    c.execute("SELECT id, username, password_hash, full_name, role, is_protected, created_at, updated_at FROM admin_users")
    users = []
    for r in c.fetchall():
        users.append({
            "id": r[0], "username": r[1], "password_hash": r[2], "full_name": r[3],
            "role": r[4], "is_protected": r[5], "created_at": r[6], "updated_at": r[7]
        })

    conn.close()

    return {
        "version": "2026.1",
        "exported_at": datetime.now().isoformat(),
        "exams": list(exams_map.values()),
        "submissions": submissions,
        "users": users,
        "config": config
    }


def import_full_backup(backup: dict) -> dict:
    """
    Khôi phục toàn bộ dữ liệu hệ thống từ file JSON sao lưu.
    """
    conn = get_connection()
    c = conn.cursor()

    restored_exams = 0
    restored_subs = 0
    restored_users = 0

    # 1. Khôi phục đề thi
    exams = backup.get("exams", [])
    for e in exams:
        eid = e.get("id")
        if not eid:
            continue
        try:
            # Ghi ra file JSON
            (EXAMS_DIR / f"{eid}.json").write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")
            # Ghi vào SQLite
            c.execute("""
                INSERT OR REPLACE INTO exams (id, title, subject, grade, data_json, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
            """, (
                eid, e.get("title", ""), e.get("subject", ""), str(e.get("grade", "")),
                json.dumps(e, ensure_ascii=False)
            ))
            restored_exams += 1
        except Exception as ex:
            logger.warning(f"Lỗi khôi phục đề {eid}: {ex}")

    # 2. Khôi phục bài thi học sinh
    submissions = backup.get("submissions", [])
    for s in submissions:
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

    # 3. Khôi phục tài khoản (không đè tài khoản admin mặc định nếu đã tồn tại)
    users = backup.get("users", [])
    for u in users:
        try:
            c.execute("""
                INSERT OR IGNORE INTO admin_users (username, password_hash, full_name, role, is_protected)
                VALUES (?, ?, ?, ?, ?)
            """, (u["username"], u["password_hash"], u.get("full_name", ""), u.get("role", "teacher"), u.get("is_protected", 0)))
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

    return {
        "success": True,
        "restored_exams": restored_exams,
        "restored_submissions": restored_subs,
        "restored_users": restored_users
    }
