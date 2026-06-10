set -e
trap 'echo "Script failed. Press ENTER to exit..."; read' ERR

dataset="bird" # bird | spider
if [ "$dataset" = "spider" ]; then
  eval_path='../../../spider/spider_data/dev.json'
  db_root_path='../../../spider/spider_data/database/'
  use_knowledge='False'
  mode='spider_dev'
  difficulty='simple'
  sql_dialect='SQLite'
elif [ "$dataset" = "bird" ]; then
  eval_path='./data/mini_dev_postgresql.json'
  db_root_path='./data/dev_databases/'
  use_knowledge='True' # only for bird dataset
  mode='mini_dev'
  difficulty='simple' # only for bird dataset
  sql_dialect='PostgreSQL'
else
  echo "Unknown dataset: $dataset (expected bird or spider)"
  exit 1
fi
YOUR_API_KEY='ollama'
engine='Qwen2.5-Coder-7B-Instruct'
num_threads=1
max_fix_attempts=0
langgraph_max_fix_attempts=0
schema_link_mode='table_only'           # none | table_only | full; ignored for LLM_PROVIDER=langgraph
schema_link_combine_strategy='union'    # union | intersection
schema_link_only='true'                # True => only run schema linker, skip SQL generation
schema_link_representations='ddl' #ddl,m_schema,mac_schema
linker_eval_output_path=''             # optional explicit path; if empty, auto-computed below
limit=250
data_output_path='./exp_result/qwen/linker/bird/'

if [ -z "$linker_eval_output_path" ]; then
  safe_engine=$(echo "$engine" | tr '/:' '__')
  safe_reps=$(echo "$schema_link_representations" | tr ',/' '__' | tr ':' '_')
  safe_dialect=$(echo "$sql_dialect" | tr '/:' '__')
  linker_eval_output_path="${data_output_path}predict_${mode}_${safe_engine}_${safe_dialect}_linker_eval_${schema_link_mode}_${schema_link_combine_strategy}_${safe_reps}.jsonl"
fi

export OPENAI_BASE_URL='http://localhost:11434/v1'
export LLM_PROVIDER='remote'   # local = Ollama | remote = direct REST model | langgraph = local Docker schema-agent
export LLM_REST_URL='http://127.0.0.1:8000/generate'
export LLM_REST_TIMEOUT='600'
export LANGGRAPH_URL='http://127.0.0.1:8081/ask'
export LANGGRAPH_USER_ID='bird-mini-dev'
export LANGGRAPH_MODEL='schema-agent'
export PGHOST='localhost'
export PGPORT='5432'
export PGDATABASE='BIRD'
export PGUSER='bird'
export PGPASSWORD='birdpass'

# Optional but recommended for syntax validation before execution:
# pip install sqlglot psycopg2-binary openai requests tqdm

echo "generate $engine batch for $dataset, run in $num_threads threads, with knowledge: $use_knowledge, max_fix_attempts: $max_fix_attempts, langgraph_max_fix_attempts: $langgraph_max_fix_attempts"

py -u ./src/gpt_request.py \
  --dataset "${dataset}" \
  --sql_dialect "${sql_dialect}" \
  --db_root_path "${db_root_path}" \
  --api_key "${YOUR_API_KEY}" \
  --mode "${mode}" \
  --engine "${engine}" \
  --eval_path "${eval_path}" \
  --data_output_path "${data_output_path}" \
  --use_knowledge "${use_knowledge}" \
  --num_processes "${num_threads}" \
  --difficulty "${difficulty}" \
  --max_fix_attempts "${max_fix_attempts}" \
  --langgraph_max_fix_attempts "${langgraph_max_fix_attempts}" \
  --schema_link_mode "${schema_link_mode}" \
  --schema_link_combine_strategy "${schema_link_combine_strategy}" \
  --schema_link_only "${schema_link_only}" \
  --schema_link_representations "${schema_link_representations}" \
  --linker_eval_output_path "${linker_eval_output_path}" \
  --limit "${limit}"

echo "Script finished. Press ENTER to exit..."
read
