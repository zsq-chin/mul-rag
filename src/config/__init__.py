import os
import json
import yaml
from pathlib import Path
from src.utils.logging_config import logger

DEFAULT_MOCK_API = 'this_is_mock_api_key_in_frontend'


# SimpleConfig 类 - 基础配置字典类
"""
提供字典形式的配置存储，并支持属性方式访问配置项
实现特点：
- 自动处理键名格式（转为小写）
- 支持字典和属性两种访问方式
- 可转换为JSON字符串
"""

"""__key内部方法：统一处理键名格式，空键转为空字符串"""
"""__str__将配置转换为JSON格式字符串"""
"""__setattr__通过属性方式设置配置项"""
"""__getattr__通过属性方式获取配置项"""
"""__dict__返回纯字典格式的配置（过滤内部属性）"""

# Config 类 - 主配置管理类
"""
系统配置管理中心，功能包括：
- 管理默认配置项
- 支持JSON/YAML格式配置文件
- 处理模型提供商和模型信息
- 自动检查环境变量和配置有效性
"""

"""__init__初始化配置系统，加载默认配置和模型信息"""
    # 初始化父类
    # 设置配置文件路径
    # 加载模型信息
    # 添加默认配置项

"""add_item
添加新的配置项
参数：
- key: 配置项名称
- default: 默认值
- des: 配置项描述（可选）
- choices: 可选值列表（可选）
"""

"""_update_models_from_file
从模型配置文件加载模型信息
加载顺序：
1. 读取 src/static/models.yaml 基础配置
2. 尝试读取 src/static/models.private.yml 私有配置
3. 合并配置信息到 model_names, embed_model_names, reranker_names
"""

"""_save_models_to_file
保存当前模型信息到私有配置文件
保存位置：src/static/models.private.yml
保存内容：
- MODEL_NAMES: 模型提供商信息
- EMBED_MODEL_INFO: Embedding模型信息
- RERANKER_LIST: 重排序模型信息
"""

"""handle_self
处理配置项间的依赖关系和有效性检查
主要逻辑：
1. 检查模型本地路径(MODEL_DIR)
2. 验证模型名称有效性
3. 检查模型提供商环境变量
4. 自动处理web_search配置
5. 确定可用的模型提供商
"""

"""load
从配置文件加载配置
支持格式：
- JSON (.json)
- YAML (.yaml)
加载逻辑：
1. 检查文件是否存在
2. 根据文件类型解析内容
3. 更新当前配置
"""

"""save
保存当前配置到文件
保存逻辑：
1. 确定文件路径（默认 saves/config/base.yaml）
2. 根据文件类型序列化配置
3. 写入文件
"""

"""compare_custom_models
比较并处理自定义模型的API密钥
特殊处理：
- 如果输入的api_key为DEFAULT_MOCK_API，则保留原值
- 如果api_key未变化，则不做修改
返回处理后的自定义模型列表
"""

class SimpleConfig(dict):

    def __key(self, key):
        return "" if key is None else key  # 目前忘记了这里为什么要 lower 了，只能说配置项最好不要有大写的

    def __str__(self):
        return json.dumps(self)

    def __setattr__(self, key, value):
        self[self.__key(key)] = value

    def __getattr__(self, key):
        return self.get(self.__key(key))

    def __getitem__(self, key):
        return self.get(self.__key(key))

    def __setitem__(self, key, value):
        return super().__setitem__(self.__key(key), value)

    def __dict__(self):
        return {k: v for k, v in self.items()}

    def update(self, other):
        for key, value in other.items():
            self[key] = value


class Config(SimpleConfig):

    def __init__(self):
        super().__init__()
        self._config_items = {}
        self.save_dir = "saves"
        self.filename = str(Path("saves/config/base.yaml"))
        os.makedirs(os.path.dirname(self.filename), exist_ok=True)

        self._update_models_from_file()

        ### >>> 默认配置
        # 功能选项
        self.add_item("enable_reranker", default=False, des="是否开启重排序")
        self.add_item("enable_knowledge_base", default=False, des="是否开启知识库")
        self.add_item("enable_knowledge_graph", default=False, des="是否开启知识图谱")
        self.add_item("enable_web_search", default=False, des="是否开启网页搜索（注：现阶段会根据 TAVILY_API_KEY 自动开启，无法手动配置，将会在下个版本移除此配置项）")  # noqa: E501
        # 默认智能体配置
        self.add_item("default_agent_id", default="", des="默认智能体ID")
        # 模型配置
        ## 注意这里是模型名，而不是具体的模型路径，默认使用 HuggingFace 的路径
        ## 如果需要自定义本地模型路径，则在 src/.env 中配置 MODEL_DIR
        self.add_item("model_provider", default="siliconflow", des="模型提供商", choices=list(self.model_names.keys()))
        self.add_item("model_name", default="Qwen/Qwen2.5-7B-Instruct", des="模型名称")

        self.add_item("embed_model", default="siliconflow/BAAI/bge-m3", des="Embedding 模型", choices=list(self.embed_model_names.keys()))
        self.add_item("reranker", default="siliconflow/BAAI/bge-reranker-v2-m3", des="Re-Ranker 模型", choices=list(self.reranker_names.keys()))  # noqa: E501
        self.add_item("model_local_paths", default={}, des="本地模型路径")
        self.add_item("use_rewrite_query", default="on", des="重写查询", choices=["off", "on", "hyde"])
        self.add_item("device", default="cuda", des="运行本地模型的设备", choices=["cpu", "cuda"])
        ### <<< 默认配置结束

        self.load()
        self.handle_self()

    def add_item(self, key, default, des=None, choices=None):
        self.__setattr__(key, default)
        self._config_items[key] = {
            "default": default,
            "des": des,
            "choices": choices
        }

    def __dict__(self):
        blocklist = [
            "_config_items",
            "model_names",
            "model_provider_status",
            "embed_model_names",
            "reranker_names",
        ]
        return {k: v for k, v in self.items() if k not in blocklist}

    def _update_models_from_file(self):
        """
        从 models.yaml 和 models.private.yml 中更新 MODEL_NAMES
        """

        with open(Path("src/static/models.yaml"), encoding='utf-8') as f:
            _models = yaml.safe_load(f)

        # 尝试打开一个 models.private.yml 文件，用来覆盖 models.yaml 中的配置
        try:
            with open(Path("src/static/models.private.yml"), encoding='utf-8') as f:
                _models_private = yaml.safe_load(f)
        except FileNotFoundError:
            _models_private = {}

        # 修改为按照子元素合并
        # _models = {**_models, **_models_private}

        self.model_names = {**_models["MODEL_NAMES"], **_models_private.get("MODEL_NAMES", {})}
        self.embed_model_names = {**_models["EMBED_MODEL_INFO"], **_models_private.get("EMBED_MODEL_INFO", {})}
        self.reranker_names = {**_models["RERANKER_LIST"], **_models_private.get("RERANKER_LIST", {})}

    def _save_models_to_file(self):
        _models = {
            "MODEL_NAMES": self.model_names,
            "EMBED_MODEL_INFO": self.embed_model_names,
            "RERANKER_LIST": self.reranker_names,
        }
        with open(Path("src/static/models.private.yml"), 'w', encoding='utf-8') as f:
            yaml.dump(_models, f, indent=2, allow_unicode=True)

    def handle_self(self):
        """
        处理配置
        """
        model_provider_info = self.model_names.get(self.model_provider, {})
        self.model_dir = os.environ.get("MODEL_DIR", "")

        if self.model_dir:
            if os.path.exists(self.model_dir):
                logger.debug(f"MODEL_DIR （{self.model_dir}） 下面的文件夹: {os.listdir(self.model_dir)}")
            else:
                logger.warning(f"提醒：MODEL_DIR （{self.model_dir}） 不存在，如果未配置，请忽略，如果配置了，请检查是否配置正确;"
                               "比如 docker-compose 文件中的映射")


        # 检查模型提供商是否存在
        if self.model_provider != "custom":
            if self.model_name not in model_provider_info["models"]:
                logger.warning(f"Model name {self.model_name} not in {self.model_provider}, using default model name")
                self.model_name = model_provider_info["default"]

            default_model_name = model_provider_info["default"]
            self.model_name = self.get("model_name") or default_model_name
        else:
            self.model_name = self.get("model_name")
            if self.model_name not in [item["custom_id"] for item in self.get("custom_models", [])]:
                logger.warning(f"Model name {self.model_name} not in custom models, using default model name")
                if self.get("custom_models", []):
                    self.model_name = self.get("custom_models", [])[0]["custom_id"]
                else:
                    self.model_name = self._config_items["model_name"]["default"]
                    self.model_provider = self._config_items["model_provider"]["default"]
                    logger.error(f"No custom models found, using default model {self.model_name} from {self.model_provider}")

        # 检查模型提供商的环境变量
        conds = {}
        self.model_provider_status = {}
        for provider in self.model_names:
            conds[provider] = self.model_names[provider]["env"]
            conds_bool = [bool(os.getenv(_k)) for _k in conds[provider]]
            self.model_provider_status[provider] = all(conds_bool)


        # 2025.04.08 修改为不手动配置，只要配置了TAVILY_API_KEY，就默认开启web_search
        if os.getenv("TAVILY_API_KEY"):
            self.enable_web_search = True

        self.valuable_model_provider = [k for k, v in self.model_provider_status.items() if v]
        assert len(self.valuable_model_provider) > 0, f"No model provider available, please check your `.env` file. API_KEY_LIST: {conds}"

    def load(self):
        """根据传入的文件覆盖掉默认配置"""
        logger.info(f"Loading config from {self.filename}")
        if self.filename is not None and os.path.exists(self.filename):

            if self.filename.endswith(".json"):
                with open(self.filename) as f:
                    content = f.read()
                    if content:
                        local_config = json.loads(content)
                        self.update(local_config)
                    else:
                        print(f"{self.filename} is empty.")

            elif self.filename.endswith(".yaml"):
                with open(self.filename) as f:
                    content = f.read()
                    if content:
                        local_config = yaml.safe_load(content)
                        self.update(local_config)
                    else:
                        print(f"{self.filename} is empty.")
            else:
                logger.warning(f"Unknown config file type {self.filename}")

    def save(self):
        logger.info(f"Saving config to {self.filename}")
        if self.filename is None:
            logger.warning("Config file is not specified, save to default config/base.yaml")
            self.filename = os.path.join(self.save_dir, "config", "base.yaml")
            os.makedirs(os.path.dirname(self.filename), exist_ok=True)

        if self.filename.endswith(".json"):
            with open(self.filename, 'w+') as f:
                json.dump(self.__dict__(), f, indent=4, ensure_ascii=False)
        elif self.filename.endswith(".yaml"):
            with open(self.filename, 'w+') as f:
                yaml.dump(self.__dict__(), f, indent=2, allow_unicode=True)
        else:
            logger.warning(f"Unknown config file type {self.filename}, save as json")
            with open(self.filename, 'w+') as f:
                json.dump(self, f, indent=4)

        logger.info(f"Config file {self.filename} saved")

    def dump_config(self):
        return json.loads(str(self))

    def compare_custom_models(self, value):
        """
        比较 custom_models 中的 api_key，如果输入的 api_key 与当前的 api_key 相同，则不修改
        如果输入的 api_key 为 DEFAULT_MOCK_API，则使用当前的 api_key
        """
        current_models_dict = {model["custom_id"]: model.get("api_key") for model in self.get("custom_models", [])}

        for i, model in enumerate(value):
            input_custom_id = model.get("custom_id")
            input_api_key = model.get("api_key")

            if input_custom_id in current_models_dict:
                current_api_key = current_models_dict[input_custom_id]
                if input_api_key == DEFAULT_MOCK_API or input_api_key == current_api_key:
                    value[i]["api_key"] = current_api_key

        return value
