"""
Text Corrector Module
Módulo de corrección automática de texto usando LanguageTool
"""

import language_tool_python
from typing import Dict, List, Any


class TextCorrector:
    """
    Corrector de texto automático usando LanguageTool
    Optimizado para español (es)
    """
    
    def __init__(self, language: str = 'es'):
        """
        Inicializa el corrector de texto
        
        Args:
            language: Código del idioma (ej: 'es', 'en', 'fr')
        """
        self.language = language
        # Configuración más estricta para detectar más errores
        config = {'cacheSize': 1000, 'pipelineCaching': True}
        self.tool = language_tool_python.LanguageTool(language, config=config)
    
    def correct_text(self, text: str) -> Dict[str, Any]:
        """
        Corrige automáticamente el texto
        
        Args:
            text: Texto a corregir
            
        Returns:
            Dict con:
                - original: Texto original
                - corrected: Texto corregido
                - changes: Lista de cambios realizados
                - changes_count: Número de correcciones
        """
        if not text or not text.strip():
            return {
                'original': text,
                'corrected': text,
                'changes': [],
                'changes_count': 0
            }
        
        # Obtener correcciones
        matches = self.tool.check(text)
        
        # Aplicar correcciones
        corrected_text = language_tool_python.utils.correct(text, matches)
        
        # Crear lista de cambios
        changes = []
        for match in matches:
            changes.append({
                'message': match.message,
                'context': match.context,
                'offset': match.offset,
                'length': match.error_length,
                'replacements': match.replacements[:3] if match.replacements else [],
                'rule': match.rule_id
            })
        
        return {
            'original': text,
            'corrected': corrected_text,
            'changes': changes,
            'changes_count': len(matches)
        }
    
    def get_suggestions(self, text: str) -> List[Dict[str, Any]]:
        """
        Obtiene sugerencias de corrección sin aplicarlas
        
        Args:
            text: Texto a analizar
            
        Returns:
            Lista de sugerencias con detalles
        """
        if not text or not text.strip():
            return []
        
        matches = self.tool.check(text)
        
        suggestions = []
        for match in matches:
            suggestions.append({
                'message': match.message,
                'context': match.context,
                'offset': match.offset,
                'length': match.error_length,
                'replacements': match.replacements[:5] if match.replacements else [],
                'rule': match.rule_id,
                'category': match.category
            })
        
        return suggestions
    
    def close(self):
        """Cierra el corrector y libera recursos"""
        if self.tool:
            self.tool.close()
