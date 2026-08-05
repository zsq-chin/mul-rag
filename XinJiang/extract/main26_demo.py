import json
import logging
from typing import  List, Generator,  Optional, Any
from json_repair import repair_json


from llm import get_ollama_response_local, get_ollama_response
from unitls import get_attr, get_attr_unitless
from read_doc3 import read
from filter_util import value_dict, synonym_dict,filter,clue_dict
from dataclasses import dataclass, field
from typing import Set, Tuple, Dict

# 配置模块


@dataclass
class AppConfig:
    MAX_RETRIES: int = 3  # 没问题，直接赋值
    # 使用 default_factory 来初始化 Tuple
    CONTENT_TYPES: Tuple[str, str] = field(default_factory=lambda: ("paragraph", "table"))
    # 使用 default_factory 来初始化 Set
    INVALID_VALUES: Set[str] = field(default_factory=lambda: {"无", "未提及", "未提供", "未知", "None", "", "无数据"})
    # 使用 default_factory 来初始化 Tuple
    SHEET_NAMES: Tuple[str, ...] = field(default_factory=lambda: ("基础表", "设计表", "施工表", "生产表"))
    # 使用 default_factory 来初始化 Dict
    PROMPT_TEMPLATES: Dict[str, str] = field(default_factory=lambda: {
        "keyword_prompt": """
        【任务指令】
        你当前的任务是从{well}井的压裂设计文档中提取指定的关键词，并将其交给他人提取数值。
        
        【文档内容】
        {info}
        
        【关键词信息】
        - 待提取关键词：{keywords}
        - 同义词字典：{synonyms}
          注：同义词字典仅提供部分同义词示例，需结合上下文判断其他可能的同义词。
        - 取值参考字典：{values}
          注：若文档中出现与取值参考字典格式一致的值，即使未直接提及关键词，也应提取并映射回主关键词。
          其余关键词取值为纯数字，非百分比。
        - 提示字典：{clues}
          注意：提示字典中提供有关字段的提示供参考
        【处理要求】
        1. **严格匹配**：仅返回文档中实际出现的关键词，区分大小写及标点符号。
        2. **同义词映射**：若匹配到同义词，需转换回主关键词并返回。
        3. **完整返回**：主关键词若带有特殊符号（如(m)、(%)、_max、_avg等），需完整返回。
        4. **排除无效数据**：拼写错误、部分匹配或未出现的关键词不返回。
        5. **去重处理**：每个主关键词仅返回一次。
        
        【结果格式】
        返回 JSON 字符串，示例如下：
        {{
            "keywords": ["大地坐标X", "A测深(m)", "Ⅰ类油层(m)"]
        }}
        如无匹配，返回：
        {{
            "keywords": []
        }}
        
        【执行步骤】
        1. 扫描文档内容，处理结构信息（如段落、表格等）。
        2. 检查主关键词和同义词，按要求提取并映射。
        3. 根据取值参考字典匹配潜在关键词并验证。
        4. 去重并验证输出，确保结果准确。
        """,
    "value_prompt": """
    **任务目标**：从{well}井的压裂设计文档中提取出{well}井的字段及其对应的数值。请按照以下步骤执行任务：

    **步骤1**：检查字段列表中的每个字段是否出现在文档内容中。如果出现属于其它井的字段，请将其忽视。

    **步骤2**：对于出现的字段，返回其数值。请确保结果仅包括那些实际出现在文档中的字段。

    **输入信息**：
    字段列表：
    ```
    {question}
    同义词字典：{synonyms}
    注：同义词字典中列出了要提取字段的部分同义词，供你参考，因为字段可能以同义词的形式出现在文档内容中，需要注意的是，字段可能以各种形式的同义词出现，不可能全部提供给你，需要你自己全力去判断。
    取值参考字典：{values}
    注：取值参考字典中给出的是部分特殊的字段的取值格式，也需要格外注意，其余字段的取值均为单个数字，必须注意不可能是带%的百分比和取值范围
    取值提示字典：{clues}
    文档内容：
    ```
    ================================================
    {info}
    ================================================

    **输出格式要求**：
    - 必须返回一个 JSON 格式的对象，仅包含在文档中出现的字段及其数值。千万不要返回形如`[{{}},{{}}]`的JSON数组，这样会导致提取出错
    - 如果没有提取到任何字段，请输出空对象：`{{}}`。
    - 示例：
      ```json
      {{"裂缝长度avg": "45.6", "压裂液用量(m3)": "1500","石英砂(70/140目)": "200"}}
      ```

    **注意**：仅返回文档中实际存在的字段且以字段列表中的标准字段名返回，不要是标准字段的同义词等形式。
        """
    })
class DocumentProcessor:
    def __init__(self, config: AppConfig):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

    def process_document(self, filename: str, sheet_index: int = 0) -> Dict[str, Any]:
        """主处理流程"""
        try:
            # 初始化处理上下文
            context = ProcessingContext(sheet_index, self.config)

            # 读取并过滤内容
            filtered_content = self._read_and_filter_content(filename, context)

            # 执行核心处理流程
            return self._process_content(context, filtered_content)
        except Exception as e:
            self.logger.error(f"文档处理失败: {str(e)}", exc_info=True)
            raise

    def _read_and_filter_content(self, filename: str, context: 'ProcessingContext') -> Generator:
        """读取并过滤文档内容"""
        paragraphs, tables = read(filename)
        return filter_content(
            context.attributes_unitless,
            paragraphs,
            tables,
            context.sheet_index
        )

    def _process_content(self, context: 'ProcessingContext', filtered_content: Generator) -> Dict[str, Any]:
        """处理过滤后的内容"""
        for content_type, content in filtered_content:
            if not context.remaining_fields:
                break
            # print(f"当前的上下文是：\n{content}")
            # 关键词提取阶段
            keyword_extractor = KeywordExtractor(context)
            keyword_list = keyword_extractor.extract(content)

            if keyword_list:
                # 值提取阶段
                value_extractor = ValueExtractor(context)
                extract_info = value_extractor.extract(content, keyword_list)

                # 更新提取结果
                context.update_results(extract_info)

        return context.all_info


class ProcessingContext:
    """处理上下文，维护处理过程中的状态"""

    def __init__(self, sheet_index: int, config: AppConfig):
        self.sheet_index = sheet_index
        self.config = config
        self._init_attributes()
        self._init_dictionaries()
        self.all_info: Dict[str, Any] = {}
        self.remaining_fields: Set[str] = set(self.attributes)

    def _init_attributes(self):
        """初始化属性配置"""
        self.attributes = get_attr("水平井压裂数据管理.xlsx", self.config.SHEET_NAMES)[self.sheet_index]
        self.process_attributes()
        self.attributes_unitless = get_attr_unitless("水平井压裂数据管理.xlsx", self.config.SHEET_NAMES)[
            self.sheet_index]

    def _init_dictionaries(self):
        """初始化字典配置"""
        self.value_dict = value_dict(self.sheet_index)
        self.synonym_dict = synonym_dict(self.sheet_index)
        self.clue_dict=clue_dict(self.sheet_index)

    def update_results(self, extract_info: Dict):
        """更新提取结果"""
        updater = FieldUpdater(self)
        updater.update_fields(extract_info)
    def process_attributes(self):
        attributes_unnecessary=["创建日期","创建用户","更新日期","更新用户","井号代码","压裂设计人","压裂设计人","设计完成时间","设计完成时间","审批完成时间","井号","年度","是否审核已通过","记录日期","区块负责人"]
        for attribute_unnecessary in attributes_unnecessary:
            if attribute_unnecessary in self.attributes:
                self.attributes.remove(attribute_unnecessary)

class KeywordExtractor:
    """关键词提取处理器"""

    def __init__(self, context: ProcessingContext):
        self.context = context
        self.logger = logging.getLogger(self.__class__.__name__)

    def extract(self, content: str) -> List[str]:
        """执行关键词提取流程"""
        prompt = self._build_prompt(content)
        # print(prompt)
        response = self._get_llm_response(prompt)
        # print(response)
        keywords=self._parse_response(response)
        keywords=self.get_current_keyword(keywords)
        return self.keywords_process(keywords)

    def _build_prompt(self, content: str) -> str:
        """构建提示模板"""
        return self.context.config.PROMPT_TEMPLATES["keyword_prompt"].format(
            well="J10054_H",
            keywords=self.context.remaining_fields,
            info=content,
            synonyms=self.context.synonym_dict,
            values=self.context.value_dict,
            clues=self.context.clue_dict
        )

    def _get_llm_response(self, prompt: str) -> str:
        """获取LLM响应（带重试机制）"""
        return get_ollama_response_with_retry(prompt, self.context.config.MAX_RETRIES)

    # def _parse_response(self, response: str) -> List[str]:
    #     """解析LLM响应"""
    #     try:
    #         print(json.loads(repair_json(response)))
    #         res = json.loads(repair_json(response)).get("keywords", [])
    #         return res
    #     except json.JSONDecodeError as e:
    #         self.logger.error(f"关键词解析失败: {str(e)}")
    #         return []

    def _parse_response(self, response: str) -> List[str]:
        """解析LLM响应"""
        try:
            data = json.loads(repair_json(response))
            self.logger.info(f"可能可以提取的关键字列表：{data}")
            # 如果解析到的数据是列表，将其包装成字典
            if isinstance(data, list):
                if len(data)==0:
                    data = {"keywords": data}
                elif len(data)==2:
                    data = data[0]
                else:
                    data = {"keywords": data}

            return data.get("keywords", [])
        except json.JSONDecodeError as e:
            self.logger.error(f"关键词解析失败: {str(e)}")
            return []
        except Exception as e:
            self.logger.error(f"关键词解析失败: {str(e)}")
            return []

    def get_current_keyword(self,keyword_list: list):
        current_keyword = []
        for keyword in keyword_list:
            if keyword in self.context.remaining_fields:
                current_keyword.append(keyword)
                continue
            for key, value in self.context.synonym_dict.items():
                if keyword in value:
                    current_keyword.append(key)
        return current_keyword
    def keywords_process(self,keywords):
        """
        该方法通过添加人工规则来补充大语言模型识别当前可提取的关键字的不足。具体规则如下：将通常一起出现的字段编为“关键字组”。
        若可提取的关键字列表中包含关键字组中的任意一个关键字，则将该关键字组中未出现在可提取的关键字列表
        中的其他关键字一并加入列表。此方法可有效弥补大语言模型在关键字提取时的遗漏，确保提取结果的完整性。
        :param keywords:
        :return:
        """



        common_keywords_list=[]
        if self.context.sheet_index == 0:
            common_keywords_list=[["井型","井别","目的层系"],['大地坐标X', '大地坐标Y', '地面海拔(m)', '造斜点(m)', 'A测深(m)', 'A垂深(m)', 'B测深(m)', 'B垂深(m)','井身结构'],['油层套管','井身结构'],['方案产能(t/d)', '部署产能(t/d)', '标定产能(t/d)'],[ 'Ⅰ类油层(m)', 'Ⅱ类油层(m)', 'Ⅲ类油层(m)','So_min(%)','So_max(%)','So_avg(%)'],['孔隙度(min)', '孔隙度(max)', '孔隙度(avg)'],["地层原油密度g/cm³","地层原油粘度g/cm³"],["脆性指数(avg)","泊松比_avg","杨氏模量_avg（Gpa）","最小水平主应力MPa"]]
            pass
        elif self.context.sheet_index == 1:
            common_keywords_list=[["水平井段","改造段长(m)","最大分簇","段数","簇数","均段间距","均簇间距"],["费用预算(万元)","公司限额(万元)"],["前置液比(%)","均砂比(%)","滑溜水、低粘比例(%)","胍胶压裂液滑溜水(m³)","胍胶压裂液原液(m³)","设计总液量","石英砂(100/200目)","石英砂(70/140目)","石英砂(50/140目)","石英砂(40/70目)","石英砂(30/50目)","石英砂(20/40目)"],[]]


            pass

        elif self.context.sheet_index == 2:
            common_keywords_list=[]
            pass

        elif self.context.sheet_index == 3:
            common_keywords_list=[]
            pass
        for common_keywords in common_keywords_list:
            if any(common_keyword in keywords for common_keyword in common_keywords):
                for common_keyword in common_keywords:
                    if common_keyword not in keywords:
                        keywords.append(common_keyword)

        self.logger.info(f"处理后可能可以提取的关键字列表：{keywords}")
        return keywords


class ValueExtractor:
    """数值提取处理器"""

    def __init__(self, context: ProcessingContext):
        self.context = context
        self.logger = logging.getLogger(self.__class__.__name__)

    def extract(self, content: str, keyword_list: List[str]) -> Dict:
        """执行数值提取流程"""
        prompt = self._build_prompt(content, keyword_list)
        print(prompt)
        response = self._get_llm_response(prompt)
        return self._parse_response(response)
    def get_keyword_synonyms_values_clues(self,keywords):
        keyword_values = {}
        keyword_synonyms = {}
        keyword_clues={}
        synonyms=self.context.synonym_dict
        values=self.context.value_dict
        clues=self.context.clue_dict
        try:
            if isinstance(keywords[0],dict):
                keywords=keywords[0].get("keywords")
            for keyword in keywords:
                if keyword in synonyms.keys():
                    keyword_synonyms[keyword] = synonyms[keyword]
                if keyword in values.keys():
                    keyword_values[keyword] = values[keyword]
                if keyword in clues.keys():
                    keyword_clues[keyword]=clues[keyword]
            return keyword_synonyms,keyword_values,keyword_clues
        except Exception as e:
            self.logger.error(f"在获取需要提取关键词{keywords}对应的同义词字典和取值格式字典时出错：{e}")
            return synonyms,values,clues

    def _build_prompt(self, content: str, keywords: List[str]) -> str:
        """构建数值提取提示"""
        keyword_synonyms,keyword_values,keyword_clues=self.get_keyword_synonyms_values_clues(keywords)
        return self.context.config.PROMPT_TEMPLATES["value_prompt"].format(
            well="J10054_H",
            question=keywords,
            info=content,
            synonyms=keyword_synonyms,
            values=keyword_values,
            clues=keyword_clues
        )

    def _get_llm_response(self, prompt: str) -> str:
        """获取LLM响应"""
        return get_ollama_response_with_retry(prompt, self.context.config.MAX_RETRIES)

    def _parse_response(self, response: str) -> Dict:
        """解析响应结果"""
        try:
            return json.loads(repair_json(response))
        except json.JSONDecodeError as e:
            self.logger.error(f"数值解析失败: {str(e)}")
            return {}
    def values_process(self,values):
        """
        该方法通过添加人工规则来补充大语言模型提取关键字对应的数值时出现的不足。对那些出现很不合理取值的关键字进行抛弃。
        :param values:
        :return:
        """

        pass

class FieldUpdater:
    """字段更新处理器"""

    def __init__(self, context: ProcessingContext):
        self.context = context
        self.logger = logging.getLogger(self.__class__.__name__)

    def update_fields(self, extract_info: Dict):
        """更新字段信息"""
        try:
            print(extract_info)
            if isinstance(extract_info,list):
                extract_info=extract_info[0]
            for field, value in extract_info.items():
                if self._is_invalid_value(value):
                    continue

                # 直接匹配字段
                if self._update_direct_field(field, value):
                    continue

                # 同义词匹配
                self._update_synonym_field(field, value)
        except Exception as e:
            logging.warning(f"更新字段信息时出错：{str(e)}")


    def _is_invalid_value(self, value: Any) -> bool:
        """验证值有效性"""
        return str(value).strip() in self.context.config.INVALID_VALUES

    def _update_direct_field(self, field: str, value: Any) -> bool:
        """直接字段更新"""
        if field in self.context.remaining_fields:
            self._store_field(field, value)
            return True
        return False

    def _update_synonym_field(self, field: str, value: Any):
        """同义词字段更新"""
        if target_field := self._match_synonym(field):
            self._store_field(target_field, value)

    def _match_synonym(self, field: str) -> Optional[str]:
        """同义词匹配"""
        for target_field, synonyms in self.context.synonym_dict.items():
            if field in synonyms and target_field in self.context.remaining_fields:
                return target_field
        return None

    def _store_field(self, field: str, value: Any):
        """存储字段并更新状态"""
        self.context.all_info[field] = value
        self.context.remaining_fields.discard(field)
        self.logger.info(f"成功提取字段: {field} = {value}")


# 公共工具函数
def get_ollama_response_with_retry(prompt: str, max_retries: int) -> str:
    """带重试机制的LLM请求"""
    for attempt in range(1, max_retries + 1):
        try:
            response = get_ollama_response(prompt)
            if response.status_code == 200:
                return response.json()["response"]
        except Exception as e:
            logging.warning(f"API请求失败，第{attempt}次重试: {str(e)}")
    raise ConnectionError(f"API请求失败，已达最大重试次数: {max_retries}")


def filter_content(attributes: List[str], paragraphs: List[str], tables: List[str], sheet_index: int) -> Generator:
    """生成过滤后的内容"""
    filtered_para, filtered_tab = filter(attributes, paragraphs, tables, sheet_index)
    return generate_alternate_contents(filtered_para, filtered_tab)


def generate_alternate_contents(paragraphs: List[str], tables: List[str]) -> Generator:
    """交替生成不同类型的内容"""
    for p, t in zip(paragraphs, tables):
        yield ("paragraph", p)
        yield ("table", t)


# 使用示例
if __name__ == "__main__":
    # 初始化配置
    config = AppConfig()

    # 设置日志
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # 执行处理
    processor = DocumentProcessor(config)
    result = processor.process_document("J10054_H井桥塞-射孔联作分段压裂设计 -审批版.docx", 0)
    print("最终提取结果:", result)
    print("提取结果数：",len(result))