from dataclasses import dataclass


@dataclass
class SchemaMatch:
    schema_id: str
    source: str
    content: str