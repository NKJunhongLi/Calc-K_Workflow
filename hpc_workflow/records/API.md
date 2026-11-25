# records 运行记录与声明版本接口

本目录保留UUID声明版本、分发版本审计和独立运行快照，Python实现原样保留。无handoff模块或最新筛选索引。路径为本机绝对字符串，产物引用为受限POSIX相对路径；不提供内容完整性证明。__init__.py没有类和函数。

## manifest.py

SCHEMA_VERSION:int=2，工作流版本取hpc_workflow.__version__，当前0.0.2。二者共同限定未修改下游接受的完成协议。

| 声明 | 参数类型 | 返回类型 | 功能、异常及副作用 |
|---|---|---|---|
| new_revision_id | 无 | str | 随机规范小写UUID4，不读文件，不根据内容计算 |
| is_revision_id | value:object | bool | 只接受规范UUID4字符串；非字符串、其他UUID版本或非规范拼写返回False，不抛解析异常 |
| software_versions | 无 | dict[str,str] | 审计hpc_workflow及ase/spglib/numpy/pymatgen/mp-api/pandas分发版本；未安装为not_installed，只查importlib.metadata，不导入科学库或联网 |

审计清单是保留实现，记录可选包not_installed不使其成为新计算流程的运行依赖。UUID表达记录中的输入版本声明，不能识别沿用UUID的人工正文改动，资源也不读取或计算摘要。

## runs.py

| 声明 | 参数类型与默认值 | 返回类型 | 功能、异常及副作用 |
|---|---|---|---|
| utc_now | 无 | str | 带UTC时区的ISO时间，无文件I/O |
| create_run | output_root:str, workflow:str, config:dict | dict | 先public_config标准化快照，再独占创建output_root/runs/时间戳-随机后缀，原子写running记录；返回run_id/directory/record；配置或写盘错误上抛 |
| finish_run | context:dict, status:str, summary:dict或None=None, error:str或None=None | None | 复制记录、设置结束时间/终态/summary/可选error；原子写成功后才更新context.record，不删除任务 |

run_manifest.json包含schema_version、workflow_version、run_id、workflow、status、started_at、finished_at、config、software_versions和终态summary/error。新入口workflow为pre，无screening_source，不建立handoff或latest索引。running不是成功证据。

## 调用与迁移

main.run_pre先全局验证，再create_run→执行pre→finish_run；异常和键盘中断登记failed/interrupted后重抛。逐任务UUID及完成记录由preprocess组织。任务/运行内部引用尽量相对工作根，迁移仍需正确指定当前主机工作根；不自动接管旧现场。

配置快照语义见[common/API.md](../common/API.md)，四份配置完整说明见[根API.md](../../API.md)。
