# Backend + frontend in one container. For any container host (Fly, Render, Cloud Run, Cloudflare Containers).
FROM python:3.13-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY frontend frontend
COPY sst sst
COPY providers providers
COPY eval eval
RUN mkdir -p data/packets data/pages data/uploads data/runs
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT}"]
