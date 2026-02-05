#!/bin/bash

# Script de inicio rápido para TTS Service
# Uso: ./start.sh

echo "🚀 Iniciando TTS Service..."

# Verificar si existe el entorno virtual
if [ ! -d "venv" ]; then
    echo "⚠️  No se encontró el entorno virtual. Ejecuta ./install.sh primero"
    exit 1
fi

# Activar entorno virtual
source venv/bin/activate

# Verificar instalación de dependencias
echo "✓ Entorno virtual activado"

# Crear directorios necesarios
mkdir -p outputs temp_uploads voice_gallery

# Iniciar servidor
echo "✓ Iniciando servidor en puerto 5000..."
python api.py
