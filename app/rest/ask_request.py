from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional

class AskRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    chat_id: str = Field(min_length=1)
    question: str
    is_thinking: bool = False
    skip_user_interaction: bool = False
    dialect: str = Field(default="sqlite", alias="sql_dialect")
    max_sql_repair_attempts: int = 2
    schema_name: Optional[str] = None
    schema_context_base: Optional[str] = None

    @field_validator("dialect")
    @classmethod
    def normalize_dialect(cls, value: str) -> str:
        dialect = (value or "sqlite").strip().lower()
        if dialect in {"postgres", "postgresql"}:
            return "postgresql"
        if dialect == "sqlite":
            return "sqlite"
        raise ValueError("dialect must be 'sqlite' or 'postgresql'")

    @field_validator("max_sql_repair_attempts")
    @classmethod
    def validate_max_sql_repair_attempts(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_sql_repair_attempts must be >= 0")
        return min(value, 5)
