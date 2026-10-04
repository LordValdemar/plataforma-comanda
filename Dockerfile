FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PASTA_DADOS=/dados \
    PORTA=5000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY propagandas ./propagandas
COPY servidor.py gerenciar.py ./

# Roda sem privilégios de administrador.
RUN useradd --system --uid 1000 painel && mkdir -p /dados && chown painel /dados
USER painel

VOLUME ["/dados"]
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/saude', timeout=4)"

CMD ["python", "servidor.py"]
