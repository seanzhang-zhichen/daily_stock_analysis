# feishu_doc.py
# -*- coding: utf-8 -*-
"""基于官方 lark-oapi SDK 的飞书云文档集成辅助函数。

本模块封装了与飞书（Feishu/Lark）云文档的交互能力，提供从 Markdown 内容
创建飞书文档的完整流程。主要功能包括：

- 读取飞书应用凭据（app_id、app_secret、folder_token）
- 初始化 lark-oapi SDK 客户端（自动处理 tenant_access_token 的获取与刷新）
- 在指定文件夹下创建飞书文档
- 将 Markdown 内容解析为飞书 Block 结构并分批写入文档

依赖：
    lark-oapi: 飞书官方 Python SDK，用于与飞书开放平台 API 交互。

配置：
    需要在配置文件中提供以下字段：
    - feishu_app_id: 飞书应用 ID
    - feishu_app_secret: 飞书应用密钥
    - feishu_folder_token: 目标文件夹的 token

注意：
    飞书 API 对单次写入的 Block 数量有限制（建议约 50 个），因此写入时会自动分批。
"""

import logging
import json
import lark_oapi as lark
from lark_oapi.api.docx.v1 import *
from typing import List, Dict, Any, Optional
from src.config import get_config

logger = logging.getLogger(__name__)


class FeishuDocManager:
    """飞书云文档管理器 (基于官方 SDK lark-oapi)。

    该类负责管理飞书云文档的生命周期，包括客户端初始化、文档创建和内容写入。
    所有操作均通过飞书官方 SDK 完成，确保与飞书 API 的兼容性。

    Attributes:
        config: 应用配置对象，包含飞书相关的配置项。
        app_id: 飞书应用 ID，用于 SDK 认证。
        app_secret: 飞书应用密钥，用于 SDK 认证。
        folder_token: 目标文件夹的 token，新文档将创建在此文件夹下。
        client: 初始化后的 lark-oapi SDK 客户端实例，配置完整时可用。
    """

    def __init__(self):
        """读取飞书凭据，并在配置完整时初始化 SDK 客户端。

        从全局配置中读取飞书相关的凭据信息（app_id、app_secret、folder_token）。
        如果所有必需配置项均已提供，则构建并初始化 lark-oapi SDK 客户端。
        SDK 会自动处理 tenant_access_token 的获取和刷新，无需人工干预。
        如果配置不完整，client 将被设为 None，后续操作会安全跳过。
        """
        self.config = get_config()
        self.app_id = self.config.feishu_app_id
        self.app_secret = self.config.feishu_app_secret
        self.folder_token = self.config.feishu_folder_token

        # 初始化 SDK 客户端
        # SDK 会自动处理 tenant_access_token 的获取和刷新，无需人工干预
        if self.is_configured():
            self.client = lark.Client.builder() \
                .app_id(self.app_id) \
                .app_secret(self.app_secret) \
                .log_level(lark.LogLevel.INFO) \
                .build()
        else:
            self.client = None

    def is_configured(self) -> bool:
        """检查飞书配置是否完整。

        判断 app_id、app_secret 和 folder_token 是否均已配置。
        这是执行任何飞书操作的前提条件。

        Returns:
            bool: 配置完整返回 True，否则返回 False。
        """
        return bool(self.app_id and self.app_secret and self.folder_token)

    def create_daily_doc(self, title: str, content_md: str) -> Optional[str]:
        """创建日报文档并将 Markdown 内容写入飞书云文档。

        该方法执行以下步骤：
        1. 检查 SDK 客户端是否已初始化且配置完整。
        2. 调用飞书 API 在指定文件夹下创建新文档。
        3. 将 Markdown 文本解析为飞书 Block 对象列表。
        4. 分批将 Block 写入文档（规避 API 单次写入数量限制）。

        Args:
            title: 文档标题。
            content_md: 要写入的 Markdown 格式内容字符串。

        Returns:
            Optional[str]: 成功时返回飞书文档的访问链接；失败时返回 None。
        """
        if not self.client or not self.is_configured():
            logger.warning("飞书 SDK 未初始化或配置缺失，跳过创建")
            return None

        try:
            # 1. 创建文档
            # 使用官方 SDK 的 Builder 模式构造请求
            create_request = CreateDocumentRequest.builder() \
                .request_body(CreateDocumentRequestBody.builder()
                              .folder_token(self.folder_token)
                              .title(title)
                              .build()) \
                .build()

            response = self.client.docx.v1.document.create(create_request)

            if not response.success():
                logger.error(f"创建文档失败: {response.code} - {response.msg} - {response.error}")
                return None

            doc_id = response.data.document.document_id
            # 这里的 domain 只是为了生成链接，实际访问会重定向
            doc_url = f"https://feishu.cn/docx/{doc_id}"
            logger.info(f"飞书文档创建成功: {title} (ID: {doc_id})")

            # 2. 解析 Markdown 并写入内容
            # 将 Markdown 转换为 SDK 需要的 Block 对象列表
            blocks = self._markdown_to_sdk_blocks(content_md)

            # 飞书 API 限制每次写入 Block 数量（建议 50 个左右），分批写入
            batch_size = 50
            doc_block_id = doc_id  # 文档本身也是一个 block

            for i in range(0, len(blocks), batch_size):
                batch_blocks = blocks[i:i + batch_size]

                # 构造批量添加块的请求
                batch_add_request = CreateDocumentBlockChildrenRequest.builder() \
                    .document_id(doc_id) \
                    .block_id(doc_block_id) \
                    .request_body(CreateDocumentBlockChildrenRequestBody.builder()
                                  .children(batch_blocks)  # SDK 需要 Block 对象列表
                                  .index(-1)  # 追加到末尾
                                  .build()) \
                    .build()

                write_resp = self.client.docx.v1.document_block_children.create(batch_add_request)

                if not write_resp.success():
                    logger.error(f"写入文档内容失败(批次{i}): {write_resp.code} - {write_resp.msg}")

            logger.info(f"文档内容写入完成")
            return doc_url

        except Exception as e:
            logger.error(f"飞书文档操作异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def _markdown_to_sdk_blocks(self, md_text: str) -> List[Block]:
        """将简单的 Markdown 文本转换为飞书 SDK 的 Block 对象列表。

        目前支持的 Markdown 元素：
        - 普通文本行（转换为 Text Block）
        - 一级标题（# 开头，转换为 Heading1 Block）
        - 二级标题（## 开头，转换为 Heading2 Block）
        - 三级标题（### 开头，转换为 Heading3 Block）
        - 分割线（--- 开头，转换为 Divider Block）

        注意：
            当前实现为简化版解析器，仅按行处理，不支持嵌套格式、列表、
            表格等复杂 Markdown 语法。对于不支持的格式，将按普通文本处理。

        Args:
            md_text: 输入的 Markdown 格式文本。

        Returns:
            List[Block]: 转换后的飞书 SDK Block 对象列表。
        """
        blocks = []
        lines = md_text.split('\n')

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 默认普通文本 (Text = 2)
            block_type = 2
            text_content = line

            # 识别标题
            if line.startswith('# '):
                block_type = 3  # H1
                text_content = line[2:]
            elif line.startswith('## '):
                block_type = 4  # H2
                text_content = line[3:]
            elif line.startswith('### '):
                block_type = 5  # H3
                text_content = line[4:]
            elif line.startswith('---'):
                # 分割线
                blocks.append(Block.builder()
                              .block_type(22)
                              .divider(Divider.builder().build())
                              .build())
                continue

            # 构造 Text 类型的 Block
            # SDK 的结构嵌套比较深: Block -> Text -> elements -> TextElement -> TextRun -> content
            text_run = TextRun.builder() \
                .content(text_content) \
                .text_element_style(TextElementStyle.builder().build()) \
                .build()

            text_element = TextElement.builder() \
                .text_run(text_run) \
                .build()

            text_obj = Text.builder() \
                .elements([text_element]) \
                .style(TextStyle.builder().build()) \
                .build()

            # 根据 block_type 放入正确的属性容器
            block_builder = Block.builder().block_type(block_type)

            if block_type == 2:
                block_builder.text(text_obj)
            elif block_type == 3:
                block_builder.heading1(text_obj)
            elif block_type == 4:
                block_builder.heading2(text_obj)
            elif block_type == 5:
                block_builder.heading3(text_obj)

            blocks.append(block_builder.build())

        return blocks
