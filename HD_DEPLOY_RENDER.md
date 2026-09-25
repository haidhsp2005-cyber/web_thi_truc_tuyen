# HƯỚNG DẪN ĐƯA HỆ THỐNG LÊN RENDER.COM (MIỄN PHÍ 100%)

Hệ thống đã được cấu hình sẵn toàn bộ các file cần thiết để đưa lên **Render.com** (`render.yaml`, `Procfile`, `runtime.txt`, `requirements.txt`).

Quá trình gồm 2 bước rất đơn giản:

---

## BƯỚC 1: ĐƯA MÃ NGUỒN LÊN GITHUB

1. Truy cập [https://github.com/new](https://github.com/new) và đăng nhập.
2. Đặt tên Repository (ví dụ: `website-tronde-kiemtra`), chọn **Public** hoặc **Private**, rồi bấm **Create repository**.
3. Mở terminal tại thư mục dự án (`D:\Antigravity\website_tronde_kiemtra`) và chạy 3 lệnh sau:

```bash
git remote add origin https://github.com/<ten_tai_khoan_github_cua_ban>/website-tronde-kiemtra.git
git branch -M main
git push -u origin main
```
*(Thay `<ten_tai_khoan_github_cua_ban>` bằng tên tài khoản GitHub của bạn)*

---

## BƯỚC 2: DEPLOY LÊN RENDER.COM

1. Truy cập [https://dashboard.render.com](https://dashboard.render.com) (đăng nhập bằng tài khoản GitHub).
2. Chọn 1 trong 2 cách sau:

### 👉 Cách 1: Tự động 100% bằng Blueprint (Khuyên dùng)
- Bấm nút **New +** (góc trên bên phải) -> Chọn **Blueprint**.
- Chọn repository `website-tronde-kiemtra` bạn vừa tạo trên GitHub.
- Render sẽ tự động đọc file `render.yaml` đã được cấu hình sẵn:
  - Tự chọn Python 3.11.9.
  - Tự cài đặt thư viện từ `requirements.txt`.
  - Tự đặt lệnh khởi động `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
  - Tự cấu hình biến môi trường tài khoản `admin` / `Longcang2026@`.
- Bấm **Apply** và chờ 2-3 phút là xong!

---

### 👉 Cách 2: Tạo thủ công qua Web Service
- Bấm nút **New +** -> Chọn **Web Service**.
- Chọn repository `website-tronde-kiemtra` từ GitHub.
- Điền các thông tin sau:
  - **Name**: `website-tronde-kiemtra` (hoặc tên trường/lớp bạn muốn)
  - **Region**: Chọn **Singapore** (để học sinh ở Việt Nam truy cập nhanh nhất)
  - **Branch**: `main`
  - **Runtime**: `Python 3`
  - **Build Command**: `pip install -r requirements.txt`
  - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  - **Instance Type**: Chọn **Free** ($0/tháng)
- Mục **Environment Variables** (thêm nếu muốn):
  - `PYTHON_VERSION`: `3.11.9`
  - `ADMIN_USERNAME`: `admin`
  - `ADMIN_PASSWORD`: `Longcang2026@`
- Bấm **Create Web Service**.

---

## 🎉 KẾT QUẢ
Sau khi Render build xong (khoảng 2-3 phút), Render sẽ cấp cho bạn một đường link internet công khai dạng:
`https://website-tronde-kiemtra.onrender.com`

- Thí sinh vào thi trực tiếp: `https://website-tronde-kiemtra.onrender.com`
- Thầy/Cô đăng nhập quản trị: `https://website-tronde-kiemtra.onrender.com/admin`
  *(Tài khoản: `admin` | Mật khẩu: `Longcang2026@`)*
