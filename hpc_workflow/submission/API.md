# Slurm 提交模块开发接口

本目录负责计划、提交和外层进度；科学转换及 zstar 内部断点逻辑属于 `calculations` 和原生 zstar。导入模块没有副作用。所有路径参数是 `str`；真实 I/O 使用当前主机格式，记录中的任务引用统一 POSIX 相对路径。调用者不得把历史机器绝对地址重新当作运行依赖。

## 记录与接口契约

每轮位于 `work/.hpc_runs/<stage>/<run_id>/`。永久管理记录仅 `plan.json`、`jobs.csv` 和 `batches/0001/progress.json`；作业 stdout/stderr 直接进入同批 Slurm 原始日志，不复制到第二份文件。`.controller.lock`、`.worker.lock`、`.hpc-worker.lock` 为一字节内核锁哨兵，锁文件保留稳定 inode，不是状态记录；锁随进程死亡自动释放，不依赖删除文件或年龄判断。集群共享文件系统必须支持跨节点的 POSIX advisory flock，不能在不支持锁的挂载上声称并发安全。

- `plan.json`：`schema_version:int=2`、`workflow_version:str='0.0.2'`、`stage:str`、`run_id:str`、`task_root:str='../../..'`、`created_at:str`、`parallel_jobs:int`、`execution:dict`、`tasks:list[dict]`、`groups:list[dict]`。每个任务包含 `path:str`、`input_revision_id:str`、`source_input_revision_id:str`、`calculation_subdir:str`、`scientific_input:dict`；zstar 另有 `source_relax_revision_id:str`、`status:str`，可带简短 `reason/evidence/symmetry`。每组包含 `batch:int`、`tasks:list[str]`。只有 `planned` 成员进入分组。
- `execution` 是本轮必要命令和科学参数，冻结后 worker 不重新加载配置。调度器路径、用户完整配置及 API 密钥不进入它。
- `jobs.csv` 列固定为 `batch,job_id,submit_status,submitted_at,stdout,stderr,reason`。日志路径相对计划目录；提交状态为 `SUBMITTING/SUBMITTED/FAILED/UNKNOWN`。
- `progress.json`：`schema_version:int=2`、`workflow_version:str='0.0.2'`、`batch:int`、`status:str`、`current_task:str\|None`、`current_command:str\|None`、`current_started_at:str\|None`、`started_at/updated_at:str`、`finished:list[dict]`。已结束条目保存 `path/status` 及 plan.tasks 的五个版本关联字段，必要时附短原因、证据引用、外层步骤和后果摘要。一个批次只有 `current_task` 可以计作正在执行。
- 计算适配器的回调为 `on_command(label:str,args:list[str],cwd:str)->dict`；返回包含 `returncode:int`、`output_tail:str`（仅内存）、`evidence:list[str]`，必要时附 `status/reason`。最终写出的 progress 不复制输出尾部。

## models.py

| 函数 | 输入 | 返回与功能 |
| --- | --- | --- |
| `utc_now()` | 无 | `str`；带 UTC 时区的微秒级 ISO 时间（使连续建计划仍按真实先后排序），统一计划、提交和边界事件时间。 |
| `short_reason(value, limit=240)` | `value:object`、`limit:int` | `str`；合并空白并截断，完整详情引用原始日志。 |

常量 `JOB_FIELDS:tuple[str,...]` 固定 CSV schema；`ACTIVE_SLURM_STATES:frozenset[str]` 列明排队/运行/完成清理等非终态；`TERMINAL_SUCCESS/PHYSICAL_REJECTION:str` 与 `FAILURE_STATES:frozenset[str]` 分开技术失败和物理拒绝。

## state.py

`RuntimeLock` 是本计算层专用上下文管理器，不改动旧 common.FileLock。POSIX 使用非阻塞 `fcntl.flock`；Windows 本地测试使用非阻塞 `msvcrt.locking`。取得失败抛 `ConflictError`，不会抢占活动进程。

| 类/方法 | 输入 | 返回与功能 |
| --- | --- | --- |
| `RuntimeLock(path)` / `__init__` | `path:str` | `RuntimeLock`；构造只保存路径和空句柄，不创建文件。 |
| `RuntimeLock.__enter__()` | 无额外参数 | `RuntimeLock`；打开一字节稳定哨兵并取得内核排他锁，拒绝链接；资源冲突/锁不支持抛 `ConflictError`。 |
| `RuntimeLock.__exit__(exc_type,exc_value,traceback)` | 异常类型/异常对象/traceback 或各为 `None` | `bool=False`；释放锁、关闭句柄，不压制正文异常且不 unlink。OOM/SIGKILL 时内核执行同类清理。 |

| 函数 | 输入 | 返回与功能 |
| --- | --- | --- |
| `work_root_for_plan(plan_path)` | `plan_path:str` | `str`；从固定目录布局推断当前 work，检查阶段层级与链接，拒绝依赖旧绝对地址。 |
| `load_plan(plan_path)` | `plan_path:str` | `dict`；只读 JSON，核对版本/阶段、相对引用、唯一任务、合法 UUID、科学设置字典和完整分组；旧 schema1 可读但不能提交/执行；错误抛 `IntegrityError` 或路径校验错误。 |
| `list_plans(work_root,stage)` | `work_root:str, stage:str` | `list[str]`；只列固定层级中的计划路径，按运行目录排序，不递归任意树。 |
| `batch_directory(plan_path,batch)` | `plan_path:str, batch:int>=1` | `str`；构造批次目录，不创建；错误编号抛 `IntegrityError`。 |
| `read_jobs(plan_path)` | `plan_path:str` | `list[dict[str,str]]`；读唯一 CSV；尚不存在返回空列表，列不符拒绝。 |
| `write_jobs(plan_path,rows)` | `plan_path:str, rows:list[dict]` | `None`；控制器锁内原子替换提交台账。 |
| `read_progress(plan_path,batch)` | `plan_path:str,batch:int` | `dict`；尚无进度返回 `{}`，格式或 batch 不匹配拒绝。 |
| `write_progress(plan_path,batch,progress)` | `plan_path:str,batch:int,progress:dict` | `None`；更新 `updated_at` 并原子发布；调用者只在边界事件写出。 |

## discovery.py

| 函数 | 输入 | 返回与功能 |
| --- | --- | --- |
| `checked_task_directory(work_root,relative)` | `work_root:str, relative:str` | `str`；恢复当前任务位置，核对 realpath 包含关系和祖先无符号链接；越界抛 `IntegrityError`。 |
| `task_directories(work_root)` | `work_root:str` | `list[str]`；识别直接子目录中有 `STRU` 或 `task_manifest.json` 的任务，明确排除隐藏目录和 `.staging-` 临时目录，旧散装目录可被发现，但没有当前 pre manifest 时不能进入当前版本计算；根不存在拒绝。 |
| `previous_tasks(work_root,stage,config=None)` | `work_root:str,stage:str,config:dict\|None` | `dict[str,dict]`；只读当前协议计划和简短终态，按任务相对路径及配置选定阶段目录保留最近条目；私有 `_execution` 仅用于内存中输入版本关联。 |
| `active_tasks(work_root,stage,config,exclude_plan=None)` | `work_root:str,stage:str,config:dict,exclude_plan:str\|None` | `dict[str,dict]`；查询历史受理作业，未结束成员标活动；队列未知或回执未知均保守保护，已结束成员不受后续批内成员占用。排除计划用于本轮 submit 自检。 |

## planner.py

| 函数 | 输入 | 返回与功能 |
| --- | --- | --- |
| `balanced_groups(paths,parallel_jobs)` | `paths:list[str],parallel_jobs:int` | `list[dict]`；0 每结构一组，1 全串行，其他精确生成该数量的非空均衡组，保留顺序。负数/布尔值/大于有效结构数抛 `ConfigError`；空任务只接受 0。 |
| `calculations_for(stage)` | `stage:str` | `tuple[Callable,Callable,Callable]`；依次返回 `prepare/inspect/run` 适配器；延迟导入科学依赖，非法阶段拒绝。 |
| `scan_tasks(stage,config,prepare=False)` | `stage:str,config:dict,prepare:bool` | `list[dict]`；逐结构关联历史输入和活动作业。`False` 严格只读；`True` 准备新阶段输入，单个硬错误登记 `input_failed` 后继续。完成/物理拒绝需关联声明版本、科学设置原值及历史终态。 |
| `create_plan(stage,config)` | `stage:str,config:dict` | `tuple[str,dict]`；先准备、按有效待算数分组，再发布唯一计划；分组失败仍发布 `status=prepared_not_submitted`、groups=[]、已准备条目为 pending 的计划保存 UUID，然后抛 ConfigError；该计划禁止 submit，修正数量后新建正常计划沿用已准备版本。需由调用者持有控制器锁。 |

`EXECUTION_KEYS:tuple[str,...]` 是冻结字段白名单。新增会改变任务行为的科学参数或命令参数时，需同步白名单和配置校验；不要冻结 scheduler 命令、无关密码和旧绝对根。

## slurm.py

| 函数 | 输入 | 返回与功能 |
| --- | --- | --- |
| `_scheduler(argv,config)` | `argv:list[str],config:dict` | `subprocess.CompletedProcess`；无 shell、禁 stdin、捕获短响应、调度命令超时；计算本身不受此短超时限制。 |
| `query_jobs(job_ids,config)` | `job_ids:list[str],config:dict` | `dict[str,dict]`；先 squeue 后 sacct，只关联主 job ID，不混入 `.batch/.extern`；不可确认返回 `UNKNOWN`。每项含 `state/reason`。 |
| `submission_tag(plan,batch)` | `plan:dict,batch:int` | `str`；本轮/阶段/批次唯一作业名，支持回执丢失后的核对。 |
| `reconcile_submission(plan,batch,config)` | `plan:dict,batch:int,config:dict` | `str\|None`；按唯一作业名查 squeue/sacct，唯一匹配返回 job ID，其余保持不确定，不授权重提。 |
| `submit_batch(plan_path,plan,batch,config)` | `plan_path:str,plan:dict,batch:int,config:dict` | `dict[str,str]`；一次 `sbatch --parsable`，传入 Python argv/CLI/计划/批次；标准 `id;cluster` 回执解析成功，超时或不可解析为 `UNKNOWN`。只有明确未受理故障登记 `FAILED`。 |

用户 Slurm 脚本在配置环境后必须 `exec "$@"`；控制器传入完整 Python argv，不在 shell 中再次拆分字符串。资源由用户 `#SBATCH` 与 Slurm 决定。

## worker.py

| 函数 | 输入 | 返回与功能 |
| --- | --- | --- |
| `_emit(stream,data)` | `stream:TextIO, data:bytes` | `None`；优先原始 bytes 转发并 flush，测试文本流采用 UTF-8 replacement 解码。 |
| `_stream_command(args,cwd)` | `args:list[str],cwd:str` | `tuple[int,str]`；两个线程持续排空 stdout/stderr，各自进入 Slurm 原流，每路只在内存保留最后 128 KiB；返回退出码和合并尾部。不新建第二份日志。 |
| `_stream_command.pump(pipe,destination)` | `pipe:BufferedReader,destination:TextIO` | `None`；内部线程函数，`read1` 避免等待满缓冲造成长时静默；尾部有界、关闭管道。 |
| `run_worker(stage,plan_path,batch)` | `stage:str,plan_path:str,batch:int` | `dict`；核对计划，持批次锁并逐任务持任务锁，核对排队后的声明版本及来源 UUID；顺序遍历所有成员并原子发布当前任务/外层命令和短终态。最终仅技术/输入失败导致 `partial_failed`，物理拒绝分开保留。不重新 prepare 或对称化。 |
| `run_worker.on_command(label,args,cwd)` | `label:str,args:list[str],cwd:str` | `dict`；内部适配器回调，写命令分隔标记、流式执行，返回当前命令退出码/有界尾部/短原因/原始日志证据；不代替 native 内部续算。 |

## pipeline.py

| 函数 | 输入 | 返回与功能 |
| --- | --- | --- |
| `submit_plan(stage,config,plan_path)` | `stage:str,config:dict,plan_path:str` | `dict`；校验计划根和阶段，防其他活动计划重复计算；先持久化 `SUBMITTING` 后 sbatch；已受理不重提，未知先核对，仍未知保留现场。调用者持控制器锁。返回阶段、计划、任务/批次数、提交数、台账行和整体状态。 |
| `run(stage,action,config,plan_path=None)` | `stage:str,action:str,config:dict,plan_path:str\|None` | `dict`；scan 只读；plan 持锁准备输入发布计划；run/resume 生成新计划并提交；submit 强制指定计划。status 与 worker 由独立入口处理，未知动作抛 `ConfigError`。 |

## 配置与状态显示模块

`config.py`、`status.py` 的逐项接口由同目录配套补充章节说明。它们分别承担独立配置加载以及纯只读状态展示；计算loader独立于新的pre配置白名单和本地目录输入。

## config.py：独立加载与校验

| 声明 | 输入类型 | 返回类型与职责 |
|---|---|---|
| command_argv | value:str/list/tuple, name:str | list[str]；POSIX 分词或复制 argv；拒绝空/NUL/换行，不启动进程 |
| _positive | value:object, name:str | None；只接受有限正 int/float，拒绝 bool；错误 ConfigError |
| validate_config | config:dict, stage:str | None；只读校验阶段、作业数、目录、数值和命令占位符，命令在内存归一化为 argv；不建立目录、不探测资源 |
| load_config | stage:str, custom_path:str/None=None | dict；固定模板深复制并叠加 CONFIG，拒绝未知键。路径以声明该值的默认/自定义文件为基准，默认脚本不会被自定义文件目录错误重定位；附 stage/_config_path |

`stage` 仅 relax/zstar。`parallel_jobs` 不接受 bool；两个计算子目录必须不同，不接受绝对路径、分隔符、冒号或隐藏目录；正式 symprec 固定1e-3、angle_tolerance固定-1，其他扫描容差仅诊断。自定义 Python 配置为用户可信代码，沿用只编译源码的加载方式，不在配置旁写 pycache。本项目common只加载pre，计算loader共享_read_custom，计算配置与算法本身保持原实现。

## status.py：纯只读状态投影

| 声明 | 输入类型 | 返回类型与职责 |
|---|---|---|
| _timestamp | value:object | float；ISO时间转秒，未知返回0，仅用于排除陈旧内层状态 |
| _native_step | task_dir:str, config:dict, progress:dict | str；outer=run时直接读 .zstar/stages JSON，显示当前 name 与 SCF/BAND/PYATB 等；过滤当前结构启动前的陈旧文件；pre/post/static直接显示外层命令 |
| _job_state | row:dict, queue:dict | tuple[str,str]；结合回执和查询，返回队列状态及短原因；未知不当作未提交 |
| collect_status | stage:str, config:dict, plan_path:str/None=None | dict；读取明确一轮或本根所有阶段计划，按最新轮去重任务，读取 jobs/progress 与实时队列。只有 current_task 计运行；其他未执行成员为批内等待。终态只比较 plan/progress 已读记录的协议及全部版本字段，不复验当前输入、日志或产物；旧协议终态显示为待核实。计划/作业/批次及 finished 索引在本次调用内复用。默认只展示配置选定阶段目录，显式 --plan 可查看其他目录历史。返回 work_root/plans/total/counts/tasks/warnings，不写任何文件 |
| collect_status._plan_records（内部函数） | path:str；当前主机的计划绝对路径 | dict；每个计划只读取一次 plan.json 和 jobs.csv，返回 plan:dict、rows:list[dict]、tasks:dict[str,dict]、groups:dict[str,dict]、jobs:dict[int,dict]；tasks/groups 以任务相对路径为键，jobs 以 batch 为键。读取及格式异常交由调用处报告，无写入 |
| collect_status._batch_records（内部函数） | path:str, batch:int | tuple[dict,dict[str,dict]]；以 (计划路径,批次编号) 缓存 progress 和 finished 索引。finished 的键为任务相对路径、值为原结束记录，仅一次遍历建索引；取出单条后才比较版本字段。不可读或格式错误时记录 warning 并缓存空记录，避免重复读取和报警。缺失 progress 沿用 read_progress 的空字典语义，无写入 |
| format_status | report:dict | str；打印根、各轮计划、并行批次及大小、job ID、去重计数和每个结构完整目录、步骤、群变化 FLAG、完整容差扫描、宽松恢复初始群信息、根因、补充原因及证据；不省略长任务列表 |

常量 ACTIVE_STATES/QUEUED_STATES/FAILED_STATES 为 set[str]，LABELS 为 dict[str,str]。Slurm父作业RUNNING只有progress当前成员计运行；已结束结果不随父作业失败改变，尚未执行成员在父作业中断后仍是批内等待并说明未执行。队列查询不可确认则显示待核实。OOM展示沿用progress已记录原因及Slurm明确OUT_OF_MEMORY；status不解析原始输出、不以137单独推断。

native状态类的构造器可能mkdir，因此完全不调用zstar的状态API，只读运行中当前结构的原生步骤JSON；其中历史path绝对地址不用作定位。每轮冻结参数用于定位历史阶段目录，新增active条目通过相对计划引用追踪原来正在跑的批次，复用已读来源计划、台账和批次进度。static虚频终止可能仅存在Slurm日志，已有progress短状态在声明版本匹配时保留，不打开Slurm日志或要求复制一份原生状态。

status 不调用 inspect_relax、inspect_zstar、revision_matches、build_relax_revision_index，也不读取当前 task_manifest、STRU/INPUT、running日志、abacus.json/time.json、末态结构或物性文件。已关联终态原样展示，即使输入或产物后来缺失也不临时改判为技术失败；worker结束判定和后续阶段准备仍负责原有检查。记录缓存仅存活于一次 collect_status，跨轮/跨批键彼此隔离，不创建缓存文件、不更改返回字段和状态名称。

扫描与显示会报告损坏计划/进度。显式选择错误计划直接失败；默认汇总对损坏计划给提示，不假装全部正常。队列回执不明必须核对唯一作业名，工作流不会把调度器暂时不可用解释成可以重提。

补充声明：`status._evidence_paths(value:object, task_dir:str, root:str, plan_path:str) -> list[str]` 将记录的 task/work/plan 三类相对证据展开为当前机器的完整屏幕地址；只处理字符串，不探测文件。新 API 文档中的运行锁为内核锁：锁文件保持稳定 inode，不应在正常运行时手工删除；进程死亡自动释放，无需删除残锁。POSIX 共享文件系统必须支持跨节点 flock，由集群文件系统提供此语义。

## v0.0.2 关联与兼容性

只允许 schema2/workflow_version0.0.2 的计划和进度用于完成判断及续算；finished 事件必须与该计划的全部 `REVISION_FIELDS` 相等，不能仅凭任务路径或主 UUID 取得终态资格。worker 拒绝旧协议计划及不匹配的已有 progress；旧协议仅能只读查看。旧计划队列处于活动或未知状态时仍保守保护所有无法用当前协议证明结束的成员。

声明版本不校验内容：人工直接修改 STRU/INPUT 且不更新版本无法被发现。修改参数/来源后沿用同一计算目录会冲突，应显式选择新阶段子目录；保持既有 work/任务/阶段布局，无自动版本目录、全局 registry 或额外 revision 文件。zstar 必须指向选定 relax 目录最新可信成功的 UUID，而不是只比较目录名称。

诊断扫描的五档 `1e-5/1e-4/1e-3/1e-2/1e-1` 必须遍历，额外配置只增加诊断档位。正式值固定 1e-3；宽松档恢复初始群不会阻止正式降对称计算。已准备任务的 symmetry 摘要沿后续计划保留，status 可直接查看。

命令版本按完整模板比较：ABACUS/PYATB/zstar 模板可能含科学参数，改变任一模板（包括 launcher 路径）要求新的阶段目录；不建立 argv 参数分类器。Slurm 分配的 ntasks/OMP 仍不进入版本。新建输入的 minimum_distance、max_symmetry_strain、angle_tolerance、扫描列表等小型策略值同样冻结。

zstar 的 scan/worker 将本轮 relax 成功版本索引置于内存 `config["_relax_revision_index"]`，同轮按任务和来源 UUID 查询，避免逐结构重复读取全部历史计划/进度。status 只展示自身阶段记录，不建立该跨阶段索引；索引不会跨顶层调用复用或写入 execution。

## 本地目录拆分兼容说明

本目录全部Python源码原样复制，输入来自新pre发布的LOCAL任务，不读取输入CSV或handoff。稳定目录、schema2/workflow0.0.2、UUID与四件套不变，scan/plan/worker/status/resume行为不改。四份默认工作根统一为新项目/work；relax/zstar CONFIG每个键的实际默认值、用途、单位和路径语义见[根API.md](../../API.md)。保留代码中的历史兼容分支不表示本项目仍有筛选入口。

