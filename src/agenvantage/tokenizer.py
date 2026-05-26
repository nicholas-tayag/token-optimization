from __future__ import annotations

import tiktoken


class TokenCounter:
    """Counts tokens using an explicit OpenAI-compatible tokenizer encoding."""

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.model = model
        try:
            self.encoding = tiktoken.encoding_for_model(model)
            self.encoding_name = self.encoding.name
        except KeyError:
            self.encoding = tiktoken.get_encoding("o200k_base")
            self.encoding_name = "o200k_base"

    def count(self, text: str) -> int:
        return len(self.encoding.encode(text))

