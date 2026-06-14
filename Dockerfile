# Lipidmix_with_LLM — NAS常駐用 MCPサーバー（streamable-http）
FROM python:3.12-slim

WORKDIR /app

# 依存インストール（レイヤキャッシュのため requirements を先に）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリ本体（.dockerignore で venv/データ等は除外）
COPY . .

# matplotlib をヘッドレス動作させる
ENV MPLBACKEND=Agg

# HTTP 常駐の既定値（compose 側で上書き可）
ENV LIPIDMIX_TRANSPORT=streamable-http \
    LIPIDMIX_HOST=0.0.0.0 \
    LIPIDMIX_PORT=8000

EXPOSE 8000

CMD ["python", "server.py"]
