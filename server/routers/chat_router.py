import os
import json
import asyncio
import functools
import queue
import time
import traceback
import uuid
from datetime import datetime # [新增] 导入 datetime
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage
from sqlalchemy.orm import Session
from pydantic import BaseModel
import httpx
from server.services.concurrency import chat_gate, retrieval_gate, upstream_proxy_gate

from src import executor, config, retriever
from src.core import HistoryManager
from src.agents import agent_manager
from src.models import select_model
from src.utils.logging_config import logger
from src.agents.tools_factory import get_all_tools
from server.utils.auth_middleware import get_superadmin_user
from server.services.access_control import assert_chat_features_allowed
from server.services.model_credentials import resolve_model_for_user
from server.services.http_clients import get_multimodal_client
from fastapi import BackgroundTasks # [新增]
from server.db_manager import db_manager # [新增] 用于在后台任务中获取独立session
from server.models.statistics_model import Question
from server.utils.auth_middleware import get_required_user, get_db
from server.models.user_model import User
from server.models.thread_model import Thread
from server.models.chat_model import ChatRecord, ExamPapersRecord, GuideRecord, ItemRecord, WriterRecord
from server.utils.multimodal_remote import (
    MAX_IMAGE_RESPONSE_BYTES,
    MAX_JSON_RESPONSE_BYTES,
    accumulate_bounded_bytes,
    build_service_auth_headers,
    filter_image_response_headers,
    format_redacted_upstream_error,
    get_multimodal_api_base,
    is_image_content_type,
    map_upstream_proxy_status,
    new_multimodal_trace_id,
    normalize_multimodal_kbs,
    validate_multimodal_image_params,
)
from server.utils.stream_sanitizer import (
    ChatStreamAssembler,
    complete_related_questions,
    parse_related_questions,
)
from server.utils.meta_sanitizer import redact_meta_for_log

chat = APIRouter(prefix="/chat")


@chat.get("/multimodal/kbs")
async def get_multimodal_kbs(current_user: User = Depends(get_required_user)):
    base_url = get_multimodal_api_base()
    if not base_url:
        # 服务端未配置远端多模态地址：不泄露地址，直接返回空列表（普通聊天不受影响）
        return {"kbs": [], "message": "多模态知识库未配置"}

    trace_id = new_multimodal_trace_id()
    headers = build_service_auth_headers(trace_id)
    t0 = time.monotonic()
    async with upstream_proxy_gate:
        try:
            resp = await get_multimodal_client().get(f"{base_url}/kb/list", headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            logger.error(
                format_redacted_upstream_error(trace_id, "kb/list", None, elapsed_ms, type(e).__name__)
            )
            raise HTTPException(status_code=502, detail=f"多模态知识库列表加载失败（trace={trace_id[:8]}）") from e

    return {"kbs": normalize_multimodal_kbs(payload)}


@chat.get("/multimodal/image")
async def get_multimodal_image(
        kbId: str = Query(...),
        fileId: str = Query(...),
        imagePath: str = Query(...),
        thumb: int = Query(0, ge=0, le=1),
        request: Request = None,
        current_user: User = Depends(get_required_user),
        ):
    base_url = get_multimodal_api_base()
    if not base_url:
        raise HTTPException(status_code=503, detail="多模态知识库未配置")

    # D2.5：校验 kbId/fileId/imagePath，拒绝绝对路径、穿越、URL、盘符、UNC、NUL
    try:
        safe_kb, safe_file, safe_image_path = validate_multimodal_image_params(kbId, fileId, imagePath)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    trace_id = new_multimodal_trace_id()
    headers = build_service_auth_headers(trace_id)
    # D2.6：条件请求 / Range 透传（远端支持时）
    if request is not None:
        if request.headers.get("if-none-match"):
            headers["if-none-match"] = request.headers["if-none-match"]
        if request.headers.get("range"):
            headers["range"] = request.headers["range"]

    await upstream_proxy_gate.__aenter__()
    try:
        client = get_multimodal_client()
        upstream_request = client.build_request(
            "GET",
            f"{base_url}/pdf/images",
            params={"kbId": safe_kb, "fileId": safe_file, "imagePath": safe_image_path, "thumb": thumb},
            headers=headers,
        )
        resp = await client.send(upstream_request, stream=True)
    except asyncio.CancelledError:
        await upstream_proxy_gate.__aexit__(None, None, None)
        raise
    except Exception as e:
        await upstream_proxy_gate.__aexit__(type(e), e, e.__traceback__)
        logger.error(format_redacted_upstream_error(trace_id, "pdf/images", None, 0.0, type(e).__name__))
        raise HTTPException(status_code=502, detail=f"多模态图片加载失败（trace={trace_id[:8]}）") from e

    status = resp.status_code
    if status == 304:
        # 远端命中条件请求：透传 304，不流式返回
        headers_out = filter_image_response_headers(resp.headers)
        await resp.aclose()
        await upstream_proxy_gate.__aexit__(None, None, None)
        return Response(status_code=304, headers=headers_out)

    # D2.4：开始流式前先检查上游状态码，不得把 JSON 错误当图片返回
    if resp.is_redirect or status >= 400:
        if resp.is_redirect:
            await resp.aclose()
            await upstream_proxy_gate.__aexit__(None, None, None)
            logger.error(format_redacted_upstream_error(trace_id, "pdf/images", status, 0.0, "Redirect"))
            raise HTTPException(status_code=502, detail=f"多模态远端跳转已被拒绝（trace={trace_id[:8]}）")
        await accumulate_bounded_bytes(resp.aiter_bytes(), MAX_JSON_RESPONSE_BYTES)  # 有界读掉错误体，仅日志
        mapped_status, mapped_message = map_upstream_proxy_status(status)
        logger.error(format_redacted_upstream_error(trace_id, "pdf/images", status, 0.0, "HTTPError"))
        await resp.aclose()
        await upstream_proxy_gate.__aexit__(None, None, None)
        raise HTTPException(status_code=mapped_status, detail=f"{mapped_message}（trace={trace_id[:8]}）")

    # D2.4：Content-Type 必须是图片，否则拒绝（如上游把 JSON 错误当 PNG 返回）
    content_type = resp.headers.get("content-type", "").split(";", 1)[0].strip()
    if not is_image_content_type(content_type):
        logger.error(format_redacted_upstream_error(trace_id, "pdf/images", status, 0.0, "BadImageContentType"))
        await resp.aclose()
        await upstream_proxy_gate.__aexit__(None, None, None)
        raise HTTPException(status_code=502, detail=f"多模态远端返回非图片 Content-Type（trace={trace_id[:8]}）")

    # D2.6：单张图片最大响应体积限制
    declared_length = None
    raw_length = resp.headers.get("content-length")
    if raw_length:
        try:
            declared_length = int(raw_length)
        except (TypeError, ValueError):
            declared_length = None
    if declared_length is not None and declared_length > MAX_IMAGE_RESPONSE_BYTES:
        logger.error(format_redacted_upstream_error(trace_id, "pdf/images", status, 0.0, "ResponseTooLarge"))
        await resp.aclose()
        await upstream_proxy_gate.__aexit__(None, None, None)
        raise HTTPException(status_code=502, detail=f"多模态图片超过体积限制（trace={trace_id[:8]}）")

    async def _stream():
        total = 0
        try:
            async for chunk in resp.aiter_bytes(chunk_size=1024 * 64):
                total += len(chunk)
                if total > MAX_IMAGE_RESPONSE_BYTES:
                    return
                yield chunk
        except (httpx.HTTPError, asyncio.CancelledError):
            # 客户端断开或上游读取失败：停止，不把断流当 500
            return
        finally:
            try:
                await resp.aclose()
            finally:
                await upstream_proxy_gate.__aexit__(None, None, None)

    return StreamingResponse(
        _stream(),
        status_code=status,
        media_type=content_type or "application/octet-stream",
        headers=filter_image_response_headers(resp.headers),
    )


async def process_question_stats(raw_query: str):
    """
    后台任务：归一化用户问题并统计
    """
    if not raw_query or not raw_query.strip():
        return

    logger.info(f"开始后台统计任务: {raw_query[:20]}...")

    # --- 第一步：LLM 归一化 (提取核心问题) ---
    standard_title = raw_query
    try:
        # 获取模型 (可以使用默认模型，也可以指定更快的模型)
        model = select_model()

        # 构造提示词，要求模型只输出核心问题
        prompt = (
            f"请将以下用户的提问概括为一个简短、标准的问答库标题。\n"
            f"要求：\n"
            f"1. 去除语气词、礼貌用语和无关描述。\n"
            f"2. 保持疑问句式，不要回答问题。\n"
            f"3. 长度控制在 20 字以内。\n"
            f"4. 直接输出标题，不要包含任何解释或标点符号。\n\n"
            f"用户提问：{raw_query}\n"
            f"标准标题："
        )

        # 调用模型 (使用 executor 在线程池中运行，避免阻塞事件循环)
        loop = asyncio.get_running_loop()
        async with chat_gate:
            response = await loop.run_in_executor(
                executor, functools.partial(model.predict, prompt)
            )

        # 处理响应内容
        if hasattr(response, 'content'):
            llm_title = response.content.strip()
        else:
            llm_title = str(response).strip()

        # 简单清洗：去除可能的引号等
        llm_title = llm_title.replace('"', '').replace('“', '').replace('”', '').replace('。', '')

        if llm_title:
            standard_title = llm_title
            logger.info(f"问题归一化: '{raw_query}' -> '{standard_title}'")

    except Exception as e:
        logger.error(f"LLM 归一化失败，将使用原始问题: {e}")
        # 失败时回退到原始问题，截取前50字符防止过长
        standard_title = raw_query[:50]

    # --- 第二步：写入数据库 ---
    session = None
    try:
        # **重要**：后台任务必须创建自己的 DB 会话，不能复用请求的 session
        session = db_manager.get_session()

        # 截取长度以符合数据库字段限制 (假设 title 为 VARCHAR(255))
        db_title = standard_title[:255]

        # 查询是否存在
        question_entry = session.query(Question).filter(Question.title == db_title).first()

        if question_entry:
            # 更新计数
            question_entry.count += 1
            question_entry.last_asked = datetime.now()
            logger.info(f"统计更新: 问题 '{db_title}' 次数+1 (当前: {question_entry.count})")
        else:
            # 新增记录
            new_question = Question(
                title=db_title,
                description=raw_query, # 保留原始提问作为描述
                category="用户提问",     # 默认分类
                count=1,
                last_asked=datetime.now()
            )
            session.add(new_question)
            logger.info(f"统计新增: 收录新问题 '{db_title}'")

        session.commit()

    except Exception as e:
        logger.error(f"统计数据写入失败: {e}")
        if session:
            session.rollback()
    finally:
        if session:
            session.close()


@chat.get("/default_agent")
async def get_default_agent(current_user: User = Depends(get_required_user)):
    """获取默认智能体ID（需要登录）"""
    try:
        default_agent_id = config.default_agent_id
        # 如果没有设置默认智能体，尝试获取第一个可用的智能体
        if not default_agent_id:
            agents = await agent_manager.get_agents_info()
            if agents:
                default_agent_id = agents[0].get("name", "")

        return {"default_agent_id": default_agent_id}
    except Exception as e:
        logger.error(f"获取默认智能体出错: {e}")
        raise HTTPException(status_code=500, detail=f"获取默认智能体出错: {str(e)}")

@chat.post("/set_default_agent")
async def set_default_agent(agent_id: str = Body(..., embed=True), current_user = Depends(get_superadmin_user)):
    """设置默认智能体ID (仅管理员)"""
    try:
        # 验证智能体是否存在
        agents = await agent_manager.get_agents_info()
        agent_ids = [agent.get("name", "") for agent in agents]

        if agent_id not in agent_ids:
            raise HTTPException(status_code=404, detail=f"智能体 {agent_id} 不存在")

        # 设置默认智能体ID
        config.default_agent_id = agent_id
        # 保存配置
        config.save()

        return {"success": True, "default_agent_id": agent_id}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"设置默认智能体出错: {e}")
        raise HTTPException(status_code=500, detail=f"设置默认智能体出错: {str(e)}")

@chat.get("/")
async def chat_get(current_user: User = Depends(get_required_user)):
    """聊天服务健康检查（需要登录）"""
    return "Chat Get!"


@chat.post("/")
async def chat_post(
        #  ...代表必填，None代表可选 
        query: str = Body(...),
        meta: dict = Body(None),
        history: list[dict] | None = Body(None),
        thread_id: str | None = Body(None),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_required_user),
        background_tasks: BackgroundTasks = BackgroundTasks() # [新增] 注入后台任务
        ):
    """处理聊天请求的主要端点（需要登录）"""

    meta = meta or {}
    assert_chat_features_allowed(current_user, meta)

    # [新增] 添加后台任务，不再阻塞主线程
    # 注意：这里不需要传入 db，因为后台任务会自己创建新的 session
    background_tasks.add_task(process_question_stats, query)

    model = resolve_model_for_user(db, current_user, meta)
    meta["server_model_name"] = model.model_name
    history_manager = HistoryManager(history, system_prompt=meta.get("system_prompt"))
    logger.debug(f"Received query: {query} with meta: {redact_meta_for_log(meta)}")

    # 构造一条 JSON 格式的数据块（chunk），并编码成 字节串，末尾再加上一个换行符 b"\n"用于 流式响应
    # 形如：
    #     {
    #     "response": " ask",
    #     "meta": {
    #         "use_graph": false,
    #         "use_web": false,
    #         "graph_name": "neo4j",
    #         "selectedKB": null,
    #         "summary_title": false,
    #         "history_round": 20,
    #         "fontSize": "default",
    #         "wideScreen": false,
    #         "server_model_name": "Qwen/Qwen2.5-72B-Instruct"
    #     },
    #     "status": "loading"
    # }
    def make_chunk(content=None, **kwargs):
        return json.dumps({
            "response": content,
            "meta": meta,
            **kwargs
        }, ensure_ascii=False).encode('utf-8') + b"\n"

    def need_retrieve(meta):
        return meta.get("use_web") or meta.get("use_graph") or meta.get("db_id") or meta.get("use_multimodal_kb")

    # Sentinel for safe sync-iterator exhaustion (StopIteration cannot
    # propagate through a Future, so the executor thread signals via a
    # sentinel tuple instead).
    _STREAM_DONE = object()

    async def _aiter_sync_stream(sync_iter, loop):
        """Advance a synchronous streaming iterator in the executor.

        Each call to ``next()`` happens in *loop*'s default executor so
        that a blocking model backend never starves the event loop.
        ``StopIteration`` is caught inside the thread and translated to
        the ``_STREAM_DONE`` sentinel so that it never escapes a
        ``Future`` (which would silently corrupt the coroutine chain).
        """
        def _next_or_sentinel():
            try:
                return next(sync_iter)
            except StopIteration:
                return _STREAM_DONE

        while True:
            delta = await loop.run_in_executor(executor, _next_or_sentinel)
            if delta is _STREAM_DONE:
                return
            yield delta

    async def generate_response():
        modified_query = query
        refs = None

        # 处理知识库检索
        if meta and need_retrieve(meta):
            yield make_chunk(status="searching")

            try:
                loop = asyncio.get_running_loop()
                # 多轮检索通过线程安全的 Queue 把进度消息回传，由事件循环边等待边转发
                progress_q = queue.Queue()
                async with retrieval_gate:
                    future = loop.run_in_executor(
                        executor,
                        functools.partial(
                            retriever, modified_query, history_manager.messages, meta, progress_q.put, model
                        ),
                    )
                    while not future.done():
                        try:
                            progress_msg = progress_q.get_nowait()
                        except queue.Empty:
                            await asyncio.sleep(0.05)
                            continue
                        if progress_msg:
                            yield make_chunk(status="searching", message=progress_msg)
                    modified_query, refs = await future
                    # 检索完成后再清空一次队列，避免最后一条进度消息在 future 完成瞬间被丢弃
                    while not progress_q.empty():
                        progress_msg = progress_q.get_nowait()
                        if progress_msg:
                            yield make_chunk(status="searching", message=progress_msg)
            except Exception as e:
                logger.error(f"Retriever error: {e}, {traceback.format_exc()}")
                yield make_chunk(message=f"Retriever error: {e}", status="error")
                return

            yield make_chunk(status="generating")

        messages = history_manager.get_history_with_msg(modified_query, max_rounds=meta.get('history_round'))
        history_manager.add_user(query)  # 注意这里使用原始查询

        reasoning_content = ""
        assembler = ChatStreamAssembler()
        try:
            loop = asyncio.get_running_loop()
            sync_iter = await loop.run_in_executor(
                executor,
                functools.partial(model.predict, messages, stream=True),
            )
            async for delta in _aiter_sync_stream(sync_iter, loop):
                # 推理模型才会有reasoning_content属性
                if not delta.content and hasattr(delta, 'reasoning_content'):
                    reasoning_content += delta.reasoning_content or ""
                    chunk = make_chunk(reasoning_content=reasoning_content, status="reasoning")
                    yield chunk
                    continue

                delta_content = delta.content or ""
                if hasattr(delta, 'is_full') and delta.is_full:
                    update = assembler.feed_snapshot(delta_content)
                else:
                    update = assembler.feed_incremental(delta_content)
                if update:
                    yield make_chunk(
                        content=update.content,
                        replace_content=update.replace_content,
                        status="loading",
                    )

            update = assembler.finish()
            if update:
                yield make_chunk(
                    content=update.content,
                    replace_content=update.replace_content,
                    status="loading",
                )

            content = assembler.content
            logger.debug(f"Final response: {content}")
            # === 新增：生成相关问题 ===
            related_questions = []
            try:
                # 只有当回答内容足够长时才生成推荐
                if len(content) > 10:
                    # 构造推荐问题的 Prompt
                    recommend_prompt = f"""基于用户的提问和你的回答，请生成3个用户可能感兴趣的后续简短问题。
                    用户提问: {query}
                    你的回答: {content[:500]}... (摘要)

                    要求：
                    1. 只返回问题列表，每行一个。
                    2. 问题简短有力，不要带序号。
                    3. 不要包含"可以问"、"例如"等废话。
                    """

                    # 使用非流式调用快速获取
                    rec_response = await loop.run_in_executor(
                        executor,
                        functools.partial(model.predict, recommend_prompt),
                    )

                    # 兼容不同的模型返回格式
                    rec_text = rec_response.content if hasattr(rec_response, 'content') else str(rec_response)
                    related_questions = parse_related_questions(rec_text)
            except Exception as e:
                logger.error(f"生成推荐问题失败: {e}")
                # 失败不影响主要流程，只是没有推荐问题
                pass
            related_questions = complete_related_questions(related_questions)

            # === 修改结束：将 related_questions 加入到 finished 块中 ===

            yield make_chunk(status="finished",
                            history=history_manager.update_ai(content),
                            refs=refs,
                            related_questions=related_questions) # <--- 添加这一行

        except Exception as e:
            update = assembler.abort()
            if update:
                yield make_chunk(
                    content=update.content,
                    replace_content=update.replace_content,
                    status="loading",
                )
            # ... 异常处理保持不变 ...
            logger.error(f"Model error: {e}, {traceback.format_exc()}")
            yield make_chunk(message=f"Model error: {e}", status="error")
            return

    await chat_gate.__aenter__()

    async def _stream_with_gate():
        try:
            async for chunk in generate_response():
                yield chunk
        finally:
            await chat_gate.__aexit__(None, None, None)

    return StreamingResponse(_stream_with_gate(), media_type='application/json')

@chat.post("/call")
async def call(
        query: str = Body(...),
        meta: dict = Body(None),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_required_user),
        ):
    """调用模型进行简单问答（需要登录）"""
    meta = meta or {}
    assert_chat_features_allowed(current_user, meta)
    model = resolve_model_for_user(db, current_user, meta)

    loop = asyncio.get_running_loop()
    async with chat_gate:
        response = await loop.run_in_executor(
            executor, functools.partial(model.predict, query)
        )

    logger.debug({"query": query, "response": response.content})

    return {"response": response.content}

@chat.get("/agent")
async def get_agent(current_user: User = Depends(get_required_user)):
    """获取所有可用智能体（需要登录）"""
    agents = await agent_manager.get_agents_info()
    # logger.debug(f"agents: {agents}")
    return {"agents": agents}

@chat.post("/agent/{agent_name}")
async def chat_agent(agent_name: str,
               query: str = Body(...),
               config: dict = Body({}),
               meta: dict = Body({}),
               current_user: User = Depends(get_required_user)):
    """使用特定智能体进行对话（需要登录）"""

    meta = meta or {}
    assert_chat_features_allowed(current_user, meta)

    meta.update({
        "query": query,
        "agent_name": agent_name,
        "server_model_name": config.get("model", agent_name),
        "thread_id": config.get("thread_id"),
        "user_id": current_user.id
    })

    # 将meta和thread_id整合到config中
    def make_chunk(content=None, **kwargs):

        return json.dumps({
            "request_id": meta.get("request_id"),
            "response": content,
            **kwargs
        }, ensure_ascii=False).encode('utf-8') + b"\n"

    async def stream_messages():

        # 代表服务端已经收到了请求
        yield make_chunk(status="init", meta=meta, msg=HumanMessage(content=query).model_dump())

        try:
            agent = agent_manager.get_agent(agent_name)
        except Exception as e:
            logger.error(f"Error getting agent {agent_name}: {e}, {traceback.format_exc()}")
            yield make_chunk(message=f"Error getting agent {agent_name}: {e}", status="error")
            return

        messages = [{"role": "user", "content": query}]

        # 构造运行时配置，如果没有thread_id则生成一个
        config["user_id"] = current_user.id
        if "thread_id" not in config or not config["thread_id"]:
            config["thread_id"] = str(uuid.uuid4())
            logger.debug(f"没有thread_id，生成一个: {config['thread_id']=}")

        runnable_config = {"configurable": {**config}}

        try:
            async for msg, metadata in agent.stream_messages(messages, config_schema=runnable_config):
                # logger.debug(f"msg: {msg.model_dump()}, metadata: {metadata}")
                if isinstance(msg, AIMessageChunk):
                    yield make_chunk(content=msg.content,
                                    msg=msg.model_dump(),
                                    metadata=metadata,
                                    status="loading")
                else:
                    yield make_chunk(msg=msg.model_dump(),
                                    metadata=metadata,
                                    status="loading")

            yield make_chunk(status="finished", meta=meta)
        except Exception as e:
            logger.error(f"Error streaming messages: {e}, {traceback.format_exc()}")
            yield make_chunk(message=f"Error streaming messages: {e}", status="error")

    await chat_gate.__aenter__()

    async def _stream_with_gate():
        try:
            async for chunk in stream_messages():
                yield chunk
        finally:
            await chat_gate.__aexit__(None, None, None)

    return StreamingResponse(_stream_with_gate(), media_type='application/json')

@chat.get("/models")
async def get_chat_models(model_provider: str, current_user: User = Depends(get_superadmin_user)):
    """获取指定模型提供商的模型列表（需要登录）"""
    model = select_model(model_provider=model_provider)
    loop = asyncio.get_running_loop()
    async with chat_gate:
        models = await loop.run_in_executor(
            executor, functools.partial(model.get_models)
        )
    return {"models": models}

@chat.post("/models/update")
async def update_chat_models(model_provider: str, model_names: list[str], current_user = Depends(get_superadmin_user)):
    """更新指定模型提供商的模型列表 (仅管理员)"""
    config.model_names[model_provider]["models"] = model_names
    config._save_models_to_file()
    return {"models": config.model_names[model_provider]["models"]}

@chat.get("/tools")
async def get_tools(current_user: User = Depends(get_superadmin_user)):
    """获取所有可用工具（需要登录）"""
    return {"tools": list(get_all_tools().keys())}

@chat.post("/agent/{agent_name}/config")
async def save_agent_config(
    agent_name: str,
    config: dict = Body(...),
    current_user: User = Depends(get_superadmin_user)
):
    """保存智能体配置到YAML文件（需要管理员权限）"""
    try:
        # 获取Agent实例和配置类
        agent = agent_manager.get_agent(agent_name)
        if not agent:
            raise HTTPException(status_code=404, detail=f"智能体 {agent_name} 不存在")

        # 使用配置类的save_to_file方法保存配置
        config_cls = agent.config_schema
        result = config_cls.save_to_file(config, agent_name)

        if result:
            return {"success": True, "message": f"智能体 {agent_name} 配置已保存"}
        else:
            raise HTTPException(status_code=500, detail="保存智能体配置失败")

    except Exception as e:
        logger.error(f"保存智能体配置出错: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"保存智能体配置出错: {str(e)}")

@chat.get("/agent/{agent_name}/history")
async def get_agent_history(
    agent_name: str,
    thread_id: str,
    current_user: User = Depends(get_required_user)
):
    """获取智能体历史消息（需要登录）"""
    try:
        # 获取Agent实例和配置类
        agent = agent_manager.get_agent(agent_name)
        if not agent:
            raise HTTPException(status_code=404, detail=f"智能体 {agent_name} 不存在")

        # 获取历史消息
        history = await agent.get_history(user_id=current_user.id, thread_id=thread_id)
        return {"history": history}

    except Exception as e:
        logger.error(f"获取智能体历史消息出错: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取智能体历史消息出错: {str(e)}")

@chat.get("/agent/{agent_name}/config")
async def get_agent_config(
    agent_name: str,
    current_user: User = Depends(get_required_user)
):
    """从YAML文件加载智能体配置（需要登录）"""
    try:
        # 检查智能体是否存在
        if not (agent := agent_manager.get_agent(agent_name)):
            raise HTTPException(status_code=404, detail=f"智能体 {agent_name} 不存在")

        config = agent.config_schema.from_runnable_config(config={}, agent_name=agent_name)
        return {"success": True, "config": config}

    except Exception as e:
        logger.error(f"加载智能体配置出错: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"加载智能体配置出错: {str(e)}")

# ==================== 线程管理 API ====================

class ThreadCreate(BaseModel):
    title: str | None = None
    agent_id: str
    description: str | None = None
    metadata: dict | None = None


class ThreadResponse(BaseModel):
    id: str
    user_id: str
    agent_id: str
    title: str | None = None
    description: str | None = None
    create_at: str
    update_at: str


@chat.post("/thread", response_model=ThreadResponse)
async def create_thread(
    thread: ThreadCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    """创建新对话线程"""
    thread_id = str(uuid.uuid4())

    new_thread = Thread(
        id=thread_id,
        user_id=current_user.id,
        agent_id=thread.agent_id,
        title=thread.title or "新对话",
        description=thread.description,
    )

    db.add(new_thread)
    db.commit()
    db.refresh(new_thread)

    return {
        "id": new_thread.id,
        "user_id": new_thread.user_id,
        "agent_id": new_thread.agent_id,
        "title": new_thread.title,
        "description": new_thread.description,
        "create_at": new_thread.create_at.isoformat(),
        "update_at": new_thread.update_at.isoformat(),
    }


@chat.get("/threads", response_model=list[ThreadResponse])
async def list_threads(
    agent_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    """获取用户的所有对话线程"""
    query = db.query(Thread).filter(
        Thread.user_id == current_user.id,
        Thread.status == 1
    )

    if agent_id:
        query = query.filter(Thread.agent_id == agent_id)

    threads = query.order_by(Thread.update_at.desc()).all()

    return [
        {
            "id": thread.id,
            "user_id": thread.user_id,
            "agent_id": thread.agent_id,
            "title": thread.title,
            "description": thread.description,
            "create_at": thread.create_at.isoformat(),
            "update_at": thread.update_at.isoformat(),
        }
        for thread in threads
    ]


@chat.delete("/thread/{thread_id}")
async def delete_thread(
    thread_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    """删除对话线程"""
    thread = db.query(Thread).filter(
        Thread.id == thread_id,
        Thread.user_id == current_user.id
    ).first()

    if not thread:
        raise HTTPException(status_code=404, detail="对话线程不存在")

    # 软删除
    thread.status = 0
    db.commit()

    return {"message": "删除成功"}


class ThreadUpdate(BaseModel):
    title: str | None = None
    description: str | None = None


@chat.put("/thread/{thread_id}", response_model=ThreadResponse)
async def update_thread(
    thread_id: str,
    thread_update: ThreadUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    """更新对话线程信息"""
    thread = db.query(Thread).filter(
        Thread.id == thread_id,
        Thread.user_id == current_user.id,
        Thread.status == 1
    ).first()

    if not thread:
        raise HTTPException(status_code=404, detail="对话线程不存在")

    if thread_update.title is not None:
        thread.title = thread_update.title

    if thread_update.description is not None:
        thread.description = thread_update.description

    db.commit()
    db.refresh(thread)

    return {
        "id": thread.id,
        "user_id": thread.user_id,
        "agent_id": thread.agent_id,
        "title": thread.title,
        "description": thread.description,
        "create_at": thread.create_at.isoformat(),
        "update_at": thread.update_at.isoformat(),
    }

# ==================== 聊天记录相关 API ====================

from datetime import datetime
from pytz import timezone
import json
beijing = timezone('Asia/Shanghai')
from zoneinfo import ZoneInfo

@chat.get("/records", response_model=list)
async def get_chat_records(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    records = db.query(ChatRecord).filter(ChatRecord.user_id == current_user.id).order_by(ChatRecord.updatetime.desc()).all()
    return [
        {
            "id": r.id,
            "content": json.loads(r.content),
            "updatetime": r.updatetime.isoformat()
        }
        for r in records
    ]

@chat.post("/records")
async def save_chat_record(
    record: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    # 取出新记录的 id
    new_id = str(record.get("id"))

    if not new_id:
        return {"success": False, "msg": "记录中缺少 id 字段"}

    # 删除 id 相同的旧记录（通过 conv_id）
    db.query(ChatRecord).filter(
        ChatRecord.user_id == current_user.id,
        ChatRecord.conv_id == new_id
    ).delete()

    # 添加新记录
    db.add(ChatRecord(
        content=json.dumps(record, ensure_ascii=False),
        conv_id=new_id,
        user_id=current_user.id,
        updatetime=datetime.now(ZoneInfo("Asia/Shanghai"))
    ))

    db.commit()
    return {"success": True}

@chat.delete("/records/{record_id}")
async def delete_chat_record(
    record_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    # 查找并删除当前用户指定 conv_id 的记录
    result = db.query(ChatRecord).filter(
        ChatRecord.user_id == current_user.id,
        ChatRecord.conv_id == record_id
    ).delete(synchronize_session=False)

    if result:
        db.commit()
        return {"success": True, "msg": f"记录 {record_id} 删除成功"}
    else:
        raise HTTPException(status_code=404, detail=f"未找到 id 为 {record_id} 的记录")


# ==================== 引导记录相关 API ====================

from datetime import datetime
from pytz import timezone
import json
from zoneinfo import ZoneInfo

@chat.get("/guide", response_model=list)
async def get_guide_records(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    records = db.query(GuideRecord).filter(GuideRecord.user_id == current_user.id).order_by(GuideRecord.updatetime.desc()).all()
    return [
        {
            "id": r.id,
            "content": json.loads(r.content),
            "updatetime": r.updatetime.isoformat()
        }
        for r in records
    ]

@chat.post("/guide")
async def save_chat_record(
    record: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    # 取出新记录的 id
    new_id = str(record.get("id"))

    if not new_id:
        return {"success": False, "msg": "记录中缺少 id 字段"}

    # 删除 id 相同的旧记录（通过 conv_id）
    db.query(GuideRecord).filter(
        GuideRecord.user_id == current_user.id,
        GuideRecord.guide_id == new_id
    ).delete()

    # 添加新记录
    db.add(GuideRecord(
        content=json.dumps(record, ensure_ascii=False),
        guide_id=new_id,
        user_id=current_user.id,
        updatetime=datetime.now(ZoneInfo("Asia/Shanghai"))
    ))

    db.commit()
    return {"success": True}

@chat.delete("/guide/{guide_id}")
async def delete_chat_record(
    guide_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    # 查找并删除当前用户指定 guide_id 的记录
    result = db.query(GuideRecord).filter(
        GuideRecord.user_id == current_user.id,
        GuideRecord.guide_id == guide_id
    ).delete(synchronize_session=False)

    if result:
        db.commit()
        return {"success": True, "msg": f"记录 {guide_id} 删除成功"}
    else:
        raise HTTPException(status_code=404, detail=f"未找到 id 为 {guide_id} 的记录")
    

# ==================== 写作达人聊天记录相关 API ====================

from datetime import datetime
from pytz import timezone
import json
beijing = timezone('Asia/Shanghai')
from zoneinfo import ZoneInfo

@chat.get("/writer", response_model=list)
async def get_writer_records(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    records = db.query(WriterRecord).filter(WriterRecord.user_id == current_user.id).order_by(WriterRecord.updatetime.desc()).all()
    return [
        {
            "id": r.id,
            "content": json.loads(r.content),
            "updatetime": r.updatetime.isoformat()
        }
        for r in records
    ]

@chat.post("/writer")
async def save_writer_record(
    record: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    # 取出新记录的 id
    new_id = str(record.get("id"))

    if not new_id:
        return {"success": False, "msg": "记录中缺少 id 字段"}

    # 删除 id 相同的旧记录（通过 conv_id）
    db.query(WriterRecord).filter(
        WriterRecord.user_id == current_user.id,
        WriterRecord.conv_id == new_id
    ).delete()

    # 添加新记录
    db.add(WriterRecord(
        content=json.dumps(record, ensure_ascii=False),
        conv_id=new_id,
        user_id=current_user.id,
        updatetime=datetime.now(ZoneInfo("Asia/Shanghai"))
    ))

    db.commit()
    return {"success": True}

@chat.delete("/writer/{writer_id}")
async def delete_writer_record(
    writer_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    logger.info(f"Deleting writer record with ID: {writer_id} for user {current_user.id}")
    # 查找并删除当前用户指定 conv_id 的记录
    result = db.query(WriterRecord).filter(
        WriterRecord.user_id == current_user.id,
        WriterRecord.conv_id == writer_id
    ).delete(synchronize_session=False)

    if result:
        db.commit()
        return {"success": True, "msg": f"记录 {writer_id} 删除成功"}
    else:
        raise HTTPException(status_code=404, detail=f"未找到 id 为 {writer_id} 的记录")
    

# ==================== 题目生成记录相关 API ====================

from datetime import datetime
from pytz import timezone
import json
from zoneinfo import ZoneInfo

@chat.get("/item", response_model=list)
async def get_item_records(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    records = db.query(ItemRecord).filter(ItemRecord.user_id == current_user.id).order_by(ItemRecord.createdtime.desc()).all()
    return [
        {
            "id": r.id,
            "content": json.loads(r.content),
            "createdtime": r.createdtime.isoformat()
        }
        for r in records
    ]

@chat.post("/item")
async def save_item_record(
    record: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    # 取出新记录的 id
    new_id = str(record.get("id"))

    if not new_id:
        return {"success": False, "msg": "记录中缺少 id 字段"}

    # 删除 id 相同的旧记录（通过 item_id）
    db.query(ItemRecord).filter(
        ItemRecord.user_id == current_user.id,
        ItemRecord.item_id == new_id
    ).delete()

    # 添加新记录
    db.add(ItemRecord(
        content=json.dumps(record, ensure_ascii=False),
        item_id=new_id,
        user_id=current_user.id,
        structured_content=record.get("structured_content") or '',
        createdtime=datetime.now(ZoneInfo("Asia/Shanghai"))
    ))

    db.commit()
    return {"success": True}

@chat.delete("/item/{item_id}")
async def delete_item_record(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    # 查找并删除当前用户指定 item_id 的记录
    result = db.query(ItemRecord).filter(
        ItemRecord.user_id == current_user.id,
        ItemRecord.item_id == item_id
    ).delete(synchronize_session=False)

    if result:
        db.commit()
        return {"success": True, "msg": f"记录 {item_id} 删除成功"}
    else:
        raise HTTPException(status_code=404, detail=f"未找到 id 为 {item_id} 的记录")


# ==================== 空白试卷记录相关 API ====================

from datetime import datetime
from pytz import timezone
import json
from zoneinfo import ZoneInfo

@chat.get("/exam", response_model=list)
async def get_exam_records(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    records = db.query(ExamPapersRecord).filter(ExamPapersRecord.user_id == current_user.id).order_by(ExamPapersRecord.createdtime.desc()).all()
    return [
        {
            "id": r.id,
            "content": json.loads(r.content),
            "submission_content": json.loads(r.submission_content) if r.submission_content and r.submission_content.strip() else None,
            "createdtime": r.createdtime.isoformat()
        }
        for r in records
    ]

@chat.post("/exam")
async def save_exam_record(
    record: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    # 取出新记录的 id
    new_id = str(record.get("id"))

    if not new_id:
        return {"success": False, "msg": "记录中缺少 id 字段"}

    # 删除 id 相同的旧记录（通过 item_id）
    db.query(ExamPapersRecord).filter(
        ExamPapersRecord.user_id == current_user.id,
        ExamPapersRecord.exam_paper_id == new_id
    ).delete()

    # 添加新记录
    db.add(ExamPapersRecord(
        content=json.dumps(record, ensure_ascii=False),
        exam_paper_id=new_id,
        user_id=current_user.id,
        createdtime=datetime.now(ZoneInfo("Asia/Shanghai"))
    ))

    db.commit()
    return {"success": True}

from fastapi import Body, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

@chat.post("/exam_sub")
async def save_exam_sub_record(
    record: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    new_id = str(record.get("id"))

    if not new_id:
        return {"success": False, "msg": "记录中缺少 id 字段"}

    temp_record = db.query(ExamPapersRecord).filter(
        ExamPapersRecord.user_id == current_user.id,
        ExamPapersRecord.exam_paper_id == new_id
    ).first()  # 注意要取 first，否则 temp_record 是 Query 对象

    if not temp_record:
        raise HTTPException(status_code=404, detail=f"未找到 id 为 {new_id} 的记录")

    try:
        temp_record.submission_content = json.dumps(record, ensure_ascii=False)
        db.commit()
        db.refresh(temp_record)  # 刷新对象，确保最新数据
        return {"success": True, "record": record}
    except SQLAlchemyError as e:
        db.rollback()  # 回滚事务
        return {"success": False, "msg": "数据库更新失败", "error": str(e)}


@chat.delete("/exam/{exam_paper_id}")
async def delete_exam_record(
    exam_paper_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_required_user)
):
    # 查找并删除当前用户指定 item_id 的记录
    result = db.query(ExamPapersRecord).filter(
        ExamPapersRecord.user_id == current_user.id,
        ExamPapersRecord.exam_paper_id == exam_paper_id
    ).delete(synchronize_session=False)

    if result:
        db.commit()
        return {"success": True, "msg": f"记录 {exam_paper_id} 删除成功"}
    else:
        raise HTTPException(status_code=404, detail=f"未找到 id 为 {exam_paper_id} 的记录")
    
