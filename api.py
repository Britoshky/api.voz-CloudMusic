from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from tts_engine import TTSEngine
import os
import uuid
import threading
import time
import base64
import hashlib
from urllib import request as urllib_request
from urllib import parse as urllib_parse
import soundfile as sf
import librosa
import json
from dotenv import load_dotenv

try:
    import redis
except ImportError:
    redis = None

try:
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
except ImportError:
    load_pem_public_key = None

# Cargar variables de entorno
load_dotenv()

app = Flask(__name__)

# Configurar CORS desde variables de entorno
allowed_origins = os.getenv('ALLOWED_ORIGINS', 'http://localhost:3000').split(',')
CORS(app, origins=allowed_origins)

# Configurar Flask
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-me')
app.config['DEBUG'] = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'

def _get_bool_env(name, default='false'):
    return os.getenv(name, default).strip().lower() in {'1', 'true', 'yes', 'on'}

# Allowlist de frontends (defensa en profundidad; solo se evalúa si vienen headers Origin/Referer)
ALLOWED_FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv('ALLOWED_FRONTEND_ORIGINS', os.getenv('ALLOWED_ORIGINS', 'https://ai.cloudmusic.cl')).split(',')
    if origin.strip()
]

# Firma frontend -> backend (Ed25519)
REQUIRE_SIGNED_REQUESTS = _get_bool_env('REQUIRE_SIGNED_REQUESTS', 'true')
SIGNATURE_ALGORITHM = os.getenv('SIGNATURE_ALGORITHM', 'ed25519').strip().lower()
FRONTEND_KEY_ID = os.getenv('FRONTEND_KEY_ID', 'tts-frontend').strip()
FRONTEND_PUBLIC_KEY_PEM = os.getenv('FRONTEND_PUBLIC_KEY_PEM', '').strip()
SIGNATURE_MAX_AGE_SECONDS = int(os.getenv('SIGNATURE_MAX_AGE_SECONDS', '300'))

# Turnstile (opcional en local, recomendado en producción)
REQUIRE_TURNSTILE = _get_bool_env('REQUIRE_TURNSTILE', 'true')
TURNSTILE_SECRET_KEY = os.getenv('TURNSTILE_SECRET_KEY', '').strip()
TURNSTILE_VERIFY_URL = os.getenv('TURNSTILE_VERIFY_URL', 'https://challenges.cloudflare.com/turnstile/v0/siteverify').strip()

# Redis
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0').strip()
REDIS_RATE_LIMIT_PREFIX = os.getenv('REDIS_RATE_LIMIT_PREFIX', 'tts:rate').strip()
REDIS_NONCE_PREFIX = os.getenv('REDIS_NONCE_PREFIX', 'tts:nonce').strip()

# Rate limiting (5 peticiones por usuario/IP por defecto)
RATE_LIMIT_MAX_REQUESTS = int(os.getenv('RATE_LIMIT_MAX_REQUESTS', '5'))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv('RATE_LIMIT_WINDOW_SECONDS', '86400'))
RATE_LIMIT_EXEMPT_PATHS = {'/health', '/queue/status'}
rate_limit_counters = {}
rate_limit_lock = threading.Lock()

redis_client = None
if redis and REDIS_URL:
    try:
        redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        redis_client.ping()
        print(f"Redis conectado: {REDIS_URL}")
    except Exception as redis_error:
        redis_client = None
        print(f"Redis no disponible (fallback memoria): {redis_error}")

nonce_cache = {}
nonce_cache_lock = threading.Lock()

# Inicializar engine TTS
engine = TTSEngine()

# Directorios desde variables de entorno
OUTPUT_DIR = os.getenv('OUTPUT_DIR', 'outputs')
TEMP_DIR = os.getenv('TEMP_DIR', 'temp_uploads')
VOICES_DIR = os.getenv('VOICES_DIR', 'voice_gallery')
VOICES_DB = os.getenv('VOICES_DB', 'voices_db.json')

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(VOICES_DIR, exist_ok=True)

def get_client_identifier():
    """Obtiene un identificador de cliente usando header opcional o IP real."""
    user_header = request.headers.get('X-User-Id')
    if user_header:
        return f"user:{user_header}"

    forwarded_for = request.headers.get('X-Forwarded-For', '')
    if forwarded_for:
        client_ip = forwarded_for.split(',')[0].strip()
        if client_ip:
            return f"ip:{client_ip}"

    real_ip = request.headers.get('X-Real-IP')
    if real_ip:
        return f"ip:{real_ip}"

    return f"ip:{request.remote_addr or 'unknown'}"

def _is_origin_allowed(origin):
    return any(origin == allowed for allowed in ALLOWED_FRONTEND_ORIGINS)

def _is_referer_allowed(referer):
    return any(referer.startswith(f"{allowed}/") or referer == allowed for allowed in ALLOWED_FRONTEND_ORIGINS)

def _get_request_body_sha256():
    body = request.get_data(cache=True) or b''
    return hashlib.sha256(body).hexdigest()

def _get_public_key():
    if SIGNATURE_ALGORITHM != 'ed25519':
        return None
    if not load_pem_public_key:
        return None
    if not FRONTEND_PUBLIC_KEY_PEM:
        return None
    pem = FRONTEND_PUBLIC_KEY_PEM.replace('\\n', '\n').encode('utf-8')
    return load_pem_public_key(pem)

def _cleanup_nonce_cache(now):
    expired = [nonce for nonce, expiry in nonce_cache.items() if expiry <= now]
    for nonce in expired:
        nonce_cache.pop(nonce, None)

def _register_nonce_once(nonce, now):
    if redis_client:
        nonce_key = f"{REDIS_NONCE_PREFIX}:{nonce}"
        try:
            was_set = redis_client.set(nonce_key, '1', nx=True, ex=SIGNATURE_MAX_AGE_SECONDS)
            return bool(was_set)
        except Exception:
            pass

    with nonce_cache_lock:
        _cleanup_nonce_cache(now)
        if nonce in nonce_cache:
            return False
        nonce_cache[nonce] = now + SIGNATURE_MAX_AGE_SECONDS
        return True

def _verify_turnstile_token(token, remote_ip):
    if not TURNSTILE_SECRET_KEY:
        return False, 'TURNSTILE_SECRET_KEY no configurado'

    payload = {
        'secret': TURNSTILE_SECRET_KEY,
        'response': token,
    }
    if remote_ip:
        payload['remoteip'] = remote_ip

    encoded = urllib_parse.urlencode(payload).encode('utf-8')
    req = urllib_request.Request(
        TURNSTILE_VERIFY_URL,
        data=encoded,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST'
    )

    try:
        with urllib_request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode('utf-8'))
    except Exception as verify_error:
        return False, f'Error validando Turnstile: {verify_error}'

    if data.get('success'):
        return True, None

    error_codes = data.get('error-codes') or ['turnstile-validation-failed']
    return False, ','.join(error_codes)

@app.before_request
def enforce_origin_and_referer():
    if request.method == 'OPTIONS' or request.path in RATE_LIMIT_EXEMPT_PATHS:
        return None

    origin = request.headers.get('Origin', '').strip()
    referer = request.headers.get('Referer', '').strip()

    # Server-to-server calls normalmente no incluyen Origin/Referer
    if origin and not _is_origin_allowed(origin):
        return jsonify({'error': 'Origin no permitido'}), 403

    if referer and not _is_referer_allowed(referer):
        return jsonify({'error': 'Referer no permitido'}), 403

    return None

@app.before_request
def enforce_signed_requests():
    if request.method == 'OPTIONS' or request.path in RATE_LIMIT_EXEMPT_PATHS:
        return None

    if not REQUIRE_SIGNED_REQUESTS:
        return None

    if SIGNATURE_ALGORITHM != 'ed25519':
        return jsonify({'error': 'SIGNATURE_ALGORITHM inválido'}), 500

    public_key = _get_public_key()
    if not public_key:
        return jsonify({'error': 'Firma requerida pero FRONTEND_PUBLIC_KEY_PEM no configurado (o falta cryptography)'}), 500

    key_id = request.headers.get('X-Frontend-Key-Id', '').strip()
    timestamp = request.headers.get('X-Frontend-Timestamp', '').strip()
    nonce = request.headers.get('X-Frontend-Nonce', '').strip()
    signature = request.headers.get('X-Frontend-Signature', '').strip()

    if not key_id or not timestamp or not nonce or not signature:
        return jsonify({'error': 'Headers de firma faltantes'}), 401

    if key_id != FRONTEND_KEY_ID:
        return jsonify({'error': 'Key ID inválido'}), 401

    now = int(time.time())
    try:
        ts_int = int(timestamp)
    except ValueError:
        return jsonify({'error': 'Timestamp inválido'}), 401

    if abs(now - ts_int) > SIGNATURE_MAX_AGE_SECONDS:
        return jsonify({'error': 'Firma expirada'}), 401

    if not _register_nonce_once(nonce, now):
        return jsonify({'error': 'Nonce reutilizado'}), 401

    body_hash = _get_request_body_sha256()
    payload = f"{request.method}\n{request.path}\n{timestamp}\n{nonce}\n{body_hash}"

    try:
        signature_bytes = base64.b64decode(signature)
    except Exception:
        return jsonify({'error': 'Firma inválida (base64)'}), 401

    try:
        public_key.verify(signature_bytes, payload.encode('utf-8'))
    except Exception:
        return jsonify({'error': 'Firma inválida'}), 401

    return None

@app.before_request
def enforce_turnstile():
    if request.method != 'POST':
        return None

    if request.path in RATE_LIMIT_EXEMPT_PATHS:
        return None

    protected = request.path in {'/clone', '/voices'} or request.path.startswith('/voices/')
    if not protected:
        return None

    if not REQUIRE_TURNSTILE:
        return None

    token = (
        request.headers.get('X-Turnstile-Token')
        or request.form.get('turnstile_token')
        or (request.get_json(silent=True) or {}).get('turnstile_token')
    )

    if not token:
        return jsonify({'error': 'Turnstile token requerido'}), 403

    remote_ip = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or request.remote_addr)
    ok, error = _verify_turnstile_token(token, remote_ip)
    if not ok:
        return jsonify({'error': f'Turnstile inválido: {error}'}), 403

    return None

@app.before_request
def enforce_rate_limit():
    """Limita a RATE_LIMIT_MAX_REQUESTS por usuario/IP en ventana de tiempo definida."""
    if request.method == 'OPTIONS' or request.path in RATE_LIMIT_EXEMPT_PATHS:
        return None

    identifier = get_client_identifier()
    now = int(time.time())

    # Redis rate limit (recomendado en producción)
    if redis_client:
        window_start = now - (now % RATE_LIMIT_WINDOW_SECONDS)
        key = f"{REDIS_RATE_LIMIT_PREFIX}:{identifier}:{window_start}"
        try:
            used_requests = int(redis_client.incr(key))
            if used_requests == 1:
                redis_client.expire(key, RATE_LIMIT_WINDOW_SECONDS + 30)

            if used_requests > RATE_LIMIT_MAX_REQUESTS:
                ttl = int(redis_client.ttl(key))
                retry_after = max(1, ttl if ttl > 0 else RATE_LIMIT_WINDOW_SECONDS)
                return jsonify({
                    "error": "Límite de peticiones alcanzado para esta ventana",
                    "limit": RATE_LIMIT_MAX_REQUESTS,
                    "used": used_requests,
                    "remaining": 0,
                    "retry_after_seconds": retry_after,
                    "window_seconds": RATE_LIMIT_WINDOW_SECONDS,
                    "identifier": identifier
                }), 429

            return None
        except Exception:
            # fallback memoria
            pass

    with rate_limit_lock:
        entry = rate_limit_counters.get(identifier)

        if not entry:
            entry = {"count": 0, "window_start": now}
            rate_limit_counters[identifier] = entry

        elapsed = now - entry["window_start"]
        if elapsed >= RATE_LIMIT_WINDOW_SECONDS:
            entry["count"] = 0
            entry["window_start"] = now

        used_requests = entry["count"]
        if used_requests >= RATE_LIMIT_MAX_REQUESTS:
            retry_after = max(1, RATE_LIMIT_WINDOW_SECONDS - elapsed)
            return jsonify({
                "error": "Límite de peticiones alcanzado para esta ventana",
                "limit": RATE_LIMIT_MAX_REQUESTS,
                "used": used_requests,
                "remaining": 0,
                "retry_after_seconds": retry_after,
                "window_seconds": RATE_LIMIT_WINDOW_SECONDS,
                "identifier": identifier
            }), 429

        entry["count"] = used_requests + 1

    return None

def convert_to_wav(input_path, output_path):
    """Convierte cualquier formato de audio a WAV usando librosa"""
    try:
        # Cargar audio (soporta mp3, m4a, ogg, flac, etc)
        audio, sr = librosa.load(input_path, sr=None, mono=True)
        # Guardar como WAV
        sf.write(output_path, audio, sr)
        return True
    except Exception as e:
        print(f"Error converting audio: {e}")
        return False

def get_audio_duration(filepath):
    """Obtiene la duración del audio en segundos"""
    try:
        audio, sr = librosa.load(filepath, sr=None)
        duration = librosa.get_duration(y=audio, sr=sr)
        return duration
    except:
        return 0

def load_voices_db():
    """Carga la base de datos de voces guardadas"""
    if os.path.exists(VOICES_DB):
        with open(VOICES_DB, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_voices_db(voices):
    """Guarda la base de datos de voces"""
    with open(VOICES_DB, 'w', encoding='utf-8') as f:
        json.dump(voices, f, ensure_ascii=False, indent=2)

@app.route('/tts', methods=['POST'])
def generate_speech():
    """XTTS requiere audio de referencia; redirigimos a /clone o /voices/<id>/use."""
    data = request.get_json(silent=True) or {}
    language = data.get('language', 'es')

    return jsonify({
        "error": "XTTS requiere audio de referencia. Usa /clone o /voices/{voice_id}/use",
        "language": language
    }), 400

@app.route('/clone', methods=['POST'])
def clone_voice():
    """Endpoint para TTS con clonación de voz desde archivo de audio"""
    if 'audio' not in request.files:
        return jsonify({"error": "No audio file"}), 400
    
    audio = request.files['audio']
    text = request.form.get('text')
    language = request.form.get('language', 'es')
    temperature = float(request.form.get('temperature', 0.75))  # Calidad de voz
    speed = float(request.form.get('speed', 1.0))  # Velocidad
    
    if not text:
        return jsonify({"error": "No text provided"}), 400
    
    # Guardar archivo temporal con su extensión original
    original_ext = os.path.splitext(audio.filename)[1]
    temp_input = os.path.join(TEMP_DIR, f"input_{uuid.uuid4()}{original_ext}")
    temp_wav = os.path.join(TEMP_DIR, f"converted_{uuid.uuid4()}.wav")
    
    audio.save(temp_input)
    
    # Convertir a WAV si es necesario
    if original_ext.lower() != '.wav':
        if not convert_to_wav(temp_input, temp_wav):
            os.remove(temp_input)
            return jsonify({"error": "Error converting audio format"}), 400
        os.remove(temp_input)
        speaker_path = temp_wav
    else:
        speaker_path = temp_input
    
    # Validar duración del audio (recomendado: 3-10 segundos)
    duration = get_audio_duration(speaker_path)
    if duration < 2:
        os.remove(speaker_path)
        return jsonify({"error": f"Audio muy corto ({duration:.1f}s). Usa mínimo 3 segundos para mejor calidad"}), 400
    
    try:
        output_path = os.path.join(OUTPUT_DIR, f"{uuid.uuid4()}.wav")
        engine.text_to_speech(
            text=text, 
            output_path=output_path, 
            speaker_wav=speaker_path, 
            language=language,
            temperature=temperature,
            speed=speed
        )
        
        os.remove(speaker_path)
        
        return send_file(output_path, mimetype='audio/wav')
    except Exception as e:
        if os.path.exists(speaker_path):
            os.remove(speaker_path)
        return jsonify({"error": str(e)}), 500

@app.route('/voices', methods=['GET'])
def get_voices():
    """Obtiene la lista de voces guardadas en la galería"""
    voices = load_voices_db()
    
    # Separar voces por tipo
    preloaded_voices = [v for v in voices if v.get('type') == 'preloaded']
    user_voices = [v for v in voices if v.get('type') != 'preloaded']
    
    return jsonify({
        'preloaded_voices': preloaded_voices,
        'user_voices': user_voices
    })

@app.route('/voices', methods=['POST'])
def save_voice():
    """Guarda una voz en la galería"""
    if 'audio' not in request.files:
        return jsonify({"error": "No audio file"}), 400
    
    audio = request.files['audio']
    name = request.form.get('name', 'Sin nombre')
    description = request.form.get('description', '')
    language = request.form.get('language', 'es')
    
    # Guardar archivo
    original_ext = os.path.splitext(audio.filename)[1]
    voice_id = str(uuid.uuid4())
    filename = f"{voice_id}.wav"
    temp_input = os.path.join(TEMP_DIR, f"temp_{voice_id}{original_ext}")
    final_path = os.path.join(VOICES_DIR, filename)
    
    audio.save(temp_input)
    
    # Convertir a WAV
    if not convert_to_wav(temp_input, final_path):
        os.remove(temp_input)
        return jsonify({"error": "Error converting audio"}), 400
    
    os.remove(temp_input)
    
    # Obtener duración
    duration = get_audio_duration(final_path)
    
    # Guardar metadata
    voices = load_voices_db()
    voice_data = {
        "id": voice_id,
        "name": name,
        "description": description,
        "language": language,
        "filename": filename,
        "duration": round(duration, 2),
        "type": "user",  # Marcar como voz de usuario
        "created_at": str(uuid.uuid4())  # Placeholder, idealmente usar timestamp
    }
    voices.append(voice_data)
    save_voices_db(voices)
    
    return jsonify(voice_data), 201

@app.route('/voices/<voice_id>', methods=['DELETE'])
def delete_voice(voice_id):
    """Elimina una voz de la galería"""
    voices = load_voices_db()
    voice = next((v for v in voices if v['id'] == voice_id), None)
    
    if not voice:
        return jsonify({"error": "Voice not found"}), 404
    
    # Eliminar archivo
    filepath = os.path.join(VOICES_DIR, voice['filename'])
    if os.path.exists(filepath):
        os.remove(filepath)
    
    # Actualizar DB
    voices = [v for v in voices if v['id'] != voice_id]
    save_voices_db(voices)
    
    return jsonify({"success": True})

@app.route('/voices/<voice_id>/use', methods=['POST'])
def use_voice(voice_id):
    """Usa una voz de la galería para generar TTS"""
    voices = load_voices_db()
    voice = next((v for v in voices if v['id'] == voice_id), None)
    
    if not voice:
        return jsonify({"error": "Voice not found"}), 404
    
    data = request.json
    text = data.get('text')
    language = data.get('language', voice['language'])
    temperature = float(data.get('temperature', 0.75))
    speed = float(data.get('speed', 1.0))
    
    if not text:
        return jsonify({"error": "No text provided"}), 400
    
    speaker_path = os.path.join(VOICES_DIR, voice['filename'])
    
    if not os.path.exists(speaker_path):
        return jsonify({"error": "Voice file not found"}), 404
    
    try:
        output_path = os.path.join(OUTPUT_DIR, f"{uuid.uuid4()}.wav")
        engine.text_to_speech(
            text=text,
            output_path=output_path,
            speaker_wav=speaker_path,
            language=language,
            temperature=temperature,
            speed=speed
        )
        
        return send_file(output_path, mimetype='audio/wav')
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/queue/status', methods=['GET'])
def queue_status():
    """Retorna el estado de la cola de TTS (jobs en espera y procesando)."""
    try:
        queue_info = {
            "status": "operational",
            "redis_available": redis_client is not None,
            "jobs_queued": 0,
            "rate_limit_users": 0,
            "timestamp": int(time.time())
        }
        
        if redis_client:
            try:
                # Contar jobs en cola (usando patrón key)
                queue_keys = redis_client.keys("tts:job:*")
                queue_info["jobs_queued"] = len(queue_keys) if queue_keys else 0
                
                # Contar usuarios activos en rate limit
                rate_limit_keys = redis_client.keys("tts:rate:*")
                queue_info["rate_limit_users"] = len(rate_limit_keys) if rate_limit_keys else 0
            except Exception:
                pass
        else:
            # Fallback a memoria si Redis no está disponible
            queue_info["jobs_queued"] = 0
            queue_info["rate_limit_users"] = len(rate_limit_counters)
        
        return jsonify(queue_info), 200
    except Exception as e:
        return jsonify({"error": str(e), "status": "unavailable"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint de health check para Coolify"""
    return jsonify({"status": "healthy", "service": "tts-voice-cloning"}), 200

if __name__ == '__main__':
    # Obtener puerto de variable de entorno o usar 4000 por defecto
    port = int(os.getenv('FLASK_PORT', 4000))
    app.run(host='0.0.0.0', port=port, debug=False)
