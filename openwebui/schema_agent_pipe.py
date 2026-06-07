from __future__ import annotations

import os
import json
import httpx

class Pipe:
    def __init__(self):
        self.type = "pipe"
        self.id = "schema-agent"
        self.name = "Schema Agent"
        self.base_url = os.getenv(
            "SCHEMA_AGENT_URL", "http://host.docker.internal:8081"
        )
        self.waiting_for_clarification = set()

    def pipes(self):
        return [{"id": self.id, "name": self.name}]

    def _is_background_task(self, text: str) -> bool:
        return isinstance(text, str) and text.startswith("### Task:")

    def _background_task_response(self, text: str) -> str:
        if "Generate 1-3 broad tags" in text:
            return '{"tags":["Schema"]}'
        if "Generate a concise, 3-5 word title" in text:
            return '{"title":"Schema Agent Chat"}'
        if "Suggest 3-5 relevant follow-up" in text:
            return '{"follow_ups":[]}'
        if (
                "Analyze the chat history to determine the necessity of generating search queries"
                in text
        ):
            return '{"queries":[]}'
        return ""

    def _content_to_text(self, content) -> str:
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text" and item.get("text"):
                        parts.append(str(item["text"]))
                    elif isinstance(item.get("content"), str):
                        parts.append(item["content"])
                elif item is not None:
                    parts.append(str(item))
            return "\n".join(parts).strip()

        if content is None:
            return ""

        return str(content)

    def _build_ask_payload(
        self,
        body: dict,
        chat_id: str,
        question: str,
    ) -> dict:
        payload = {
            "chat_id": chat_id,
            "question": question,
            "is_thinking": True,
        }

        optional_fields = (
            "is_thinking",
            "skip_user_interaction",
            "sql_dialect",
            "max_sql_repair_attempts",
            "schema_name",
            "schema_context_base",
        )
        for field in optional_fields:
            if field in body and body[field] is not None:
                payload[field] = body[field]

        if "sql_dialect" not in payload and body.get("dialect") is not None:
            payload["sql_dialect"] = body["dialect"]

        return payload

    async def _emit_status(self, __event_emitter__, description: str, done: bool = False):
        if not __event_emitter__:
            return

        try:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": description,
                        "done": done,
                    },
                }
            )
        except Exception:
            pass

    def _chunk_text(self, text: str, chunk_size: int = 80):
        if not text:
            return

        for index in range(0, len(text), chunk_size):
            yield text[index : index + chunk_size]

    def _looks_like_sql(self, text: str) -> bool:
        if not isinstance(text, str):
            return False

        candidate = text.strip()
        if not candidate:
            return False

        if candidate.startswith("```"):
            return False

        upper = candidate.upper()
        sql_prefixes = (
            "SELECT",
            "WITH",
            "INSERT",
            "UPDATE",
            "DELETE",
            "CREATE",
            "ALTER",
            "DROP",
            "EXPLAIN",
        )
        return upper.startswith(sql_prefixes)

    def _format_display_answer(self, answer: str) -> str:
        if not isinstance(answer, str):
            return str(answer)

        if self._looks_like_sql(answer):
            return f"```sql\n{answer.strip()}\n```"

        return answer

    async def _stream_ask(
        self,
        payload: dict,
        link_key: str,
        __event_emitter__=None,
    ):
        await self._emit_status(__event_emitter__, "Starting schema agent...")

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/ask/stream",
                    json=payload,
                ) as resp:
                    resp.raise_for_status()

                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue

                        data = line.removeprefix("data:").strip()
                        if not data or data == "[DONE]":
                            continue

                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        event_type = event.get("type")

                        if event_type == "status":
                            await self._emit_status(
                                __event_emitter__,
                                event.get("description", "Schema agent is working..."),
                            )
                            continue

                        if event_type == "needs_human":
                            self.waiting_for_clarification.add(link_key)
                            await self._emit_status(
                                __event_emitter__,
                                "Waiting for clarification.",
                                True,
                            )
                            follow_up_question = event.get(
                                "follow_up_question",
                                "Please clarify.",
                            )
                            for chunk in self._chunk_text(follow_up_question):
                                yield chunk
                            continue

                        if event_type == "final":
                            await self._emit_status(__event_emitter__, "Done.", True)
                            answer = event.get("answer", "No answer returned by backend.")
                            rendered_answer = self._format_display_answer(answer)
                            for chunk in self._chunk_text(rendered_answer):
                                yield chunk
                            continue

                        if event_type == "error":
                            await self._emit_status(
                                __event_emitter__,
                                "Backend error.",
                                True,
                            )
                            message = event.get("message", "Unknown backend error.")
                            for chunk in self._chunk_text(f"Backend error: {message}"):
                                yield chunk
                            continue

            await self._emit_status(__event_emitter__, "Done.", True)

        except httpx.HTTPStatusError as e:
            await self._emit_status(__event_emitter__, "Backend error.", True)
            try:
                detail = e.response.text
            except Exception:
                detail = str(e)
            for chunk in self._chunk_text(f"Backend error: {detail}"):
                yield chunk

        except Exception as e:
            await self._emit_status(__event_emitter__, "Pipe error.", True)
            for chunk in self._chunk_text(f"Pipe error: {e}"):
                yield chunk

    async def _stream_resume(
        self,
        payload: dict,
        link_key: str,
        __event_emitter__=None,
    ):
        await self._emit_status(__event_emitter__, "Resuming schema agent...")

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/resume/stream",
                    json=payload,
                ) as resp:
                    resp.raise_for_status()

                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue

                        data = line.removeprefix("data:").strip()
                        if not data or data == "[DONE]":
                            continue

                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        event_type = event.get("type")

                        if event_type == "status":
                            await self._emit_status(
                                __event_emitter__,
                                event.get("description", "Schema agent is working..."),
                            )
                            continue

                        if event_type == "needs_human":
                            self.waiting_for_clarification.add(link_key)
                            await self._emit_status(
                                __event_emitter__,
                                "Waiting for clarification.",
                                True,
                            )
                            follow_up_question = event.get(
                                "follow_up_question",
                                "Please clarify.",
                            )
                            for chunk in self._chunk_text(follow_up_question):
                                yield chunk
                            continue

                        if event_type == "final":
                            self.waiting_for_clarification.discard(link_key)
                            await self._emit_status(__event_emitter__, "Done.", True)
                            answer = event.get("answer", "No answer returned by backend.")
                            rendered_answer = self._format_display_answer(answer)
                            for chunk in self._chunk_text(rendered_answer):
                                yield chunk
                            continue

                        if event_type == "error":
                            await self._emit_status(
                                __event_emitter__,
                                "Backend error.",
                                True,
                            )
                            message = event.get("message", "Unknown backend error.")
                            for chunk in self._chunk_text(f"Backend error: {message}"):
                                yield chunk
                            continue

            await self._emit_status(__event_emitter__, "Done.", True)

        except httpx.HTTPStatusError as e:
            await self._emit_status(__event_emitter__, "Backend error.", True)
            try:
                detail = e.response.text
            except Exception:
                detail = str(e)
            for chunk in self._chunk_text(f"Backend error: {detail}"):
                yield chunk

        except Exception as e:
            await self._emit_status(__event_emitter__, "Pipe error.", True)
            for chunk in self._chunk_text(f"Pipe error: {e}"):
                yield chunk

    async def pipe(
        self,
        body: dict,
        __metadata__=None,
        __chat_id__=None,
        __event_emitter__=None,
    ):
        messages = body.get("messages", []) or []
        latest = self._content_to_text(messages[-1].get("content", "")) if messages else ""

        if self._is_background_task(latest):
            return self._background_task_response(latest)

        chat_id = str(__chat_id__ or "")
        if not chat_id and isinstance(__metadata__, dict):
            chat_id = str(__metadata__.get("chat_id") or "")
        if not chat_id:
            chat_id = str(body.get("chat_id") or "")
        if not chat_id:
            return "Pipe error: Open WebUI did not provide a chat_id."

        if chat_id in self.waiting_for_clarification:
            payload = {
                "chat_id": chat_id,
                "human_answer": latest,
            }
            return self._stream_resume(payload, chat_id, __event_emitter__)

        payload = self._build_ask_payload(
            body=body,
            chat_id=chat_id,
            question=latest,
        )

        return self._stream_ask(payload, chat_id, __event_emitter__)
