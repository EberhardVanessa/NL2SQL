import os
import re
import sqlite3


db_table_map = {
    "debit_card_specializing": [
        "customers",
        "gasstations",
        "products",
        "transactions_1k",
        "yearmonth",
    ],
    "student_club": [
        "major",
        "member",
        "attendance",
        "budget",
        "event",
        "expense",
        "income",
        "zip_code",
    ],
    "thrombosis_prediction": ["Patient", "Examination", "Laboratory"],
    "european_football_2": [
        "League",
        "Match",
        "Player",
        "Player_Attributes",
        "Team",
        "Team_Attributes",
    ],
    "formula_1": [
        "circuits",
        "seasons",
        "races",
        "constructors",
        "constructorResults",
        "constructorStandings",
        "drivers",
        "driverStandings",
        "lapTimes",
        "pitStops",
        "qualifying",
        "status",
        "results",
    ],
    "superhero": [
        "alignment",
        "attribute",
        "colour",
        "gender",
        "publisher",
        "race",
        "superpower",
        "superhero",
        "hero_attribute",
        "hero_power",
    ],
    "codebase_community": [
        "posts",
        "users",
        "badges",
        "comments",
        "postHistory",
        "postLinks",
        "tags",
        "votes",
    ],
    "card_games": [
        "cards",
        "foreign_data",
        "legalities",
        "rulings",
        "set_translations",
        "sets",
    ],
    "toxicology": ["molecule", "atom", "bond", "connected"],
    "california_schools": ["satscores", "frpm", "schools"],
    "financial": [
        "district",
        "account",
        "client",
        "disp",
        "card",
        "loan",
        "order",
        "trans",
    ],
}


RESERVED_IDENTIFIERS = {"order", "group", "by", "user", "match"}


def quote_identifier_sqlite(identifier: str) -> str:
    return f'"{identifier}"' if identifier.lower() in RESERVED_IDENTIFIERS else identifier



def quote_identifier_postgres(identifier: str) -> str:
    return f'"{identifier}"' if identifier.lower() in RESERVED_IDENTIFIERS else identifier



def nice_look_table(column_names: list, values: list):
    rows = []
    widths = [
        max(len(str(value[i])) for value in values + [column_names])
        for i in range(len(column_names))
    ]
    header = "".join(
        f"{column.rjust(width)} " for column, width in zip(column_names, widths)
    )
    for value in values:
        row = "".join(f"{str(v).rjust(width)} " for v, width in zip(value, widths))
        rows.append(row)
    rows = "\n".join(rows)
    return header + "\n" + rows



def generate_schema_prompt_sqlite(db_path, num_rows=None):
    full_schema_prompt_list = []
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    schemas = {}
    for table in tables:
        table_name = table[0]
        if table_name == "sqlite_sequence":
            continue
        cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?;",
            (table_name,),
        )
        create_prompt = cursor.fetchone()[0]
        schemas[table_name] = create_prompt
        if num_rows:
            cur_table = quote_identifier_sqlite(table_name)
            cursor.execute(f"SELECT * FROM {cur_table} LIMIT {int(num_rows)}")
            column_names = [description[0] for description in cursor.description]
            values = cursor.fetchall()
            rows_prompt = nice_look_table(column_names=column_names, values=values)
            verbose_prompt = (
                f"/* \n {num_rows} example rows: \n SELECT * FROM {cur_table} LIMIT {num_rows}; \n"
                f" {rows_prompt} \n */"
            )
            schemas[table_name] = f"{create_prompt} \n {verbose_prompt}"

    for _, ddl in schemas.items():
        full_schema_prompt_list.append(ddl)
    conn.close()
    return "\n\n".join(full_schema_prompt_list)



def connect_mysql():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "YOUR_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE", "BIRD"),
        unix_socket=os.getenv("MYSQL_SOCKET", "/tmp/mysql.sock"),
    )



def format_mysql_create_table(table_name, columns_info):
    lines = [f"CREATE TABLE {table_name}", "("]
    primary_key_defined = False
    for col in columns_info:
        column_name, data_type, nullable, key, _, _ = col
        sql_type = str.upper(data_type)
        null_type = "NOT NULL" if nullable == "NO" else "NULL"
        primary_key_part = " PRIMARY KEY" if "PRI" in key and not primary_key_defined else ""
        if "PRI" in key:
            primary_key_defined = True
        lines.append(f"    `{column_name}` {sql_type} {null_type}{primary_key_part},")
    lines[-1] = lines[-1].rstrip(",")
    lines.append(");")
    return "\n".join(lines)



def format_postgresql_create_table(table_name, columns_info, primary_keys=None, foreign_keys=None):
    primary_keys = primary_keys or []
    foreign_keys = foreign_keys or []

    lines = [f"CREATE TABLE {table_name}", "("]

    for i, (column_name, data_type, is_nullable) in enumerate(columns_info):
        null_status = "NULL" if is_nullable == "YES" else "NOT NULL"
        postgres_data_type = data_type.upper()
        column_name_fmt = quote_identifier_postgres(column_name)
        suffix = "," if i < len(columns_info) - 1 or primary_keys or foreign_keys else ""
        lines.append(f"    {column_name_fmt} {postgres_data_type} {null_status}{suffix}")

    if primary_keys:
        pk_cols = ", ".join(quote_identifier_postgres(col) for col in primary_keys)
        suffix = "," if foreign_keys else ""
        lines.append(f"    PRIMARY KEY ({pk_cols}){suffix}")

    for j, fk in enumerate(foreign_keys):
        src_col = quote_identifier_postgres(fk["column_name"])
        ref_table = fk["foreign_table_name"]
        ref_col = quote_identifier_postgres(fk["foreign_column_name"])
        suffix = "," if j < len(foreign_keys) - 1 else ""
        lines.append(f"    FOREIGN KEY ({src_col}) REFERENCES {ref_table} ({ref_col}){suffix}")

    lines.append(");")
    return "\n".join(lines)


def connect_postgresql():
    import psycopg2

    return psycopg2.connect(
        dbname=os.getenv("PGDATABASE", "BIRD"),
        user=os.getenv("PGUSER", "bird"),
        password=os.getenv("PGPASSWORD", "birdpass"),
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
    )


def _is_schema_sql_path(db_path):
    return os.path.basename(os.path.normpath(db_path)).lower() == "schema.sql"


def _spider_sqlite_path_from_schema_path(schema_path):
    db_dir = os.path.dirname(os.path.normpath(schema_path))
    db_id = os.path.basename(db_dir)
    return os.path.join(db_dir, f"{db_id}.sqlite")


def _extract_create_table_statements(schema_sql):
    statements = []
    current = []
    collecting = False

    for line in schema_sql.splitlines():
        stripped = line.strip()
        if not collecting and re.match(r"^CREATE\s+TABLE\b", stripped, flags=re.IGNORECASE):
            collecting = True
            current = [line]
            if stripped.endswith(";"):
                statements.append("\n".join(current))
                collecting = False
            continue
        if collecting:
            current.append(line)
            if stripped.endswith(";"):
                statements.append("\n".join(current))
                collecting = False

    return "\n\n".join(statements).strip()


def generate_schema_prompt_from_schema_sql(schema_path):
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    ddl = _extract_create_table_statements(schema_sql)
    return ddl or schema_sql


def _connect_sqlite_for_schema(db_path):
    if _is_schema_sql_path(db_path):
        sqlite_path = _spider_sqlite_path_from_schema_path(db_path)
        if os.path.exists(sqlite_path):
            return sqlite3.connect(sqlite_path)

        conn = sqlite3.connect(":memory:")
        ddl = generate_schema_prompt_from_schema_sql(db_path)
        conn.executescript(ddl)
        return conn

    return sqlite3.connect(db_path)


def generate_schema_dict_sqlite(db_path):
    """
    Build a structured schema dictionary from a Spider SQLite DB or schema.sql file.
    """
    conn = _connect_sqlite_for_schema(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    table_names = [row[0] for row in cursor.fetchall() if row[0] != "sqlite_sequence"]

    schema = {"tables": {}, "foreign_keys": []}

    for table in table_names:
        quoted_table = '"' + table.replace('"', '""') + '"'
        cursor.execute(f"PRAGMA table_info({quoted_table})")
        columns_info = cursor.fetchall()

        columns = {}
        for _cid, column_name, data_type, _notnull, _default, pk in columns_info:
            columns[column_name] = {
                "type": str(data_type or "TEXT").upper(),
                "primary_key": bool(pk),
            }

        cursor.execute(f"PRAGMA foreign_key_list({quoted_table})")
        for _id, _seq, target_table, source_column, target_column, *_rest in cursor.fetchall():
            if source_column in columns:
                columns[source_column]["foreign_key"] = {
                    "table": target_table,
                    "column": target_column,
                }
            schema["foreign_keys"].append(
                {
                    "source_table": table,
                    "source_column": source_column,
                    "target_table": target_table,
                    "target_column": target_column,
                }
            )

        schema["tables"][table] = {"columns": columns}

    conn.close()
    return schema


def generate_schema_dict(db_path):
    if _is_schema_sql_path(db_path):
        return generate_schema_dict_sqlite(db_path)
    return generate_schema_dict_postgresql(db_path)


def generate_schema_dict_postgresql(db_path):
    """
    Build a structured schema dictionary for schema linking and evaluation.
    """
    db = connect_postgresql()
    cursor = db.cursor()
    db_name = os.path.splitext(os.path.basename(os.path.normpath(db_path)))[0]
    tables = list(db_table_map[db_name])

    schema = {"tables": {}, "foreign_keys": []}

    for table in tables:
        cursor.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position;
            """,
            (table,),
        )
        raw_schema = cursor.fetchall()

        cursor.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
             AND tc.table_name = kcu.table_name
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = 'public'
              AND tc.table_name = %s
            ORDER BY kcu.ordinal_position;
            """,
            (table,),
        )
        primary_keys = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT
                kcu.column_name,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = 'public'
              AND tc.table_name = %s
            ORDER BY kcu.ordinal_position;
            """,
            (table,),
        )
        foreign_keys = [
            {
                "column_name": row[0],
                "foreign_table_name": row[1],
                "foreign_column_name": row[2],
            }
            for row in cursor.fetchall()
        ]

        columns = {}
        for column_name, data_type, _is_nullable in raw_schema:
            columns[column_name] = {
                "type": str(data_type).upper(),
                "primary_key": column_name in primary_keys,
            }

        for fk in foreign_keys:
            col = fk["column_name"]
            if col in columns:
                columns[col]["foreign_key"] = {
                    "table": fk["foreign_table_name"],
                    "column": fk["foreign_column_name"],
                }
            schema["foreign_keys"].append(
                {
                    "source_table": table,
                    "source_column": fk["column_name"],
                    "target_table": fk["foreign_table_name"],
                    "target_column": fk["foreign_column_name"],
                }
            )

        schema["tables"][table] = {"columns": columns}

    db.close()
    return schema


def generate_schema_prompt_postgresql(db_path):
    schema = generate_schema_dict_postgresql(db_path)
    schemas = {}

    for table_name, table_data in schema.get("tables", {}).items():
        columns_info = []
        primary_keys = []
        foreign_keys = []
        for column_name, col_data in table_data.get("columns", {}).items():
            columns_info.append((column_name, col_data.get("type", "TEXT"), "YES"))
            if col_data.get("primary_key"):
                primary_keys.append(column_name)
            fk = col_data.get("foreign_key")
            if isinstance(fk, dict):
                foreign_keys.append(
                    {
                        "column_name": column_name,
                        "foreign_table_name": fk.get("table", ""),
                        "foreign_column_name": fk.get("column", ""),
                    }
                )

        schemas[table_name] = format_postgresql_create_table(
            table_name=table_name,
            columns_info=columns_info,
            primary_keys=primary_keys,
            foreign_keys=foreign_keys,
        )

    return "\n\n".join(schemas.values())


def generate_schema_prompt(db_path):
    if _is_schema_sql_path(db_path):
        return generate_schema_prompt_from_schema_sql(db_path)
    return generate_schema_prompt_postgresql(db_path)
