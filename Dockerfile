FROM python:3.12-slim

WORKDIR /app

# Instala dependencias del bot
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia el código fuente
COPY . .

# Comando de inicio
CMD ["python", "-m", "src.main"]
