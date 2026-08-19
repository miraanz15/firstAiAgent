"""Provider-neutral result types, shared by every backend."""

from dataclasses import dataclass, field


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, usage) -> None:
        """Accepts any SDK usage object; missing fields count as zero."""
        for attr in ("input_tokens", "prompt_token_count"):
            value = getattr(usage, attr, None)
            if value:
                self.input_tokens += value
                break
        for attr in ("output_tokens", "candidates_token_count"):
            value = getattr(usage, attr, None)
            if value:
                self.output_tokens += value
                break


@dataclass
class Result:
    text: str = ""
    report_path: str | None = None
    usage: Usage = field(default_factory=Usage)
    warnings: list[str] = field(default_factory=list)
    turns: int = 0
