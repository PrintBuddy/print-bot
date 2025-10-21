#!/bin/bash
set -e

# Nombre de la imagen y del contenedor
IMAGE_NAME="print-bot"
CONTAINER_NAME="print-bot"

# Ruta absoluta a la carpeta del bot
BOT_PATH="/home/archimind/print-bot"

echo "🚀 Deploying Telegram bot..."

# Ir a la carpeta del bot
cd "$BOT_PATH"

# Construir la imagen
echo "🔧 Building Docker image..."
docker build -t $IMAGE_NAME .

# Detener el contenedor previo (si existe)
if [ "$(docker ps -q -f name=$CONTAINER_NAME)" ]; then
    echo "🛑 Stopping existing container..."
    docker stop $CONTAINER_NAME
fi

# Eliminar contenedor previo (si existe)
if [ "$(docker ps -a -q -f name=$CONTAINER_NAME)" ]; then
    echo "🧹 Removing old container..."
    docker rm $CONTAINER_NAME
fi

# Cargar variables de entorno desde .env (opcional)
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Ejecutar el nuevo contenedor
echo "🐳 Starting new container..."
docker run -d \
  --name $CONTAINER_NAME \
  --env-file .env \
  --restart unless-stopped \
  $IMAGE_NAME

echo "✅ Bot deployed successfully!"