GET_SCHEMA_PROMPT = """
You are a careful database schema assistant.

Extract only the schema information that is relevant to the user's question.

User question:
{question}
Target SQL dialect:
{sql_dialect}
{clarification_block}
Schema files:
{schema_context}

Return a concise schema summary.
Do not invent tables, columns, or relations.
""".strip()


CHECK_SCHEMA_CLARIFICATION_PROMPT = """
You are checking whether the schema extraction is sufficient.

User question:
{question}

Target SQL dialect:
{sql_dialect}

Extracted schema:
{extracted_schema}

User clarification so far:
{thinking_clarification_answer}

Return JSON exactly like this:
{{
  "needs_clarification": true or false,
  "follow_up_question": "question for the user if needed, otherwise empty string"
}}

Ask for clarification only if the extracted schema is insufficient, ambiguous, or cannot safely support the next step.
""".strip()


GET_TABLES_COLUMNS_PROMPT = """
You are a careful database schema assistant.

Based on the user question, user clarification, and extracted schema, identify the relevant tables and columns.

User question:
{question}
Target SQL dialect:
{sql_dialect}
{clarification_block}
Extracted schema:
{extracted_schema}

Return JSON with this shape:
{{
  "tables": [
    {{
      "table": "table_name",
      "columns": ["column_1", "column_2"],
      "reason": "why this table is relevant"
    }}
  ]
}}

Only include tables and columns supported by the schema.
Do not invent anything.
""".strip()


CHECK_TABLES_COLUMNS_CLARIFICATION_PROMPT = """
You are checking whether the selected tables and columns are sufficient.

User question:
{question}

Target SQL dialect:
{sql_dialect}

Extracted schema:
{extracted_schema}

Selected tables and columns:
{tables_columns}

User clarification so far:
{thinking_clarification_answer}

Return JSON exactly like this:
{{
  "needs_clarification": true or false,
  "follow_up_question": "question for the user if needed, otherwise empty string"
}}

Ask for clarification only if table or column selection is ambiguous or unsafe.
""".strip()


GET_WHERE_CONDITIONS_PROMPT = """
You are a careful SQL planning assistant.

Identify the WHERE conditions needed to answer the user's question.

User question:
{question}
Target SQL dialect:
{sql_dialect}
{clarification_block}

Relevant tables and columns:
{tables_columns}

Schema:
{extracted_schema}

Return JSON with this shape:
{{
  "where_conditions": [
    {{
      "column": "table.column",
      "operator": "=",
      "value": "value or placeholder",
      "reason": "why this filter is needed"
    }}
  ]
}}

If no WHERE conditions are needed, return:
{{
  "where_conditions": []
}}

Only use columns supported by the schema.
""".strip()


CHECK_WHERE_CONDITIONS_CLARIFICATION_PROMPT = """
You are checking whether the WHERE conditions are sufficient.

User question:
{question}

Target SQL dialect:
{sql_dialect}

Selected tables and columns:
{tables_columns}

WHERE conditions:
{where_conditions}

User clarification so far:
{thinking_clarification_answer}

Return JSON exactly like this:
{{
  "needs_clarification": true or false,
  "follow_up_question": "question for the user if needed, otherwise empty string"
}}

Ask for clarification only if filters, date ranges, entities, or requested constraints are ambiguous.
""".strip()


GET_LIMIT_AGGREGATION_PROMPT = """
You are a careful SQL planning assistant.

Determine whether the query needs:
- aggregation
- GROUP BY
- ORDER BY
- LIMIT

User question:
{question}
Target SQL dialect:
{sql_dialect}
{clarification_block}
Relevant tables and columns:
{tables_columns}

WHERE conditions:
{where_conditions}

Schema:
{extracted_schema}

Return JSON with this shape:
{{
  "aggregations": [
    {{
      "expression": "COUNT(*) or SUM(table.column) etc.",
      "alias": "alias_name",
      "reason": "why this aggregation is needed"
    }}
  ],
  "group_by": ["table.column"],
  "order_by": [
    {{
      "expression": "column_or_alias",
      "direction": "ASC or DESC"
    }}
  ],
  "limit": null
}}

If something is not needed, use an empty list or null.
Only use supported tables and columns.
Do not invent joins, columns, aliases, or relationships.
""".strip()


CHECK_LIMIT_AGGREGATION_CLARIFICATION_PROMPT = """
You are checking whether LIMIT, aggregation, grouping, and ordering are sufficient.

User question:
{question}

Target SQL dialect:
{sql_dialect}

Tables and columns:
{tables_columns}

WHERE conditions:
{where_conditions}

Limit and aggregation planning:
{limit_aggregation}

User clarification so far:
{thinking_clarification_answer}

Return JSON exactly like this:
{{
  "needs_clarification": true or false,
  "follow_up_question": "question for the user if needed, otherwise empty string"
}}

Ask for clarification only if aggregation, grouping, sorting, ranking, or limits are ambiguous.
""".strip()


CREATE_QUERY_PROMPT = """
You are a SQL query generator.

Create exactly one SQL query using only the information below.

User question:
{question}
Target SQL dialect:
{sql_dialect}
{clarification_block}
Schema:
{extracted_schema}

Relevant tables and columns:
{tables_columns}

WHERE conditions:
{where_conditions}

Limit, aggregation, grouping and ordering:
{limit_aggregation}

Output rules:
- Return only raw SQL.
- Do not use markdown.
- Do not use ```sql fences.
- Do not explain the query.
- Do not add notes, comments, bullet points, or prose.
- Do not include any text before or after the SQL.
- Do not invent tables, columns, joins, or relations.
""".strip()


CHECK_QUERY_CLARIFICATION_PROMPT = """
You are checking whether the generated query is safe and complete.

User question:
{question}

Target SQL dialect:
{sql_dialect}

Generated query:
{generated_query}

Schema:
{extracted_schema}

User clarification so far:
{thinking_clarification_answer}

Return JSON exactly like this:
{{
  "needs_clarification": true or false,
  "follow_up_question": "question for the user if needed, otherwise empty string"
}}

Ask for clarification only if the final query cannot be safely created or still depends on an unresolved user choice.
""".strip()


VERIFY_QUERY_PROMPT = """
You are verifying a {sql_dialect} SQL query against the schema and planning artifacts.

Question:
{question}
{clarification_block}
Schema:
{extracted_schema}

Relevant tables and columns:
{tables_columns}

WHERE conditions:
{where_conditions}

Limit, aggregation, grouping and ordering:
{limit_aggregation}

Candidate SQL:
{generated_query}

Instructions:
1. Check whether the SQL answers the question correctly.
2. Check whether all tables and columns used in the SQL exist in the schema.
3. Check whether joins and filters are consistent with the schema.
4. If the SQL is wrong, return a corrected {sql_dialect} SQL query.
5. If it is already correct, return the SQL unchanged.
6. Output only SQL.
7. Do not include comments, explanations, or markdown fences.

Final SQL:
""".strip()


REPAIR_QUERY_PROMPT = """
You are repairing an invalid {sql_dialect} SQL query.

Schema:
{extracted_schema}

Question:
{question}
{clarification_block}
Invalid SQL:
{generated_query}

Database error:
{error_message}

Rules:
1. The SQL above is invalid.
2. You MUST return a corrected {sql_dialect} SQL query.
3. Do NOT repeat the same invalid SQL.
4. Only use tables and columns that exist in the schema.
5. Preserve the original intent of the question.
6. Output only SQL.
7. Do not include comments, explanations, or markdown fences.

Corrected SQL:
""".strip()


TARGETED_REPAIR_QUERY_PROMPT = """
Fix the invalid {sql_dialect} SQL query using the schema and the exact database error.

Schema:
{extracted_schema}

Question:
{question}
{clarification_block}
Current invalid SQL:
{generated_query}

Database error:
{error_message}

Instructions:
1. The repaired SQL MUST be different from the invalid SQL if the error indicates a missing table or missing column.
2. Find the correct join path from the schema.
3. Do not invent columns, foreign keys, or table aliases.
4. Preserve the original meaning of the question.
5. Output only SQL.
6. Do not include comments, explanations, or markdown fences.

Corrected SQL:
""".strip()


REGENERATE_QUERY_PROMPT = """
The following {sql_dialect} SQL is invalid and should NOT be reused.

Schema:
{extracted_schema}

Question:
{question}
{clarification_block}
Invalid SQL:
{generated_query}

Database error:
{error_message}

Write a new {sql_dialect} SQL query from scratch that answers the question correctly.
Do not reuse the invalid join path or missing column references.
Use only tables and columns that exist in the schema.

Output only SQL.
Do not include comments, explanations, or markdown fences.

New SQL:
""".strip()
