import torch
from TTS.api import TTS
import os
import warnings
from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv()

# Fix PyTorch 2.6+ weights_only default
import torch.serialization
_original_torch_load = torch.load
def _patched_load(*args, **kwargs):
    kwargs.setdefault('weights_only', False)
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_load

class TTSEngine:
    def __init__(self):
        # Configurar GPU desde variables de entorno
        cuda_device = os.getenv('CUDA_VISIBLE_DEVICES', '0')
        os.environ['CUDA_VISIBLE_DEVICES'] = cuda_device
        
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Using device: {self.device}")
        
        # Cargar modelo desde variable de entorno
        model_name = os.getenv('XTTS_MODEL', 'tts_models/multilingual/multi-dataset/xtts_v2')
        self.tts = TTS(model_name).to(self.device)
        
    def text_to_speech(self, text, output_path="output.wav", speaker_wav=None, language="es", 
                       temperature=0.75, speed=1.0, enable_text_splitting=True):
        """
        text: texto a convertir
        output_path: donde guardar el audio
        speaker_wav: archivo de voz a clonar (REQUERIDO para XTTS)
        language: idioma (es, en, fr, etc)
        temperature: control de variabilidad (0.1-1.0, menor = más estable, 0.75 óptimo)
        speed: velocidad del habla (0.5-2.0)
        enable_text_splitting: divide textos largos automáticamente para mejor calidad
        """
        if not speaker_wav:
            raise ValueError("XTTS requiere speaker_wav. Proporciona un audio de referencia de 6+ segundos")
        
        # Configuración optimizada para máxima calidad
        self.tts.tts_to_file(
            text=text,
            speaker_wav=speaker_wav,
            language=language,
            file_path=output_path,
            temperature=temperature,  # Control de estabilidad/variabilidad
            speed=speed,  # Velocidad del habla
            enable_text_splitting=enable_text_splitting  # Mejor para textos largos
        )
        return output_path

if __name__ == "__main__":
    engine = TTSEngine()
    engine.text_to_speech(
        "Hola, soy una voz generada con inteligencia artificial en español",
        "test_output.wav"
    )
    print("Audio generado: test_output.wav")
