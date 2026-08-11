Tổng quan dự án

Orbit là một AI agent nhúng trong ứng dụng chat, giúp:

Tóm tắt hội thoại
Trích xuất công việc / lịch hẹn từ tin nhắn
Tạo nhắc nhở (luôn có bước xác nhận trước khi thực hiện — human-in-the-loop)
Quản lý lịch cá nhân (tích hợp Google Calendar)

Repo là monorepo gồm 2 phần độc lập:

Backend: FastAPI + LangGraph, thư mục src/
Frontend: React + Vite, thư mục Frontend/
Trạng thái hiện tại — QUAN TRỌNG, đọc trước khi sửa bất kỳ tính năng nào

Trước khi code, luôn xác nhận tính năng đang chạm vào thuộc nhóm nào bên dưới, vì cách xử lý rất khác nhau.

Đã hoạt động thật (có backend + database, KHÔNG được thay bằng mock)
Đăng ký / Đăng nhập / Đăng xuất — bcrypt hash password, JWT auth, role user/admin. Các route /assistant, /chat, /tasks, ... được bảo vệ bởi ProtectedRoute; /admin/* thêm AdminRoute (FE) + require_admin (BE).
Đăng nhập bằng Google — nút "Sign in with Google" trên /login và /register (component GoogleLogin từ @react-oauth/google), backend xác minh ID token qua POST /api/v1/auth/google (src/auth/google_oauth.py), find-or-create tài khoản qua bảng google_identities riêng (không đụng bảng users) — chỉ tự động link vào tài khoản mật khẩu có sẵn khi Google xác nhận email_verified=true. Cần tự tạo Google OAuth Client ID loại "Web application" và điền GOOGLE_OAUTH_CLIENT_ID (.env) + VITE_GOOGLE_CLIENT_ID (Frontend/.env) mới dùng được — xem .env.example.
Nhắn tin 1-1 và nhóm real-time qua WebSocket, lịch sử tin nhắn, đếm tin nhắn chưa đọc.
AI Agent chat: endpoint POST /api/v1/chat + /chat/resume, dùng LangGraph, 9 tool (thêm search_messages — tìm tin nhắn cũ trong hội thoại theo từ khoá, đọc-only) — 4 tool có tác dụng phụ (create/update/delete_calendar_event, create_reminder) BẮT BUỘC đi qua interrupt() chờ người dùng xác nhận.
Tasks, Calendar (Google Calendar 2 chiều, per-user — mỗi user tự "Connect Google Calendar" nối đúng calendar của họ, không còn dùng chung 1 tài khoản), Reminders (bền vững qua restart), Memory, Profile, AI Assistant (/assistant), Admin dashboard — tất cả đã nối API thật.
Proactive detection: mỗi tin nhắn mới được lọc regex rồi hỏi LLM, tự tạo Task gợi ý và đẩy WebSocket.
Theo dõi + chặn token: bảng usage_logs; ngay khi vượt 80%/100% DAILY_TOKEN_BUDGET, usage_service._maybe_alert_budget đẩy WebSocket usage_budget_alert tới mọi admin đang online (không chỉ khi mở /admin, hiện qua BudgetAlertToast.jsx ở bất kỳ trang nào); is_over_budget() chặn hẳn cuộc gọi LLM mới (/chat, proactive detection) khi đã chạm ngân sách — /chat/resume được miễn trừ để không treo interrupt() dở dang.
AI đọc hội thoại chỉ khi được cấp quyền: bảng ai_permissions (conversation_id, user_id, granted), mặc định chưa cấp quyền, POST /api/v1/chat từ chối (403) nếu chưa được người dùng đó cấp; toggle Grant/Revoke Permission trong AIPanel.jsx gọi GET/PUT /conversations/{id}/ai-permission thật.

Đã có nhưng chưa hoàn chỉnh
Chưa deploy online, chưa có rate limiting. Xem ROADMAP.md.

➡️ KHÔNG còn trang nào dùng Frontend/src/data/mockData.js (file vẫn tồn tại nhưng không được import ở đâu). Khi được yêu cầu "sửa lỗi trang Tasks" hay tương tự, đây là bug logic thật, không phải việc nối API. Trạng thái chi tiết theo từng yêu cầu đề bài: docs/PRD.md và ROADMAP.md.

Kiến trúc thư mục
├── src/                    # Backend — FastAPI + LangGraph
│   ├── agents/             # Agent LangGraph (planner, tools, state)
│   ├── api/                # REST routes: auth, chat (người-với-người), agent chat
│   ├── auth/                # Hash mật khẩu, tạo/kiểm tra JWT
│   ├── db/                  # SQLAlchemy models + session (PostgreSQL)
│   ├── models/               # Pydantic schemas
│   ├── services/              # chat_service, scheduler, llm, calendar_service, reminder_service, proactive_service, usage_service
│   ├── websocket/              # Kênh real-time cho chat
│   └── main.py                 # Điểm khởi tạo FastAPI app
├── tests/                    # pytest cho backend
└── Frontend/                   # Frontend — React + Vite
    └── src/
        ├── api/                  # Gọi REST API + WebSocket client
        ├── context/               # AuthContext (JWT, user hiện tại)
        ├── hooks/                  # useConversations, useMessages
        ├── components/              # Component theo tính năng (chat, layout, ...)
        ├── pages/                    # Các trang ứng dụng
        └── router/                    # React Router + ProtectedRoute
Công nghệ sử dụng
Layer	Công nghệ
AI Agent	LangGraph + LangChain (Google Gemini, Groq, hoặc OpenAI, đổi qua LLM_PROVIDER trong .env)
Backend	FastAPI, SQLAlchemy (async) + PostgreSQL, JWT (PyJWT) + bcrypt, WebSocket
Frontend	React 18, Vite, React Router, React Hook Form, Bootstrap 5, Framer Motion
Test	pytest, pytest-asyncio, httpx
Lint	ruff
Lệnh chạy dự án

Luôn cần chạy song song 2 server khi phát triển/test thủ công: backend (cổng 8000) và frontend (cổng 5173). Backend không tự chạy nền — tắt terminal là tắt server.

Backend
bash
# Lần đầu setup
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env             # điền GOOGLE_API_KEY (hoặc GROQ_API_KEY + LLM_PROVIDER=groq, hoặc OPENAI_API_KEY + LLM_PROVIDER=openai nếu Gemini hết quota) nếu cần AI chat

# Chạy dev server
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
# hoặc nếu có make: make run
# Windows: LUÔN dùng `python scripts/run_dev.py` thay vì lệnh uvicorn ở trên, không phải tuỳ chọn —
# agent memory bền vững (AsyncPostgresSaver) cần SelectorEventLoop, nhưng uvicorn CLI trên
# Windows luôn chọn ProactorEventLoop trước khi app được import, không có cờ CLI nào sửa được.
Health check: GET http://localhost:8000/health → {"status":"ok",...}
Swagger UI: http://localhost:8000/docs
DB: PostgreSQL bắt buộc qua DATABASE_URL trong .env (không có default, không còn hỗ trợ SQLite) — xem README.md để tạo database. Test suite dùng database Postgres riêng (orbit_test mặc định, đổi qua TEST_DATABASE_URL).
Nếu sửa .env mà hành vi backend không đổi, kiểm tra có tiến trình uvicorn/scripts/run_dev.py cũ nào còn sống trên port 8000 trước khi nghi code sai — uvicorn --reload trên Windows để lại tiến trình con (spawn qua multiprocessing) vẫn giữ cổng dù tiến trình cha đã bị tắt, nhiều bản cũ/mới có thể cùng nhận request. Kiểm tra: netstat -ano | findstr :8000 rồi Stop-Process -Id <pid> -Force cho từng tiến trình tìm thấy, sau đó khởi động lại.
Frontend
bash
cd Frontend
npm install
npm run dev
App: http://localhost:5173
Frontend mặc định gọi backend tại http://localhost:8000/api/v1. Nếu backend chạy địa chỉ khác, tạo Frontend/.env từ Frontend/.env.example và sửa VITE_API_BASE_URL / VITE_WS_BASE_URL.
Test backend
bash
pytest tests/ -v
# hoặc: make test

Chưa thấy test suite riêng cho frontend trong README — nếu thêm test frontend, kiểm tra Frontend/package.json trước để biết runner đang dùng (nếu có) trước khi giả định.

Lint
bash
ruff check .
Quy ước code khi chỉnh sửa
Backend: giữ cấu trúc theo tầng đã có — route mỏng trong api/, logic nghiệp vụ trong services/, không nhét business logic vào route handler. Schema request/response dùng Pydantic trong models/. Thao tác DB qua SQLAlchemy models trong db/, tránh viết raw SQL trừ khi thật cần thiết.
Agent/LangGraph: các tool mới của agent (ví dụ tool gọi thêm API ngoài) đặt trong agents/, tuân theo pattern planner/tools/state đã có. Nếu tool thực hiện hành động có tác dụng phụ (tạo sự kiện, gửi nhắc nhở, xoá dữ liệu, ...), bắt buộc có bước xác nhận (human-in-the-loop) trước khi thực thi, giống cách đang làm với reminder/calendar — không bỏ qua bước này dù chỉ để test nhanh.
Frontend: component chia theo tính năng trong components/, trang trong pages/. Gọi API qua lớp api/, không gọi fetch/axios trực tiếp trong component. State auth/JWT lấy qua AuthContext, không tự lưu token rải rác. Khi nối một trang từ mock sang API thật, thay dữ liệu nhập từ data/mockData.js bằng hook tương ứng (theo pattern của useConversations, useMessages) thay vì sửa trực tiếp cấu trúc mock.
Auth: không tự ý đổi cơ chế hash mật khẩu (bcrypt) hay cấu trúc JWT hiện có trừ khi được yêu cầu rõ ràng — đây là phần đã "chạy thật" và có thể ảnh hưởng tài khoản người dùng đang tồn tại.
WebSocket: kênh real-time đã hoạt động cho chat 1-1/nhóm — nếu thêm sự kiện realtime mới (ví dụ cập nhật Task/Reminder khi nối API thật), tái sử dụng kênh/pattern trong websocket/ thay vì tạo kết nối WebSocket song song mới.
Trước khi commit / báo hoàn thành task
Chạy pytest tests/ -v nếu có đổi backend.
Chạy ruff check . và sửa lỗi lint.
Nếu đổi frontend, chạy thử npm run dev và kiểm tra route liên quan không bị vỡ (đặc biệt các route được bảo vệ bởi ProtectedRoute).
Không commit file .env thật (chỉ .env.example).
Tài liệu liên quan trong repo
Frontend/README.md — hướng dẫn riêng cho frontend, gồm cách xử lý lỗi thường gặp khi chạy npm trên Windows.
Frontend/detai.md — đề bài / yêu cầu gốc của dự án, tham khảo khi không chắc scope tính năng.
ARCHITECTURE.md — kiến trúc hệ thống hiện tại, sơ đồ, quyết định công nghệ.
ROADMAP.md — bảng đối chiếu từng yêu cầu đề bài với trạng thái thật + việc còn lại theo độ ưu tiên.
WORKLOG.md — nhật ký công việc theo ngày của cả nhóm, xem để biết ai đang làm phần nào trước khi động vào.
docs/guide/ — tài liệu khóa học AI20K (setup, LangGraph, FastAPI, testing, deploy).
Lưu ý an toàn khi code
Không hardcode GOOGLE_API_KEY, GROQ_API_KEY hay bất kỳ secret nào vào code — luôn đọc từ .env.
Khi agent thao tác với Google Calendar hoặc tạo nhắc nhở, giữ nguyên bước xác nhận người dùng trước khi gọi API thật; đây là yêu cầu thiết kế cốt lõi của sản phẩm (human-in-the-loop), không phải chi tiết có thể lược bỏ để "cho gọn".