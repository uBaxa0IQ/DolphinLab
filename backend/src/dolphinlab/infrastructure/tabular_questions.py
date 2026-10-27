"""Small, read-only CSV/TSV operations. No agent-authored code runs in the API."""

import csv
import hashlib
import io
import re
import time
from decimal import Decimal, localcontext

from dolphinlab.domain.conversation import DataQuestion

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 100_000
MAX_GROUPS = 2000
NUMBER = re.compile(r"^[+-]?\d{1,24}(?:\.\d{1,12})?$", flags=re.ASCII)


def number(value):
    value = value.strip()
    if not NUMBER.fullmatch(value):
        raise ValueError(
            "Для расчёта нужны обычные конечные числа с точкой, без разделителей тысяч"
        )
    return Decimal(value)


def matches(row, filters):
    for item in filters:
        value = row[item.column].strip()
        if item.operator == "is_null":
            accepted = not value
        elif item.operator == "not_null":
            accepted = bool(value)
        elif item.operator in ("eq", "ne"):
            accepted = value == (item.value or "")
            if item.operator == "ne":
                accepted = not accepted
        elif not value:
            accepted = False
        else:
            left, right = number(value), number(item.value or "")
            accepted = {
                "gt": left > right,
                "gte": left >= right,
                "lt": left < right,
                "lte": left <= right,
            }[item.operator]
        if not accepted:
            return False
    return True


def calculate(path, name, sha256, query: DataQuestion):
    started = time.monotonic()
    suffix = name.lower().rsplit(".", 1)[-1]
    if suffix not in ("csv", "tsv"):
        raise ValueError(
            "Быстрый расчёт поддерживает CSV/TSV UTF-8; для другого формата подготовь исследование"
        )
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Файл больше 20 МБ: нужен исследовательский запуск")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("Размер или хеш входного файла изменился; расчёт не выполнен")
    try:
        reader = csv.reader(
            io.StringIO(raw.decode("utf-8-sig")),
            delimiter=query.delimiter or ("\t" if suffix == "tsv" else ","),
            strict=True,
        )
        columns = next(reader)
    except (UnicodeError, StopIteration, csv.Error) as error:
        raise ValueError("Нужна непустая таблица CSV/TSV UTF-8 с заголовком") from error
    if (
        not columns
        or len(columns) > 200
        or len(columns) != len(set(columns))
        or any(not name.strip() or len(name) > 200 for name in columns)
    ):
        raise ValueError("Нужны уникальные непустые имена столбцов; до 200 столбцов")
    required = [*query.group_by, *(item.column for item in query.filters)]
    if query.column:
        required.append(query.column)
    if any(column not in columns for column in required):
        raise ValueError("Столбец не найден; сначала прочитай схему таблицы")
    result = {
        "input_id": str(query.input_id),
        "name": name,
        "sha256": sha256,
        "operation": query.operation,
        "delimiter": query.delimiter or ("\t" if suffix == "tsv" else ","),
        "columns": columns,
        "filters": [item.model_dump() for item in query.filters],
        "status": "computed",
        "independently_verified": False,
    }
    scanned = matched = missing = 0
    samples, groups = [], {}
    with localcontext() as arithmetic:
        arithmetic.prec = 64
        try:
            for cells in reader:
                scanned += 1
                if scanned > MAX_ROWS or (scanned % 1000 == 0 and time.monotonic() - started > 3):
                    raise ValueError(
                        "Превышен объём быстрого расчёта; нужен исследовательский запуск"
                    )
                if len(cells) != len(columns) or any(len(cell) > 4000 for cell in cells):
                    raise ValueError("Некорректная строка таблицы или ячейка длиннее 4000 символов")
                row = dict(zip(columns, cells, strict=True))
                if not matches(row, query.filters):
                    continue
                matched += 1
                if len(samples) < query.limit:
                    samples.append({key: value[:200] for key, value in row.items()})
                if query.operation != "aggregate":
                    continue
                key = tuple(row[column] for column in query.group_by)
                if key not in groups:
                    if len(groups) >= MAX_GROUPS:
                        raise ValueError("Слишком много групп для быстрого ответа")
                    groups[key] = {"count": 0, "sum": Decimal(0), "min": None, "max": None}
                group = groups[key]
                value = row[query.column].strip() if query.column else None
                if query.column and not value:
                    missing += 1
                    continue
                group["count"] += 1
                if query.metric != "count":
                    numeric = number(value)
                    group["sum"] += numeric
                    group["min"] = numeric if group["min"] is None else min(group["min"], numeric)
                    group["max"] = numeric if group["max"] is None else max(group["max"], numeric)
        except csv.Error as error:
            raise ValueError("Некорректный формат CSV/TSV") from error
        result.update(rows_scanned=scanned, matched_rows=matched)
        if query.operation == "preview":
            result.update(rows=samples, preview_values_clipped_at=200, total_rows=matched)
        elif query.operation == "aggregate":
            output = []
            for key, group in sorted(groups.items())[:50]:
                if query.metric == "mean":
                    value = group["sum"] / group["count"] if group["count"] else None
                else:
                    value = group[query.metric]
                if query.metric != "count" and not group["count"]:
                    value = None
                output.append(
                    {
                        "group": dict(zip(query.group_by, key, strict=True)),
                        "value": format(value, "f") if isinstance(value, Decimal) else value,
                        "non_null_values": group["count"],
                    }
                )
            if not groups and not query.group_by:
                output = [
                    {
                        "group": {},
                        "value": 0 if query.metric == "count" else None,
                        "non_null_values": 0,
                    }
                ]
            result.update(
                metric=query.metric,
                column=query.column,
                groups=output,
                total_groups=len(groups),
                truncated=len(groups) > 50,
                excluded_empty_values=missing,
                decimal_precision=64,
            )
    return result
