FROM python:3.11-slim AS builder

USER root

ENV TZ=Asia/Shanghai
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# ===== 1. 系统依赖（仅最小必要） =====
RUN apt-get update && apt-get install -y \
    curl \
    tzdata \
    && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo Asia/Shanghai > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# ===== 2. 先复制 requirements 并安装 Python 依赖（利用 Docker 缓存） =====
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ===== 3. 复制项目源码 =====
COPY . .

EXPOSE 7860

CMD ["python", "app.py"]