# -*- coding: utf-8 -*-
"""
===================================
格式化工具模块
===================================

提供各种内容格式化工具函数，用于将通用格式转换为平台特定格式。
"""

import re
from typing import Callable, List, Optional

import markdown2

TRUNCATION_SUFFIX = "\n\n...(本段内容过长已截断)"
PAGE_MARKER_PREFIX = f"\n\n📄"
PAGE_MARKER_SAFE_BYTES = 16  # 形如 "\n\n📄 9999/9999" 的预留字节数
PAGE_MARKER_SAFE_LEN = 13   # 形如 "\n\n📄 9999/9999" 的预留字符数
MIN_MAX_WORDS = 10
MIN_MAX_BYTES = 40
HIDDEN_MARKDOWN_METADATA_RE = re.compile(
    r"^\[dsa-[^\]]+\]:\s+#\s+\([^)\n]*\)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# 特殊字符的 Unicode 码点范围。
_SPECIAL_CHAR_RANGE = (0x10000, 0xFFFFF)
_SPECIAL_CHAR_REGEX = re.compile(r'[\U00010000-\U000FFFFF]')


def _page_marker(i: int, total: int) -> str:
    """生成分块通知消息中紧凑的页码标记（如“📄 3/10”）。"""
    return f"{PAGE_MARKER_PREFIX} {i+1}/{total}"


def _is_special_char(c: str) -> bool:
    """判断字符是否为特殊字符
    
    Args:
        c: 字符
        
    Returns:
        True 如果字符为特殊字符，False 否则
    """
    if len(c) != 1:
        return False
    cp = ord(c)
    return _SPECIAL_CHAR_RANGE[0] <= cp <= _SPECIAL_CHAR_RANGE[1]


def _count_special_chars(s: str) -> int:
    """
    计算字符串中的特殊字符数量
    
    Args:
        s: 字符串
    """
    # 使用正则匹配 (0x10000, 0xFFFFF) 范围内的特殊字符
    match = _SPECIAL_CHAR_REGEX.findall(s)
    return len(match)


def _effective_len(s: str, special_char_len: int = 2) -> int:
    """
    计算字符串的有效长度
    
    Args:
        s: 字符串
        special_char_len: 每个特殊字符的长度，默认为 2
        
    Returns:
        s 的有效长度
    """
    n = len(s)
    n += _count_special_chars(s) * (special_char_len - 1)
    return n


def _slice_at_effective_len(s: str, effective_len: int, special_char_len: int = 2) -> tuple[str, str]:
    """
    按有效长度分割字符串
    
    Args:
        s: 字符串
        effective_len: 有效长度
        special_char_len: 每个特殊字符的长度，默认为 2
        
    Returns:
        分割后的前、后部分字符串
    """
    if _effective_len(s, special_char_len) <= effective_len:
        return s, ""
    
    s_ = s[:effective_len]
    n_special_chars = _count_special_chars(s_)
    residual_lens = n_special_chars * (special_char_len - 1) + len(s_) - effective_len
    while residual_lens > 0:
        residual_lens -= special_char_len if _is_special_char(s_[-1]) else 1
        s_ = s_[:-1]
    return s_, s[len(s_):]


def markdown_to_html_document(markdown_text: str) -> str:
    """把 Markdown 转换为完整的 HTML 文档（供邮件、md2img 等渠道使用）。

    基于 markdown2 并开启表格与代码块支持，内联紧凑易读的 CSS 排版；
    被通知邮件与 md2img 共用。

    Args:
        markdown_text: 原始 Markdown 内容。

    Returns:
        含 DOCTYPE / head / body 的完整 HTML 文档字符串。
    """
    html_content = markdown2.markdown(
        markdown_text,
        extras=["tables", "fenced-code-blocks", "break-on-newline", "cuddled-lists"],
    )

    css_style = """
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
                line-height: 1.5;
                color: #24292e;
                font-size: 14px;
                padding: 15px;
                max-width: 900px;
                margin: 0 auto;
            }
            h1 {
                font-size: 20px;
                border-bottom: 1px solid #eaecef;
                padding-bottom: 0.3em;
                margin-top: 1.2em;
                margin-bottom: 0.8em;
                color: #0366d6;
            }
            h2 {
                font-size: 18px;
                border-bottom: 1px solid #eaecef;
                padding-bottom: 0.3em;
                margin-top: 1.0em;
                margin-bottom: 0.6em;
            }
            h3 {
                font-size: 16px;
                margin-top: 0.8em;
                margin-bottom: 0.4em;
            }
            p {
                margin-top: 0;
                margin-bottom: 8px;
            }
            table {
                border-collapse: collapse;
                width: 100%;
                margin: 12px 0;
                display: block;
                overflow-x: auto;
                font-size: 13px;
            }
            th, td {
                border: 1px solid #dfe2e5;
                padding: 6px 10px;
                text-align: left;
            }
            th {
                background-color: #f6f8fa;
                font-weight: 600;
            }
            tr:nth-child(2n) {
                background-color: #f8f8f8;
            }
            tr:hover {
                background-color: #f1f8ff;
            }
            blockquote {
                color: #6a737d;
                border-left: 0.25em solid #dfe2e5;
                padding: 0 1em;
                margin: 0 0 10px 0;
            }
            code {
                padding: 0.2em 0.4em;
                margin: 0;
                font-size: 85%;
                background-color: rgba(27,31,35,0.05);
                border-radius: 3px;
                font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
            }
            pre {
                padding: 12px;
                overflow: auto;
                line-height: 1.45;
                background-color: #f6f8fa;
                border-radius: 3px;
                margin-bottom: 10px;
            }
            hr {
                height: 0.25em;
                padding: 0;
                margin: 16px 0;
                background-color: #e1e4e8;
                border: 0;
            }
            ul, ol {
                padding-left: 20px;
                margin-bottom: 10px;
            }
            li {
                margin: 2px 0;
            }
        """

    return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                {css_style}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """


def markdown_to_plain_text(markdown_text: str) -> str:
    """
    将 Markdown 转换为纯文本
    
    移除 Markdown 格式标记，保留可读性
    """
    text = markdown_text
    
    # 移除标题标记 # ## ###
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    
    # 移除加粗 **text** -> text
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    
    # 移除斜体 *text* -> text
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    
    # 移除引用 > text -> text
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
    
    # 移除列表标记 - item -> item
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
    
    # 移除分隔线 ---
    text = re.sub(r'^---+$', '────────', text, flags=re.MULTILINE)
    
    # 移除表格语法 |---|---|
    text = re.sub(r'\|[-:]+\|[-:|\s]+\|', '', text)
    text = re.sub(r'^\|(.+)\|$', r'\1', text, flags=re.MULTILINE)
    
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()


def strip_hidden_markdown_metadata(markdown_text: str) -> str:
    """移除绝不能进入通知渠道的内部 Markdown 元数据。"""

    return HIDDEN_MARKDOWN_METADATA_RE.sub("", markdown_text)


def _bytes(s: str) -> int:
    """返回字符串的 UTF-8 字节长度，用于通知负载预算控制。"""
    return len(s.encode('utf-8'))


def utf8_len(s: str) -> int:
    """返回字符串 UTF-8 编码的字节长度，用于渠道负载预算控制。"""

    return len(s.encode("utf-8"))


def _custom_unit_to_index(text: str, budget: int, len_fn: Callable[[str], int]) -> int:
    """在自定义单位（如字节数）预算内，返回安全的 Python 字符串最大下标。

    通过二分查找定位切点，保证 len_fn(text[:idx]) 不超过预算。
    """

    if len_fn(text) <= budget:
        return len(text)
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if len_fn(text[:mid]) <= budget:
            low = mid
        else:
            high = mid - 1
    return low


def _has_unclosed_inline_code(text: str) -> bool:
    """判断候选切点是否落在单个反引号的行内代码区间内。"""

    escaped = False
    count = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and not escaped:
            escaped = True
            index += 1
            continue
        if char == "`" and not escaped:
            if text[index:index + 3] == "```":
                index += 3
                escaped = False
                continue
            count += 1
        escaped = False
        index += 1
    return count % 2 == 1


def _last_unclosed_markdown_link_start(text: str) -> int:
    """当 Markdown 行内链接跨越了拟定切点时，返回其起始位置。"""

    last_open_paren = text.rfind("](")
    if last_open_paren > text.rfind(")"):
        return max(text.rfind("[", 0, last_open_paren), last_open_paren)
    if text.rfind("[") > text.rfind("]"):
        return text.rfind("[")
    return -1


def chunk_markdown_preserving_blocks(
    content: str,
    max_units: int,
    *,
    len_fn: Optional[Callable[[str], int]] = None,
    add_page_marker: bool = False,
) -> List[str]:
    """切分 Markdown，保证不把链接、行内代码或代码围栏切在闭合之前。

    ``len_fn`` 让发送端可以使用协议真实的负载度量。A 股 webhook 通道使用
    :func:`utf8_len`，因为它们的限制按字节计算。
    """

    measure = len_fn or len
    if max_units < MIN_MAX_WORDS:
        raise ValueError(f"max_units={max_units} < {MIN_MAX_WORDS}, 可能陷入无限递归。")
    if measure(content) <= max_units:
        return [content]

    marker_reserve = measure(_page_marker(9998, 9998)) if add_page_marker else 0
    indicator_reserve = measure("\n\n(9999/9999)")
    fence_close = "\n```"
    chunks: List[str] = []
    remaining = content
    carry_language: Optional[str] = None

    while remaining:
        prefix = f"```{carry_language}\n" if carry_language is not None else ""
        # headroom 预留 marker/indicator/前缀/代码围栏关闭符，避免最后一段被切断
        headroom = max_units - marker_reserve - indicator_reserve - measure(prefix) - measure(fence_close)
        if headroom < MIN_MAX_WORDS:
            headroom = max(MIN_MAX_WORDS, max_units - marker_reserve - indicator_reserve - measure(prefix))
        if headroom <= 0:
            raise ValueError("max_units is too small for markdown-preserving chunking")

        if measure(prefix) + measure(remaining) <= max_units - marker_reserve - indicator_reserve:
            chunks.append(prefix + remaining)
            break

        split_limit = _custom_unit_to_index(remaining, headroom, measure)
        region = remaining[:split_limit]
        # 优先按段落/换行/空格回退，保证切分点尽量自然
        split_at = region.rfind("\n\n")
        if split_at < split_limit // 2:
            split_at = region.rfind("\n")
        if split_at < split_limit // 2:
            split_at = region.rfind(" ")
        if split_at < 1:
            split_at = split_limit

        candidate = remaining[:split_at]
        unsafe_start = len(candidate)
        if _has_unclosed_inline_code(candidate):
            unsafe_start = min(unsafe_start, candidate.rfind("`"))
        link_start = _last_unclosed_markdown_link_start(candidate)
        if link_start >= 0:
            unsafe_start = min(unsafe_start, link_start)
        if unsafe_start < len(candidate):
            # 把切点回退到最近的空格/换行，避免切在未闭合的代码或链接里
            safe_split = max(candidate.rfind(" ", 0, unsafe_start), candidate.rfind("\n", 0, unsafe_start))
            if safe_split > 0:
                split_at = safe_split

        chunk_body = remaining[:split_at].rstrip()
        in_code = carry_language is not None
        language = carry_language or ""
        # 扫描当前块体判断是否落在代码围栏内部，以便把围栏延续到下一段
        for line in chunk_body.split("\n"):
            stripped = line.strip()
            if stripped.startswith("```"):
                if in_code:
                    in_code = False
                    language = ""
                else:
                    in_code = True
                    language = (stripped[3:].strip().split() or [""])[0]

        remaining = remaining[split_at:]
        if remaining.startswith("\n"):
            remaining = remaining[1:]
        elif not in_code and remaining.startswith(" "):
            remaining = remaining[1:]

        full_chunk = prefix + chunk_body
        if in_code:
            full_chunk += fence_close
            carry_language = language
        else:
            carry_language = None
        chunks.append(full_chunk)

    if len(chunks) > 1:
        total = len(chunks)
        for index, chunk in enumerate(chunks):
            suffix = f"\n\n({index + 1}/{total})"
            if add_page_marker:
                suffix += _page_marker(index, total)
            chunks[index] = chunk + suffix
    elif add_page_marker:
        chunks[0] = chunks[0] + _page_marker(0, 1)
    return chunks


def _chunk_by_max_bytes(content: str, max_bytes: int) -> List[str]:
    """无法按自然分隔切分时，强制按字节上限切分并在块尾附截断标记。"""
    if _bytes(content) <= max_bytes:
        return [content]
    if max_bytes < MIN_MAX_BYTES:
        raise ValueError(f"max_bytes={max_bytes} < {MIN_MAX_BYTES}, 可能陷入无限递归。")
    
    sections: List[str] = []
    suffix = TRUNCATION_SUFFIX
    effective_max_bytes = max_bytes - _bytes(suffix)
    if effective_max_bytes <= 0:
        effective_max_bytes = max_bytes
        suffix = ""
        
    while True:
        chunk, content = slice_at_max_bytes(content, effective_max_bytes)
        if content.strip() != "":
            sections.append(chunk + suffix)
        else:
            # 最后一段了，直接添加并离开循环
            sections.append(chunk)
            break
    return sections


def chunk_content_by_max_bytes(content: str, max_bytes: int, add_page_marker: bool = False) -> List[str]:
    """
    按字节数智能分割消息内容
    
    Args:
        content: 完整消息内容
        max_bytes: 单条消息最大字节数
        add_page_marker: 是否添加分页标记
        
    Returns:
        分割后的区块列表
    """
    def _chunk(content: str, max_bytes: int) -> List[str]:
        """在字节上限内按自然分隔符递归切分内容。"""
        # 优先按分隔线/标题分割，保证分页自然
        if max_bytes < MIN_MAX_BYTES:
            raise ValueError(f"max_bytes={max_bytes} < {MIN_MAX_BYTES}, 可能陷入无限递归。")
        
        if _bytes(content) <= max_bytes:
            return [content]
        
        sections, separator = _chunk_by_separators(content)
        if separator == "" and len(sections) == 1:
            # 无法智能分割，则强制按字数分割
            return _chunk_by_max_bytes(content, max_bytes)
        
        chunks: List[str] = []
        current_chunk: List[str] = []
        current_bytes = 0
        separator_bytes = _bytes(separator) if separator else 0
        effective_max_bytes = max_bytes - separator_bytes

        for section in sections:
            section += separator
            section_bytes = _bytes(section)
            
            # 如果单个 section 就超长，需要强制截断
            if section_bytes > effective_max_bytes:
                # 先保存当前积累的内容
                if current_chunk:
                    chunks.append("".join(current_chunk))
                    current_chunk = []
                    current_bytes = 0

                # 强制按字节截断，避免整段被截断丢失
                section_chunks = _chunk(
                    section[:-separator_bytes], effective_max_bytes
                )
                section_chunks[-1] = section_chunks[-1] + separator
                chunks.extend(section_chunks)
                continue

            # 检查加入后是否超长
            if current_bytes + section_bytes > effective_max_bytes:
                # 保存当前块，开始新块
                if current_chunk:
                    chunks.append("".join(current_chunk))
                current_chunk = [section]
                current_bytes = section_bytes
            else:
                current_chunk.append(section)
                current_bytes += section_bytes
                
        # 添加最后一块
        if current_chunk:
            chunks.append("".join(current_chunk))
            
        # 移除最后一个块的分割符
        if (chunks and 
            len(chunks[-1]) > separator_bytes and 
            chunks[-1][-separator_bytes:] == separator
        ):
            chunks[-1] = chunks[-1][:-separator_bytes]
        
        return chunks
    
    if add_page_marker:
        max_bytes = max_bytes - PAGE_MARKER_SAFE_BYTES
    
    chunks = _chunk(content, max_bytes)
    if add_page_marker:
        total_chunks = len(chunks)
        for i, chunk in enumerate(chunks):
            chunks[i] = chunk + _page_marker(i, total_chunks)
    return chunks


def slice_at_max_bytes(text: str, max_bytes: int) -> tuple[str, str]:
    """
    按字节数截断字符串，确保不会在多字节字符中间截断

    Args:
        text: 要截断的字符串
        max_bytes: 最大字节数

    Returns:
        (截断后的字符串, 剩余未截断内容)
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, ""

    # 从最大字节数开始向前查找，找到完整的 UTF-8 字符边界
    truncated = encoded[:max_bytes]
    while truncated and (truncated[-1] & 0xC0) == 0x80:
        truncated = truncated[:-1]

    truncated = truncated.decode('utf-8', errors='ignore')
    return truncated, text[len(truncated):]


def format_feishu_markdown(content: str) -> str:
    """
    将通用 Markdown 转换为飞书 lark_md 更友好的格式
    
    转换规则：
    - 飞书不支持 Markdown 标题（# / ## / ###），用加粗代替
    - 引用块使用前缀替代
    - 分隔线统一为细线
    - 表格转换为条目列表
    
    Args:
        content: 原始 Markdown 内容
        
    Returns:
        转换后的飞书 Markdown 格式内容
        
    Example:
        >>> markdown = "# 标题\\n> 引用\\n| 列1 | 列2 |"
        >>> formatted = format_feishu_markdown(markdown)
        >>> print(formatted)
        **标题**
        💬 引用
        • 列1：值1 | 列2：值2
    """
    def _flush_table_rows(buffer: List[str], output: List[str]) -> None:
        """将表格缓冲区中的行转换为飞书格式"""
        if not buffer:
            return

        def _parse_row(row: str) -> List[str]:
            """解析表格行，提取单元格"""
            cells = [c.strip() for c in row.strip().strip('|').split('|')]
            return [c for c in cells if c]

        rows = []
        for raw in buffer:
            # 跳过分隔行（如 |---|---|）
            if re.match(r'^\s*\|?\s*[:-]+\s*(\|\s*[:-]+\s*)+\|?\s*$', raw):
                continue
            parsed = _parse_row(raw)
            if parsed:
                rows.append(parsed)

        if not rows:
            return

        header = rows[0]
        data_rows = rows[1:] if len(rows) > 1 else []
        for row in data_rows:
            pairs = []
            for idx, cell in enumerate(row):
                key = header[idx] if idx < len(header) else f"列{idx + 1}"
                pairs.append(f"{key}：{cell}")
            output.append(f"• {' | '.join(pairs)}")

    lines = []
    table_buffer: List[str] = []

    for raw_line in content.splitlines():
        line = raw_line.rstrip()

        # 处理表格行
        if line.strip().startswith('|'):
            table_buffer.append(line)
            continue

        # 刷新表格缓冲区
        if table_buffer:
            _flush_table_rows(table_buffer, lines)
            table_buffer = []

        # 转换标题（# ## ### 等）
        if re.match(r'^#{1,6}\s+', line):
            title = re.sub(r'^#{1,6}\s+', '', line).strip()
            line = f"**{title}**" if title else ""
        # 转换引用块
        elif line.startswith('> '):
            quote = line[2:].strip()
            line = f"💬 {quote}" if quote else ""
        # 转换分隔线
        elif line.strip() == '---':
            line = '────────'
        # 转换列表项
        elif line.startswith('- '):
            line = f"• {line[2:].strip()}"

        lines.append(line)

    # 处理末尾的表格
    if table_buffer:
        _flush_table_rows(table_buffer, lines)

    return "\n".join(lines).strip()


def _chunk_by_separators(content: str) -> tuple[list[str], str]:
    """
    通过分割线等特殊字符将消息内容分割为多个区块
    
    Args:
        content: 完整消息内容
        
    Returns:
        sections: 分割后的区块列表
        separator: 区块之间的分隔符，None 表示无法分割
    """
    # 智能分割：优先按 "---" 分隔（股票之间的分隔线）
    # 其次尝试各级标题分割
    if "\n---\n" in content:
        sections = content.split("\n---\n")
        separator = "\n---\n"
    elif "\n# " in content:
        # 按 # 分割 (兼容一级标题)
        parts = content.split("\n## ")
        sections = [parts[0]] + [f"## {p}" for p in parts[1:]]
        separator = "\n"
    elif "\n## " in content:
        # 按 ## 分割 (兼容二级标题)
        parts = content.split("\n## ")
        sections = [parts[0]] + [f"## {p}" for p in parts[1:]]
        separator = "\n"
    elif "\n### " in content:
        # 按 ### 分割
        parts = content.split("\n### ")
        sections = [parts[0]] + [f"### {p}" for p in parts[1:]]
        separator = "\n"
    elif "\n**" in content:
        # 按 ** 加粗标题分割 (兼容 AI 未输出标准 Markdown 标题的情况)
        parts = content.split("\n**")
        sections = [parts[0]] + [f"**{p}" for p in parts[1:]]
        separator = "\n"
    elif "\n" in content:
        # 按 \n 分割
        sections = content.split("\n")
        separator = "\n"
    else:
        return [content], ""
    return sections, separator


def _chunk_by_max_words(content: str, max_words: int, special_char_len: int = 2) -> list[str]:
    """
    按字数分割消息内容
    
    Args:
        content: 完整消息内容
        max_words: 单条消息最大字数
        special_char_len: 每个特殊字符的长度，默认为 2
        
    Returns:
        分割后的区块列表
    """
    if _effective_len(content, special_char_len) <= max_words:
        return [content]
    if max_words < MIN_MAX_WORDS:
        raise ValueError(
            f"max_words={max_words} < {MIN_MAX_WORDS}, 可能陷入无限递归。"
        )

    sections = []
    suffix = TRUNCATION_SUFFIX
    effective_max_words = max_words - len(suffix)  # 预留后缀，避免边界超限
    if effective_max_words <= 0:
        effective_max_words = max_words
        suffix = ""

    while True:
        chunk, content = _slice_at_effective_len(content, effective_max_words, special_char_len)
        if content.strip() != "":
            sections.append(chunk + suffix)
        else:
            # 最后一段了，直接添加并离开循环
            sections.append(chunk)
            break
    return sections


def chunk_content_by_max_words(
    content: str, 
    max_words: int, 
    special_char_len: int = 2,
    add_page_marker: bool = False
    ) -> list[str]:
    """
    按字数智能分割消息内容
    
    Args:
        content: 完整消息内容
        max_words: 单条消息最大字数
        special_char_len: 每个特殊字符的长度，默认为 2
        add_page_marker: 是否添加分页标记
        
    Returns:
        分割后的区块列表
    """
    def _chunk(content: str, max_words: int, special_char_len: int = 2) -> list[str]:
        """在字数预算内，按自然分隔符递归切分内容。"""
        if max_words < MIN_MAX_WORDS:
            # Safe guard，避免无限递归
            # 理论上，max_words在每次递归中可以减小到无限小，但实际中不太可能发生，
            # 除非每次_chunk_by_separators都能成功返回分隔符，且max_words初始值太小。
            raise ValueError(f"max_words={max_words} < {MIN_MAX_WORDS}, 可能陷入无限递归。")
        
        if _effective_len(content, special_char_len) <= max_words:
            return [content]

        sections, separator = _chunk_by_separators(content)
        if separator == "" and len(sections) == 1:
            # 无法智能分割，则强制按字数分割
            return _chunk_by_max_words(content, max_words, special_char_len)

        chunks = []
        current_chunk = []
        current_word_len = 0
        separator_len = len(separator) if separator else 0
        effective_max_words = max_words - separator_len # 预留分割符长度，避免边界超限

        for section in sections:
            section += separator
            section_word_len = _effective_len(section, special_char_len)

            # 如果单个 section 就超长，需要强制截断
            if section_word_len > max_words:
                # 先保存当前积累的内容
                if current_chunk:
                    chunks.append("".join(current_chunk))

                # 强制截断这个超长 section
                section_chunks = _chunk(
                    section[:-separator_len], effective_max_words, special_char_len
                    )
                section_chunks[-1] = section_chunks[-1] + separator
                chunks.extend(section_chunks)
                continue

            # 检查加入后是否超长
            if current_word_len + section_word_len > max_words:
                # 保存当前块，开始新块
                if current_chunk:
                    chunks.append("".join(current_chunk))
                current_chunk = [section]
                current_word_len = section_word_len
            else:
                current_chunk.append(section)
                current_word_len += section_word_len

        # 添加最后一块
        if current_chunk:
            chunks.append("".join(current_chunk))

        # 移除最后一个块的分割符
        if (chunks and
            len(chunks[-1]) > separator_len and
            chunks[-1][-separator_len:] == separator
        ):
            chunks[-1] = chunks[-1][:-separator_len]
        return chunks
    
    
    if add_page_marker:
        max_words = max_words - PAGE_MARKER_SAFE_LEN
    
    chunks = _chunk(content, max_words, special_char_len)
    if add_page_marker:
        total_chunks = len(chunks)
        for i, chunk in enumerate(chunks):
            chunks[i] = chunk + _page_marker(i, total_chunks)
    return chunks
