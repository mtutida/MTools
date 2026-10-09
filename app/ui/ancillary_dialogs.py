from __future__ import annotations

from PySide6.QtCore import QPoint, QEvent, QSize, Qt
from PySide6.QtGui import QColor, QPixmap

from PySide6.QtWidgets import (
    QDialog,
    QApplication,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core.app_version import APP_NAME, APP_VERSION
from app.ui.assets import icon as asset_icon
from app.ui.window_icon import apply_window_icon
from app.ui.dialog_caption import schedule_dialog_caption
from app.ui.theme_mode_controller import palette_for_styled_dialog
from app.ui.theme_tokens import build_button_stylesheet, build_theme_tokens, color_to_css


class _BaseAncillaryDialog(QDialog):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        apply_window_icon(self)
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)
        self.setSizeGripEnabled(False)
        self._applying_styles = False

    def _apply_theme_styles(self):
        if self._applying_styles:
            return
        self._applying_styles = True
        try:
            tokens = build_theme_tokens(palette_for_styled_dialog(self.palette()), use_application_palette=False)
            stylesheet = build_button_stylesheet(
                tokens,
                min_height=30,
                border_radius=4,
                horizontal_padding=10,
            )
            prefixed = stylesheet.replace("QPushButton", "QDialogButtonBox QPushButton")
            self.setStyleSheet(prefixed)
        finally:
            self._applying_styles = False

    def showEvent(self, event):
        super().showEvent(event)
        schedule_dialog_caption(self)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange, QEvent.ThemeChange):
            self._apply_theme_styles()
            schedule_dialog_caption(self)


class HelpDialog(_BaseAncillaryDialog):
    _INDEX_ROWS = (
        ("overview", "Visão geral"),
        ("import", "Adicionar e organizar arquivos"),
        ("analysis", "Análise e estimativas"),
        ("quick_apply", "Aplicar sugestões"),
        ("profiles", "Perfis e configuração"),
        ("output", "Destino de saída"),
        ("run", "Processar e cancelar"),
        ("preferences", "Preferências e aparência"),
        ("glossary", "Termos principais"),
        ("notes", "Observações importantes"),
    )

    def _help_tokens(self):
        return build_theme_tokens(palette_for_styled_dialog(self.palette()), use_application_palette=False)

    def _help_index_link_color(self):
        tokens = self._help_tokens()
        link = QColor(tokens.empty_state_link_text)
        surface = QColor(tokens.surface_panel)
        if surface.lightness() < 128:
            # The help index sits on a dark ancillary-dialog surface. When the app
            # changes from light to dark, the base link token can remain visually
            # too deep. Brighten it locally so the index always keeps a light,
            # clearly legible blue tone in dark mode.
            link = link.lighter(148)
            text = QColor(tokens.text_primary)
            link = QColor(
                round(link.red() * 0.82 + text.red() * 0.18),
                round(link.green() * 0.82 + text.green() * 0.18),
                round(link.blue() * 0.82 + text.blue() * 0.18),
                255,
            )
        return link

    def _refresh_index_html(self):
        index = getattr(self, "_help_index_label", None)
        if index is None:
            return
        tokens = self._help_tokens()
        text_color = color_to_css(tokens.text_primary)
        secondary_color = color_to_css(tokens.text_secondary)
        link_color = color_to_css(self._help_index_link_color())
        rows = "".join(
            "<li style='margin-bottom:4px;'>"
            f"<a style='color:{link_color}; text-decoration:underline;' href='{anchor}'>{label}</a>"
            "</li>"
            for anchor, label in self._INDEX_ROWS
        )
        index.setText(
            f"<div style='color:{text_color};'>"
            "<p style='margin-top:0; margin-bottom:2px;'><b>Navegação rápida</b></p>"
            f"<p style='margin-top:0; margin-bottom:8px; color:{secondary_color};'>"
            "Clique em um tópico para ir direto à seção.</p>"
            "<ul style='margin-top:0; margin-bottom:0; margin-left:18px; padding-left:12px;'>"
            f"{rows}"
            "</ul>"
            "</div>"
        )

    def _apply_theme_styles(self):
        super()._apply_theme_styles()
        self._refresh_index_html()
        tokens = self._help_tokens()
        panel = color_to_css(tokens.surface_panel)
        card = color_to_css(tokens.surface_card)
        border = color_to_css(tokens.border_panel)
        primary = color_to_css(tokens.text_primary)
        secondary = color_to_css(tokens.text_secondary)
        accent = color_to_css(tokens.empty_state_link_text)
        if hasattr(self, "_help_scroll"):
            self._help_scroll.viewport().setStyleSheet(f"background: {panel};")
        if hasattr(self, "_help_content"):
            self._help_content.setStyleSheet(f"background: {panel}; color: {primary};")
        self.setStyleSheet(
            self.styleSheet()
            + "\n"
            + "QFrame#HelpHero, QFrame#HelpSection, QFrame#HelpIndexCard {"
            + f"background: {card}; border: 1px solid {border}; border-radius: 8px;"
            + "}\n"
            + "QLabel#HelpTitle {"
            + f"color: {primary};"
            + "}\n"
            + "QLabel#HelpSubtitle, QLabel#HelpSectionBody {"
            + f"color: {secondary};"
            + "}\n"
            + "QLabel#HelpSectionTitle {"
            + f"color: {primary};"
            + "}\n"
            + "QPushButton#HelpTopButton {"
            + f"color: {accent}; border: none; background: transparent;"
            + "}\n"
        )

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange, QEvent.ThemeChange):
            self._refresh_index_html()

    def __init__(self, parent=None):
        super().__init__("Ajuda", parent)
        self.setObjectName("HelpDialog")
        self.setMinimumSize(560, 420)
        self.setSizeGripEnabled(True)
        self._help_initial_size_applied = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self._help_scroll = scroll

        content = QWidget(scroll)
        self._help_content = content
        content_layout = QVBoxLayout(content)
        self._help_content_layout = content_layout
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(10)

        section_targets: dict[str, QLabel] = {}

        def scroll_to_section(anchor: str):
            target = section_targets.get(anchor)
            if target is not None:
                target_y = target.mapTo(content, QPoint(0, 0)).y()
                scroll.verticalScrollBar().setValue(max(0, target_y - 8))

        def make_text_label(text: str, parent: QWidget, *, object_name: str = "HelpSectionBody") -> QLabel:
            label = QLabel(text, parent)
            label.setObjectName(object_name)
            label.setWordWrap(True)
            label.setTextFormat(Qt.RichText)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            return label

        hero = QFrame(content)
        hero.setObjectName("HelpHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(16, 14, 16, 14)
        hero_layout.setSpacing(6)

        title = QLabel(f"Ajuda do {APP_NAME}", hero)
        title.setObjectName("HelpTitle")
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(max(title_font.pointSize(), 15))
        title.setFont(title_font)
        title.setWordWrap(True)
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)

        subtitle = QLabel(
            f"{APP_NAME} {APP_VERSION} compacta vídeos, converte áudio e ajuda a organizar lotes de arquivos sem alterar os originais automaticamente.",
            hero,
        )
        subtitle.setObjectName("HelpSubtitle")
        subtitle_font = subtitle.font()
        subtitle_font.setPointSize(max(subtitle_font.pointSize(), 10))
        subtitle.setFont(subtitle_font)
        subtitle.setWordWrap(True)
        subtitle.setTextInteractionFlags(Qt.TextSelectableByMouse)

        hero_layout.addWidget(title)
        hero_layout.addWidget(subtitle)
        content_layout.addWidget(hero)

        index_card = QFrame(content)
        index_card.setObjectName("HelpIndexCard")
        index_layout = QVBoxLayout(index_card)
        index_layout.setContentsMargins(16, 12, 16, 12)
        index_layout.setSpacing(0)

        index = QLabel(index_card)
        self._help_index_label = index
        index.setObjectName("HelpIndexLabel")
        index.setWordWrap(True)
        index.setTextFormat(Qt.RichText)
        index.setTextInteractionFlags(Qt.TextBrowserInteraction)
        index.setOpenExternalLinks(False)
        self._refresh_index_html()
        index.linkActivated.connect(scroll_to_section)
        index_layout.addWidget(index)
        content_layout.addWidget(index_card)

        sections = [
            (
                "overview",
                "Visão geral",
                f"<p>Use o {APP_NAME} para reduzir arquivos de vídeo, converter ou extrair áudio e acompanhar cada item em cards individuais.</p>"
                "<ul>"
                "<li>A tela principal mostra arquivos, perfil escolhido, destino, estimativa e estado.</li>"
                "<li>A Ajuda é apenas informativa: não altera fila, configurações, arquivos ou processamento.</li>"
                "<li>Os originais permanecem no local de entrada; a saída é criada no destino configurado.</li>"
                "</ul>",
            ),
            (
                "import",
                "Adicionar e organizar arquivos",
                "<p>Carregue arquivos pela barra superior ou pela área central quando a lista estiver vazia.</p>"
                "<ul>"
                "<li><b>Adicionar</b>: seleção manual de arquivos.</li>"
                "<li><b>Adicionar Rápido</b>: importação assistida conforme as Preferências.</li>"
                "<li><b>Importar Pasta</b>: adiciona mídias compatíveis de uma pasta.</li>"
                "<li><b>Arrastar e soltar</b>: solta arquivos diretamente na lista.</li>"
                "<li><b>Selecionar tudo</b> marca os itens elegíveis; a lixeira remove os selecionados quando permitido.</li>"
                "</ul>",
            ),
            (
                "analysis",
                "Análise e estimativas",
                "<p>Cada arquivo é analisado para detectar duração, resolução, FPS, bitrate, áudio e outras informações úteis.</p>"
                "<ul>"
                "<li>O botão <b>Analisar mídia</b> pode atualizar a leitura do item em contexto.</li>"
                "<li>As estimativas são referências calculadas a partir dos dados detectados e do perfil escolhido.</li>"
                "<li>O tamanho final pode variar por codec, conteúdo, trilhas, metadados e limitações do arquivo original.</li>"
                "</ul>",
            ),
            (
                "quick_apply",
                "Aplicar sugestões",
                f"<p>Depois da análise, o {APP_NAME} pode sugerir alternativas rápidas para preencher o perfil <b>Avançado</b> sem configurar cada campo manualmente.</p>"
                "<ul>"
                "<li><b>Aplicar equilibrado</b>: usa uma configuração recomendada para reduzir o arquivo mantendo um equilíbrio entre qualidade, compatibilidade e tamanho.</li>"
                "<li><b>Aplicar alta compressão</b>: aplica uma configuração mais agressiva para priorizar redução de tamanho, com maior chance de perda visual ou redução de resolução/FPS/bitrate.</li>"
                "<li>Os botões preenchem os controles do <b>Avançado</b> para o arquivo em contexto; revise os campos antes de processar quando precisar de um resultado específico.</li>"
                "<li>As sugestões dependem dos dados detectados na mídia e podem não aparecer quando o arquivo não tiver informações suficientes ou quando a ação não for aplicável.</li>"
                "</ul>",
            ),
            (
                "profiles",
                "Perfis e configuração",
                "<p>O botão <b>Configurar</b> abre o painel do arquivo selecionado ou em contexto. Ajustes recalculam a estimativa do card.</p>"
                "<ul>"
                "<li><b>Rápido</b>: níveis simples de compressão, com menos decisões manuais.</li>"
                "<li><b>Estratégico</b>: escolha uma intenção como preservar qualidade, otimizar arquivo ou reduzir escala.</li>"
                "<li><b>Avançado</b>: controle meta de tamanho, bitrate, resolução, FPS, áudio e formato.</li>"
                "<li><b>Áudio</b>: converte/comprime áudio ou extrai áudio de vídeos; nesse caso o card usa <b>Gravar</b>.</li>"
                "<li><b>Restaurar padrões</b> volta o perfil atual ao padrão; <b>Aplicar a todos</b> replica ajustes compatíveis sem copiar destino, nome, progresso ou dados detectados.</li>"
                "</ul>",
            ),
            (
                "output",
                "Destino de saída",
                "<ul>"
                "<li><b>Abrir pasta</b> abre a pasta configurada para o arquivo.</li>"
                "<li><b>Alterar pasta</b> muda apenas o destino daquele card.</li>"
                "<li>A pasta padrão é ajustada nas Preferências.</li>"
                "<li>Quando aplicável, a saída padrão usa uma subpasta <b>CompactMe</b> dentro da pasta de origem.</li>"
                "</ul>",
            ),
            (
                "run",
                "Processar e cancelar",
                "<ul>"
                "<li><b>Processar</b> executa os itens selecionados; <b>Processar Todos</b> executa todos os elegíveis.</li>"
                "<li>Durante a execução, botões passam a indicar cancelamento ou estado de progresso.</li>"
                "<li>O processamento validado é sequencial: um arquivo por vez.</li>"
                "<li>Arquivos em fila, analisando ou processando ficam protegidos contra reconfigurações indevidas.</li>"
                "<li>Estados comuns: <b>Pronto</b>, <b>Analisando</b>, <b>Em fila</b>, <b>Processando</b>, <b>Concluído</b>, <b>Falha</b> e <b>Cancelado</b>.</li>"
                "</ul>",
            ),
            (
                "preferences",
                "Preferências e aparência",
                "<p>Abra o menu <b>⋮</b> para acessar Preferências, Aparência, Ajuda, Sobre e Sair.</p>"
                "<ul>"
                "<li><b>Preferências</b>: pasta padrão de saída, sobrescrita, importação assistida, abertura automática do painel, confirmação de remoção, logs e barra de progresso geral.</li>"
                "<li><b>Aparência</b>: alterna entre tema do sistema, claro e escuro.</li>"
                "<li>A tela de ajuda acompanha o tema atual para manter contraste e legibilidade.</li>"
                "</ul>",
            ),
            (
                "glossary",
                "Termos principais",
                "<ul>"
                "<li><b>Bitrate</b>: quantidade de dados por segundo; em geral, mais bitrate aumenta qualidade e tamanho.</li>"
                "<li><b>kbps</b>: quilobits por segundo, unidade comum de bitrate.</li>"
                "<li><b>FPS</b>: quadros por segundo; reduzir pode diminuir tamanho e fluidez.</li>"
                "<li><b>Resolução</b>: largura e altura do vídeo, como 1920×1080.</li>"
                "<li><b>Codec</b>: método de codificação, como H.264, AAC, Opus ou MP3.</li>"
                "<li><b>Trilhas</b>: faixas internas, como áudio, legendas ou idiomas.</li>"
                "<li><b>Normalização</b>: ajuste para deixar o volume mais uniforme.</li>"
                "</ul>",
            ),
            (
                "notes",
                "Observações importantes",
                "<ul>"
                "<li>O tamanho desejado no Avançado é uma meta e não deve exceder o tamanho original.</li>"
                "<li>Compressão sempre envolve equilíbrio entre tamanho, qualidade, compatibilidade e tempo de processamento.</li>"
                "<li>Configurações dos perfis pertencem aos cards; Preferências controla opções gerais do aplicativo.</li>"
                "<li>Em caso de falha, revise formato, permissões da pasta de saída, espaço em disco e integridade do arquivo de entrada.</li>"
                "</ul>",
            ),
        ]

        def normalize_body_html(body: str) -> str:
            return (
                "<div style='margin-left:0;'>"
                + body.replace("<p>", "<p style='margin-top:0; margin-bottom:8px;'>")
                .replace("<ul>", "<ul style='margin-top:0; margin-bottom:0; margin-left:18px; padding-left:12px;'>")
                + "</div>"
            )

        for anchor, section_title, body in sections:
            section = QFrame(content)
            section.setObjectName("HelpSection")
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(16, 12, 16, 12)
            section_layout.setSpacing(6)

            header = QLabel(section_title, section)
            header.setObjectName("HelpSectionTitle")
            header_font = header.font()
            header_font.setBold(True)
            header_font.setPointSize(max(header_font.pointSize(), 11))
            header.setFont(header_font)
            header.setWordWrap(True)
            header.setTextInteractionFlags(Qt.TextSelectableByMouse)
            section_targets[anchor] = header

            text = make_text_label(normalize_body_html(body), section)

            section_layout.addWidget(header)
            section_layout.addWidget(text)
            content_layout.addWidget(section)

        content_layout.addSpacing(360)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        footer = QWidget(self)
        footer_layout = QGridLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setHorizontalSpacing(8)
        footer_layout.setColumnStretch(0, 1)
        footer_layout.setColumnStretch(1, 0)
        footer_layout.setColumnStretch(2, 1)

        top_button = QPushButton("↑ Voltar ao topo", footer)
        top_button.setObjectName("HelpTopButton")
        top_button.setToolTip("Voltar ao início da Ajuda")
        top_button.setFlat(True)
        top_button.setFocusPolicy(Qt.NoFocus)
        top_button.clicked.connect(lambda: scroll.verticalScrollBar().setValue(0))
        footer_layout.addWidget(top_button, 0, 1, Qt.AlignCenter)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, footer)
        close_button = buttons.button(QDialogButtonBox.Close)
        if close_button is not None:
            close_button.setText("Fechar")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        footer_layout.addWidget(buttons, 0, 2, Qt.AlignRight)
        layout.addWidget(footer)

        self._apply_theme_styles()
        # Apply the adaptive size and parent-centered position before the dialog
        # becomes visible. Scheduling this after show()/exec() makes Windows draw
        # the dialog once at the default position and then redraw it after resize
        # + move, which caused a visible flicker/shake when opening Help.
        self._apply_initial_dialog_size()

    def _current_screen_available_geometry(self):
        parent = self.parentWidget()
        screen = None
        if parent is not None and parent.window() is not None:
            parent_window = parent.window()
            if parent_window.screen() is not None:
                screen = parent_window.screen()
            else:
                screen = QApplication.screenAt(parent_window.frameGeometry().center())
        if screen is None:
            screen = self.screen() or QApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _apply_initial_dialog_size(self):
        if getattr(self, "_help_initial_size_applied", False):
            return
        if self.windowState() & Qt.WindowState.WindowMaximized:
            self._help_initial_size_applied = True
            return

        layout = self.layout()
        if layout is not None:
            layout.activate()
        if hasattr(self, "_help_content") and self._help_content.layout() is not None:
            self._help_content.layout().activate()

        available = self._current_screen_available_geometry()
        max_width = int(available.width() * 0.90) if available is not None else 760
        # Open comfortably smaller than the available monitor height. The dialog
        # remains resizable/maximizable, and the scroll area handles overflow.
        max_initial_height = int(available.height() * 0.82) if available is not None else 680

        layout_hint = self.layout().sizeHint() if self.layout() is not None else self.sizeHint()
        content_hint = self._help_content.sizeHint() if hasattr(self, "_help_content") else QSize(0, 0)
        scroll_hint = self._help_scroll.sizeHint() if hasattr(self, "_help_scroll") else QSize(0, 0)

        extra_height = max(0, layout_hint.height() - scroll_hint.height())
        natural_height = max(layout_hint.height(), content_hint.height() + extra_height + 8)
        natural_width = max(700, layout_hint.width(), content_hint.width() + 48)

        width = min(max(560, natural_width), max(560, max_width))
        height = min(max_initial_height, max(420, natural_height))

        # Do not cap the maximum size. A maximum bound prevents the native
        # maximize/restore controls from behaving normally on some Windows setups.
        self.setMaximumSize(16777215, 16777215)
        self.resize(width, height)
        self._center_initial_position(available)
        self._help_initial_size_applied = True

    def _center_initial_position(self, available):
        if available is None:
            return
        parent = self.parentWidget()
        if parent is not None and parent.window() is not None:
            reference_rect = parent.window().frameGeometry()
        else:
            reference_rect = available

        target_x = reference_rect.center().x() - self.width() // 2
        target_y = reference_rect.center().y() - self.height() // 2

        target_x = max(available.left(), min(target_x, available.right() - self.width() + 1))
        target_y = max(available.top(), min(target_y, available.bottom() - self.height() + 1))
        self.move(target_x, target_y)


class AboutDialog(_BaseAncillaryDialog):
    def __init__(self, parent=None):
        super().__init__("Sobre", parent)
        self.setObjectName("AboutDialog")
        self.setFixedSize(468, 264)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 18, 20, 14)
        layout.setSpacing(0)

        icon_label = QLabel(self)
        icon_pixmap = QPixmap(asset_icon("compactme_icon_256.png"))
        if not icon_pixmap.isNull():
            icon_label.setPixmap(
                icon_pixmap.scaled(
                    QSize(42, 42),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
        icon_label.setFixedSize(46, 46)
        icon_label.setAlignment(Qt.AlignCenter)

        title = QLabel(APP_NAME, self)
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(max(title_font.pointSize(), 14))
        title.setFont(title_font)
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)
        header_row.addWidget(icon_label)
        header_row.addWidget(title, 1)

        subtitle = QLabel("Aplicativo para compactação de arquivos de áudio e vídeo", self)
        subtitle_font = subtitle.font()
        subtitle_font.setPointSize(max(subtitle_font.pointSize(), 10))
        subtitle.setFont(subtitle_font)
        subtitle.setWordWrap(True)
        subtitle.setTextInteractionFlags(Qt.TextSelectableByMouse)

        description = QLabel(
            f"O {APP_NAME} ajuda a reduzir o tamanho de arquivos de mídia, "
            "ajustando compressão, resolução, bitrate e perfis de saída de forma prática.",
            self,
        )
        description.setWordWrap(True)
        description.setTextInteractionFlags(Qt.TextSelectableByMouse)

        info = QLabel(
            f"Versão: {APP_VERSION}\n"
            "Software desenvolvido com assistência de IA.\n\n"
            "© 2026 Marcelo Tutida. Todos os direitos reservados.",
            self,
        )
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)

        body_widget = QWidget(self)
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(6, 0, 0, 0)
        body_layout.setSpacing(0)

        body_layout.addWidget(subtitle)
        body_layout.addSpacing(8)
        body_layout.addWidget(description)
        body_layout.addSpacing(10)
        body_layout.addWidget(info)

        layout.addLayout(header_row)
        layout.addSpacing(8)
        layout.addWidget(body_widget)
        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        close_button = buttons.button(QDialogButtonBox.Close)
        if close_button is not None:
            close_button.setText("Fechar")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._apply_theme_styles()
