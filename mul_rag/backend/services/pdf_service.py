# services/pdf_service.py
from __future__ import annotations
import os, io, math, json, re, shutil, time
from pathlib import Path
from typing import Dict, Any, List
import fitz
from PIL import Image
import matplotlib
matplotlib.use("Agg")  # 服务器无头
import matplotlib.pyplot as plt
import matplotlib.patches as patches

from langchain_unstructured import UnstructuredLoader
from unstructured.partition.pdf import partition_pdf
from html2text import html2text
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage
import base64
import requests

# 统一的根目录：每个 kbId 一个子目录；文件存放在 kbId/files/<fileId>/ 下
DATA_ROOT = Path("data")

# OLMOCR 配置
OLMOCR_ENDPOINT = "http://localhost:8005/v1/chat/completions"
OLMOCR_MODEL = "olmocr"

def sanitize_filename(name: str) -> str:
    """将字符串转换为安全的文件名"""
    # 移除无效字符
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    # 合并空白
    name = re.sub(r'\s+', " ", name).strip()
    # 限制长度，防止 Errno 36 File name too long (取前50个字符)
    if len(name) > 50:
        name = name[:50].strip()
    return name

def kb_dir(kb_id: str) -> Path:
    d = DATA_ROOT / kb_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def kb_files_dir(kb_id: str) -> Path:
    d = kb_dir(kb_id) / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def workdir(kb_id: str, file_id: str) -> Path:
    """某个知识库下某个文件的工作目录。"""
    d = kb_files_dir(kb_id) / file_id
    d.mkdir(parents=True, exist_ok=True)
    return d

def kb_metadata_path(kb_id: str) -> Path:
    return kb_dir(kb_id) / "meta.json"

def read_kb_metadata(kb_id: str) -> Dict[str, Any]:
    path = kb_metadata_path(kb_id)
    if path.exists():
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            meta.setdefault("kbId", kb_id)
            return meta
        except Exception:
            pass
    return {"kbId": kb_id, "name": kb_id}

def write_kb_metadata(kb_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    data = {"kbId": kb_id, **meta}
    path = kb_metadata_path(kb_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data

def dir_original_pages(kb_id: str, file_id: str) -> Path:
    p = workdir(kb_id, file_id) / "pages" / "original"
    p.mkdir(parents=True, exist_ok=True); return p

def dir_parsed_pages(kb_id: str, file_id: str) -> Path:
    p = workdir(kb_id, file_id) / "pages" / "parsed"
    p.mkdir(parents=True, exist_ok=True); return p

def original_pdf_path(kb_id: str, file_id: str, filename: str = None) -> Path:
    """
    返回主文件的路径 (PDF, Excel, CSV)。
    如果提供了 filename，则使用该文件名（用于保存时）。
    如果不提供 filename，则尝试查找目录下已存在的主文件（用于读取时）。
    """
    wd = workdir(kb_id, file_id)
    if filename:
        return wd / filename
    
    # 尝试查找现有的文件
    for ext in ["*.pdf", "*.xlsx", "*.xls", "*.csv"]:
        files = list(wd.glob(ext))
        if files:
            return files[0]
            
    # 默认回退到 file_id 作为文件名 (假设是 PDF)
    return wd / file_id

def markdown_output(kb_id: str, file_id: str) -> Path:
    return workdir(kb_id, file_id) / "output.md"

def images_dir(kb_id: str, file_id: str) -> Path:
    p = workdir(kb_id, file_id) / "images"
    p.mkdir(parents=True, exist_ok=True); return p

def save_upload(kb_id: str, file_id: str, upload_bytes: bytes, filename: str) -> Dict[str, Any]:
    """保存上传的文件，并返回页数（如果是 PDF）"""
    # 清理旧内容但保留 metadata
    wd = workdir(kb_id, file_id)
    for entry in wd.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            try:
                entry.unlink()
            except Exception:
                pass

    # kb 元数据不存在时写入默认
    meta_path = kb_metadata_path(kb_id)
    if not meta_path.exists():
        write_kb_metadata(kb_id, {"name": kb_id, "createdAt": int(time.time())})

    # 使用原始文件名保存
    file_path = original_pdf_path(kb_id, file_id, filename)
    file_path.write_bytes(upload_bytes)
    
    pages = 0
    if filename.lower().endswith('.pdf'):
        try:
            with fitz.open(file_path) as doc:
                pages = doc.page_count
        except Exception as e:
            print(f"Error reading PDF page count: {e}")
            
    return {"kbId": kb_id, "fileId": file_id, "name": filename, "pages": pages}

def render_original_pages(kb_id: str, file_id: str, dpi: int = 144, progress_callback=None):
    """把原始 PDF 渲染为 PNG，存到 pages/original/"""
    pdf_path = original_pdf_path(kb_id, file_id)
    out_dir = dir_original_pages(kb_id, file_id)
    with fitz.open(pdf_path) as doc:
        for idx, page in enumerate(doc, start=1):
            if progress_callback: progress_callback(idx / doc.page_count)
            mat = fitz.Matrix(dpi/72, dpi/72)
            pix = page.get_pixmap(matrix=mat)
            (out_dir / f"page-{idx:04d}.png").write_bytes(pix.tobytes("png"))

def _plot_boxes_to_ax(ax, pix, segments):
    category_to_color = {
        "Title": "orchid",
        "Image": "forestgreen",
        "Table": "tomato",
    }
    categories = set()
    for seg in segments:
        points = seg["coordinates"]["points"]
        lw = seg["coordinates"]["layout_width"]
        lh = seg["coordinates"]["layout_height"]
        scaled = [(x * pix.width / lw, y * pix.height / lh) for x, y in points]
        color = category_to_color.get(seg.get("category"), "deepskyblue")
        categories.add(seg.get("category", "Text"))
        poly = patches.Polygon(scaled, linewidth=1, edgecolor=color, facecolor="none")
        ax.add_patch(poly)

    legend_handles = [patches.Patch(color="deepskyblue", label="Text")]
    for cat, color in category_to_color.items():
        if cat in categories:
            legend_handles.append(patches.Patch(color=color, label=cat))
    ax.legend(handles=legend_handles, loc="upper right")

def render_parsed_pages_with_boxes(kb_id: str, file_id: str, docs_local: List[Dict[str, Any]], dpi: int = 144, progress_callback=None):
    """
    根据 UnstructuredLoader 的 metadata（含坐标）在原图上叠框，输出到 pages/parsed/
    """
    pdf_path = original_pdf_path(kb_id, file_id)
    out_dir = dir_parsed_pages(kb_id, file_id)
    with fitz.open(pdf_path) as doc:
        # 预聚合：按 page_number 分组 segments
        segments_by_page: Dict[int, List[Dict[str, Any]]] = {}
        for d in docs_local:
            meta = d.metadata if hasattr(d, "metadata") else d["metadata"]      
            pno = meta.get("page_number")
            if pno is None: continue
            segments_by_page.setdefault(pno, []).append(meta)

        for page_number in range(1, doc.page_count + 1):
            if progress_callback: progress_callback(page_number / doc.page_count)
            page = doc.load_page(page_number - 1)
            mat = fitz.Matrix(dpi/72, dpi/72)
            pix = page.get_pixmap(matrix=mat)
            pil = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            fig, ax = plt.subplots(1, figsize=(10, 10))
            ax.imshow(pil)
            ax.axis("off")
            _plot_boxes_to_ax(ax, pix, segments_by_page.get(page_number, []))
            fig.tight_layout()
            fig.savefig(out_dir / f"page-{page_number:04d}.png", bbox_inches="tight", pad_inches=0)
            plt.close(fig)

def unstructured_segments(kb_id: str, file_id: str, progress_callback=None) -> List[Any]:
    """用 UnstructuredLoader 产生高分辨率布局段"""
    # 查找实际的 PDF 文件路径
    pdf_path = str(original_pdf_path(kb_id, file_id))

    # 【修复1】解决因 huggingface 下载卡死的问题，强制指定镜像
    import os
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"

    # 【修复3】避免底层 C++ (OpenCV/Paddle) 多线程在 FastAPI 异步中产生死锁！
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
    os.environ["NUMEXPR_NUM_THREADS"] = "1"

    loader = UnstructuredLoader(
        file_path=pdf_path,
        strategy="hi_res",
        infer_table_structure=False, # 强烈建议：关闭极其导致崩溃的表格嵌套模型！
        languages=["chi_sim", "eng"], # 【修复2】改用官方要求的新参数 languages
        ocr_engine="paddleocr",
    )
    
    # 转换为列表触发实际解析，我们模拟一个进度的增长
    if progress_callback: progress_callback(0.1)
    
    out = []
    # 这里加载是阻塞的，耗时最长
    elements = loader.lazy_load()
    elems_list = list(elements)
    
    if progress_callback: progress_callback(0.9)
    
    for i, d in enumerate(elems_list):
        if progress_callback: progress_callback(0.9 + 0.1 * (i / max(1, len(elems_list))))
        out.append(d)
    return out

def pdf_to_markdown(kb_id: str, file_id: str, progress_callback=None):
    # 查找实际的 PDF 文件路径
    pdf_path = str(original_pdf_path(kb_id, file_id))
    out_md = markdown_output(kb_id, file_id)
    img_dir = images_dir(kb_id, file_id)

    # 避免 FastAPI 进程内 C++ 底层 OpenCV 与 PaddleOCR 多线程交叉死锁
    import os
    os.environ["OMP_NUM_THREADS"] = "1"

    if progress_callback: progress_callback(0.1)

    elements = partition_pdf(
        filename=pdf_path,
        infer_table_structure=False, # 强烈建议：关闭极其导致崩溃的表格嵌套模型！
        strategy="hi_res",
        languages=["chi_sim", "eng"], # 【修复2】改用官方要求的新参数
        ocr_engine="paddleocr"  # 同上
    )

    if progress_callback: progress_callback(0.8)

    # 提取图片
    image_map = {}
    with fitz.open(pdf_path) as doc:
        for page_num, page in enumerate(doc, start=1):
            if progress_callback: progress_callback(0.8 + 0.2 * (page_num / doc.page_count))
            image_map[page_num] = []
            for img_index, img in enumerate(page.get_images(full=True), start=1):
                xref = img[0]
                pix = fitz.Pixmap(doc, xref)
                img_path = img_dir / f"page{page_num}_img{img_index}.png"
                if pix.n < 5:
                    pix.save(str(img_path))
                else:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                    pix.save(str(img_path))
                image_map[page_num].append(img_path.name)  # 只保存文件名

    md_lines: List[str] = []
    inserted_images = set()
    image_captions = {} # filename -> caption

    for i, el in enumerate(elements):
        cat = getattr(el, "category", None)
        text = (getattr(el, "text", "") or "").strip()
        meta = getattr(el, "metadata", None)
        page_num = getattr(meta, "page_number", None) if meta else None

        if not text and cat != "Image":
            continue

        if cat == "Title" and text.startswith("- "):
            md_lines.append(text + "\n")
        elif cat == "Title":
            md_lines.append(f"# {text}\n")
        elif cat in ["Header", "Subheader"]:
            md_lines.append(f"## {text}\n")
        elif cat == "Table":
            html = getattr(meta, "text_as_html", None) if meta else None
            if html:
                md_lines.append(html2text(html) + "\n")
            else:
                md_lines.append((text or "") + "\n")
        elif cat == "Image" and page_num:
            # 尝试寻找紧邻的图片标题 (FigureCaption)
            caption = ""
            # 向下查找最多 20 个元素 (跨页查找)
            for j in range(i + 1, min(i + 20, len(elements))):
                next_el = elements[j]
                next_cat = getattr(next_el, "category", None)
                next_text = (getattr(next_el, "text", "") or "").strip()
                
                # 跳过页面元数据 (页眉、页脚、页码)
                if next_cat in ["Header", "Footer", "PageNumber"]:
                    continue
                
                # 如果遇到下一张图片，说明当前图片没有标题，停止查找
                if next_cat == "Image":
                    break

                # 如果遇到 FigureCaption 或以 "图" / "Figure" 开头的短文本
                if next_cat == "FigureCaption" or \
                   ((next_text.startswith("图") or next_text.lower().startswith("figure")) and len(next_text) < 100):
                    caption = next_text
                    break
                
                # 如果遇到其他明显的内容块（标题、长文本、表格），且不是标题，则停止
                # 注意：有时标题会被误识别为 NarrativeText，所以上面先判断了 startswith
                if next_cat in ["Title", "Header", "Subheader", "NarrativeText", "ListItem", "Table"]:
                    if len(next_text) > 100: # 长文本肯定不是标题
                        break
                    # 短文本但不是 FigureCaption 且不以图开头，可能是正文的一部分，停止
                    if not (next_text.startswith("图") or next_text.lower().startswith("figure")):
                        break

            # 改进：每个 Image 元素只消耗一张图片，避免多图连排时第一张图吞掉所有图片
            # 查找当前页未插入的第一张图片
            target_img = None
            for name in image_map.get(page_num, []):
                if (page_num, name) not in inserted_images:
                    target_img = name
                    break
            
            if target_img:
                if caption:
                    # 尝试重命名图片文件以匹配标题
                    safe_name = sanitize_filename(caption)
                    if safe_name:
                        ext = os.path.splitext(target_img)[1]
                        new_filename = f"{safe_name}{ext}"
                        
                        # 处理重名
                        counter = 1
                        while (img_dir / new_filename).exists() and new_filename != target_img:
                            new_filename = f"{safe_name}_{counter}{ext}"
                            counter += 1
                        
                        old_path = img_dir / target_img
                        new_path = img_dir / new_filename
                        
                        if old_path.exists() and old_path != new_path:
                            old_path.rename(new_path)
                            target_img = new_filename

                    md_lines.append(f"![Image: {caption}](./images/{target_img})\n")
                    md_lines.append(f"*{caption}*\n")
                    image_captions[target_img] = caption
                else:
                    md_lines.append(f"![Image](./images/{target_img})\n")
                inserted_images.add((page_num, target_img))
        else:
            md_lines.append(text + "\n")

    out_md.write_text("\n".join(md_lines), encoding="utf-8")
    
    # 保存提取到的图片标题映射
    caption_path = workdir(kb_id, file_id) / "image_captions.json"
    caption_path.write_text(json.dumps(image_captions, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return {"markdown": out_md.name, "images_dir": "images"}

def encode_image(image_path):
    """Getting the base64 string"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")

def summarize_images(kb_id: str, file_id: str):
    """对提取的图片进行摘要"""
    img_dir = images_dir(kb_id, file_id)
    if not img_dir.exists():
        return []
    
    summary_path = workdir(kb_id, file_id) / "image_summaries.json"
    
    # 加载图片标题映射
    caption_map = {}
    caption_file = workdir(kb_id, file_id) / "image_captions.json"
    if caption_file.exists():
        try:
            caption_map = json.loads(caption_file.read_text(encoding="utf-8"))
        except:
            pass
    
    summaries = []
    # 使用 llava:7b 进行图片理解
    print(f"Start summarizing images for {file_id} using llava:7b (port 11436)...")
    try:
        chat = ChatOllama(model="llava:7b", temperature=0, base_url="http://127.0.0.1:11434")
    except Exception as e:
        print(f"Failed to load llava:7b: {e}")
        return []
    
    images = sorted(list(img_dir.glob("*.png")))
    
    # 定义单个图片处理函数
    def process_single_image(img_path):
        try:
            b64_img = encode_image(img_path)
            name = img_path.name
            page_num = 1
            if "page" in name:
                try:
                    p_str = name.split("page")[1]
                    if "_" in p_str:
                        page_num = int(p_str.split("_")[0])
                    else:
                        page_num = int(p_str.split(".")[0].replace("-", ""))
                except:
                    pass
            
            caption = caption_map.get(name, "")
            # 提示词为中文，要求生成中文描述
            prompt = "请详细描述这张图片的内容，重点关注图片中的文字、图表结构和关键信息，以便于后续的检索。"
            "请直接输出描述内容，不要包含'这张图片展示了'等废话,并且严格要求用中文。"
            if caption:
                prompt += f" 该图片的标题是: '{caption}'。请结合标题对图片进行描述。"

            # 每个线程独立实例化一个 ChatOllama 对象，避免潜在的并发冲突
            local_chat = ChatOllama(model="llava:7b", temperature=0, base_url="http://127.0.0.1:11434")
            msg = local_chat.invoke([
                HumanMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
                    ]
                )
            ])
            print(f"Summarized {name}")
            
            # 将标题强制拼接到摘要前，确保检索时能匹配到
            final_summary = msg.content
            if caption:
                final_summary = f"Caption: {caption}\nDescription: {final_summary}"

            return {
                "img_name": name,
                "summary": final_summary,
                "page_num": page_num
            }
        except Exception as e:
            print(f"Error summarizing {img_path.name}: {e}")
            return None

    # 使用 ThreadPoolExecutor 并发处理
    import concurrent.futures
    # 根据机器性能调整 max_workers，Ollama 并发能力取决于显存
    max_workers = 1 # 改为 1 避免多模态并发导致显存溢出重启
    print(f"Processing {len(images)} images with {max_workers} workers...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(process_single_image, images))
    
    # 过滤失败的结果
    summaries = [r for r in results if r is not None]
            
    summary_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    return summaries

def get_image_summaries(kb_id: str, file_id: str) -> List[Dict[str, Any]]:
    """获取图片摘要列表"""
    summary_path = workdir(kb_id, file_id) / "image_summaries.json"
    if summary_path.exists():
        try:
            return json.loads(summary_path.read_text(encoding="utf-8"))
        except:
            pass
    
    # Fallback: 如果没有摘要文件，构建基础列表
    img_dir = images_dir(kb_id, file_id)
    if not img_dir.exists():
        return []
    
    # 尝试加载 Captions
    caption_map = {}
    caption_file = workdir(kb_id, file_id) / "image_captions.json"
    if caption_file.exists():
        try:
            caption_map = json.loads(caption_file.read_text(encoding="utf-8"))
        except:
            pass

    fallback_list = []
    for img_file in sorted(img_dir.glob("*.png")):
        name = img_file.name
        # 尝试解析页码 (page1_img1.png)
        p_num = 0
        try:
            p_str = name.split("page")[1].split("_")[0]
            p_num = int(p_str)
        except:
            pass
        
        caption = caption_map.get(name, "")
        fallback_list.append({
            "img_name": name,
            "summary": caption or "(未生成详细摘要)",
            "page_num": p_num
        })
    
    return fallback_list

def save_image_summaries(kb_id: str, file_id: str, summaries: List[Dict[str, Any]]) -> bool:
    """保存图片摘要列表"""
    summary_path = workdir(kb_id, file_id) / "image_summaries.json"
    try:
        summary_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        print(f"Failed to save summaries: {e}")
        return False

def _olmocr_page(img_path: Path) -> str:
    """调用 olmocr 模型解析单页图片"""
    def to_data_uri(p: Path) -> str:
        with open(p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return f"data:image/png;base64,{b64}"

    content = [
        {
            "type": "text",
            "text": (
                "Convert this page into clean Markdown in natural reading order. "
                "Identify titles and sections and format them as Markdown headers (e.g., use '# ' for main titles like '1、...', '## ' for subsections like '1.1...', etc.). "
                "Remove headers/footers. "
                "Important: For tables, especially those with merged cells (complex nested headers), YOU MUST use HTML <table> syntax. "
                "Carefully calculate 'rowspan' and 'colspan' for every merged cell. "
                "Ensure that multi-level headers are represented by multiple <tr> rows. "
                "Preserve all cell content strictly, including units (e.g., 'm', '°C', '%') which might appear in separate rows. "
                "For simple tables without specific merging, you may use Markdown tables. "
                "Represent math as LaTeX ($...$ or $$...$$). "
                "Do not invent missing content."
            ),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": to_data_uri(img_path),
                "detail": "auto"
            },
        },
    ]

    payload = {
        "model": OLMOCR_MODEL,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "max_tokens": 4096,
    }

    try:
        r = requests.post(OLMOCR_ENDPOINT, json=payload, timeout=120)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Error in olmocr for {img_path}: {e}")
        return f"\n\n<!-- OCR ERROR: {e} -->\n\n"

def pdf_to_markdown_olmocr(kb_id: str, file_id: str):
    """使用 olmocr 模型将 PDF 转换为 Markdown"""
    # 1. 确保图片已提取 (用于 summarize_images)
    pdf_path = str(original_pdf_path(kb_id, file_id))
    img_dir = images_dir(kb_id, file_id)
    
    # 复用 fitz 提取图片逻辑，确保 images 目录下有图片
    with fitz.open(pdf_path) as doc:
        for page_num, page in enumerate(doc, start=1):
            for img_index, img in enumerate(page.get_images(full=True), start=1):
                xref = img[0]
                img_path = img_dir / f"page{page_num}_img{img_index}.png"
                if not img_path.exists(): # 避免重复提取
                    pix = fitz.Pixmap(doc, xref)
                    if pix.n < 5:
                        pix.save(str(img_path))
                    else:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                        pix.save(str(img_path))

    # 2. 遍历 pages/original 下的页面图片进行 OCR
    # 页面图片已在 run_full_parse_pipeline 中通过 render_original_pages 生成，此处无需重复渲染
    page_dir = dir_original_pages(kb_id, file_id)
    pages = sorted(list(page_dir.glob("page-*.png")))
    
    md_pages = []
    image_captions = {} # filename -> caption
    print(f"Starting olmocr parse for {file_id}, {len(pages)} pages...")

    # 获取所有图片并按页码分组
    page_images_map = {}
    if img_dir.exists():
        for img_file in img_dir.glob("*.png"):
            # 假设文件名格式 page{num}_img{idx}.png
            try:
                # 兼容 page1_img1.png 格式
                p_str = img_file.name.split("page")[1].split("_")[0]
                p_num = int(p_str)
                if p_num not in page_images_map:
                    page_images_map[p_num] = []
                page_images_map[p_num].append(img_file.name)
            except:
                pass

    # 简单串行
    for i, p in enumerate(pages, start=1):
        print(f"OCR processing {p.name}...")
        content = _olmocr_page(p)
        md_pages.append(content)

        # 尝试从 content 中提取图片标题
        # 简单的启发式：查找以 "图" 或 "Figure" 开头的行
        # 这里的 i 对应页码 (假设 pages 是按顺序 page-0001.png ...)
        current_page_imgs = sorted(page_images_map.get(i, []))
        
        if current_page_imgs:
            lines = content.split('\n')
            captions = []
            for line in lines:
                line = line.strip()
                # 匹配 "图 1", "图1", "Figure 1" 等，且长度不过长
                # 也可以匹配 "图X" 这种
                if (re.match(r'^(图|Figure)\s*\d+', line, re.IGNORECASE) or line.startswith("图")) and len(line) < 100:
                    captions.append(line)
            
            # 简单的 1对1 映射或顺序映射
            # 如果只有一个图片，取第一个找到的标题
            if len(current_page_imgs) == 1 and captions:
                image_captions[current_page_imgs[0]] = captions[0]
            # 如果有多个图片和多个标题，按顺序尝试映射
            elif len(current_page_imgs) > 1 and len(captions) > 0:
                for img_idx, img_name in enumerate(current_page_imgs):
                    if img_idx < len(captions):
                        image_captions[img_name] = captions[img_idx]
        
    full_md = "\n\n\\pagebreak\n\n".join(md_pages)
    
    out_md = markdown_output(kb_id, file_id)
    out_md.write_text(full_md, encoding="utf-8")

    # 重命名图片文件以匹配提取到的标题
    final_captions = {}
    for img_name, caption in image_captions.items():
        safe_name = sanitize_filename(caption)
        if safe_name:
            ext = os.path.splitext(img_name)[1]
            new_filename = f"{safe_name}{ext}"
            
            # 处理重名
            counter = 1
            while (img_dir / new_filename).exists() and new_filename != img_name:
                 new_filename = f"{safe_name}_{counter}{ext}"
                 counter += 1
            
            old_path = img_dir / img_name
            new_path = img_dir / new_filename
            
            if old_path.exists() and old_path != new_path:
                old_path.rename(new_path)
                final_captions[new_filename] = caption
            else:
                final_captions[img_name] = caption
        else:
            final_captions[img_name] = caption
            
    image_captions = final_captions

    # 保存提取到的图片标题映射
    caption_path = workdir(kb_id, file_id) / "image_captions.json"
    caption_path.write_text(json.dumps(image_captions, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return {"markdown": out_md.name, "images_dir": "images"}

def run_full_parse_pipeline(kb_id: str, file_id: str, method: str = "original", progress_callback=None) -> Dict[str, Any]:
    """
    完整流程：带有细粒度进度的文件解析管道
    """
    # 步骤1: 渲染原始页图 (0% - 15%)
    if progress_callback: progress_callback(5)
    render_original_pages(kb_id, file_id, progress_callback=lambda p: progress_callback(5 + int(p * 10)) if progress_callback else None)
    
    if method == "olmocr":
        if progress_callback: progress_callback(20)
        md_info = pdf_to_markdown_olmocr(kb_id, file_id)
        if progress_callback: progress_callback(70)
    else:
        # 步骤2: Unstructured 解析结构 (15% - 50%)
        if progress_callback: progress_callback(15)
        docs = unstructured_segments(kb_id, file_id, progress_callback=lambda p: progress_callback(15 + int(p * 35)) if progress_callback else None)
        
        # 步骤3: 渲染叠框图 (50% - 65%)
        render_parsed_pages_with_boxes(kb_id, file_id, docs, progress_callback=lambda p: progress_callback(50 + int(p * 15)) if progress_callback else None)
        
        # 步骤4: 渲染 markdown (65% - 80%)
        if progress_callback: progress_callback(65)
        md_info = pdf_to_markdown(kb_id, file_id, progress_callback=lambda p: progress_callback(65 + int(p * 15)) if progress_callback else None)
        
    # 步骤5: 总结图片 (80% - 95%)
    if progress_callback: progress_callback(80)
    summarize_images(kb_id, file_id)
    if progress_callback: progress_callback(95)

    images_list = []
    img_dir = images_dir(kb_id, file_id)
    if img_dir.exists():
        images_list = [f.name for f in img_dir.glob("*.png")]

    if progress_callback: progress_callback(100)
def delete_workdir(kb_id: str, file_id: str) -> bool:
    """删除某知识库下某文件的工作目录"""
    import shutil
    # 直接构造路径，避免调用 workdir() 产生创建目录的副作用
    d = kb_files_dir(kb_id) / file_id
    if d.exists():
        try:
            shutil.rmtree(d)
            return True
        except Exception as e:
            print(f"Error deleting workdir {d}: {e}")
            return False
    return True
