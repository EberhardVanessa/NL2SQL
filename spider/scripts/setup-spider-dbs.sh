#!/bin/sh
set -eu

DATABASE_ROOT="${DATABASE_ROOT:-/workspace/spider_data/database}"

echo "SQLite Spider DB setup"
echo "Database root: $DATABASE_ROOT"

if [ ! -d "$DATABASE_ROOT" ]; then
  echo "ERROR: Database root does not exist: $DATABASE_ROOT"
  exit 1
fi

echo ""
echo "Scanning database folders..."

find "$DATABASE_ROOT" -mindepth 1 -maxdepth 1 -type d | sort | while read db_dir; do
  db_name="$(basename "$db_dir")"
  db_file="$db_dir/$db_name.sqlite"
  schema_file="$db_dir/schema.sql"

  echo ""
  echo "========================================"
  echo "Database: $db_name"
  echo "Folder:   $db_dir"
  echo "DB file:  $db_file"
  echo "Schema:   $schema_file"

  if [ -f "$db_file" ]; then
    echo "Found existing SQLite file."

    table_count="$(sqlite3 "$db_file" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")"

    if [ "$table_count" -gt 0 ]; then
      echo "OK: $db_name.sqlite contains $table_count tables."
      echo "Tables:"
      sqlite3 "$db_file" ".tables"
    else
      echo "WARNING: $db_name.sqlite exists but contains no user tables."

      if [ -f "$schema_file" ]; then
        echo "Applying schema.sql to existing empty database..."
        sqlite3 "$db_file" < "$schema_file"
        echo "Schema applied."
      else
        echo "No schema.sql found. Cannot create tables."
      fi
    fi

  else
    echo "SQLite file is missing: $db_file"

    if [ -f "$schema_file" ]; then
      echo "Creating new SQLite database from schema.sql..."
      sqlite3 "$db_file" < "$schema_file"
      echo "Created: $db_file"
    else
      echo "ERROR: No $db_name.sqlite and no schema.sql found. Skipping."
      continue
    fi
  fi

  echo "Done: $db_name"
done

echo ""
echo "All database folders processed."