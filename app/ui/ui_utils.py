from PySide6.QtGui import QIcon

from app.ui.assets import icon


def load_icon(name: str) -> QIcon:
    """
    Helper central para carregar ícones da UI.
    """
    return QIcon(icon(name))
