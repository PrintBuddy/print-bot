# Base image
FROM python:3.12-slim

# Evitar warnings de buffer
ENV PYTHONUNBUFFERED=1

# Instalar dependencias del sistema si las necesitas (opcional)
# RUN apt-get update && apt-get install -y <paquetes>

# Crear directorio de la app
WORKDIR /app

# Copiar requirements e instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todo el código del bot
COPY . .

# Comando para ejecutar el bot
CMD ["python", "-m", "src.main"]
