"""
Exam Builder Router - Tạo, quản lý và nhập đề thi từ file/văn bản
"""
import io
import re
import json
import uuid
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import JSONResponse, Response, StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/exam-builder", tags=["exam-builder"])

DATA_DIR = Path(__file__).parent.parent.parent / "data"


def _list_exam_files() -> List[dict]:
    """Quét thư mục data/ lấy tất cả file JSON đề thi."""
    exams = []
    for f in sorted(DATA_DIR.glob("*.json")):
        if f.name == "exam_db.sqlite":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            exams.append({
                "file": f.stem,
                "id": data.get("id", f.stem),
                "title": data.get("title", f.stem),
                "subject": data.get("subject", "Khác"),
                "grade": str(data.get("grade", "")),
                "duration_minutes": data.get("duration_minutes", 45),
                "part1_count": len(data.get("parts", {}).get("part1", {}).get("questions", [])),
                "part2_count": len(data.get("parts", {}).get("part2", {}).get("questions", [])),
                "part3_count": len(data.get("parts", {}).get("part3", {}).get("questions", [])),
                "part4_count": len(data.get("parts", {}).get("part4", {}).get("questions", [])),
            })
        except Exception as e:
            logger.warning(f"Bỏ qua file {f.name}: {e}")
    return exams


@router.get("/list")
async def list_exams():
    """Danh sách tất cả đề thi có sẵn."""
    return {"exams": _list_exam_files()}


@router.get("/get/{exam_id}")
async def get_exam_full(exam_id: str):
    """Lấy toàn bộ đề thi kể cả đáp án (cho giáo viên chỉnh sửa)."""
    # 1. Thử file theo exam_id
    path = DATA_DIR / f"{exam_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    
    # 2. Quét tìm theo id bên trong file
    for f in DATA_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("id") == exam_id:
                return data
        except Exception:
            continue
            
    raise HTTPException(404, "Không tìm thấy đề thi!")


@router.get("/sample/pdf-exam")
async def get_pdf_sample_exam():
    """Lấy mẫu đề thi Toán 12 chuẩn theo đúng file PDF người dùng gửi."""
    path = DATA_DIR / "exam_toan_12_101.json"
    if not path.exists():
        raise HTTPException(404, "File mẫu chưa được tạo!")
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/sample/add-to-system")
async def add_sample_exam_to_system():
    """Thêm đề thi mẫu chuẩn GDPT 2026 (Toán 12 - Mã 101) vào danh sách đề thi hệ thống."""
    src = DATA_DIR / "exam_toan_12_101.json"
    if not src.exists():
        raise HTTPException(404, "Không tìm thấy file mẫu đề thi Toán 12!")
    data = json.loads(src.read_text(encoding="utf-8"))
    src.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "success": True,
        "message": "Đã thêm Đề thi mẫu chuẩn GDPT 2026 (Môn Toán 12 - Mã 101) vào hệ thống thành công!",
        "exam_id": data.get("id", "exam_toan_12_101"),
        "exam": data
    }


def _is_run_red_or_marked(run) -> tuple[bool, bool]:
    """Kiểm tra xem run trong file Word có màu đỏ hoặc gạch chân không."""
    is_red = False
    is_underline = False

    # 1. run.font.color.rgb
    try:
        if run.font and run.font.color and run.font.color.rgb:
            s = str(run.font.color.rgb).upper().strip()
            if len(s) == 6:
                r = int(s[0:2], 16)
                g = int(s[2:4], 16)
                b = int(s[4:6], 16)
                if (r >= 150 and g <= 115 and b <= 115) or (r > 1.8 * max(g, b, 1) and r > 120):
                    is_red = True
    except Exception:
        pass

    # 2. XML w:color
    if not is_red:
        try:
            colors = run._r.xpath('./w:rPr/w:color/@w:val')
            for c in colors:
                c_str = str(c).upper().strip()
                if c_str in ("FF0000", "RED", "C00000", "ED1C24", "E00000", "D00000", "FF3333", "E60000", "FF1A1A", "FF0033"):
                    is_red = True
                    break
                if len(c_str) == 6:
                    try:
                        r = int(c_str[0:2], 16)
                        g = int(c_str[2:4], 16)
                        b = int(c_str[4:6], 16)
                        if (r >= 150 and g <= 115 and b <= 115) or (r > 1.8 * max(g, b, 1) and r > 120):
                            is_red = True
                            break
                    except Exception:
                        pass
        except Exception:
            pass

    # 3. Highlight đỏ
    if not is_red:
        try:
            hl = run._r.xpath('./w:rPr/w:highlight/@w:val')
            if hl and str(hl[0]).lower() in ("red", "darkred"):
                is_red = True
        except Exception:
            pass

    # 4. Underline
    try:
        if run.underline or (run.font and run.font.underline):
            is_underline = True
        u = run._r.xpath('./w:rPr/w:u/@w:val')
        if u and u[0] not in ('none', 'false', '0'):
            is_underline = True
    except Exception:
        pass

    return is_red, is_underline


def _sanitize_math_symbols(math_latex: str) -> str:
    """Chuẩn hóa các ký tự toán học Unicode và cú pháp hệ phương trình sang chuẩn LaTeX KaTeX."""
    if not math_latex:
        return ""
    # Thay thế các ký hiệu Unicode bằng lệnh LaTeX tương đương
    replacements = [
        ("−", "-"),
        ("′", "'"),
        ("≠", "\\neq "),
        ("≤", "\\le "),
        ("≥", "\\ge "),
        ("×", "\\times "),
        ("·", "\\cdot "),
        ("±", "\\pm "),
        ("∞", "\\infty "),
        ("÷", "\\div "),
        ("∠", "\\angle "),
        ("⊥", "\\perp "),
        ("∥", "\\parallel "),
        ("≈", "\\approx "),
        ("∈", "\\in "),
        ("∉", "\\notin "),
        ("⊂", "\\subset "),
        ("⊃", "\\supset "),
        ("∪", "\\cup "),
        ("∩", "\\cap "),
        ("∅", "\\emptyset "),
        ("∆", "\\Delta "),
        ("Δ", "\\Delta "),
        ("π", "\\pi "),
        ("α", "\\alpha "),
        ("β", "\\beta "),
        ("γ", "\\gamma "),
        ("λ", "\\lambda "),
        ("θ", "\\theta "),
        ("ω", "\\omega "),
    ]
    for orig, rep in replacements:
        math_latex = math_latex.replace(orig, rep)
    
    # Chuẩn hóa biến có dấu phẩy trên: {a}^{'} -> a', {x}_{0} -> x_0
    math_latex = re.sub(r"\{([a-zA-Z])\}\^\{'\}", r"\1'", math_latex)
    math_latex = re.sub(r"\{([a-zA-Z])\}\^\{′\}", r"\1'", math_latex)

    # 1. Sửa lỗi Word OMML gộp hệ phương trình: ${...$
    def fix_omml_brace(m):
        body = m.group(1).strip()
        if r"\\" in body or "\n" in body or "&" in body:
            return f"$\\begin{{cases}} {body} \\end{{cases}}$"
        return f"${body}$"

    math_latex = re.sub(r"\$\{([^$]+?)\$", fix_omml_brace, math_latex)

    # 2. Chuẩn hóa hệ phương trình dạng \left\{ \begin{matrix} hoặc \begin{array} sang \begin{cases}
    math_latex = re.sub(r"\\left\\\{\s*\\begin\{(?:matrix|array)\}(?:\{[a-zA-Z]*\})?", r"\\begin{cases}", math_latex)
    math_latex = re.sub(r"\\end\{(?:matrix|array)\}(?:\s*\\right\.?)?", r"\\end{cases}", math_latex)

    # 3. Tự động đóng \begin{cases} nếu người dùng quên gõ \end{cases}
    cases_open = len(re.findall(r"\\begin\{cases\}", math_latex))
    cases_close = len(re.findall(r"\\end\{cases\}", math_latex))
    if cases_open > cases_close:
        missing = cases_open - cases_close
        if math_latex.endswith("$"):
            math_latex = math_latex[:-1] + (" \\end{cases}" * missing) + "$"
        else:
            math_latex = math_latex + (" \\end{cases}" * missing)

    # 4. Nếu \begin{cases} bên trong KHÔNG có dấu \\ hoặc &, nó không phải hệ phương trình, mở ngoặc trả về biểu thức gốc
    math_latex = re.sub(r"\\begin\{cases\}\s*([^&\\\n]+?)\s*\\end\{cases\}", r"\1", math_latex)

    # 5. Dọn dẹp lỗi ngoặc nhọn thừa do tách Word OMML: x}_{0} -> x_{0}, {x}_{0} -> x_{0}
    math_latex = re.sub(r"\{?([a-zA-Z0-9]+)\}_", r"\1_", math_latex)

    # 6. Làm sạch ký hiệu toạ độ Word OMML: ((x)_{0};(y)_{0}) -> (x_0; y_0)
    math_latex = re.sub(r"\(\(([a-zA-Z])\)_\{?(\d+)\}?;\s*\(([a-zA-Z])\)_\{?(\d+)\}?\)", r"(\1_\2; \3_\4)", math_latex)
    math_latex = re.sub(r"\(([a-zA-Z])\)_\{?(\d+)\}?", r"\1_\2", math_latex)

    # 7. Tự động đóng \left\{ nếu thiếu \right
    if "\\left\\{" in math_latex and "\\right" not in math_latex:
        if math_latex.endswith("$"):
            math_latex = math_latex[:-1] + " \\right.$"
        else:
            math_latex = math_latex + " \\right."

    # 7. Tự động đóng dấu $ nếu lẻ dấu $
    dollar_count = math_latex.count("$")
    if dollar_count % 2 != 0:
        math_latex = math_latex + "$"

    # 8. Bỏ bao bọc $ $ không cần thiết cho số nguyên / số thập phân đơn giản (VD: $1$ -> 1, $-1$ -> -1)
    math_latex = re.sub(r"^\s*\$([+-]?\d+(?:[\.,]\d+)?)\$\s*$", r"\1", math_latex)

    return math_latex.strip()


def _omml_node_to_latex(node) -> str:
    """Chuyển đổi một node Word Office Math (OMML) sang cú pháp LaTeX chuẩn KaTeX."""
    tag = node.tag.split("}")[-1]
    if tag == "t":
        return node.text or ""
    elif tag == "f":  # Phân số \frac{a}{b}
        num = "".join(_omml_node_to_latex(c) for c in (node.xpath('./*[local-name()="num"]') or []))
        den = "".join(_omml_node_to_latex(c) for c in (node.xpath('./*[local-name()="den"]') or []))
        return f"\\frac{{{num}}}{{{den}}}"
    elif tag == "rad":  # Căn: \sqrt[n]{x}
        deg = "".join(_omml_node_to_latex(c) for c in (node.xpath('./*[local-name()="deg"]') or []))
        base = "".join(_omml_node_to_latex(c) for c in (node.xpath('./*[local-name()="e"]') or []))
        if deg.strip():
            return f"\\sqrt[{deg}]{{{base}}}"
        return f"\\sqrt{{{base}}}"
    elif tag == "sSup":  # Số mũ: x^y
        base = "".join(_omml_node_to_latex(c) for c in (node.xpath('./*[local-name()="e"]') or []))
        sup = "".join(_omml_node_to_latex(c) for c in (node.xpath('./*[local-name()="sup"]') or []))
        return f"{{{base}}}^{{{sup}}}"
    elif tag == "sSub":  # Chỉ số dưới: x_1
        base = "".join(_omml_node_to_latex(c) for c in (node.xpath('./*[local-name()="e"]') or []))
        sub = "".join(_omml_node_to_latex(c) for c in (node.xpath('./*[local-name()="sub"]') or []))
        return f"{{{base}}}_{{{sub}}}"
    elif tag == "sSubSup":  # Cả trên và dưới
        base = "".join(_omml_node_to_latex(c) for c in (node.xpath('./*[local-name()="e"]') or []))
        sub = "".join(_omml_node_to_latex(c) for c in (node.xpath('./*[local-name()="sub"]') or []))
        sup = "".join(_omml_node_to_latex(c) for c in (node.xpath('./*[local-name()="sup"]') or []))
        return f"{{{base}}}_{{{sub}}}^{{{sup}}}"
    elif tag == "eqArr":  # Mảng phương trình (hệ phương trình trong Word OMML)
        rows = []
        for e in node.xpath('./*[local-name()="e"]'):
            row_txt = "".join(_omml_node_to_latex(c) for c in e).strip()
            if row_txt:
                rows.append(row_txt)
        return " \\\\ ".join(rows)
    elif tag == "m":  # Ma trận
        mr_list = node.xpath('./*[local-name()="mr"]')
        rows = []
        for mr in mr_list:
            cells = ["".join(_omml_node_to_latex(c) for c in e).strip() for e in mr.xpath('./*[local-name()="e"]')]
            rows.append(" & ".join(cells))
        return " \\\\ ".join(rows)
    elif tag == "d":  # Dấu ngoặc (delimiters)
        beg_list = node.xpath('./*[local-name()="dPr"]/*[local-name()="begChr"]/@*[local-name()="val"]')
        end_list = node.xpath('./*[local-name()="dPr"]/*[local-name()="endChr"]/@*[local-name()="val"]')
        
        beg = beg_list[0] if beg_list else "("
        end = end_list[0] if end_list else ")"

        # Lấy nội dung các phần tử con bên trong delimiter
        e_elements = node.xpath('./*[local-name()="e"]')
        content_parts = []
        for e in e_elements:
            t = "".join(_omml_node_to_latex(c) for c in e).strip()
            if t:
                content_parts.append(t)
        content = " \\\\ ".join(content_parts)

        # Xử lý trường hợp hệ phương trình: begChr là '{' và endChr rỗng
        if beg == "{" and (not end or end == ""):
            return f"\\begin{{cases}} {content} \\end{{cases}}"
        elif beg == "{" and end == "}":
            return f"\\left\\{{ {content} \\right\\}}"
        elif beg == "[" and end == "]":
            return f"\\left[ {content} \\right]"
        elif beg == "(" and end == ")":
            return f"\\left( {content} \\right)"
        elif beg == "|" and end == "|":
            return f"\\left| {content} \\right|"
        elif not end:
            return f"\\left{beg} {content} \\right."
        else:
            return f"\\left{beg} {content} \\right{end}"
    elif tag == "groupChr":  # Dấu vectơ hoặc gạch ngang trên đầu: \vec{u}
        e = "".join(_omml_node_to_latex(c) for c in (node.xpath('./*[local-name()="e"]') or []))
        return f"\\vec{{{e}}}"
    else:
        parts = [_omml_node_to_latex(c) for c in node]
        if len(node) == 0 and node.text:
            return node.text
        return "".join(parts)


def _extract_element_runs_and_math(elem, p) -> List[str]:
    """Trích xuất một phần tử đoạn văn Word gồm cả chữ thường và công thức toán OMML."""
    import docx
    parts = []
    for child in elem:
        tag = child.tag.split("}")[-1]
        if tag == "r":
            r = docx.text.run.Run(child, p)
            txt = r.text
            if not txt:
                continue
            r_red, r_u = _is_run_red_or_marked(r)
            if r_red:
                parts.append(f"{{{{RED}}}}{txt}{{{{/RED}}}}")
            elif r_u:
                parts.append(f"{{{{UNDERLINE}}}}{txt}{{{{/UNDERLINE}}}}")
            else:
                parts.append(txt)
        elif tag in ("oMath", "oMathPara"):
            raw_math = _omml_node_to_latex(child).strip()
            math_latex = _sanitize_math_symbols(raw_math)
            if math_latex:
                parts.append(f" ${math_latex}$ ")
        elif tag == "hyperlink":
            parts.extend(_extract_element_runs_and_math(child, p))
    return parts


def _extract_docx_paragraphs_with_format(doc) -> List[str]:
    """
    Trích xuất văn bản từ file Word bảo toàn thứ tự tự nhiên của các đoạn văn và bảng biểu (tables),
    đồng thời giữ thông tin chữ in đỏ, gạch chân và chuyển đổi công thức toán OMML sang LaTeX chuẩn.
    """
    import docx
    paragraphs = []

    # Duyệt qua các phần tử con của body theo đúng thứ tự xuất hiện trong tài liệu
    for child in doc.element.body:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            p = docx.text.paragraph.Paragraph(child, doc)
            annotated_runs = _extract_element_runs_and_math(child, p)
            line = "".join(annotated_runs).strip()
            if line:
                paragraphs.append(line)
        elif tag == "tbl":
            table = docx.table.Table(child, doc)
            for row in table.rows:
                row_parts = []
                for cell in row.cells:
                    cell_parts = []
                    for p in cell.paragraphs:
                        cell_runs = _extract_element_runs_and_math(p._element, p)
                        t = "".join(cell_runs).strip()
                        if t:
                            cell_parts.append(t)
                    cell_text = " ".join(cell_parts).strip()
                    if cell_text:
                        row_parts.append(cell_text)
                if row_parts:
                    paragraphs.append("  ".join(row_parts))

    return paragraphs


@router.post("/upload-file")
async def upload_exam_file(file: UploadFile = File(...)):
    """
    Nhập đề thi từ file Word (.docx), PDF (.pdf), Text (.txt) hoặc JSON (.json).
    Tự động trích xuất văn bản, phân tích các câu hỏi và lưu vào hệ thống.
    """
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    
    if ext not in (".docx", ".pdf", ".txt", ".json"):
        raise HTTPException(400, "Định dạng file không hỗ trợ! Vui lòng tải file .docx, .pdf, .txt hoặc .json.")

    content_bytes = await file.read()
    raw_text = ""

    if ext == ".docx":
        try:
            import docx
            doc = docx.Document(io.BytesIO(content_bytes))
            paragraphs = _extract_docx_paragraphs_with_format(doc)
            raw_text = "\n".join(paragraphs)
        except Exception as e:
            logger.error(f"Lỗi đọc file docx: {e}")
            raise HTTPException(400, f"Không thể đọc file Word: {e}")

    elif ext == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content_bytes))
            pages_text = []
            for i, page in enumerate(reader.pages):
                pt = page.extract_text() or ""
                if pt.strip():
                    pages_text.append(pt)
            raw_text = "\n".join(pages_text)
        except Exception as e:
            try:
                import fitz
                doc = fitz.open(stream=content_bytes, filetype="pdf")
                pages_text = [page.get_text() for page in doc]
                raw_text = "\n".join(pages_text)
            except Exception as e2:
                logger.error(f"Lỗi đọc file PDF: {e2}")
                raise HTTPException(400, f"Không thể đọc file PDF: {e}")

    elif ext == ".json":
        try:
            data = json.loads(content_bytes.decode("utf-8"))
            if not data.get("title"):
                data["title"] = Path(filename).stem
            if not data.get("id"):
                data["id"] = f"exam_{uuid.uuid4().hex[:8]}"
            out_path = DATA_DIR / f"{data['id']}.json"
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return {
                "success": True,
                "message": f"Đã nhập thành công đề thi '{data['title']}' từ file JSON!",
                "exam_id": data["id"],
                "exam": data
            }
        except Exception as e:
            raise HTTPException(400, f"File JSON không hợp lệ: {e}")

    elif ext == ".txt":
        try:
            raw_text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = content_bytes.decode("cp1252", errors="replace")

    if not raw_text.strip():
        raise HTTPException(400, "Nội dung file rỗng hoặc không thể trích xuất văn bản!")

    # Chạy qua parser
    res = await parse_exam_text({"text": raw_text})
    parsed = res["parsed"]
    if not parsed["title"]:
        parsed["title"] = Path(filename).stem.replace("_", " ").upper()
    parsed["id"] = f"exam_{uuid.uuid4().hex[:8]}"

    # Lưu vào hệ thống
    await save_exam(parsed)

    return {
        "success": True,
        "message": f"Đã nhập và tạo đề thi thành công từ file {filename} ({res['summary']['part1_count']} câu P1, {res['summary']['part2_count']} câu P2, {res['summary']['part3_count']} câu P3)!",
        "exam_id": parsed["id"],
        "exam": parsed,
        "summary": res["summary"]
    }


@router.post("/save")
async def save_exam(exam_data: dict):
    """Lưu đề thi mới hoặc cập nhật đề thi hiện tại."""
    if not exam_data.get("title", "").strip():
        raise HTTPException(400, "Vui lòng nhập tiêu đề đề thi!")
    
    parts = exam_data.get("parts", {})
    p1_qs = parts.get("part1", {}).get("questions", [])
    p2_qs = parts.get("part2", {}).get("questions", [])
    p3_qs = parts.get("part3", {}).get("questions", [])
    p4_qs = parts.get("part4", {}).get("questions", [])

    total_qs = len(p1_qs) + len(p2_qs) + len(p3_qs) + len(p4_qs)
    if total_qs == 0:
        raise HTTPException(400, "Không nhận diện được câu hỏi nào trong đề thi! Vui lòng kiểm tra lại định dạng file (ví dụ: 'Câu 1:... A. ... B. ...').")

    # Gán ID nếu chưa có
    if not exam_data.get("id"):
        exam_data["id"] = f"exam_{uuid.uuid4().hex[:8]}"

    # Tính toán thang điểm linh hoạt
    p1_count = len(p1_qs)
    p2_count = len(p2_qs)
    p3_count = len(p3_qs)
    p4_count = len(p4_qs)

    existing_scoring = exam_data.get("scoring")
    if existing_scoring and isinstance(existing_scoring, dict) and "part1_total" in existing_scoring:
        pass
    else:
        # Tự động tính thang điểm 10 theo các phần thực tế có trong đề
        if p4_count > 0:
            p4_total = 1.0
            remaining = 9.0
        else:
            p4_total = 0.0
            remaining = 10.0

        if p1_count > 0 and p2_count > 0 and p3_count > 0:
            p1_total = 5.0 if p4_count == 0 else 4.0
            p2_total = 4.0 if p4_count == 0 else 3.0
            p3_total = 1.0 if p4_count == 0 else 2.0
        elif p1_count > 0 and p2_count > 0:
            p1_total = 6.0
            p2_total = 4.0
            p3_total = 0.0
        elif p1_count > 0 and p3_count > 0:
            p1_total = 7.0
            p2_total = 0.0
            p3_total = 3.0
        elif p1_count > 0:
            p1_total = remaining
            p2_total = 0.0
            p3_total = 0.0
        elif p2_count > 0 and p3_count > 0:
            p1_total = 0.0
            p2_total = 7.0
            p3_total = 3.0
        elif p2_count > 0:
            p1_total = 0.0
            p2_total = remaining
            p3_total = 0.0
        elif p3_count > 0:
            p1_total = 0.0
            p2_total = 0.0
            p3_total = remaining
        else:
            p1_total = 0.0
            p2_total = 0.0
            p3_total = 0.0

        p1_per_q = round(p1_total / p1_count, 4) if p1_count > 0 else 0.0
        p3_per_q = round(p3_total / p3_count, 4) if p3_count > 0 else 0.0
        p2_item_pts = round(p2_total / p2_count, 2) if p2_count > 0 else 1.0
        p2_rubric = {
            "1_correct": round(p2_item_pts * 0.1, 2),
            "2_correct": round(p2_item_pts * 0.25, 2),
            "3_correct": round(p2_item_pts * 0.5, 2),
            "4_correct": p2_item_pts
        }

        exam_data["scoring"] = {
            "part1_total": p1_total,
            "part1_per_question": p1_per_q,
            "part2_total": p2_total,
            "part2_rubric": p2_rubric,
            "part3_total": p3_total,
            "part3_per_question": p3_per_q,
            "part4_total": p4_total,
        }

    # Đảm bảo cấu trúc đầy đủ cho các phần còn thiếu
    _ensure_part_structure(exam_data)

    exam_id = exam_data["id"]
    out_path = DATA_DIR / f"{exam_id}.json"
    out_path.write_text(
        json.dumps(exam_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    logger.info(f"Đã lưu đề thi: {exam_id} - {exam_data['title']}")
    return {"success": True, "exam_id": exam_id, "message": f"Đã lưu đề thi '{exam_data['title']}' thành công!"}


@router.delete("/delete/{exam_id}")
async def delete_exam(exam_id: str):
    """Xóa đề thi (không xóa đề thi mẫu)."""
    if exam_id in ("sample_exam", "exam_001", "exam_toan_12_101"):
        raise HTTPException(400, "Không thể xóa đề thi mẫu chuẩn của hệ thống!")
    path = DATA_DIR / f"{exam_id}.json"
    if not path.exists():
        raise HTTPException(404, "Không tìm thấy đề thi!")
    path.unlink()
    return {"success": True, "message": "Đã xóa đề thi!"}


# ===================== PARSER NHẬP ĐỀ TỰ ĐỘNG =====================

def _extract_blocks(text_segment: str) -> List[str]:
    """Tách đoạn văn thành từng khối câu hỏi bắt đầu bằng Câu X:, Bài X:, Cau X: hoặc Question X:"""
    # Khớp Câu X:, [Câu X], (Câu X), Bài X:, Question X:...
    pattern = r"(?=(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*(?:\[|\()?(?:C[âa]u|B[àa]i|Question)\s*\d+(?:\]|\))?[\.:\-\/\s\)])"
    raw_blocks = re.split(pattern, text_segment, flags=re.IGNORECASE)
    blocks = []
    for b in raw_blocks:
        b_clean = b.strip()
        start_probe = re.sub(r"^(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*", "", b_clean)
        if re.match(r"^(?:\[|\()?(?:C[âa]u|B[àa]i|Question)\s*\d+", start_probe, re.IGNORECASE):
            blocks.append(b_clean)

    # Dự phòng: Nếu đề không dùng chữ "Câu/Bài", mà chỉ đánh số "1.", "2." ở đầu dòng
    if not blocks:
        num_pattern = r"(?=(?:^|\n)\s*(?:\[|\()?\d+[\.:\-\)\/]\s+)"
        raw_num_blocks = re.split(num_pattern, text_segment)
        for b in raw_num_blocks:
            b_clean = b.strip()
            if re.match(r"^(?:\[|\()?\d+[\.:\-\)\/]\s+", b_clean):
                blocks.append(b_clean)

    return blocks


def _parse_part1_block(q_block: str, allow_lowercase: bool = False, create_empty_if_missing: bool = False) -> Optional[dict]:
    """Phân tích một khối câu hỏi thành câu trắc nghiệm 4 lựa chọn (Phần I)."""
    # Khớp A., A:, A), A/, A-, (A), [A]. Nếu allow_lowercase=True, hỗ trợ cả a, b, c, d
    if allow_lowercase:
        opt_regex = r'(?:^|[\s\n]|(?<=\}\}))(?P<prefix>(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*)(?:(?P<bracket>[\(\[])(?P<key_b>[A-Da-d])[\)\]]|(?P<key_plain>[A-Da-d])[\.\)\:\/\-])\s*(?P<mid>(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*)(?P<post>(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*)'
    else:
        opt_regex = r'(?:^|[\s\n]|(?<=\}\}))(?P<prefix>(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*)(?:(?P<bracket>[\(\[])(?P<key_b>[A-D])[\)\]]|(?P<key_plain>[A-D])[\.\)\:\/\-])\s*(?P<mid>(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*)(?P<post>(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*)'

    matches = list(re.finditer(opt_regex, q_block))

    # Nếu không tìm thấy ít nhất 2 phương án
    if len(matches) < 2:
        if create_empty_if_missing:
            # Vẫn giữ câu hỏi, tạo 4 lựa chọn trống để giáo viên bổ sung thay vì làm mất câu
            raw_q_text = re.sub(r"^(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*(?:\[|\()?(?:C[âa]u|B[àa]i|Question)?\s*\d+(?:\]|\))?[\.:\-\/\s]*", "", q_block, flags=re.IGNORECASE)
            q_text = re.sub(r"\{\{/?(?:RED|UNDERLINE)\}\}", "", raw_q_text).strip()
            return {
                "text": q_text,
                "options": {"A": "", "B": "", "C": "", "D": ""},
                "answer": "A",
                "explanation": ""
            }
        return None

    first_opt_start = matches[0].start()
    raw_q_text = q_block[:first_opt_start].strip()
    raw_q_text = re.sub(r"^(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*(?:\[|\()?(?:C[âa]u|B[àa]i|Question)?\s*\d+(?:\]|\))?[\.:\-\/\s]*", "", raw_q_text, flags=re.IGNORECASE)
    q_text = re.sub(r"\{\{/?(?:RED|UNDERLINE)\}\}", "", raw_q_text).strip()

    options = {"A": "", "B": "", "C": "", "D": ""}
    detected_answer = ""

    for idx, m in enumerate(matches):
        key = (m.group("key_b") or m.group("key_plain")).upper()
        content_start = m.end()
        content_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(q_block)
        raw_val = q_block[content_start:content_end].strip()
        full_opt_str = q_block[m.start():content_end]

        is_marked = False
        if "{{RED}}" in full_opt_str or "{{UNDERLINE}}" in full_opt_str:
            is_marked = True
        elif re.search(r"(\*\s*$|^\s*\*|\[[xX]\]|\(đúng\)|\(Đúng\))", raw_val):
            is_marked = True

        val = re.sub(r"\{\{/?(?:RED|UNDERLINE)\}\}", "", raw_val)
        val = re.sub(r"(\*\s*$|^\s*\*|\[[xX]\]|\(đúng\)|\(Đúng\))", "", val).strip()

        options[key] = val
        if is_marked and not detected_answer:
            detected_answer = key

    if not detected_answer:
        ans_match = re.search(r"(?:Đáp án|Đ/A|KQ)[:\s]+([A-D])\b", q_block, re.IGNORECASE)
        if ans_match:
            detected_answer = ans_match.group(1).upper()

    return {
        "text": _sanitize_math_symbols(q_text),
        "options": {k: _sanitize_math_symbols(v) for k, v in options.items()},
        "answer": detected_answer or "A",
        "explanation": ""
    }


def _parse_part2_block(q_block: str, doc_has_red: bool = False, create_empty_if_missing: bool = False) -> Optional[dict]:
    """Phân tích một khối câu hỏi thành câu trắc nghiệm Đúng/Sai (Phần II)."""
    item_regex = r'(?:^|[\s\n]|(?<=\}\}))(?P<prefix>(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*)(?:(?P<bracket>[\(\[])(?P<key_b>[a-d])[\)\]]|(?P<key_plain>[a-d])[\.\)\:\/\-])\s*(?P<mid>(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*)(?P<post>(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*)'
    matches = list(re.finditer(item_regex, q_block))

    if len(matches) < 2:
        if create_empty_if_missing:
            raw_desc = re.sub(r"^(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*(?:\[|\()?(?:C[âa]u|B[àa]i|Question)?\s*\d+(?:\]|\))?[\.:\-\/\s]*", "", q_block, flags=re.IGNORECASE)
            q_desc = re.sub(r"\{\{/?(?:RED|UNDERLINE)\}\}", "", raw_desc).strip()
            return {
                "text": _sanitize_math_symbols(q_desc),
                "items": {
                    "a": {"text": "", "answer": True},
                    "b": {"text": "", "answer": False},
                    "c": {"text": "", "answer": True},
                    "d": {"text": "", "answer": False}
                }
            }
        return None

    first_item_start = matches[0].start()
    raw_desc = q_block[:first_item_start].strip()
    raw_desc = re.sub(r"^(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*(?:\[|\()?(?:C[âa]u|B[àa]i|Question)?\s*\d+(?:\]|\))?[\.:\-\/\s]*", "", raw_desc, flags=re.IGNORECASE)
    q_desc = re.sub(r"\{\{/?(?:RED|UNDERLINE)\}\}", "", raw_desc).strip()

    q_has_red = "{{RED}}" in q_block or "{{UNDERLINE}}" in q_block
    items = {}

    for idx, m in enumerate(matches):
        key = (m.group("key_b") or m.group("key_plain")).lower()
        content_start = m.end()
        content_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(q_block)
        raw_val = q_block[content_start:content_end].strip()
        full_item_str = q_block[m.start():content_end]

        ans = True
        if "{{RED}}" in full_item_str or "{{UNDERLINE}}" in full_item_str:
            ans = True
        elif q_has_red or doc_has_red:
            ans = False
        elif re.search(r"[\(\[\{]?(?:Sai|S)[\)\]\}]?\s*$", raw_val, re.IGNORECASE):
            ans = False
        elif re.search(r"[\(\[\{]?(?:Đúng|Đ|D)[\)\]\}]?\s*$", raw_val, re.IGNORECASE):
            ans = True

        val = re.sub(r"\{\{/?(?:RED|UNDERLINE)\}\}", "", raw_val)
        val = re.sub(r"[\(\[\{]?(?:Sai|S|Đúng|Đ|D)[\)\]\}]?\s*$", "", val, flags=re.IGNORECASE).strip()
        items[key] = {"text": _sanitize_math_symbols(val), "answer": ans}

    return {
        "text": _sanitize_math_symbols(q_desc),
        "items": items
    }


def _parse_part3_block(q_block: str) -> dict:
    """Phân tích câu trả lời ngắn (Phần III)."""
    clean_q = re.sub(r"^(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*(?:\[|\()?(?:C[âa]u|B[àa]i|Question)?\s*\d+(?:\]|\))?[\.:\-\/\s]*", "", q_block, flags=re.IGNORECASE)
    clean_q = re.sub(r"\{\{/?(?:RED|UNDERLINE)\}\}", "", clean_q).strip()

    ans_match = re.search(r"(?:Đáp án|KQ|Kết quả|Đ/A)[:\s]+([^\n]+)", clean_q, re.IGNORECASE)
    answer = ""
    if ans_match:
        answer = ans_match.group(1).strip()
        clean_q = clean_q[:ans_match.start()].strip()

    clean_ans = _sanitize_math_symbols(answer)
    return {
        "text": _sanitize_math_symbols(clean_q),
        "answer": clean_ans,
        "accepted_answers": [clean_ans] if clean_ans else [],
        "tolerance": 0.01,
        "explanation": ""
    }


@router.post("/parse-text")
async def parse_exam_text(payload: dict):
    """
    Phân tích văn bản đề thi thô thành cấu trúc các phần thi chuẩn.
    Hỗ trợ nhận diện các định dạng: Câu 1, A., B., C., D., a), b), c), d), Đáp án:...
    Bảo toàn 100% số lượng câu hỏi trong tài liệu, không làm mất bất kỳ câu hỏi nào.
    """
    text = payload.get("text", "").strip()
    if not text:
        raise HTTPException(400, "Văn bản đề thi không được để trống!")

    # Chuẩn hóa ngắt dòng và khoảng trắng không ngắt
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")

    parsed = {
        "title": "",
        "subject": "Toán học",
        "grade": "12",
        "duration_minutes": 50,
        "parts": {
            "part1": {"name": "Phần I. Trắc nghiệm nhiều lựa chọn", "instruction": "", "questions": []},
            "part2": {"name": "Phần II. Trắc nghiệm Đúng / Sai", "instruction": "", "questions": []},
            "part3": {"name": "Phần III. Trả lời ngắn", "instruction": "", "questions": []},
            "part4": {"name": "Phần IV. Tự luận", "instruction": "", "questions": []}
        }
    }

    # 1. Trích xuất tiêu đề nếu có
    title_match = re.search(r"(?:ĐỀ KIỂM TRA|BÀI KIỂM TRA|ĐỀ THI)[^\n]+", text, re.IGNORECASE)
    if title_match:
        parsed["title"] = re.sub(r"\{\{/?(?:RED|UNDERLINE)\}\}", "", title_match.group(0)).strip()
    else:
        first_line = text.split("\n")[0].strip()
        if len(first_line) > 5 and not first_line.lower().startswith("câu"):
            parsed["title"] = re.sub(r"\{\{/?(?:RED|UNDERLINE)\}\}", "", first_line).strip()

    # Tự động nhận diện môn học từ tiêu đề hoặc nội dung
    subj_checks = [
        ("Sinh học", r"(?:Sinh\s*học|Môn\s*Sinh\b|\bSinh\s*1[0-2]\b)"),
        ("Toán học", r"(?:Toán\s*học|Môn\s*Toán\b|\bToán\s*1[0-2]\b)"),
        ("Vật lí", r"(?:Vật\s*l[íy]|Môn\s*L[íy]\b|\bL[íy]\s*1[0-2]\b)"),
        ("Hóa học", r"(?:Hóa\s*học|Môn\s*Hóa\b|\bHóa\s*1[0-2]\b)"),
        ("Lịch sử", r"(?:Lịch\s*sử|Môn\s*Sử\b|\bSử\s*1[0-2]\b)"),
        ("Địa lí", r"(?:Địa\s*l[íy]|Môn\s*Địa\b|\bĐịa\s*1[0-2]\b)"),
        ("Tiếng Anh", r"(?:Tiếng\s*Anh|English|\bAnh\s*1[0-2]\b)"),
        ("Ngữ văn", r"(?:Ngữ\s*văn|Văn\s*học|\bVăn\s*1[0-2]\b)"),
        ("Tin học", r"(?:Tin\s*học|\bTin\s*1[0-2]\b)"),
        ("Công nghệ", r"(?:Công\s*nghệ|\bCN\s*1[0-2]\b)"),
        ("Giáo dục kinh tế và pháp luật", r"(?:GDKT|Kinh\s*tế\s*và\s*pháp\s*luật)"),
    ]
    for subj_name, pattern in subj_checks:
        if re.search(pattern, (parsed["title"] or "") + " " + text[:800], re.IGNORECASE):
            parsed["subject"] = subj_name
            break

    # Nhận diện khối lớp
    grade_match = re.search(r"\b(?:Lớp|Khối)\s*(1[0-2]|[6-9])\b|\b(1[0-2]|[6-9])\b", (parsed["title"] or "") + " " + text[:400], re.IGNORECASE)
    if grade_match:
        parsed["grade"] = grade_match.group(1) or grade_match.group(2)

    has_red_keys = "{{RED}}" in text

    # 2. Phân chia các phần (PHẦN I, PHẦN II, PHẦN III, PHẦN IV)
    part_splits = re.split(r"((?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*(?:PHẦN|Phần)\s+(?:I{1,3}|IV|[1-4]|[A-D]|thứ\s*[a-z\u0103\u00e2]+)[^\n]*)", text, flags=re.IGNORECASE)
    
    sections = {"part1": "", "part2": "", "part3": "", "part4": ""}

    if len(part_splits) > 1:
        current_section = None
        for i in range(1, len(part_splits), 2):
            header = re.sub(r"\{\{/?(?:RED|UNDERLINE)\}\}", "", part_splits[i]).strip()
            content = part_splits[i+1] if i+1 < len(part_splits) else ""
            
            if re.search(r"(?:PHẦN|Phần)\s*(?:I\b|1\b|A\b|thứ\s*nhất|thứ\s*một)", header, re.IGNORECASE):
                current_section = "part1"
            elif re.search(r"(?:PHẦN|Phần)\s*(?:II\b|2\b|B\b|thứ\s*hai)", header, re.IGNORECASE):
                current_section = "part2"
            elif re.search(r"(?:PHẦN|Phần)\s*(?:III\b|3\b|C\b|thứ\s*ba)", header, re.IGNORECASE):
                current_section = "part3"
            elif re.search(r"(?:PHẦN|Phần)\s*(?:IV\b|4\b|D\b|thứ\s*bốn|thứ\s*tư)", header, re.IGNORECASE):
                current_section = "part4"
            
            if current_section:
                sections[current_section] += "\n" + content

        # Xử lý từng phần khi có phân mục rõ ràng
        # Phần I: Luôn giữ toàn bộ các câu trong Phần I
        p1_blocks = _extract_blocks(sections["part1"])
        for q_block in p1_blocks:
            # Thử phân tích dạng trắc nghiệm (hỗ trợ cả A-D lẫn a-d)
            p1_res = _parse_part1_block(q_block, allow_lowercase=True, create_empty_if_missing=True)
            if p1_res:
                p1_res["id"] = f"p1_q{len(parsed['parts']['part1']['questions']) + 1}"
                parsed["parts"]["part1"]["questions"].append(p1_res)

        # Phần II: Trắc nghiệm Đúng/Sai
        p2_blocks = _extract_blocks(sections["part2"])
        for q_block in p2_blocks:
            p2_res = _parse_part2_block(q_block, doc_has_red=has_red_keys, create_empty_if_missing=True)
            if p2_res:
                p2_res["id"] = f"p2_q{len(parsed['parts']['part2']['questions']) + 1}"
                parsed["parts"]["part2"]["questions"].append(p2_res)

        # Phần III: Trả lời ngắn
        p3_blocks = _extract_blocks(sections["part3"])
        for q_block in p3_blocks:
            p3_res = _parse_part3_block(q_block)
            p3_res["id"] = f"p3_q{len(parsed['parts']['part3']['questions']) + 1}"
            parsed["parts"]["part3"]["questions"].append(p3_res)

        # Phần IV: Tự luận
        p4_blocks = _extract_blocks(sections["part4"])
        for q_block in p4_blocks:
            clean_q = re.sub(r"^(?:\{\{/?(?:RED|UNDERLINE)\}\}\s*)*(?:\[|\()?(?:C[âa]u|B[àa]i|Question)?\s*\d+(?:\]|\))?[\.:\-\/\s]*", "", q_block, flags=re.IGNORECASE)
            clean_q = re.sub(r"\{\{/?(?:RED|UNDERLINE)\}\}", "", clean_q).strip()
            first_sentence = clean_q.split(".")[0][:60]
            parsed["parts"]["part4"]["questions"].append({
                "id": f"p4_q{len(parsed['parts']['part4']['questions']) + 1}",
                "title": first_sentence or "Câu tự luận",
                "text": clean_q,
                "rubric": {
                    "max_score": 1.0,
                    "criteria": [
                        {"name": "Phương pháp và lập luận", "points": 0.5},
                        {"name": "Kết quả tính toán chính xác", "points": 0.5}
                    ],
                    "sample_answer": ""
                }
            })

    else:
        # TRƯỜNG HỢP ĐỀ KHÔNG CÓ TIÊU ĐỀ PHẦN I, PHẦN II (ví dụ đề 20 câu trắc nghiệm thuần túy):
        # Trích xuất toàn bộ các câu hỏi trong tài liệu
        all_blocks = _extract_blocks(text)
        for b in all_blocks:
            # 1. Kiểm tra xem có phải câu hỏi Đúng/Sai (Phần II) hay không (phải có từ khóa khẳng định hoặc đúng sai)
            is_explicit_tf = bool(re.search(r"(?:khẳng định sau đúng hay sai|xét tính đúng sai|xét các mệnh đề|xét các khẳng định)", b, re.IGNORECASE))
            if is_explicit_tf:
                p2_res = _parse_part2_block(b, doc_has_red=has_red_keys)
                if p2_res:
                    p2_res["id"] = f"p2_q{len(parsed['parts']['part2']['questions']) + 1}"
                    parsed["parts"]["part2"]["questions"].append(p2_res)
                    continue

            # 2. Thử dạng Trắc nghiệm 4 lựa chọn (Phần I - A, B, C, D)
            p1_res = _parse_part1_block(b, allow_lowercase=False)
            if p1_res:
                p1_res["id"] = f"p1_q{len(parsed['parts']['part1']['questions']) + 1}"
                parsed["parts"]["part1"]["questions"].append(p1_res)
                continue

            # 3. Thử dạng Trắc nghiệm với nhãn a), b), c), d)
            p1_lower = _parse_part1_block(b, allow_lowercase=True)
            if p1_lower:
                p1_lower["id"] = f"p1_q{len(parsed['parts']['part1']['questions']) + 1}"
                parsed["parts"]["part1"]["questions"].append(p1_lower)
                continue

            # 4. Kiểm tra xem có dòng "Đáp án: <số>" để xếp vào Trả lời ngắn (Phần III)
            has_ans_line = bool(re.search(r"(?:Đáp án|KQ|Kết quả|Đ/A)[:\s]+[^\n]+", b, re.IGNORECASE))
            if has_ans_line:
                p3_res = _parse_part3_block(b)
                p3_res["id"] = f"p3_q{len(parsed['parts']['part3']['questions']) + 1}"
                parsed["parts"]["part3"]["questions"].append(p3_res)
                continue

            # 5. Mặc định đưa vào Phần I để giáo viên chỉnh sửa, TUYỆT ĐỐI KHÔNG LÀM MẤT CÂU
            p1_fallback = _parse_part1_block(b, allow_lowercase=True, create_empty_if_missing=True)
            if p1_fallback:
                p1_fallback["id"] = f"p1_q{len(parsed['parts']['part1']['questions']) + 1}"
                parsed["parts"]["part1"]["questions"].append(p1_fallback)

    # Đảm bảo tổng số câu tìm được: nếu sau các bước mà vẫn rỗng, quét toàn bộ dòng
    total_found = (
        len(parsed["parts"]["part1"]["questions"])
        + len(parsed["parts"]["part2"]["questions"])
        + len(parsed["parts"]["part3"]["questions"])
        + len(parsed["parts"]["part4"]["questions"])
    )
    if total_found == 0:
        all_blocks = _extract_blocks(text)
        for b in all_blocks:
            p1_c = _parse_part1_block(b, allow_lowercase=True, create_empty_if_missing=True)
            if p1_c:
                p1_c["id"] = f"p1_q{len(parsed['parts']['part1']['questions']) + 1}"
                parsed["parts"]["part1"]["questions"].append(p1_c)

    return {
        "success": True,
        "parsed": parsed,
        "summary": {
            "part1_count": len(parsed["parts"]["part1"]["questions"]),
            "part2_count": len(parsed["parts"]["part2"]["questions"]),
            "part3_count": len(parsed["parts"]["part3"]["questions"]),
            "part4_count": len(parsed["parts"]["part4"]["questions"]),
            "total_count": (
                len(parsed["parts"]["part1"]["questions"])
                + len(parsed["parts"]["part2"]["questions"])
                + len(parsed["parts"]["part3"]["questions"])
                + len(parsed["parts"]["part4"]["questions"])
            )
        }
    }


# ===================== TẢI MẪU SOẠN ĐỀ THI =====================

@router.get("/template/download")
async def download_exam_template(format: str = Query("txt", pattern="^(txt|json|docx)$")):
    """Tải file mẫu soạn đề thi (.txt, .json hoặc .docx) để giáo viên nhập nội dung."""
    
    if format == "json":
        # Mẫu JSON
        sample_path = DATA_DIR / "exam_toan_12_101.json"
        if sample_path.exists():
            content = sample_path.read_text(encoding="utf-8")
        else:
            content = json.dumps({"title": "Đề thi mẫu", "parts": {}}, ensure_ascii=False, indent=2)
            
        return Response(
            content=content.encode("utf-8"),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="mau_de_thi_gdpt2026.json"'}
        )

    elif format == "docx":
        # Mẫu Word (.docx)
        import docx
        from docx.shared import Inches, Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        
        doc = docx.Document()
        
        # Tiêu đề trường/sở
        p_head = doc.add_paragraph()
        p_head.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_h1 = p_head.add_run("SỞ GD&ĐT ... - TRƯỜNG THPT ...\n")
        run_h1.bold = True
        run_h1.font.size = Pt(13)
        run_h2 = p_head.add_run("ĐỀ KIỂM TRA ĐỊNH KỲ CHUẨN BỘ GD&ĐT 2026\n")
        run_h2.bold = True
        run_h2.font.size = Pt(15)
        run_h3 = p_head.add_run("Môn: TOÁN HỌC - Lớp: 12 (Thời gian làm bài: 50 phút)\n")
        run_h3.font.size = Pt(12)
        run_h3.font.italic = True
        
        doc.add_paragraph("─" * 45).alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # BẢNG HƯỚNG DẪN ĐÁNH DẤU ĐÁP ÁN CHO GIÁO VIÊN
        table_guide = doc.add_table(rows=1, cols=1)
        table_guide.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cell = table_guide.cell(0, 0)
        p_guide = cell.paragraphs[0]
        r_g_title = p_guide.add_run("📌 QUY ƯỚC ĐÁNH DẤU ĐÁP ÁN KHI SOẠN ĐỀ CHO HỆ THỐNG:\n")
        r_g_title.bold = True
        r_g_title.font.size = Pt(11)
        
        p_guide.add_run("• ")
        r_p1_lbl = p_guide.add_run("Phần I (Trắc nghiệm nhiều lựa chọn): ")
        r_p1_lbl.bold = True
        p_guide.add_run("Phương án đúng được ")
        r_p1_red = p_guide.add_run("TÔ MÀU ĐỎ")
        r_p1_red.bold = True
        r_p1_red.font.color.rgb = RGBColor(255, 0, 0)
        p_guide.add_run(". Tuyệt đối KHÔNG đánh chữ [Đúng] vào các đáp án.\n")

        p_guide.add_run("• ")
        r_p2_lbl = p_guide.add_run("Phần II (Trắc nghiệm Đúng / Sai): ")
        r_p2_lbl.bold = True
        p_guide.add_run("Ý nào ")
        r_p2_d = p_guide.add_run("ĐÚNG thì TÔ MÀU ĐỎ")
        r_p2_d.bold = True
        r_p2_d.font.color.rgb = RGBColor(255, 0, 0)
        p_guide.add_run(", ý nào ")
        r_p2_s = p_guide.add_run("SAI thì để MÀU ĐEN bình thường")
        r_p2_s.bold = True
        p_guide.add_run(". Tuyệt đối KHÔNG đánh chữ (Đúng) hoặc (Sai) vào các đáp án.\n")

        p_guide.add_run("• ")
        r_p3_lbl = p_guide.add_run("Phần III (Trả lời ngắn): ")
        r_p3_lbl.bold = True
        p_guide.add_run("Ghi dòng ")
        r_p3_box = p_guide.add_run("Đáp án: <kết quả>")
        r_p3_box.bold = True
        p_guide.add_run(" bằng chữ màu đen bình thường (")
        r_p3_note = p_guide.add_run("KHÔNG CẦN TÔ ĐỎ ĐÁP ÁN")
        r_p3_note.bold = True
        p_guide.add_run(").")

        doc.add_paragraph()

        # ================= PHẦN I =================
        p1_h = doc.add_heading("PHẦN I (5,0 điểm). Thí sinh trả lời từ câu 1 đến câu 12. Mỗi câu chọn 1 phương án đúng.", level=2)
        
        # Câu 1: B đúng -> tô đỏ, không ghi [Đúng]
        doc.add_paragraph("Câu 1: Trong không gian Oxyz, cho đường thẳng d: (x-1)/2 = (y+1)/-3 = z/1. Vectơ chỉ phương của d là:")
        p_opt1 = doc.add_paragraph()
        p_opt1.add_run("A. u = (1; -1; 0)        ")
        r1_b = p_opt1.add_run("B. u = (2; -3; 1)")
        r1_b.bold = True
        r1_b.font.color.rgb = RGBColor(255, 0, 0)
        p_opt1.add_run("        C. u = (2; 3; 1)        D. u = (-1; 1; 0)")
        
        # Câu 2: D đúng -> tô đỏ, không ghi [Đúng]
        doc.add_paragraph("Câu 2: Tập xác định của hàm số y = log2(x - 3) là:")
        p_opt2 = doc.add_paragraph()
        p_opt2.add_run("A. (-∞; 3)        B. ℝ \\ {3}        C. [3; +∞)        ")
        r2_d = p_opt2.add_run("D. (3; +∞)")
        r2_d.bold = True
        r2_d.font.color.rgb = RGBColor(255, 0, 0)

        p_note1 = doc.add_paragraph("(Thầy/Cô tiếp tục soạn các câu 3, 4, ... theo định dạng trên, đáp án đúng tô màu đỏ)")
        p_note1.runs[0].font.italic = True
        p_note1.runs[0].font.color.rgb = RGBColor(128, 128, 128)
        
        # ================= PHẦN II =================
        doc.add_heading("PHẦN II (4,0 điểm). Thí sinh trả lời từ câu 1 đến câu 4. Mỗi ý a, b, c, d chọn Đúng hoặc Sai.", level=2)
        doc.add_paragraph("Câu 1: Một chất điểm chuyển động với vận tốc v(t) = 3t^2 - 6t + 4 (m/s), với t >= 0.")
        
        # Ý a (Đúng) -> Tô đỏ, không ghi (Đúng)
        p2_a = doc.add_paragraph()
        r2_a = p2_a.add_run("a) Vận tốc tức thời nhỏ nhất của chất điểm bằng 1 m/s.")
        r2_a.bold = True
        r2_a.font.color.rgb = RGBColor(255, 0, 0)

        # Ý b (Đúng) -> Tô đỏ, không ghi (Đúng)
        p2_b = doc.add_paragraph()
        r2_b = p2_b.add_run("b) Gia tốc của chất điểm tại thời điểm t là a(t) = 6t - 6 (m/s^2).")
        r2_b.bold = True
        r2_b.font.color.rgb = RGBColor(255, 0, 0)

        # Ý c (Sai) -> Màu đen bình thường, không ghi (Sai)
        p2_c = doc.add_paragraph()
        p2_c.add_run("c) Tại thời điểm t = 2 (s), gia tốc của chất điểm bằng 12 m/s^2.")

        # Ý d (Đúng) -> Tô đỏ, không ghi (Đúng)
        p2_d = doc.add_paragraph()
        r2_d = p2_d.add_run("d) Quãng đường chất điểm đi được từ t = 0 đến t = 3 là 15 m.")
        r2_d.bold = True
        r2_d.font.color.rgb = RGBColor(255, 0, 0)

        p_note2 = doc.add_paragraph("(Thầy/Cô tiếp tục soạn các câu 2, 3, 4 theo định dạng trên: Ý đúng tô màu đỏ, ý sai để màu đen bình thường)")
        p_note2.runs[0].font.italic = True
        p_note2.runs[0].font.color.rgb = RGBColor(128, 128, 128)

        # ================= PHẦN III =================
        doc.add_heading("PHẦN III (1,0 điểm). Thí sinh trả lời từ câu 1 đến câu 6. Điền kết quả ngắn.", level=2)
        
        # Câu 1: Màu đen bình thường, Đáp án: 9 (màu đen bình thường, ko cần tô đỏ)
        doc.add_paragraph("Câu 1: Tìm hệ số góc của tiếp tuyến của đồ thị hàm số y = x^3 - 3x^2 + 2 tại x0 = 3.")
        p3_ans1 = doc.add_paragraph()
        r3_ans1 = p3_ans1.add_run("Đáp án: 9")
        r3_ans1.bold = True

        # Câu 2: Màu đen bình thường, Đáp án: 5 (màu đen bình thường, ko cần tô đỏ)
        doc.add_paragraph("Câu 2: Cho hình hộp chữ nhật ABCD.A'B'C'D' có AB = 3, AD = 4, AA' = 5. Khoảng cách giữa AB và C'D' là bao nhiêu?")
        p3_ans2 = doc.add_paragraph()
        r3_ans2 = p3_ans2.add_run("Đáp án: 5")
        r3_ans2.bold = True

        p_note3 = doc.add_paragraph("(Thầy/Cô tiếp tục soạn các câu 3, 4, 5, 6 theo định dạng trên: Dòng 'Đáp án: <kết quả>' để chữ màu đen bình thường, không cần tô đỏ)")
        p_note3.runs[0].font.italic = True
        p_note3.runs[0].font.color.rgb = RGBColor(128, 128, 128)

        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedoc.wordprocessingml.document",
            headers={"Content-Disposition": 'attachment; filename="mau_de_thi_gdpt2026.docx"'}
        )

    else:
        # Mẫu TXT
        txt_content = """SỞ GD&ĐT ... - TRƯỜNG THPT ...
ĐỀ KIỂM TRA ĐỊNH KỲ MÔN TOÁN 12 (CHUẨN BỘ GD&ĐT 2026)
Môn: TOÁN HỌC - Lớp: 12 - Thời gian: 50 phút

📌 QUY ƯỚC SOẠN ĐỀ:
- Nếu dùng file Word (.docx): Đáp án đúng chỉ cần TÔ MÀU ĐỎ (không đánh chữ Đúng hay Sai). Phần III ghi "Đáp án: <số>" chữ đen bình thường.
- Nếu dùng file Text (.txt): Đánh dấu * sau đáp án đúng của Phần I, hoặc ghi (Đúng)/(Sai) ở Phần II.

PHẦN I (5.0 điểm). Trắc nghiệm nhiều lựa chọn (Câu 1 đến Câu 12).
Câu 1: Trong không gian Oxyz, cho đường thẳng d: (x-1)/2 = (y+1)/-3 = z/1. Vectơ chỉ phương của d là
A. u = (1; -1; 0)
B. u = (2; -3; 1) *
C. u = (2; 3; 1)
D. u = (-1; 1; 0)

Câu 2: Tập xác định của hàm số y = log2(x - 3) là
A. (-inf; 3)
B. R \\ {3}
C. [3; +inf)
D. (3; +inf) *

PHẦN II (4.0 điểm). Trắc nghiệm Đúng / Sai (Câu 1 đến Câu 4).
Câu 1: Một chất điểm chuyển động với vận tốc v(t) = 3t^2 - 6t + 4 (m/s), với t >= 0.
a) Vận tốc tức thời nhỏ nhất của chất điểm bằng 1 m/s. (Đúng)
b) Gia tốc của chất điểm tại thời điểm t là a(t) = 6t - 6. (Đúng)
c) Tại thời điểm t = 2 (s), gia tốc của chất điểm bằng 12 m/s^2. (Sai)
d) Quãng đường chất điểm đi được từ t = 0 đến t = 3 là 15 m. (Đúng)

PHẦN III (1.0 điểm). Trả lời ngắn (Câu 1 đến Câu 6).
Câu 1: Tìm hệ số góc của tiếp tuyến của đồ thị hàm số y = x^3 - 3x^2 + 2 tại điểm có hoành độ x0 = 3.
Đáp án: 9

Câu 2: Cho hình hộp chữ nhật ABCD.A'B'C'D' có AB = 3, AD = 4, AA' = 5. Tính khoảng cách giữa hai đường thẳng AB và C'D'.
Đáp án: 5
"""
        return Response(
            content=txt_content.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="mau_soan_de_thi.txt"'}
        )


def _ensure_part_structure(exam_data: dict):
    """Đảm bảo cấu trúc JSON đủ các phần và tự động sinh id cho các câu hỏi nếu thiếu."""
    parts = exam_data.setdefault("parts", {})

    p1 = parts.setdefault("part1", {})
    p1.setdefault("name", "Phần I: Trắc nghiệm nhiều lựa chọn")
    p1.setdefault("instruction", "Chọn đáp án đúng duy nhất (A, B, C hoặc D) cho mỗi câu sau.")
    p1.setdefault("questions", [])

    p2 = parts.setdefault("part2", {})
    p2.setdefault("name", "Phần II: Trắc nghiệm Đúng / Sai")
    p2.setdefault("instruction", "Trong mỗi câu, xét tính Đúng (Đ) hoặc Sai (S) của mỗi ý (a), (b), (c), (d).")
    p2.setdefault("questions", [])

    p3 = parts.setdefault("part3", {})
    p3.setdefault("name", "Phần III: Trả lời ngắn")
    p3.setdefault("instruction", "Điền đáp án vào ô trống. Chỉ ghi kết quả (số hoặc biểu thức đơn giản nhất).")
    p3.setdefault("questions", [])

    p4 = parts.setdefault("part4", {})
    p4.setdefault("name", "Phần IV: Tự luận tự chọn")
    p4.setdefault("instruction", "Chọn MỘT trong các câu dưới đây để làm. Trình bày đầy đủ các bước giải.")
    p4.setdefault("questions", [])

    # Tự động gán id cho các câu hỏi nếu chưa có
    for part_key, prefix in [("part1", "p1_q"), ("part2", "p2_q"), ("part3", "p3_q"), ("part4", "p4_q")]:
        for i, q in enumerate(parts.get(part_key, {}).get("questions", []), 1):
            if not q.get("id"):
                q["id"] = f"{prefix}{i}"
