FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Le modèle de recherche par le sens est embarqué : pas de téléchargement au premier usage.
ENV MODEL_DIR=/opt/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='/opt/models')"

COPY studio studio
COPY web web
COPY seed seed

ENV DATA_DIR=/data
EXPOSE 8000
CMD ["sh", "-c", "python -m studio serve --host 0.0.0.0 --port ${PORT:-8000}"]
