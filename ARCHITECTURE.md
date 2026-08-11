# Architecture Document

## System Overview

Orbit là AI agent nhúng trong ứng dụng chat: FastAPI + LangGraph ở backend, React + Vite ở
frontend, **PostgreSQL** làm database duy nhất (không còn hỗ trợ SQLite, xem mục Database). Backend
có auth thật (JWT + bcrypt), nhắn tin 1-1/nhóm realtime qua WebSocket, phân
quyền role user/admin, và agent LangGraph (LLM: Google Gemini, Groq, hoặc OpenAI, đổi qua `.env`)
với các tool có human-in-the-loop (calendar, reminder) cùng tool tóm tắt/trích xuất task/tìm kiếm
tin cũ. Toàn bộ tính
năng người dùng (Tasks, Calendar, Reminders, Memory, Profile, AI Assistant, Admin) đã nối API thật
— không còn trang nào dùng mock data.

## Architecture Diagram

```mermaid
graph TB
    subgraph Frontend["Frontend (React + Vite)"]
        UI[Pages / Components]
        WSClient[WebSocket client]
    end

    subgraph Backend["Backend (FastAPI)"]
        AuthAPI["/api/v1/auth"]
        ChatAPI["/api/v1/conversations, /messages"]
        AdminAPI["/api/v1/admin"]
        AgentAPI["/api/v1/chat, /chat/resume"]
        DataAPI["/api/v1/tasks, /calendar, /reminders, /memories"]
        WS["/api/v1/ws"]
        Agent[LangGraph Agent]
        LLM[LLM Service - get_llm]
        Tools[Agent Tools]
        Scheduler["APScheduler - reminders + calendar poll"]
        Proactive[proactive_service]
    end

    subgraph Data["Data Layer"]
        DB[(PostgreSQL)]
        Google[Google Calendar API]
        LLMProvider[Gemini / Groq]
    end

    UI -->|HTTP/REST| AuthAPI
    UI -->|HTTP/REST| ChatAPI
    UI -->|HTTP/REST| AdminAPI
    UI -->|HTTP/REST| AgentAPI
    UI -->|HTTP/REST| DataAPI
    WSClient <-->|WebSocket| WS
    AgentAPI --> Agent
    Agent -.->|checkpoint per thread_id| DB
    Agent --> LLM
    Agent --> Tools
    LLM --> LLMProvider
    Tools --> Google
    Tools --> DB
    ChatAPI -->|new message| Proactive
    WS -->|new message| Proactive
    Proactive --> LLM
    Proactive -->|task gợi ý| DB
    Scheduler -->|poll changes| Google
    Scheduler -->|fire reminder| DB
    Scheduler -.->|broadcast| WS
    AuthAPI --> DB
    ChatAPI --> DB
    AdminAPI --> DB
    DataAPI --> DB
    WS --> DB
```

## Components

### 1. Frontend (React + Vite)
- **Purpose:** SPA cho toàn bộ trải nghiệm người dùng — auth, chat realtime, AI assistant, quản lý
  cá nhân (task/lịch/nhắc việc/memory/hồ sơ), admin.
- **Trang chính** (`Frontend/src/pages/`): `LoginPage`/`RegisterPage`, `ChatPage`, `TaskPage`,
  `CalendarPage`, `ReminderPage`, `MemoryPage`, `ProfilePage`, `PersonalAssistantPage` (`/assistant`
  — chat trực tiếp với agent, có nút Xác nhận/Huỷ khi agent cần human-in-the-loop), và 4 trang admin
  (`admin/AdminDashboardPage`, `AdminUsersPage`, `AdminConversationsPage`, `AdminUserDataPage` —
  quản lý Task/Reminder/Memory toàn hệ thống). Tất cả gọi API thật qua `Frontend/src/api/`, không
  còn trang nào dùng `Frontend/src/data/mockData.js`.
- **Panel AI trong chat** (`AIPanel.jsx`): Summarize, Extract tasks, Find schedule, Deadlines,
  Suggest reminder (có nút Xác nhận/Huỷ ngay trong panel), và ô tự do "Ask Orbit".
- **State Management:** React Context (`AuthContext` cho JWT/user hiện tại) + hook riêng theo tính
  năng (`useConversations`, `useMessages`), kênh WebSocket dùng chung qua `AppLayout.jsx` (Outlet
  context `subscribe`/`sendJson`) thay vì mỗi trang tự mở kết nối riêng.

### 2. Backend (FastAPI)
- **Purpose:** REST API + WebSocket cho auth, nhắn tin, quản trị, dữ liệu cá nhân, và cổng vào AI
  agent.
- **API Design:** RESTful, mounted dưới `/api/v1` — `auth_routes.py`, `chat_routes.py`,
  `admin_routes.py`, `routes.py` (agent chat), `task_routes.py`, `calendar_routes.py`,
  `reminder_routes.py`, `memory_routes.py`. Route mỏng — business logic nằm ở `src/services/`.
- **Authentication:** JWT (PyJWT), password hash bcrypt (`src/auth/`). `get_current_user` +
  `require_admin` dependency cho phân quyền 2 role (`user`/`admin`). `/api/v1/chat` verify thêm
  người gọi có phải participant của `conversation_id` (nếu có truyền) trước khi cho agent xử lý.
  Ngoài email/mật khẩu, `POST /auth/google` (`src/auth/google_oauth.py`) cho đăng nhập bằng Google —
  xác minh chữ ký ID token (GIS, không cần client secret, không có route callback), find-or-create
  qua bảng `google_identities` riêng (FK tới `users`, không ALTER bảng `users` sẵn có); JWT trả về
  tạo bởi đúng `create_access_token` dùng chung với flow mật khẩu — cấu trúc token không đổi.

### 3. AI Agent (LangGraph)
- **Agent Type:** Plan-and-execute dạng đơn giản — 1 node `planner` (LLM bound tools) ⇄ 1 node
  `tools` (`ToolNode`), lặp tới khi planner trả lời không kèm tool call. Tool không cần xác nhận
  (`summarize_conversation`, `extract_tasks`) dừng ngay sau khi chạy — dùng thẳng output làm câu trả
  lời, không gọi LLM lần 2 để "relay" lại (từng gây lỗi 400 do model tự hallucinate cú pháp tool call).
- **State:** `AgentState` (TypedDict, `total=False`) — `messages` (reducer `add_messages`),
  `context`, `summary`, `error`, `user_id`, `conversation_id` (chỉ set từ `routes.py`, không bao giờ
  do LLM cung cấp — cho tool cần biết đang ở hội thoại nào mà không thể bị "spoof"), ...
  (`src/agents/state.py`).
- **Nodes:** `planner_node` (`src/agents/nodes/planner_node.py`) — bind `ALL_TOOLS`, inject ngày
  giờ hiện tại theo `calendar_timezone` vào system prompt (tránh agent đoán sai "tomorrow"/"next
  Monday"), ghi token usage qua `usage_service.log_usage`, bắt exception vào `state["error"]`.
- **Tools** (`src/agents/tools/`, registry `ALL_TOOLS` trong `tools/__init__.py`, 9 tool):
  - `summarize_conversation`, `extract_tasks` — đọc `state["context"]`, không cần xác nhận.
  - `search_messages` — tìm tin nhắn cũ trong đúng hội thoại đang chat theo từ khoá (Postgres
    `ILIKE`, không phải semantic search — dự án chủ động không dùng vector store), đọc-only, không
    cần xác nhận; `conversation_id` lấy từ `state`, không phải tham số LLM tự truyền.
  - `create_calendar_event` / `list_calendar_events` / `update_calendar_event` /
    `delete_calendar_event` — Google Calendar thật qua `google-api-python-client`; mọi thao tác có
    tác dụng phụ đều bắt buộc `interrupt()` chờ xác nhận người dùng trước khi gọi API thật.
  - `create_reminder` / `list_reminders` — tương tự, `create_reminder` bắt buộc `interrupt()` trước
    khi lên lịch qua APScheduler (`SQLAlchemyJobStore`, bền vững qua restart).
- **Checkpointer (memory hội thoại):** `AsyncPostgresSaver` (bền vững qua restart) — xây trong
  `init_checkpointer()` lúc FastAPI lifespan khởi động, vì cần chạy trong event loop đang hoạt động
  (`src/agents/graph.py`). `agent` là `None` cho tới khi hàm này chạy xong.
- **Flow:**

```mermaid
graph LR
    START --> planner
    planner -->|có tool call| tools
    planner -->|trả lời thẳng / lỗi| END
    tools -->|tool cần xác nhận| planner
    tools -->|tool terminal| END
```

### 4. Agent chủ động (Proactive)
- **Purpose:** phát hiện cam kết/lịch hẹn/hạn chót ngay khi tin nhắn mới tới, không cần người dùng
  chủ động yêu cầu — mục "Nâng cao" của đề bài.
- **Cách hoạt động** (`src/services/proactive_service.py::maybe_suggest_task`): chạy nền
  (fire-and-forget) sau mỗi tin nhắn gửi qua REST (`chat_routes.py`, `BackgroundTasks`) hoặc
  WebSocket (`websocket/routes.py`, `asyncio.create_task` giữ tham chiếu tránh bị GC). Pre-filter
  rẻ bằng regex (EN+VI) trước khi gọi LLM để tiết kiệm chi phí; nếu qua bộ lọc, hỏi LLM xác nhận có
  cam kết thật không, rồi tạo `Task` (`status="suggested"`, `source="proactive"`) và đẩy WebSocket
  `task_suggested` → toast `TaskSuggestedToast.jsx` + mục "AI suggestions" trong `/tasks` để người
  dùng Accept/Dismiss. Toàn bộ bọc try/except — lỗi không bao giờ chặn việc gửi tin nhắn.

### 5. Database
- **Type:** **PostgreSQL only** qua SQLAlchemy async (`asyncpg`), cấu hình qua `DATABASE_URL` trong
  `.env` — bắt buộc, không có default, không còn nhánh SQLite. `src/db/session.py::_async_url()`
  chỉ còn việc đổi scheme `postgresql://` → `postgresql+asyncpg://`.
- **Windows:** bắt buộc chạy bằng `python scripts/run_dev.py` thay vì `uvicorn` CLI trực tiếp —
  `AsyncPostgresSaver` cần `SelectorEventLoop`, nhưng CLI `uvicorn` trên Windows luôn chọn
  `ProactorEventLoop` trước khi app được import, không cờ nào sửa được; `run_dev.py` gọi
  `uvicorn.run()` trực tiếp bằng Python để chỉ định đúng loại event loop.
- **Migration:** không dùng Alembic — bảng mới tạo tự động qua `Base.metadata.create_all()`, không
  ALTER cột trên bảng cũ (không cần nữa từ khi bỏ SQLite — file DB SQLite cũ không còn tồn tại để
  phải vá).
- **Test suite:** chạy trên database Postgres riêng (`orbit_test` mặc định, đổi qua
  `TEST_DATABASE_URL`) — tạo schema 1 lần cho cả phiên test (`tests/conftest.py::_test_database`,
  session-scoped), truncate toàn bộ bảng sau mỗi test để cách ly. Engine dùng `NullPool` khi
  `APP_ENV=test` vì test chạy app qua nhiều event loop khác nhau (`client` fixture dùng loop chính
  của pytest-asyncio, `TestClient` cho WebSocket test dùng loop riêng trong thread nền) — asyncpg
  connection bị bind cứng vào loop đã tạo ra nó, pool connection tái sử dụng giữa 2 loop sẽ lỗi
  "attached to a different loop".
- **Tables hiện có** (`src/db/models.py`): `User` (role, is_active, job_title, timezone,
  preferences), `GoogleIdentity` (link `user_id` ↔ `google_sub` cho đăng nhập Google — bảng riêng,
  không thêm cột vào `User`), `Conversation`, `ConversationParticipant`, `Message`, `Task` (status
  suggested/pending/in_progress/completed/dismissed, source manual/proactive), `Reminder` (status
  scheduled/fired/cancelled), `Memory` (ghi chú cá nhân người dùng tự thêm — category/title/detail,
  **khác** với agent checkpoint memory ở trên), `UsageLog` (token mỗi lần gọi LLM),
  `GoogleCalendarCredential` (1 dòng/user — `refresh_token_enc`/`access_token_enc` mã hoá Fernet,
  `sync_token` cho polling đồng bộ, `google_email` để hiện UI; tồn tại = user đó đã Connect Google
  Calendar riêng của họ — trước đây là `CalendarSyncState` dùng chung 1 dòng "default" cho cả app
  hồi Calendar còn là 1 tài khoản chia sẻ, không mã hoá gì cả). Ngoài ra
  APScheduler tự quản bảng `apscheduler_jobs`, và khi dùng Postgres, LangGraph tự quản các bảng
  `checkpoints`/`checkpoint_blobs`/`checkpoint_writes`/`checkpoint_migrations`.

### 6. Vector Store
- **Hiện tại:** chưa nối — `chroma_persist_dir` khai báo sẵn trong `src/config.py` nhưng không nơi
  nào trong code thực sự dùng embedding/vector search.
- **Đánh giá:** yêu cầu "Cơ bản" về memory hội thoại đã đạt qua `AsyncPostgresSaver` (memory theo
  từng thread), và "Memory cá nhân" (sở thích, thói quen...) đã đạt qua tính năng Memory (ghi chú do
  người dùng tự thêm, `/memory`). Đề bài chỉ liệt kê Qdrant/pgvector ở mục "Tech stack gợi ý", không
  phải yêu cầu đầu ra bắt buộc — nên vector store dài hạn hiện **không còn là việc cần làm rõ ràng**,
  chỉ cân nhắc nếu sau này cần semantic search xuyên nhiều hội thoại cũ.

## Data Flow

1. User gửi tin nhắn/thao tác từ Frontend qua REST hoặc WebSocket.
2. Route xác thực (JWT) và validate input (Pydantic schema trong `src/models/`).
3. Với tin nhắn người-với-người: broadcast realtime qua `src/websocket/` tới thành viên khác, đồng
   thời chạy nền `proactive_service.maybe_suggest_task` để phát hiện cam kết/lịch hẹn.
4. Với tin nhắn agent (`POST /api/v1/chat`): verify participant nếu có `conversation_id`, build
   `AgentState`, chạy qua LangGraph (`src/agents/graph.py::agent`, checkpoint theo `thread_id`).
5. Planner gọi LLM (Gemini/Groq); nếu cần hành động có tác dụng phụ (calendar/reminder), graph dừng
   lại ở `interrupt()` chờ xác nhận qua `POST /api/v1/chat/resume`.
6. Song song, APScheduler chạy 2 job nền: bắn `reminder_fired` đúng giờ hẹn, và poll Google Calendar
   mỗi `CALENDAR_POLL_INTERVAL_SECONDS` (mặc định 20s) để bắt thay đổi tạo trực tiếp trong Google
   Calendar (ngoài app) — cả hai đẩy kết quả qua WebSocket cho người liên quan.

## Deployment Architecture

```mermaid
graph LR
    subgraph "Hiện tại (local only)"
        BE_C[Backend - scripts/run_dev.py hoặc Dockerfile]
        FE_C[Frontend - vite dev server]
        DB_C[(PostgreSQL - local)]
    end
```

`Dockerfile` (multi-stage, non-root, healthcheck `/health`) và `docker-compose.yml` định nghĩa
service `backend` nhưng **chưa deploy online thật** — chưa có domain public, chưa có CD workflow
ngoài `ci.yml` (lint + test trên GitHub Actions). Đây là hạng mục lớn nhất còn thiếu so với đề bài.

## Security

- API key/secret đọc từ `.env` (không commit), ví dụ `GOOGLE_API_KEY`, `GROQ_API_KEY`, `SECRET_KEY`.
- Input validation qua Pydantic ở mọi route.
- Password hash bcrypt, JWT cho auth — xem quy ước "không tự ý đổi cơ chế" trong `CLAUDE.md`.
- CORS cấu hình qua `cors_origins` trong `.env`.
- Human-in-the-loop bắt buộc cho mọi tool có tác dụng phụ (calendar, reminder) — không được bỏ
  qua kể cả để test nhanh (quy ước trong `CLAUDE.md`).
- `/api/v1/chat` verify current_user là participant của `conversation_id` trước khi agent xử lý —
  chặn user A mượn nội dung hội thoại của user B qua request tự chế.
- Rate limiting: **chưa có** trên API endpoints — cân nhắc nếu deploy thật và mở public.
- Quyền AI đọc hội thoại: bảng `ai_permissions` (`conversation_id`, `user_id`, `granted`) thật ở
  backend — mỗi participant tự cấp/thu hồi quyền cho AI đọc hội thoại đó, độc lập với các thành
  viên khác (không cần đồng thuận cả nhóm). `POST /api/v1/chat` (`src/api/routes.py`) gọi
  `chat_service.assert_ai_permission` ngay sau `assert_participant`, trước khi build context cho
  agent — 403 nếu chưa cấp quyền hoặc quyền đã bị thu hồi. `GET/PUT /conversations/{id}/ai-permission`
  (`src/api/chat_routes.py`) cho FE đọc/ghi; `AIPanel.jsx` gọi API thật thay vì state cục bộ, mặc
  định hiển thị "Permission required" cho tới khi fetch xong. Panel vẫn giữ dòng minh bạch báo
  người dùng nội dung sẽ được gửi sang Gemini/Groq.
- Không có mã hoá đầu-cuối (E2E) thật — quyết định có chủ đích, xem `ROADMAP.md`. Đã rà soát:
  không có nơi nào trong `src/` log/lưu nội dung tin nhắn thô ra ngoài DB của app —
  `usage_service.py` chỉ log số token (không log nội dung prompt), không có `print`/`logger` nào
  khác đụng tới nội dung tin nhắn; nội dung chỉ rời khỏi app khi agent gửi sang LLM provider đã
  khai báo (Gemini/Groq), và chỉ khi `ai_permissions` cho phép.

## Design Decisions

| Decision | Choice | Reason |
| --- | --- | --- |
| Backend framework | FastAPI | Async, auto-docs (`/docs`), type-safe qua Pydantic |
| Agent orchestration | LangGraph | Quản lý state + human-in-the-loop (`interrupt`) sẵn có, phù hợp yêu cầu xác nhận trước hành động |
| LLM provider | Google Gemini hoặc Groq (`src/services/llm.py::get_llm()`, đổi qua `LLM_PROVIDER`) | Đổi được khi 1 bên hết quota — thực tế đã cần dùng đến (Gemini free-tier từng về 0 quota) |
| Database | PostgreSQL only (bỏ SQLite) | Cần cho agent memory bền vững qua restart (`AsyncPostgresSaver`), FK constraint thật (SQLite không enforce mặc định), một DB duy nhất cho cả dev lẫn test |
| Vector store | Không triển khai | Yêu cầu memory đã đạt qua checkpointer + tính năng Memory ghi chú; không có nhu cầu semantic search rõ ràng để biện minh thêm 1 service |
| Frontend framework | React + Vite | Giữ nguyên so với đề bài gợi ý Next.js — tránh viết lại toàn bộ frontend không tương xứng lợi ích |
| Realtime | WebSocket thuần (FastAPI) | Dùng chung 1 kênh cho chat, reminder-fired, proactive-suggestion, calendar sync — không mở kênh song song |
| Scheduler | APScheduler (`SQLAlchemyJobStore`) | Bền vững qua restart, dùng chung cho reminder-fire và calendar-poll thay vì đổi hẳn sang BullMQ/Node |
| Đồng bộ Google Calendar | Polling định kỳ với `syncToken`, lặp riêng theo từng user **đang online** đã Connect (không phải webhook `events.watch`) | Webhook thật của Google cần domain public HTTPS mà project chưa deploy — polling là lựa chọn thực tế, có thể nâng cấp lên webhook sau khi deploy. Chỉ poll user đang online (không phải mọi user đã Connect) để không tốn quota Google cho thay đổi lịch của người không mở app — họ tự fetch lại khi mở `/calendar` |
| Google Calendar OAuth | Per-user, redirect thật (`GET /calendar/oauth/url` → Google → `GET /calendar/oauth/callback` ở backend), OAuth Client "Web application" tách biệt với client đăng nhập, `state` ký JWT mang `user_id` | Yêu cầu đề bài "lịch cá nhân" — user phải thấy đúng lịch của họ, không phải 1 lịch dùng chung cho cả app. Tách 2 OAuth Client vì đăng nhập chỉ verify ID token (không cần secret), Calendar cần authorization-code + refresh_token (bắt buộc có secret) — không phải xin quyền Calendar ngay lúc đăng nhập |
| Mã hoá refresh token | Fernet (`src/auth/crypto.py`), key `CREDENTIAL_ENCRYPTION_KEY` | Refresh token Calendar là bí mật dài hạn — lộ ra là đọc/ghi được lịch của user vô thời hạn tới khi họ tự revoke, khác `password_hash` (một chiều) hay JWT `secret_key` (không phải thứ đọc trực tiếp ra hành động trên tài khoản Google người khác) |

Tiến độ triển khai theo giai đoạn và các hạng mục còn lại: xem [ROADMAP.md](ROADMAP.md).
