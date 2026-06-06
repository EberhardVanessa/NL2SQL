from table_schema import generate_schema_prompt


def generate_comment_prompt(question, knowledge=None, sql_dialect="PostgreSQL"):
    base_prompt = f"-- Using valid {sql_dialect}"
    knowledge_text = " and understanding External Knowledge" if knowledge else ""
    knowledge_prompt = f"-- External Knowledge: {knowledge}" if knowledge else ""

    combined_prompt = (
        f"{base_prompt}{knowledge_text}, answer the following questions for the tables provided above.\n"
        f"-- {question}\n"
        f"{knowledge_prompt}"
    )
    return combined_prompt


def generate_cot_prompt(sql_dialect="PostgreSQL"):
    return f"\nGenerate the {sql_dialect} SQL for the above question after thinking step by step: "


def generate_instruction_prompt(sql_dialect="PostgreSQL"):
    return f"""
In your response, you do not need to mention your intermediate steps.
Do not include any comments in your response.
Do not need to start with the symbol ```
You only need to return the result {sql_dialect} SQL code
start from SELECT
""".strip()


def generate_combined_prompts_one(db_path, question, knowledge=None, schema_prompt_override=None, sql_dialect="PostgreSQL"):
    schema_prompt = schema_prompt_override if schema_prompt_override is not None else generate_schema_prompt(db_path)
    comment_prompt = generate_comment_prompt(question, knowledge, sql_dialect)
    cot_prompt = generate_cot_prompt(sql_dialect)
    instruction_prompt = generate_instruction_prompt(sql_dialect)

    combined_prompts = "\n\n".join(
        [schema_prompt, comment_prompt, cot_prompt, instruction_prompt]
    )
    return combined_prompts


def generate_verification_prompt(db_path, question, candidate_sql, knowledge=None, schema_prompt_override=None, sql_dialect="PostgreSQL"):
    schema_prompt = schema_prompt_override if schema_prompt_override is not None else generate_schema_prompt(db_path)
    knowledge_prompt = f"\nExternal Knowledge:\n{knowledge}" if knowledge else ""

    return f"""
You are verifying a {sql_dialect} SQL query against the schema.

Schema:
{schema_prompt}

Question:
{question}{knowledge_prompt}

Candidate SQL:
{candidate_sql}

Instructions:
1. Check whether the SQL answers the question correctly.
2. Check whether all tables and columns used in the SQL exist in the schema.
3. Check whether joins and filters are consistent with the schema.
4. If the SQL is wrong, return a corrected SQL query.
5. If it is already correct, return the SQL unchanged.
6. Output only SQL.
7. Do not include comments, explanations, or markdown fences.
8. Start with SELECT.

Final SQL:
""".strip()


def generate_repair_prompt(db_path, question, failing_sql, error_message, knowledge=None, schema_prompt_override=None, sql_dialect="PostgreSQL"):
    schema_prompt = schema_prompt_override if schema_prompt_override is not None else generate_schema_prompt(db_path)
    knowledge_prompt = f"\nExternal Knowledge:\n{knowledge}" if knowledge else ""

    return f"""
You are repairing an invalid {sql_dialect} SQL query.

Schema:
{schema_prompt}

Question:
{question}{knowledge_prompt}

Invalid SQL:
{failing_sql}

Database error:
{error_message}

Rules:
1. The SQL above is invalid.
2. You MUST return a corrected SQL query.
3. Do NOT repeat the same invalid SQL.
4. Only use tables and columns that exist in the schema.
5. Preserve the original intent of the question.
6. Output only SQL.
7. Do not include comments, explanations, or markdown fences.
8. Start with SELECT.

Corrected SQL:
""".strip()


def generate_targeted_repair_prompt(db_path, question, failing_sql, error_message, knowledge=None, schema_prompt_override=None, sql_dialect="PostgreSQL"):
    schema_prompt = schema_prompt_override if schema_prompt_override is not None else generate_schema_prompt(db_path)
    knowledge_prompt = f"\nExternal Knowledge:\n{knowledge}" if knowledge else ""

    return f"""
Fix the invalid {sql_dialect} SQL query using the schema and the exact database error.

Schema:
{schema_prompt}

Question:
{question}{knowledge_prompt}

Current invalid SQL:
{failing_sql}

Database error:
{error_message}

Instructions:
1. The repaired SQL MUST be different from the invalid SQL if the error indicates a missing table or missing column.
2. Find the correct join path from the schema.
3. Do not invent columns, foreign keys, or table aliases.
4. Preserve the original meaning of the question.
5. Output only SQL.
6. Do not include comments, explanations, or markdown fences.
7. Start with SELECT.

Corrected SQL:
""".strip()


def generate_regeneration_prompt(db_path, question, previous_sql, error_message, knowledge=None, schema_prompt_override=None, sql_dialect="PostgreSQL"):
    schema_prompt = schema_prompt_override if schema_prompt_override is not None else generate_schema_prompt(db_path)
    knowledge_prompt = f"\nExternal Knowledge:\n{knowledge}" if knowledge else ""

    return f"""
The following SQL is invalid and should NOT be reused.

Schema:
{schema_prompt}

Question:
{question}{knowledge_prompt}

Invalid SQL:
{previous_sql}

Database error:
{error_message}

Write a new SQL query from scratch that answers the question correctly.
Do not reuse the invalid join path or missing column references.
Use only tables and columns that exist in the schema.

Output only SQL.
Do not include comments, explanations, or markdown fences.
Start with SELECT.

New SQL:
""".strip()
