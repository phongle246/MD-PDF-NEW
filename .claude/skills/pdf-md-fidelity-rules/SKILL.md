---
name: pdf-md-fidelity-rules
description: Quy tắc fidelity bắt buộc khi sửa pipeline PDF→Markdown (không rewrite, không đoán, số liệu, provenance, tách nội dung AI). Dùng mỗi khi thay đổi engine/mdkb hoặc đánh giá output.
---

# Quy tắc fidelity

1. **Không rewrite**: không tóm tắt, diễn giải, dịch, sửa typo nội dung sách. Chỉ cleanup xác định (nối dòng, bỏ header/footer lặp, ligature, dehyphenation chắc chắn). Lý do: Markdown là bản nguồn chuẩn cho dịch sau này.
2. **Dehyphenation an toàn**: chỉ nối `pedi-\natric` khi vế ghép thuộc từ vựng đã thấy trong sách hoặc không có tiền tố/hậu tố hyphen hợp lệ; `long-term` giữ nguyên.
3. **Không đoán**: text đọc không được → `<!-- UNCERTAIN: ... page N -->`. OCR số/liều/Hy Lạp thấp tin cậy → flag.
4. **Số liệu**: mọi token số trong nguồn phải xuất hiện trong Markdown; lệch → issue, không tự sửa.
5. **Provenance**: mỗi block có page + bbox; Markdown có `<!-- source_page: N -->`.
6. **Tách AI**: nội dung AI chỉ ở metadata/index, có nhãn "Generated navigation index — not part of the original textbook."
7. **PDF gốc read-only**; ghi SHA-256.

Thêm test cho mỗi quy tắc bị chạm tới.
