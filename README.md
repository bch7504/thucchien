# P-132 — Orbit AI Assistant

Dự án AI20K Build Phase: một AI agent nhúng trong ứng dụng chat, giúp tóm tắt hội thoại, trích xuất công việc/lịch hẹn, tạo nhắc nhở (có xác nhận trước khi thực hiện) và quản lý lịch cá nhân. Repo gồm 2 phần: **backend** (FastAPI + LangGraph, thư mục `src/`) và **frontend** (React + Vite, thư mục `Frontend/`).

## Hiện có gì

### Đã hoạt động thật (có backend, có database)

- **Đăng ký / Đăng nhập / Đăng xuất**: tài khoản lưu thật trong database PostgreSQL, mật khẩu hash bằng bcrypt, xác thực bằng JWT. Route bên trong ứng dụng (`/assistant`, `/chat`, `/tasks`, ...) được bảo vệ — chưa đăng nhập sẽ tự chuyển về `/login`.
- **Đăng nhập bằng Google**: nút "Sign in with Google" trên `/login` và `/register` (cùng 1 nút xử lý cả đăng nhập lẫn đăng ký lần đầu). Backend xác minh ID token của Google (`src/auth/google_oauth.py`), không cần client secret. Tài khoản Google được lưu trong bảng `google_identities` riêng (không đụng bảng `users`/mật khẩu hiện có); nếu email trùng tài khoản mật khẩu có sẵn thì tự liên kết — nhưng chỉ khi Google xác nhận `email_verified`. Cần tự tạo Google OAuth Client ID (xem mục "Cách chạy web" bước 2) mới bật được nút này.
- **Agent nhớ hội thoại bền vững qua PostgreSQL**: agent dùng `AsyncPostgresSaver` — hội thoại/interrupt sống sót qua restart backend. Trên Windows, bắt buộc chạy bằng `python scripts/run_dev.py` thay vì `uvicorn` CLI trực tiếp — xem mục "Cách chạy web" bên dưới.
- **Nhắn tin 1-1 và theo nhóm, real-time**: tạo cuộc trò chuyện 1-1 hoặc nhóm (chọn nhiều người), gửi/nhận tin nhắn tức thời qua WebSocket, xem lại lịch sử tin nhắn, đếm tin nhắn chưa đọc.
- **AI Agent (chat với AI)**: endpoint `/api/v1/chat` (yêu cầu đăng nhập) dùng LangGraph, có tool gọi Google Calendar và tạo nhắc nhở với bước xác nhận (human-in-the-loop) trước khi thực hiện. Hỗ trợ 3 provider LLM (Google Gemini, Groq, hoặc OpenAI — đổi qua `LLM_PROVIDER` trong `.env`) để dễ chuyển khi một bên hết quota.
- **AI Assistant cá nhân** (`/assistant`): khung chat riêng nối thẳng vào agent thật ở trên (không phải dữ liệu mẫu) — hỏi tự do, khi agent muốn tạo lịch/nhắc việc sẽ hiện nút Xác nhận/Huỷ ngay trong chat.
- **Phân quyền Admin**: tài khoản có 2 role (`user`/`admin`). Trang `/admin` (Dashboard, Users, Conversations) chỉ hiển thị và truy cập được với tài khoản `admin` — xem thống kê hệ thống, đổi role/khoá-mở khoá tài khoản, xem/xoá hội thoại để kiểm duyệt, và theo dõi lượng token AI đã dùng trong ngày.
- **Cảnh báo + tự chặn khi vượt hạn mức token/chi phí**: ngay khi tổng token dùng trong ngày vượt 80%/100% `DAILY_TOKEN_BUDGET`, mọi admin đang online nhận toast cảnh báo qua WebSocket ở bất kỳ trang nào đang mở (không chỉ khi chủ động mở `/admin`); một khi đã vượt hẳn ngân sách, cuộc gọi LLM mới (`/chat` và agent chủ động) bị chặn hẳn thay vì chỉ cảnh báo — riêng việc hoàn tất một hành động đã được người dùng xác nhận (`/chat/resume`) không bị chặn để không treo lơ lửng.
- **Tóm tắt hội thoại theo yêu cầu**: trong trang Chat, bấm icon AI trên header → **Summarize** — AI đọc tin nhắn thật (theo scope 20/50 tin gần nhất đang chọn) và trả về bản tóm tắt.
- **Trích xuất Task từ hội thoại**: cùng panel AI → **Extract tasks** — AI tìm việc cần làm/lịch hẹn trong hội thoại, lưu vào trang `/tasks` mục "AI suggestions"; người dùng bấm **Accept**/**Dismiss** để xác nhận. Panel AI còn có **Find schedule**, **Deadlines**, **Suggest reminder** (hiện nút Xác nhận/Huỷ ngay trong panel vì tạo reminder cần human-in-the-loop), cùng ô **Ask Orbit** để hỏi tự do về hội thoại đang xem.
- **Task Inbox ưu tiên** (`/tasks/inbox`): view tách riêng khỏi danh sách task thường, nhóm việc cần chú ý ngay thành 4 mức — cần quyết định (gợi ý AI chưa Accept/Dismiss), quá hạn, sắp đến hạn trong 48h, và priority cao — thay vì phải tự lọc trong danh sách đầy đủ.
- **Lịch cá nhân (Google Calendar thật, per-user, đồng bộ 2 chiều, realtime)**: trang `/calendar` — mỗi người tự bấm **Connect Google Calendar** (OAuth Client riêng, xem mục "Cách chạy web" bước 2) để nối đúng Google Calendar của chính họ; chưa Connect thì trang chỉ hiện nút mời kết nối, không có sự kiện nào. Sau khi kết nối, xem/tạo/sửa/xoá sự kiện thật trên calendar của người đó. Agent cũng dùng chung API này (`list/create/update/delete_calendar_event` tool, tự biết đang thao tác trên calendar của ai đang chat) nên có thể quản lý lịch qua chat, không chỉ qua UI. Mọi thay đổi (từ UI, từ chat, hoặc tạo/sửa/xoá trực tiếp trong chính Google Calendar) đều đẩy qua WebSocket — chỉ tới đúng người sở hữu calendar đó, không phải mọi người đang mở `/calendar`. Thay đổi từ phía Google được bắt bằng cách polling định kỳ cho từng user **đang online** đã kết nối (`CALENDAR_POLL_INTERVAL_SECONDS`, mặc định 20s) chứ chưa dùng webhook thật của Google (cần domain public HTTPS mà project chưa deploy).
- **Nhắc nhở bền vững + realtime**: trang `/reminders` tạo nhắc nhở thật, lưu DB, sống sót qua restart server (APScheduler + `SQLAlchemyJobStore`); khi đến giờ, đẩy thông báo realtime qua WebSocket dù đang ở trang nào.
- **Hồ sơ cá nhân** (`/profile`): sửa tên/chức danh/timezone/tuỳ chọn thông báo và đổi mật khẩu — lưu thật vào database, không còn là dữ liệu mẫu.
- **Memory cá nhân** (`/memory`): thêm/sửa/xoá "điều Orbit nên nhớ về bạn" (sở thích, thói quen, thông tin người liên quan...), lọc theo danh mục và tìm kiếm — lưu thật vào database.
- **Agent chủ động (proactive), realtime**: mỗi tin nhắn mới trong Chat được rà tự động (pre-filter rẻ + LLM xác nhận) — nếu chứa cam kết/lịch hẹn/hạn chót, Orbit tự tạo gợi ý và đẩy thẳng vào `/tasks` mục "AI suggestions" qua WebSocket (không cần refresh) kèm toast, không cần người dùng chủ động yêu cầu. Toàn bộ thao tác Task (accept/dismiss/complete/xoá) cũng đồng bộ realtime giữa các tab/thiết bị.
- **Múi giờ thống nhất Asia/Ho_Chi_Minh (Hà Nội)**: mọi nơi hiển thị ngày giờ (Chat, Task, Calendar, Reminder, Memory, Admin) đều quy về giờ Hà Nội bất kể múi giờ máy người xem, qua `Frontend/src/utils/datetime.js`. Backend cũng cố định giờ Hà Nội cho scheduler (reminder fire đúng giờ dù server chạy múi giờ khác) và mốc "hôm nay" của thống kê token.

### Công cụ đánh giá (dev, không phải tính năng người dùng)

- `scripts/eval_extract_tasks.py` — đo Precision/Recall/F1 của việc trích xuất **tiêu đề** task, và riêng **độ chính xác ngày giờ** (`due_at` có resolve đúng "ngày mai"/"thứ Sáu này" theo ngày chạy thật không — hai thứ này lệch pha nhau: tiêu đề đúng không có nghĩa ngày đúng) trên bộ dữ liệu tay (8 case tiếng Việt + Anh, có cả case không có task để đo độ chính xác). Gọi LLM thật nên không nằm trong `pytest tests/` — chạy tay: `python scripts/eval_extract_tasks.py`. Kết quả gần nhất (model `gpt-4o-mini` qua OpenAI): **Title F1 = 100%, Date accuracy = 100%** (8/8 case, 7/7 case có ngày).

### Chưa xong

- **Deploy online**: có `Dockerfile`/`docker-compose.yml` nhưng chưa deploy lên domain public.

## Kiến trúc

> **Backend nằm ở thư mục [`src/`](src/) ở gốc repo** (FastAPI + LangGraph), tách biệt hoàn toàn với frontend ở [`Frontend/`](Frontend/) (React + Vite). Chạy backend bằng lệnh `uvicorn src.main:app ...` từ thư mục gốc repo, không phải từ bên trong `src/`.

```
├── src/                  # Backend — FastAPI + LangGraph
│   ├── agents/           # Agent LangGraph (planner, tools, state)
│   ├── api/               # REST routes: auth, chat (người-với-người), agent chat
│   ├── auth/              # Hash mật khẩu, tạo/kiểm tra JWT
│   ├── db/                # SQLAlchemy models + session (PostgreSQL)
│   ├── models/             # Pydantic schemas
│   ├── services/           # chat_service, scheduler, llm, usage_service
│   ├── websocket/          # Kênh real-time cho chat
│   └── main.py             # Điểm khởi tạo FastAPI app
├── tests/                 # pytest cho backend
└── Frontend/               # Frontend — React + Vite
    └── src/
        ├── api/            # Gọi REST API + WebSocket client
        ├── context/         # AuthContext (JWT, user hiện tại)
        ├── hooks/            # useConversations, useMessages
        ├── components/        # Component theo tính năng (chat, layout, ...)
        ├── pages/              # Các trang ứng dụng
        └── router/              # React Router + ProtectedRoute
```

## Cách chạy web (local development)

Cần chạy **song song 2 server** — backend (cổng 8000) và frontend (cổng 5173) — mỗi lần dùng app đều cần mở cả hai (backend không tự chạy nền, tắt terminal là tắt server).

### 1. Chuẩn bị

- Python 3.11+
- Node.js 18+ và npm
- PostgreSQL đang chạy (local hoặc Docker) — bắt buộc, dự án không còn hỗ trợ SQLite. Tạo sẵn 1
  database (ví dụ `orbit`), sẽ dùng địa chỉ này cho `DATABASE_URL` ở bước 2.
- Đã clone repo và `cd` vào thư mục gốc dự án

### 2. Chạy Backend

```bash
# Tạo virtual environment (chỉ cần làm 1 lần)
python -m venv .venv

# Kích hoạt venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# Cài dependency
pip install -r requirements.txt

# Tạo file cấu hình (chỉ cần làm 1 lần)
cp .env.example .env
# Mở .env, điền GOOGLE_API_KEY (lấy tại https://aistudio.google.com/apikey) nếu muốn dùng tính năng AI chat (tóm tắt, calendar, nhắc nhở).
#   Nếu tài khoản Google chưa có quota free-tier (lỗi 429/quota=0 khi gọi), đổi provider:
#   - Groq: LLM_PROVIDER=groq, GROQ_API_KEY (lấy tại https://console.groq.com/keys), MODEL_NAME=openai/gpt-oss-20b.
#   - OpenAI: LLM_PROVIDER=openai, OPENAI_API_KEY (lấy tại https://platform.openai.com/api-keys), MODEL_NAME=gpt-4o-mini.
# Sửa DATABASE_URL trỏ vào database Postgres đã tạo ở bước 1 (postgresql://user:pass@host:5432/dbname) — bắt buộc, không có giá trị mặc định.
# Điền INITIAL_ADMIN_EMAIL nếu muốn tài khoản đăng ký với email đó tự động có quyền admin.
# Muốn bật nút "Đăng nhập bằng Google": tạo 1 OAuth Client ID loại "Web application" tại
#   https://console.cloud.google.com/apis/credentials, Authorized JavaScript origins:
#   http://localhost:5173. Điền Client ID vào GOOGLE_OAUTH_CLIENT_ID ở đây, và giá trị y hệt vào
#   VITE_GOOGLE_CLIENT_ID trong Frontend/.env (bước 3) — không điền thì nút Google chỉ ẩn/không hoạt
#   động, các tính năng khác không ảnh hưởng.
# Muốn bật nút "Connect Google Calendar" (mỗi user tự nối Calendar riêng của họ, xem docs/PER_USER_CALENDAR.md):
#   1. Bật "Google Calendar API" tại https://console.cloud.google.com — APIs & Services → Library.
#   2. OAuth consent screen: thêm scope https://www.googleapis.com/auth/calendar, thêm email từng
#      người sẽ test vào "Test users" (scope nhạy cảm nên app ở chế độ Testing, tối đa 100 test
#      user, ai không có trong danh sách sẽ gặp lỗi access_denied).
#   3. Credentials → Create Credentials → OAuth client ID → Web application (KHÁC client đăng nhập
#      ở trên — client này cần đổi authorization code lấy refresh token nên phải có Client Secret).
#   4. Authorized redirect URIs: thêm ĐÚNG http://localhost:8000/api/v1/calendar/oauth/callback
#      (đây là redirect thật, không phải popup — phải khớp từng ký tự với GOOGLE_CALENDAR_REDIRECT_URI).
# Điền GOOGLE_CALENDAR_CLIENT_ID + GOOGLE_CALENDAR_CLIENT_SECRET ở đây — không cần điền gì ở
#   Frontend/.env (khác với nút đăng nhập ở trên, nút Connect Calendar không cần biến VITE_* nào,
#   toàn bộ OAuth chạy ở backend). Cũng cần CREDENTIAL_ENCRYPTION_KEY (mã hoá refresh token trước
#   khi lưu DB) — sinh 1 lần bằng:
#     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#   Không điền GOOGLE_CALENDAR_CLIENT_ID/SECRET thì nút Connect vẫn hiện nhưng bấm vào báo lỗi rõ
#   ràng thay vì mở được màn hình Google; các tính năng khác không ảnh hưởng.

# Chạy server
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

Nếu có `make` (macOS/Linux, hoặc cài Make trên Windows): dùng `make run` thay cho lệnh `uvicorn` ở trên.

**Windows**: luôn dùng `python scripts/run_dev.py` thay cho lệnh `uvicorn` ở trên (cùng `--reload`, cùng cổng 8000) — không phải tuỳ chọn. Lý do: agent memory bền vững (`AsyncPostgresSaver`) cần `SelectorEventLoop`, nhưng CLI `uvicorn` trên Windows luôn chọn `ProactorEventLoop` trước cả khi app được import, không có cờ nào sửa được — `run_dev.py` gọi `uvicorn.run()` trực tiếp bằng Python để chỉ định đúng loại event loop.

Kiểm tra backend đã chạy: mở `http://localhost:8000/health` phải trả về `{"status":"ok",...}`. Swagger UI (danh sách toàn bộ API) ở `http://localhost:8000/docs`.

### 3. Chạy Frontend

Mở một terminal khác:

```bash
cd Frontend
npm install
npm run dev
```

Mở `http://localhost:5173` trong trình duyệt. Frontend mặc định gọi backend tại `http://localhost:8000/api/v1` — nếu backend chạy ở địa chỉ khác, tạo file `Frontend/.env` từ `Frontend/.env.example` và sửa `VITE_API_BASE_URL`/`VITE_WS_BASE_URL`. Muốn bật nút "Đăng nhập bằng Google" thì cũng cần tạo `Frontend/.env` và điền `VITE_GOOGLE_CLIENT_ID` (cùng giá trị `GOOGLE_OAUTH_CLIENT_ID` đã điền ở bước 2). Nút "Connect Google Calendar" thì **không cần** biến `VITE_*` nào — toàn bộ OAuth chạy ở backend (đã cấu hình ở bước 2), frontend chỉ mở 1 cửa sổ popup trỏ tới URL do backend trả về.

### 4. Dùng thử

1. Vào `http://localhost:5173/register`, tạo tài khoản.
2. Mở thêm một trình duyệt/tab ẩn danh khác, tạo tài khoản thứ hai.
3. Từ tài khoản thứ nhất, vào trang **Chats**, bấm nút bút (soạn tin nhắn) để chọn người và bắt đầu chat 1-1 hoặc chọn nhiều người để tạo nhóm.
4. Gửi tin nhắn — tài khoản còn lại sẽ nhận tin nhắn theo thời gian thực nếu đang mở cùng cuộc trò chuyện, hoặc thấy số tin nhắn chưa đọc.
5. Muốn thử trang **Admin**: đăng ký một tài khoản với email trùng `INITIAL_ADMIN_EMAIL` đã đặt trong `.env` (hoặc đổi role một tài khoản có sẵn thành `admin` trực tiếp trong DB) — tài khoản đó sẽ thấy mục "Admin" trong Sidebar, vào được `/admin`.
6. Muốn thử **AI Summarize / Extract tasks / Find schedule / Deadlines / Ask Orbit**: cần điền `GOOGLE_API_KEY` (hoặc Groq, xem bước 2) thật trong `.env`. Trong 1 cuộc trò chuyện có vài tin nhắn, bấm icon AI trên header (⭐) rồi thử từng quick action, hoặc gõ câu hỏi tự do vào ô "Ask Orbit".
7. Muốn thử **AI Assistant cá nhân** (`/assistant`): vào trang này và chat trực tiếp — nếu bạn yêu cầu tạo lịch/nhắc việc, agent sẽ hỏi lại xác nhận ngay trong khung chat trước khi tạo thật.
8. Muốn xem **theo dõi token AI**: vào `/admin` (cần tài khoản admin, xem bước 5) — 2 stat card "AI tokens used today"/"AI requests today" và banner cảnh báo khi dùng ≥80% ngân sách `DAILY_TOKEN_BUDGET`. Hạ tạm `DAILY_TOKEN_BUDGET` (ví dụ `=50`) trong `.env` rồi restart backend nếu muốn thấy toast cảnh báo realtime (`usage_budget_alert` qua WebSocket) xuất hiện ngay khi đang ở bất kỳ trang nào, không cần mở `/admin` — và xác nhận `/chat` bị chặn hẳn (không chỉ cảnh báo) một khi đã vượt hẳn ngân sách.
9. Muốn thử **Agent chủ động**: gửi 1 tin nhắn kiểu "nhớ họp lúc 3h chiều mai nhé" trong trang Chat — vài giây sau sẽ có toast "Orbit spotted a commitment" ở góc phải, và gợi ý xuất hiện trong `/tasks` mục "AI suggestions".
10. Muốn thử **Memory**: vào `/memory`, bấm "Add memory" để lưu một điều bạn muốn Orbit nhớ, sửa/xoá qua menu 3 chấm trên mỗi thẻ.
11. Muốn thử **Task Inbox ưu tiên**: vào `/tasks/inbox` (hoặc mục "Inbox" trong Sidebar) — task quá hạn/sắp đến hạn/priority cao/cần quyết định được nhóm riêng khỏi danh sách task đầy đủ ở `/tasks`.
12. Muốn thử **Đăng nhập bằng Google**: cần đã điền `GOOGLE_OAUTH_CLIENT_ID`/`VITE_GOOGLE_CLIENT_ID` thật (xem bước 2, 3). Vào `/login` hoặc `/register`, bấm nút Google bên dưới nút Sign in/Create account — lần đầu sẽ tự tạo tài khoản mới (role admin nếu email trùng `INITIAL_ADMIN_EMAIL`), lần sau đăng nhập lại đúng tài khoản đó.
13. Muốn thử **Calendar (per-user)**: cần đã điền `GOOGLE_CALENDAR_CLIENT_ID`/`GOOGLE_CALENDAR_CLIENT_SECRET`/`GOOGLE_CALENDAR_REDIRECT_URI`/`CREDENTIAL_ENCRYPTION_KEY` thật (xem bước 2 — client riêng, khác client đăng nhập, cần Authorized redirect URI khớp chính xác). Vào `/calendar`, bấm **Connect Google Calendar** (mở popup thật tới Google, không phải giả lập), chọn tài khoản Google, đồng ý quyền truy cập — popup tự đóng, sau đó xem/tạo/sửa/xoá sự kiện thật trên đúng Calendar của tài khoản Google vừa chọn. Đăng nhập bằng 2 tài khoản khác nhau và tự Connect 2 Google account khác nhau ở mỗi bên để thấy rõ mỗi người có Calendar riêng, không dùng chung — tạo sự kiện bên A không hiện bên B.

### 5. Test Calendar cùng nhiều thành viên trong nhóm

Calendar là tính năng **per-user** (mỗi người tự connect đúng Google Calendar của mình), nhưng cả nhóm **dùng chung 1 OAuth Client** (`GOOGLE_CALENDAR_CLIENT_ID`/`SECRET`) — không cần ai tạo Google Cloud project riêng, và không cần deploy online mới test được.

1. **Một người trong nhóm** tạo Google Cloud project + OAuth Client theo đúng hướng dẫn ở bước 2 (mục "Connect Google Calendar"). Ở phần OAuth consent screen ("Audience" trong Console bản mới) → **Test users**, add **email Gmail của tất cả thành viên sẽ test** (tối đa 100, app đang ở chế độ Testing) — không phải chỉ email của người tạo.
2. Người đó gửi `GOOGLE_CALENDAR_CLIENT_ID` + `GOOGLE_CALENDAR_CLIENT_SECRET` cho cả nhóm qua kênh riêng tư (chat nhóm) — **không** đưa lên GitHub/PR, không commit vào `.env`.
3. Mỗi thành viên còn lại `git pull` rồi tự chạy backend + frontend trên máy mình như bước 1-3 ở trên, với:
   - `DATABASE_URL` trỏ vào **database Postgres riêng trên máy họ** (không dùng chung DB với người khác — mỗi người có dữ liệu độc lập, kể cả token Calendar đã mã hoá).
   - `GOOGLE_CALENDAR_CLIENT_ID`/`GOOGLE_CALENDAR_CLIENT_SECRET` = giá trị nhận ở bước 2 (dùng chung cho cả nhóm).
   - `GOOGLE_CALENDAR_REDIRECT_URI` giữ nguyên mặc định `http://localhost:8000/api/v1/calendar/oauth/callback` — ai cũng chạy backend ở `localhost:8000` trên máy mình nên không cần đổi.
   - `CREDENTIAL_ENCRYPTION_KEY` **tự sinh riêng** cho máy mình (không cần trùng với người khác, vì mỗi người có DB riêng ở trên).
4. Mỗi người tự đăng ký 1 tài khoản Orbit riêng (bước 4.1), vào `/calendar` → **Connect Google Calendar** → chọn đúng Gmail đã được add làm Test user ở bước 1.

Lỗi thường gặp khi test theo nhóm:
- Bấm Connect ra lỗi `access_denied` ngay ở màn hình Google → email dùng để đăng nhập Google **không nằm trong Test users** (quay lại bước 1, add thêm).
- Backend báo lỗi ngay khi bấm Connect, không mở được popup Google → quên điền hoặc quên **restart backend** sau khi sửa `GOOGLE_CALENDAR_CLIENT_ID`/`SECRET` trong `.env`.
- Popup Google đóng lại nhưng báo "Could not connect Google Calendar." → xem log ở terminal đang chạy `python scripts/run_dev.py` ngay lúc đó để biết lý do cụ thể (thường in kèm traceback ở dòng `Failed to exchange the authorization code`).

### Chạy test backend

Test chạy trên một database Postgres riêng (không đụng tới database dev) — tạo 1 lần:

```bash
psql -U postgres -c "CREATE DATABASE orbit_test;"
```

Mặc định test kết nối `postgresql://postgres:123456@localhost:5432/orbit_test`; đổi bằng biến môi
trường `TEST_DATABASE_URL` nếu Postgres local dùng user/password khác.

```bash
pytest tests/ -v
# hoặc: make test
```

## Công nghệ sử dụng

| Layer | Công nghệ |
| --- | --- |
| AI Agent | LangGraph + LangChain (Google Gemini, Groq, hoặc OpenAI, đổi qua `LLM_PROVIDER`) |
| Backend | FastAPI, SQLAlchemy (async) + PostgreSQL, JWT (PyJWT) + bcrypt, WebSocket |
| Agent memory | LangGraph checkpointer — `AsyncPostgresSaver` (bền vững qua restart) |
| Frontend | React 18, Vite, React Router, React Hook Form, Bootstrap 5, Framer Motion |
| Test | pytest, pytest-asyncio, httpx |
| Lint | ruff |

## Tài liệu thiết kế (deliverable "Chốt bài toán + thiết kế")

- [docs/BRIEF.md](docs/BRIEF.md) — 1-page brief: vấn đề, người dùng, giải pháp, phạm vi, chỉ số thành công, rủi ro.
- [docs/PRD.md](docs/PRD.md) — PRD: user stories + acceptance criteria, yêu cầu phi chức năng, ERD, API surface, luồng agent.
- [docs/UI_FLOW.md](docs/UI_FLOW.md) — sitemap, luồng người dùng (tóm tắt/trích task, human-in-the-loop, proactive), mô tả từng màn hình.
- [docs/wireframes.html](docs/wireframes.html) — wireframe các màn hình chính (mở bằng trình duyệt).
- [docs/AI_LOG.md](docs/AI_LOG.md) — setup & bằng chứng hệ thống ghi log sử dụng AI trong repo.

## Tài liệu khác

- [CLAUDE.md](CLAUDE.md) — hướng dẫn chi tiết cho AI coding assistant làm việc trong repo này (quy ước code, lệnh chạy đầy đủ).
- [Frontend/README.md](Frontend/README.md) — hướng dẫn riêng cho frontend (cấu trúc, xử lý lỗi thường gặp khi chạy npm trên Windows).
- [Frontend/detai.md](Frontend/detai.md) — đề bài / yêu cầu gốc của dự án.
- [ARCHITECTURE.md](ARCHITECTURE.md) — kiến trúc hệ thống hiện tại và các quyết định công nghệ.
- [ROADMAP.md](ROADMAP.md) — bảng đối chiếu từng yêu cầu đề bài với trạng thái thật hiện tại + việc còn lại theo độ ưu tiên.
- [docs/deploy.md](docs/deploy.md) — hướng dẫn deploy production (Render + Supabase + Vercel + CD qua GitHub Actions), từng bước dashboard theo đúng thứ tự + checklist verify end-to-end.
- [WORKLOG.md](WORKLOG.md) — nhật ký công việc theo ngày của cả nhóm.
- [docs/guide/](docs/guide/) — tài liệu khóa học AI20K (setup, LangGraph, FastAPI, testing, deploy).
