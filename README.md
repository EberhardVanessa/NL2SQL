# LangGraph and OpenWebUI NL2SQL

This project contains a FastAPI backend for schema-aware NL2SQL generation and an OpenWebUI pipe that forwards chat messages to the backend. It also contains local copies of BIRD and Spider benchmark data for experimentation and evaluation.

## Motivation

Large datasets are often stored in relational databases, but querying them requires SQL knowledge. This creates a barrier for non-technical users who want to access data directly. This project was created to investigate a privacy-preserving NL2SQL pipeline that translates natural language questions into executable SQL queries using locally hosted LLMs. The system combines schema linking, SQL validation, and a chatbot interface through OpenWebUI so users can ask database questions without exposing data to cloud-based LLM services.

## Authors

- Pascal Nadler
- Vanessa Eberhard

## Project Structure

```text
.
+-- app/                       FastAPI application and NL2SQL agent code
|   +-- file/                  Schema file upload, storage, and matching helpers
|   +-- graph/                 LangGraph pipeline, prompts, SQL validation, and agent state
|   +-- llm/                   Local OpenAI-compatible and REST LLM adapters
|   +-- rest/                  FastAPI request models and route service logic
|   +-- config.py              Environment-based application settings
|   +-- main.py                FastAPI entrypoint
+-- bird/                      Local copy of BIRD benchmark data and evaluation assets
|   +-- mini_dev/              BIRD Mini-Dev data, evaluation, and experiment files
|   +-- postgresql/            PostgreSQL-related BIRD setup/assets
+-- spider/                    Local copy of Spider benchmark data and evaluation assets
|   +-- spider_data/           Spider JSON files, SQL gold files, and databases
|   +-- scripts/               Spider helper scripts
|   +-- notebooks/             Evaluation notebooks
|   +-- evaluation_summary/    Evaluation output summaries
+-- example/                   Example schema files for manual testing
+-- openwebui/                 OpenWebUI Dockerfile and Schema Agent pipe
+-- docker-compose.yml         Docker Compose setup for the FastAPI backend
+-- dockerfile                 Docker image for the FastAPI backend
+-- requirements.txt           Python dependencies
+-- README.md                  Project documentation
```

## Dataset Sources

The benchmark data in this repository is copied from the original datasets:

- BIRD data in `bird/`: copied from the original BIRD-SQL benchmark / BIRD Mini-Dev dataset. Original dataset page: https://bird-bench.github.io/. Mini-Dev repository: https://github.com/bird-bench/mini_dev.
- Spider data in `spider/`: copied from the original Spider text-to-SQL dataset. Original dataset page: https://yale-lily.github.io/spider. Original dataset repository: https://github.com/taoyds/spider.

Use the original dataset pages for license, citation, and redistribution requirements.

## Prerequisites

- Docker Desktop.
- An LLM endpoint:
  - Ollama or another OpenAI-compatible endpoint for `LLM_PROVIDER=local`.
  - A custom REST generation endpoint for `LLM_PROVIDER=remote`.

The default backend port is `8081`. The default OpenWebUI port is `3000`.

## FastAPI Backend

The FastAPI backend is started through the Docker Compose file in the project root folder.

Run from the project root folder:

```powershell
docker compose up --build -d
```

Check that the backend is running:

```powershell
curl http://localhost:8081/health
```

List uploaded schema files:

```powershell
curl http://localhost:8081/files
```

Upload an example schema:

```powershell
curl -X POST http://localhost:8081/files/upload -F "files=@example/schema.txt"
```

Ask a question:

```powershell
curl -X POST http://localhost:8081/ask -H "Content-Type: application/json" -d "{\"chat_id\":\"demo\",\"question\":\"Show all users with their order totals.\",\"sql_dialect\":\"sqlite\",\"is_thinking\":true,\"skip_user_interaction\":true}"
```

Stop the backend:

```powershell
docker compose down
```

The checked-in `docker-compose.yml` uses `LLM_PROVIDER=remote` and calls `http://host.docker.internal:8000/generate`. If you want to use Ollama directly instead, run the backend with the Docker command below.

```powershell
docker build -t schema-agent .
docker rm -f schema-agent
docker run -d --name schema-agent -p 8081:8081 -v schema-agent-data:/data --restart unless-stopped -e LLM_PROVIDER=local -e OPENAI_BASE_URL=http://host.docker.internal:11434/v1 -e OPENAI_API_KEY=ollama -e MODEL_NAME=llama3.1:8b -e DATA_DIR=/data -e UPLOAD_DIR=/data/uploads -e REGISTRY_PATH=/data/file_registry.json schema-agent
```

## OpenWebUI Setup

Build and start the OpenWebUI container from the repository root:

```powershell
docker build -t schema-agent-openwebui .\openwebui
docker rm -f open-webui
docker run -d --name open-webui -p 3000:8080 -v open-webui:/app/backend/data --restart unless-stopped -e OLLAMA_BASE_URL=http://host.docker.internal:11434 -e WEBUI_AUTH=False -e SCHEMA_AGENT_URL=http://host.docker.internal:8081 schema-agent-openwebui
```

Open the UI:

```powershell
Start-Process http://localhost:3000
```

Add the Schema Agent pipe:

1. Open `http://localhost:3000`.
2. Go to the OpenWebUI admin functions/tools area.
3. Import or paste the content of `openwebui/schema_agent_pipe.py`.
4. Enable the pipe and select `Schema Agent` in a chat.

The pipe sends messages to:

```text
http://host.docker.internal:8081/ask/stream
```

That URL works when OpenWebUI runs in Docker and the FastAPI backend is exposed on the host at port `8081`.

## Useful API Endpoints

- `GET /health`: health check.
- `GET /files`: list active uploaded schema files.
- `POST /files/upload`: upload one or more schema files.
- `DELETE /files`: remove active schema files.
- `POST /ask`: run the NL2SQL pipeline once.
- `POST /ask/stream`: run the NL2SQL pipeline with server-sent events.
- `POST /resume`: answer a human-in-the-loop clarification.
- `POST /resume/stream`: resume with server-sent events.

## Notes

- `/ask` requires either uploaded schema files or a `schema_context_base` field in the JSON request.
- `sql_dialect` accepts `sqlite` or `postgresql`.
- `max_sql_repair_attempts` is capped at `5` by the backend.
- For Docker on Windows, `host.docker.internal` is used so containers can call services running on the host machine.
