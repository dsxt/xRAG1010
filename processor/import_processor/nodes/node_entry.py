# processor/import_processor/nodes/node_entry.py
import os
from pathlib import Path

from processor.import_processor.base import BaseNode
from processor.import_processor.exceptions import ValidationError, StateFieldError, FileProcessingError
from processor.import_processor.state import ImportGraphState


class NodeEntry(BaseNode):
    """
    入口节点：任务分发
    """

    name = "node_entry"

    def process(self, state: ImportGraphState):

        """
        1.接收状态: 获取 `import_file_path`。
        2.判断类型: 检查文件后缀是 `.pdf` 还是 `.md`。
        3.设置标记: 更新 state 中的 `is_pdf_read_enabled/pdf_path` 或 `is_md_read_enabled/md_path`，供主图路由使用。
        4.提取标题: 从文件名中提取 `file_title`，后续作为元数据。
        :param state: `import_file_path`
        :return: `is_pdf_read_enabled/pdf_path` 或 `is_md_read_enabled/md_path` 、`file_title`
        """

        # 1. 从state中获取文件
        import_file_path = state.get("import_file_path")
        # import_file_path = state["import_file_path"] #这样写的话字段不存在直接报错
        # 判断路径是否为空
        if not import_file_path:
            raise StateFieldError(field_name='import_file_path', expected_type=str)

        # 2. 转换Path标准化对象
        import_file_path_obj = Path(import_file_path)
        # 判断文件是否存在
        if not import_file_path_obj.exists():
            raise FileProcessingError(message=f"文件{import_file_path_obj.name}不存在")

        # 3. 检查文件后缀。同时清掉另一种格式的标记，避免复用 state 时走错分支
        suffix = import_file_path_obj.suffix.lower()
        state["is_pdf_read_enabled"] = False
        state["is_md_read_enabled"] = False
        if suffix == ".pdf":
            state["is_pdf_read_enabled"] = True
            state["pdf_path"] = str(import_file_path_obj)
        elif suffix == ".md":
            state["is_md_read_enabled"] = True
            state["md_path"] = str(import_file_path_obj)
        else:
            raise ValidationError(message=f"该文件的后缀格式{import_file_path_obj.suffix}不支持")

        # 4. 获取上传文件的标题，更新到state中
        state["file_title"] = import_file_path_obj.stem
        if not state.get("file_dir"):
            state["file_dir"] = os.getenv("MD_ROOT_DIR") or r"D:\python\output"

        # 5. 返回state
        return state