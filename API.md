# 根目录入口、配置与应用编排接口

本项目只接收本地结构目录，执行 pre、独立 relax/zstar 计算和整批 post。日常步骤见 README.md；本文件完整说明入口与四份 CONFIG。计算/提交/后处理/资源源码保留原实现，内部协议仍为 schema_version=2、workflow_version=0.0.2。

## cli.py

| 声明 | 参数类型 | 返回类型 | 行为与副作用 |
|---|---|---|---|
| build_parser | 无 | argparse.ArgumentParser | 构建 pre/validate/run/post/help；不加载配置、不导入科学库、不建目录 |
| main | argv:Sequence[str]或None=None | int | 设置 dont_write_bytecode，解析命令并延迟派发；输出中文 JSON 或状态/后处理文本；未预期错误保留 traceback |

| 命令 | 位置参数 | 选项 | 调用目标 |
|---|---|---|---|
| pre | 无 | --config FILE | main.run_pre |
| validate | workflow=pre | --config FILE | main.validate_pre |
| run | stage=relax/zstar；action=run/scan/plan/submit/resume/status/worker，默认run | --config FILE、--plan FILE、--batch N | main.run_calculation |
| post | action=collect/validate/clean，默认collect | --config FILE | main.run_post |
| help | topic=pre/validate/run/post，可省略；run可再接relax/zstar | 无 | 对应解析器帮助 |

无 screening、原 pipeline、--from-screening 或 MP/MJM 来源选择。退出码0表示 success/valid/no_tasks 等成功入口结果，1表示运行/部分失败，2配置/命令错误，130中断。status正常完成查询返回0，不等于所有计算成功。ConfigError优先捕获；WorkflowError/OSError/ValueError/ImportError简明报告，未预期错误保留traceback。

## main.py

| 声明 | 参数类型与默认值 | 返回类型 | 行为、异常及副作用 |
|---|---|---|---|
| validate_pre | config_path:str或None=None | dict | 加载唯一pre配置，只读核对输入目录/参数/INPUT/资源文本；返回workflow=pre、status=valid、config、input_source=LOCAL；不逐文件转换或创建运行目录 |
| run_pre | config_path:str或None=None | dict | 全局验证后create_run；调用目录pre引擎，finish_run登记结果；失败/中断保留快照与已发布任务并重抛；不启动DFT |
| run_calculation | stage:str, action:str="run", config_path:str或None=None, plan_path:str或None=None, batch:int或None=None | dict | 原派发实现；worker只读冻结计划且不接受配置，submit必须明确plan；status只读状态；准备/提交可创建阶段目录、计划及调用Slurm |
| run_post | config_path:str或None=None, validate_only:bool=False, action:str="collect" | dict | 原派发实现；独立加载post，collect发布CSV后可清理，validate只读，clean不覆盖CSV；非法动作或clean+validate_only报ConfigError |

run_calculation中 --batch 仅worker可用，worker要求正整数batch与plan；--plan仅submit/status/worker可用。scan/status不准备输入；plan/run/resume准备阶段输入。post不加载当前zstar_config，不启动计算或查询队列。

## 配置加载与路径语义

四份根配置均声明CONFIG字典。--config文件只叠加列出的键，未知键报错，不改默认字典。pre由common.config_loader加载，relax/zstar由submission.config加载，post由postprocess.config加载。自定义Python配置可信且可执行；不应把不可信代码作为配置。

- structures_root是唯一必填的数据输入，必须原本就是当前主机绝对路径；不自动把相对路径解释成绝对路径。非空input_file同样要求绝对路径。
- work_root允许绝对路径或相对配置文件的路径；默认值按默认模板位置解释，自定义覆盖值按覆盖文件目录解释。四份默认work_root均为本项目/work；自定义时由用户保持同一批次。
- 命令行给出的--config/--plan位置按命令启动目录解释。默认Slurm脚本按默认模板目录定位，覆盖脚本按覆盖文件目录定位。
- dojo_root和resources/abacus.py内PP/轨道均是Linux绝对路径文本，始终用POSIX语法，不探测实际文件、不改成Windows反斜杠。
- 任务产物/运行记录使用受限相对引用；UUID与文件存在性不证明正文未变。新项目不复用旧工作流计算现场。

## pre_config.py：全部CONFIG键

structures_root以外的键都有默认值，通常只需填写结构父目录即可开始前处理。输入只单层扫描、不按名称过滤，固定按CIF→ABACUS→自动识别读取；没有递归、格式插件或筛选开关。

| 键 | 类型 | 默认值 | 完整说明与限制 |
|---|---|---|---|
| structures_root | str或None | None，必须填写 | 当前主机可读的结构父目录绝对地址。只处理直接普通文件，不递归；子目录/链接不输入。支持范围CIF、STRU、POSCAR，混入其他普通文件仍尝试读取并可能失败；不是CSV或handoff目录 |
| work_root | str | project_root()/work | 独立批次输出根，任务平铺其中，另含runs、pre台账和.hpc_runs。允许相对配置目录；不能借此复用旧现场。与后续三份配置保持一致 |
| dataset_namespace | str | local | 非空稳定命名空间，与LOCAL及完整源文件名共同决定task_id。同一批子集重跑保持不变；更换它会形成不同任务身份，不是显示标签 |
| input_file | str或None | None | 为空使用templates.py内置INPUT原字节；非空为用户绝对本机文件，必须存在且非空，坏路径不回退。当前非磁性模板nspin 1原样沿用，不重写科学参数 |
| reuse_existing | bool | True | 检查当前协议success记录、UUID、明确输入设置和三份非空普通产物后复用；False遇到已有任务报冲突，不覆盖旧目录。不会检测源文件正文的所有人工改动 |
| primitive_method | str | find_primitive | 选择一次spglib.find_primitive；也可standardize_cell(to_primitive=True)。不自动尝试另一套算法，无原子映射、输出重复对称性识别或写后回读 |
| symprec | int或float | 0.001 | spglib位置容差，单位Å，有限正数。用于pre所选转换；不是后续zstar诊断扫描配置 |
| angle_tolerance | int或float | -1.0 | spglib角度容差，-1委托库自动判断，也可有限正角度；0和其他负值非法 |
| standardize_no_idealize | bool | True | 仅primitive_method=standardize_cell时使用。True保留非理想化晶格，False允许所选标准化的理想化；不会改变find_primitive行为 |
| primitive_failure_policy | str | reject | 仅spglib返回None时决定reject或use_input_cell。后者显式沿用输入胞并记录fallback；坏数组、组成或几何不会被此开关放行 |
| minimum_distance | int或float | 0.1 | 单位Å，有限正数。短截断邻居及自身周期镜像重叠阈值；用于输入与转换输出，不是键长筛选或材料稳定性证明 |
| writer_timeout | int或float | 120.0 | 秒，常驻隔离writer初始化/单次响应的有限等待上限。超时/进程死亡当前任务失败并保留staging；不限制后续DFT运行时长 |
| dojo_root | str或None | /public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-FR | 官方资源主目录，必须为合法Linux绝对路径文本。仅被资源表官方条目使用；私人条目仍在abacus.py中。若整表改成绝对资源路径可省略/置空，不检查远端安装 |

删除旧input_source、mp_input_mode、screening_handoff、structure_variant、state_root、metadata_csv、cif_suffix及旧资源覆盖键。LOCAL来源由程序固定生成，不由用户填。validate pre不会逐个读取结构，不能替代一次实际pre。

## relax_config.py / zstar_config.py：共同CONFIG键

下列共同键在两份配置中分别设置。默认值是本次复制保留的实际模板，不是抽象函数的回退值。计算配置加载器、科学设置冻结及原生状态判据详见submission/API.md和calculations/API.md。

| 键 | 类型 | 默认值 | 完整说明与限制 |
|---|---|---|---|
| work_root | str | project_root()/work | 当前主机正式pre任务根，必须与pre/post一致。允许相对配置文件目录；整目录迁移后使用新位置，不依赖旧机器绝对任务引用 |
| slurm_script | str | 项目/slurm/run_cell-relax.slurm或run_zstar.slurm | Slurm资源与环境脚本路径；默认相对默认模板，自定义相对覆盖文件。保留既有私有环境设置，脚本不分配结构身份 |
| parallel_jobs | int | 40 | 提交作业数量，不是每批结构数。0每结构一作业，1全串行，其他非负整数精确均分非空组；超过有效待算数量报错，不静默缩减 |
| python_command | str或list[str] | ["python3"] | Slurm内部worker的Python argv前缀；可指定已有环境解释器。不支持模板占位符，不通过shell执行 |
| sbatch_command | str或list[str] | ["sbatch"] | 提交命令argv前缀；使用可解析回执并防重复提交，不支持科学模板占位符 |
| squeue_command | str或list[str] | ["squeue"] | 实时队列查询argv前缀；查询失败为UNKNOWN，不把不可查询当终态 |
| sacct_command | str或list[str] | ["sacct"] | 记账查询argv前缀，补充队列已离开的主作业状态；不读取batch/extern作为结构结果 |
| command_timeout | int或float | 30.0 | 调度器提交/查询超时秒数，有限正数；不限制ABACUS、PYATB或zstar计算时长 |
| relax_subdir | str | cell-relax | 每个任务内弛豫单层目录；不得隐藏、绝对、含分隔符或与zstar_subdir等价。已有目录不覆盖 |
| zstar_subdir | str | zstar | 每个任务内zstar单层目录；限制同上。修改阶段目录可隔离不同科学设置，不自动建多层revision树 |
| relax_max_steps | int或None | None | 弛豫完成检查的允许步数，正整数或空。空时从实际relax INPUT解析relax_nmax；达到上限但末步收敛仍成功，只用于完成判据 |
| abacus_command | str或list[str] | 既有OpenMPI绝对路径 + -n {ntasks} abacus | ABACUS launcher模板。模板完整参与阶段版本比较；{ntasks}取Slurm分配，资源/软件由用户环境提供，非shell脚本 |
| minimum_distance | int或float | 0.1 | Å，有限正数。阶段准备和完成读取的基本重叠检查；不代表物理稳定性或DFT收敛 |

relax另有：

| 键 | 类型 | 默认值 | 完整说明与限制 |
|---|---|---|---|
| relax_input_file | str或None | None | 为空复制每个pre任务自己的INPUT；非空指定统一弛豫INPUT，相对配置目录定位。只在首次准备时采用，不覆盖既有阶段输入；模板路径属于冻结科学设置 |

## zstar_config.py：专属CONFIG键

| 键 | 类型 | 默认值 | 完整说明与限制 |
|---|---|---|---|
| symprec | int或float | 0.001 | 正式结构编辑位置容差，Å，固定为1e-3；不能用宽松扫描档替换。默认四命令使用此值，自定义命令原样展开不额外补参 |
| angle_tolerance | int或float | -1.0 | 固定-1，保持正式识别/编辑的自动角度容差，不开放任意角度 |
| symmetry_scan_tolerances | list[float] | [1e-5,1e-4,1e-3,1e-2,1e-1] | relaxed群诊断容差，所有项有限正数；固定五档始终保留，可追加，不能通过此列表改变正式群或编辑容差 |
| max_symmetry_strain | int或float | 0.01 | 无量纲有限正数。比较相同标准基底下理想化与非理想化原胞晶格的形变奇异值，超出硬拒绝；无原子位移映射QC |
| pyatb_command | str或list[str] | /public/home/ustc_ljh/.conda/envs/deepmd/bin/mpirun -n {ntasks} pyatb | PYATB launcher模板，作为正确引用的完整字符串传给native；整条模板参与输入版本比较 |
| zstar_command | str或list[str] | ["zstar"] | native可执行前缀；zstar_commands=None时构造默认四命令，前缀改变也参与科学设置比较 |
| xc | str | pbesol | 非空泛函名称，为默认pre命令或自定义{xc}占位符提供值；实际自定义命令若不引用则不会自动补入 |
| kspacing | int或float | 0.1 | 有限正数，为默认pre或自定义{kspacing}占位符提供k点间隔；自定义命令不引用时不额外补参 |
| zstar_commands | dict或None | 现有pre/run/post/static四条argv覆盖 | None使用内置四命令；dict必须恰好包含pre/run/post/static，每值命令字符串或argv序列。按固定顺序逐条运行，非零不中断后续；保留既有外部INPUT-scf引用 |

本模板当前zstar_commands实际使用：pre为zstar bec pre --stru STRU --input既有外部INPUT-scf --force；run为zstar bec run --root .及两个launcher；post为zstar bec post --root .；static为zstar dielectric static。外部INPUT已确认nspin 1，不在本次拆分中改写。xc/kspacing等值只有被命令引用时才实际传递，不能把配置值当成native已收到的参数。

允许的命令占位符为{ntasks}/{omp_threads}/{symprec}/{xc}/{kspacing}/{abacus_command}/{pyatb_command}。Slurm核数/线程数运行时展开；完整命令模板及QC设置冻结，单独动态资源数不作为科学设置。字符串按POSIX shlex拆词，推荐argv列表保留空格路径；不隐式执行管道、重定向或shell表达式。

## post_config.py：全部CONFIG键

| 键 | 类型 | 默认值 | 完整说明与限制 |
|---|---|---|---|
| work_root | str | project_root()/work | 当前主机独立批次根，与pre/计算相同；整根一张结果表，不按Slurm分组另出表 |
| output_csv | str或None | None | 空时work_root/post/zstar_results.csv；非空为当前主机.csv位置，相对值按声明它的配置文件目录解释。已有目标拒绝覆盖，也不能写入任务或.hpc_runs目录 |
| zstar_subdir | str | zstar | 无计划任务定位的默认安全单层目录；有历史计划时尊重冻结布局，不能用当前配置改绑旧结果 |
| band_relative_root | str | 0.no-move/pyatb-band/Out | 安全POSIX相对子树，定位唯一同目录band.dat/band_info.dat非空文件对；不能绝对、反斜杠或路径穿越；多对报歧义 |
| cleanup_completed | bool | True | collect成功发布CSV后自动清理符合资格的CSR/restart；False仅禁自动清理，显式post clean仍可执行。validate始终只读预览、不删除 |

post返回status为success/valid/incomplete/no_tasks，自动清理失败可为partial_failed且保留已写CSV；clean可为success/partial_failed/blocked/no_tasks。counts区分成功、物理终止、技术失败、输入失败和后处理失败。全部正式任务有可信终态才发布；pre未发布文件不占最终行。

LOCAL manifest省略global_sample_id/dft_priority及nominal字段，原post将缺失信息变为空值。最终CSV仍46列，包括四个空列；不做导出后删列，以免破坏独立clean对表头和完整结果的比较。第44列仍为zstar_input_revision_id。

## 模块索引与兼容边界

- hpc_workflow/API.md：分层及调用关系。
- hpc_workflow/common/API.md：配置、路径、原子文件和错误。
- hpc_workflow/inputs/API.md：LOCAL内存记录与ELEMENTS。
- hpc_workflow/preprocess/API.md：目录发现、读取、一次转换、发布与恢复。
- hpc_workflow/records/API.md：UUID和运行快照，无handoff。
- hpc_workflow/calculations/API.md：原单结构算法。
- hpc_workflow/submission/API.md：原Slurm、worker、status与续算。
- hpc_workflow/postprocess/API.md：原46列提取及清理。
- hpc_workflow/resources/API.md：原资源映射文本。
- slurm/API.md：保留环境脚本；tests/API.md：实际测试声明及验证边界。

schema2/workflow0.0.2是未修改下游的兼容前提，不是新旧工作根可混用的声明。任务四件套、稳定task_id、UUID与明确input_description继续保留；无内容摘要或原子映射。保留模块源码中MP/MJM分支仅是兼容实现，本项目没有对应输入入口。
