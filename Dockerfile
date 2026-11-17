FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt && useradd --uid 10001 --create-home branchframe
COPY branchframe ./branchframe
COPY web ./web
RUN mkdir /data && chown branchframe:branchframe /data
USER branchframe
ENV BRANCHFRAME_DATA=/data PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl --fail http://127.0.0.1:8000/api/health || exit 1
CMD ["python", "-m", "branchframe.server", "--host", "0.0.0.0", "--workers", "2"]
