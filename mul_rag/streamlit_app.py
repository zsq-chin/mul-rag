import streamlit as st
import requests
import json
import time
import os
import re
import pandas as pd
import urllib.parse
import base64

# 设置页面配置
st.set_page_config(
    page_title="多模态 RAG 系统 (Python版)",
    page_icon="🤖",
    layout="wide"
)

# --- 自定义 CSS 样式 ---
st.markdown("""
<style>
    /* 全局字体优化 */
    html, body, [class*="css"] {
        font-family: 'PingFang SC', 'Helvetica Neue', Helvetica, 'Microsoft YaHei', Arial, sans-serif;
    }
    
    /* Level 1 Tabs (Top-level tabs: Multimodal Search, KB Preview) */
    .stTabs [data-baseweb="tab-list"] button [data-testid="stMarkdownContainer"] p {
        font-size: 1.8rem;
        font-weight: 700;
        padding-top: 5px;
        padding-bottom: 5px;
    }

    /* Level 2 Tabs (Nested tabs: File Mgmt, Image Mgmt, Numerical Mgmt) */
    /* Target stTabs that are inside a tab-panel (which implies nesting) */
    [data-baseweb="tab-panel"] .stTabs [data-baseweb="tab-list"] button [data-testid="stMarkdownContainer"] p {
        font-size: 1.2rem;
        font-weight: 600;
    }
    
    /* 选中 Tab 的下划线颜色 */
    .stTabs [data-baseweb="tab-highlight"] {
        background-color: #FF4B4B;
    }

    /* 侧边栏/Expander 标题字体 */
    .streamlit-expanderHeader {
        font-size: 1.1rem !important;
        font-weight: 500;
        color: #31333F;
    }
    
    /* 按钮样式微调 */
    .stButton button {
        border-radius: 8px;
        font-weight: 500;
        transition: all 0.2s;
        justify-content: flex-start !important; /* 文左对齐 */
        padding-left: 10px !important;
    }
    .stButton button:hover {
        border-color: #FF4B4B;
        color: #FF4B4B;
    }

    /* 列表头加粗 */
    .css-164nlkn {
        font-weight: bold;
        font-size: 1.05rem;
    }

    /* 数据表格标题 */
    h3 {
        font-size: 1.5rem !important;
        padding-bottom: 10px;
        border-bottom: 2px solid #f0f2f6;
        margin-bottom: 20px;
    }
    
    /* 分隔线间距 */
    hr {
        margin-top: 1.5rem;
        margin-bottom: 1.5rem;
    }
    
    /* Toast 样式 */
    .stToast {
        background-color: #ffffff;
        border: 1px solid #f0f2f6;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    }
</style>
""", unsafe_allow_html=True)

# 初始化 Session State
if "messages" not in st.session_state:
    st.session_state.messages = []
if "kb_id" not in st.session_state:
    st.session_state.kb_id = None
if "file_id" not in st.session_state:
    st.session_state.file_id = None
if "session_id" not in st.session_state:
    st.session_state.session_id = f"sess_{int(time.time())}"
if "parsing_status" not in st.session_state:
    st.session_state.parsing_status = "idle"

# Sidebar 配置
# (Sidebar 已移除，功能移至 Tab 5)
api_base = "http://localhost:8002/api/v1"

# --- 辅助函数：新建知识库处理 ---
def handle_create_kb(key_suffix, existing_kbs, api_base):
    with st.expander("➕ 新建知识库", expanded=True): 
        with st.form(f"create_kb_form_{key_suffix}"):
            col_c1, col_c2, col_c3 = st.columns([2, 1, 1])
            with col_c1:
                new_kb_name = st.text_input("知识库名称 (例如: 公司财报2023)", placeholder="请输入名称...", key=f"kb_name_{key_suffix}")
            with col_c2:
                vector_store_type = st.selectbox(
                    "向量库类型", 
                    options=["faiss", "milvus", "es"], 
                    index=0,
                    help="faiss: 本地索引; milvus/es: 后端需配置连接。",
                    key=f"vs_type_{key_suffix}"
                )
            with col_c3:
                embed_model = st.text_input("向量模型", value="bge-m3:latest", key=f"emb_model_{key_suffix}")
            
            submitted = st.form_submit_button("立即创建")
            if submitted:
                name_to_create = new_kb_name.strip()
                if not name_to_create:
                    st.error("请输入知识库名称")
                    return None
                elif any(k.get('kbName') == name_to_create or k.get('kbId') == name_to_create for k in existing_kbs):
                    st.error(f"知识库名称 '{name_to_create}' 已存在，请使用其他名称。")
                    return None
                else:
                    try:
                        # 构造创建请求
                        payload = {
                            "kbName": name_to_create, 
                            "embedModel": embed_model, 
                            "vectorStoreType": vector_store_type
                        }
                        r = requests.post(f"{api_base}/kb/create", json=payload)
                        
                        if r.status_code == 200:
                            st.success(f"知识库 '{name_to_create}' 创建成功！")
                            time.sleep(1)
                            st.rerun()
                            return r.json().get("kbId")
                        else:
                            st.error(f"创建失败: {r.text}")
                            return None
                    except Exception as e:
                        st.error(f"连接失败: {e}")
                        return None
    return None

# 主界面 Tabs

tab4, tab5, tab6 = st.tabs(["🔍 多模态搜索", "📚 知识库预览", "⛏️ 知识提取"])
#tab1, tab2, tab3, tab4, tab5 = st.tabs(["💬 对话", "📄 文档查看", "🖼️ 提取图片", "🔍 调试搜索", "📚 知识库预览"])
# --- Tab 1: 对话 ---
# with tab1:
#     st.header("多模态 RAG 对话 (全局知识库)")
#     st.caption("当前对话将检索知识库中所有已索引的文件。")
    
#     # 辅助函数：显示引用
#     def render_citations(citations, api_base_url):
#         if not citations:
#             return
#         with st.expander("📚 参考引用 (点击查看原文)"):
#             for c in citations:
#                 page = c.get("page")
#                 score = c.get("score")
#                 text = c.get("snippet", "")
#                 preview_url = c.get("previewUrl", "")
                
#                 # 构建完整 URL
#                 full_url = preview_url
#                 if preview_url.startswith("/"):
#                     # 尝试从 api_base 提取 host
#                     # api_base 如 http://localhost:8000/api/v1
#                     if "/api/v1" in api_base_url:
#                         host = api_base_url.split("/api/v1")[0]
#                         full_url = host + preview_url
#                     else:
#                         # 简单拼接
#                         full_url = api_base_url.rstrip("/") + preview_url

#                 st.markdown(f"**📄 Page {page}** (相似度: {score:.2f})")
#                 st.markdown(f"🔗 [点击查看原文页面]({full_url})")
#                 st.caption(text[:200] + "..." if len(text) > 200 else text)
#                 st.divider()

#     # 显示历史消息
#     for msg in st.session_state.messages:
#         with st.chat_message(msg["role"]):
#             st.markdown(msg["content"])
#             if "citations" in msg and msg["citations"]:
#                 render_citations(msg["citations"], api_base)

#     # 输入框
#     if prompt := st.chat_input("请输入关于文档的问题..."):
#         # 添加用户消息
#         st.session_state.messages.append({"role": "user", "content": prompt})
#         with st.chat_message("user"):
#             st.markdown(prompt)

#         # 请求后端流式响应
#         with st.chat_message("assistant"):
#             message_placeholder = st.empty()
#             full_response = ""
#             citations = []
            
#             try:
#                 payload = {
#                     "message": prompt,
#                     "sessionId": st.session_state.session_id,
#                     "pdfFileId": st.session_state.file_id
#                 }
                
#                 with requests.post(f"{api_base}/chat", json=payload, stream=True) as r:
#                     if r.status_code == 200:
#                         for line in r.iter_lines():
#                             if line:
#                                 decoded_line = line.decode('utf-8')
#                                 if decoded_line.startswith("event:"):
#                                     event_type = decoded_line.split(":", 1)[1].strip()
#                                 elif decoded_line.startswith("data:"):
#                                     data_str = decoded_line.split(":", 1)[1].strip()
#                                     try:
#                                         data_json = json.loads(data_str)
                                        
#                                         if event_type == "token":
#                                             token_text = data_json.get("text", "")
#                                             full_response += token_text
                                            
#                                             # 实时替换图片路径为完整的 API URL
#                                             # 使用正则进行更稳健的替换
#                                             if "/api/v1" in api_base:
#                                                 api_root = api_base.split("/api/v1")[0]
#                                             else:
#                                                 api_root = api_base.rstrip("/")
                                            
#                                             display_response = re.sub(r'\]\(\s*/api/v1/', f']({api_root}/api/v1/', full_response)
#                                             display_response = re.sub(r'src="\s*/api/v1/', f'src="{api_root}/api/v1/', display_response)
                                            
#                                             message_placeholder.markdown(display_response + "▌")
                                            
#                                         elif event_type == "citation":
#                                             citations.append(data_json)
                                            
#                                         elif event_type == "error":
#                                             st.error(f"后端错误: {data_json.get('message')}")
#                                     except json.JSONDecodeError:
#                                         st.error("无法解析后端数据")
#                         # 最终显示也需要替换
#                         if "/api/v1" in api_base:
#                             api_root = api_base.split("/api/v1")[0]
#                         else:
#                             api_root = api_base.rstrip("/")
                            
#                         final_display_response = re.sub(r'\]\(\s*/api/v1/', f']({api_root}/api/v1/', full_response)
#                         final_display_response = re.sub(r'src="\s*/api/v1/', f'src="{api_root}/api/v1/', final_display_response)
                        
#                         # 最终显示也需要替换
#                         if "/api/v1" in api_base:
#                             api_root = api_base.split("/api/v1")[0]
#                         else:
#                             api_root = api_base.rstrip("/")
                            
#                         final_display_response = re.sub(r'\]\(\s*/api/v1/', f']({api_root}/api/v1/', full_response)
#                         final_display_response = re.sub(r'src="\s*/api/v1/', f'src="{api_root}/api/v1/', final_display_response)
                        
#                         message_placeholder.markdown(final_display_response)
#                         # 保存助手回复
#                         st.session_state.messages.append({
#                             "role": "assistant", 
#                             "content": final_display_response, # 保存替换后的内容，以便历史记录正确显示
#                             "citations": citations
#                         })
                        
#                         if citations:
#                             render_citations(citations, api_base)
#                     else:
#                         st.error(f"API 请求失败: {r.status_code}")
#             except Exception as e:
#                 st.error(f"发生异常: {e}")

# # --- Tab 2: 文档查看 ---
# with tab2:
#     st.header("文档可视化")
#     if st.session_state.file_id:
#         col1, col2 = st.columns([1, 3])
#         with col1:
#             page_num = st.number_input("页码", min_value=1, value=1)
        
#         col_orig, col_parsed = st.columns(2)
        
#         with col_orig:
#             st.subheader("原始页面")
#             orig_url = f"{api_base}/pdf/page?fileId={st.session_state.file_id}&page={page_num}&type=original"
#             # 使用 st.image 直接加载 URL (如果后端支持跨域且可访问)
#             # 或者下载后显示
#             try:
#                 st.image(orig_url, use_container_width=True)
#             except:
#                 st.warning("无法加载原始页面")

#         with col_parsed:
#             st.subheader("解析后 (带框)")
#             if st.session_state.parsing_status == "ready":
#                 parsed_url = f"{api_base}/pdf/page?fileId={st.session_state.file_id}&page={page_num}&type=parsed"
#                 try:
#                     st.image(parsed_url, use_container_width=True)
#                 except:
#                     st.warning("无法加载解析页面")
#             else:
#                 st.info("文档尚未解析完成")
#     else:
#         st.info("请先上传文件")

# # --- Tab 3: 提取图片 ---
# with tab3:
#     st.header("提取的图片与表格")
#     if st.session_state.file_id and st.session_state.parsing_status == "ready":
#         if st.button("加载图片列表"):
#             try:
#                 resp = requests.get(f"{api_base}/pdf/images_list", params={"fileId": st.session_state.file_id})
#                 if resp.status_code == 200:
#                     images = resp.json().get("images", [])
#                     if images:
#                         st.success(f"共找到 {len(images)} 张图片/表格截图")
                        
#                         # 使用网格布局显示图片
#                         cols = st.columns(3)
#                         for idx, img_name in enumerate(images):
#                             img_url = f"{api_base}/pdf/images?fileId={st.session_state.file_id}&imagePath={img_name}"
#                             with cols[idx % 3]:
#                                 st.image(img_url, caption=img_name, use_container_width=True)
#                     else:
#                         st.info("未提取到任何图片或表格。")
#                 else:
#                     st.error("获取图片列表失败")
#             except Exception as e:
#                 st.error(f"错误: {e}")
#     elif not st.session_state.file_id:
#         st.info("请先上传文件")
#     else:
#         st.info("请等待解析完成")

# --- Tab 4: 调试搜索 ---
with tab4:
    st.header("向量索引搜索")
    
    # 0. 获取知识库列表
    all_kbs_search = []
    try:
        r_list = requests.get(f"{api_base}/kb/list", timeout=3)
        if r_list.status_code == 200:
            all_kbs_search = r_list.json().get("kbs", [])
    except:
        pass

    if not all_kbs_search:
        st.info("暂无知识库，请先去创建。")
        st.stop()

    # 1. 选择知识库
    kb_map = {kb['kbId']: kb.get('kbName', kb['kbId']) for kb in all_kbs_search}
    kb_ids = list(kb_map.keys())
    
    current_idx_search = 0
    if st.session_state.kb_id in kb_ids:
        current_idx_search = kb_ids.index(st.session_state.kb_id)
        
    selected_kb_search = st.selectbox(
        "选择检索知识库", 
        kb_ids, 
        format_func=lambda x: kb_map[x],
        index=current_idx_search,
        key="search_tab_kb_select"
    )
    
    if selected_kb_search != st.session_state.kb_id:
        st.session_state.kb_id = selected_kb_search
        st.session_state.current_kb_name = kb_map[selected_kb_search]
        st.session_state.file_id = None
        st.rerun()
        
    st.divider()

    # 2. 搜索输入
    query = st.text_input("搜索关键词")
    k = st.slider("Top K", 1, 10, 3)

    # 获取当前 KB 文件列表用于下拉选择
    kb_files_search = []
    try:
        r = requests.get(f"{api_base}/kb/files", params={"kbId": st.session_state.kb_id}, timeout=10)
        if r.status_code == 200:
            kb_files_search = r.json().get("files", [])
    except Exception:
        kb_files_search = []

    file_options = ["当前知识库 (All files)"] + [f["fileId"] for f in kb_files_search]

    default_idx = 0
    if st.session_state.file_id and st.session_state.file_id in file_options:
        default_idx = file_options.index(st.session_state.file_id)

    selected_option = st.selectbox("选择搜索范围", file_options, index=default_idx)
    
    if st.button("搜索"):
        try:
            payload = {
                "query": query,
                "k": k,
                "kbId": st.session_state.kb_id,
            }

            if selected_option != "当前知识库 (All files)":
                payload["fileId"] = selected_option
            
            resp = requests.post(f"{api_base}/index/search", json=payload)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if not results:
                    st.info("未找到相关结果")
                else:
                    for i, res in enumerate(results):
                        score = res.get("score", 0)
                        res_file_id = res.get("entity_key", "Unknown")
                        chunk_text = res.get("chunk_text", "")
                        source_meta = res.get("source", "")
                        
                        # 动态替换图片链接，使其指向后端 API
                        def replace_img_url(match):
                            rel_path = match.group(1) # e.g. ./images/page26_img1.png
                            img_name = os.path.basename(rel_path)
                            # 对图片名进行 URL 编码，处理中文和空格
                            img_name_encoded = urllib.parse.quote(img_name)
                            base = api_base.rstrip('/')
                            return f"![Image]({base}/pdf/images?kbId={st.session_state.kb_id}&fileId={res_file_id}&imagePath={img_name_encoded})"
                        
                        # 替换 Markdown 图片语法
                        chunk_text_display = re.sub(r'!\[.*?\]\((.*?)\)', replace_img_url, chunk_text)
                        
                        with st.expander(f"Result {i+1} | Score: {score:.4f} | File: {res_file_id}", expanded=True):
                            # 尝试检测是否为结构化数据 (Excel/CSV 行)
                            # 特征：包含 "工作表:" 和 "行号:"
                            if "工作表:" in chunk_text and "行号:" in chunk_text:
                                try:
                                    # 解析 Key-Value
                                    data_dict = {}
                                    for line in chunk_text.split('\n'):
                                        if ": " in line:
                                            k, v = line.split(": ", 1)
                                            data_dict[k] = v
                                    
                                    # 展示为表格
                                    if data_dict:
                                        st.markdown("#### 📊 结构化数据匹配")
                                        # 转置显示，适合单行数据查看
                                        df_row = pd.DataFrame([data_dict]).T
                                        df_row.columns = ["值"]
                                        st.dataframe(df_row, use_container_width=True)
                                    else:
                                        st.markdown(chunk_text_display)
                                except:
                                    st.markdown(chunk_text_display)
                            else:
                                st.markdown(chunk_text_display)

                            st.divider()
                            st.caption(f"Source Metadata: {source_meta}")
            else:
                st.error(f"搜索失败: {resp.text}")
        except Exception as e:
            st.error(f"错误: {e}")


# --- Tab 5: 知识库预览 & 管理 ---
with tab5:
    if "current_kb_view" not in st.session_state:
        st.session_state.current_kb_view = None

    # === VIEW 1: 知识库概览列表 ===
    if st.session_state.current_kb_view is None:
        st.markdown("### 📚 知识库管理")
        st.caption("选择一个知识库进入管理，或创建新的知识库。")

        # 0. 预先获取现有知识库列表 (用于重名校验和展示)
        existing_kbs = []
        fetch_error = None
        try:
            r_list = requests.get(f"{api_base}/kb/list", timeout=5)
            if r_list.status_code == 200:
                existing_kbs = r_list.json().get("kbs", [])
            else:
                fetch_error = f"获取列表失败: {r_list.text}"
        except Exception as e:
            fetch_error = f"无法连接后端服务: {e}"

        # 1. 新建知识库 (使用辅助函数)
        handle_create_kb("tab5_overview", existing_kbs, api_base)
        
        st.divider()

        # 2. 知识库列表卡片
        if st.button("🔄 刷新列表", key="refresh_kb_list"):
            st.rerun()

        if fetch_error:
            st.error(fetch_error)
        else:
            kbs = existing_kbs
            if not kbs:
                st.info("暂无知识库，请先新建。")
            else:
                # 分列显示卡片
                cols = st.columns(3) # 每行3个
                for i, kb in enumerate(kbs):
                    with cols[i % 3]:
                        with st.container(border=True):
                            # 标题行 + 删除按钮
                            col_title, col_del = st.columns([0.8, 0.2])
                            with col_title:
                                st.subheader(f"📂 {kb.get('kbName')}")
                            with col_del:
                                # 垃圾桶按钮
                                if st.button("🗑️", key=f"pre_del_{kb['kbId']}", help="删除此知识库"):
                                    st.session_state["kb_confirm_delete"] = kb['kbId']
                                    st.rerun()
                            
                            # 删除确认逻辑
                            if st.session_state.get("kb_confirm_delete") == kb['kbId']:
                                st.warning("永久删除此知识库？此操作不可恢复。")
                                col_y, col_n = st.columns(2)
                                with col_y:
                                    if st.button("✅ 确认", key=f"yes_del_{kb['kbId']}", use_container_width=True):
                                        try:
                                            r = requests.post(
                                                f"{api_base}/kb/delete", 
                                                json={"kbId": kb['kbId']},
                                                timeout=10
                                            )
                                            if r.status_code == 200:
                                                st.success("删除成功")
                                                st.session_state["kb_confirm_delete"] = None
                                                time.sleep(1)
                                                st.rerun()
                                            else:
                                                st.error(f"失败: {r.text}")
                                        except Exception as ex:
                                            st.error(f"错误: {ex}")
                                with col_n:
                                    if st.button("❌ 取消", key=f"no_del_{kb['kbId']}", use_container_width=True):
                                        st.session_state["kb_confirm_delete"] = None
                                        st.rerun()
                            else:
                                # 正常显示信息
                                st.caption(f"ID: {kb.get('kbId')}")
                                
                                c1, c2 = st.columns(2)
                                c1.metric("文件数", kb.get('fileCount', 0))
                                c2.metric("类型", kb.get('vectorStoreType', 'faiss'))

                                if st.button("进入知识库 ➡️", key=f"enter_{kb['kbId']}", use_container_width=True):
                                    st.session_state.current_kb_view = kb['kbId']
                                    st.session_state.current_kb_name = kb['kbName']
                                    st.session_state.kb_id = kb['kbId'] # 同步到全局
                                    st.rerun()

    # === VIEW 2: 单个知识库详情 (原 Tab 5 功能) ===
    else:
        current_kb = st.session_state.current_kb_view
        
        # 0. 准备通用数据: 根据 current_kb 实时获取 KB信息 (名称, embedding model等)
        # 避免 session_state 中名称不同步的问题
        kb_embed_model = "bge-m3 (Default)"
        display_name = st.session_state.get("current_kb_name", current_kb)
        
        try:
            r_info = requests.get(f"{api_base}/kb/list", timeout=3)
            if r_info.status_code == 200:
                for k in r_info.json().get("kbs", []):
                    if k['kbId'] == current_kb:
                        kb_embed_model = k.get("embedModel", "Unknown")
                        display_name = k.get("kbName", display_name) # 强制更新 name
                        st.session_state.current_kb_name = display_name
                        break
        except: 
            pass
        
        # 头部导航
        col_nav1, col_nav2 = st.columns([1, 6])
        with col_nav1:
            if st.button("⬅️ 返回", use_container_width=True):
                st.session_state.current_kb_view = None
                st.rerun()
        with col_nav2:
             st.markdown(f"### 知识库: `{display_name}`")

        st.divider()

        # === 上传区域 ===
        with st.expander("📤 上传文件到当前知识库", expanded=True):
            uploaded_files = st.file_uploader(
                "选择多个文件 (支持 PDF, Excel, CSV)", 
                type=["pdf", "xlsx", "xls", "csv"],
                accept_multiple_files=True,
                key="uploader_tab5"
            )
            
            # 解析选项
            col_u1, col_u2 = st.columns([1, 1])
            with col_u1:
                parse_method_choice = st.radio(
                    "解析方式", 
                    ["original", "olmocr"], 
                    format_func=lambda x: {"original": "基础解析 (快, 传统OCR)", "olmocr": "增强解析 (慢, 多模态大模型)"}[x],
                    horizontal=True,
                    key="parse_method_tab5"
                )

            if uploaded_files:
                # 0. 预先获取当前知识库的已有文件列表，用于查重
                existing_filenames = set()
                try:
                    if current_kb:
                        r_files = requests.get(f"{api_base}/kb/files", params={"kbId": current_kb}, timeout=5)
                        if r_files.status_code == 200:
                            f_list = r_files.json().get("files", [])
                            existing_filenames = {str(f.get("fileName")).strip().lower() for f in f_list if f.get("fileName")}
                        else:
                            st.warning(f"无法获取文件列表 (Code: {r_files.status_code})，查重功能可能受限")
                    else:
                        st.error("未检测到当前知识库ID")
                except Exception as e:
                    st.warning(f"查重请求失败: {e}")
                    pass

                # 状态存储初始化
                if "batch_status" not in st.session_state:
                    st.session_state.batch_status = {}  # key: filename_size, val: {fid: x, status: x}
                
                # 1. 自动上传队列
                st.caption(f"正在准备处理 {len(uploaded_files)} 个文件...")
                
                files_to_process = []
                
                # 进度容器
                upload_progress = st.empty()
                
                for i, up_file in enumerate(uploaded_files):
                    f_key = f"{up_file.name}_{up_file.size}"
                    
                    # 检查是否已上传
                    if f_key not in st.session_state.batch_status:
                        
                        # 查重逻辑
                        if up_file.name.strip().lower() in existing_filenames:
                            st.session_state.batch_status[f_key] = {"fid": None, "status": "duplicate"}
                            st.warning(f"⚠️ 跳过上传: `{up_file.name}` (知识库中已存在同名文件)")
                            continue

                        # 显示上传进度
                        # 计算整体进度
                        prog_val = (i + 1) / len(uploaded_files)
                        upload_progress.progress(prog_val, text=f"正在上传 ({i+1}/{len(uploaded_files)}): {up_file.name}")
                        
                        try:
                            # 指针归零以防万一
                            up_file.seek(0)
                            
                            mime_type = "application/octet-stream"
                            if up_file.name.endswith(".pdf"): mime_type = "application/pdf"
                            elif up_file.name.endswith(".xlsx"): mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            elif up_file.name.endswith(".csv"): mime_type = "text/csv"
                            
                            files = {"file": (up_file.name, up_file, mime_type)}
                            resp = requests.post(
                                f"{api_base}/pdf/upload",
                                files=files,
                                data={"kbId": current_kb},
                                timeout=120,
                            )
                            if resp.status_code == 200:
                                data = resp.json()
                                f_id = data["fileId"]
                                st.session_state.batch_status[f_key] = {"fid": f_id, "status": "uploaded"}
                            else:
                                st.session_state.batch_status[f_key] = {"fid": None, "status": f"error: {resp.text}"}
                                st.error(f"Failed to upload {up_file.name}: {resp.text}")
                        except Exception as e:
                             st.session_state.batch_status[f_key] = {"fid": None, "status": f"error: {e}"}
                             st.error(f"Error uploading {up_file.name}: {e}")
                    
                    # 收集 ID
                    info = st.session_state.batch_status.get(f_key, {})
                    if info.get("fid"):
                        files_to_process.append({"name": up_file.name, "fid": info["fid"], "key": f_key})

                upload_progress.empty()
                
                if files_to_process:
                    st.success(f"✅ 已就绪 {len(files_to_process)} 个文件 (共 {len(uploaded_files)} 个)")
                    
                    # 2. 批量操作区
                    col_b1, col_b2 = st.columns(2)
                    uploaded_count = len(files_to_process)
                    
                    with col_b1:
                        if st.button(f"🚀 解析 ({uploaded_count})", use_container_width=True, type="primary"):
                            # 1. 提交任务
                            submit_bar = st.progress(0, text="正在分发解析任务...")
                            active_tasks = [] # list of dict: {fid, name, progress, status}
                            
                            for idx, item in enumerate(files_to_process):
                                try:
                                    requests.post(f"{api_base}/pdf/parse", json={
                                        "kbId": current_kb,
                                        "fileId": item["fid"],
                                        "method": parse_method_choice
                                    })
                                    active_tasks.append({
                                        "fid": item["fid"], 
                                        "name": item["name"], 
                                        "progress": 0, 
                                        "status": "pending"
                                    })
                                except: 
                                    pass
                                di_prog = (idx + 1) / uploaded_count
                                submit_bar.progress(di_prog, text=f"任务分发中 {idx+1}/{uploaded_count}")
                            
                            submit_bar.empty()
                            st.toast(f"开始处理 {len(active_tasks)} 个文件的解析监控", icon="🕵️")
                            
                            # 2. 轮询监控进度
                            status_container = st.container(border=True)
                            with status_container:
                                st.markdown("**📊 实时解析进度**")
                                table_placeholder = st.empty()
                                
                                # 最大轮询时间 (例如 5 分钟)
                                max_retries = 300 
                                for _ in range(max_retries):
                                    all_done = True
                                    
                                    # 遍历更新状态
                                    for task in active_tasks:
                                        # 如果已经完成或失败，跳过请求
                                        if task["status"] in ["ready", "error"]:
                                            continue
                                            
                                        all_done = False
                                        try:
                                            r = requests.get(f"{api_base}/pdf/status", params={"kbId": current_kb, "fileId": task["fid"]}, timeout=1)
                                            if r.status_code == 200:
                                                d = r.json()
                                                # 使用 0-100 的整数进度，避免小数格式化问题
                                                task["progress"] = d.get("progress", 0) 
                                                task["status"] = d.get("status", "unknown")
                                        except:
                                            pass
                                    
                                    # 构造 DataFrame 显示
                                    df_status = pd.DataFrame(active_tasks)
                                    # 只需要简单的几列
                                    if not df_status.empty:
                                        table_placeholder.dataframe(
                                            df_status[["name", "progress", "status"]],
                                            column_config={
                                                "name": st.column_config.TextColumn("文件名", width="medium"),
                                                "progress": st.column_config.ProgressColumn(
                                                    "当前解析进度", 
                                                    min_value=0, 
                                                    max_value=100, 
                                                    format="%d%%"
                                                ),
                                                "status": st.column_config.TextColumn("状态", width="small")
                                            },
                                            use_container_width=True,
                                            hide_index=True,
                                            # 使用 dataframe 不需要每次强制刷新 key，除非数据没变但想刷 (这里数据在变)
                                            # key=f"status_table_{time.time()}" 
                                        )
                                    
                                    if all_done:
                                        st.success("🎉 所有文件解析完成！")
                                        break
                                    
                                    time.sleep(1.5)
                                else:
                                    st.warning("⚠️ 监控超时，请稍后在文件列表中查看最终状态。")

                    with col_b2:
                        # 索引一般需要解析完成后进行，但这里允许用户批量触发
                        if st.button(f"🚋 索引 ({uploaded_count})", use_container_width=True, help="建议确认文件解析完成（Ready）后再点击"):
                            # 准备显示容器
                            idx_container = st.container(border=True)
                            with idx_container:
                                st.markdown("**🏗️ 索引构建队列**")
                                idx_prog_bar = st.progress(0, text="准备开始...")
                                idx_status_text = st.empty()
                                
                                success_cnt = 0
                                errors = []
                                
                                for idx, item in enumerate(files_to_process):
                                    fname = item["name"]
                                    idx_status_text.markdown(f"👉 正在处理: **{fname}** ({idx+1}/{uploaded_count})")
                                    
                                    try:
                                        requests.post(f"{api_base}/index/build", json={
                                            "kbId": current_kb,
                                            "fileId": item["fid"]
                                        })
                                        success_cnt += 1
                                    except Exception as e: 
                                        errors.append(f"{fname}: {e}")
                                    
                                    # 更新总进度
                                    idx_prog_bar.progress((idx + 1) / uploaded_count)
                                
                                idx_status_text.success(f"✅ 完成！成功 {success_cnt} 个，失败 {len(errors)} 个")
                                if errors:
                                    st.error("失败详情:\n" + "\n".join(errors))
                                
                            time.sleep(2)
                            st.rerun()
                    
                    # 简单列表展示
                    with st.expander(f"查看当前批次文件 ({len(files_to_process)})", expanded=False):
                        for f in files_to_process:
                             st.text(f"📄 {f['name']} (ID: {f['fid']})")

                else:
                    if uploaded_files:
                        # 如果只是因为同名文件跳过，则不显示报错
                        all_duplicates = True
                        for up_file in uploaded_files:
                            f_key_check = f"{up_file.name}_{up_file.size}"
                            status_check = st.session_state.batch_status.get(f_key_check, {}).get("status")
                            if status_check != "duplicate":
                                all_duplicates = False
                                break
                        
                        if not all_duplicates:
                            st.error("没有文件上传成功，请检查后端服务。")

        st.divider()

        # === 库管理 Tabs (文件 / 图片 / 数值) ===
        m_tab1, m_tab2, m_tab3 = st.tabs(["📑 文件库管理", "🖼️ 图像管理", "📊 数值管理"])
        
        # (KB信息已在顶部获取)

        # --- Tab 1: 文件管理 ---
        with m_tab1:
            kb_files = []
            try:
                r = requests.get(f"{api_base}/kb/files", params={"kbId": current_kb}, timeout=5)
                if r.status_code == 200:
                    kb_files = r.json().get("files", [])
            except Exception as e:
                st.error(f"无法获取文件列表: {e}")

            # 1. 批量操作区 / 列表
            if not kb_files:
                st.info("当前知识库为空，请先上传文件。")
            else:
                if "selected_files" not in st.session_state:
                    st.session_state.selected_files = set()

                # 批量操作配置
                c_cfg1, c_cfg2 = st.columns([2, 5])
                with c_cfg1:
                     reparse_choice = st.selectbox(
                        "重新解析使用的模型:",
                        options=["original", "olmocr"],
                        format_func=lambda x: {"original": "基础解析 (快, 传统OCR)", "olmocr": "增强解析 (慢, 多模态大模型)"}[x],
                        key="reparse_method_sel"
                    )

                # 工具栏
            col_tools_1, col_tools_2, col_tools_3, col_tools_4 = st.columns([1.5, 1.5, 1.5, 4])
            with col_tools_1:
                # 重新构建 (改为重新解析)
                if st.button("♻️ 重新解析", help="使用上方选择的解析模型，对选中的文件重新运行解析"):
                    if not st.session_state.selected_files:
                        st.warning("请先勾选文件")
                    else:
                        st.toast("正在启动重新解析任务...", icon="⏳")
                        
                        # 1. 准备任务列表
                        active_tasks = []
                        fid_name_map = {f['fileId']: f.get('fileName', f['fileId']) for f in kb_files}
                        
                        submit_bar = st.progress(0, text="正在分发任务...")
                        total_sel = len(st.session_state.selected_files)
                        
                        cnt = 0
                        for idx, fid in enumerate(st.session_state.selected_files):
                            fname = fid_name_map.get(fid, fid)
                            try:
                                requests.post(f"{api_base}/pdf/parse", json={"kbId": current_kb, "fileId": fid, "method": reparse_choice})
                                active_tasks.append({
                                    "fid": fid, 
                                    "name": fname, 
                                    "progress": 0, 
                                    "status": "pending"
                                })
                                cnt += 1
                            except Exception as e:
                                active_tasks.append({
                                    "fid": fid, 
                                    "name": fname, 
                                    "progress": 0, 
                                    "status": "error"
                                })
                            
                            submit_bar.progress((idx + 1) / total_sel)
                        
                        submit_bar.empty()
                        
                        if cnt > 0:
                            # 2. 进度监控
                            st.info(f"已提交 {cnt} 个解析任务，正在监控执行进度...")
                            status_container = st.container(border=True)
                            with status_container:
                                st.markdown("**📊 重新解析进度**")
                                table_placeholder = st.empty()
                                
                                max_retries = 300 
                                for _ in range(max_retries):
                                    all_done = True
                                    for task in active_tasks:
                                        if task["status"] in ["ready", "error"]:
                                            continue
                                            
                                        all_done = False
                                        try:
                                            r = requests.get(f"{api_base}/pdf/status", params={"kbId": current_kb, "fileId": task["fid"]}, timeout=1)
                                            if r.status_code == 200:
                                                d = r.json()
                                                task["progress"] = d.get("progress", 0) 
                                                task["status"] = d.get("status", "unknown")
                                        except:
                                            pass
                                    
                                    # 渲染表格
                                    df_status = pd.DataFrame(active_tasks)
                                    if not df_status.empty:
                                        table_placeholder.dataframe(
                                            df_status[["name", "progress", "status"]],
                                            column_config={
                                                "name": st.column_config.TextColumn("文件名", width="medium"),
                                                "progress": st.column_config.ProgressColumn(
                                                    "当前解析进度", 
                                                    min_value=0, 
                                                    max_value=100, 
                                                    format="%d%%"
                                                ),
                                                "status": st.column_config.TextColumn("状态", width="small")
                                            },
                                            use_container_width=True,
                                            hide_index=True
                                        )
                                    
                                    if all_done:
                                        st.success("🎉 所有选中文件解析完成！")
                                        break
                                    
                                    time.sleep(1.5)
                                else:
                                    st.warning("⚠️ 监控超时，请查看最终文件状态。")
                            
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("任务未能成功提交，请检查后端状态。")

            with col_tools_2:
                 # 重建索引
                if st.button("🏗️ 重建索引", help="基于现有的解析结果重新构建向量索引"):
                    if not st.session_state.selected_files:
                        st.warning("请先勾选文件")
                    else:
                        cnt = 0
                        errors = []
                        st.toast("正在提交索引构建任务...", icon="⏳")
                        
                        my_bar = st.progress(0, text="正在请求构建...")
                        total = len(st.session_state.selected_files)
                        
                        for i, fid in enumerate(st.session_state.selected_files):
                            try:
                                r = requests.post(f"{api_base}/index/build", json={"kbId": current_kb, "fileId": fid})
                                if r.status_code != 200:
                                    errors.append(f"{fid}: {r.text}")
                                cnt += 1
                            except Exception as e:
                                errors.append(f"{fid}: {e}")
                            my_bar.progress((i + 1) / total)
                            
                        if errors:
                            st.error(f"完成 {cnt} 个，失败 {len(errors)} 个:\n" + "\n".join(errors))
                        else:
                            st.success(f"成功为 {cnt} 个文件重建索引")
                            time.sleep(1)
                            st.rerun()

            with col_tools_3:
                # 删除文件
                if st.button("🗑️ 删除文件", help="物理删除文件及其索引"):
                     if not st.session_state.selected_files:
                        st.warning("请先勾选文件")
                     else:
                        errors = []
                        for fid in st.session_state.selected_files:
                            try:
                                resp = requests.post(
                                    f"{api_base}/kb/file/delete", 
                                    json={"kbId": current_kb, "fileId": fid, "deleteFile": True, "deleteIndex": True}
                                )
                                if resp.status_code != 200:
                                    errors.append(f"{fid} 删除失败: {resp.text}")
                            except Exception as ex:
                                errors.append(f"{fid} 请求错误: {ex}")
                        
                        if errors:
                            st.error("\n".join(errors))
                        else:
                            st.session_state.selected_files = set()
                            st.success("文件已删除")
                            time.sleep(1)
                            st.rerun()

            with col_tools_4:
                # 删除索引
                if st.button("🧹 删除索引", help="仅清除向量索引，保留原文件"):
                     if not st.session_state.selected_files:
                        st.warning("请先勾选文件")
                     else:
                        errors = []
                        for fid in st.session_state.selected_files:
                            try:
                                resp = requests.post(
                                    f"{api_base}/kb/file/delete", 
                                    json={"kbId": current_kb, "fileId": fid, "deleteFile": False, "deleteIndex": True}
                                )
                                if resp.status_code != 200:
                                    errors.append(f"{fid} 索引清除失败: {resp.text}")
                            except Exception as ex:
                                errors.append(f"{fid} 请求错误: {ex}")
                        
                        if errors:
                            st.error("\n".join(errors))
                        else:
                           st.success("索引已清除")
                           time.sleep(1)
                           st.rerun()

           

            st.markdown("---")

            # 2. 文件列表头
            # 布局: [Check 0.5] [Filename 2.5] [Type 0.8] [ParseModel 1.5] [EmbedModel 1.5] [Status 1.5]
            h_c1, h_c2, h_c3, h_c4, h_c5, h_c6 = st.columns([0.5, 2.5, 0.8, 1.5, 1.5, 1.5])
            
            # === 全选逻辑 ===
            all_view_fids = [f.get('fileId') for f in kb_files]
            
            def on_change_select_all():
                is_select = st.session_state.select_all_files_key
                if is_select:
                    st.session_state.selected_files.update(all_view_fids)
                else:
                    st.session_state.selected_files.difference_update(all_view_fids)
                
                # 手动同步每个子 Checkbox 的状态，确保前端立刻刷新
                for fid in all_view_fids:
                    st.session_state[f"chk_{fid}"] = is_select

            # 计算当前是否全选以同步UI状态
            is_all_now = bool(all_view_fids) and all(fid in st.session_state.selected_files for fid in all_view_fids)
            st.session_state.select_all_files_key = is_all_now
            
            # 使用 checkbox 替代原来的 "**选择**" 文本
            # 空 label 避免占用额外高度，key 用于 callback
            h_c1.checkbox("全选", key="select_all_files_key", on_change=on_change_select_all)
            
            h_c2.markdown("<div style='text-align: left; font-weight: bold;'>文件名 (点击预览)</div>", unsafe_allow_html=True)
            h_c3.markdown("**类型**")
            h_c4.markdown("**分词/解析模型**")
            h_c5.markdown("**向量化模型**")
            h_c6.markdown("**状态**")

            # 定义预览弹窗
            def show_preview_dialog_content(fid, fname, is_excel, page_count):
                st.caption(f"ID: {fid}")
                
                if is_excel:
                    # Excel/CSV 视图
                    preview_tabs = st.tabs(["📊 表格数据"])
                    
                    # Tab 1: DataFrame
                    with preview_tabs[0]:
                        with st.spinner("正在加载表格数据..."):
                            try:
                                r_df = requests.get(f"{api_base}/kb/file/dataframe", params={"kbId": current_kb, "fileId": fid})
                                if r_df.status_code == 200:
                                    sheets = r_df.json().get("sheets", {})
                                    if not sheets:
                                        st.warning("未识别到数据")
                                    else:
                                        # 如果有多个 sheet，用 tab 或 selectbox 切换
                                        sheet_names = list(sheets.keys())
                                        if len(sheet_names) > 1:
                                            selected_sheet = st.selectbox("选择工作表", sheet_names, key=f"sheet_sel_{fid}")
                                        else:
                                            selected_sheet = sheet_names[0]
                                        
                                        records = sheets[selected_sheet]
                                        df_preview = pd.DataFrame(records)
                                        st.dataframe(df_preview, use_container_width=True, height=500)
                                else:
                                    st.error(f"加载失败: {r_df.text}")
                            except Exception as e:
                                st.error(f"Error: {e}")


                else:
                    # PDF / 其它视图
                    preview_tabs = st.tabs(["📝 解析内容 (Markdown)", "🖼️ 原始页面"])
                    
                    # Tab 1: Content
                    with preview_tabs[0]:
                        try:
                            r_c = requests.get(f"{api_base}/kb/file/content", params={"kbId": current_kb, "fileId": fid})
                            if r_c.status_code == 200:
                                c = r_c.json().get("content", "")
                                st.text_area("Markdown Content", c, height=600)
                            else:
                                st.info("暂无 Markdown 内容 (请先解析)")
                        except Exception as e:
                            st.error(str(e))
                            
                    # Tab 2: Pages
                    with preview_tabs[1]:
                        if page_count > 0:
                            # 顶部控制栏
                            col_ctrl, _ = st.columns([2, 2])
                            with col_ctrl:
                                mode = st.radio("显示模式", ["original", "parsed"], horizontal=True, 
                                                format_func=lambda x: "原始页面 (PDF)" if x=="original" else "解析预览 (检测框)",
                                                key=f"md_dlg_{fid}")
                            
                            st.divider()
                            
                            # 滚动容器显示所有页面
                            with st.container(height=650):
                                for pg in range(1, page_count + 1):
                                    st.caption(f"📄 Page {pg} / {page_count}")
                                    u = f"{api_base}/pdf/page?kbId={current_kb}&fileId={fid}&page={pg}&type={mode}"
                                    # 利用浏览器缓存，滚动加载
                                    st.image(u, use_container_width=True)
                                    st.divider()
                        else:
                            st.info("无页面图像")

            use_dialog = hasattr(st, "dialog")
            if use_dialog:
                @st.dialog("📄 文件预览", width="large")
                def open_prev_dlg(fid, fname, is_excel, page_count):
                    show_preview_dialog_content(fid, fname, is_excel, page_count)

            # 3. 渲染每一行 (使用滚动容器)
            with st.container(height=500):
                for f in kb_files:
                    fid = f.get('fileId')
                    fname = f.get('fileName') or fid
                    page_count = f.get('pageCount', 0)
                    file_type = f.get('type', 'unknown').upper()
                    is_excel = f.get('type') == 'excel'
                    
                    # 行容器
                    row_c1, row_c2, row_c3, row_c4, row_c5, row_c6 = st.columns([0.5, 2.5, 0.8, 1.5, 1.5, 1.5])
                    
                    # Col 1: Checkbox
                    is_checked = fid in st.session_state.selected_files
                    
                    # Manual state handling
                    # 为了避免 Streamlit "created with a default value but also set via Session State API" 警告，
                    # 我们不再使用 value=... 参数，而是确保 key 在 session_state 中已经初始化。
                    if f"chk_{fid}" not in st.session_state:
                        st.session_state[f"chk_{fid}"] = is_checked

                    new_checked = row_c1.checkbox("", key=f"chk_{fid}")
                    if new_checked != is_checked:
                        if new_checked: st.session_state.selected_files.add(fid)
                        else: st.session_state.selected_files.discard(fid)
                        st.rerun()

                    # Col 2: Filename Button -> Preview
                    if row_c2.button(f"📄 {fname}", key=f"btn_file_{fid}", help="点击预览", use_container_width=True):
                        if use_dialog:
                            open_prev_dlg(fid, fname, is_excel, page_count)
                        else:
                            # Fallback: Expander at top? or Toast
                            st.toast("升级 Streamlit 可使用弹窗预览", icon="⚠️")
                    
                    # Col 3: Type
                    row_c3.text(file_type)

                    # Col 4: Parsing Model
                    p_model = "Original/OCR"
                    if is_excel: p_model = "Pandas"
                    row_c4.text(p_model)

                    # Col 5: Embed Model
                    row_c5.text(kb_embed_model)

                    # Col 6: Status
                    is_parsed = f.get('hasParsed', False)
                    is_indexed = f.get('isIndexed', False)
                    
                    status_md = ""
                    if is_parsed:
                        status_md += "✅ 解析完成<br>"
                    else:
                        status_md += "⏳ 解析中...<br>"
                        
                    if is_indexed:
                        status_md += "🔵 已索引"
                    else:
                        status_md += "⚪ 未索引"
                        
                    row_c6.markdown(status_md, unsafe_allow_html=True)
                    
                    st.divider()

        # --- Tab 2: 图像管理 ---
        with m_tab2:
            st.caption("管理知识库中提取的所有图片及其描述。可在下方直接修改描述并保存。")
            
            # 状态 Key
            k_imgs = f"kb_imgs_{current_kb}"
            # 标记是否已加载过 (用于自动加载)
            k_imgs_loaded = f"kb_imgs_loaded_{current_kb}"

            if k_imgs not in st.session_state:
                st.session_state[k_imgs] = []
            if k_imgs_loaded not in st.session_state:
                st.session_state[k_imgs_loaded] = False

            # 定义加载数据的函数
            def load_images_data():
                try:
                    r_imgs = requests.get(f"{api_base}/kb/images", params={"kbId": current_kb}, timeout=10)
                    if r_imgs.status_code == 200:
                        st.session_state[k_imgs] = r_imgs.json().get("images", [])
                        st.session_state[k_imgs_loaded] = True
                    else:
                        st.error(f"Failed: {r_imgs.text}")
                except Exception as e:
                    st.error(f"Error: {e}")

            # 1. 自动加载逻辑 (如果从未加载过)
            if not st.session_state[k_imgs_loaded]:
                with st.spinner("正在自动加载图片列表..."):
                    load_images_data()
                    st.rerun()

            # 2. 手动刷新按钮
            if st.button("🔄 刷新图片列表", key="btn_load_imgs_real"):
                 with st.spinner("正在刷新..."):
                    load_images_data()
                    st.rerun()
            
            images_data = st.session_state[k_imgs]
            if not images_data:
                if st.session_state[k_imgs_loaded]:
                    st.info("当前知识库未检测到已解析的图片。")
            else:
                # 转换为 DataFrame 以使用 DataEditor
                # 字段: Preview (Image URL), FileName, Source (Page), Summary (Editable)
                
                # 构造用于显示的列表
                display_rows = []
                for idx, img in enumerate(images_data):
                    fname = img.get('fileName', 'Unknown')
                    page = img.get('page_num', '?')
                    desc = img.get('summary', '')
                    img_path = img.get('img_name', '')
                    fid = img.get('fileId')
                    
                    # 构造图片 URL
                    # 注意：api_base 可能是 localhost:8001/api/v1
                    # 需要 quote img_path
                    enc_path = urllib.parse.quote(img_path)
                    img_url = f"{api_base}/pdf/images?kbId={current_kb}&fileId={fid}&imagePath={enc_path}"
                    
                    display_rows.append({
                        "id": idx, # 用于索引
                        "file_id": fid,
                        "base_img_path": img_path, # 原始文件名，用于回传
                        "图片预览": img_url,
                        "图片名称": img_path,
                        "图片来源": f"{fname} (Top {idx+1}) - P{page}", # Hacky source
                        "来源文件": fname,
                        "页码": page,
                        "图片描述 (可编辑)": desc
                    })
                
                df_imgs = pd.DataFrame(display_rows)
                
                # 配置列
                column_config = {
                    "图片预览": st.column_config.ImageColumn("预览", width="medium"),
                    "图片名称": st.column_config.TextColumn("文件名", width="small", disabled=True),
                    "来源文件": st.column_config.TextColumn("来源文件", width="small", disabled=True),
                    "页码": st.column_config.NumberColumn("页码", width="small", disabled=True),
                    "图片描述 (可编辑)": st.column_config.TextColumn("图片描述", width="large"),
                    "id": None, # Hide
                    "file_id": None,
                    "base_img_path": None,
                    "图片来源": None
                }
                
                edited_df = st.data_editor(
                    df_imgs[["图片预览", "图片名称", "来源文件", "页码", "图片描述 (可编辑)", "id", "file_id", "base_img_path"]],
                    column_config=column_config,
                    use_container_width=True,
                    key="img_ed_tab2",
                    height=600,
                    hide_index=True
                )
                
                # 检测修改并保存
                # Streamlit data_editor 返回修改后的 DF
                # 我们需要对比差异，或者提供一个“保存修改”按钮批量更新
                
                if st.button("💾 保存描述修改", type="primary", key="btn_save_imgs"):
                    # 找出被修改的行
                    # 简单策略：按照 file_id 分组，将修改后的 summaries 重新提交
                    
                    # 1. 重构数据结构
                    # file_id -> list of summaries
                    updates_by_file = {}
                    
                    # 将 DF 转回 list of dicts
                    new_records = edited_df.to_dict('records')
                    
                    # 既然 data_editor 是全量返回，我们需要知道原始数据结构中的其他字段(如 bbox 等)是否存在？
                    # 我们的 images_data 是原始数据。 new_records 只有部分字段。
                    # 所以我们需要 merge。
                    
                    has_changes = False
                    
                    for row in new_records:
                        idx = row['id']
                        new_desc = row['图片描述 (可编辑)']
                        original = images_data[idx]
                        
                        # 检查是否有变化
                        if new_desc != original['summary']:
                            has_changes = True
                            fid = original['fileId']
                            if fid not in updates_by_file:
                                updates_by_file[fid] = []
                            
                            # 注意：我们需要该文件下 *所有* 图片的完整列表才能提交更新吗？
                            # 看后端 /pdf/image_summaries/update 实现：
                            # 它是全量覆盖: save_image_summaries(req.kbId, req.fileId, req.summaries)
                            # 所以我们需要先按 fileId 分组所有图片，更新变化的那些，保持没变的那些原样，然后一起提交。
                            
                    if not has_changes:
                        st.info("未检测到任何修改")
                    else:
                         # 2. 准备全量数据
                         # 先按 fileId 归类原始数据
                         file_groups = {}
                         for img in images_data:
                             fid = img['fileId']
                             if fid not in file_groups: file_groups[fid] = []
                             file_groups[fid].append(img)
                         
                         # 应用修改
                         success_count = 0
                         # 遍历 DF 中所有行（因为用户可能改了多行）
                         # 也可以只遍历 new_records，因为它是全集
                         for row in new_records:
                             idx = row['id']
                             new_desc = row['图片描述 (可编辑)']
                             original = images_data[idx]
                             
                             if new_desc != original['summary']:
                                 # 找到对应 file_group 中的该图片对象并更新
                                 # images_data[idx] 引用的是 file_groups 中的同一个对象吗？
                                 # 是的，Python 列表存的是引用。
                                 original['summary'] = new_desc
                         
                         # 提交更新 (仅提交有变动的文件)
                         updated_files = set([images_data[idx]['fileId'] for row in new_records if row['图片描述 (可编辑)'] != images_data[idx]['summary']])
                         
                         progress_bar = st.progress(0, text="正在保存...")
                         for i, fid in enumerate(updated_files):
                             subset = file_groups[fid]
                             # 清理不必要的显示字段以免传给后端报错 (后端只存必要字段)
                             clean_subset = []
                             for s in subset:
                                 # 复制一份，只保留核心字段
                                 clean_item = {k: v for k, v in s.items() if k in ['img_name', 'summary', 'page_num', 'bbox']}
                                 clean_subset.append(clean_item)

                             try:
                                 r_up = requests.post(f"{api_base}/pdf/image_summaries/update", json={
                                     "kbId": current_kb,
                                     "fileId": fid,
                                     "summaries": clean_subset
                                 })
                                 if r_up.status_code == 200:
                                     success_count += 1
                                 else:
                                     st.error(f"文件 {fid} 更新失败: {r_up.text}")
                             except Exception as e:
                                 st.error(f"Error updating {fid}: {e}")
                             progress_bar.progress((i + 1) / len(updated_files))
                         
                         if success_count > 0:
                             st.success(f"成功更新 {success_count} 个文件的图片描述！索引已重建。")
                             time.sleep(1)
                             # 刷新本地缓存
                             st.rerun()

        # --- Tab 3: 数值管理 ---
        with m_tab3:
            st.caption("管理知识库中所有结构化数值文件（如 Excel/CSV）。")
            
            # 过滤结构化文件
            struct_files = [f for f in kb_files if f.get('type') == 'excel']
            
            if not struct_files:
                st.info("当前知识库没有结构化数值文件 (Excel/CSV)。")
            else:
                # 初始化选择状态
                if "selected_struct_fid" not in st.session_state:
                    st.session_state.selected_struct_fid = None

                # 左右布局：列表 | 详情
                col_struct_list, col_struct_detail = st.columns([1, 3])
                
                # 左侧：可滚动的文件列表
                with col_struct_list:
                    st.markdown("### 📂 文件列表")
                    with st.container(height=600):
                        for f in struct_files:
                            fid = f.get('fileId')
                            fname = f.get('fileName') or fid
                            
                            # 选中高亮样式 (通过 type="primary" 实现)
                            btn_type = "primary" if st.session_state.selected_struct_fid == fid else "secondary"
                            
                            # 为了保证 key 唯一且点击有效
                            if st.button(f"📊 {fname}", key=f"btn_struct_{fid}", type=btn_type, use_container_width=True):
                                st.session_state.selected_struct_fid = fid
                                st.rerun()

                # 右侧：详情展示
                with col_struct_detail:
                    current_struct_fid = st.session_state.selected_struct_fid
                    # 校验选中文件是否仍在当前列表中
                    if current_struct_fid and not any(sf.get('fileId') == current_struct_fid for sf in struct_files):
                        current_struct_fid = None
                        st.session_state.selected_struct_fid = None
                    
                    if not current_struct_fid:
                        st.info("👈 请在左侧选择一个文件查看详情")
                    else:
                        # 获取当前选中文件的名称
                        curr_file_obj = next((f for f in struct_files if f['fileId'] == current_struct_fid), None)
                        curr_fname = curr_file_obj.get('fileName') if curr_file_obj else current_struct_fid
                        
                        st.markdown(f"### 📄 {curr_fname}")
                        st.divider()
                        
                        # 加载数据
                        with st.spinner("正在加载数据..."):
                            try:
                                r_df = requests.get(f"{api_base}/kb/file/dataframe", params={"kbId": current_kb, "fileId": current_struct_fid})
                                if r_df.status_code == 200:
                                    sheets = r_df.json().get("sheets", {})
                                    if not sheets:
                                        st.warning("⚠️ 文件读取为空或格式不支持")
                                    else:
                                        # 多 Sheet 展示
                                        sheet_names = list(sheets.keys())
                                        if len(sheet_names) > 1:
                                            # 使用 Tabs 切换 Sheet
                                            tabs_sheets = st.tabs(sheet_names)
                                            for i, s_name in enumerate(sheet_names):
                                                with tabs_sheets[i]:
                                                    df_sheet = pd.DataFrame(sheets[s_name])
                                                    st.dataframe(df_sheet, use_container_width=True, height=550)
                                                    st.caption(f"共 {len(df_sheet)} 行 | Sheet: {s_name}")
                                        else:
                                            # 单 Sheet 直接展示
                                            s_name = sheet_names[0]
                                            df_sheet = pd.DataFrame(sheets[s_name])
                                            st.dataframe(df_sheet, use_container_width=True, height=600)
                                            st.caption(f"共 {len(df_sheet)} 行")
                                            
                                else:
                                    st.error(f"❌ 加载失败: {r_df.text}")
                            except Exception as e:
                                st.error(f"❌ 请求错误: {e}")

# --- Tab 6: 知识提取 ---
with tab6:
    st.header("知识提取与结构化")
    st.caption("上传文档 -> 定义提取目标 -> 智能提取 -> 存入知识库")

    # init session state for extraction
    if "ext_job_id" not in st.session_state:
        st.session_state.ext_job_id = None
    if "ext_job_info" not in st.session_state:
        st.session_state.ext_job_info = None

    col_ex_1, col_ex_2 = st.columns([1, 1])

    with col_ex_1:
        st.subheader("1. 文件与配置")
        uploaded_file = st.file_uploader("上传待提取的文件 (PDF, Excel, CSV, TXT)", type=["pdf", "xlsx", "csv", "txt", "md"])
        
        # --- 预置模板逻辑 ---
        PRESET_TEMPLATES = {
            "地层压力和温度": (
                "请提取'地层压力和温度'表格。该表通常包含多级表头。\n"
                "目标列(JSON Key)：\n"
                "- 序号\n"
                "- 井号\n"
                "- 原始_饱和压力_MPa\n"
                "- 原始_地层压力_MPa\n"
                "- 原始_压力系数\n"
                "- 原始_油层温度_℃\n"
                "- 原始_地温梯度_℃/100m\n"
                "- 结论_温度\n"
                "- 结论_压力\n"
                "- 备注\n"
                "注意：请处理'原始'和'结论'下的合并单元格结构，将子列的数据准确提取到对应字段。"
            ),
            "油水关系及油藏类型": (
                "请提取'油水关系及油藏类型'表格。\n"
                "目标列(JSON Key)：\n"
                "- 序号\n"
                "- 层位\n"
                "- 油藏类型\n"
                "- 油藏类型细分\n"
                "- 边底水\n"
                "- 气顶\n"
                "- 油水界面_m\n"
                "- 备注\n"
                "注意：若存在合并行，请将合并内容填充到每一行。"
            ),
            "油分析": (
                "请提取'原油分析'或'油分析'表格。\n"
                "目标列(JSON Key)：\n"
                "- 序号\n"
                "- 层位\n"
                "- 取样_取样井号\n"
                "- 取样_取样井段_m\n"
                "- 取样_取样时间\n"
                "- 油分析_测粘温度_℃\n"
                "- 油分析_地面密度_g/cm3\n"
                "- 油分析_地面粘度_mPa.s\n"
                "- 油分析_凝固点_℃\n"
                "- 油分析_含硫_%\n"
                "- 油分析_含蜡_%\n"
                "- 油分析_H2S 含量_%\n"
                "- 结论\n"
                "注意：若存在合并行，请将合并内容填充到每一行。"
            ),
            "水分析": (
                "请提取'地层水分析'或'水分析'表格。\n"
                "目标列(JSON Key)：\n"
                "- 序号\n"
                "- 层位\n"
                "- 取样_取样井号\n"
                "- 取样_取样井段_m\n"
                "- 取样_取样时间\n"
                "- 水分析_Na+_mg/l\n"
                "- 水分析_Mg+_mg/l\n"
                "- 水分析_Ca+_mg/l\n"
                "- 水分析_Cl-_mg/l\n"
                "- 水分析_SO4-_mg/l\n"
                "- 水分析_CO3-_mg/l\n"
                "- 水分析_总矿化度_mg/l\n"
                "- 结论\n"
                "注意：若存在合并行，请将合并内容填充到每一行。"
            ),
            "气分析": (
                "请提取'天然气分析'或'气分析'表格。\n"
                "目标列(JSON Key)：\n"
                "- 序号\n"
                "- 层位\n"
                "- 取样_取样井号\n"
                "- 取样_取样井段_m\n"
                "- 取样_取样时间\n"
                "- 气分析_氦\n"
                "- 气分析_氢\n"
                "- 气分析_氧\n"
                "- 气分析_氮\n"
                "- 气分析_二氧化碳\n"
                "- 气分析_乙烷\n"
                "- 气分析_丙烷\n"
                "- 气分析_异丁烷\n"
                "- 气分析_正丁烷\n"
                "- 气分析_新戊烷\n"
                "- 气分析_异戊烷\n"
                "- 气分析_正戊烷\n"
                "- 气分析_己烷\n"
                "- 气分析_庚烷和更重组分\n"
                "- 气分析_一氧化碳\n"
                "- 气分析_硫化氢\n"
                "- 气分析_二氧化硫\n"
                "注意：若存在合并行，请将合并内容填充到每一行。"
            )
        }

        # 初始化 Prompt 状态键
        if "extraction_prompt_text" not in st.session_state:
            st.session_state.extraction_prompt_text = ""

        # 模式选择
        ext_mode = st.radio("指令模式", ["自定义输入", "预置模板 (油气领域)"], horizontal=True)
        
        if ext_mode == "预置模板 (油气领域)":
            selected_tmpl_key = st.selectbox("选择提取模板", list(PRESET_TEMPLATES.keys()))
            # 检测模板切换，自动填充 Text Area
            if st.session_state.get("last_selected_tmpl") != selected_tmpl_key:
                st.session_state.extraction_prompt_text = PRESET_TEMPLATES[selected_tmpl_key]
                st.session_state.last_selected_tmpl = selected_tmpl_key
        
        extraction_instruction = st.text_area(
            "提取指令 (Prompt)", 
            key="extraction_prompt_text",
            height=150,
            placeholder="例如：\n请提取文档中的所有发票信息，包含发票代码、号码、金额、日期。\n或者：提取所有提到的公司名称及其对应的地址。"
        )
        
        output_fmt = st.selectbox("输出格式", ["Excel", "CSV"])
        custom_filename = st.text_input("保存文件名 (可选)", placeholder="留空则自动生成: extracted_{timestamp}")
        
        parse_method = st.selectbox(
            "解析模型",
            options=["original", "olmocr"],
            format_func=lambda x: {"original": "基础解析 (结构化OCR, 适合文本/表格)", "olmocr": "多模态大模型 (视觉增强, 适合复杂排版)"}[x]
        )
        
        if st.session_state.ext_job_id:
            if st.button("🔄 如果卡住点此重置状态"):
                st.session_state.ext_job_id = None
                st.session_state.ext_job_info = None
                st.rerun()

    with col_ex_2:
        st.subheader("2. 目标知识库")
        
        # Fetch KBs
        kb_options = {}
        try:
            r_kb = requests.get(f"{api_base}/kb/list", timeout=3)
            if r_kb.status_code == 200:
                kb_list_data = r_kb.json().get("kbs", [])
                for k in kb_list_data:
                    kid = k.get("kbId")
                    kname = k.get("kbName", kid)
                    display = f"{kname} ({kid})" if kname != kid else kid
                    kb_options[kid] = display
        except:
            pass
            
        kb_mode = st.radio("选择知识库模式", ["现有知识库", "新建知识库"], horizontal=True)
        
        target_kb_id = ""
        if kb_mode == "现有知识库":
            if kb_options:
                target_kb_id = st.selectbox(
                    "选择目标知识库", 
                    list(kb_options.keys()),
                    format_func=lambda x: kb_options[x]
                )
            else:
                st.warning("暂无可用知识库，请选择新建")
                kb_mode = "新建知识库" # Force new
        
        if kb_mode == "新建知识库":
            # 复用新建逻辑
            existing_kbs_list = []
            if kb_list_data:
                 existing_kbs_list = kb_list_data
            
            created_id = handle_create_kb("extract_tab", existing_kbs_list, api_base)
            if created_id:
                target_kb_id = created_id
            else:
                st.caption("请在上方新建知识库 (创建成功后请切换到'现有知识库'选择)")

    st.divider()
    
    # Action Button
    if st.button("🚀 开始提取", type="primary", use_container_width=True, disabled=(st.session_state.ext_job_id is not None)):
        if not uploaded_file:
            st.warning("请先上传文件")
        elif not target_kb_id:
            st.warning("请指定目标知识库")
        elif not extraction_instruction:
            st.warning("请输入提取指令")
        else:
            try:
                with st.spinner("正在上传文件并创建任务..."):
                    files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
                    data = {
                        "instruction": extraction_instruction,
                        "kb_id": target_kb_id,
                        "output_format": output_fmt,
                        "custom_filename": custom_filename,
                        "parse_method": parse_method
                    }
                    resp = requests.post(f"{api_base}/extraction/extract", files=files, data=data, timeout=600)
                    if resp.status_code == 200:
                        job_data = resp.json()
                        st.session_state.ext_job_id = job_data.get("jobId")
                        st.session_state.ext_job_info = None
                        st.rerun()
                    else:
                        st.error(f"启动失败: {resp.text}")
            except Exception as e:
                st.error(f"请求错误: {e}")

    # Status Monitor
    if st.session_state.ext_job_id:
        st.info("🔄 任务进行中，自动刷新页面中...")
        
        try:
            r_stat = requests.get(f"{api_base}/extraction/status", params={"jobId": st.session_state.ext_job_id})
            if r_stat.status_code == 200:
                job_info = r_stat.json()
                st.session_state.ext_job_info = job_info
                
                status = job_info.get("status")
                progress = job_info.get("progress", 0)
                
                # Progress Bar
                st.progress(int(progress), text=f"当前阶段: {status} ({progress}%)")
                
                # Show parsed content if ready (Parsing happens at progress > 10, usually > 40 means done parsing)
                if job_info.get("parsed_file_path") and progress >= 40:
                    with st.expander("📝 能够查看解析后的中间内容 (Parsed Content)", expanded=False):
                        if st.button("加载/刷新解析内容"):
                            try:
                                r_cnt = requests.get(f"{api_base}/extraction/content", params={"jobId": st.session_state.ext_job_id})
                                if r_cnt.status_code == 200:
                                    st.text_area("解析结果", r_cnt.json().get("content"), height=400)
                                else:
                                    st.error("获取内容失败")
                            except:
                                st.error("网络请求失败")

                if status == "completed":
                    st.success("✅ 提取完成！")
                    extracted_data = job_info.get("data", [])
                    res_path = job_info.get("result_filepath", "")
                    rel_context = job_info.get("relevant_context", "")
                    rel_pages = job_info.get("relevant_pages", [])

                    if rel_context:
                        with st.expander("🔍 查看定位到的源文件上下文与页面 (Source Context & Pages)", expanded=False):
                            c1, c2 = st.columns([1, 1])
                            with c1:
                                st.text_area("提取所基于的文本上下文:", rel_context, height=400)
                            with c2:
                                if rel_pages:
                                    st.markdown(f"**定位到的相关页面 (共 {len(rel_pages)} 页)**")
                                    # Create Tabs for pages if multiple
                                    if len(rel_pages) > 1:
                                        p_tabs = st.tabs([f"Page {p}" for p in rel_pages])
                                        for idx, p in enumerate(rel_pages):
                                            with p_tabs[idx]:
                                                st.image(f"{api_base}/extraction/image?jobId={st.session_state.ext_job_id}&page={p}", caption=f"Original Page {p}", use_container_width=True)
                                    else:
                                        p = rel_pages[0]
                                        st.image(f"{api_base}/extraction/image?jobId={st.session_state.ext_job_id}&page={p}", caption=f"Original Page {p}", use_container_width=True)
                                else:
                                    st.info("未能自动定位到具体页码图像")
                    
                    st.subheader("提取结果预览")
                    if extracted_data:
                        df_res = pd.DataFrame(extracted_data)
                        
                        st.caption("✏️ 您可以直接双击下方表格单元格进行修改，如需增删行请使用表格工具栏。修改完成后请点击“保存修改并入库”。")
                        edited_df = st.data_editor(
                            df_res, 
                            use_container_width=True, 
                            num_rows="dynamic",
                            key="extraction_editor"
                        )
                        
                        col_btns_1, col_btns_2 = st.columns([1, 1])
                        
                        with col_btns_1:
                            if st.button("💾 保存修改并入库", type="primary"):
                                try:
                                    # Convert DF back to list of dicts
                                    # Handle NaN/inf for JSON compliance
                                    cleaned_df = edited_df.fillna("") 
                                    new_data = cleaned_df.to_dict(orient='records')
                                    
                                    r_up = requests.post(f"{api_base}/extraction/update_result", json={
                                        "jobId": st.session_state.ext_job_id,
                                        "data": new_data
                                    })
                                    
                                    if r_up.status_code == 200:
                                        st.toast("修改已保存到知识库！", icon="✅")
                                        # Update session state to reflect changes
                                        st.session_state.ext_job_info['data'] = new_data
                                        time.sleep(1)
                                        st.rerun()
                                    else:
                                        st.error(f"保存失败: {r_up.text}")
                                except Exception as e:
                                    st.error(f"请求错误: {e}")

                        with col_btns_2:
                            # Download Button
                            if res_path and os.path.exists(res_path):
                                with open(res_path, "rb") as f:
                                    st.download_button(
                                        label=f"⬇️ 下载结果 ({output_fmt})",
                                        data=f,
                                        file_name=os.path.basename(res_path),
                                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if output_fmt == "Excel" else "text/csv"
                                    )
                    else:
                        st.warning("未提取到有效数据，请检查指令或文档内容。")
                    
                    if st.button("开启新任务"):
                        st.session_state.ext_job_id = None
                        st.session_state.ext_job_info = None
                        st.rerun()

                elif status == "failed":
                    st.error(f"❌ 任务失败: {job_info.get('error')}")
                    if st.button("重试 / 返回"):
                        st.session_state.ext_job_id = None
                        st.session_state.ext_job_info = None
                        st.rerun()
                else:
                    # Still running
                    time.sleep(2)
                    st.rerun()
            else:
                st.error("无法获取任务状态")
                time.sleep(5)
                st.rerun()
                
        except Exception as e:
            st.error(f"轮询错误: {e}")
            time.sleep(5)
            st.rerun()



