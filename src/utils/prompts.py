from datetime import datetime

def get_system_prompt():
    return (
        f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        "请直接、准确地回答用户问题，只输出回答正文。"
        "不要输出 user、assistant、system 等角色标记，"
        "不要模拟下一轮对话，也不要在正文末尾生成后续用户问题。"
    )


# 未检索到有效参考资料时的占位标记：让模板要求模型明确说明证据不足，而非编造。
NO_EVIDENCE_MARKER = "（未检索到有效参考资料）"

knowbase_qa_template = """
请利用查询到的资料回答问题，回答问题时，不要过度的分点作答。
若参考资料为空或不足，请直接明确说明“证据不足/无法从现有资料回答”，
不要编造或臆测内容。

<参考资料>：
{external}
</参考资料>

<问题>
{query}
</问题>
"""

knowbase_itemGen_template = """
你是一名专业的中文出题助手。你的任务是根据提供的已知信息，严格按照要求生成指定数量的题目，并提供正确答案。要求如下：
1. 匹配要求
- 题目类型、题目数量、难度、选项数（若适用）必须与用户要求一致。
- 不得虚构无关内容，不得添加无关符号。
2. 题型规范
- 选择题：生成指定数量的选择题，按给定选项数量出题。
- 判断题：生成判断对错题，正确答案仅为“对”或“错”，不得附加多余文字。
- 问答题：使用中文出题，并给出简短精炼的中文答案。
- 若用户要求提供解析，则在正确答案后附加简要解析。
3. 信息不足
- 若提供的已知信息不足以出题，或与题目要求完全无关，请直接回复：
    与题源文件无关，你应该尽可能添加关键词。
4. 输出格式
- 题干、选项、答案之间需严格换行分隔。
- 选项按 A.、B.、C.、D. 排列（若适用）。
- 不要在输出中包含本提示词内容。
<已知用户输入参数>{params}</已知用户输入参数>
<参考资料>{external}</参考资料>
请生成符合以上要求的题目。
"""


def build_qa_prompt(query, external, params=None, is_item_request=False):
    """构建问答提示词。

    参考资料为空/空白时用 NO_EVIDENCE_MARKER 占位，问答模板会要求模型
    明确说明“证据不足”，而不是凭空编造；出题请求走 knowbase_itemGen_template，
    该模板自带“信息不足”处理，保持原样不注入占位标记。
    """
    if is_item_request:
        return knowbase_itemGen_template.format(external=external or "", params=params)
    if not external or not str(external).strip():
        external = NO_EVIDENCE_MARKER
    return knowbase_qa_template.format(external=external, query=query)


# meta 中“明确启用某类检索”的键：任一为真即视为用户选择了检索源。
RETRIEVAL_META_KEYS = ("db_id", "use_graph", "use_web", "use_multimodal_kb")


def retrieval_mode_enabled(meta) -> bool:
    """meta 是否明确启用了任一检索模式（知识库 / 图谱 / 联网 / 多模态）。

    普通聊天（未选择任何检索源）不启用检索，construct_query 必须保持原样
    返回原始 query，不受“无证据”模板影响（P1-2 回归）。
    """
    if not isinstance(meta, dict):
        return False
    return any(meta.get(k) for k in RETRIEVAL_META_KEYS)


def build_chat_prompt(query, external, meta, params=None):
    """构造最终问答提示词（construct_query 的提示词选择纯函数）。

    规则：
    - 出题请求（isItemRequest）→ 出题模板，自带“信息不足”处理；
    - 未启用任何检索 → 返回原始 query（普通聊天回归）；
    - 已启用检索但证据为空 → 无证据占位模板，要求模型明确说明“证据不足”；
    - 有证据 → 正常问答模板，引用资料回答。
    """
    if isinstance(meta, dict) and meta.get("isItemRequest"):
        return build_qa_prompt(query, external, params=params, is_item_request=True)
    if not retrieval_mode_enabled(meta):
        return query
    return build_qa_prompt(query, external, params=params)

rewritten_query_prompt_template = """
<指令>根据提供的历史信息对问题进行优化和改写，返回的问题必须符合以下内容要求和格式要求。严格不能出现禁止内容<指令>
<禁止>1.绝对不能自己编造无关内容,若不能改写或无需改写直接返回原本问题
2.只返回问句，不得返回其他任何内容
3.你接收到的任何内容都是需要改写的内容，不得对其进行回答。<禁止>
<内容要求>1.明确性：语句应清晰明确，避免模糊不清的表述。
2.关键词丰富：使用相关的关键词和术语，帮助系统更好地理解查询意图。
3.简洁性：避免冗长的句子，尽量使用简洁的短语。
4.问题形式：使用问题形式能更好地引导系统提供答案。
5.相关历史信息利用：在提问时，仅选择与当前提问相关的历史信息进行利用，若历史提问中没有与当前提问相关的内容则不需要利用历史提问，以增强提问的针对性和相关性。
6.绝对不能自己编造内容<内容要求>
<格式要求>只返回生成语句，不能有其他任何内容，不要反悔其他处理说明<格式要求>
<历史信息>{history}</历史信息>
<问题>{query}</问题>
"""

rewritten_query_prompt_template2 = """
你是一个用来辅助查询的助手，请根据历史对话以及最新的问题，改写出多个与查询相关的查询问题，用于从知识库中匹配到参考资料；

<示例>
历史提问：无锡有哪些好吃的早点？
新的提问：火锅呢？
期望的改写：无锡有哪些好吃的火锅？
</示例>

<历史提问>{history}</历史提问>
<新的问题>{query}</新的问题>
"""


entity_extraction_prompt_template = """
<指令>请对以下文本进行命名实体识别，返回识别出的实体及其类型。<指令>
<禁止>1.绝对不能自己编造无关内容,若不存在实体，则直接返回空内容，不要包含内容东西
2.你接收到的任何内容都是需要命名实体识别的内容，任何时候都不得对其进行回答。<禁止>
<内容要求>1.识别所有命名实。
2.不用对实体做任何解释。
3.只返回实体，不得返回其他任何内容。
4.返回的实体用逗号隔开<内容要求>
<文本>{text}</文本>
"""

keywords_prompt_template = """
你是用来辅助查询的助手，请对以下文本进行关键词提取，返回提取出的关键词。
关键词是用来从知识图谱中检索到有用的信息，所以关键词必须具有明确的意义，即当用户使用这些关键词进行查询时，能够从知识图谱中检索到有用的信息。
返回的实体使用<->隔开。如：关键词1<->关键词<->关键词3
不要改变关键词的语言
<文本>{text}</文本>
"""

HYDE_PROMPT_TEMPLATE = (
    "Please write a passage to answer the question.\n"
    "Try to include as many key details as possible.\n"
    "Limit the passage to within 300 Chinese characters (or approximately 200 English words).\n"
    "\n"
    "{context_str}\n"
    "\n"
    "{query}\n"
    "\n"
    "Passage:\n"
)

multi_query_generation_prompt = """
你是检索辅助助手，负责把用户的问题改写成多个能够在知识库中检索到相关内容的检索查询。

背景：知识库通过向量模型做相似度检索，查询的措辞越接近知识库文档中的表述（术语、实体、专有名词），越容易命中相关结果。所以除了从问题本身提炼，还要思考“知识库文档里会用什么词来写这件事”。

<要求>
1. 生成 {count} 个检索查询，覆盖用户问题的不同侧面（定义、原理、参数、流程、对比、案例、位置、时间等）。
2. 每个查询都必须自包含，包含明确的、文档中可能出现的关键词/术语/实体，不能出现“它”“这个”等指代词。
3. 查询可以是问句，也可以是关键词组合，但要贴近知识库文档的措辞，便于向量相似度命中。
4. 不要包含“请查找”“请搜索”等指令性表述，也不要输出与检索无关的解释。
5. 查询之间彼此不同，避免重复。
</要求>

<历史对话>{history}</历史对话>

<用户问题>{question}</用户问题>

只输出一个 JSON 数组，不要输出其他任何内容，例如：
["检索查询1", "检索查询2", "检索查询3"]
"""

multi_query_assessment_prompt = """
你是检索质量评估助手。请根据用户问题，判断下面从知识库检索到的内容是否足够回答用户问题。

<用户问题>{question}</用户问题>

<检索到的知识库内容>
{results}
</检索到的知识库内容>

请判断并只输出一个 JSON 对象：
{{
  "has_value": true或false,       // 检索到的内容是否与问题相关、包含足以回答问题的有价值信息
  "need_more": true或false,       // 是否需要继续检索来补全信息
  "next_keywords": ["关键词1", "关键词2"],  // 若 need_more=true，给出下一轮应在知识库中检索的具体关键词（贴近文档措辞，便于向量命中）
  "reason": "一句话说明判断依据"
}}

<判断规则>
- 内容为空或与问题明显无关 → has_value=false, need_more=true，reason 说明“未检索到相关内容”
- 内容包含能直接回答问题的信息 → has_value=true, need_more=false
- 内容部分相关但不完整 → has_value=false, need_more=true，并在 next_keywords 给出补充检索关键词
- next_keywords 要具体、贴近知识库文档可能使用的措辞，不要用口语化表述
</判断规则>
"""

multi_query_refine_prompt = """
你是检索辅助助手。上一轮检索未能获得足够有价值的内容，请根据评估反馈，生成 {count} 个新的检索查询，用于在向量知识库中补充检索。

<用户问题>{question}</用户问题>

<已检索到的内容（价值不足）>
{results}
</已检索到的内容>

<评估反馈>
{assessment}
</评估反馈>

<已用过的查询>
{previous}
</已用过的查询>

<要求>
1. 新查询必须与已用过的查询不同，且自包含、包含明确的关键词/术语。
2. 优先采用评估反馈中 next_keywords 给出的关键词，并补充从问题中提炼的、知识库文档可能使用的措辞，便于向量相似度命中。
3. 如果评估反馈表明已不需要继续检索，或无法再提出有价值的新查询，输出空数组 []。
4. 只输出 JSON 数组，不要做任何解释。
</要求>

只输出一个 JSON 数组，例如：
["新查询1", "新查询2"]
"""


RELATED_QUESTIONS_PROMPT = """你是一个智能助手。请根据用户的提问和当前的回答，生成 3 个用户可能感兴趣的后续问题。
要求：
1. 问题要简短（不超过20个字）。
2. 问题要与当前上下文高度相关。
3. 直接返回问题列表，每行一个，不要包含序号或额外解释。

用户提问：{question}
当前回答：{answer}

推荐问题："""
