import re

from app.file.schema_match import SchemaMatch

SCHEMA_PATTERN = re.compile(
    r"""
    (?:
        \bUSE\s+SCHEMA\s+([a-zA-Z0-9_.-]+)
        |
        ^\s*--\s*Database:\s*([a-zA-Z0-9_.-]+)\s*$
    )
    """,
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)

def extract_schema_sections(content: str) -> list[SchemaMatch]:
    matches = list(SCHEMA_PATTERN.finditer(content))

    if not matches:
        return [
            SchemaMatch(
                schema_id="default",
                source="default",
                content=content,
            )
        ]

    sections: list[SchemaMatch] = []

    for index, match in enumerate(matches):
        schema_id = match.group(1) or match.group(2)

        if match.group(1):
            source = "use_schema"
        else:
            source = "database_comment"

        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)

        section_content = content[start:end].strip()

        sections.append(
            SchemaMatch(
                schema_id=schema_id,
                source=source,
                content=section_content,
            )
        )

    return sections