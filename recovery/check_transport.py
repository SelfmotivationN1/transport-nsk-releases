"""Exercise recovered modules with synthetic files and an isolated database."""

import argparse
import hashlib
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch


def check_route_pdf_import(pdf_delivery, fixtures):
    source = fixtures / "Маршрутный лист.pdf"
    header = [
        "Маршрутный лист №: НФ-000001 от 14.02.2025, Газель",
        f"{'Контрагент':48}№ накладной    Адрес доставки",
        f"{'Наименование продукции':100}Заявлено    Фактически",
    ]
    invoice = f"{'Магазин № 1':48}{'НФ-000012':14}Улица Примерная, 12"
    phone = f"{'08:00-12:00, позвонить заранее':51}8-000-123-45-67"
    product = f"{'Товар':100}2 кг 2"
    signoff = "Кладовщик сдал: ____________    Водитель принял: ____________"

    def read(lines, **options):
        with patch.object(pdf_delivery, "read_pdf", return_value=(source, ["\n".join(lines)], "0" * 64)):
            return pdf_delivery.read_route_pdf(source, **options)[0]

    lines = [*header, invoice, phone, product]
    route = read([*lines, signoff])
    assert [doc["number"] for doc in route["documents"]] == ["НФ-000012"]
    assert route["rows"][0]["doc_id"] == route["documents"][0]["id"]
    assert "printed_stops" not in route and "printed_quantity" not in route
    assert route["issues"] and "import_error" not in route

    totals = "Всего доставок: 1 Всего количество: 2"
    route = read([*lines, totals, signoff])
    assert route["printed_stops"] == 1 and route["printed_quantity"] == "2"
    assert not route["issues"]

    second_invoice = f"{'Магазин № 2':48}{'12-034':14}Улица Примерная, 14"
    for invalid in (
        lines,
        [*lines, "Всего доставок: 1", signoff],
        [*lines, second_invoice, signoff],
        [*lines, "Всего доставок: 2 Всего количество: 2", signoff],
        [*lines, "Всего доставок: 1 Всего количество: 3", signoff],
    ):
        assert read(invalid, tolerant=True).get("import_error"), invalid

    route = read([*lines, second_invoice, product, signoff], quantity_mode="Фактически")
    assert [doc["number"] for doc in route["documents"]] == ["НФ-000012", "12-034"]
    print("PASS: invoice and phone separation, missing totals fallback and strict totals checks.")


def run_checks(bundle, fixtures):
    overrides = Path(__file__).with_name("overrides")
    sys.path[:0] = [str(overrides), str(bundle / "pyc"), str(bundle / "runtime")]
    import core
    import exporting
    import importing
    import pdf_delivery
    import planner_schedule
    import printing
    import storage
    from PySide6.QtWidgets import QApplication
    from openpyxl import Workbook, load_workbook
    from ortools.constraint_solver import pywrapcp
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    check_route_pdf_import(pdf_delivery, fixtures)

    trip = core.new_trip()
    trip.update(title="Recovery fixture", manual_mass="500", manual_value="7000",
                rate="10", fuel_price="60", salary_enabled=False)
    trip["points"].append(core.point("Test delivery"))
    core.sync_legs(trip)
    for leg in trip["legs"]:
        leg.update(km="20", minutes="30")
    result = core.calculate(trip)
    assert not result["errors"], result["errors"]
    assert result["distance"] == Decimal("40")
    assert result["liters"] == Decimal("4")
    assert result["total"] == Decimal("240")
    assert planner_schedule.next_slot(360, [("existing", 360, 380)]) == 380
    assert planner_schedule.next_slot(360, [("later", 380, 400)]) == 360
    print("PASS: route distance, fuel cost and loading slot overlap.")

    distances = ((0, 4, 9), (4, 0, 3), (9, 3, 0))
    manager = pywrapcp.RoutingIndexManager(len(distances), 1, 0)
    solver = pywrapcp.RoutingModel(manager)
    callback = solver.RegisterTransitCallback(
        lambda start, end: distances[manager.IndexToNode(start)][manager.IndexToNode(end)]
    )
    solver.SetArcCostEvaluatorOfAllVehicles(callback)
    solution = solver.SolveWithParameters(pywrapcp.DefaultRoutingSearchParameters())
    assert solution is not None and solution.ObjectiveValue() == 16
    print("PASS: bundled OR-Tools solved a three-stop route.")

    records = fixtures / "records"
    records.mkdir()
    store = storage.Store(records)
    store.put("recovery-check", {"source": "fixture"})
    identity = store.save(trip)
    reopened = storage.Store(records)
    assert reopened.get("recovery-check") == {"source": "fixture"}
    assert reopened.load(identity)["title"] == trip["title"]
    assert len(reopened.history()) == 1
    db = next(records.glob("*.sqlite3"))
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    print("PASS: SQLite save, reopen, history, settings and integrity check.")

    price = fixtures / "Пробный прайс.xlsx"
    book = Workbook()
    book.active.append(["Наименование", "Ед.", "Себестоимость"])
    book.active.append(["Тестовый товар", "кг", 73.25])
    book.save(price)
    source_hash = hashlib.sha256(price.read_bytes()).digest()
    assert list(importing.sheets(price))[0][1][1][0] == "Тестовый товар"
    catalog = importing.Catalog(fixtures, main_source=price)
    match = catalog.lookup("Тестовый товар", "кг")
    assert match is not None and Decimal(match["price"]) == Decimal("73.25")
    assert hashlib.sha256(price.read_bytes()).digest() == source_hash
    export = fixtures / "Пробный отчёт.xlsx"
    exporting.export_trip(export, trip, result)
    report = load_workbook(export, read_only=True)
    try:
        assert any(
            trip["title"] in str(cell)
            for sheet in report
            for row in sheet.iter_rows(values_only=True)
            for cell in row
        )
    finally:
        report.close()
    print("PASS: price lookup, input preservation and readable Excel export.")

    pdf = fixtures / "Пробный документ.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=240, height=100)
    name = NameObject
    font = DictionaryObject({
        name("/Type"): name("/Font"),
        name("/Subtype"): name("/Type1"),
        name("/BaseFont"): name("/Helvetica"),
    })
    page[name("/Resources")] = DictionaryObject({
        name("/Font"): DictionaryObject({name("/F1"): font}),
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 50 Td (Recovery PDF fixture) Tj ET")
    page.replace_contents(stream)
    writer.write(pdf)
    pdf_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
    path, pages, reported_hash = pdf_delivery.read_pdf(pdf)
    assert path == pdf and pages == ["Recovery PDF fixture"]
    assert reported_hash == pdf_hash == hashlib.sha256(pdf.read_bytes()).hexdigest()
    try:
        pdf_delivery.read_route_pdf(pdf)
    except core.InputError:
        pass
    else:
        raise AssertionError("A PDF with no route text was unexpectedly accepted.")
    qt_app = QApplication([])
    pdf_report = fixtures / "Пробный отчёт.pdf"
    printing.export_pdf(pdf_report, trip, result)
    report_pages = PdfReader(pdf_report).pages
    assert any(trip["title"] in " ".join(page.extract_text().split()) for page in report_pages)
    qt_app.quit()
    print("PASS: PDF read, input preservation, invalid route rejection and PDF export.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    qt_path = bundle / "runtime" / "PySide6"
    os.environ["QT_PLUGIN_PATH"] = str(qt_path / "plugins")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    with os.add_dll_directory(str(qt_path)):
        with tempfile.TemporaryDirectory(prefix="transport-nsk-check-") as fixture_path:
            run_checks(bundle, Path(fixture_path))


if __name__ == "__main__":
    main()
