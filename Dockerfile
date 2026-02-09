# Usar imagen base de Python con CUDA
FROM nvidia/cuda:12.8.0-runtime-ubuntu22.04

# Instalar Python 3.11
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3-pip \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Establecer directorio de trabajo
WORKDIR /app

# Copiar requirements
COPY requirements.txt .

# Crear entorno virtual e instalar dependencias
RUN python3.11 -m venv venv
RUN . venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt

# Copiar código de la aplicación
COPY . .

# Crear directorios necesarios
RUN mkdir -p outputs temp_uploads voice_gallery

# Exponer puerto
EXPOSE 4000

# Variables de entorno
ENV PYTHONUNBUFFERED=1
ENV FLASK_PORT=4000

# Comando de inicio
CMD ["venv/bin/python", "api.py"]
