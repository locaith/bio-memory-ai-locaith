# A/B đo hạng mục multi-hop: cùng một bản build, chỉ khác tham số xếp hạng.
#   Nhánh A ("old")  = tái lập hành vi TRƯỚC khi sửa (recency 1.5, ngưỡng dense 0.4, không lọc trùng)
#   Nhánh B ("new")  = mặc định sau khi sửa (recency 0.4, ngưỡng 0.15, lọc trùng 0.8)
# Chạy tuần tự để hai nhánh không tranh Ollama.
$ErrorActionPreference = "Continue"
Set-Location "C:\locaith\bio-memory-ai-locaith"

$env:LLM_BACKEND        = "ollama"
$env:MODEL_ID           = "qwen2.5:7b-instruct"
$env:OLLAMA_BASE_URL    = "http://localhost:11434"
$env:EMBEDDING_BACKEND  = "openai"
$env:EMBEDDING_BASE_URL = "http://localhost:11434/v1"
$env:EMBEDDING_API_KEY  = "ollama"
$env:EMBEDDING_MODEL    = "nomic-embed-text"
$env:EMBEDDING_DIMENSIONS = "768"

$common = @(
  "scripts\run_locomo_eval.py",
  "--systems","naive-rag,bio-memory",
  "--categories","1",
  "--max-conversations","6",
  "--top-k","10"
)

Write-Output "===== NHANH A (tai lap hanh vi CU) ====="
$env:BIO_EPISODE_RECENCY_WEIGHT = "1.5"
$env:BIO_EPISODE_MIN_DENSE      = "0.4"
$env:BIO_EPISODE_DEDUP_JACCARD  = "1.1"   # >1 => khong bao gio loc trung
& ".\.venv\Scripts\python.exe" @common --tag "mh_A_old"

Write-Output ""
Write-Output "===== NHANH B (sau khi sua) ====="
$env:BIO_EPISODE_RECENCY_WEIGHT = "0.4"
$env:BIO_EPISODE_MIN_DENSE      = "0.15"
$env:BIO_EPISODE_DEDUP_JACCARD  = "0.8"
& ".\.venv\Scripts\python.exe" @common --tag "mh_B_new"

Write-Output ""
Write-Output "===== XONG CA HAI NHANH ====="
