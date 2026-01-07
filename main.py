import re
import sqlite3
import sys
from functools import partial
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

DB_PATH = Path(__file__).with_name("emails.db")
STATUSES = ["unused", "using", "used", "leftover"]
STATUS_SORT = ["using", "unused", "leftover", "used"]
STATUS_SORT_CLAUSE = "CASE status " + " ".join(
    f"WHEN '{status}' THEN {index}" for index, status in enumerate(STATUS_SORT)
) + f" ELSE {len(STATUS_SORT)} END"

with open('base.txt', 'r', encoding='utf-8') as f:
    SEED_TEXT = f.read()

SEED_STATUS = {
    "x": "used",
    "o": "using",
    "-": "leftover",
    " ": "unused",
}


def parse_seed() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    pattern = re.compile(
        r"^\* \[(?P<state>[xo\- ])\]\s+(?P<email>\S+)(?:\s+\((?P<number>[^)]+)\))?$"
    )
    for line in SEED_TEXT.splitlines():
        line = line.strip()
        if not line:
            continue
        match = pattern.match(line)
        if not match:
            continue
        state = match.group("state")
        email = match.group("email").strip()
        number = (match.group("number") or "").strip()
        status = SEED_STATUS.get(state, "unused")
        rows.append((email, status, number))
    return rows


class EmailChecklistApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._loading = False
        self.conn = sqlite3.connect(DB_PATH)
        self._init_db()
        self._ensure_seed_data()
        self._build_ui()
        self._load_rows()

    def _init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'unused',
                number TEXT
            )
            """
        )
        self.conn.commit()

    def _ensure_seed_data(self) -> None:
        count = self.conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        if count != 0:
            return
        seed_rows = parse_seed()
        if not seed_rows:
            return
        self.conn.executemany(
            "INSERT INTO emails (email, status, number) VALUES (?, ?, ?)",
            seed_rows,
        )
        self.conn.commit()

    def _build_ui(self) -> None:
        self.setWindowTitle("Email Checklist")
        self.resize(900, 600)

        container = QWidget()
        layout = QVBoxLayout(container)

        form_layout = QHBoxLayout()
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("email@example.com")
        self.status_input = QComboBox()
        self.status_input.addItems(STATUSES)
        self.status_input.setCurrentText("unused")
        self.number_input = QLineEdit()
        self.number_input.setPlaceholderText("number (optional)")
        add_button = QPushButton("Add")
        delete_button = QPushButton("Delete Selected")

        form_layout.addWidget(QLabel("Email"))
        form_layout.addWidget(self.email_input)
        form_layout.addWidget(QLabel("Status"))
        form_layout.addWidget(self.status_input)
        form_layout.addWidget(QLabel("Number"))
        form_layout.addWidget(self.number_input)
        form_layout.addWidget(add_button)
        form_layout.addWidget(delete_button)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Email", "Status", "Number"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )

        layout.addLayout(form_layout)
        layout.addWidget(self.table)
        self.setCentralWidget(container)

        add_button.clicked.connect(self._add_email)
        delete_button.clicked.connect(self._delete_selected)
        self.table.itemChanged.connect(self._on_item_changed)

    def _load_rows(self) -> None:
        self._loading = True
        self.table.setRowCount(0)
        cursor = self.conn.execute(
            "SELECT id, email, status, number FROM emails "
            f"ORDER BY {STATUS_SORT_CLAUSE}, id"
        )
        for row_index, (row_id, email, status, number) in enumerate(cursor.fetchall()):
            self.table.insertRow(row_index)

            email_item = QTableWidgetItem(email)
            email_item.setFlags(email_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            email_item.setData(Qt.ItemDataRole.UserRole, row_id)
            self.table.setItem(row_index, 0, email_item)

            status_box = QComboBox()
            status_box.addItems(STATUSES)
            status_box.setCurrentText(status if status in STATUSES else "unused")
            status_box.currentTextChanged.connect(
                partial(self._update_status, row_id)
            )
            self.table.setCellWidget(row_index, 1, status_box)

            number_item = QTableWidgetItem(number or "")
            number_item.setData(Qt.ItemDataRole.UserRole, row_id)
            self.table.setItem(row_index, 2, number_item)

        self._loading = False

    def _add_email(self) -> None:
        email = self.email_input.text().strip()
        if not email:
            QMessageBox.warning(self, "Missing Email", "Enter an email address.")
            return
        status = self.status_input.currentText()
        number = self.number_input.text().strip() or None
        try:
            self.conn.execute(
                "INSERT INTO emails (email, status, number) VALUES (?, ?, ?)",
                (email, status, number),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self,
                "Duplicate Email",
                "That email already exists in the checklist.",
            )
            return

        self.email_input.clear()
        self.number_input.clear()
        self._load_rows()

    def _delete_selected(self) -> None:
        rows = {item.row() for item in self.table.selectedItems()}
        if not rows:
            return
        confirm = QMessageBox.question(
            self,
            "Delete",
            f"Delete {len(rows)} selected row(s)?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        for row in sorted(rows, reverse=True):
            row_id = self._row_id_for_row(row)
            if row_id is None:
                continue
            self.conn.execute("DELETE FROM emails WHERE id = ?", (row_id,))
        self.conn.commit()
        self._load_rows()

    def _row_id_for_row(self, row: int) -> int | None:
        item = self.table.item(row, 0)
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _update_status(self, row_id: int, status: str) -> None:
        if self._loading:
            return
        if status not in STATUSES:
            return
        self.conn.execute(
            "UPDATE emails SET status = ? WHERE id = ?", (status, row_id)
        )
        self.conn.commit()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        if item.column() != 2:
            return
        row_id = item.data(Qt.ItemDataRole.UserRole)
        if row_id is None:
            row_id = self._row_id_for_row(item.row())
        if row_id is None:
            return
        number = item.text().strip() or None
        self.conn.execute("UPDATE emails SET number = ? WHERE id = ?", (number, row_id))
        self.conn.commit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt uses camelCase
        self.conn.close()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    window = EmailChecklistApp()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
