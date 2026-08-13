"""Router modeli — warstwa OpenAI-compat do vLLM/Ollama/SGLang.

Dobór modelu po tagach/capabilities z config/models.yaml + routing.yaml,
z fallbackami. Implementacja w Etapie 1.
"""

from __future__ import annotations
