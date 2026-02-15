# TTS Voice Cloning Service

Servicio de clonación de voz usando XTTS-v2 con soporte para RTX 5090.

## 🚀 Inicio Rápido (Desarrollo Local)

### 1. Configuración
```bash
# Copiar archivo de ejemplo
cp .env.example .env

# Editar variables según tu entorno
nano .env
```

### 2. Instalación
```bash
chmod +x install.sh start.sh
./install.sh
```

### 3. Iniciar Servidor
```bash
./start.sh
```

El servidor estará disponible en `http://localhost:5002`

## 🔧 Variables de Entorno

Crea un archivo `.env` basado en `.env.example`:

```bash
# Puerto del servidor
PORT=5002

# URLs permitidas para CORS (separadas por coma)
ALLOWED_ORIGINS=http://localhost:3004

# Modo de ejecución
ENVIRONMENT=development

# Directorios
OUTPUT_DIR=outputs
TEMP_DIR=temp_uploads
VOICES_DIR=voice_gallery
VOICES_DB=voices_db.json

# Configuración de XTTS
XTTS_MODEL=tts_models/multilingual/multi-dataset/xtts_v2

# GPU
CUDA_VISIBLE_DEVICES=0

# Flask
FLASK_DEBUG=false
FLASK_SECRET_KEY=your-secret-key-here-change-in-production

# Rate limit por usuario/IP
RATE_LIMIT_MAX_REQUESTS=5
RATE_LIMIT_WINDOW_SECONDS=86400
```

## 🐳 Deploy en Coolify

### 1. Subir a GitHub:
```bash
git init
git add .
git commit -m "TTS Service"
git remote add origin https://github.com/tu-usuario/tts-service.git
git push -u origin main
```

### 2. En Coolify:
   - Click en "New Resource" → "Docker Container"
   - Selecciona tu repositorio
   - **Build Pack:** Dockerfile
   - **Puerto:** 5002
   
### 3. Variables de Entorno en Coolify:
En la sección "Environment Variables", agrega:

```
PORT=5002
ALLOWED_ORIGINS=https://tu-frontend.com
ENVIRONMENT=production
FLASK_SECRET_KEY=tu-secret-key-super-seguro-aqui
CUDA_VISIBLE_DEVICES=0
XTTS_MODEL=tts_models/multilingual/multi-dataset/xtts_v2
```

### 4. Configurar GPU:
En "Advanced" → "Docker Options":
```json
{
  "deploy": {
    "resources": {
      "reservations": {
        "devices": [
          {
            "driver": "nvidia",
            "count": 1,
            "capabilities": ["gpu"]
          }
        ]
      }
    }
  }
}
```

### 5. Deploy:
   - Click "Deploy"
   - Espera 5-10 minutos (primera vez descarga modelo XTTS)

## 📡 Endpoints (cola asíncrona 1x1)

### POST /clone
Encola un job de clonación de voz y retorna `job_id`
```bash
curl -X POST http://localhost:5002/clone \
  -F "audio=@voz.mp3" \
  -F "text=Hola mundo" \
  -F "language=es" \
  -F "temperature=0.75" \
  -F "speed=1.0"
```

Respuesta esperada:
```json
{
  "job_id": "uuid",
  "status": "queued",
  "status_url": "/jobs/{job_id}",
  "result_url": "/jobs/{job_id}/result"
}
```

### GET /jobs/{job_id}
Consulta estado del job (`queued`, `processing`, `completed`, `failed`)

### GET /jobs/{job_id}/result
Descarga el WAV cuando el job esté en `completed`

### GET /queue/status
Estado global de cola y worker

### GET /voices
Lista voces guardadas

### POST /voices
Guarda voz en galería

### POST /voices/{id}/use
Encola job para usar una voz de galería

## 🎛️ Parámetros de Calidad

- **temperature** (0.1-1.0): 0.65-0.75 recomendado para mejor similitud
- **speed** (0.5-2.0): 1.0 = velocidad normal
- **Audio de referencia:** 3-10 segundos recomendado

## 🔧 Configuración

Edita `api.py` para cambiar el puerto:
```python
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=True)
```
