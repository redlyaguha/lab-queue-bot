import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_GROUP_ID: int = int(os.getenv("ADMIN_GROUP_ID", "0"))


settings = Settings()