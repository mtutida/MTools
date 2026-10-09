from PySide6.QtCore import Qt, QAbstractTableModel
import os

AUDIO_EXT = (".mp3",".aac",".wav",".flac",".ogg",".m4a",".wma",".opus",".alac")


def format_size(path):
    try:
        s = os.path.getsize(path)
        for unit in ["B","KB","MB","GB"]:
            if s < 1024:
                return f"{s:.0f} {unit}"
            s /= 1024
        return f"{s:.1f} TB"
    except Exception:
        return ""


def file_type_label(path):
    ext = os.path.splitext(path)[1].lower()
    return "Áudio" if ext in AUDIO_EXT else "Vídeo"


class FileTableModel(QAbstractTableModel):

    headers = ["Arquivo","Tipo","Tamanho"]

    def __init__(self, files):
        super().__init__()
        self.files = files
        self._sort_column = 0
        self._sort_order = Qt.AscendingOrder

    def rowCount(self, parent=None):
        return len(self.files)

    def columnCount(self, parent=None):
        return len(self.headers)

    def data(self, index, role):
        if not index.isValid():
            return None

        file = self.files[index.row()]

        if role == Qt.DisplayRole:
            if index.column() == 0:
                return os.path.basename(file)
            if index.column() == 1:
                return file_type_label(file)
            if index.column() == 2:
                return format_size(file)
        if role == Qt.ToolTipRole:
            if index.column() == 0:
                return file
        if role == Qt.StatusTipRole:
            return file

        if role == Qt.TextAlignmentRole:
            if index.column() == 0:
                return Qt.AlignLeft | Qt.AlignVCenter
            if index.column() == 1:
                return Qt.AlignCenter | Qt.AlignVCenter
            if index.column() == 2:
                return Qt.AlignRight | Qt.AlignVCenter

        return None

    def headerData(self, section, orientation, role):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return self.headers[section]
            if role == Qt.TextAlignmentRole:
                if section == 0:
                    return Qt.AlignLeft | Qt.AlignVCenter
                if section == 1:
                    return Qt.AlignCenter | Qt.AlignVCenter
                if section == 2:
                    return Qt.AlignRight | Qt.AlignVCenter
            if role == Qt.ToolTipRole:
                if section == 0:
                    return "Nome do arquivo"
                if section == 1:
                    return "Tipo de mídia detectado"
                if section == 2:
                    return "Tamanho atual do arquivo"
        return None

    def sort(self, column, order):
        self._sort_column = column
        self._sort_order = order
        reverse = order == Qt.DescendingOrder

        if column == 0:
            self.files.sort(key=lambda f: os.path.basename(f).lower(), reverse=reverse)
        elif column == 1:
            self.files.sort(key=lambda f: (file_type_label(f), os.path.basename(f).lower()), reverse=reverse)
        elif column == 2:
            def size(f):
                try:
                    return os.path.getsize(f)
                except Exception:
                    return 0
            self.files.sort(key=size, reverse=reverse)
        self.layoutChanged.emit()

    def refresh(self):
        self.sort(self._sort_column, self._sort_order)
