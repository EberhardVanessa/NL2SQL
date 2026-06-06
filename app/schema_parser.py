from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+([\w\.\"`\[\]]+)\s*\((.*?)\);",
    re.IGNORECASE | re.DOTALL,
    )


def normalize_identifier(value: str) -> str:
    return value.strip().strip('"`[]')


def summarize_sql_schema(schema_text: str) -> str:
    parts: list[str] = []
    for table_name, body in CREATE_TABLE_RE.findall(schema_text):
        t = normalize_identifier(table_name)
        columns = []
        for raw_line in body.split(","):
            line = raw_line.strip()
            if not line:
                continue
            upper = line.upper()
            if upper.startswith(("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CONSTRAINT", "CHECK")):
                continue
            first = line.split()[0]
            columns.append(normalize_identifier(first))
        parts.append(f"Table {t}: columns = {', '.join(columns)}")
    if parts:
        return "\n".join(parts)
    return schema_text[:25000]


def parse_uploaded_content(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    text = content.decode("utf-8", errors="ignore")

    if suffix in {".sql", ".ddl", ".txt", ".md"}:
        return summarize_sql_schema(text)

    if suffix == ".json":
        data: Any = json.loads(text)
        return json.dumps(data, indent=2)[:25000]

    return text[:25000]