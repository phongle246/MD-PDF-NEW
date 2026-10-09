---
name: pdf-md-orchestrator
description: Điều phối thay đổi lớn cho app textbook PDF → Markdown (engine Python + UI React/Tauri): phân việc cho pipeline-engineer, ui-builder, fidelity-reviewer. Dùng khi người dùng yêu cầu thêm tính năng, sửa lỗi chuyển đổi, chạy lại, cập nhật hoặc bổ sung pipeline.
---

# Điều phối

**Chế độ thực thi:** subagent uỷ quyền (kết quả một lần, không cần hội thoại qua lại).

0. Kiểm tra bối cảnh: đọc `docs/` và `engine/tests`; chạy `pytest engine/tests` để biết nền.
1. Phân loại yêu cầu: engine → `pipeline-engineer`; UI → `ui-builder`; song song nếu độc lập (một message, nhiều lệnh Agent).
2. Sau mỗi module xong: `fidelity-reviewer` kiểm tra bằng PDF fixture (`engine/tests/fixtures.py`).
3. Chạy lại `pytest` và `npm run build` ở `app/`. Chỉ báo xong khi cả hai xanh.

# Lỗi
Thử lại một lần; nếu vẫn lỗi, báo rõ phần thiếu, không đoán.

# Kịch bản kiểm thử
- Thường: thêm pass → test mới xanh → reviewer không CRITICAL.
- Lỗi: test fixture 2 cột hỏng thứ tự → engineer sửa → chạy lại.
