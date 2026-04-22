import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_GROUP_ID: int = int(os.getenv("ADMIN_GROUP_ID", "0"))
    ADMIN_IDS: list[int] = field(
        default_factory=lambda: [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
    )


settings = Settings()


def is_admin(user_id: int) -> bool:
    return user_id in settings.ADMIN_IDS