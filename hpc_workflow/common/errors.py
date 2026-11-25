"""工作流可预期错误；调用方保留错误类别而不伪装成成功。"""


class WorkflowError(RuntimeError):
    """可向命令行用户报告的工作流错误。"""


class ConfigError(WorkflowError, ValueError):
    """配置类型、路径语义或组合关系不正确。"""


class ConflictError(WorkflowError):
    """已有任务、锁或输出与本轮操作冲突。"""


class IntegrityError(WorkflowError):
    """输入不可用、交接协议或声明版本关联不成立。"""
