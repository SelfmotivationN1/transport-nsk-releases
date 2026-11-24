"""Route-sheet import fix for the recovered Transport NSK 1.2.3 application."""

import logging
import re
import sys
import uuid
from decimal import Decimal
from importlib import import_module
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from core import InputError, clean_address, infer_mass, norm, number


def _load_released_module():
    # Keep the other PDF formats on the verified release implementation.
    if getattr(sys, "frozen", False):
        return import_module("_pdf_delivery_release_123")
    for directory in sys.path:
        path = Path(directory) / "pdf_delivery.pyc"
        if path.is_file():
            spec = spec_from_file_location(__name__, path)
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise ImportError("The recovered pdf_delivery.pyc is missing from the module path.")


_released = _load_released_module()
RoutePDFError = _released.RoutePDFError
read_pdf = _released.read_pdf
route_goods = _released.route_goods
route_points = _released.route_points
parse_sales_pdf = _released.parse_sales_pdf
parse_customer_order_pdf = _released.parse_customer_order_pdf

_NUMBER = _released.NUM
_HEADER = re.compile(r"Маршрутный лист №:\s*([А-ЯA-Z]+-\d+)\s+от\s+(\d{2}\.\d{2}\.\d{4}),?\s*(.*)")
_PRODUCT = re.compile(rf"^(.*?)\s{{2,}}({_NUMBER})\s+(кг|шт|т)\.?\s*(?:({_NUMBER}))?\s*$")
_IDENTITY = re.compile(r"\b[А-ЯЁA-Z0-9]{2,12}-\d+\b")
# An invoice number cannot be a segment of a hyphenated phone number or a time range.
_INVOICE = re.compile(r"(?<![\w-])[А-ЯЁA-Z0-9]{2,12}-\d+(?![\w-]|:\d)")
_TOTALS = re.compile(rf"Всего (?:доставок|адресов):\s*(\d+).*Всего количество:\s*({_NUMBER})")
_TOTAL_LABEL = re.compile(r"^\s*Всего\s+(?:доставок|адресов|количество)\b", re.IGNORECASE)
_SIGNOFF = re.compile(r"\s*Кладовщик сдал:\s*_{2,}\s+Водитель принял:\s*_{2,}\s*")
_LABELS = ("Контрагент", "Дополнительная информация", "Кладовщик сдал", "Водитель принял", "Зона доставки:")
_MISSING_TOTALS = "В PDF нет напечатанных итогов. Рейс загружен по читаемым накладным и товарным строкам."


def _route_record(path, digest, route_number, date="", vehicle="", pages=()):
    return dict(number=route_number, date=date, vehicle=vehicle.strip(), documents=[], rows=[],
                file=str(path), hash=digest, issues=[], pages=list(pages))


def _counterparty(lines, boundary, summary):
    names = [text[:boundary].strip() for text in lines]
    customer = next((text for text in names if text and not re.match(r"Менеджер:|тел\.|\d{2}:|-,", text, re.I)), "")
    if summary:
        name_parts = []
        for text in names:
            if re.search(r"Менеджер:|тел\.", text, re.I):
                break
            text = _IDENTITY.sub("", text).strip()
            if text:
                name_parts.append(text)
        if name_parts:
            return " ".join(name_parts)
    return customer


def _route_problem(route, summary):
    docs, rows = route["documents"], route["rows"]
    if not rows or not docs or any(not doc["address"] or not doc["counterparty"] for doc in docs):
        return "не удалось полностью прочитать адреса и товары."

    printed_stops = route.get("printed_stops")
    if printed_stops is None:
        product_documents = {row["doc_id"] for row in rows}
        if (not summary and route.get("_signed_layout") and not route.get("_unread_totals")
                and all(doc["id"] in product_documents for doc in docs)):
            route["issues"].append(_MISSING_TOTALS)
            logging.getLogger(__name__).warning("%s: %s", route["number"], _MISSING_TOTALS)
            return ""
        return "не удалось сверить число доставок и количество товаров с итогом PDF."

    stops = len({(norm(doc["counterparty"]), norm(doc["address"])) for doc in docs}) if summary else len(docs)
    if printed_stops != stops:
        return "число доставок не совпало с итогом PDF."
    if number(route["printed_quantity"]) != sum((number(row["qty"]) for row in rows), Decimal(0)):
        return "количество товаров не совпало с итогом PDF."
    return ""


def read_route_pdf(path, quantity_mode="Заявлено", tolerant=False, summary=False):
    path, pages, digest = read_pdf(path)
    routes = []
    route = doc = None
    pending = []
    started = quantity_header = False
    declared_col = 100
    invoice_col = None

    if summary and not any(_HEADER.search(page) for page in pages):
        route = _route_record(path, digest, "Заявки-" + digest[:8])
        routes.append(route)

    for page_no, page in enumerate(pages, 1):
        lines = page.rstrip().splitlines()
        for line_no, line in enumerate(lines, 1):
            header = _HEADER.search(line)
            if header:
                route = _route_record(path, digest, *header.groups(), pages=(page_no,))
                routes.append(route)
                doc = None
                pending = []
                started = quantity_header = False
                invoice_col = None
                continue
            if route is None:
                continue
            if page_no not in route["pages"]:
                route["pages"].append(page_no)
            if "№ накладной" in line:
                invoice_col = line.index("№ накладной")
            if "Заявлено" in line and "Фактически" in line:
                declared_col = line.index("Заявлено")
                quantity_header = True
                continue
            if (invoice_col is not None and quantity_header and route["pages"] == [page_no]
                    and line_no == len(lines) and _SIGNOFF.fullmatch(line)):
                route["_signed_layout"] = True
            if any(label in line for label in _LABELS) or line.strip() == "Поддоны":
                continue

            totals = _TOTALS.search(line)
            if totals:
                route["printed_stops"] = int(totals[1])
                route["printed_quantity"] = str(number(totals[2]))
                pending = []
                continue
            if _TOTAL_LABEL.match(line):
                route["_unread_totals"] = True
                continue

            product = _PRODUCT.match(line)
            if product and product.start(2) >= declared_col - 12:
                if doc is None:
                    raise InputError(f"Страница {page_no}: товар без накладной.")
                qty = product[4] if quantity_mode == "Фактически" else product[2]
                if not qty:
                    raise InputError(f"Страница {page_no}, строка {line_no}: фактическое количество не заполнено. Выберите «Заявлено».")
                route["rows"].append(dict(doc_id=doc["id"], name=product[1].strip(), qty=str(number(qty, required=True)),
                                          unit=product[3], source_row=f"{page_no}:{line_no}"))
                pending = []
                started = True
                continue

            ident = next((match for match in _INVOICE.finditer(line)
                          if invoice_col is None or invoice_col - 12 <= match.start() <= invoice_col + 20), None)
            if ident:
                boundary = ident.start()
                right = ident.end() + 2
                fragments = pending + [line]
                customer = _counterparty(fragments, boundary, summary)
                addresses = [text[right:].strip() for text in fragments if text[right:].strip()]
                preceding = _IDENTITY.findall(line[:boundary])
                doc = dict(id=uuid.uuid4().hex, number=ident[0], order=preceding[-1] if preceding else "",
                           counterparty=customer, address=clean_address(" ".join(addresses)), file=str(path),
                           hash=digest, sheet=route["number"], date=route["date"],
                           internal_transfer="перемещение" in norm(customer), source_page=page_no,
                           source_line=line_no, _right=right)
                route["documents"].append(doc)
                pending = []
                started = False
                continue

            if not line.strip():
                continue
            if doc is not None and not started:
                fragment = line[doc["_right"]:].strip()
                left = line[:doc["_right"]].strip()
                if fragment and not fragment.lower().startswith("тел") and (not left or left.startswith(("Менеджер:", "тел."))):
                    doc["address"] = clean_address(doc["address"] + " " + fragment)
            else:
                pending.append(line)

    if not routes:
        raise InputError("В PDF не найден маршрутный лист поддерживаемого формата.")

    for route in routes:
        problem = _route_problem(route, summary)
        route.pop("_signed_layout", None)
        route.pop("_unread_totals", None)
        if problem:
            error = RoutePDFError(route, problem)
            if not tolerant:
                raise error
            route["import_error"] = f"{error}\n{error.details}"
            continue
        for doc in route["documents"]:
            doc.pop("_right", None)
        masses = [infer_mass(row["name"], row["qty"], row["unit"])[0] for row in route["rows"]]
        route["unknown_mass"] = sum(mass is None for mass in masses)
        route["mass"] = "" if route["unknown_mass"] else str(sum(masses, Decimal(0)))
    return routes
