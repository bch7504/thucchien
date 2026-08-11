# Deploy online — Render (backend) + Supabase (Postgres) + Vercel (frontend)

Hướng dẫn deploy production thật cho Orbit. Đây là hạng mục ưu tiên #1 còn thiếu theo
[ROADMAP.md](../ROADMAP.md)/[ARCHITECTURE.md](../ARCHITECTURE.md) — đề bài gốc
([Frontend/detai.md](../Frontend/detai.md)) yêu cầu bắt buộc "App deploy online, đăng nhập, ≥2 vai
trò".

Stack đã chốt: **Render** (backend, build từ [Dockerfile](../Dockerfile) hiện có) + **Supabase**
(Postgres managed, free tier vĩnh viễn) + **Vercel** (frontend Vite) + **GitHub Actions** làm CD,
gate deploy sau khi CI (lint+test) pass trên `main`. Render free tier tự sleep sau 15 phút không
request (kéo theo APScheduler trong process — reminders, calendar polling — cũng dừng theo), nên có
thêm 1 workflow keep-alive ping `/health` mỗi ~10 phút để giảm thiểu (không loại bỏ hoàn toàn) rủi ro
reminder trễ giờ.

File liên quan đã có sẵn trong repo, không cần tạo thêm gì ở bước thao tác dashboard dưới đây:
[Dockerfile](../Dockerfile) (CMD dùng `$PORT`), [render.yaml](../render.yaml) (Render Blueprint),
[.github/workflows/deploy.yml](../.github/workflows/deploy.yml) (CD),
[.github/workflows/keep-alive.yml](../.github/workflows/keep-alive.yml) (chống sleep),
[.github/workflows/ci.yml](../.github/workflows/ci.yml) (đã có Postgres service container),
[Frontend/vercel.json](../Frontend/vercel.json) (SPA rewrite),
[src/db/session.py](../src/db/session.py) (SSL cho asyncpg khi nối Supabase).

## Các bước thủ công bắt buộc (trình duyệt/dashboard), ĐÚNG THỨ TỰ

Không AI coding assistant nào thao tác được trình duyệt — toàn bộ mục này người có quyền truy cập
GitHub repo + tạo được account Render/Supabase/Vercel/Google Cloud Console phải tự làm.

**Bước 0:** Merge xong PR chứa các file ở trên vào `main`, xác nhận `ci.yml` XANH THẬT trên `main`
(không chỉ trên PR) — CI trước đây thiếu Postgres service, gate "deploy sau khi CI pass" vô nghĩa nếu
CI chưa từng thực sự chạy được.

1. **Chốt tên trước** (quyết định URL trước khi tạo gì cả): Render service name (vd `orbit-backend`
   → `https://orbit-backend.onrender.com`), Vercel project name (vd `orbit-frontend` →
   `https://orbit-frontend.vercel.app`). Ghi lại 2 URL này, dùng xuyên suốt các bước dưới.
2. **Tạo Supabase project**: supabase.com → New project → region gần nhất (Singapore nếu có) → đặt
   mật khẩu DB mạnh, lưu lại → đợi provisioning xong (vài phút).
3. **Lấy connection string — chọn đúng "Session pooler"**: Project Settings → Database →
   "Connection string" → tab **Session pooler** (KHÔNG "Direct connection" — thường IPv6-only, Render
   không connect được; KHÔNG "Transaction pooler" port 6543 — phá server-side prepared statement mà
   asyncpg dùng mặc định, trừ khi sửa thêm code) → copy URI, thay `[YOUR-PASSWORD]` bằng mật khẩu
   thật ở bước 2. **Không thêm `?ssl=...`/`?sslmode=...` vào chuỗi này** — SSL đã được xử lý riêng
   cho từng driver trong [src/db/session.py](../src/db/session.py) (asyncpg dùng `ssl` kwarg,
   psycopg dùng `sslmode` kiểu libpq; 1 query-param chung sẽ phá 1 trong 2). Đây là giá trị
   `DATABASE_URL`.
4. **Generate 2 secret** (chạy local, có sẵn để dán ở bước 5):
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"          # -> SECRET_KEY
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # -> CREDENTIAL_ENCRYPTION_KEY
   ```
5. **Tạo Render service qua Blueprint**: render.com → New + → Blueprint → connect GitHub repo →
   Render đọc [render.yaml](../render.yaml), preview 1 web service tên đã chốt ở bước 1 → Apply. Điền
   các biến `sync: false` khi được hỏi:
   - `DATABASE_URL` = bước 3
   - `SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY` = bước 4
   - `GOOGLE_API_KEY` (hoặc `GROQ_API_KEY`/`OPENAI_API_KEY` tuỳ `LLM_PROVIDER`), `GOOGLE_OAUTH_CLIENT_ID`,
     `GOOGLE_CALENDAR_CLIENT_ID`, `GOOGLE_CALENDAR_CLIENT_SECRET` = giá trị thật của nhóm
   - `INITIAL_ADMIN_EMAIL` = email sẽ làm admin đầu tiên trên production (xác nhận lại với nhóm)
   - `GOOGLE_CALENDAR_REDIRECT_URI` = `https://<render-url>/api/v1/calendar/oauth/callback`
   - `CORS_ORIGINS` = `https://<vercel-url>` (không khoảng trắng nếu sau này thêm nhiều origin —
     `src/main.py` dùng `.split(",")` không `.strip()`)
   - `FRONTEND_ORIGIN` = `https://<vercel-url>`

   Xác nhận Settings → Auto-Deploy hiện **"Off"** (đã khai `autoDeployTrigger: off` trong
   `render.yaml` — `deploy.yml` là đường deploy duy nhất, tránh chồng chéo 2 cơ chế deploy).
6. **Lấy Deploy Hook**: Service vừa tạo → Settings → mục "Deploy Hook" → copy URL → GitHub repo →
   Settings → Secrets and variables → Actions → New repository **secret** tên
   `RENDER_DEPLOY_HOOK_URL`. Đồng thời tạo repository **Variable** (tab "Variables" cạnh "Secrets")
   tên `RENDER_URL`, giá trị `https://<render-url>` (không có dấu `/` cuối) — dùng chung cho
   `deploy.yml` và `keep-alive.yml`.
7. **Deploy lần đầu (thủ công)**: vì `autoDeployTrigger: off` và `deploy.yml` chỉ kích hoạt được sau
   khi chính nó đã nằm trên `main` (`workflow_run` không trigger được từ chính lần merge đầu tiên
   thêm file này), lần đầu phải trigger tay: Render dashboard → Manual Deploy → "Deploy latest
   commit" — HOẶC tab Actions của repo → workflow "Deploy" → "Run workflow"
   (`workflow_dispatch`). Theo dõi Logs tab trên Render: build Docker xong → thấy log
   `Starting AI20K Agent in production mode` → không traceback → service chuyển "Live".
8. **Verify backend sống**: `curl https://<render-url>/health` → kỳ vọng
   `{"status":"ok","env":"production"}`. Lỗi/treo lâu → xem Render Logs tab tìm traceback từ
   `init_db()` (sai `DATABASE_URL`/SSL) hoặc `init_checkpointer()` (sai psycopg conninfo).
9. **Tạo Vercel project**: vercel.com → Add New → Project → import repo → **Root Directory =
   `Frontend`** (bắt buộc, không phải root repo) → Framework Preset tự nhận "Vite" (Build Command
   `npm run build`, Output `dist` tự điền) → trước khi Deploy, thêm Environment Variables:
   - `VITE_API_BASE_URL` = `https://<render-url>/api/v1`
   - `VITE_WS_BASE_URL` = `wss://<render-url>/api/v1/ws` (chú ý `wss://` không phải `ws://`)
   - `VITE_GOOGLE_CLIENT_ID` = cùng giá trị `GOOGLE_OAUTH_CLIENT_ID` đã set ở bước 5

   Deploy. Xác nhận URL ra đúng dự đoán ở bước 1 (nếu Vercel tự thêm hậu tố do trùng tên, quay lại
   sửa `CORS_ORIGINS`/`FRONTEND_ORIGIN` ở bước 5 cho khớp URL thật).
10. **Verify frontend build đúng SPA routing**: mở thẳng `https://<vercel-url>/tasks/inbox` (không
    qua điều hướng từ trang chủ) hoặc F5 giữa chừng ở 1 route con → phải load được app, không phải
    404 của Vercel (xác nhận [Frontend/vercel.json](../Frontend/vercel.json) áp dụng đúng —
    `AppRouter.jsx` dùng `BrowserRouter`, không có rewrite sẽ 404 mọi route con).
11. **Cập nhật Google Cloud Console** (2 OAuth Client riêng, xem `.env.example` để biết chỗ tạo) —
    console.cloud.google.com/apis/credentials của dự án:
    - Client dùng cho `GOOGLE_OAUTH_CLIENT_ID` ("Sign in with Google"): mở edit → **Authorized
      JavaScript origins** → Add `https://<vercel-url>` → Save.
    - Client dùng cho `GOOGLE_CALENDAR_CLIENT_ID` ("Connect Google Calendar"): mở edit →
      **Authorized redirect URIs** → Add `https://<render-url>/api/v1/calendar/oauth/callback`
      (khớp EXACT với `GOOGLE_CALENDAR_REDIRECT_URI` đã set ở bước 5, kể cả dấu `/` cuối) → Save.
    - Nếu OAuth consent screen của Calendar Client còn ở "Testing": xác nhận mọi tài khoản Google sẽ
      dùng để test/demo production đã có trong danh sách "Test users" — nếu không sẽ bị
      `access_denied` dù mọi thứ khác đúng.
    - Thay đổi có thể mất vài phút–vài giờ để Google áp dụng — đừng vội nghi code sai nếu vừa save
      xong mà login/connect calendar vẫn lỗi.
12. **Kích hoạt CD**: đảm bảo `RENDER_DEPLOY_HOOK_URL` (secret) và `RENDER_URL` (variable) đã set ở
    bước 6. Push 1 commit bất kỳ lên `main` → tab Actions: `CI` chạy → xanh → `Deploy` tự trigger qua
    `workflow_run` → job `deploy-backend` gọi Deploy Hook → step "Wait for backend to become healthy"
    poll `/health` tới khi 200 → xanh toàn bộ. Đối chiếu Render dashboard phải thấy 1 deploy event
    mới đúng thời điểm Actions job chạy.
13. **Xác nhận keep-alive**: tab Actions → workflow "Keep Render Awake" → "Run workflow" (chạy tay 1
    lần để verify ngay, không đợi cron) → log phải show `curl` thành công.

## End-to-end verification trên production

Thực hiện trên chính `https://<vercel-url>`, KHÔNG dùng localhost:

1. Đăng ký tài khoản mới (email/password) → không có lỗi CORS trong DevTools Console (nếu
   `CORS_ORIGINS` sai, request `/auth/register` bị browser chặn với lỗi CORS rõ ràng, không phải lỗi
   500 từ server).
2. Đăng nhập lại bằng tài khoản vừa tạo → vào được các trang protected route.
3. Thử "Sign in with Google" (nếu đã enable) → không lỗi origin/`redirect_uri_mismatch` ngay trên
   popup Google.
4. Gửi 1 tin nhắn chat, mở AI panel, bật quyền AI đọc hội thoại (`ai_permissions`), yêu cầu AI "tóm
   tắt hội thoại" → có phản hồi thật từ LLM (không phải 503/timeout — nếu treo lâu, kiểm tra
   `GOOGLE_API_KEY`/`LLM_PROVIDER` đã set đúng trên Render).
5. Yêu cầu AI trích xuất task/tạo reminder có ngày giờ cụ thể ("nhắc tôi ngày mai 9h sáng...") → flow
   human-in-the-loop (confirm trước khi tạo) chạy đúng, và giờ tạo ra đúng giờ Hà Nội (không lệch 7
   tiếng — Supabase Postgres mặc định UTC khác máy dev local).
6. Vào `/calendar`, bấm "Connect Google Calendar" → redirect qua Google thật, quay lại app, event
   Google Calendar thật xuất hiện đúng — bài test quan trọng nhất cho bước 11 (Google Console).
7. Tạo task/nhắc việc qua UI thường (không qua AI) → kiểm tra Supabase dashboard → Table Editor có
   row mới (xác nhận `init_db()`'s `create_all()` đã tự tạo đúng schema trên Supabase).
8. Mở 2 tab, xác nhận có 1 sự kiện realtime (vd reminder mới, proactive task suggestion) đẩy qua tab
   kia không cần refresh — xác nhận `VITE_WS_BASE_URL` dùng đúng `wss://` (để nhầm `ws://` trên domain
   HTTPS, browser block mixed-content, WebSocket không bao giờ connect được — thường là lỗi im lặng,
   chỉ thấy trong DevTools Network tab).
9. Đăng nhập bằng tài khoản admin (`INITIAL_ADMIN_EMAIL`), vào `/admin`, xác nhận Dashboard/Users/
   Conversations load đúng dữ liệu thật, ≥2 role (user thường không thấy menu Admin).
10. Đợi ~16 phút không có request nào (hoặc tạm tắt keep-alive để test), gửi lại 1 request bất kỳ →
    vẫn chạy được, chỉ chậm hơn (cold start), không lỗi cứng.

## Rủi ro & Rollback

| Tình huống | Xử lý |
| --- | --- |
| Deploy Hook trigger nhưng health check fail | Render không swap traffic sang bản mới — bản cũ tiếp tục chạy, không downtime. Xem Render Logs của deploy event bị fail, sửa lỗi (thường env var sai hoặc code lỗi), deploy lại. |
| Bug phát hiện ngay sau khi deploy | `init_db()` dùng `create_all()` — chỉ thêm bảng/cột, không tự xoá/đổi tên gì, nên rollback code về version trước hoàn toàn an toàn với schema hiện tại. Render: tab "Deploys" → chọn deploy trước → "Rollback to this deploy" (1 click). Vercel: tab "Deployments" → "..." → "Promote to Production" trên bản cũ. |
| Tương lai đổi/xoá tên cột (ngoài phạm vi tài liệu này) | `create_all()` không xử lý được rename/drop cột — nợ kỹ thuật đã biết, Alembic bị hoãn có chủ đích (xem [ROADMAP.md](../ROADMAP.md)). |
| `DATABASE_URL` sai (Direct connection thay vì Session pooler, gõ nhầm ký tự) | Backend crash ngay ở `init_db()`/`init_checkpointer()` trong lifespan, healthcheck luôn fail. Supabase không bị ảnh hưởng gì — sửa lại `DATABASE_URL` trên Render dashboard, Manual Deploy lại. |
| Keep-alive workflow bị GitHub tự tắt sau 60 ngày repo im lặng | Kiểm tra định kỳ tab Actions còn "Active" không; push 1 commit bất kỳ hoặc bật tay lại. |
| `workflow_run` không bao giờ trigger `deploy.yml` | Dùng `workflow_dispatch` (đã có sẵn) để chạy tay bất cứ lúc nào, không phụ thuộc trigger tự động. |
| Google OAuth lỗi trên production dù code/env đúng hết | Thường do bước 11 (Google Console) bị bỏ sót hoặc chưa propagate, dễ nhầm là lỗi deploy — luôn test riêng bước 3 và 6 ở phần verification sau mỗi lần đổi domain. |

## Ngoài phạm vi tài liệu này (cố ý, không làm ở đây)

Alembic migrations, rate limiting, structured logging/monitoring, global exception handler, security
headers — đã ghi nhận là nợ kỹ thuật ở [ROADMAP.md](../ROADMAP.md)/[ARCHITECTURE.md](../ARCHITECTURE.md),
để lại cho phiên làm việc riêng sau khi deploy ổn định. Webhook `events.watch` thật cho Calendar
(thay polling) có thể làm ngay sau khi có domain public HTTPS thật từ tài liệu này, nhưng là 1 thay
đổi tách biệt.
