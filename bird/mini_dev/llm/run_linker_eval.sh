set -e
trap 'echo "Script failed. Press ENTER to exit..."; read' ERR

mode='mini_dev'
engine='qwen2.5-coder:7b'
data_output_path='./exp_result/ollama_output_guarded/'
dialect='postgres'  # sqlglot dialect: postgres | sqlite | mysql | ...
# Optional override. If empty, auto-build from mode/engine/data_output_path.
input_jsonl='./exp_result/qwen/linker/bird/predict_mini_dev_Qwen2.5-Coder-7B-Instruct_PostgreSQL_linker_eval_table_only_union_ddl.jsonl'
output_csv=''

if [ -z "$input_jsonl" ]; then
  input_jsonl="${data_output_path}predict_${mode}_$(echo "$engine" | tr '/:' '__')_PostgreSQL_linker_eval.jsonl"
fi

if [ -z "$output_csv" ]; then
  output_csv="${data_output_path}predict_${mode}_$(echo "$engine" | tr '/:' '__')_PostgreSQL_linker_eval_diagnostics.csv"
fi

echo "Running linker eval"
echo "input_jsonl: $input_jsonl"
echo "output_csv:  $output_csv"
echo "dialect:     $dialect"

py -u ./src/linker_eval.py \
  --input_jsonl "${input_jsonl}" \
  --output_csv "${output_csv}" \
  --dialect "${dialect}"

echo "Script finished. Press ENTER to exit..."
read
