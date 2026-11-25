# inputs 本地来源记录接口

本目录只声明内存记录及真实元素集合，不解析CSV、不联网、不写文件。文件发现由preprocess.planner负责，结构读取由preprocess.validation负责。__init__.py没有自定义函数或类。

## models.py

ELEMENTS:frozenset[str]包含H至Og共118个真实元素符号。资源模块和post读者仍从此路径导入，因此其位置与含义保持；它不是元素黑名单或材料筛选规则。

InputRecord为frozen dataclass，字段如下：

| 字段/构造参数 | 类型与默认 | 含义 |
|---|---|---|
| input_source | str，必填 | 本入口固定LOCAL；不由用户配置 |
| source_record_id | str，必填 | 相对源父目录的完整文件名，含扩展名，稳定任务身份的一部分 |
| source_file | str，必填 | 源直接文件名，用于定位与manifest审计；当前单层输入下与source_record_id相同 |
| formula | str="" | 读取成功后从真实结构得到的化学式；读取失败时尚无该信息 |
| elements | tuple[str,...]=() | 读取成功后从真实结构取得的首现顺序去重元素 |

无自定义方法；字段不可重绑。pipeline通过dataclasses.replace补入实际化学信息，不制造CSV行号、排序值、样本号、priority、Ehull、nominal值或metadata空对象。

## 调用关系与身份边界

preprocess.planner.discover_inputs → InputRecord(LOCAL,name,name) → build_plans → pipeline逐文件读取 → replace化学信息 → writer.write_task。

命名空间由pre_config的dataset_namespace提供，默认local。命名空间、来源和含扩展名源ID决定身份，化学式不参与编号；增加其他文件不改变原文件身份。文件内容改变不自动产生新UUID，这不是内容去重系统。

本目录不再有mp.py、mjm.py、InputIssue或CSV解析辅助函数。原样保留的post自己的MP/MJM兼容分支不代表本项目仍支持这些输入。pre配置完整表见[根API.md](../../API.md)。

