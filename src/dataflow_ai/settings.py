"""Application configuration loaded from environment variables and local .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _resolve_path(root: Path, raw_value: str) -> Path:
    candidate = Path(raw_value)
    return candidate if candidate.is_absolute() else root / candidate


def _read_env_file(env_file: Path) -> dict[str, str]:
    """Parse key-value pairs from a .env file without modifying os.environ."""
    if not env_file.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        content = env_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            if key:
                values[key] = val
    except Exception:
        pass
    return values


def _positive_float(value: str | None, default: float, *, minimum: float = 0.1, maximum: float = 300.0) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= maximum else default


def _bounded_int(value: str | None, default: int, *, minimum: int = 0, maximum: int = 5) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= maximum else default


@dataclass(frozen=True)
class Settings:
    """Project directories, database credentials, and service settings."""

    project_root: Path
    data_dir: Path
    raw_dir: Path
    processed_dir: Path
    quarantine_dir: Path
    sample_dir: Path
    warehouse_dir: Path
    database_path: Path
    logs_dir: Path
    temp_dir: Path | None = None
    database_url: str | None = None
    motherduck_token: str | None = None
    log_level: str = "INFO"
    ollama_base_url: str | None = None
    ollama_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 1
    ai_query_timeout_seconds: float = 8.0
    ai_synthesize_insights: bool = False
    ingestion_api_key: str | None = None
    cors_origins: tuple[str, ...] = ("http://localhost:8501", "http://127.0.0.1:8501")
    environment: str = "development"
    debug: bool = False
    jwt_secret: str = "dataflow-development-secret-key-32-chars-min"
    auth_required: bool = False
    stripe_webhook_secret: str | None = None
    razorpay_webhook_secret: str | None = None

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ("production", "prod")

    @property
    def has_motherduck_placeholder(self) -> bool:
        """True when a placeholder MotherDuck token (e.g. 'abcdef') is configured."""
        if not self.motherduck_token:
            return False
        tok = self.motherduck_token.strip().lower()
        return tok in ("abcdef", "your-token-here", "your_token_here", "token") or len(tok) < 10

    @property
    def is_cloud_database(self) -> bool:
        """True when the warehouse is hosted on MotherDuck (cloud DuckDB) with an active token."""
        if self.has_motherduck_placeholder:
            return False
        return bool(
            (self.database_url and self.database_url.startswith("md:"))
            and self.motherduck_token
            and len(self.motherduck_token.strip()) >= 10
        )

    @property
    def effective_database_dsn(self) -> str:
        """Return the connection string for DuckDB: MotherDuck URI or local file path."""
        if self.is_cloud_database and self.database_url and self.database_url.startswith("md:"):
            return self.database_url
        return str(self.database_path)

    @classmethod
    def from_environment(cls, project_root: Path | None = None) -> Settings:
        root = (project_root or Path(os.getenv("DATAFLOW_PROJECT_ROOT", Path.cwd()))).resolve()
        env_file = root / ".env"
        if not env_file.is_file() and project_root is None:
            env_file = Path.cwd() / ".env"
        file_env = _read_env_file(env_file)

        def env(key: str, default: str | None = None) -> str | None:
            val = os.getenv(key)
            if val is not None and val != "":
                return val
            file_val = file_env.get(key)
            if file_val is not None and file_val != "":
                return file_val
            return default

        data_dir = _resolve_path(root, env("DATAFLOW_DATA_DIR") or "data")
        warehouse_dir = data_dir / "warehouse"
        database_path = _resolve_path(
            root,
            env("DATAFLOW_DATABASE_PATH") or str(warehouse_dir / "dataflow.duckdb"),
        )
        cors_raw = env("DATAFLOW_CORS_ORIGINS")
        cors_origins = tuple(o.strip() for o in cors_raw.split(",") if o.strip()) if cors_raw else ("http://localhost:8501", "http://127.0.0.1:8501")
        env_str = env("DATAFLOW_ENV") or env("ENVIRONMENT") or "development"
        debug_bool = (env("DATAFLOW_DEBUG") or "false").lower() in ("true", "1")
        jwt_sec = env("DATAFLOW_JWT_SECRET") or env("JWT_SECRET_KEY") or "dataflow-development-secret-key-32-chars-min"
        auth_req = (env("DATAFLOW_AUTH_REQUIRED") or "false").lower() in ("true", "1")

        llm_api_key = env("LLM_API_KEY") or env("GEMINI_API_KEY") or None
        llm_base_url = env("LLM_BASE_URL") or None
        llm_model = env("LLM_MODEL") or None
        if llm_api_key and not llm_base_url:
            llm_base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
            if not llm_model:
                llm_model = "gemini-3.6-flash"

        temp_dir = _resolve_path(root, env("DATAFLOW_TEMP_DIR") or str(data_dir / "temp"))

        motherduck_token = env("MOTHERDUCK_TOKEN") or None
        database_url_raw = env("DATAFLOW_DATABASE_URL") or None

        # If MotherDuck token is set but no explicit database URL, default to md:dataflow_ai
        if motherduck_token and not database_url_raw:
            database_url_raw = "md:dataflow_ai"

        return cls(
            project_root=root,
            data_dir=data_dir,
            raw_dir=data_dir / "raw",
            processed_dir=data_dir / "processed",
            quarantine_dir=data_dir / "quarantine",
            sample_dir=data_dir / "sample",
            warehouse_dir=warehouse_dir,
            database_path=database_path,
            logs_dir=root / "logs",
            temp_dir=temp_dir,
            database_url=database_url_raw,
            motherduck_token=motherduck_token,
            log_level=(env("DATAFLOW_LOG_LEVEL") or "INFO").upper(),
            ollama_base_url=env("OLLAMA_BASE_URL") or None,
            ollama_model=env("OLLAMA_MODEL") or None,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            llm_timeout_seconds=_positive_float(env("LLM_TIMEOUT_SECONDS"), 20.0),
            llm_max_retries=_bounded_int(env("LLM_MAX_RETRIES"), 1),
            ai_query_timeout_seconds=_positive_float(env("AI_QUERY_TIMEOUT_SECONDS"), 8.0, maximum=60.0),
            ai_synthesize_insights=(env("AI_SYNTHESIZE_INSIGHTS") or "false").lower() in ("true", "1"),
            ingestion_api_key=env("INGESTION_API_KEY") or None,
            cors_origins=cors_origins,
            environment=env_str,
            debug=debug_bool,
            jwt_secret=jwt_sec,
            auth_required=auth_req,
            stripe_webhook_secret=env("STRIPE_WEBHOOK_SECRET") or None,
            razorpay_webhook_secret=env("RAZORPAY_WEBHOOK_SECRET") or None,
        )

    def validate_for_deployment(self) -> list[str]:
        """Verify environment variables and security constraints for deployment."""
        errors: list[str] = []
        if self.debug and self.is_production:
            errors.append("Debug mode cannot be enabled in production (set DATAFLOW_DEBUG=false).")

        if self.is_production:
            if "*" in self.cors_origins:
                errors.append("Wildcard CORS ('*') is forbidden in production; specify explicit frontend domain(s).")
            for origin in self.cors_origins:
                if origin.startswith("http://") and "localhost" not in origin and "127.0.0.1" not in origin:
                    errors.append(f"Insecure HTTP origin '{origin}' in production; HTTPS is required.")
            if self.jwt_secret == "dataflow-development-secret-key-32-chars-min" or len(self.jwt_secret) < 32:
                errors.append("Insecure or default DATAFLOW_JWT_SECRET in production; must be a secure key with >= 32 characters.")

        if self.database_url:
            low_url = self.database_url.lower()
            if low_url.startswith(("postgres://", "postgresql://")):
                if not any(k in low_url for k in ("sslmode=require", "sslmode=verify-ca", "sslmode=verify-full")):
                    errors.append("PostgreSQL connection string must enforce TLS/SSL in production (e.g. '?sslmode=require').")
            elif low_url.startswith("mongodb://"):
                if not any(k in low_url for k in ("tls=true", "ssl=true")):
                    errors.append("MongoDB connection string must enforce TLS/SSL (e.g. '?tls=true' or use mongodb+srv://).")
            default_creds = ("postgres:postgres@", "admin:admin@", "root:root@", "user:password@", "root:password@")
            if any(cred in low_url for cred in default_creds):
                errors.append("Insecure default database credentials detected in connection string.")

        if self.llm_base_url and not self.llm_api_key:
            errors.append("LLM_BASE_URL is configured but LLM_API_KEY is missing.")
        if self.llm_api_key and not self.llm_model:
            errors.append("LLM_API_KEY is configured but LLM_MODEL is missing.")

        return errors

    def ensure_directories(self) -> None:
        # When using a cloud database, local warehouse directory is not needed.
        directories = [
            self.raw_dir,
            self.processed_dir,
            self.quarantine_dir,
            self.sample_dir,
            self.logs_dir,
            self.temp_dir or (self.data_dir / "temp"),
        ]
        if not self.is_cloud_database:
            directories.append(self.warehouse_dir)
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

