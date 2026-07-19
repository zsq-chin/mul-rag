import os
import pandas as pd
import json
import time
import re
import fitz  # PyMuPDF
import base64
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
import shutil
import logging

# Use langchain_ollama as seen in other files
try:
    from langchain_ollama import ChatOllama, OllamaEmbeddings
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document
except ImportError:
    # Fallback or handle error
    ChatOllama = None

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
EXTRACTION_DIR_NAME = "extractions"

# Import from index_service for unified processing
from services.index_service import split_markdown, load_embeddings

# OLMOCR Config (Mirrors pdf_service.py)
# Changed port to 8005 to avoid conflict with other users
OLMOCR_ENDPOINT = os.getenv("OLM_OCR_ENDPOINT", "http://localhost:8005/v1/chat/completions")
OLMOCR_MODEL = "olmocr"

def _olmocr_page_content(img_path: Path) -> str:
    """Call olmocr model to parse a single page image."""
    def to_data_uri(p: Path) -> str:
        with open(p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return f"data:image/png;base64,{b64}"

    content = [
        {
            "type": "text",
            "text": (
                "Convert this page into clean Markdown in natural reading order. "
                "Identify titles and sections and format them as Markdown headers. "
                "Remove headers/footers. "
                "Important: For tables, especially those with merged cells (complex nested headers), YOU MUST use HTML <table> syntax. "
                "Carefully calculate 'rowspan' and 'colspan' for every merged cell. "
                "Ensure that multi-level headers are represented by multiple <tr> rows. "
                "Preserve all cell content strictly, including units (e.g., 'm', '°C', '%') which might appear in separate rows. "
                "For simple tables without specific merging, you may use Markdown tables. "
                "Represent math as LaTeX. Do not invent missing content."
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
    except requests.exceptions.ConnectionError:
        logger.error(f"Connection refused to {OLMOCR_ENDPOINT}. Service not started.")
        return "\n\n<!-- OCR FAIL: OLMOCR Service (port 8005) is NOT running. Please start it. -->\n\n"
    except Exception as e:
        logger.error(f"Error in olmocr for {img_path}: {e}")
        return f"\n\n<!-- OCR ERROR: {e} -->\n\n"

def get_extraction_dir(kb_id: str) -> Path:
    """Get the directory for extraction tasks within a KB."""
    # We can store extractions in the KB folder or a separate one.
    # User said "Select knowledge base to store extraction content".
    # Let's put it in data/<kb_id>/extractions/
    d = DATA_ROOT / kb_id / EXTRACTION_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d

def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r'\s+', "_", name).strip()
    return name[:100]

def save_uploaded_file_for_extraction(file_content: bytes, filename: str, kb_id: str) -> str:
    """Save the uploaded file to disk for processing."""
    extraction_dir = get_extraction_dir(kb_id)
    safe_name = sanitize_filename(filename)
    # Create a unique folder for this extraction task to avoid collisions
    task_id = f"task_{int(time.time())}_{safe_name}"
    task_dir = extraction_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = task_dir / safe_name
    with open(file_path, "wb") as f:
        f.write(file_content)
        
    return str(file_path)


from services.pdf_service import partition_pdf
from unstructured.documents.elements import Table

def parse_file_content(file_path: str, method: str = "original") -> str:
    """
    Parse file content to text with structure preserved (Markdown/HTML). 
    Supports PDF for now.
    methods: 'original' (Unstructured Hi-Res), 'olmocr' (Vision Model).
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    text_content = ""

    if suffix == ".pdf":
        try:
            if method == "olmocr":
                # OLMOCR Strategy
                logger.info(f"Using OLMOCR parsing for {file_path}")
                md_pages = []
                with fitz.open(path) as doc:
                    # Save pages for checking later, DO NOT DELETE
                    img_dir = path.parent / "pages"
                    img_dir.mkdir(exist_ok=True)
                    
                    for page_num, page in enumerate(doc):
                        # Render page to image
                        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3)) # 3x zoom for better OCR / details
                        img_out = img_dir / f"page_{page_num + 1}.png" # 1-based naming for simplicity
                        pix.save(str(img_out))
                        
                        # Call OCR
                        page_text = _olmocr_page_content(img_out)
                        
                        # Inject Page Header for RAG tracking (Header 1)
                        # Demote existing headers (# -> ##) to make Page Header the top level
                        demoted_text = re.sub(r'(^|\n)# ', r'\1## ', page_text)
                        demoted_text = re.sub(r'(^|\n)## ', r'\1### ', demoted_text)
                        
                        marked_content = f"# Page {page_num + 1}\n\n{demoted_text}"
                        md_pages.append(marked_content)
                    
                    # Do not cleanup temp images anymore, we need them for UI preview
                    # try:
                    #    shutil.rmtree(img_dir)
                    # except:
                    #    pass
                
                text_content = "\n\n".join(md_pages)
                
            else:
                # Default Original Strategy (Unstructured)
                elements = partition_pdf(
                    filename=str(path),
                    strategy="hi_res",              
                    infer_table_structure=True,     
                    ocr_languages="chi_sim+eng",    
                )
                
                # Group by page
                pages_content = {}
                for el in elements:
                    p_num = el.metadata.page_number or 1
                    if p_num not in pages_content:
                        pages_content[p_num] = []
                    
                    if isinstance(el, Table) and hasattr(el.metadata, "text_as_html"):
                         pages_content[p_num].append(el.metadata.text_as_html)
                    else:
                         pages_content[p_num].append(str(el))
                
                # Sort and Join
                processed_text = []
                for p_num in sorted(pages_content.keys()):
                    p_text = "\n\n".join(pages_content[p_num])
                    # Wrap in Page Header
                    processed_text.append(f"# Page {p_num}\n\n{p_text}")
                
                text_content = "\n\n".join(processed_text)

        except ImportError as e:
            return f"Error: dependency missing {e}"
        except Exception as e:
            logger.error(f"Structured Parsing failed: {e}. Fallback to fitz.")
            try:
                # import fitz  <-- Removed to prevent local variable shadowing issue
                doc = fitz.open(path)
                for i, page in enumerate(doc):
                    text_content += f"# Page {i+1}\n\n" + page.get_text() + "\n"
            except Exception as e2:
                 return f"Error parsing PDF: {str(e2)}"

    elif suffix in [".txt", ".md", ".csv", ".json"]:
        try:
            text_content = path.read_text(encoding="utf-8")
        except Exception as e:
            text_content = f"Error reading text file: {e}"
    else:
        text_content = f"Unsupported file format: {suffix}"

    return text_content

def locate_relevant_content(full_text: str, query: str) -> (str, List[int]):
    """
    Locate content in source file using Vector Search (RAG).
    Returns (combined_text, list_of_page_numbers).
    """
    if not full_text:
        return "", []
    if not query:
        return full_text[:100000], []

    try:
        # 1. Split text using the standard Markdown splitter from index_service
        # It splits on #, ##, ###. Since we injected "# Page X", 
        # docs[i].metadata['Header 1'] should contain "Page X"
        docs = split_markdown(full_text)
        
        if not docs:
            return full_text[:100000], [] 

        # 2. Embeddings
        embeddings = load_embeddings() 
        
        # 3. Create temporary FAISS index
        vectorstore = FAISS.from_documents(docs, embeddings)
        
        # 4. Search
        retrieved_docs = vectorstore.similarity_search(query, k=3)
        
        if not retrieved_docs:
             return full_text[:100000], []

        # 5. Extract Pages and Content
        relevant_chunks = []
        page_nums = set()
        
        for d in retrieved_docs:
            relevant_chunks.append(d.page_content)
            # Try to parse "Page X" from metadata or content
            # Metadata key 'Header 1' is from MarkdownHeaderTextSplitter
            h1 = d.metadata.get("Header 1", "")
            if h1.startswith("Page "):
                try:
                    p = int(h1.replace("Page ", "").strip())
                    page_nums.add(p)
                except:
                    pass
        
        combined_text = "\n\n...\n\n".join(relevant_chunks)
        
        return combined_text, sorted(list(page_nums))

    except Exception as e:
        logger.error(f"Vector location failed: {e}. Fallback to full text.")
        return full_text[:100000], []


def llm_extract(content: str, instruction: str, model_name: str = "qwen2.5:latest") -> List[Dict[str, Any]]:
    """
    Call LLM to extract data.
    """
    if not content:
        return []

    if not ChatOllama:
       raise ImportError("langchain_ollama not installed")

    # Use custom Ollama instance on GPU 5 (port 11436)
    # Use environment variable or host.docker.internal for docker access
    base_url = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    
    llm = ChatOllama(
        model=model_name, 
        temperature=0,
        base_url=base_url
    ) 

    # Prompt engineering
    prompt_text = f"""
    You are a data extraction assistant.
    
    User Instruction: {instruction}
    
    Source Content (May contain Markdown text, Markdown tables, or HTML tables):
    {content[:60000]}  
    
    Task: Extract the information requested in 'User Instruction' from 'Source Content'.
    
    Guidelines:
    1. If the source contains tables (HTML '<table>...</table>' or Markdown '|...|'), carefully parse the rows and columns.
    2. Pay special attention to 'rowspan' and 'colspan' attributes in HTML tables, as they indicate merged cells. replicate the value of the merged cell across all covered rows/columns.
    3. Format the output as a JSON array of objects, where each object represents a row or a record.
    4. Use the table headers or field names from the instruction as keys in the JSON objects.
    5. Return ONLY a JSON array. Example: [{{"field1": "value1", "field2": "value2"}}, ...]
    6. If nothing is found matching the instruction, return [].
    7. Do not output any markdown code blocks (like ```json), just the raw JSON string.
    """
    
    messages = [
        SystemMessage(content="You are a precise data extraction engine. Output valid JSON only."),
        HumanMessage(content=prompt_text)
    ]
    
    try:
        response = llm.invoke(messages)
        content_str = response.content.strip()
        # Clean up markdown if present
        if content_str.startswith("```"):
            content_str = content_str.strip("`").replace("json", "").strip()
        
        data = json.loads(content_str)
        if isinstance(data, dict):
            data = [data]
        return data
    except json.JSONDecodeError:
        logger.error(f"Failed to decode JSON from LLM: {response.content}")
        return [{"error": "Failed to parse LLM output", "raw": response.content}]
    except Exception as e:
        logger.error(f"LLM extraction error: {e}")
        return [{"error": str(e)}]


def export_data(data: List[Dict[str, Any]], format_type: str, output_path: str):
    """
    Save data to Excel or CSV.
    """
    df = pd.DataFrame(data)
    
    if format_type.lower() == "excel":
        df.to_excel(output_path, index=False)
    elif format_type.lower() == "csv":
        df.to_csv(output_path, index=False)
    elif format_type.lower() == "json":
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    else:
        raise ValueError("Unsupported format")

