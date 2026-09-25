"""
Export Service - Xuất bảng điểm và đề thi
Hỗ trợ: Excel (.xlsx), HTML (in ấn/PDF), Word (.docx)
"""
import io
import json
import logging
from datetime import datetime
from typing import List, Optional

import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

logger = logging.getLogger(__name__)


def export_class_results_excel(submissions: List[dict], exam_title: str = "Bảng điểm") -> bytes:
    """
    Xuất bảng điểm toàn lớp ra file Excel.
    submissions: List các kết quả bài thi đã chấm.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Bảng điểm"

    # Style
    header_fill = PatternFill("solid", fgColor="1E3A8A")
    header_font = Font(bold=True, color="FFFFFF", size=12)
    title_font = Font(bold=True, size=14)
    border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    center = Alignment(horizontal='center', vertical='center')

    # Tiêu đề
    ws.merge_cells("A1:I1")
    ws["A1"] = exam_title
    ws["A1"].font = title_font
    ws["A1"].alignment = center

    ws.merge_cells("A2:I2")
    ws["A2"] = f"Ngày xuất: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    ws["A2"].alignment = center

    # Header
    headers = ["STT", "Họ và Tên", "Lớp", "Phần I\n(5đ)", "Phần II\n(2đ)", "Phần III\n(2đ)", "Phần IV\n(1đ)", "Tổng điểm\n(/10đ)", "Xếp loại"]
    col_widths = [5, 25, 8, 10, 10, 10, 10, 12, 12]

    for col, (h, w) in enumerate(zip(headers, col_widths), 1):
        cell = ws.cell(row=4, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center
        cell.border = border
        ws.column_dimensions[get_column_letter(col)].width = w

    ws.row_dimensions[4].height = 35

    # Dữ liệu học sinh
    rank_colors = {"Giỏi": "D1FAE5", "Khá": "DBEAFE", "Trung bình": "FEF9C3", "Yếu": "FEE2E2"}

    for stt, sub in enumerate(submissions, 1):
        row = stt + 4
        scores = sub.get("scores", {})
        rank = scores.get("rank", "")
        fill_color = rank_colors.get(rank, "FFFFFF")

        values = [
            stt,
            sub.get("student_name", ""),
            sub.get("student_class", ""),
            scores.get("part1_score", 0),
            scores.get("part2_score", 0),
            scores.get("part3_score", 0),
            scores.get("part4_score", 0),
            scores.get("total_score", 0),
            rank
        ]

        for col, val in enumerate(values, 1):
            cell = ws.cell(row=row, column=col, value=val)
            cell.alignment = center
            cell.border = border
            if fill_color != "FFFFFF":
                cell.fill = PatternFill("solid", fgColor=fill_color)
            if col == 8:
                cell.font = Font(bold=True, size=12)

    # Thống kê cuối
    stat_row = len(submissions) + 6
    ws.cell(row=stat_row, column=1, value="THỐNG KÊ").font = Font(bold=True)
    if submissions:
        all_scores = [s.get("scores", {}).get("total_score", 0) for s in submissions]
        ws.cell(row=stat_row+1, column=1, value=f"Điểm TB: {sum(all_scores)/len(all_scores):.2f}")
        ws.cell(row=stat_row+2, column=1, value=f"Điểm cao nhất: {max(all_scores):.2f}")
        ws.cell(row=stat_row+3, column=1, value=f"Điểm thấp nhất: {min(all_scores):.2f}")
        
        gioi = sum(1 for s in submissions if s.get("scores", {}).get("rank") == "Giỏi")
        kha = sum(1 for s in submissions if s.get("scores", {}).get("rank") == "Khá")
        tb = sum(1 for s in submissions if s.get("scores", {}).get("rank") == "Trung bình")
        yeu = sum(1 for s in submissions if s.get("scores", {}).get("rank") == "Yếu")
        ws.cell(row=stat_row+4, column=1, value=f"Giỏi: {gioi} | Khá: {kha} | Trung bình: {tb} | Yếu: {yeu}")

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


def export_result_html(submission: dict, exam_data: dict) -> str:
    """
    Tạo HTML phiếu kết quả bài thi DÀNH CHO THÍ SINH (dùng để in/xuất PDF).
    TUÂN THỦ: Chỉ in kết quả số lượng câu đúng câu sai và điểm số, KHÔNG HIỆN ĐÁP ÁN ĐỀ.
    """
    scores = submission.get("scores", {})
    p1 = submission.get("part1_result", {})
    p2 = submission.get("part2_result", {})
    p3 = submission.get("part3_result", {})
    p4 = submission.get("part4_result", {})
    
    submitted_at = submission.get("submitted_at", "")
    if submitted_at:
        try:
            dt = datetime.fromisoformat(submitted_at)
            submitted_at = dt.strftime("%d/%m/%Y %H:%M")
        except:
            pass

    rank_class = {"Giỏi": "text-green-700", "Khá": "text-blue-700", "Trung bình": "text-yellow-700", "Yếu": "text-red-600"}
    rank_color = rank_class.get(scores.get("rank", ""), "text-gray-700")

    # Thống kê Phần I
    p1_total = p1.get("total_count", len(p1.get("details", [])))
    p1_correct = p1.get("correct_count", 0)
    p1_wrong = max(0, p1_total - p1_correct)

    # Thống kê Phần II
    p2_details = p2.get("details", [])
    p2_total_items = 0
    p2_correct_items = 0
    for q in p2_details:
        items = q.get("items", [])
        if isinstance(items, list):
            p2_total_items += len(items)
            p2_correct_items += sum(1 for it in items if it.get("is_correct"))
        elif isinstance(items, dict):
            p2_total_items += len(items)
            p2_correct_items += sum(1 for k, it in items.items() if it.get("is_correct"))
        else:
            p2_total_items += 4
            p2_correct_items += q.get("correct_count", 0)
    if p2_total_items == 0:
        p2_total_items = len(p2_details) * 4
    p2_wrong_items = max(0, p2_total_items - p2_correct_items)

    # Thống kê Phần III
    p3_total = p3.get("total_count", len(p3.get("details", [])))
    p3_correct = p3.get("correct_count", 0)
    p3_wrong = max(0, p3_total - p3_correct)

    # Tổng hợp toàn bài
    total_items = p1_total + p2_total_items + p3_total
    total_correct = p1_correct + p2_correct_items + p3_correct
    total_wrong = p1_wrong + p2_wrong_items + p3_wrong
    accuracy = round((total_correct / total_items * 100), 1) if total_items > 0 else 0

    duration_m = submission.get('duration_seconds', 0) // 60
    duration_s = submission.get('duration_seconds', 0) % 60

    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<title>Phiếu kết quả thi - {submission.get('student_name','')}</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  @media print {{
    .no-print {{ display: none !important; }}
    body {{ background: white !important; padding: 0 !important; font-size: 13px; }}
    .print-card {{ box-shadow: none !important; border: 1px solid #e5e7eb !important; }}
  }}
</style>
</head>
<body class="bg-gray-100 p-4 sm:p-6 font-sans">
<div class="max-w-3xl mx-auto bg-white shadow-xl rounded-2xl overflow-hidden print-card border">
  
  <!-- Header Trường & Kỳ thi -->
  <div class="p-6 text-center border-b bg-gradient-to-r from-blue-900 to-indigo-900 text-white">
    <p class="text-xs uppercase tracking-widest text-blue-200 font-bold mb-1">SỞ GIÁO DỤC VÀ ĐÀO TẠO • TRƯỜNG THPT</p>
    <h1 class="text-xl sm:text-2xl font-black">{exam_data.get('title','PHIẾU BÁO ĐIỂM KIỂM TRA')}</h1>
    <p class="mt-1 text-xs text-blue-200">Môn: {exam_data.get('subject','Toán học')} | Khối: {exam_data.get('grade','12')} | Chuẩn GDPT 2025</p>
  </div>
  
  <!-- Thông tin thí sinh -->
  <div class="grid grid-cols-2 sm:grid-cols-4 gap-4 p-5 bg-blue-50/60 border-b text-xs sm:text-sm">
    <div>
      <span class="text-gray-500 block text-xs">Họ và tên thí sinh</span>
      <strong class="text-gray-900 text-base">{submission.get('student_name','')}</strong>
    </div>
    <div>
      <span class="text-gray-500 block text-xs">Lớp</span>
      <strong class="text-gray-900 text-base">{submission.get('student_class','')}</strong>
    </div>
    <div>
      <span class="text-gray-500 block text-xs">Thời gian nộp bài</span>
      <strong class="text-gray-800">{submitted_at}</strong>
    </div>
    <div>
      <span class="text-gray-500 block text-xs">Thời gian làm bài</span>
      <strong class="text-gray-800">{duration_m} phút {duration_s} giây</strong>
    </div>
  </div>

  <!-- BẢNG TỔNG ĐIỂM VÀ XẾP LOẠI -->
  <div class="p-6 border-b">
    <div class="flex flex-col sm:flex-row items-center justify-between gap-6 bg-gradient-to-br from-indigo-50 to-blue-50 rounded-2xl p-6 border-2 border-indigo-100">
      <div>
        <p class="text-xs font-bold text-gray-500 uppercase tracking-wider">TỔNG ĐIỂM ĐẠT ĐƯỢC</p>
        <div class="flex items-baseline gap-2 mt-1">
          <span class="text-6xl font-black text-blue-950">{scores.get('total_score', 0)}</span>
          <span class="text-2xl text-blue-800 font-bold">/ 10.0 điểm</span>
        </div>
        <p class="text-xs text-gray-600 mt-1">Tỷ lệ đạt: <strong>{scores.get('percentage', 0)}%</strong> tổng điểm bài kiểm tra</p>
      </div>

      <div class="text-center sm:text-right">
        <p class="text-xs font-bold text-gray-500 uppercase tracking-wider">XẾP LOẠI</p>
        <p class="text-3xl font-black {rank_color} mt-1">{scores.get('rank', 'Chưa xếp loại')}</p>
        <p class="text-xs text-gray-500 mt-1">Độ chính xác: <strong>{accuracy}%</strong></p>
      </div>
    </div>
  </div>

  <!-- BẢNG THỐNG KÊ SỐ LƯỢNG CÂU ĐÚNG & CÂU SAI (TUÂN THỦ: KHÔNG CÓ ĐÁP ÁN ĐỀ) -->
  <div class="p-6 border-b space-y-4">
    <h2 class="text-base font-bold text-gray-900 flex items-center gap-2">
      <span>📊</span>
      <span>Thống Kê Số Lượng Câu Đúng & Sai</span>
    </h2>

    <div class="overflow-x-auto">
      <table class="w-full text-sm border-collapse border border-gray-200 rounded-xl overflow-hidden">
        <thead class="bg-gray-100 text-gray-800 text-xs uppercase font-bold">
          <tr>
            <th class="border border-gray-200 px-4 py-3 text-left">Phần thi</th>
            <th class="border border-gray-200 px-3 py-3 text-center text-green-700">✓ Số câu Đúng</th>
            <th class="border border-gray-200 px-3 py-3 text-center text-red-600">✗ Số câu Sai</th>
            <th class="border border-gray-200 px-3 py-3 text-center">Tổng số câu / ý</th>
            <th class="border border-gray-200 px-3 py-3 text-center text-blue-900">Điểm đạt</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-200 text-xs sm:text-sm">
          <tr>
            <td class="border border-gray-200 px-4 py-3 font-semibold text-gray-800">
              Phần I: Trắc nghiệm 4 lựa chọn
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center font-bold text-green-700 bg-green-50/50">
              {p1_correct} câu
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center font-bold text-red-600 bg-red-50/50">
              {p1_wrong} câu
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center font-semibold text-gray-700">
              {p1_total} câu
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center font-black text-indigo-900">
              {scores.get('part1_score', 0)} / 5.0đ
            </td>
          </tr>

          <tr>
            <td class="border border-gray-200 px-4 py-3 font-semibold text-gray-800">
              Phần II: Trắc nghiệm Đúng / Sai
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center font-bold text-green-700 bg-green-50/50">
              {p2_correct_items} ý
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center font-bold text-red-600 bg-red-50/50">
              {p2_wrong_items} ý
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center font-semibold text-gray-700">
              {p2_total_items} ý
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center font-black text-purple-900">
              {scores.get('part2_score', 0)} / 4.0đ
            </td>
          </tr>

          <tr>
            <td class="border border-gray-200 px-4 py-3 font-semibold text-gray-800">
              Phần III: Trả lời ngắn
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center font-bold text-green-700 bg-green-50/50">
              {p3_correct} câu
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center font-bold text-red-600 bg-red-50/50">
              {p3_wrong} câu
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center font-semibold text-gray-700">
              {p3_total} câu
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center font-black text-teal-900">
              {scores.get('part3_score', 0)} / 1.0đ
            </td>
          </tr>

          {f'''<tr>
            <td class="border border-gray-200 px-4 py-3 font-semibold text-gray-800">
              Phần IV: Tự luận
            </td>
            <td colspan="3" class="border border-gray-200 px-3 py-3 text-center text-gray-600 italic">
              Đã chấm điểm theo barem tiêu chí
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center font-black text-orange-700">
              {scores.get('part4_score', 0)} / 1.0đ
            </td>
          </tr>''' if p4 and not p4.get("skipped") else ''}

          <tr class="bg-blue-50/80 font-black">
            <td class="border border-gray-200 px-4 py-3 text-gray-900">
              TỔNG CỘNG TOÀN BÀI
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center text-green-800 text-base">
              {total_correct}
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center text-red-700 text-base">
              {total_wrong}
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center text-gray-900 text-base">
              {total_items}
            </td>
            <td class="border border-gray-200 px-3 py-3 text-center text-blue-900 text-lg">
              {scores.get('total_score', 0)} / 10đ
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Ghi chú bảo mật -->
    <div class="bg-gray-50 rounded-xl p-3.5 border text-xs text-gray-600 flex items-center gap-2">
      <span>🔒</span>
      <span><strong>Ghi chú bảo mật:</strong> Để đảm bảo tính công bằng và bảo mật ca thi, phiếu kết quả này chỉ công bố số lượng câu đúng/sai và điểm số. Đáp án đề thi không được công khai.</span>
    </div>
  </div>

  <!-- Chữ ký xác nhận -->
  <div class="p-6 grid grid-cols-2 text-center text-xs sm:text-sm">
    <div>
      <p class="font-bold text-gray-700">THÍ SINH</p>
      <p class="text-xs text-gray-400 italic">(Ký và ghi rõ họ tên)</p>
      <div class="h-20"></div>
      <p class="font-semibold text-gray-800">{submission.get('student_name','')}</p>
    </div>
    <div>
      <p class="font-bold text-gray-700">CÁN BỘ CHẤM THI</p>
      <p class="text-xs text-gray-400 italic">(Ký và ghi rõ họ tên)</p>
      <div class="h-20"></div>
      <p class="font-semibold text-gray-800">................................................</p>
    </div>
  </div>

  <!-- Nút in (ẩn khi bấm in) -->
  <div class="p-4 bg-gray-50 border-t text-center no-print flex items-center justify-center gap-3">
    <button onclick="window.print()" class="bg-blue-900 hover:bg-blue-800 text-white px-6 py-2.5 rounded-xl font-bold text-sm shadow-md transition-all flex items-center gap-1.5 cursor-pointer">
      <span>🖨️</span><span>In phiếu kết quả (A4)</span>
    </button>
    <button onclick="window.close()" class="bg-gray-200 hover:bg-gray-300 text-gray-700 px-5 py-2.5 rounded-xl font-bold text-sm transition-all cursor-pointer">
      Đóng
    </button>
  </div>

</div>
</body>
</html>"""
    return html


# ===================== DÀNH CHO ADMIN / GIÁO VIÊN: IN BÀI THI HỌC SINH =====================

def export_student_exam_print_html(submission: dict, exam_data: dict) -> str:
    """
    Tạo HTML bản in BÀI THI ĐẦY ĐỦ CỦA HỌC SINH dành cho GIÁO VIÊN / ADMIN:
    Gồm toàn bộ đề bài, phương án học sinh chọn, đáp án đúng của đề, ký hiệu Đúng/Sai, điểm số từng câu.
    """
    scores = submission.get("scores", {})
    p1_res = submission.get("part1_result", {})
    p2_res = submission.get("part2_result", {})
    p3_res = submission.get("part3_result", {})
    p4_res = submission.get("part4_result", {})

    submitted_at = submission.get("submitted_at", "")
    if submitted_at:
        try:
            dt = datetime.fromisoformat(submitted_at)
            submitted_at = dt.strftime("%d/%m/%Y %H:%M")
        except:
            pass

    # Chi tiết Phần I
    p1_details_html = ""
    for idx, d in enumerate(p1_res.get("details", []), 1):
        st_ans = d.get("student_answer") or "—"
        cr_ans = d.get("correct_answer") or ""
        is_cor = d.get("is_correct", False)
        pts = d.get("points_earned", 0)
        icon = "✅ ĐÚNG" if is_cor else "❌ SAI"
        row_bg = "bg-green-50/60" if is_cor else "bg-red-50/60"

        opts_html = ""
        raw_opts = d.get("options", {})
        if raw_opts:
            opts_parts = []
            for k in ["A", "B", "C", "D"]:
                val = raw_opts.get(k, "")
                if val:
                    cls = "font-bold text-red-600 underline" if k == cr_ans else "text-gray-700"
                    opts_parts.append(f"<span class='mr-4 {cls}'><b>{k}.</b> {val}</span>")
            opts_html = f"<div class='mt-1 text-xs'>{' '.join(opts_parts)}</div>"

        p1_details_html += f"""
        <div class="p-3 border rounded-xl mb-2 {row_bg}">
          <div class="flex justify-between items-start text-xs sm:text-sm font-semibold">
            <span class="text-gray-900"><b>Câu {idx}:</b> {d.get('text', '')}</span>
            <span class="ml-2 font-bold flex-shrink-0 {'text-green-700' if is_cor else 'text-red-600'}">{icon} (+{pts}đ)</span>
          </div>
          {opts_html}
          <div class="mt-2 text-xs flex gap-4 text-gray-600 border-t pt-1.5 border-gray-200">
            <span>Học sinh chọn: <b class="text-blue-900 font-black">{st_ans}</b></span>
            <span>Đáp án chuẩn: <b class="text-green-700 font-black">{cr_ans}</b></span>
          </div>
        </div>"""

    # Chi tiết Phần II
    p2_details_html = ""
    for idx, q in enumerate(p2_res.get("details", []), 1):
        q_pts = q.get("score", 0)
        items_html = ""
        items_list = q.get("items", [])
        if isinstance(items_list, dict):
            items_list = [{"key": k, **v} for k, v in items_list.items()]
            
        for item in items_list:
            k = item.get("key", "")
            txt = item.get("text", "")
            st_val = item.get("student_answer")
            cr_val = item.get("correct_answer")
            is_cor = item.get("is_correct", False)
            
            st_str = "Đúng" if st_val is True else ("Sai" if st_val is False else "—")
            cr_str = "Đúng" if cr_val is True else "Sai"
            it_bg = "bg-green-50" if is_cor else "bg-red-50"
            it_icon = "✅" if is_cor else "❌"

            items_html += f"""
            <tr class="{it_bg}">
              <td class="border px-2 py-1 text-center font-bold w-8">({k})</td>
              <td class="border px-3 py-1 text-xs sm:text-sm">{txt}</td>
              <td class="border px-2 py-1 text-center font-bold">{st_str}</td>
              <td class="border px-2 py-1 text-center font-bold text-green-700">{cr_str}</td>
              <td class="border px-2 py-1 text-center">{it_icon}</td>
            </tr>"""

        p2_details_html += f"""
        <div class="p-3 border rounded-xl mb-3 bg-white">
          <div class="flex justify-between items-start text-xs sm:text-sm font-semibold mb-2">
            <span class="text-gray-900"><b>Câu {idx}:</b> {q.get('text', '')}</span>
            <span class="font-bold text-purple-900 flex-shrink-0">Điểm: {q_pts}đ ({q.get('correct_count',0)}/4 ý đúng)</span>
          </div>
          <table class="w-full text-xs border-collapse">
            <thead>
              <tr class="bg-gray-100 text-gray-600">
                <th class="border px-2 py-1">Ý</th>
                <th class="border px-3 py-1 text-left">Nội dung mệnh đề</th>
                <th class="border px-2 py-1 w-20">HS chọn</th>
                <th class="border px-2 py-1 w-20 text-green-700">Đ/A chuẩn</th>
                <th class="border px-2 py-1 w-12">KQ</th>
              </tr>
            </thead>
            <tbody>{items_html}</tbody>
          </table>
        </div>"""

    # Chi tiết Phần III
    p3_details_html = ""
    for idx, d in enumerate(p3_res.get("details", []), 1):
        st_ans = d.get("student_answer") or "(bỏ trống)"
        cr_ans = d.get("correct_answer") or ""
        is_cor = d.get("is_correct", False)
        pts = d.get("points_earned", 0)
        icon = "✅ ĐÚNG" if is_cor else "❌ SAI"
        row_bg = "bg-green-50/60" if is_cor else "bg-red-50/60"

        p3_details_html += f"""
        <div class="p-3 border rounded-xl mb-2 {row_bg}">
          <div class="flex justify-between items-start text-xs sm:text-sm font-semibold">
            <span class="text-gray-900"><b>Câu {idx}:</b> {d.get('text', '')}</span>
            <span class="font-bold flex-shrink-0 {'text-green-700' if is_cor else 'text-red-600'}">{icon} (+{pts}đ)</span>
          </div>
          <div class="mt-2 text-xs flex gap-4 text-gray-700 border-t pt-1.5 border-gray-200">
            <span>Học sinh trả lời: <b class="text-blue-900 font-bold">{st_ans}</b></span>
            <span>Đáp án chuẩn: <b class="text-green-700 font-bold">{cr_ans}</b></span>
          </div>
        </div>"""

    # Chi tiết Phần IV
    p4_html = ""
    if p4_res and not p4_res.get("skipped"):
        p4_html = f"""
        <div class="p-4 border rounded-xl bg-orange-50/50 mb-3">
          <div class="flex justify-between items-center mb-2">
            <h3 class="font-bold text-sm text-gray-900">✍️ PHẦN IV: BÀI LÀM TỰ LUẬN</h3>
            <span class="font-black text-orange-700 text-sm">Điểm: {p4_res.get('score', 0)} / 1.0đ</span>
          </div>
          <div class="bg-white p-3 rounded-lg border text-xs sm:text-sm text-gray-800 whitespace-pre-line mb-3">
            <b>Bài làm của học sinh:</b>\n{submission.get('part4_answer', '')}
          </div>
          <div class="text-xs text-gray-600 space-y-1 bg-white p-2.5 rounded-lg border">
            <p><strong>Nhận xét:</strong> {p4_res.get('overall_comment', '')}</p>
            <p><strong>Ưu điểm:</strong> {p4_res.get('strengths', '—')}</p>
            <p><strong>Cần cải thiện:</strong> {p4_res.get('weaknesses', '—')}</p>
          </div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<title>Bản in bài thi học sinh - {submission.get('student_name','')}</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/contrib/auto-render.min.js"></script>
<script>
  document.addEventListener("DOMContentLoaded", function() {{
    if (window.renderMathInElement) {{
      renderMathInElement(document.body, {{
        delimiters: [
          {{left: '$$', right: '$$', display: true}},
          {{left: '$', right: '$', display: false}},
          {{left: '\\\\(', right: '\\\\)', display: false}},
          {{left: '\\\\[', right: '\\\\]', display: true}}
        ],
        throwOnError: false
      }});
    }}
  }});
</script>
<style>
  @media print {{
    .no-print {{ display: none !important; }}
    body {{ background: white !important; padding: 0 !important; font-size: 12px; }}
    .page-break {{ page-break-before: always; }}
  }}
</style>
</head>
<body class="bg-gray-100 p-4 sm:p-6 font-sans">
<div class="max-w-4xl mx-auto bg-white shadow-xl rounded-2xl p-6 sm:p-8 border">
  
  <!-- Header Quốc ngữ / Sở GD -->
  <div class="flex justify-between items-start border-b pb-4 mb-4">
    <div>
      <p class="text-xs font-bold uppercase text-gray-600">SỞ GD&ĐT ... • TRƯỜNG THPT ...</p>
      <h1 class="text-lg sm:text-xl font-black text-gray-900 mt-0.5">{exam_data.get('title','BÀI KIỂM TRA HỌC SINH')}</h1>
      <p class="text-xs text-gray-500">Môn: {exam_data.get('subject','Toán')} | Lớp: {exam_data.get('grade','12')} | Mã nộp bài: <b>{submission.get('submission_id','')}</b></p>
    </div>
    <div class="text-right border-2 border-blue-900 rounded-xl px-4 py-2 bg-blue-50/50">
      <span class="text-xs font-bold text-gray-500 block uppercase">ĐIỂM TỔNG KẾT</span>
      <span class="text-4xl font-black text-blue-900">{scores.get('total_score', 0)}</span>
      <span class="text-sm font-bold text-gray-600">/ 10</span>
      <span class="block text-xs font-bold text-blue-700">{scores.get('rank','')}</span>
    </div>
  </div>

  <!-- Thông tin học sinh & Thống kê điểm -->
  <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 bg-gray-50 p-3.5 rounded-xl border text-xs mb-5">
    <div><span>Học sinh:</span> <strong class="text-sm block text-gray-900">{submission.get('student_name','')}</strong></div>
    <div><span>Lớp:</span> <strong class="text-sm block text-gray-900">{submission.get('student_class','')}</strong></div>
    <div><span>Thời gian nộp:</span> <strong class="block text-gray-800">{submitted_at}</strong></div>
    <div><span>Điểm từng phần:</span> <strong class="block text-indigo-900">P1: {scores.get('part1_score',0)}đ · P2: {scores.get('part2_score',0)}đ · P3: {scores.get('part3_score',0)}đ</strong></div>
  </div>

  <!-- PHẦN I -->
  <div class="mb-5">
    <h2 class="text-sm font-black uppercase text-indigo-900 mb-2 border-b-2 border-indigo-200 pb-1">
      PHẦN I: TRẮC NGHIỆM NHIỀU LỰA CHỌN ({p1_res.get('correct_count',0)}/{p1_res.get('total_count',0)} câu đúng • Điểm: {scores.get('part1_score',0)}/5.0đ)
    </h2>
    {p1_details_html}
  </div>

  <!-- PHẦN II -->
  <div class="mb-5">
    <h2 class="text-sm font-black uppercase text-purple-900 mb-2 border-b-2 border-purple-200 pb-1">
      PHẦN II: TRẮC NGHIỆM ĐÚNG / SAI (Điểm: {scores.get('part2_score',0)}/4.0đ)
    </h2>
    {p2_details_html}
  </div>

  <!-- PHẦN III -->
  <div class="mb-5">
    <h2 class="text-sm font-black uppercase text-teal-900 mb-2 border-b-2 border-teal-200 pb-1">
      PHẦN III: TRẢ LỜI NGẮN ({p3_res.get('correct_count',0)}/{p3_res.get('total_count',0)} câu đúng • Điểm: {scores.get('part3_score',0)}/1.0đ)
    </h2>
    {p3_details_html}
  </div>

  <!-- PHẦN IV -->
  {p4_html}

  <!-- Chữ ký giám khảo -->
  <div class="grid grid-cols-2 text-center text-xs mt-6 pt-4 border-t">
    <div>
      <p class="font-bold text-gray-700 uppercase">GIÁM KHẢO 1</p>
      <div class="h-16"></div>
      <p class="font-semibold text-gray-800">................................................</p>
    </div>
    <div>
      <p class="font-bold text-gray-700 uppercase">GIÁM KHẢO 2</p>
      <div class="h-16"></div>
      <p class="font-semibold text-gray-800">................................................</p>
    </div>
  </div>

  <!-- Thanh nút bấm in -->
  <div class="mt-6 pt-4 border-t text-center no-print flex justify-center gap-3">
    <button onclick="window.print()" class="bg-blue-900 hover:bg-blue-800 text-white font-bold px-6 py-2.5 rounded-xl shadow-md cursor-pointer flex items-center gap-1.5 text-sm">
      <span>🖨️</span><span>In bài thi học sinh (A4)</span>
    </button>
    <button onclick="window.close()" class="bg-gray-200 hover:bg-gray-300 text-gray-700 font-bold px-5 py-2.5 rounded-xl text-sm cursor-pointer">
      Đóng
    </button>
  </div>

</div>
</body>
</html>"""
    return html


# ===================== DÀNH CHO ADMIN / GIÁO VIÊN: IN ĐỀ THI GỐC (CHO HỌC SINH LÀM) =====================

def export_clean_exam_print_html(exam_data: dict) -> str:
    """
    Tạo bản in ĐỀ THI SẠCH (không chứa đáp án đỏ hay chữ Đúng/Sai) để in ra giấy/photocopy phát cho học sinh làm bài.
    """
    title = exam_data.get("title", "ĐỀ KIỂM TRA ĐỊNH KỲ")
    subject = exam_data.get("subject", "Toán học")
    grade = exam_data.get("grade", "12")
    duration = exam_data.get("duration_minutes", 50)
    exam_id = exam_data.get("id", "101")

    parts = exam_data.get("parts", {})

    # 1. Phần I
    p1_html = ""
    p1_qs = parts.get("part1", {}).get("questions", [])
    for idx, q in enumerate(p1_qs, 1):
        opts = q.get("options", {})
        opts_rendered = []
        for k in ["A", "B", "C", "D"]:
            v = opts.get(k, "")
            if v:
                opts_rendered.append(f"<span class='mr-6'><b>{k}.</b> {v}</span>")
        opts_block = f"<div class='mt-1 text-sm pl-4 flex flex-wrap gap-y-1'>{' '.join(opts_rendered)}</div>"

        p1_html += f"""
        <div class="mb-3 text-sm">
          <p class="font-medium text-gray-900"><b>Câu {idx}:</b> {q.get('text', '')}</p>
          {opts_block}
        </div>"""

    # 2. Phần II
    p2_html = ""
    p2_qs = parts.get("part2", {}).get("questions", [])
    for idx, q in enumerate(p2_qs, 1):
        items = q.get("items", {})
        items_list = []
        if isinstance(items, list):
            items_list = items
        elif isinstance(items, dict):
            for k in ["a", "b", "c", "d"]:
                if k in items:
                    v = items[k]
                    txt = v.get("text", "") if isinstance(v, dict) else str(v)
                    items_list.append({"key": k, "text": txt})
                    
        items_rendered = "".join([f"<p class='pl-4 text-sm mt-0.5'><b>{it.get('key')})</b> {it.get('text')}</p>" for it in items_list])

        p2_html += f"""
        <div class="mb-3 text-sm">
          <p class="font-medium text-gray-900"><b>Câu {idx}:</b> {q.get('text', '')}</p>
          {items_rendered}
        </div>"""

    # 3. Phần III
    p3_html = ""
    p3_qs = parts.get("part3", {}).get("questions", [])
    for idx, q in enumerate(p3_qs, 1):
        p3_html += f"""
        <div class="mb-3 text-sm flex items-start justify-between gap-3">
          <p class="font-medium text-gray-900 flex-1"><b>Câu {idx}:</b> {q.get('text', '')}</p>
          <span class="border-b border-dotted border-gray-500 w-32 text-center text-xs text-gray-400 pb-0.5 flex-shrink-0">Đáp số: .................</span>
        </div>"""

    # 4. Phần IV
    p4_html = ""
    p4_qs = parts.get("part4", {}).get("questions", [])
    if p4_qs:
        p4_rendered = ""
        for idx, q in enumerate(p4_qs, 1):
            p4_rendered += f"""
            <div class="mb-2 text-sm">
              <p class="font-medium text-gray-900"><b>Câu {idx}:</b> {q.get('text', '')}</p>
            </div>"""
        p4_html = f"""
        <div class="mt-4 pt-3 border-t">
          <h2 class="font-bold text-sm uppercase text-gray-900 mb-2">PHẦN IV: TỰ LUẬN (1,0 điểm)</h2>
          {p4_rendered}
          <div class="h-44 border border-dashed border-gray-300 rounded-xl mt-2 p-3 text-xs text-gray-400">
            (Thí sinh làm bài tự luận vào khung này hoặc giấy thi riêng)
          </div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<title>Đề thi - {title}</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/contrib/auto-render.min.js"></script>
<script>
  document.addEventListener("DOMContentLoaded", function() {{
    if (window.renderMathInElement) {{
      renderMathInElement(document.body, {{
        delimiters: [
          {{left: '$$', right: '$$', display: true}},
          {{left: '$', right: '$', display: false}},
          {{left: '\\\\(', right: '\\\\)', display: false}},
          {{left: '\\\\[', right: '\\\\]', display: true}}
        ],
        throwOnError: false
      }});
    }}
  }});
</script>
<style>
  @media print {{
    .no-print {{ display: none !important; }}
    body {{ background: white !important; padding: 0 !important; font-size: 13px; line-height: 1.45; }}
  }}
</style>
</head>
<body class="bg-gray-100 p-4 sm:p-6 font-sans">
<div class="max-w-4xl mx-auto bg-white shadow-xl rounded-2xl p-6 sm:p-8 border">
  
  <!-- Header Đề thi chuẩn -->
  <div class="grid grid-cols-12 gap-3 border-b-2 border-black pb-3 mb-4">
    <div class="col-span-7 text-center border-r pr-3">
      <p class="text-xs uppercase font-bold tracking-wider">SỞ GIÁO DỤC VÀ ĐÀO TẠO • TRƯỜNG THPT</p>
      <h1 class="text-base sm:text-lg font-black uppercase mt-0.5">{title}</h1>
      <p class="text-xs font-semibold">Môn: {subject} — Lớp: {grade}</p>
      <p class="text-xs italic text-gray-600">Thời gian làm bài: {duration} phút (không kể thời gian phát đề)</p>
    </div>
    <div class="col-span-5 pl-2 text-xs space-y-1.5 flex flex-col justify-center">
      <div>Họ và tên thí sinh: ....................................................</div>
      <div>Số báo danh: ....................... Phòng thi: ....................</div>
      <div class="font-bold flex justify-between items-center pt-1 border-t">
        <span>MÃ ĐỀ THI: {exam_id[:6].upper()}</span>
        <span class="text-[11px] font-normal italic text-gray-500">(Đề thi gồm {len(p1_qs) + len(p2_qs) + len(p3_qs)} câu)</span>
      </div>
    </div>
  </div>

  <!-- PHẦN I -->
  <div class="mb-5">
    <h2 class="font-bold text-sm uppercase text-gray-900 mb-2.5 pb-1 border-b">
      PHẦN I (5,0 điểm). Thí sinh trả lời từ câu 1 đến câu {len(p1_qs)}. Mỗi câu hỏi thí sinh chỉ chọn một phương án.
    </h2>
    {p1_html}
  </div>

  <!-- PHẦN II -->
  <div class="mb-5">
    <h2 class="font-bold text-sm uppercase text-gray-900 mb-2.5 pb-1 border-b">
      PHẦN II (4,0 điểm). Thí sinh trả lời từ câu 1 đến câu {len(p2_qs)}. Trong mỗi ý a), b), c), d) ở mỗi câu, thí sinh chọn Đúng hoặc Sai.
    </h2>
    {p2_html}
  </div>

  <!-- PHẦN III -->
  <div class="mb-5">
    <h2 class="font-bold text-sm uppercase text-gray-900 mb-2.5 pb-1 border-b">
      PHẦN III (1,0 điểm). Thí sinh trả lời từ câu 1 đến câu {len(p3_qs)}. Thí sinh ghi đáp số ngắn vào ô tương ứng.
    </h2>
    {p3_html}
  </div>

  <!-- PHẦN IV -->
  {p4_html}

  <p class="text-center font-bold text-xs uppercase tracking-widest mt-6 pt-3 border-t">
    ——— HẾT ———<br>
    <span class="text-[11px] font-normal italic text-gray-500">Cán bộ coi thi không giải thích gì thêm.</span>
  </p>

  <!-- Nút In -->
  <div class="mt-6 pt-4 border-t text-center no-print flex justify-center gap-3">
    <button onclick="window.print()" class="bg-blue-900 hover:bg-blue-800 text-white font-bold px-6 py-2.5 rounded-xl shadow-md cursor-pointer flex items-center gap-1.5 text-sm">
      <span>📄</span><span>In đề thi giấy (A4)</span>
    </button>
    <button onclick="window.close()" class="bg-gray-200 hover:bg-gray-300 text-gray-700 font-bold px-5 py-2.5 rounded-xl text-sm cursor-pointer">
      Đóng
    </button>
  </div>

</div>
</body>
</html>"""
    return html


# ===================== DÀNH CHO ADMIN / GIÁO VIÊN: IN ĐÁP ÁN GỐC & MA TRẬN CHẤM =====================

def export_exam_answers_print_html(exam_data: dict) -> str:
    """
    Tạo bản in ĐÁP ÁN GỐC & HƯỚNG DẪN CHẤM BÀI:
    Gồm ma trận đáp án Phần I, Phần II, Phần III, thang điểm và toàn bộ đề có đáp án đúng tô đỏ.
    """
    title = exam_data.get("title", "ĐỀ KIỂM TRA ĐỊNH KỲ")
    subject = exam_data.get("subject", "Toán học")
    grade = exam_data.get("grade", "12")
    exam_id = exam_data.get("id", "101")
    parts = exam_data.get("parts", {})

    p1_qs = parts.get("part1", {}).get("questions", [])
    p2_qs = parts.get("part2", {}).get("questions", [])
    p3_qs = parts.get("part3", {}).get("questions", [])

    # Ma trận Phần I: Bảng 12 câu
    p1_table_cells = ""
    for idx, q in enumerate(p1_qs, 1):
        ans = q.get("answer", "")
        p1_table_cells += f"""
        <div class="border text-center p-2 rounded-lg bg-indigo-50/60">
          <span class="text-xs text-gray-500 font-medium block">Câu {idx}</span>
          <span class="text-lg font-black text-red-600">{ans}</span>
        </div>"""

    # Ma trận Phần II: Bảng Đúng/Sai
    p2_rows = ""
    for idx, q in enumerate(p2_qs, 1):
        items = q.get("items", {})
        if isinstance(items, list):
            items_dict = {it.get("key", ""): it.get("answer") for it in items}
        elif isinstance(items, dict):
            items_dict = {k: (v.get("answer") if isinstance(v, dict) else v) for k, v in items.items()}
        else:
            items_dict = {}

        def _fmt_ds(val):
            if val is True:
                return "<span class='text-red-600 font-black'>Đ</span>"
            elif val is False:
                return "<span class='text-gray-800 font-bold'>S</span>"
            return "—"

        p2_rows += f"""
        <tr>
          <td class="border px-3 py-2 text-center font-bold">Câu {idx}</td>
          <td class="border px-3 py-2 text-center text-sm">{_fmt_ds(items_dict.get('a'))}</td>
          <td class="border px-3 py-2 text-center text-sm">{_fmt_ds(items_dict.get('b'))}</td>
          <td class="border px-3 py-2 text-center text-sm">{_fmt_ds(items_dict.get('c'))}</td>
          <td class="border px-3 py-2 text-center text-sm">{_fmt_ds(items_dict.get('d'))}</td>
          <td class="border px-3 py-2 text-center text-xs text-gray-500">1 ý: 0.1đ · 2 ý: 0.25đ · 3 ý: 0.5đ · 4 ý: 1.0đ</td>
        </tr>"""

    # Ma trận Phần III: Bảng Trả lời ngắn
    p3_table_cells = ""
    for idx, q in enumerate(p3_qs, 1):
        ans = q.get("answer", "")
        p3_table_cells += f"""
        <div class="border text-center p-2 rounded-lg bg-teal-50/60">
          <span class="text-xs text-gray-500 font-medium block">Câu {idx}</span>
          <span class="text-base font-black text-teal-900">{ans}</span>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<title>Đáp án gốc & Hướng dẫn chấm - {title}</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/contrib/auto-render.min.js"></script>
<script>
  document.addEventListener("DOMContentLoaded", function() {{
    if (window.renderMathInElement) {{
      renderMathInElement(document.body, {{
        delimiters: [
          {{left: '$$', right: '$$', display: true}},
          {{left: '$', right: '$', display: false}},
          {{left: '\\\\(', right: '\\\\)', display: false}},
          {{left: '\\\\[', right: '\\\\]', display: true}}
        ],
        throwOnError: false
      }});
    }}
  }});
</script>
<style>
  @media print {{
    .no-print {{ display: none !important; }}
    body {{ background: white !important; padding: 0 !important; font-size: 13px; }}
  }}
</style>
</head>
<body class="bg-gray-100 p-4 sm:p-6 font-sans">
<div class="max-w-4xl mx-auto bg-white shadow-xl rounded-2xl p-6 sm:p-8 border">
  
  <!-- Header -->
  <div class="text-center border-b-2 border-black pb-4 mb-5">
    <p class="text-xs uppercase font-bold tracking-widest text-gray-600">SỞ GIÁO DỤC VÀ ĐÀO TẠO • TRƯỜNG THPT</p>
    <h1 class="text-lg sm:text-2xl font-black uppercase text-red-700 mt-1">ĐÁP ÁN GỐC & HƯỚNG DẪN CHẤM BÀI</h1>
    <p class="text-sm font-semibold text-gray-800">{title} — Môn: {subject} — Khối: {grade}</p>
    <p class="text-xs text-gray-500 mt-0.5">Mã đề: <b>{exam_id[:6].upper()}</b> • Chuẩn Bộ Giáo Dục và Đào Tạo 2025</p>
  </div>

  <!-- MA TRẬN ĐÁP ÁN PHẦN I -->
  <div class="mb-6">
    <h2 class="text-sm font-black uppercase text-indigo-900 mb-2 border-b pb-1 flex justify-between items-center">
      <span>1. BẢNG ĐÁP ÁN PHẦN I: TRẮC NGHIỆM 4 LỰA CHỌN (5,0 ĐIỂM)</span>
      <span class="text-xs text-gray-500 font-normal">Mỗi câu đúng: 0.25 điểm (đối với đề 20 câu) hoặc chia đều thang điểm</span>
    </h2>
    <div class="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-12 gap-2">
      {p1_table_cells}
    </div>
  </div>

  <!-- MA TRẬN ĐÁP ÁN PHẦN II -->
  <div class="mb-6">
    <h2 class="text-sm font-black uppercase text-purple-900 mb-2 border-b pb-1 flex justify-between items-center">
      <span>2. BẢNG ĐÁP ÁN PHẦN II: TRẮC NGHIỆM ĐÚNG / SAI (4,0 ĐIỂM)</span>
      <span class="text-xs text-red-600 font-bold">Ký hiệu: Đ = Đúng, S = Sai</span>
    </h2>
    <table class="w-full text-sm border-collapse border border-gray-300">
      <thead class="bg-purple-900 text-white text-xs">
        <tr>
          <th class="border border-gray-300 px-3 py-2 w-20">Câu hỏi</th>
          <th class="border border-gray-300 px-3 py-2 w-24">Ý (a)</th>
          <th class="border border-gray-300 px-3 py-2 w-24">Ý (b)</th>
          <th class="border border-gray-300 px-3 py-2 w-24">Ý (c)</th>
          <th class="border border-gray-300 px-3 py-2 w-24">Ý (d)</th>
          <th class="border border-gray-300 px-3 py-2 text-left">Quy định tính điểm</th>
        </tr>
      </thead>
      <tbody>
        {p2_rows}
      </tbody>
    </table>
  </div>

  <!-- MA TRẬN ĐÁP ÁN PHẦN III -->
  <div class="mb-6">
    <h2 class="text-sm font-black uppercase text-teal-900 mb-2 border-b pb-1 flex justify-between items-center">
      <span>3. BẢNG ĐÁP ÁN PHẦN III: TRẢ LỜI NGẮN (1,0 ĐIỂM)</span>
      <span class="text-xs text-gray-500 font-normal">Chỉ chấp nhận đáp số chính xác</span>
    </h2>
    <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2">
      {p3_table_cells}
    </div>
  </div>

  <!-- Nút In -->
  <div class="mt-8 pt-4 border-t text-center no-print flex justify-center gap-3">
    <button onclick="window.print()" class="bg-red-700 hover:bg-red-800 text-white font-bold px-6 py-2.5 rounded-xl shadow-md cursor-pointer flex items-center gap-1.5 text-sm">
      <span>🔑</span><span>In đáp án gốc & hướng dẫn chấm (A4)</span>
    </button>
    <button onclick="window.close()" class="bg-gray-200 hover:bg-gray-300 text-gray-700 font-bold px-5 py-2.5 rounded-xl text-sm cursor-pointer">
      Đóng
    </button>
  </div>

</div>
</body>
</html>"""
    return html

