from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "target",
    "build",
    "dist",
    "out",
    "__pycache__",
    ".idea",
    ".vscode",
    ".gradle",
    ".mvn",
    ".devanalyzer",
    ".pytest_cache",
    ".mypy_cache",
}

SUPPORTED_EXTENSIONS = {
    # Java
    ".java", ".kt", ".xml", ".properties", ".gradle", ".kts",
    # Node / TS
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json",
    # Python
    ".py", ".toml", ".cfg", ".ini", ".txt",
    # Common
    ".yml", ".yaml", ".md",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DEVANALYZER_", extra="ignore")

    llm_provider: str = "ollama"
    llm_model: str = "qwen2.5-coder:7b"
    ollama_host: str = "http://localhost:11434"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    max_file_size_kb: int = 512
    ignore_dirs: set[str] = Field(default_factory=lambda: set(DEFAULT_IGNORE_DIRS))
    supported_extensions: set[str] = Field(default_factory=lambda: set(SUPPORTED_EXTENSIONS))
    output_dir_name: str = ".devanalyzer"


def get_settings() -> Settings:
    return Settings()
