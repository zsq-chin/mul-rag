import pandas as pd
import os
from typing import List, Tuple
from services.faiss_store import FaissStore
from services.index_service import load_embeddings, init_db, index_dir

def process_excel_file(file_path: str, kb_id: str, file_id: str):
    """
    读取 Excel/CSV，将每一行序列化为文本，并存入 FAISS。
    """
    print(f"Processing Excel file: {file_path}")
    
    # 1. 读取文件
    dfs = {} # sheet_name -> DataFrame
    try:
        if file_path.endswith('.csv'):
            dfs['Sheet1'] = pd.read_csv(file_path)
        else:
            # Supports .xlsx and .xls
            # sheet_name=None 读取所有工作表
            # header=None: 先不指定表头，手动寻找
            dfs = pd.read_excel(file_path, sheet_name=None, header=None)
    except Exception as e:
        print(f"Error reading file {file_path}: {e}")
        raise e
    
    metas: List[Tuple[str, str, str]] = []
    texts_to_embed: List[str] = []
    
    # 2. 遍历每个工作表
    for sheet_name, df in dfs.items():
        print(f"Processing sheet: {sheet_name}")
        
        # --- 智能表头检测 (Robust) ---
        if not df.empty:
            header_row_idx = 0
            
            # 策略0: 检查第一行是否为文件名 (常见于报表标题)
            filename_no_ext = os.path.splitext(os.path.basename(file_path))[0]
            # 将第一行转为字符串并检查是否包含文件名
            first_row_text = df.iloc[0].astype(str).str.cat(sep=' ')
            
            if filename_no_ext in first_row_text and len(df) > 1:
                print(f"Sheet '{sheet_name}': First row matches filename. Using row 1 as header.")
                header_row_idx = 1
            else:
                # 策略1: 扫描前 10 行寻找有效列最多的行
                max_valid_cols = 0
                scan_rows = min(10, len(df))
                for i in range(scan_rows):
                    row = df.iloc[i]
                    valid_count = row.count() # 非 NaN/None 的数量
                    
                    # 如果当前行有效列数显著多于之前的最大值，更新
                    if valid_count > max_valid_cols:
                        max_valid_cols = valid_count
                        header_row_idx = i
                print(f"Detected header at row {header_row_idx} with {max_valid_cols} valid columns")
            
            # 设置表头
            new_header = []
            row_vals = df.iloc[header_row_idx]
            for idx, val in enumerate(row_vals):
                if pd.isna(val) or str(val).strip() == '':
                    new_header.append(f"Column_{idx}")
                else:
                    # 修复：去除列名中的换行符，避免检索展示时被截断
                    clean_val = str(val).strip().replace('\n', ' ').replace('\r', '')
                    new_header.append(clean_val)
            
            df.columns = new_header
            # 截取数据 (从表头下一行开始)
            df = df.iloc[header_row_idx+1:].reset_index(drop=True)

        # 统一清理所有列名 (防止未触发检测逻辑时列名仍有换行)
        df.columns = [str(c).strip().replace('\n', ' ').replace('\r', '') for c in df.columns]

        # 处理空值，填充为空字符串
        df = df.fillna('')
        
        # 遍历每一行进行序列化
        columns = list(df.columns)
        MAX_COLS = 12

        for index, row in df.iterrows():
            # 策略：如果列数过多 (>12)，则分块处理，避免单个 chunk 过大导致检索失效
            if len(columns) > MAX_COLS:
                col_chunks = [columns[i:i + MAX_COLS] for i in range(0, len(columns), MAX_COLS)]
            else:
                col_chunks = [columns]

            for chunk_idx, col_chunk in enumerate(col_chunks):
                # 构建语义化的文本块
                # 格式: "工作表: Sheet1\n列名1: 值1\n列名2: 值2..."
                row_text_parts = [f"工作表: {sheet_name}"]
                
                for col in col_chunk:
                    val = str(row[col]).strip()
                    if val: # 只保留非空值
                        row_text_parts.append(f"{col}: {val}")
                
                # 添加元数据上下文
                row_text_parts.append(f"来源文件: {os.path.basename(file_path)}")
                row_text_parts.append(f"行号: {index + 1}")
                
                chunk_text = "\n".join(row_text_parts)
                texts_to_embed.append(chunk_text)
                
                # 准备元数据 (entity_key, source, chunk_text)
                part_suffix = f" (Part {chunk_idx + 1})" if len(col_chunks) > 1 else ""
                source_meta = f"Sheet '{sheet_name}', Row {index + 1}{part_suffix} from {os.path.basename(file_path)}"
                metas.append((file_id, source_meta, chunk_text))

    if not texts_to_embed:
        print("No data found in Excel file.")
        return

    # 3. 初始化 DB 和 Store（Per-KB）
    conn = init_db(kb_id)
    idx_dir = index_dir(kb_id)
    # 修正：保持与 index_service.py 一致的索引文件名
    index_path = str(idx_dir / "index.faiss")
    store = FaissStore(index_path=index_path, conn=conn)
    store.load() # 加载现有索引

    # 4. 加载 Embedding 模型
    print("Loading embedding model...")
    embed_model = load_embeddings(kb_id)

    # 5. 批量计算向量并存储
    print(f"Embedding {len(texts_to_embed)} rows from Excel...")
    batch_size = 32
    for i in range(0, len(texts_to_embed), batch_size):
        batch_texts = texts_to_embed[i : i + batch_size]
        batch_metas = metas[i : i + batch_size]
        
        try:
            batch_vectors = embed_model.embed_documents(batch_texts)
            store.add_embeddings(batch_vectors, batch_metas)
            print(f"Processed batch {i} - {i + len(batch_texts)}")
        except Exception as e:
            print(f"Error embedding batch {i}: {e}")

    print(f"Successfully indexed {len(texts_to_embed)} rows.")
    conn.close()
    return len(texts_to_embed)
