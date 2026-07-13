"""
SecureCartography v2 - Login Dialog

Vault unlock screen with password authentication.
Matches the mockup design with icon, title, and styled inputs.
"""
import traceback
from pathlib import Path
from typing import Optional, Callable

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QIcon, QPixmap, QColor, QPalette
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QWidget, QMessageBox, QApplication,
    QGraphicsDropShadowEffect
)

from .themes import ThemeColors, ThemeManager, ThemeName, fix_all_comboboxes, StyledComboBox, repolish_theme
from .settings import SettingsManager, get_settings
from sc2.scng.creds.vault import VaultLockedOut


# =============================================================================
# Login-screen design tokens — from design_handoff_login_screen (high-fidelity).
# These are login-card-specific colors, deliberately distinct from the global
# app theme (e.g. Nord uses a light input on a dark card). Exact hex per handoff.
# shadow = (blurRadius, xOffset, yOffset, QColor)
# =============================================================================
LOGIN_THEMES = {
    ThemeName.LIGHT: {
        "outerBg": "#121319", "cardBg": "#fbfbfc", "cardBorder": "#e4e7ec",
        "shadow": (70, 0, 24, QColor(0, 0, 0, 115)),
        "topBar": "#2451e0",
        "titleColor": "#111827", "labelColor": "#6b7686",
        "inputBg": "#eef0f3", "inputBorder": "#e0e3e8", "inputText": "#1f2937",
        "placeholderColor": "#9aa3b0", "iconColor": "#2451e0", "eyeColor": "#7a4a3f",
        "buttonA": "#1c3fe0", "buttonB": "#2b52e6", "buttonText": "#ffffff",
        "resetBorder": "#e2483f", "resetText": "#e2483f", "versionColor": "#a2a9b5",
        "pillBg": "#f2f3f5", "pillBorder": "#e0e3e8", "pillText": "#374151",
        "menuBg": "#ffffff", "menuBorder": "#e0e3e8", "menuHover": "#f2f3f5",
    },
    ThemeName.DARK: {
        "outerBg": "#000000", "cardBg": "#0e0d0c", "cardBorder": "#2a2216",
        "shadow": (70, 0, 24, QColor(0, 0, 0, 180)),
        "topBar": "#d9b23c",
        "titleColor": "#f5f1e6", "labelColor": "#a89f8d",
        "inputBg": "#1a140a", "inputBorder": "#332a18", "inputText": "#e8e2d4",
        "placeholderColor": "#6b6152", "iconColor": "#d9b23c", "eyeColor": "#c96b5c",
        "buttonA": "#c9a227", "buttonB": "#e2c04a", "buttonText": "#1a1408",
        "resetBorder": "#b33a34", "resetText": "#e0524a", "versionColor": "#5c574c",
        "pillBg": "#141210", "pillBorder": "#332a18", "pillText": "#e8e2d4",
        "menuBg": "#161310", "menuBorder": "#332a18", "menuHover": "#221c11",
    },
    ThemeName.CYBER: {
        "outerBg": "#000000", "cardBg": "#080b10", "cardBorder": "#123039",
        "shadow": (60, 0, 20, QColor(37, 224, 232, 90)),
        "topBar": "#25e0e8",
        "titleColor": "#eaf6f8", "labelColor": "#7fb8c2",
        "inputBg": "#0c1c20", "inputBorder": "#16414a", "inputText": "#d9f7fa",
        "placeholderColor": "#4d7580", "iconColor": "#25e0e8", "eyeColor": "#ef3f6c",
        "buttonA": "#18d3d8", "buttonB": "#37f0e8", "buttonText": "#052226",
        "resetBorder": "#d81e4f", "resetText": "#ef3f6c", "versionColor": "#3f6870",
        "pillBg": "#0c1a1e", "pillBorder": "#16414a", "pillText": "#bdeef2",
        "menuBg": "#0a1519", "menuBorder": "#16414a", "menuHover": "#102830",
    },
    ThemeName.NORD: {
        "outerBg": "#20242e", "cardBg": "#3b4252", "cardBorder": "#4c566a",
        "shadow": (70, 0, 24, QColor(0, 0, 0, 128)),
        "topBar": "#88c0d0",
        "titleColor": "#eceff4", "labelColor": "#c2cbdb",
        "inputBg": "#e5e9f0", "inputBorder": "#d8dee9", "inputText": "#2e3440",
        "placeholderColor": "#8a94a8", "iconColor": "#5e81ac", "eyeColor": "#bf616a",
        "buttonA": "#7fa8bd", "buttonB": "#8fb9cc", "buttonText": "#1f2733",
        "resetBorder": "#bf616a", "resetText": "#e88e93", "versionColor": "#8a94a8",
        "pillBg": "#434c5e", "pillBorder": "#4c566a", "pillText": "#eceff4",
        "menuBg": "#3b4252", "menuBorder": "#4c566a", "menuHover": "#434c5e",
    },
}


def login_tokens(theme_name: ThemeName) -> dict:
    """Login-card design tokens for a theme (falls back to Light)."""
    return LOGIN_THEMES.get(theme_name, LOGIN_THEMES[ThemeName.LIGHT])


class IconLabel(QLabel):
    """
    Label that renders a simple icon using unicode or custom painting.
    For PyQt6, we'll use a simple approach with unicode symbols
    or custom SVG rendering.
    """

    def __init__(self, icon_char: str = "🔒", size: int = 32, parent=None):
        super().__init__(parent)
        self.setText(icon_char)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.font()
        font.setPointSize(size)
        self.setFont(font)


class ThemeBanner(QLabel):
    """
    Theme-aware banner image widget.

    Displays different banner images based on the active theme.
    Automatically handles scaling and aspect ratio preservation.
    """

    # Mapping of theme names to banner image filenames
    BANNER_MAP = {
        ThemeName.CYBER: "banner_cyber.png",
        ThemeName.DARK: "banner_amber.png",
        ThemeName.LIGHT: "banner_light.png",
    }

    def __init__(self, theme_name: ThemeName = ThemeName.CYBER,
                 max_width: int = 320, max_height: int = 120, parent=None):
        super().__init__(parent)
        self._theme_name = theme_name
        self._max_width = max_width
        self._max_height = max_height
        self._assets_path = self._find_assets_path()

        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent; border: none;")

        self._load_banner()

    def _find_assets_path(self) -> Path:
        """Locate the assets directory."""
        # Try relative to this file
        local_path = Path(__file__).parent / "assets"
        if local_path.exists():
            return local_path

        # Try from package root
        package_path = Path(__file__).parent.parent / "ui" / "assets"
        if package_path.exists():
            return package_path

        # Fallback - current directory
        return Path("assets")

    def _load_banner(self):
        """Load and display the banner for current theme."""
        banner_file = self.BANNER_MAP.get(self._theme_name, "banner_cyber.png")
        banner_path = self._assets_path / banner_file

        if banner_path.exists():
            pixmap = QPixmap(str(banner_path))

            # Scale while preserving aspect ratio
            scaled = pixmap.scaled(
                self._max_width,
                self._max_height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.setPixmap(scaled)
            self.setFixedSize(scaled.size())
        else:
            # Fallback to text if image not found
            self.setText(f"[Banner: {banner_file}]")
            self.setFixedSize(self._max_width, self._max_height)

    def set_theme(self, theme_name: ThemeName):
        """Switch to a different theme's banner."""
        if theme_name != self._theme_name:
            self._theme_name = theme_name
            self._load_banner()

    def set_max_size(self, width: int, height: int):
        """Update maximum dimensions and reload."""
        self._max_width = width
        self._max_height = height
        self._load_banner()


class PasswordInput(QWidget):
    """
    Styled password input with visibility toggle,
    matching the mockup's ThemedInput component.
    """

    textChanged = pyqtSignal(str)
    returnPressed = pyqtSignal()

    def __init__(self, placeholder: str = "Enter password...", parent=None):
        super().__init__(parent)
        self._setup_ui(placeholder)

    def _setup_ui(self, placeholder: str):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Container frame for styling
        self.container = QFrame()
        self.container.setObjectName("passwordContainer")
        container_layout = QHBoxLayout(self.container)
        container_layout.setContentsMargins(12, 0, 8, 0)
        container_layout.setSpacing(8)

        # Shield icon (as text for simplicity)
        self.icon_label = QLabel("🛡")
        self.icon_label.setStyleSheet("background: transparent; border: none;")
        container_layout.addWidget(self.icon_label)

        # Password input
        self.input = QLineEdit()
        self.input.setPlaceholderText(placeholder)
        self.input.setEchoMode(QLineEdit.EchoMode.Password)
        self.input.setStyleSheet("""
            QLineEdit {
                background: transparent;
                border: none;
                padding: 10px 0;
                font-size: 14px;
            }
        """)
        self.input.textChanged.connect(self.textChanged.emit)
        self.input.returnPressed.connect(self.returnPressed.emit)
        container_layout.addWidget(self.input, 1)

        # Visibility toggle
        self.toggle_btn = QPushButton("👁")
        self.toggle_btn.setFixedSize(32, 32)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover {
                opacity: 0.7;
            }
        """)
        self.toggle_btn.clicked.connect(self._toggle_visibility)
        container_layout.addWidget(self.toggle_btn)

        layout.addWidget(self.container)

        self._visible = False

    def _toggle_visibility(self):
        self._visible = not self._visible
        if self._visible:
            self.input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_btn.setText("🙈")
        else:
            self.input.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_btn.setText("👁️")

    def text(self) -> str:
        return self.input.text()

    def setText(self, text: str):
        self.input.setText(text)

    def clear(self):
        self.input.clear()

    def setFocus(self):
        self.input.setFocus()

    def apply_theme(self, tokens: dict):
        """Apply login-card design tokens to the input pill."""
        self.container.setStyleSheet(f"""
            QFrame#passwordContainer {{
                background-color: {tokens['inputBg']};
                border: 1px solid {tokens['inputBorder']};
                border-radius: 10px;
            }}
        """)
        self.icon_label.setStyleSheet(f"""
            background: transparent;
            border: none;
            color: {tokens['iconColor']};
        """)
        self.input.setStyleSheet(f"""
            QLineEdit {{
                background: transparent;
                border: none;
                padding: 10px 0;
                font-size: 14px;
                color: {tokens['inputText']};
            }}
        """)
        # Placeholder color is a palette role, not a QSS property.
        pal = self.input.palette()
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(tokens['placeholderColor']))
        self.input.setPalette(pal)
        self.toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                font-size: 14px;
                color: {tokens['eyeColor']};
            }}
            QPushButton:hover {{
                color: {tokens['eyeColor']};
            }}
        """)


class LoginDialog(QDialog):
    """
    Vault login dialog.

    Handles:
    - First-time vault initialization
    - Vault unlock with password
    - Vault reset (delete and reinitialize)

    Signals:
        vault_unlocked: Emitted when vault is successfully unlocked
    """

    vault_unlocked = pyqtSignal(object)  # Emits the unlocked vault

    def __init__(
        self,
        vault,  # CredentialVault instance
        theme_manager: ThemeManager,
        settings: Optional[SettingsManager] = None,
        parent=None
    ):
        super().__init__(parent)
        self.vault = vault
        self.theme_manager = theme_manager
        self.settings = settings or get_settings()

        self.setWindowTitle("Secure Cartography")
        self.setFixedSize(420, 620)  # Fixed size for clean layout
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._setup_ui()
        self._apply_theme()

    def _setup_ui(self):
        """Build the login dialog UI."""
        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # Content card
        self.card = QFrame()
        self.card.setObjectName("loginCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(32, 24, 32, 24)
        card_layout.setSpacing(0)  # Control spacing manually

        # Top accent line
        self.accent_line = QFrame()
        self.accent_line.setFixedHeight(3)
        self.accent_line.setObjectName("accentLine")
        card_layout.addWidget(self.accent_line)

        # Theme selector row (right-aligned)
        theme_row = QHBoxLayout()
        theme_row.setContentsMargins(0, 12, 0, 0)
        theme_row.addStretch()

        self.theme_combo = StyledComboBox()
        self.theme_combo.setFixedWidth(120)
        self.theme_combo.setFixedHeight(32)
        self.theme_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_combo.addItem("⚡ Cyber", ThemeName.CYBER)
        self.theme_combo.addItem("🌙 Dark", ThemeName.DARK)
        self.theme_combo.addItem("☀️ Light", ThemeName.LIGHT)
        self.theme_combo.addItem("❄️ Nord", ThemeName.NORD)

        # Set popup colors from current theme
        self.theme_combo.set_theme_colors(self.theme_manager.theme)

        # Set current theme from settings
        current_theme = self.theme_manager.theme_name
        for i in range(self.theme_combo.count()):
            if self.theme_combo.itemData(i) == current_theme:
                self.theme_combo.setCurrentIndex(i)
                break

        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self.theme_combo)

        card_layout.addLayout(theme_row)

        # Spacer
        card_layout.addSpacing(16)

        # Banner image (theme-aware)
        banner_container = QWidget()
        banner_container.setObjectName("bannerContainer")
        banner_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        banner_layout = QHBoxLayout(banner_container)
        banner_layout.setContentsMargins(0, 0, 0, 0)
        banner_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Logo slot (188×108, white rounded card) holding the TUIASI crest logo.
        # Design tokens say the banner bg is transparent and the logo itself
        # carries the white card — we render a white rounded QLabel for all
        # themes so it matches the handoff on both light and dark cards.
        self.banner = QLabel()
        self.banner.setObjectName("logoBanner")
        self.banner.setFixedSize(188, 108)
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setStyleSheet(
            "QLabel#logoBanner { background: #ffffff; border-radius: 10px; }"
        )
        logo_path = Path(__file__).parent / "assets" / "tuiasi_logo.png"
        if logo_path.exists():
            pm = QPixmap(str(logo_path))
            if not pm.isNull():
                pm = pm.scaled(
                    170, 94,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.banner.setPixmap(pm)
        banner_layout.addWidget(self.banner)

        card_layout.addWidget(banner_container)

        # Spacer
        card_layout.addSpacing(16)

        # Title (per handoff: 19px, weight 800, letter-spacing 0.4px)
        self.title_label = QLabel("MAPARE REȚEA & CONFIG PUSH")
        self.title_label.setObjectName("heading")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setFixedHeight(52)
        font = self.title_label.font()
        font.setPixelSize(19)
        font.setWeight(QFont.Weight.ExtraBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.4)
        self.title_label.setFont(font)
        card_layout.addWidget(self.title_label)

        # Subtitle removed in the redesign — keep a hidden widget so any
        # references remain valid, but it occupies no space.
        self.subtitle_label = QLabel("")
        self.subtitle_label.setObjectName("subheading")
        self.subtitle_label.setFixedHeight(0)
        self.subtitle_label.hide()
        card_layout.addWidget(self.subtitle_label)

        # Spacer before form
        card_layout.addSpacing(28)

        # Password section
        form_container = QWidget()
        form_container.setObjectName("formContainer")
        form_container.setAutoFillBackground(False)
        form_layout = QVBoxLayout(form_container)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(10)

        self.password_label = QLabel("MASTER PASSWORD")
        self.password_label.setObjectName("sectionTitle")
        self.password_label.setFixedHeight(16)
        form_layout.addWidget(self.password_label)

        self.password_input = PasswordInput("Enter master key...")
        self.password_input.setFixedHeight(48)
        form_layout.addWidget(self.password_input)

        # Status message (for errors)
        self.status_label = QLabel("")
        self.status_label.setObjectName("statusError")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setFixedHeight(40)
        self.status_label.hide()
        form_layout.addWidget(self.status_label)

        self.form_container = form_container  # Save reference for styling
        card_layout.addWidget(form_container)

        # Spacer before buttons
        card_layout.addSpacing(24)

        # Buttons
        button_container = QWidget()
        button_container.setObjectName("buttonContainer")
        button_layout = QVBoxLayout(button_container)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(12)

        self.unlock_btn = QPushButton("🛡  UNLOCK SYSTEM")
        self.unlock_btn.setFixedHeight(48)
        self.unlock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unlock_btn.clicked.connect(self._on_unlock)
        button_layout.addWidget(self.unlock_btn)

        self.reset_btn = QPushButton("↺  RESET CREDENTIALS")
        self.reset_btn.setFixedHeight(48)
        self.reset_btn.setObjectName("danger")
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.clicked.connect(self._on_reset)
        button_layout.addWidget(self.reset_btn)

        self.button_container = button_container  # Save reference for styling
        card_layout.addWidget(button_container)

        # Spacer before version
        card_layout.addSpacing(24)

        # Version info
        self.version_label = QLabel("v2.0")
        self.version_label.setObjectName("muted")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.version_label.setFixedHeight(16)
        card_layout.addWidget(self.version_label)

        # Bottom padding
        card_layout.addSpacing(8)

        main_layout.addWidget(self.card)

        # Connect enter key
        self.password_input.returnPressed.connect(self._on_unlock)

        # Update UI based on vault state
        self._update_for_vault_state()

    def _update_for_vault_state(self):
        """Update UI based on whether vault exists."""
        if self.vault.is_initialized:
            self.unlock_btn.setText("🛡  UNLOCK SYSTEM")
            self.reset_btn.show()
        else:
            self.unlock_btn.setText("🛡  CREATE VAULT")
            self.reset_btn.hide()

    def _apply_theme(self):
        """Apply the login-card design tokens for the current theme."""
        theme = self.theme_manager.theme
        t = login_tokens(self.theme_manager.theme_name)

        # Dialog background is transparent (for the rounded card effect)
        self.setStyleSheet("background-color: transparent;")

        # Card — cardBg + 1px cardBorder + 22px radius
        self.card.setStyleSheet(f"""
            QFrame#loginCard {{
                background-color: {t['cardBg']};
                border: 1px solid {t['cardBorder']};
                border-radius: 22px;
            }}
        """)

        # Intermediate layout containers stay transparent
        transparent_style = "background-color: transparent; border: none;"
        self.form_container.setStyleSheet(transparent_style)
        self.button_container.setStyleSheet(transparent_style)

        # Drop shadow (per-theme; Cyber uses an accent glow)
        blur, xoff, yoff, shadow_color = t['shadow']
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(blur)
        shadow.setXOffset(xoff)
        shadow.setYOffset(yoff)
        shadow.setColor(shadow_color)
        self.card.setGraphicsEffect(shadow)

        # Top accent bar — transparent → accent → transparent
        self.accent_line.setStyleSheet(f"""
            QFrame#accentLine {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 transparent,
                    stop:0.5 {t['topBar']},
                    stop:1 transparent
                );
                border: none;
            }}
        """)
        try:
            fix_all_comboboxes(self, self.theme_manager.theme)
        except Exception:
            traceback.print_exc()

        # Logo banner is theme-independent (white card) — nothing to re-theme.

        # Title — titleColor
        self.title_label.setStyleSheet(f"""
            QLabel#heading {{
                color: {t['titleColor']};
                background: transparent;
                border: none;
            }}
        """)

        # MASTER PASSWORD label — labelColor, 11px/700/0.6px
        self.password_label.setStyleSheet(f"""
            QLabel#sectionTitle {{
                color: {t['labelColor']};
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
                background: transparent;
                border: none;
            }}
        """)

        # Error status — reset/error tone
        self.status_label.setStyleSheet(f"""
            QLabel#statusError {{
                color: {t['resetText']};
                background: transparent;
                border: none;
                padding: 8px;
            }}
        """)

        # Version — versionColor
        self.version_label.setStyleSheet(f"""
            QLabel#muted {{
                color: {t['versionColor']};
                background: transparent;
                border: none;
            }}
        """)

        # Password input pill (login tokens)
        self.password_input.apply_theme(t)

        # Theme switcher pill + dropdown menu (pill/menu tokens)
        self.theme_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {t['pillBg']};
                border: 1px solid {t['pillBorder']};
                border-radius: 9px;
                padding: 7px 12px;
                color: {t['pillText']};
                font-size: 12px;
                font-weight: 600;
            }}
            QComboBox:hover {{
                border-color: {t['topBar']};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 18px;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {t['pillText']};
                width: 0;
                height: 0;
                margin-right: 6px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {t['menuBg']};
                border: 1px solid {t['menuBorder']};
                border-radius: 10px;
                selection-background-color: {t['menuHover']};
                selection-color: {t['pillText']};
                color: {t['pillText']};
                outline: none;
                padding: 4px;
            }}
            QComboBox QAbstractItemView::item {{
                padding: 9px 14px;
                min-height: 24px;
                border-radius: 6px;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: {t['menuHover']};
            }}
        """)

        # Unlock button — buttonA→buttonB gradient, buttonText
        self._style_unlock_idle()

        # Reset button — transparent, 1.5px resetBorder, resetText
        self.reset_btn.setStyleSheet(f"""
            QPushButton#danger {{
                background-color: transparent;
                color: {t['resetText']};
                border: 1.5px solid {t['resetBorder']};
                border-radius: 11px;
                padding: 13px 0;
                font-weight: 700;
                font-size: 12px;
                letter-spacing: 1px;
            }}
            QPushButton#danger:hover {{
                background-color: {t['resetBorder']};
                color: #ffffff;
            }}
        """)

        # Force an immediate repaint so the new theme paints now, not only
        # when a field next gets a hover/style event.
        repolish_theme(self)

    def _style_unlock_idle(self):
        """Idle unlock-button style — buttonA→buttonB gradient, buttonText."""
        t = login_tokens(self.theme_manager.theme_name)
        self.unlock_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 {t['buttonA']},
                    stop:1 {t['buttonB']}
                );
                color: {t['buttonText']};
                border: none;
                border-radius: 11px;
                padding: 14px 0;
                font-weight: 700;
                font-size: 13px;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 {t['buttonB']},
                    stop:1 {t['buttonA']}
                );
            }}
        """)

    def _show_unlock_success(self):
        """Success state: green gradient + '✓ ACCESS GRANTED' (per handoff)."""
        self._unlock_pending = True
        self.unlock_btn.setEnabled(False)
        self.password_input.input.setEnabled(False)
        self.unlock_btn.setText("✓  ACCESS GRANTED")
        self.unlock_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #22c55e, stop:1 #16a34a
                );
                color: #ffffff;
                border: none;
                border-radius: 11px;
                padding: 14px 0;
                font-weight: 700;
                font-size: 13px;
                letter-spacing: 1px;
            }
        """)

    def _finish_unlock(self):
        """Emit the unlocked vault and close, after the success flash."""
        self.vault_unlocked.emit(self.vault)
        self.accept()

    def _show_error(self, message: str):
        """Show error message."""
        self.status_label.setText(message)
        self.status_label.show()

    def _hide_error(self):
        """Hide error message."""
        self.status_label.hide()

    def _on_unlock(self):
        """Handle unlock/create button click."""
        # Ignore re-entry while the success flash is pending (guards against a
        # second Enter/click during the 450ms before the window transitions).
        if getattr(self, "_unlock_pending", False):
            return
        password = self.password_input.text()

        if not password:
            self._show_error("Please enter a password")
            return

        self._hide_error()

        try:
            if self.vault.is_initialized:
                # Unlock existing vault
                self.vault.unlock(password)
            else:
                # Initialize new vault
                if len(password) < 8:
                    self._show_error("Password must be at least 8 characters")
                    return
                self.vault.initialize(password)

            # Success — show the ACCESS GRANTED state briefly, then transition.
            self._show_unlock_success()
            QTimer.singleShot(450, self._finish_unlock)

        except VaultLockedOut as e:
            # Surface lockout messages verbatim so the user sees the cooldown.
            self._show_error(str(e))
            self.password_input.clear()
            self.password_input.setFocus()
        except Exception as e:
            error_msg = str(e)
            if "Invalid" in error_msg or "password" in error_msg.lower():
                self._show_error("Invalid password")
            else:
                self._show_error(f"Error: {error_msg}")
            self.password_input.clear()
            self.password_input.setFocus()

    def _on_reset(self):
        """Handle reset credentials button click."""
        reply = QMessageBox.warning(
            self,
            "Reset Credentials",
            "This will DELETE all stored credentials.\n\n"
            "This action cannot be undone.\n\n"
            "Are you sure you want to continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                # Delete vault database
                db_path = self.vault.db_path
                if db_path.exists():
                    db_path.unlink()

                # Also delete salt file if separate
                salt_path = db_path.parent / ".salt"
                if salt_path.exists():
                    salt_path.unlink()

                # Reinitialize vault object
                self.vault = type(self.vault)(db_path)
                self._update_for_vault_state()

                self._show_error("Vault reset. Please create a new password.")
                self.password_input.clear()
                self.password_input.setFocus()

            except Exception as e:
                self._show_error(f"Reset failed: {e}")

    def showEvent(self, event):
        """Focus password input on show."""
        super().showEvent(event)
        self.password_input.setFocus()

    def set_theme(self, theme_name: ThemeName):
        """Change theme and reapply styling."""
        self.theme_manager.set_theme(theme_name)
        self._apply_theme()

    def _on_theme_changed(self, index: int):
        """Handle theme dropdown selection."""
        theme = self.theme_combo.itemData(index)
        if theme:
            # Update theme manager
            self.theme_manager.set_theme(theme)

            # Save to settings
            self.settings.set_theme(theme)

            # Update application-wide stylesheet
            app = QApplication.instance()
            if app:
                app.setStyleSheet(self.theme_manager.stylesheet)

            # Reapply dialog-specific styling
            self._apply_theme()


# =============================================================================
# Mock Vault for Testing
# =============================================================================

class MockVault:
    """
    Mock vault for testing UI without real credentials.
    Password 'testpass' unlocks, anything else fails.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._initialized = True
        self._unlocked = False
        self.db_path = db_path or Path.home() / ".scng" / "test_vault.db"

    @property
    def is_initialized(self):
        return self._initialized

    @property
    def is_unlocked(self):
        return self._unlocked

    def initialize(self, password):
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        self._initialized = True
        self._unlocked = True

    def unlock(self, password):
        if password != "testpass":
            raise Exception("Invalid vault password")
        self._unlocked = True
        return True

    def lock(self):
        self._unlocked = False


# =============================================================================
# Standalone testing
# =============================================================================

if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)

    theme_manager = ThemeManager(ThemeName.CYBER)
    vault = MockVault()

    dialog = LoginDialog(vault, theme_manager)

    # Test theme switching
    # dialog.set_theme(ThemeName.DARK)
    # dialog.set_theme(ThemeName.LIGHT)

    if dialog.exec() == QDialog.DialogCode.Accepted:
        print("Vault unlocked successfully!")
    else:
        print("Login cancelled")

    sys.exit()