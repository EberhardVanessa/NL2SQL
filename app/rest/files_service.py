from fastapi import FastAPI, File, HTTPException, UploadFile

from app.file.utils import extract_schema_sections


async def prepare_file(file):
    filename = file.filename or "schema.txt"
    if not filename.lower().endswith(".txt"):
        raise HTTPException(
            status_code=400,
            detail=f"Only .txt files are allowed. Invalid file: {filename}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file is empty: {filename}",
        )

    try:
        text_content = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail=f"Only UTF-8 text files are supported: {filename}",
        )


    schema_sections = extract_schema_sections(text_content)
    return filename, content, schema_sections

def build_response(saved, schemas, registry):
    return {
        "status": "ok",
        "uploaded_files": saved,
        "schemas": [
            {
                "schema_id": schema_id,
                "filename": filename,
                "source": source,
            }
            for schema_id, filename, _, source in schemas
        ],
        "file_count": len(registry.list_files()),
        "schema_count": len(registry.list_schemas()),
    }

async def upload_files_service(files, registry):
    uploaded: list[tuple[str, bytes]] = []
    schemas: list[tuple[str, str, str, str]] = []
    # schema_id, filename, section_content, source

    for file in files:
        filename, content, schema_sections = await prepare_file(file)
        uploaded.append((filename, content))
        for schema_section in schema_sections:
            schemas.append(
                (
                    schema_section.schema_id,
                    filename,
                    schema_section.content,
                    schema_section.source,
                )
            )

    saved = registry.add_files(uploaded)

    for schema_id, filename, section_content, source in schemas:
        registry.add_schema_content(
            schema_id=schema_id,
            filename=filename,
            content=section_content,
            source=source,
        )

    return build_response(saved, schemas, registry)