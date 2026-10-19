# processor/import_processor/nodes/node_document_split.py
import json
import logging
import re
from pathlib import Path
from typing import Tuple, List, Dict

from langchain_text_splitters import RecursiveCharacterTextSplitter

from processor.import_processor.base import BaseNode, setup_logging
from processor.import_processor.exceptions import StateFieldError
from processor.import_processor.state import ImportGraphState

class NodeDocumentSplit(BaseNode):
    """
    节点: 文档切分 (node_document_split)
    将长文档切分成小的 Chunks (切片) 以便检索。
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_document_split"

    def process(self, state: ImportGraphState) -> ImportGraphState:
        """
        节点：文档切分（node_document_split）
        整体流程：加载输入→按MD标题初切→长切短合→统计输出→结果备份
        核心目的：将长MD文档切分为长度适中的Chunk，适配大模型上下文窗口和向量检索
        后续扩展点：可在各步骤间新增Chunk元信息补充、自定义切分规则、向量入库前置处理等

        必要参数：md_content、file_title
        更新参数：chunks

        :param state: 工作流状态对象
        :return: 更新后的状态对象
        """
        # 步骤1：加载并标准化输入数据
        content, file_title = self._step_1_get_inputs(state)

        # 步骤2：按MD标题进行初次切分
        sections, title_count, lines_count = self._step_2_split_by_titles(content, file_title)

        # 步骤3：无标题场景兜底处理
        sections = self._step_3_handle_no_title(content, sections, title_count, file_title)

        # 步骤4：Chunk精细化处理（长切短合
        sections = self._step_4_refine_chunks(sections)
        for section in sections:
            print(f"section-title: {section['title']}")
            # print(f"section-content: {section['content']}")
        print(f"标题数: {title_count}")
        print(f"行数: {lines_count}")

        # 步骤5：输出文档切分统计信息
        self._step_5_print_stats(lines_count, sections)

        # 步骤6：Chunk结果本地JSON备份
        self._step_6_backup(state, sections)

        # 写入状态字典
        state["chunks"] = sections
        return state

    def _step_1_get_inputs(self, state: ImportGraphState) -> Tuple[str, str]:
        """
        【步骤1】获取并预处理输入数据
        功能：从状态字典中提取MD内容/文件标题/最大长度，做基础标准化
        :param state: 项目状态字典（ImportGraphState），包含md_content等核心键
        :return: 标准化后的MD内容/文件标题
        """
        # 1、非空校验
        file_title = state.get("file_title")
        if not file_title:
            raise StateFieldError(field_name="file_title", message="文件标题不能为空", expected_type=str)

        md_content = state.get("md_content")
        if not md_content:
            raise StateFieldError(field_name="md_content", message="文件内容不能为空", expected_type=str)

        # 2、基础标准化：统一换行符
        md_content = md_content.replace("\r\n", "\n").replace("\r", "\n")

        # 防御性去重——连续 4 次以上重复的长片段（>=10字）压成 1 次
        # 注意：这只是兜底，根因应去修上游（OCR / 清洗 / 拼接）节点
        original_len = len(md_content)
        md_content = re.sub(r'([^\n]{10,200}?)(?:\1){3,}', r'\1', md_content)
        if len(md_content) < original_len:
            self.logger.warning(
                f"步骤1：检测到疑似重复内容，已压缩 {original_len - len(md_content)} 字符，请排查上游数据源"
            )

        return md_content, file_title

    def _step_2_split_by_titles(self, content: str, file_title: str):
        title_pattern = r'^\s*#{1,6}\s+.+'

        lines = content.split("\n")
        sections, title_count = [], 0
        current_title, current_lines = "", []
        in_code_block = False
        code_block_char = None  # 记录开始围栏的字符：` 或 ~
        code_block_len = 0  # 记录开始围栏的长度

        def _flush_section():
            if not current_lines:
                return
            sections.append({
                "title": current_title,
                "content": "\n".join(current_lines),
                "file_title": file_title,
                "group_id": len(sections),
            })

        for line in lines:
            stripped_line = line.strip()

            # ---------- 代码块状态机 ----------
            if not in_code_block:
                # 尝试匹配「代码块开始标记」
                # 规则：最多3空格 + 3+个`或~ + 可选 info string（且 info 不含同种围栏字符）
                start_match = re.match(r'^( {0,3})(`{3,}|~{3,})(.*)$', line)
                if start_match:
                    fence = start_match.group(2)
                    info = start_match.group(3)
                    fence_char = fence[0]
                    if fence_char not in info:  # info string 不含同种围栏符 → 合法
                        in_code_block = True
                        code_block_char = fence_char
                        code_block_len = len(fence)
                        current_lines.append(line)
                        continue
                    # 否则落到下面，当普通行处理
            else:
                # 尝试匹配「代码块结束标记」
                # 规则：最多3空格 + 与开始同种字符 + 长度 >= 开始长度 + 行尾只有空格
                end_match = re.match(r'^( {0,3})(`{3,}|~{3,})[ \t]*$', line)
                if end_match:
                    fence = end_match.group(2)
                    if fence[0] == code_block_char and len(fence) >= code_block_len:
                        in_code_block = False
                        code_block_char = None
                        code_block_len = 0
                        current_lines.append(line)
                        continue
                # 代码块内部的所有行（包括 "````123~~~abc```````"）原样保留
                current_lines.append(line)
                continue

            # ---------- 标题识别 ----------
            if re.match(title_pattern, line):
                _flush_section()
                current_title = stripped_line
                current_lines = [current_title]
                title_count += 1
                self.logger.info(f"识别标题：{current_title}")
            else:
                current_lines.append(line)

        _flush_section()
        self.logger.info(f"文档粗切完成，共{len(sections)}章节，标题数{title_count}，共{len(lines)}行")
        return sections, title_count, len(lines)

    def _step_3_handle_no_title(self, content: str, sections: List[Dict[str, str]], title_count: int,
                                file_title: str) -> List[Dict[str, str]]:
        """
        【步骤3】无标题兜底处理
        功能：若MD中未识别到任何标题，将全文作为一个整体处理，避免后续逻辑异常
        :param content: 标准化后的MD完整内容
        :param sections: 步骤2切分后的章节列表
        :param title_count: 步骤2识别的有效标题数量
        :param file_title: 所属文件标题
        :return: 兜底后的章节列表
        """
        if title_count == 0:
            # 无标题情况：替换为单章节，标题为"无标题"
            self.logger.warning(f"步骤3：未识别到任何MD标题，将全文作为单个章节处理，文件：{file_title}")
            return [{"title": "无标题", "content": content, "file_title": file_title,"group_id": 0, }]
        # 有标题情况：直接返回步骤2的结果
        self.logger.debug(f"步骤3：检测到{title_count}个有效标题，无需兜底处理")
        return sections

    def _step_4_refine_chunks(self, sections: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        【步骤4】Chunk精细化处理（核心：长切短合，适配大模型/检索）
        执行流程：1.切分超长章节 2.合并过短章节 3.父标题兜底（适配Milvus向量库schema）
        :param sections: 步骤3处理后的章节列表
        :return: 长度适中、低碎片化的最终Chunk列表
        """

        # 阶段1：切分超长章节 → 所有章节长度控制在最大长度内
        refined_split = []
        for sec in sections:
            # 对每个章节执行超长切分，结果平铺加入列表（避免嵌套）
            refined_split.extend(self._split_long_section(sec))
        self.logger.info(f"步骤4-1：超长章节切分完成，共生成{len(refined_split)}个初始子Chunk")

        # 阶段2：合并过短章节 → 减少碎片化，提升后续检索/大模型调用效果
        final_sections = self._merge_short_sections(refined_split)
        self.logger.info(f"步骤4-2：过短章节合并完成，最终得到{len(final_sections)}个Chunk")

        # 阶段3：过滤纯标点/空白垃圾块（如 "## 警告\n\n。"）
        before = len(final_sections)
        final_sections = [
            s for s in final_sections
            if re.search(r'[\w\u4e00-\u9fff]', s.get("content", ""))
        ]
        if len(final_sections) < before:
            self.logger.warning(f"步骤4-3：过滤纯标点/空白块 {before - len(final_sections)} 个")

        # 阶段4：父标题兜底 → 适配Milvus向量库schema（parent_title为必填字段）
        # 兜底规则：无parent_title则用自身title，title也无则填空字符串
        for sec in final_sections:
            if not sec.get("parent_title"):
                sec["parent_title"] = sec.get("title") or ""
        self.logger.debug(f"步骤4-4：父标题兜底完成，所有Chunk均包含parent_title字段")

        return final_sections

    def _split_long_section(self, section: Dict[str, str]) -> List[Dict[str, str]]:
        """
        【辅助函数】超长章节二次切分（核心适配LangChain分割器）
        功能：单个章节内容超限时，按「段落→句子→空格」从粗到细切分，保留语义
        切分规则：1.先按空行(段落) 2.再按换行 3.最后按中英文标点/空格
        :param section: 原始章节字典，必须包含content键，可选title/file_title等
        :return: 切分后的子章节列表，每个子章节带父标题/序号等元信息
        """
        # 内容空值兜底：无内容直接返回原章节
        content = section.get("content", "")
        # 长度未超限，无需切分，直接返回原章节（列表格式保持统一）
        if len(content) <= self.config.max_content_length:
            return [section]

        # 提取章节标题，用于组装子Chunk前缀（保留标题上下文）
        title = section.get("title", "")
        # 标题前缀：带空行分隔，与正文区分开
        prefix = f"{title}\n\n" if title else ""
        # 计算正文可用长度：总长度 - 标题前缀长度（避免标题占满Chunk额度）
        available_len = self.config.max_content_length - len(prefix)
        # 极端情况：标题长度超过阈值，无法切分，返回原章节
        if available_len <= 0:
            self.logger.warning(f"章节标题过长，无法切分：{title[:20]}...")
            return [section]

        # 清理正文重复标题：避免原章节中正文开头重复标题，导致子Chunk内容冗余
        body = content
        if title and body.lstrip().startswith(title):
            body = body[body.find(title) + len(title):].lstrip()

        # 初始化LangChain递归分割器（核心工具：按优先级分隔符切分，保留语义）
        # separators：分割符优先级（从粗到细），优先按大语义单元切分，最后才硬拆
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=available_len,  # 正文部分最大长度（已扣除标题）
            chunk_overlap=0,  # 无重叠：按标题切分后语义完整，无需重叠
            # 分割符优先级：空行(段落)→换行→中文标点→英文标点→空格，最后硬拆（在 chunk_size 位置强制切断）
            # 先用第一个分隔符进行切分，切分后如果某个 Chunk 还是超过 chunk_size，则继续用下一个优先级的分隔符切分
            separators=["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " "],
        )

        # 切分正文并组装子章节（带完整元信息，便于溯源）
        sub_sections = []

        # 遍历切分后的每个文本块，idx 从 1 开始计数
        for idx, chunk in enumerate(splitter.split_text(body), start=1):

            # 清理空内容：跳过切分后的空字符串
            text = chunk.strip()
            if not text:
                continue
            # 过滤纯标点/空白块（没有字母/数字/汉字）
            if not re.search(r'[\w\u4e00-\u9fff]', text):
                continue
            # 组装子Chunk完整内容 = 标题前缀 + 切分后的正文
            full_text = (prefix + text).strip()

            # 子章节元信息：保留父级关联，添加序号，便于后续检索/溯源
            sub_sections.append({
                "title": f"{title}-{idx}" if title else f"chunk-{idx}",  # 子Chunk标题（带序号）
                "content": full_text,  # 切分后的完整内容
                "parent_title": title,  # 父章节标题（用于后续合并）
                "part": idx,  # 子Chunk序号
                "file_title": section.get("file_title"),  # 所属文件标题
                "group_id": section.get("group_id"),
            })

        self.logger.debug(f"超长章节切分完成：{title} → 生成{len(sub_sections)}个子Chunk")
        return sub_sections

    def _merge_short_sections(self, sections: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if not sections:
            self.logger.debug("待合并Chunk列表为空，直接返回")
            return []

        merged_sections = []
        current_chunk = None

        def _group_key(sec):
            # 优先用唯一 group_id；兜底 parent_title/title（兼容旧数据）
            gid = sec.get("group_id")
            if gid is not None:
                return gid
            # 有 parent_title（超长切分的子块）用它；否则用 title（原章节自身）
            return sec.get("parent_title") or sec.get("title") or ""

        for sec in sections:
            if current_chunk is None:
                current_chunk = sec
                continue

            is_current_short = len(current_chunk["content"]) < self.config.min_content_length
            is_same_parent = _group_key(current_chunk) == _group_key(sec)
            # 合并后不能超过 max_content_length
            merged_len = len(current_chunk["content"]) + len(sec["content"]) + 2
            can_merge = (
                is_current_short
                and is_same_parent
                and merged_len <= self.config.max_content_length
            )

            if can_merge:
                parent_title = sec.get("parent_title", "")
                next_content = sec["content"]
                if parent_title and next_content.startswith(parent_title):
                    next_content = next_content[len(parent_title):].lstrip()
                current_chunk["content"] += "\n\n" + next_content
                # 同步更新 title，避免 "title=警告-1 / part=2" 错位
                if "part" in sec:
                    current_chunk["part"] = sec["part"]
                    base = current_chunk.get("parent_title") or current_chunk.get("title") or ""
                    current_chunk["title"] = f"{base}-{sec['part']}" if base else f"chunk-{sec['part']}"
                self.logger.debug(
                    f"合并短Chunk：{current_chunk.get('parent_title')} → 累计长度{len(current_chunk['content'])}")
            else:
                merged_sections.append(current_chunk)
                current_chunk = sec

        if current_chunk is not None:
            merged_sections.append(current_chunk)

        self.logger.debug(f"短Chunk合并完成：原{len(sections)}个 → 合并后{len(merged_sections)}个")
        return merged_sections

    def _step_5_print_stats(self, lines_count: int, sections: List[Dict[str, str]]) -> None:
        """
        【步骤5】输出文档切分统计信息（日志记录，便于监控/调试）
        :param lines_count: MD原始文本总行数
        :param sections: 最终处理后的Chunk列表
        """
        chunk_num = len(sections)
        # 输出核心统计信息：原始行数/最终Chunk数/首个Chunk预览
        self.logger.info("-" * 50 + " 文档切分统计信息 " + "-" * 50)
        self.logger.info(f"MD原始文本总行数：{lines_count}")
        self.logger.info(f"最终生成Chunk数量：{chunk_num}")

    def _step_6_backup(self, state: ImportGraphState, sections: List[Dict[str, str]]) -> None:
        """
        【步骤6】Chunk结果本地JSON备份（便于调试/问题排查，保留处理结果）
        :param state: 项目状态字典，需包含md_dir（备份目录）
        :param sections: 最终处理后的Chunk列表
        """

        try:
            # 备份目录跟导入输出目录保持一致，不再写死盘符
            backup_root = state.get("file_dir") or "D:/python/output"
            backup_dir = Path(backup_root) / (state.get("file_title") or "unknown")
            backup_dir.mkdir(parents=True, exist_ok=True)
            print(f"backup_dir:{backup_dir}")
            backup_path = backup_dir / "chunks.json"
            # 写入JSON文件：保留中文/格式化缩进，便于人工查看
            with open(backup_path, "w", encoding="utf-8") as f:
                """
                sections是Python 嵌套数据结构（List[Dict[str, str]]，列表里装字典，字典里可能嵌套字符串 / 数字等），而普通文件写入
                （如f.write(sections)）仅支持写入字符串，直接写 Python 数据结构会报错。
                json.dump的核心作用就是：将 Python 原生数据结构（列表、字典、字符串、数字等）直接序列化并写入 JSON 文件，无需手动转换为字符串，
                同时保证数据格式规范、可跨语言 / 跨场景读取，完美适配「Chunk 列表备份」的需求。
                """
                json.dump(
                    sections,
                    f,
                    # 开启 True："title": "\u4e00\u7ea7\u6807\u9898"（乱码，无法直接看）；
                    # 开启 False："title": "一级标题"（正常中文，人工可直接阅读）。
                    ensure_ascii=False,  # 保留中文，不转义为\u编码
                    indent=2  # 格式化缩进，便于阅读
                )
            self.logger.info(f"步骤6：Chunk结果备份成功，备份文件路径：{backup_path}")
        except Exception as e:
            # 备份失败仅记录日志，不终止主流程
            self.logger.error(f"步骤6：Chunk结果备份失败，错误信息：{str(e)}", exc_info=True)

if __name__ == "__main__":

    setup_logging()

    # md_path = r"D:\python\output\hak180产品安全手册\hak180产品安全手册_new.md"
    md_path = r"D:\python\output\华为擎云B530 用户指南-(PUCZ,Windows11_03,zh-cn)\华为擎云B530 用户指南-(PUCZ,Windows11_03,zh-cn)_new.md"
    with open(md_path, "r", encoding="utf-8") as f:
        md_content = f.read()

    init_state = {
        "md_path": md_path,
        "md_content": md_content,
        "file_title": "华为擎云B530 用户指南-(PUCZ,Windows11_03,zh-cn)"
    }
    # 执行文档切分节点
    node_document_split = NodeDocumentSplit()
    result = node_document_split(init_state)

    logging.getLogger().info(json.dumps(result, ensure_ascii=False, indent=4))