import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    BRIEFING_CHANNEL_ID: str = os.getenv("BRIEFING_CHANNEL_ID", "")
    ANALYSIS_CHANNEL_ID: str = os.getenv("ANALYSIS_CHANNEL_ID", "")
    GERAL_CHANNEL_ID: str = os.getenv("GERAL_CHANNEL_ID", "")
    RAIZA_DISCORD_ID: str = os.getenv("RAIZA_DISCORD_ID", "")

    # Limites operacionais
    MAX_MESSAGES_TO_COLLECT: int = 100
    MAX_FILE_SIZE_MB: int = 10
    PDF_MAX_PAGES: int = 3      # páginas máximas do PDF convertidas em imagem
    PDF_DPI: int = 150          # resolução da conversão PDF → PNG
    GPT_MODEL: str = "gpt-4o"
    GPT_MAX_TOKENS: int = 4096

    def validate(self) -> None:
        required = [
            "DISCORD_TOKEN",
            "OPENAI_API_KEY",
            "BRIEFING_CHANNEL_ID",
            "ANALYSIS_CHANNEL_ID",
        ]
        missing = [v for v in required if not getattr(self, v)]
        if missing:
            raise ValueError(
                "Variáveis de ambiente obrigatórias não configuradas: "
                + ", ".join(missing)
            )
