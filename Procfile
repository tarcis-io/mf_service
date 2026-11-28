# Cloud Run sets $PORT; bind it (fall back to 8000 locally).
web: uvicorn mf_service:app --host 0.0.0.0 --port ${PORT:-8000}
