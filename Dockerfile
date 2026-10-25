FROM python:3.11-slim

WORKDIR /app

# Cai dat cac thu vien he thong can thiet
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Cai dat Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toan bo ma nguon
COPY . .

# Chuan bi quyen thu muc cho app
RUN mkdir -p /app/data && \
    useradd -m -u 1000 user && \
    chown -R user:user /app

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PORT=8000

EXPOSE 8000

# Chay ung dung tren cong $PORT (mac dinh 8000)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
