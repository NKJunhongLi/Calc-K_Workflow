# preprocess 本地目录前处理接口

处理链：单层发现来源 → CIF/ABACUS/自动读取 → 必要检查及自由移动/零磁矩政策 → 一次spglib原胞转换 → 常驻隔离ASE直接写出 → 完成记录和rename发布。无CSV/handoff、材料筛选、原子映射、内容摘要、输出群复验、写后结构回读或排序副产物检查。__init__.py无类和函数。

## validation.py

| 声明 | 参数类型与默认值 | 返回类型 | 行为、异常与副作用 |
|---|---|---|---|
| validate_config | config:dict, check_inputs:bool=True | dict | 返回浅副本；拒绝旧来源键，检查原胞枚举/命名空间/布尔/有限容差、本机路径、输入输出树隔离、可选非空INPUT和资源文本。check_inputs=False不要求源目录存在，仍检查非空源路径语法和用户INPUT。ConfigError/文件错误上抛，不创建目录 |
| validate_geometry | atoms:ase.Atoms（注解object）, minimum_distance:float=0.1 | None | 原实现：非空有限晶胞/坐标、非退化体积；设置pbc=True，Minkowski约化检查自身周期镜像，再用短截断neighbor_list查重叠，不建全对距离矩阵；错误IntegrityError |
| read_structure_safely | file_path:str | ase.Atoms（注解object） | 确认本机路径，顺序read(format=cif)、read(format=abacus)、read(format=None)；成功立即返回；三次失败汇总各异常类型和消息抛IntegrityError。设置do_not_split_by_at_sign=True，文件名中@不视为帧选择。只读文件 |
| read_and_validate_structure | path:str, config:dict | ase.Atoms（注解object） | 调用三段读取；重建前检查占位、合法元素与几何，再清除源约束，移除读者可能建立的(N,1)磁矩数组，重新建立(N,)全零标量磁矩。只使用源几何，后续writer不继承计算器/速度/位点附加字段；无CSV组成对照，物理失败不回退解析器 |

validate_config使用的全部用户键、默认值、单位和适用范围详见[根API.md的pre配置表](../../API.md)。structures_root必须为绝对普通目录；work_root与它必须互不包含；检查使用normcase(realpath)，Windows大小写变体同样拒绝。默认minimum_distance=0.1 Å只拒绝明显无效几何，不作稳定性证明。资源仅检查映射配对与Linux文本，不探测实际文件。

只承诺CIF、STRU、POSCAR，输入约定每文件一个初始结构；自动识别不是严格格式白名单。固定非磁性/无约束政策来自本工作流约定，不保留源磁态和Selective Dynamics。输入INPUT按既有nspin 1模板原样使用，不改科学参数。

## planner.py

| 声明 | 参数类型 | 返回类型 | 行为、异常与副作用 |
|---|---|---|---|
| discover_inputs | structures_root:str | tuple[list[InputRecord],list[dict]] | os.scandir单层枚举并按name排序，不过滤名称/扩展名，不递归；直接普通文件生成LOCAL记录。跳过链接、junction、目录和特殊文件，返回name/reason；不建目录，读取错误上抛 |
| task_id_for | record:InputRecord（注解object）, namespace:str | str | 来源小写前缀、namespace、source_record_id经URL安全编码拼接；下划线/点号转义防拼接与staging碰撞，无内容摘要。非法来源/空文本/编码超过180报ConfigError，无I/O |
| build_plans | records:list, config:dict | list[dict] | 正常返回task_id、record、task_directory和源source_file绝对路径；单文件编码超限或源路径拼接错误返回task_id为空的record/planning_error条目，不重复拼接坏路径；大小写等价目录身份仍整批拒绝，不创建目录 |

新入口只生成LOCAL。task_id_for保留MP/MJM身份校验能力，供未修改下游和既有夹具使用；没有对应输入适配器。source_record_id和source_file均为含扩展名直接文件名。不同后缀不同任务，化学式不决定身份，任务仍平铺work根。

skipped的reason为link或directory_or_special_file。普通log/csv仍是候选，三次无法读取时是任务尝试失败，不是扫描跳过。目录和特殊项不进入tasks_failed。

## primitive.py

PrimitiveResult为frozen dataclass，构造/字段atoms:ase.Atoms（注解object）、used_input_cell_fallback:bool、source_nsites:int，无自定义方法，不带原子映射。

| 声明 | 参数类型 | 返回类型 | 行为、异常与副作用 |
|---|---|---|---|
| make_primitive | atoms:ase.Atoms, config:dict | PrimitiveResult | 原实现仅调用一次选定find_primitive或standardize_cell(to_primitive=True)，采用原容差/角度/no_idealize；None按reject/use_input_cell处理。检查返回数组形状/有限值/合法整数元素、整倍数缩胞计量和几何；不重新识别群或匹配源位点；IntegrityError/科学库错误上抛，无文件I/O |

## templates.py

DEFAULT_INPUT_BYTES:bytes是INPUT-template内置原字节副本。load_input_bytes(config:dict)→tuple[bytes,str]返回INPUT字节与builtin/user标签。input_file留空采用内置；非空必须绝对本机地址，只读一次，坏路径或空内容不回退。

## writer.py

REQUIRED_PRODUCTS固定为STRU、INPUT、primitive.cif。进程、恢复与发布机制保留，只将完成记录的数据来源改为本地文件。

| 声明 | 参数类型与默认值 | 返回类型 | 行为、异常与副作用 |
|---|---|---|---|
| inspect_existing | task_directory:str, expected_input_description:dict或None, task_id:str | dict或None | 不存在返回None；检查当前schema/工作流版本/success、可重算任务身份、UUID、输入描述对象、可选设置一致性、三个普通非空必要产物；不读结构正文；冲突抛ConflictError并保留现场 |
| scan_staging | work_root:str | dict[str,list[str]] | 每轮一次根枚举，索引task_id.staging-32位UUID；只读 |
| recover_staging | task_directory:str, expected_input_description:dict, task_id:str, staging_index:dict或None=None | dict或None | 遍历外部传入索引，用inspect_existing判断完整当前现场，rename发布；残缺/旧现场保留，需外层工作锁，不自行扫描根 |
| _worker_loop | connection:multiprocessing.Connection（注解object） | None | 子进程一次导入ASE，通过Pipe接收数字/晶胞/坐标/资源文本/INPUT；每请求重建Atoms，切换staging写三文件、删除确切ase_sort.dat，再恢复CWD/临时环境并响应；普通写出失败不结束循环 |
| WriterSession | timeout:float=120.0 | WriterSession | 惰性常驻会话；无新写出不启动进程，记录process/connection/starts/worker_pid |
| WriterSession.__init__ | timeout:float=120.0 | None | 初始化字段，无进程副作用 |
| WriterSession.__enter__ | 无 | WriterSession | 返回自身 |
| WriterSession._start | 无 | None | spawn+Pipe，等待ready；启动错误/超时释放资源并上抛 |
| WriterSession._receive | 无 | tuple[str,object] | 有限poll/recv；超时或传输错误释放会话并抛IntegrityError |
| WriterSession.write | request:dict | None | 启动/复用worker，内存发送并等待；普通失败抛IntegrityError但保留会话；死亡清空会话供下一任务重启 |
| WriterSession.close | 无 | None | 请求结束、join，必要时terminate，关闭进程与管道，可重复调用 |
| WriterSession.__exit__ | exc_type:type或None, exc_value:BaseException或None, traceback:TracebackType或None | bool=False | 关闭会话，不吞异常 |
| write_task | plan:dict, conversion:PrimitiveResult, resource_mapping:dict, input_bytes:bytes, input_origin:str, input_description:dict, config:dict, software_versions:dict, writer:WriterSession或None=None | dict | 独占staging，通过会话写三文件，确认非空后生成UUID、原子写完成记录再rename；未传writer时使用独立作用域会话；失败留staging，不覆盖正式目录 |

writer中的Atoms仍只由元素、坐标、晶胞重建，因此不恢复源约束/磁矩/速度。自由移动和零磁矩按ASE合法输出语义验收，不强制逐行m/mag字面形式。无writer_request.json/result.json，不对临时科学文件逐项fsync，完成记录通过原子写同步。

## pipeline.py

CSV_FIELDS为16列：task_id、task_name、task_directory、input_source、source_record_id、formula、elements、source_file、input_revision_id、source_nsites、primitive_nsites、used_input_cell_fallback、stage、status、action、reason。

| 声明 | 参数类型与默认值 | 返回类型 | 行为、异常与副作用 |
|---|---|---|---|
| _row | record:InputRecord（注解object）, task_id:str='', manifest:dict或None=None；关键字status:str='failed', action:str='failed', stage:str='metadata', reason:str='' | dict | 固定16列运行行；成功manifest提供版本/原子数；读取失败化学信息可空，不伪造来源信息，无I/O |
| _aggregate_rows | work_root:str | list[dict] | 每轮开始读取当前协议正式manifest重建累计索引，跳过staging/旧协议，不读计算产物正文；坏记录IntegrityError，不改旧现场 |
| run_pre | config:dict, run_dir:str | dict | 全局检查后发现/规划文件；工作锁内建立一次staging索引、一次累计索引和一个WriterSession。逐文件将planning_error先转为planning阶段失败，再读取正常文件、从实际结构生成化学式/元素、检查资源、比较设置并复用/恢复/单次转换写出。单文件Exception登记并继续；全局错误上抛。输出本轮及工作根台账/失败/summary |

read_and_validate_structure成功后才用真实Atoms生成formula（empirical=True）和首现元素列表。新manifest不包含global_sample_id、dft_priority、nominal_k_available/value、energy_above_hull、material_id、csv_row_number、source_metadata空对象。

summary字段：status；tasks_succeeded/created/reused/recovered/failed；input_files；skipped_entries（name/reason列表）；tasks_planned；total_published_tasks；manifest/failure_manifest/run_manifest相对名；run_id；input_origin；software_versions；writer_process_starts；unpublished_staging。返回在这些顶层字段之外再带summary同内容字典。存在失败或零计划返回partial_failed，全部有效计划成功才success。

run_dir的preprocess_manifest.csv记录本轮全部尝试，preprocess_failed.csv记录本轮失败；work_root同名manifest为累计成功任务，failure为本轮失败。批末用本轮内存记录更新，不重新读全量输入。失败未发布的文件不进入下游正式任务或最终CSV。

## 完成记录、版本与下游契约

四件套保留：STRU/INPUT/primitive.cif/task_manifest.json。manifest含schema_version=2、workflow_version=0.0.2、status=success、task_id、UUID4 input_revision_id、input_description、LOCAL身份、formula/elements、source_file/source_file_base、input_origin、源/原胞原子数、fallback、software_versions、products和输入文件名。

input_description直接比较identity、source_file、INPUT字节base64、conversion设置与实际resource_mapping。不哈希源结构或资源，不自动发现同名源文件所有正文改动；新建分配UUID，复用/恢复不变。明确设置改变时拒绝占用目录，用户选择新工作根。

下游直接导入validate_geometry与inspect_existing，因此签名/异常/返回契约保留。post.metadata用get读取缺失的样本/nominal信息，LOCAL无编号要求；原post仍发布46列，四个旧来源列为空。primitive.cif是post完成检查必要产物，不能省略。

