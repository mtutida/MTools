from app.core.paths import ICON_DIR

def icon(name: str) -> str:
    return str(ICON_DIR / name)
