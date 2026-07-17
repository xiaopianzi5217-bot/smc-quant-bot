FROM python:3.11-slim

USER root

ENV TZ=Asia/Shanghai
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
# HF Space 构建超时保护：限制 pip 下载时间
ENV PIP_TIMEOUT=120

# ===== 1. 系统依赖 =====
RUN apt-get update && apt-get install -y \
    curl \
    tzdata \
    && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo Asia/Shanghai > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# ===== 2. 安装 Python 依赖 =====
WORKDIR /app

COPY requirements.txt .
# 分步安装避免单个包超时，lightgbm 放最后
RUN pip install --no-cache-dir --timeout 120 \
    gradio pandas numpy requests urllib3 python-dotenv scikit-learn \
    && pip install --no-cache-dir --timeout 120 \
    websockets aiohttp \
    && pip install --no-cache-dir --timeout 120 \
    plotly ccxt cryptography \
    && pip install --no-cache-dir --timeout 300 \
    lightgbm 2>&1 || echo "[WARN] lightgbm 安装跳过（运行时按需加载）"

# ===== 3. 复制项目源码 =====
COPY . .

EXPOSE 7860

CMD ["python", "app.py"]