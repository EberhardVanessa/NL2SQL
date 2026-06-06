from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from fastapi import HTTPException


class FileRegistry:
    def __init__(self, registry_path: str, upload_dir: str):
        self.registry_path = Path(registry_path)
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.registry_path.exists():
            self._save({"files": [], "schemas": {}})
        else:
            data = self._load()
            data.setdefault("files", [])

            # Backward compatibility:
            # old version may have schemas as a list
            schemas = data.get("schemas", {})
            if isinstance(schemas, list):
                data["schemas"] = {
                    schema["schema_id"]: schema
                    for schema in schemas
                    if "schema_id" in schema
                }
            elif not isinstance(schemas, dict):
                data["schemas"] = {}

            self._save(data)

    def _load(self) -> dict:
        with self.registry_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict) -> None:
        with self.registry_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def list_files(self) -> list[dict]:
        data = self._load()
        return data.get("files", [])

    def list_schemas(self) -> dict:
        data = self._load()
        return data.get("schemas", {})

    def add_files(self, uploaded_files: list[tuple[str, bytes]]) -> list[dict]:
        data = self._load()
        data.setdefault("files", [])
        data.setdefault("schemas", {})

        result = []

        for original_name, content in uploaded_files:
            file_id = str(uuid.uuid4())
            safe_name = original_name.replace("/", "_").replace("\\", "_")
            stored_name = f"{file_id}_{safe_name}"
            path = self.upload_dir / stored_name

            with path.open("wb") as f:
                f.write(content)

            entry = {
                "id": file_id,
                "filename": original_name,
                "stored_name": stored_name,
                "path": str(path),
                "size": len(content),
            }

            data["files"].append(entry)
            result.append(entry)

        self._save(data)
        return result

    def add_schema_content(
        self,
        schema_id: str,
        filename: str,
        content: str,
        source: str,
    ) -> dict:
        data = self._load()
        data.setdefault("files", [])
        data.setdefault("schemas", {})

        if schema_id in data["schemas"]:
            existing = data["schemas"][schema_id]

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Duplicate schema id '{schema_id}' found. "
                    f"Already registered from file '{existing['filename']}', "
                    f"duplicate found in file '{filename}'."
                ),
            )

        schema_entry = {
            "id": str(uuid.uuid4()),
            "schema_id": schema_id,
            "filename": filename,
            "source": source,
            "content": content,
        }

        data["schemas"][schema_id] = schema_entry
        self._save(data)

        return schema_entry

    def get_schema_content(self, schema_id: str) -> str | None:
        if not schema_id:
            return None

        data = self._load()
        schema = data.get("schemas", {}).get(schema_id)

        if not schema:
            return None

        return schema["content"]

    def get_schema(self, schema_id: str) -> dict | None:
        if not schema_id:
            return None

        data = self._load()
        schema = data.get("schemas", {}).get(schema_id)

        if not schema:
            return None

        return schema

    def clear_files(self) -> None:
        data = self._load()

        for entry in data.get("files", []):
            try:
                os.remove(entry["path"])
            except FileNotFoundError:
                pass

        self._save({"files": [], "schemas": {}})

    def combined_text(self) -> str:
        files = self.list_files()

        if not files:
            return ""

        parts: list[str] = []

        for entry in files:
            path = Path(entry["path"])

            if not path.exists():
                continue

            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue

            parts.append(
                f"===== FILE: {entry['filename']} =====\n{text.strip()}\n"
            )

        return "\n\n".join(parts).strip()
