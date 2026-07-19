## 后端 
cd /data/lichengyang/lrn/MUL_rag/mul_rag/backend && /home/lichengyang/anaconda3/envs/mul_rag/bin/uvicorn app:app --reload --host 0.0.0.0 --port 8002

## 前端 
python -m streamlit run /data/lichengyang/lrn/MUL_rag/mul_rag/streamlit_app.py

## olmocr 
CUDA_VISIBLE_DEVICES=3 vllm serve /data/lichengyang/lrn/MUL_rag/mul_rag/olmocr/olmOCR-2-7B-1025-FP8 --served-model-name olmocr --max-model-len 16384 --port 8002 --gpu-memory-utilization 0.5