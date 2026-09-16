"""환경변수 설정.

값은 .env 또는 실제 환경변수에서 읽는다. 검증에 실패하면 서버가 뜨지 않는다.
실키는 .env 에만 두고 커밋하지 않는다 (.env.example 만 커밋).
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# HS256 은 32바이트 이상을 권장한다(RFC 7518). 운영에서는 반드시 교체한다.
DEV_JWT_SECRET = "dev-only-change-me-dev-only-change-me-32b"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 서버 ---
    app_env: Literal["development", "test", "production"] = "development"
    port: int = Field(default=8000, ge=1, le=65535)
    app_version: str = "0.1.0"

    # --- DB · 캐시 ---
    database_url: str = "postgresql+asyncpg://postgres:devpass@localhost:5432/p2j"
    redis_url: str = "redis://localhost:6379/0"

    # --- JWT (§1.6: access 30분, refresh 14일 rotation) ---
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_seconds: int = Field(default=1800, ge=60)
    jwt_refresh_ttl_days: int = Field(default=14, ge=1)

    # --- 도메인 규칙 ---
    # 하루 경계 시각. 4 = 매일 04:00 KST 에 날짜가 넘어간다 (BR-01).
    service_day_start_hour: int = Field(default=4, ge=0, le=23)

    # --- 배치 (04-backend-v1 §5.9) ---
    # 하루가 04:00 에 바뀌므로 그 직후에 전일을 판정한다. 시각은 KST 기준.
    # 인스턴스를 여러 개로 늘리면 잡이 중복 실행된다. 그때는 false 로 끄고 외부 cron 을 쓴다.
    batch_enabled: bool = True
    batch_hour: int = Field(default=4, ge=0, le=23)
    batch_minute: int = Field(default=10, ge=0, le=59)

    # --- CORS ---
    # 운영에서 허용할 origin 을 콤마로 구분. 개발·테스트에서는 항상 열려 있다.
    cors_origins: str = ""

    # --- 스토리지 (§6.3) ---
    # local  : 이 서버가 STORAGE_DIR 아래에 받아 둔다. 설정이 필요 없다.
    # firebase: 서명 URL 로 클라이언트가 버킷에 직접 올린다 (명세의 원안).
    #           FIREBASE_CREDENTIALS_PATH · FIREBASE_STORAGE_BUCKET 이 둘 다 있어야 한다.
    storage_driver: Literal["local", "firebase"] = "local"
    storage_dir: str = "var/uploads"

    # --- 외부 서비스 (해당 기능 구현 시 필수로 승격) ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    firebase_credentials_path: str = ""
    firebase_storage_bucket: str = ""
    ai_parse_daily_limit: int = 30

    @field_validator("jwt_secret")
    @classmethod
    def _secret_must_be_set_in_production(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("app_env") == "production" and (not value or value == DEV_JWT_SECRET):
            raise ValueError("운영 환경에서는 JWT_SECRET 을 반드시 설정해야 합니다.")
        return value

    @model_validator(mode="after")
    def _firebase_needs_credentials(self) -> "Settings":
        # 부팅 때 걸러 낸다. 사진을 올리려는 순간에야 503 을 보는 것보다 낫다.
        # field_validator 로는 안 된다 — 선언 순서상 firebase_* 가 아직 안 채워져 있다.
        if self.storage_driver == "firebase" and not (
            self.firebase_credentials_path and self.firebase_storage_bucket
        ):
            raise ValueError(
                "STORAGE_DRIVER=firebase 이면 FIREBASE_CREDENTIALS_PATH 와 "
                "FIREBASE_STORAGE_BUCKET 을 설정해야 합니다."
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
