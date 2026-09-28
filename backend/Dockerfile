FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements-postgres.txt
COPY . .
RUN useradd --uid 10001 --create-home appuser && mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["sh","-c","alembic upgrade head && exec python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --workers 1"]
