# Kế hoạch Backend và phần việc còn lại

> Cập nhật: 2026-08-12  
> Phạm vi đối chiếu: mã nguồn hiện có trong `src/`, `requirements.txt`, `Dockerfile`,
> `render.yaml` và frontend hiện tại. Đây là trạng thái thực tế của repository, không phải
> kế hoạch kiến trúc cũ.

## 1. Cách chạy hiện tại

Backend dùng FastAPI, LangGraph, SQLAlchemy async và PostgreSQL. Tạo `.env` từ `.env.example`,
điền `DATABASE_URL`, `SECRET_KEY` và API key của provider LLM, sau đó:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/run_dev.py
```

Backend chạy ở `http://localhost:8000`; kiểm tra `/health` và `/docs`.

`requirements.txt` hiện chỉ chứa dependency runtime của backend. Dependency test/lint đã không
còn trong bản repository tối giản; nếu khôi phục test sau này nên tạo riêng `requirements-dev.txt`.

Trên macOS/Linux có thể chạy:

```bash
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

PostgreSQL là bắt buộc. Repository không có SQLite fallback và không có Alembic migration; khi
khởi động, `src/db/session.py` dùng `Base.metadata.create_all()` để tạo bảng còn thiếu.

## 2. Trạng thái thực tế

### Kết quả kiểm tra ngày 2026-08-12

- Python syntax check (`compileall`) đã pass.
- Backend chưa được smoke test end-to-end trong môi trường kiểm tra này vì virtualenv chưa có
  dependency và việc cài `requirements.txt` bị timeout; PostgreSQL/`.env` cũng chưa được cấu hình.
- Frontend build đã pass sau `npm ci` và `npm run build`.

| Phần | Trạng thái | Bằng chứng trong code |
|---|---|---|
| FastAPI app và health endpoint | Đã có | `src/main.py` |
| PostgreSQL async | Đã có | `src/db/session.py`, `src/db/models.py` |
| Đăng ký, đăng nhập, JWT, bcrypt | Đã có | `src/api/auth_routes.py`, `src/auth/` |
| Google ID-token login | Đã có, cần cấu hình OAuth | `src/auth/google_oauth.py` |
| Profile và đổi mật khẩu | Đã có | `/auth/me`, `/auth/me/password` |
| Role user/admin, khóa tài khoản | Đã có | `src/auth/dependencies.py`, `admin_routes.py` |
| Chat 1-1 và nhóm | Đã có | `src/api/chat_routes.py`, `chat_service.py` |
| Lịch sử tin nhắn, unread/read | Đã có | `chat_routes.py`, `chat_service.py` |
| AI permission theo conversation | Đã có | `AIPermission`, các route permission |
| WebSocket realtime | Đã có ở một process | `src/websocket/manager.py`, `routes.py` |
| Agent LangGraph và PostgreSQL checkpointer | Đã có | `src/agents/graph.py`, `planner_node.py` |
| Human-in-the-loop | Đã có | `/chat` và `/chat/resume` |
| Gemini/Groq/OpenAI provider | Đã có | `src/services/llm.py`, `.env.example` |
| Tóm tắt và trích xuất task | Đã có | `agents/tools/summarize_tool.py`, `task_tool.py` |
| Task CRUD và AI suggestion | Đã có | `src/api/task_routes.py`, `proactive_service.py` |
| Reminder và scheduler | Đã có | `reminder_routes.py`, `reminder_service.py`, APScheduler |
| Memory CRUD | Đã có | `src/api/memory_routes.py` |
| Google Calendar per-user OAuth | Đã có, cần cấu hình OAuth | `calendar_routes.py`, `google_credentials.py` |
| Calendar polling đồng bộ thay đổi | Đã có | `calendar_service.py`, scheduler |
| Admin dashboard data | Đã có | `src/api/admin_routes.py` |
| Token usage và budget alert | Đã có | `usage_service.py`, WebSocket event |
| Vector/RAG search | Chưa hoàn thiện | `agents/tools/example_tool.py` là stub; search hiện là text search |
| Redis/Celery worker | Chưa có | Không có package hoặc module tương ứng |
| Alembic migration | Chưa có | Schema hiện dùng `create_all()` |
| Audit log đầy đủ | Chưa có | Chỉ có `UsageLog`; chưa có audit domain riêng |
| Test suite trong repository | Đã bị loại khỏi bản runtime tối giản | Không còn thư mục `tests/` |

## 3. Những điểm khác so với kế hoạch cũ

Kế hoạch cũ mô tả một hệ thống lớn hơn code hiện tại. Các điều chỉnh bắt buộc:

1. Không còn hai frontend `Frontend/user` và `Frontend/admin`. Admin hiện nằm trong cùng một app
   React ở `Frontend/`, được bảo vệ bởi `AdminRoute`.
2. Không dùng `pgvector`, Redis hoặc Celery. Agent memory dùng PostgreSQL checkpointer; scheduler
   dùng APScheduler trong process; WebSocket dùng connection manager trong process.
3. Không có Alembic, migration versioning, request ID contract hoặc hệ thống encryption toàn bộ
   nội dung như kế hoạch cũ. Chỉ credential Google được mã hóa bằng Fernet; mật khẩu được hash
   bằng bcrypt.
4. Calendar Google đã được làm sớm và đang là calendar thật theo từng user; không có calendar
   nội bộ riêng.
5. Google OAuth login đã có trong code dù kế hoạch cũ để ngoài MVP; tính năng chỉ hoạt động khi
   điền `GOOGLE_OAUTH_CLIENT_ID`.
6. Proactive suggestion, priority task inbox và cảnh báo token đã có trong code; chúng không còn
   là phần việc chưa bắt đầu.
7. Test, CI workflow và các file ghi log đã được loại khỏi repository tối giản. Vì vậy chưa thể
   gọi là đã nghiệm thu tự động; cần kiểm tra thủ công hoặc khôi phục test ở một nhánh phát triển.

## 4. Kế hoạch tiếp theo theo thứ tự ưu tiên

### Bước A — Xác nhận môi trường local

- [ ] Tạo PostgreSQL database và cấu hình `DATABASE_URL`.
- [ ] Chạy backend bằng `python scripts/run_dev.py`.
- [ ] Xác nhận `/health` trả về `status: ok` và `/docs` mở được.
- [ ] Kiểm tra startup tạo đủ các bảng cho database mới.
- [ ] Chạy frontend theo `docs/FRONTEND_IMPLEMENTATION_PLAN.md` và thử register/login.

### Bước B — Kiểm tra luồng MVP đang có

- [ ] Register/login/logout và profile.
- [ ] Tạo chat 1-1, chat nhóm, gửi message, đọc lại lịch sử.
- [ ] Grant/revoke AI permission rồi thử summarize/extract task.
- [ ] Tạo task, đổi trạng thái, tạo reminder và nhận notification.
- [ ] Tạo/sửa/xóa memory.
- [ ] Dùng `/assistant` và thử flow confirm/cancel của agent.
- [ ] Với admin: kiểm tra users, conversations, tasks, reminders và memories.

### Bước C — Hoàn thiện phần còn thiếu

- [ ] Thay `search_knowledge` stub bằng search thật nếu cần RAG; hiện không được coi là tính năng
  đã hoàn thành.
- [ ] Thêm migration tool (Alembic hoặc migration SQL) trước khi thay đổi schema trên production.
- [ ] Tách realtime và job scheduler khỏi process nếu chạy nhiều instance backend.
- [ ] Thêm audit log cho thao tác admin và các action nhạy cảm.
- [ ] Thêm lại test backend tối thiểu cho auth, chat, task, reminder, calendar và agent.
- [ ] Thêm rate limit, structured error handling và kiểm tra secret/config production.

### Bước D — Deploy

`Dockerfile` và `render.yaml` đang có thể dùng làm cơ sở deploy backend. Trước khi deploy thật,
cần chuẩn bị PostgreSQL production, secret, CORS, frontend origin và kiểm tra `/health`. Các
workflow GitHub Actions đã bị loại khỏi bản tối giản nên deploy hiện không tự động qua CI.

## 5. Tiêu chí hoàn thành phiên bản hiện tại

- Backend khởi động được với PostgreSQL mới và không cần file ngoài repository.
- Frontend gọi được toàn bộ API chính ở `http://localhost:8000/api/v1`.
- User có thể đăng ký, đăng nhập, chat, dùng task/reminder/memory và assistant.
- Admin route bị chặn với user thường.
- Google Calendar và Google login được kiểm tra riêng sau khi cấu hình OAuth.
- Các phần chưa có ở mục 4 được ghi rõ, không đánh dấu hoàn thành chỉ vì endpoint tồn tại.
