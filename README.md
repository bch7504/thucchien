# Orbit AI Assistant

Ứng dụng gồm backend FastAPI/LangGraph và frontend React/Vite.

## Yêu cầu

- Python 3.11+
- Node.js 18+
- PostgreSQL

## Chạy backend

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Điền `DATABASE_URL`, khóa LLM và các biến cần thiết trong `.env`, sau đó chạy:

```powershell
python scripts/run_dev.py
```

Backend chạy tại http://localhost:8000.

## Chạy frontend

Mở terminal khác:

```powershell
cd Frontend
npm install
npm run dev
```

Frontend chạy tại http://localhost:5173.

Xem [kế hoạch Backend](docs/BE_AND_REMAINING_IMPLEMENTATION_PLAN.md) và
[kế hoạch Frontend](docs/FRONTEND_IMPLEMENTATION_PLAN.md) để chạy theo từng bước.

## Chạy bằng Docker

Tạo `.env` từ `.env.example`, điền cấu hình rồi chạy:

```powershell
docker compose up --build
```
