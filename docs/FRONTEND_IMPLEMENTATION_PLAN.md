# Kế hoạch Frontend và phần việc còn lại

> Cập nhật: 2026-08-12  
> Frontend thực tế hiện tại là một React/Vite app duy nhất trong `Frontend/`; không còn tách
> `Frontend/user` và `Frontend/admin` như bản kế hoạch cũ.

## 1. Cách chạy FE

Mở terminal riêng sau khi backend đã chạy:

```powershell
cd Frontend
npm install
npm run dev
```

Mở `http://localhost:5173`. Mặc định API và WebSocket trỏ về backend local:

- REST: `http://localhost:8000/api/v1`
- WebSocket: `ws://localhost:8000/api/v1/ws`

Nếu cần thay đổi, tạo `Frontend/.env` từ `Frontend/.env.example`. Chỉ cấu hình
`VITE_GOOGLE_CLIENT_ID` khi cần nút Google login.

## 2. Cấu trúc hiện tại

- `src/api/`: client cho auth, chat, agent, task, reminder, memory, calendar và admin.
- `src/context/AuthContext.jsx`: user hiện tại, token và auth lifecycle.
- `src/router/`: protected route và admin route.
- `src/pages/`: trang user và admin.
- `src/components/`: chat, AI panel, calendar, task, reminder, memory, profile và admin tables.
- `src/api/useWebSocket.js`: kết nối lại WebSocket và nhận realtime event.
- `package.json`: React 18, Vite, React Router, Bootstrap, FullCalendar, Framer Motion.

## 3. Trạng thái thực tế

| Phần | Trạng thái | Thành phần hiện tại |
|---|---|---|
| Vite app và layout | Đã có | `main.jsx`, `AppLayout.jsx` |
| Login/register | Đã có | `LoginPage`, `RegisterPage`, `api/auth.js` |
| JWT session và protected route | Đã có | `AuthContext`, `ProtectedRoute` |
| Google login | Đã có, cần env | `@react-oauth/google`, `LoginPage` |
| Chat 1-1/nhóm | Đã có | `ChatPage`, conversation/message components |
| WebSocket realtime/reconnect | Đã có | `useWebSocket.js`, layout toasts |
| AI permission và quick actions | Đã có | `AIPanel.jsx` |
| Personal Assistant | Đã có | `PersonalAssistantPage`, `PersonalAIChat` |
| Human-in-the-loop confirm/cancel | Đã có | `api/agent.js`, assistant/chat UI |
| Tasks và Task Inbox | Đã có | `TaskPage`, `TaskInboxPage`, `TaskTable` |
| Reminders và browser/in-app notification | Đã có | `ReminderPage`, reminder toast |
| Google Calendar UI | Đã có, cần OAuth backend | `CalendarPage`, calendar components |
| Memory CRUD | Đã có | `MemoryPage`, `MemoryModal` |
| Profile/settings | Đã có | `ProfilePage`, `SettingsSection` |
| Admin dashboard | Đã có trong cùng app | `/admin`, `/admin/users`, `/admin/conversations`, `/admin/user-data` |
| Responsive styling | Đã có ở mức hiện tại | `styles.css`, Bootstrap |
| Unit/integration/E2E test | Chưa có trong repository tối giản | Test files đã bị xóa khi dọn repo |
| Production build đã xác nhận | Đã kiểm tra ngày 2026-08-12 | `npm ci` và `npm run build` pass; npm audit báo 5 vulnerability của dependency |

## 4. Những điểm khác so với kế hoạch cũ

1. Kế hoạch cũ yêu cầu hai app user/admin độc lập; code hiện tại dùng một bundle và `AdminRoute`.
2. Kế hoạch cũ đề xuất TanStack Query, SSE Agent stream và state architecture riêng; package hiện
   tại không có TanStack Query, agent gọi REST `/chat` và `/chat/resume`, chưa có SSE.
3. Kế hoạch cũ để Google login ngoài MVP; code hiện tại đã có UI và API Google login, nhưng vẫn
   phụ thuộc `GOOGLE_OAUTH_CLIENT_ID` và `VITE_GOOGLE_CLIENT_ID`.
4. Kế hoạch cũ coi calendar nội bộ là MVP; code hiện tại dùng Google Calendar thật theo user,
   qua OAuth và polling ở backend.
5. Admin UI hiện có thêm quản lý task, reminder và memory; đây là phần đã triển khai ngoài các
   mục dashboard/user/conversation ban đầu.
6. Proactive task toast, priority inbox và budget alert đã có trong app layout, dù kế hoạch cũ
   xếp chúng vào milestone nâng cao.
7. Không còn CI/test trong repository tối giản, nên chưa được đánh dấu “đã nghiệm thu build/test”.

## 5. Kế hoạch kiểm tra theo tiến trình

### Giai đoạn 1 — Cài đặt và build

- [ ] Node.js 18+ và npm hoạt động.
- [x] `npm ci` hoàn tất trong `Frontend/`.
- [x] `npm run build` hoàn tất không lỗi ngày 2026-08-12.
- [ ] `npm run dev` mở được `http://localhost:5173`.
- [ ] Không commit `node_modules`, `dist` hoặc file `.env`.

### Giai đoạn 2 — Auth và shell

- [ ] Backend đã trả `/health` trước khi mở FE.
- [ ] Register tạo tài khoản mới.
- [ ] Login đưa user vào `/assistant` hoặc `/chat`.
- [ ] Refresh/reload vẫn giữ session hợp lệ.
- [ ] Logout xóa session và quay về `/login`.
- [ ] User thường không truy cập được `/admin`.

### Giai đoạn 3 — User core

- [ ] Tạo conversation 1-1 và group.
- [ ] Gửi/nhận message và kiểm tra reconnect WebSocket.
- [ ] Đánh dấu đã đọc và kiểm tra unread count.
- [ ] Grant/revoke AI permission.
- [ ] Chat với assistant, thử summarize/extract và confirm/cancel action.

### Giai đoạn 4 — Productivity

- [ ] Tạo, đổi trạng thái và xóa task.
- [ ] Kiểm tra Task Inbox: overdue, sắp đến hạn, priority và AI suggestions.
- [ ] Tạo/xóa reminder và chờ toast notification.
- [ ] Thêm/sửa/xóa memory.
- [ ] Cập nhật profile, timezone và password.
- [ ] Nếu đã cấu hình Google: connect calendar, tạo/sửa/xóa event và kiểm tra popup callback.

### Giai đoạn 5 — Admin và realtime

- [ ] Đăng nhập bằng email nằm trong `INITIAL_ADMIN_EMAIL`.
- [ ] Kiểm tra dashboard stats.
- [ ] Tìm user, đổi role và khóa/mở khóa user.
- [ ] Xem/xóa conversation, task, reminder và memory từ admin.
- [ ] Mở hai tab để kiểm tra task suggestion, reminder và budget alert realtime.

## 6. Phần việc còn lại

- [ ] Cài dependency và xác nhận `npm run build` trên máy phát triển/CI.
- [ ] Thêm test component/API hoặc khôi phục test suite ở nhánh phát triển.
- [ ] Chuẩn hóa trạng thái loading, empty, error và retry cho từng API.
- [ ] Cải thiện accessibility: keyboard navigation, focus trap cho modal và nhãn form.
- [ ] Tách cấu hình production, dùng `https://` và `wss://` khi deploy.
- [ ] Thêm SPA rewrite cho hosting frontend nếu nền tảng không tự hỗ trợ route history.
- [ ] Chỉ triển khai sau khi backend production URL, CORS và OAuth redirect URI đã thống nhất.

## 7. Tiêu chí hoàn thành frontend hiện tại

- `npm run build` thành công.
- Các route public/protected/admin điều hướng đúng.
- User hoàn thành được auth, chat, assistant, task, reminder, memory và profile.
- Admin chỉ hiển thị với role admin và gọi được admin API.
- FE nhận được WebSocket event khi backend đang chạy.
- Các tính năng Google được đánh dấu phụ thuộc cấu hình, không coi là lỗi code khi env chưa có.
