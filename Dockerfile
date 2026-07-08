FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt* ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt || \
    pip install --no-cache-dir fastapi uvicorn[standard] redis pandas numpy streamlit plotly

COPY . .

EXPOSE 8000 8501

CMD ["python", "-m", "spire_reactor.main"]
