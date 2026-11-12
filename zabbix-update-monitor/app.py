"""Run: python app.py [--demo]."""
import json
import os
from pathlib import Path
import sys
from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QStandardPaths
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, QAbstractItemView,
    QFrame, QProgressBar,
)
from core import ZabbixApi, ApiError, classify, normalize_url


COLORS = {
    "green": ("#e3f5eb", "#175c3b"), "yellow": ("#fff3cc", "#795500"),
    "red": ("#fce4e6", "#942d38"), "unknown": ("#edf0f5", "#526079"),
}
STYLE = """
QWidget { font-family: 'Segoe UI', sans-serif; font-size: 13px; color: #23324b; }
QMainWindow, QDialog { background: #f3f6fb; }
QFrame#panel { background: white; border: 1px solid #dde5f0; border-radius: 12px; }
QLabel#title { font-size: 28px; font-weight: 700; color: #172941; }
QLabel#muted { color: #64748b; }
QLineEdit { padding: 10px; background: white; border: 1px solid #cbd5e1; border-radius: 7px; }
QLineEdit:focus { border: 1px solid #3b78da; }
QPushButton { padding: 10px 18px; background: white; border: 1px solid #cbd5e1; border-radius: 7px; font-weight: 600; }
QPushButton:hover { background: #eaf1fb; }
QPushButton#primary { background: #2765cb; color: white; border: none; }
QPushButton#primary:hover { background: #1c50a7; }
QPushButton:disabled { background: #e2e8f0; color: #8492a6; }
QTableWidget { background: white; border: 1px solid #dde5f0; border-radius: 8px; gridline-color: #ffffff; }
QHeaderView::section { background: #eaf0f8; color: #52627a; padding: 12px; border: none; font-weight: 600; }
QTableWidget::item { padding: 8px; }
QTableWidget::item:selected { background: #cddfff; color: #193b70; }
QListWidget { background: white; border: 1px solid #d9e2ee; border-radius: 8px; padding: 6px; }
QListWidget::item { padding: 8px; }
QProgressBar { border: none; background: #e3eaf4; height: 4px; }
QProgressBar::chunk { background: #2765cb; }
"""


class FetchThread(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, url, token, selected, demo=False):
        super().__init__()
        self.url, self.token, self.selected, self.demo = url, token, selected, demo

    def run(self):
        try:
            if self.demo:
                hosts = [{"hostid": str(i), "host": f"srv-{i:02}", "name": name}
                         for i, name in enumerate(["Сервер приложений", "База данных", "Файловый сервер",
                                                   "Контроллер домена", "Резервное копирование", "Почтовый сервер"], 1)]
                items = [{"hostid": str(i), "lastvalue": (date.today() - timedelta(days=age)).isoformat(),
                          "lastclock": str(int(datetime.now().timestamp())), "state": "0", "status": "0"}
                         for i, age in enumerate([5, 23, 30, 48, 12], 1)]
                version = "демо"
            else:
                api = ZabbixApi(self.url, self.token)
                version = api.connect()
                hosts = api.hosts()
                ids = [h["hostid"] for h in hosts if self.selected is None or h["hostid"] in self.selected]
                items = api.items(ids)
            self.ready.emit({"hosts": hosts, "items": items, "version": version})
        except ApiError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("Не удалось обработать ответ сервера. Проверьте адрес и совместимость API.")
        finally:
            self.token = ""


class HostDialog(QDialog):
    def __init__(self, hosts, selected, parent):
        super().__init__(parent)
        self.setWindowTitle("Выбор узлов сети")
        self.resize(580, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Отметьте узлы, даты обновления которых нужно проверять"))
        search = QLineEdit(placeholderText="Поиск по имени или техническому имени…")
        layout.addWidget(search)
        self.list = QListWidget()
        for host in hosts:
            item = QListWidgetItem(f"{host['name']}  ·  {host['host']}")
            item.setData(Qt.ItemDataRole.UserRole, host["hostid"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if host["hostid"] in selected else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        layout.addWidget(self.list)
        search.textChanged.connect(self.filter)
        actions = QHBoxLayout()
        for title, state in [("Выбрать найденные", Qt.CheckState.Checked), ("Снять найденные", Qt.CheckState.Unchecked)]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, s=state: self.mark(s))
            actions.addWidget(button)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Применить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def filter(self, text):
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(text.casefold() not in item.text().casefold())

    def mark(self, state):
        for i in range(self.list.count()):
            item = self.list.item(i)
            if not item.isHidden():
                item.setCheckState(state)

    def selected_ids(self):
        return {self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked}


class NumberItem(QTableWidgetItem):
    def __lt__(self, other):
        return self.data(Qt.ItemDataRole.UserRole) < other.data(Qt.ItemDataRole.UserRole)


class Window(QMainWindow):
    def __init__(self, demo=False):
        super().__init__()
        self.demo, self.worker, self.hosts, self.items = demo, None, [], []
        self.setWindowTitle("Zabbix · Контроль обновлений" + (" · ДЕМО" if demo else ""))
        self.resize(1180, 780)
        self.config_path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)) / "settings.json"
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                config = {}
        except (OSError, ValueError):
            config = {}
        ids = config.get("hostids")
        self.selected = set(map(str, ids)) if isinstance(ids, list) else None
        self.selection_url = config.get("url", "")
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        layout.addWidget(QLabel("Контроль обновлений", objectName="title"))
        layout.addWidget(QLabel("Дата последнего обновления серверов  /  is-upd-date", objectName="muted"))
        panel = QFrame(objectName="panel")
        form = QVBoxLayout(panel)
        form.setContentsMargins(18, 16, 18, 16)
        form.addWidget(QLabel("ПОДКЛЮЧЕНИЕ К ZABBIX"))
        fields = QHBoxLayout()
        self.url = QLineEdit(os.getenv("ZABBIX_URL", str(config.get("url", ""))), placeholderText="https://zabbix.example.com/zabbix")
        self.token = QLineEdit(os.getenv("ZABBIX_API_TOKEN", ""), placeholderText="API-токен (не сохраняется на диск)")
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        fields.addWidget(self.url, 3)
        fields.addWidget(self.token, 2)
        self.refresh = QPushButton("Обновить", objectName="primary")
        self.refresh.clicked.connect(self.load)
        fields.addWidget(self.refresh)
        form.addLayout(fields)
        layout.addWidget(panel)
        tools = QHBoxLayout()
        self.choose = QPushButton("Выбрать узлы")
        self.choose.setEnabled(False)
        self.choose.clicked.connect(self.choose_hosts)
        tools.addWidget(self.choose)
        self.selection_label = QLabel("Сначала подключитесь к Zabbix", objectName="muted")
        tools.addWidget(self.selection_label)
        tools.addStretch()
        self.search = QLineEdit(placeholderText="Найти узел в таблице…")
        self.search.setMaximumWidth(320)
        self.search.textChanged.connect(self.filter_rows)
        tools.addWidget(self.search)
        layout.addLayout(tools)
        legend = QHBoxLayout()
        self.counters = {}
        for level, title in [("green", "Менее 23 дней"), ("yellow", "23–29 дней"), ("red", "От 30 дней"), ("unknown", "Нет корректной даты")]:
            label = QLabel()
            bg, fg = COLORS[level]
            label.setStyleSheet(f"background: {bg}; color: {fg}; padding: 12px; border-radius: 8px; font-weight: 600;")
            legend.addWidget(label)
            self.counters[level] = (label, title)
        layout.addLayout(legend)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Узел сети", "Техническое имя", "Дата обновления", "Дней", "Состояние", "Данные получены"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = QLabel("", objectName="muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        for label, title in self.counters.values():
            label.setText(f"{title}  ·  0")
        QTimer.singleShot(0, self.startup)

    def startup(self):
        if self.demo or (self.url.text().strip() and self.token.text().strip()):
            self.load()
        else:
            self.status.setText("Введите адрес Zabbix и API-токен, затем нажмите «Обновить». При следующих запусках с токеном в ZABBIX_API_TOKEN данные загрузятся автоматически.")

    def save_config(self):
        if self.demo:
            return
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps({"url": self.selection_url,
                "hostids": sorted(self.selected) if self.selected is not None else None}, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            self.status.setText(self.status.text() + " Не удалось сохранить настройки.")

    def load(self):
        if self.worker is not None:
            return
        if not self.demo:
            try:
                url = normalize_url(self.url.text())
            except ApiError as exc:
                self.status.setText(str(exc))
                return
            if not self.token.text().strip():
                self.status.setText("Введите API-токен.")
                return
            if url != self.selection_url:
                self.selected = None
                self.hosts, self.items = [], []
                self.render()
            self.selection_url = url
        self.refresh.setEnabled(False)
        self.choose.setEnabled(False)
        self.url.setEnabled(False)
        self.token.setEnabled(False)
        self.progress.show()
        self.status.setText("Загрузка из Zabbix… Ранее загруженные данные пока остаются в таблице.")
        self.worker = FetchThread(self.url.text(), self.token.text(), self.selected, self.demo)
        self.worker.ready.connect(self.loaded)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def loaded(self, result):
        self.hosts, self.items = result["hosts"], result["items"]
        if self.selected is None:
            self.selected = {h["hostid"] for h in self.hosts}
        self.render()
        visible_ids = {h["hostid"] for h in self.hosts}
        missing = len(self.selected - visible_ids)
        self.status.setText(f"{'ДЕМО · ' if self.demo else ''}Zabbix {result['version']} · Обновлено {datetime.now():%d.%m.%Y %H:%M:%S}" +
                            (f" · Недоступных выбранных узлов: {missing}" if missing else ""))
        self.save_config()

    def failed(self, message):
        self.status.setText("Ошибка: " + message + " Таблица не обновлена; прежние данные могут быть устаревшими.")

    def finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.refresh.setEnabled(True)
        self.choose.setEnabled(bool(self.hosts))
        self.url.setEnabled(True)
        self.token.setEnabled(True)
        self.progress.hide()

    def choose_hosts(self):
        dialog = HostDialog(self.hosts, self.selected or set(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            unavailable = (self.selected or set()) - {h["hostid"] for h in self.hosts}
            self.selected = dialog.selected_ids() | unavailable
            self.save_config()
            self.render()
            self.load()

    def render(self):
        hosts = [h for h in self.hosts if h["hostid"] in (self.selected or set())]
        by_host = {}
        for item in self.items:
            by_host.setdefault(item["hostid"], []).append(item)
        counts = dict.fromkeys(COLORS, 0)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(hosts))
        for row, host in enumerate(hosts):
            matches = by_host.get(host["hostid"], [])
            item = matches[0] if len(matches) == 1 else {}
            raw = str(item.get("lastvalue", "")).strip()
            state = classify(raw)
            reason = None
            if not matches:
                reason = "Элемент is-upd-date не найден"
            elif len(matches) > 1:
                reason = "Найдено несколько элементов"
            elif str(item.get("status", "0")) != "0":
                reason = "Элемент отключён"
            elif str(item.get("state", "0")) != "0":
                reason = "Элемент не поддерживается"
            elif not str(item.get("lastclock", "0")).isdigit() or int(item.get("lastclock", 0)) <= 0:
                reason = "Нет полученных данных"
            level = "unknown" if reason else state.level
            counts[level] += 1
            received = "—"
            try:
                if int(item.get("lastclock", 0)) > 0:
                    received = datetime.fromtimestamp(int(item["lastclock"])).strftime("%Y-%m-%d %H:%M")
            except (ValueError, OverflowError, OSError):
                pass
            values = [host["name"], host["host"], raw or "—", str(state.age) if state.age is not None else "—", reason or state.text, received]
            bg, fg = COLORS[level]
            for col, value in enumerate(values):
                cell = NumberItem(value) if col == 3 else QTableWidgetItem(value)
                if col == 3:
                    cell.setData(Qt.ItemDataRole.UserRole, state.age if state.age is not None else -999999)
                cell.setBackground(QColor(bg))
                cell.setForeground(QColor(fg))
                cell.setToolTip(value)
                self.table.setItem(row, col, cell)
        self.table.setSortingEnabled(True)
        self.selection_label.setText(f"Выбрано: {len(self.selected or set())} · Доступно: {len(self.hosts)}")
        for level, (label, title) in self.counters.items():
            label.setText(f"{title}  ·  {counts[level]}")
        self.filter_rows()

    def filter_rows(self):
        text = self.search.text().casefold()
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, not any(text in self.table.item(row, col).text().casefold() for col in (0, 1)))

    def closeEvent(self, event):
        if self.worker is not None:
            self.status.setText("Дождитесь завершения запроса перед закрытием окна (тайм-аут одного запроса — 20 секунд).")
            event.ignore()
        else:
            event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setOrganizationName("ServerTools")
    app.setApplicationName("ZabbixUpdateMonitor")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = Window(demo="--demo" in sys.argv)
    window.show()
    sys.exit(app.exec())
