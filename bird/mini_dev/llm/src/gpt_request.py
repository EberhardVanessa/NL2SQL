#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

try:
    from tqdm import tqdm
except ModuleNotFoundError:
    def tqdm(iterable, total=None):
        return iterable

from linking_integration import SchemaLinkingOptions, run_schema_linking_for_question
from llm_client import LLMGateway, LLMRuntimeConfig
from pipeline_io import (
    build_output_name,
    decouple_question_schema,
    generate_debug_file,
    generate_linker_eval_jsonl,
    generate_sql_file,
    load_eval_data,
    post_process_response,
    to_json_safe,
)
from prompt import (
    generate_combined_prompts_one,
    generate_regeneration_prompt,
    generate_repair_prompt,
    generate_targeted_repair_prompt,
    generate_verification_prompt,
)
from sql_validation import validate_sql
from table_schema import generate_schema_prompt


def normalize_sql(sql: str) -> str:
    return " ".join(sql.strip().split()).lower()


@dataclass(frozen=True)
class PipelineOptions:
    """Toggles for optional pipeline building blocks."""

    max_fix_attempts: int = 2
    langgraph_max_fix_attempts: int = 2
    schema_linking: SchemaLinkingOptions = field(default_factory=SchemaLinkingOptions)

    @property
    def repair_enabled(self) -> bool:
        return self.max_fix_attempts > 0


@dataclass(frozen=True)
class QuestionTask:
    index: int
    db_id: str
    db_path: str
    question: str
    knowledge: str | None
    dataset: str
    gold_sql: str | None
    sql_dialect: str


@dataclass(frozen=True)
class WorkerTask:
    question: QuestionTask
    llm_config: LLMRuntimeConfig
    options: PipelineOptions


def build_llm_config(args: argparse.Namespace) -> LLMRuntimeConfig:
    provider = os.getenv("LLM_PROVIDER", "local").lower()
    return LLMRuntimeConfig(
        engine=args.engine,
        provider=provider,
        api_key=args.api_key,
        rest_url=os.getenv("LLM_REST_URL", "http://127.0.0.1:8000/generate"),
        rest_timeout=int(os.getenv("LLM_REST_TIMEOUT", "300")),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1"),
        langgraph_url=os.getenv("LANGGRAPH_URL", "http://127.0.0.1:8081/ask"),
        langgraph_user_id=os.getenv("LANGGRAPH_USER_ID", "bird-mini-dev"),
        langgraph_model=os.getenv("LANGGRAPH_MODEL", args.engine),
    )


def build_pipeline_options(args: argparse.Namespace) -> PipelineOptions:
    representations = tuple(
        rep.strip() for rep in str(args.schema_link_representations).split(",") if rep.strip()
    )
    schema_linking = SchemaLinkingOptions(
        mode=args.schema_link_mode,
        combine_strategy=args.schema_link_combine_strategy,
        only=str(args.schema_link_only).lower() == "true",
        representations=representations or ("ddl", "m_schema", "mac_schema"),
    )
    return PipelineOptions(
        max_fix_attempts=args.max_fix_attempts,
        langgraph_max_fix_attempts=args.langgraph_max_fix_attempts,
        schema_linking=schema_linking,
    )


def resolve_sql_dialect(dataset: str, sql_dialect: str) -> str:
    if sql_dialect and sql_dialect.lower() != "auto":
        return sql_dialect
    return "SQLite" if dataset.lower() == "spider" else "PostgreSQL"


def build_question_tasks(
    eval_data: list[dict[str, Any]],
    db_root_path: str,
    use_knowledge: bool,
    dataset: str,
    sql_dialect: str,
) -> list[QuestionTask]:
    question_list, db_path_list, knowledge_list, db_id_list, gold_sql_list = decouple_question_schema(
        eval_data,
        db_root_path,
        dataset,
    )
    assert len(question_list) == len(db_path_list) == len(knowledge_list) == len(db_id_list) == len(gold_sql_list)

    return [
        QuestionTask(
            index=i,
            db_id=db_id_list[i],
            db_path=db_path_list[i],
            question=question_list[i],
            knowledge=knowledge_list[i] if use_knowledge else None,
            dataset=dataset.lower(),
            gold_sql=gold_sql_list[i],
            sql_dialect=sql_dialect,
        )
        for i in range(len(question_list))
    ]


def resolve_schema_context(task: QuestionTask, schema_prompt_override: str | None) -> str:
    return schema_prompt_override if schema_prompt_override is not None else generate_schema_prompt(task.db_path)


def build_langgraph_initial_question(task: QuestionTask) -> str:
    parts = [
        f"Generate exactly one {task.sql_dialect} SQL query for this question.",
        "Return only raw SQL.",
        f"Question: {task.question}",
    ]
    if task.knowledge:
        parts.append(f"External Knowledge: {task.knowledge}")
    return "\n".join(parts)


def build_langgraph_verification_question(task: QuestionTask, candidate_sql: str) -> str:
    parts = [
        f"Verify this {task.sql_dialect} SQL query against the schema.",
        "If it is wrong, return a corrected query. If it is correct, return it unchanged.",
        "Return only raw SQL.",
        f"Question: {task.question}",
    ]
    if task.knowledge:
        parts.append(f"External Knowledge: {task.knowledge}")
    parts.append(f"Candidate SQL:\n{candidate_sql}")
    return "\n".join(parts)


def build_langgraph_repair_question(
    task: QuestionTask,
    current_sql: str,
    error_message: str,
    mode: str,
) -> str:
    mode_instruction = {
        "repair": "Repair this invalid SQL query.",
        "targeted": "Fix this invalid SQL query using the exact database error.",
        "regenerate": "Write a new SQL query from scratch. Do not reuse the invalid query.",
    }.get(mode, "Repair this invalid SQL query.")
    parts = [
        f"{mode_instruction} Use {task.sql_dialect}.",
        "Return only raw SQL.",
        f"Question: {task.question}",
    ]
    if task.knowledge:
        parts.append(f"External Knowledge: {task.knowledge}")
    parts.extend(
        [
            f"Invalid SQL:\n{current_sql}",
            f"Database error:\n{error_message}",
        ]
    )
    return "\n".join(parts)


def langgraph_request_id(task: QuestionTask) -> str:
    return str(task.index + 1)


def generate_initial_sql(
    llm: LLMGateway,
    task: QuestionTask,
    schema_prompt_override: str | None,
    max_sql_repair_attempts: int = 2,
) -> tuple[str, str]:
    schema_context = resolve_schema_context(task, schema_prompt_override)
    prompt = generate_combined_prompts_one(
        db_path=task.db_path,
        question=task.question,
        knowledge=task.knowledge,
        schema_prompt_override=schema_context,
        sql_dialect=task.sql_dialect,
    )
    with open("file.txt", "a", encoding="utf-8") as f:
        f.write(prompt)

    if llm.is_langgraph:
        sql = llm.complete_sql_with_schema(
            prompt=prompt,
            question=build_langgraph_initial_question(task),
            schema_context=schema_context,
            request_id=langgraph_request_id(task),
            sql_dialect=task.sql_dialect,
            max_sql_repair_attempts=max_sql_repair_attempts,
            temperature=0.0,
            max_tokens=512,
        )
    else:
        sql = llm.complete_sql(prompt=prompt, temperature=0.0, max_tokens=512)
    return sql, prompt


def verify_sql(
    llm: LLMGateway,
    task: QuestionTask,
    candidate_sql: str,
    schema_prompt_override: str | None,
) -> str:
    schema_context = resolve_schema_context(task, schema_prompt_override)
    prompt = generate_verification_prompt(
        db_path=task.db_path,
        question=task.question,
        candidate_sql=candidate_sql,
        knowledge=task.knowledge,
        schema_prompt_override=schema_context,
        sql_dialect=task.sql_dialect,
    )
    return llm.complete_sql_with_schema(
        prompt=prompt,
        question=build_langgraph_verification_question(task, candidate_sql),
        schema_context=schema_context,
        request_id=langgraph_request_id(task),
        sql_dialect=task.sql_dialect,
        max_sql_repair_attempts=0,
        temperature=0.0,
        max_tokens=512,
    )


def repair_sql_with_fallback(
    llm: LLMGateway,
    task: QuestionTask,
    current_sql: str,
    error_message: str,
    schema_prompt_override: str | None,
) -> str:
    schema_context = resolve_schema_context(task, schema_prompt_override)
    repair_prompt = generate_repair_prompt(
        db_path=task.db_path,
        question=task.question,
        failing_sql=current_sql,
        error_message=error_message,
        knowledge=task.knowledge,
        schema_prompt_override=schema_context,
        sql_dialect=task.sql_dialect,
    )
    repaired_sql = llm.complete_sql_with_schema(
        prompt=repair_prompt,
        question=build_langgraph_repair_question(task, current_sql, error_message, "repair"),
        schema_context=schema_context,
        request_id=langgraph_request_id(task),
        sql_dialect=task.sql_dialect,
        max_sql_repair_attempts=0,
        temperature=0.2,
        max_tokens=512,
    )

    if normalize_sql(repaired_sql) == normalize_sql(current_sql):
        targeted_prompt = generate_targeted_repair_prompt(
            db_path=task.db_path,
            question=task.question,
            failing_sql=current_sql,
            error_message=error_message,
            knowledge=task.knowledge,
            schema_prompt_override=schema_context,
            sql_dialect=task.sql_dialect,
        )
        repaired_sql = llm.complete_sql_with_schema(
            prompt=targeted_prompt,
            question=build_langgraph_repair_question(task, current_sql, error_message, "targeted"),
            schema_context=schema_context,
            request_id=langgraph_request_id(task),
            sql_dialect=task.sql_dialect,
            max_sql_repair_attempts=0,
            temperature=0.3,
            max_tokens=512,
        )

    if normalize_sql(repaired_sql) == normalize_sql(current_sql):
        regen_prompt = generate_regeneration_prompt(
            db_path=task.db_path,
            question=task.question,
            previous_sql=current_sql,
            error_message=error_message,
            knowledge=task.knowledge,
            schema_prompt_override=schema_context,
            sql_dialect=task.sql_dialect,
        )
        repaired_sql = llm.complete_sql_with_schema(
            prompt=regen_prompt,
            question=build_langgraph_repair_question(task, current_sql, error_message, "regenerate"),
            schema_context=schema_context,
            request_id=langgraph_request_id(task),
            sql_dialect=task.sql_dialect,
            max_sql_repair_attempts=0,
            temperature=0.3,
            max_tokens=512,
        )

    return repaired_sql


def run_repair_loop(
    llm: LLMGateway,
    task: QuestionTask,
    current_sql: str,
    initial_validation: dict[str, Any],
    options: PipelineOptions,
    schema_prompt_override: str | None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    validation = initial_validation
    repair_attempts = []

    for attempt in range(1, options.max_fix_attempts + 1):
        if validation["syntax_ok"] and validation["execution_ok"]:
            break

        error_message = validation["syntax_error"] or validation["execution_error"] or "Unknown SQL validation error"
        repaired_sql = repair_sql_with_fallback(
            llm=llm,
            task=task,
            current_sql=current_sql,
            error_message=error_message,
            schema_prompt_override=schema_prompt_override,
        )

        repair_validation = validate_sql(repaired_sql, db_path=task.db_path, dialect=task.sql_dialect)
        repair_attempts.append(
            {
                "attempt": attempt,
                "input_sql": current_sql,
                "error": error_message,
                "repaired_sql": repaired_sql,
                "no_change": normalize_sql(repaired_sql) == normalize_sql(current_sql),
                "syntax_ok": repair_validation["syntax_ok"],
                "syntax_error": repair_validation["syntax_error"],
                "execution_ok": repair_validation["execution_ok"],
                "execution_error": repair_validation["execution_error"],
            }
        )

        current_sql = repaired_sql
        validation = repair_validation

    return current_sql, validation, repair_attempts


def build_debug_record(
    task: QuestionTask,
    initial_sql: str,
    verification_sql: str,
    final_sql: str,
    validation: dict[str, Any],
    repair_attempts: list[dict[str, Any]],
    schema_linking_debug: dict[str, Any] | None,
    linked_result: dict[str, Any] | None,
    options: PipelineOptions,
) -> dict[str, Any]:
    return {
        "question": task.question,
        "db_id": task.db_id,
        "dataset": task.dataset,
        "gold_sql": task.gold_sql,
        "initial_sql": initial_sql,
        "verification_sql": verification_sql,
        "final_sql": final_sql,
        "syntax_ok": validation["syntax_ok"],
        "execution_ok": validation["execution_ok"],
        "repair_attempts": repair_attempts,
        "schema_linking": to_json_safe(schema_linking_debug) if schema_linking_debug else None,
        "schema_linking_summary": to_json_safe(
            {
                "mode": options.schema_linking.mode,
                "combine_strategy": options.schema_linking.combine_strategy,
                "predicted_tables": sorted(list(linked_result.get("predicted_tables", set()))) if linked_result else [],
                "predicted_columns": sorted(list(linked_result.get("predicted_columns", set()))) if linked_result else [],
            }
        )
        if linked_result
        else None,
        "index": task.index,
    }


def build_failed_debug_record(task: QuestionTask, exc: BaseException) -> dict[str, Any]:
    return {
        "question": task.question,
        "db_id": task.db_id,
        "dataset": task.dataset,
        "gold_sql": task.gold_sql,
        "initial_sql": "",
        "verification_sql": "",
        "final_sql": "SELECT 1",
        "syntax_ok": False,
        "execution_ok": False,
        "repair_attempts": [],
        "schema_linking": None,
        "schema_linking_summary": None,
        "index": task.index,
        "failed": True,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ),
    }


def process_question(task: QuestionTask, llm: LLMGateway, options: PipelineOptions):
    schema_prompt_override = None
    schema_linking_debug = None
    linked_result = None

    if options.schema_linking.enabled and not llm.is_langgraph:
        linked_result, schema_prompt_override, schema_linking_debug = run_schema_linking_for_question(
            llm=llm,
            db_path=task.db_path,
            question=task.question,
            knowledge=task.knowledge,
            options=options.schema_linking,
        )

        if options.schema_linking.only:
            debug_record = build_debug_record(
                task=task,
                initial_sql="",
                verification_sql="",
                final_sql="SELECT 1",
                validation={"syntax_ok": None, "execution_ok": None},
                repair_attempts=[],
                schema_linking_debug=schema_linking_debug,
                linked_result=linked_result,
                options=options,
            )
            print(f"Schema-link-only processed {task.index}th question: {task.question}")
            return "SELECT 1", task.index, debug_record

    initial_sql, _initial_prompt = generate_initial_sql(
        llm=llm,
        task=task,
        schema_prompt_override=schema_prompt_override,
        max_sql_repair_attempts=options.langgraph_max_fix_attempts,
    )

    verification_sql = ""
    if options.repair_enabled:
        verification_sql = verify_sql(
            llm=llm,
            task=task,
            candidate_sql=initial_sql,
            schema_prompt_override=schema_prompt_override,
        )

    current_sql = verification_sql if verification_sql else initial_sql
    validation = validate_sql(current_sql, db_path=task.db_path, dialect=task.sql_dialect)
    repair_attempts: list[dict[str, Any]] = []

    if options.repair_enabled:
        current_sql, validation, repair_attempts = run_repair_loop(
            llm=llm,
            task=task,
            current_sql=current_sql,
            initial_validation=validation,
            options=options,
            schema_prompt_override=schema_prompt_override,
        )

    debug_record = build_debug_record(
        task=task,
        initial_sql=initial_sql,
        verification_sql=verification_sql,
        final_sql=current_sql,
        validation=validation,
        repair_attempts=repair_attempts,
        schema_linking_debug=schema_linking_debug,
        linked_result=linked_result,
        options=options,
    )

    print(f"Processed {task.index}th question: {task.question}")
    return current_sql, task.index, debug_record


def worker_function(worker_task: WorkerTask):
    llm = LLMGateway(worker_task.llm_config)
    return process_question(
        task=worker_task.question,
        llm=llm,
        options=worker_task.options,
    )


def collect_response_from_tasks(
    question_tasks: list[QuestionTask],
    llm_config: LLMRuntimeConfig,
    options: PipelineOptions,
    num_threads: int = 3,
):
    tasks = [
        WorkerTask(
            question=question_task,
            llm_config=llm_config,
            options=options,
        )
        for question_task in question_tasks
    ]

    responses = []
    debug_records = []

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        future_to_task = {
            executor.submit(worker_function, task): task for task in tasks
        }
        for future in tqdm(
            concurrent.futures.as_completed(future_to_task),
            total=len(tasks),
        ):
            worker_task = future_to_task[future]
            try:
                final_sql, i, debug_record = future.result()
                response_sql = post_process_response(
                    final_sql,
                    worker_task.question.db_path,
                    dataset=worker_task.question.dataset,
                    db_id=worker_task.question.db_id,
                )
            except Exception as exc:
                i = worker_task.question.index
                debug_record = build_failed_debug_record(worker_task.question, exc)
                response_sql = post_process_response(
                    "SELECT 1",
                    worker_task.question.db_path,
                    dataset=worker_task.question.dataset,
                    db_id=worker_task.question.db_id,
                )
                print(
                    f"Failed {i}th question: {worker_task.question.question} "
                    f"({type(exc).__name__}: {exc})"
                )

            responses.append((response_sql, i))
            debug_records.append(debug_record)

    return responses, debug_records


def collect_response_from_gpt(
    db_path_list,
    question_list,
    api_key,
    engine,
    llm_provider,
    llm_rest_url,
    llm_rest_timeout,
    num_threads=3,
    knowledge_list=None,
    max_fix_attempts=2,
    schema_link_mode="none",
    schema_link_combine_strategy="union",
    schema_link_only=False,
    schema_link_representations=None,
):
    """
    Backward-compatible wrapper for callers that used the old positional API.
    New code should prefer collect_response_from_tasks with dataclass configs.
    """
    representations = tuple(schema_link_representations or ["ddl", "m_schema", "mac_schema"])
    llm_config = LLMRuntimeConfig(
        engine=engine,
        provider=str(llm_provider).lower(),
        api_key=api_key,
        rest_url=llm_rest_url,
        rest_timeout=int(llm_rest_timeout),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1"),
        langgraph_url=os.getenv("LANGGRAPH_URL", "http://127.0.0.1:8081/ask"),
        langgraph_user_id=os.getenv("LANGGRAPH_USER_ID", "bird-mini-dev"),
        langgraph_model=os.getenv("LANGGRAPH_MODEL", engine),
    )
    options = PipelineOptions(
        max_fix_attempts=max_fix_attempts,
        langgraph_max_fix_attempts=max_fix_attempts,
        schema_linking=SchemaLinkingOptions(
            mode=schema_link_mode,
            combine_strategy=schema_link_combine_strategy,
            only=str(schema_link_only).lower() == "true",
            representations=representations,
        ),
    )
    question_tasks = [
        QuestionTask(
            index=i,
            db_id=os.path.splitext(os.path.basename(os.path.normpath(db_path_list[i])))[0],
            db_path=db_path_list[i],
            question=question_list[i],
            knowledge=knowledge_list[i] if knowledge_list else None,
            dataset="bird",
            gold_sql=None,
            sql_dialect="PostgreSQL",
        )
        for i in range(len(question_list))
    ]
    return collect_response_from_tasks(
        question_tasks=question_tasks,
        llm_config=llm_config,
        options=options,
        num_threads=num_threads,
    )


def parse_args() -> argparse.Namespace:
    args_parser = argparse.ArgumentParser()
    args_parser.add_argument("--eval_path", type=str, default="")
    args_parser.add_argument("--dataset", type=str, choices=["bird", "spider"], default="bird")
    args_parser.add_argument("--sql_dialect", type=str, default="auto")
    args_parser.add_argument("--mode", type=str, default="dev")
    args_parser.add_argument("--test_path", type=str, default="")
    args_parser.add_argument("--use_knowledge", type=str, default="False")
    args_parser.add_argument("--db_root_path", type=str, default="")
    args_parser.add_argument("--api_key", type=str, required=True)
    args_parser.add_argument("--engine", type=str, required=True)
    args_parser.add_argument("--data_output_path", type=str)
    args_parser.add_argument("--chain_of_thought", type=str)
    args_parser.add_argument("--num_processes", type=int, default=3)
    args_parser.add_argument("--max_fix_attempts", type=int, default=2)
    args_parser.add_argument("--langgraph_max_fix_attempts", type=int, default=2)
    args_parser.add_argument("--limit", type=int, default=None)
    args_parser.add_argument("--difficulty", type=str, default="all")
    args_parser.add_argument("--schema_link_mode", type=str, default="none")
    args_parser.add_argument("--schema_link_combine_strategy", type=str, default="union")
    args_parser.add_argument("--schema_link_only", type=str, default="False")
    args_parser.add_argument("--schema_link_representations", type=str, default="ddl,m_schema,mac_schema")
    args_parser.add_argument("--linker_eval_output_path", type=str, default="")
    return args_parser.parse_args()


def main() -> None:
    args = parse_args()
    llm_config = build_llm_config(args)
    options = build_pipeline_options(args)
    sql_dialect = resolve_sql_dialect(args.dataset, args.sql_dialect)

    eval_data = load_eval_data(
        path=args.eval_path,
        difficulty=args.difficulty,
        limit=args.limit,
        dataset=args.dataset,
    )
    question_tasks = build_question_tasks(
        eval_data=eval_data,
        db_root_path=args.db_root_path,
        use_knowledge=args.use_knowledge.lower() == "true",
        dataset=args.dataset,
        sql_dialect=sql_dialect,
    )

    responses, debug_records = collect_response_from_tasks(
        question_tasks=question_tasks,
        llm_config=llm_config,
        options=options,
        num_threads=args.num_processes,
    )

    output_name = build_output_name(
        data_output_path=args.data_output_path,
        mode=args.mode,
        engine=args.engine,
        chain_of_thought=args.chain_of_thought,
        sql_dialect=sql_dialect,
    )
    generate_sql_file(sql_lst=responses, output_path=output_name)

    debug_output_name = output_name.replace(".json", "_debug.json")
    generate_debug_file(debug_records, debug_output_name)

    linker_eval_output_name = args.linker_eval_output_path or output_name.replace(".json", "_linker_eval.jsonl")
    linker_eval_rows = generate_linker_eval_jsonl(
        eval_data=eval_data,
        debug_lst=debug_records,
        output_path=linker_eval_output_name,
    )

    print(
        "successfully collect results from {} for {} evaluation; SQL dialect {}; LLM provider {} Difficulty: {}; Use knowledge: {}; Use COT: {}; Max fix attempts: {}; Schema link mode: {}; Schema link combine: {}; Schema link only: {}; Linker eval rows: {}".format(
            args.engine,
            args.mode,
            sql_dialect,
            llm_config.provider,
            args.difficulty,
            args.use_knowledge,
            args.chain_of_thought,
            options.max_fix_attempts,
            options.schema_linking.mode,
            options.schema_linking.combine_strategy,
            options.schema_linking.only,
            len(linker_eval_rows),
        )
    )


if __name__ == "__main__":
    main()
