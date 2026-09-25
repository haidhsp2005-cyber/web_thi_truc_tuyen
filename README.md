# Hệ Thống Kiểm Tra Trực Tuyến - Chuẩn GDPT 2025

Hệ thống web kiểm tra trực tuyến với AI chấm bài tự luận, xoay vòng API key Gemini miễn phí.

## 🚀 Khởi động nhanh (Windows)

```
Double-click vào file: run.bat
```

Sau đó mở trình duyệt truy cập: **http://localhost:8000**

---

## 📋 Cấu hình API Key (Bắt buộc cho chức năng AI)

1. Lấy API key miễn phí tại: [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. Mở file `.env` (được tạo tự động khi chạy lần đầu)
3. Điền key vào:
```env
GEMINI_API_KEYS=key1_cua_ban,key2_cua_ban,key3_cua_ban
```
4. Hoặc thêm key qua trang Admin (không cần khởi động lại)

> **Mẹo**: Tạo 2-3 key miễn phí để hệ thống xoay vòng, chấm 10 bài tự luận mà không lo hết hạn mức!

---

## 🔐 Tài khoản Quản trị viên (Admin)

Khi vào trang quản lý hoặc tạo đề thi, hệ thống yêu cầu đăng nhập:
- **Đường dẫn đăng nhập**: [http://localhost:8000/admin/login](http://localhost:8000/admin/login)
- **Tên đăng nhập**: `Admin`
- **Mật khẩu**: `Longcang2026@`

*(Thông tin này có thể tùy chỉnh trong file `.env` qua 2 biến `ADMIN_USERNAME` và `ADMIN_PASSWORD`)*

---

## 📁 Cấu trúc dự án

```
website_tronde_kiemtra/
├── app/
│   ├── main.py                 # FastAPI app chính
│   ├── models.py               # Pydantic models
│   ├── database.py             # SQLite database
│   ├── routers/
│   │   ├── exam.py             # API routes thi
│   │   └── admin.py            # API routes admin
│   └── services/
│       ├── key_rotator.py      # Xoay vòng API key
│       ├── ai_service.py       # Gọi Gemini AI
│       ├── grading_service.py  # Chấm điểm
│       └── export_service.py   # Xuất Excel/HTML
├── templates/
│   ├── index.html              # Trang đăng nhập thi
│   ├── exam.html               # Phòng thi trực tuyến
│   ├── result.html             # Kết quả bài thi
│   └── admin.html              # Trang quản lý
├── data/
│   └── sample_exam.json        # Đề thi mẫu Toán lớp 10
├── .env.example                # Mẫu cấu hình
├── requirements.txt
└── run.bat                     # Script khởi động
```

---

## 🌐 Chia sẻ link cho học sinh (miễn phí)

### Cách 1: Qua Cloudflare Tunnel (Khuyên dùng)
```bash
# Tải cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
cloudflared tunnel --url http://localhost:8000
```
Sau đó copy link `https://xxx.trycloudflare.com` gửi cho học sinh.

### Cách 2: Qua mạng LAN/Wifi lớp học
Học sinh truy cập `http://<IP-máy-giáo-viên>:8000` (ví dụ: `http://192.168.1.15:8000`)

---

## 🎯 Cấu trúc đề thi chuẩn GDPT 2025

| Phần | Loại câu | Số câu | Thang điểm |
|------|----------|--------|------------|
| **Phần I** | TNKQ nhiều lựa chọn | 20 câu | 5.0đ (0.25đ/câu) |
| **Phần II** | Đúng / Sai (4 ý/câu) | 4 câu | 2.0đ (theo barem BGD) |
| **Phần III** | Trả lời ngắn | 6 câu | 2.0đ (~0.33đ/câu) |
| **Phần IV** | Tự luận tự chọn | 1 câu | 1.0đ (AI chấm) |

### Barem điểm Phần II (Đúng/Sai):
- 1 ý đúng: **0.1 điểm**
- 2 ý đúng: **0.25 điểm**
- 3 ý đúng: **0.5 điểm**
- 4 ý đúng: **0.5 điểm**

---

## 📊 Tính năng xuất kết quả

- **Phiếu kết quả học sinh** (In/PDF): Từ trang kết quả → nút "In kết quả"
- **Bảng điểm toàn lớp** (Excel .xlsx): Từ trang Admin → nút "Xuất bảng điểm"
- **Xem đáp án chi tiết** + lời giải: Hiển thị ngay trên trang kết quả

---

## ⚡ Cơ chế xoay vòng API Key

```
Key 1 ──→ Thành công ──→ Đếm lần dùng
Key 2 ──→ Lỗi 429 ──→ Cooldown 120s ──→ Chuyển sang key khác
Key 3 ──→ Thành công ──→ Đếm lần dùng
```

- **Round-robin**: Luân phiên phân bổ đều qua tất cả keys
- **Tự động failover**: Chuyển sang key khác nếu gặp lỗi 429 (rate limit)
- **Cooldown tự phục hồi**: Key bị khóa sẽ tự mở lại sau 120 giây
- **Dashboard giám sát**: Xem trạng thái tất cả keys trong trang Admin

---

## 🛠️ Chạy thủ công

```bash
# Cài thư viện
pip install -r requirements.txt

# Chạy server
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
