# postprocess：一个工作根的一份最终结果表

本目录读取新版前处理/计算记录和物性产物，一个独立 `work_root` 是一个数据批次，多个Slurm组和续算轮次不成为新的数据批次。不实现旧散装结果导入，不调用Slurm查询或提交，不执行zstar。结果表为一张46列CSV；发布后默认清理明确完成任务的指定中间文件，不改变输入/物性/终态证据。复用已有内核锁机制，不写额外结果表或清理日志。

正常调用：根 `cli.py post` → `main.run_post` → 本目录 `config.load_config` → `pipeline.run_post` → `_collect_batch` → `discovery.discover_tasks` → `readers.read_task_data` → `writer.write_results_csv` → `cleanup.run_cleanup`。`post validate`同样核验并预览清理，但不发布CSV、不建立输出目录或锁。`post clean`经pipeline.run_clean独立清理已有CSV对应批次，不覆盖CSV。

## 结果和状态契约

保留旧版39列及顺序；追加 `input_source,dataset_namespace,source_record_id,zstar_relative_path,zstar_input_revision_id,provenance_status,remarks`，总计46列。没有数据用空字符串，不使用0、None、NaN或空BEC数组冒充。本项目LOCAL的样本号、priority与两项nominal列均为空；MP/MJM分支仅为原样保留的兼容实现。nominal未知/明确False/True语义不变。

所有有限 `dielectric_total_trace_mean` 按数值降序，零与负值仍有效，没有该值的行置底；同值按来源/命名空间/源ID/任务名确定排序。priority原值不重编。成功任务remarks为空，物理终止/技术失败/输入失败保留主因，已成功计算但产物解析有误则为后处理失败。失败可保留有效部分，不强制置底；只看总介电迹平均值是否存在。

`provenance_status` 为 revision_linked/revision_mismatch/invalid_record/unavailable，仅指版本关联，不能当作计算成功标志。只有revision_linked且元数据可信时读取物性。无明确整结构终态的任务阻止整批发布，不能把缺文件或队列暂不可见猜成失败。明确上游relax失败也作为该结构的终态行。

## config.py

| 声明 | 参数类型 | 返回类型 | 功能、副作用与异常 |
|---|---|---|---|
| `output_path` | config:dict | str | 返回明确output_csv或当前work_root/post/zstar_results.csv，不建目录 |
| `validate_config` | config:dict, check_inputs:bool=True | None | 检查四个必要路径键及可选cleanup_completed布尔键、路径语法、受限阶段名称和.csv扩展名；默认只读检查工作根。旧四键配置默认开启自动清理；拒绝1/0/字符串等伪布尔。未知/缺失键与无效位置抛ConfigError；不检查资源文件 |
| `load_config` | custom_path:str/None=None | dict | 固定post_config.CONFIG深复制后叠加可信Python CONFIG，拒绝未知键；默认值按默认模板、自定义值按覆盖文件目录定位，附_config_path；不建立目录 |

五个用户配置键：work_root:str为当前机器批次绝对根；output_csv:str/None为空时使用固定批次输出，否则相对配置文件或明确本机绝对位置；zstar_subdir:str默认zstar，实际有计划时用冻结布局；band_relative_root:str默认0.no-move/pyatb-band/Out，只允许安全POSIX相对引用；cleanup_completed:bool默认True，控制collect后的自动清理，不禁止显式clean，validate始终只读。

## writer.py

| 声明 | 参数类型 | 返回类型 | 功能、副作用与异常 |
|---|---|---|---|
| `build_fieldnames` | 无 | tuple[str,...] | 精确构造46个唯一列；内部列定义错误抛RuntimeError |
| `build_csv_row` | task:dict, values:dict, remarks:str="" | dict | 合并可信元数据和独立物性子集；拒绝解析器覆盖身份/nominal；补版本与备注；MP样本号强制空；不写文件 |
| `_sort_key` | row:dict | tuple | 总迹平均数值降序、空值置底、身份稳定次序；错误类型/非有限值抛IntegrityError |
| `_safe_output_parent` | path:str | None | 发布前拒绝目标/祖先链接和Windows目录联接，再建立真实输出父目录；仅由实际写出调用 |
| `write_results_csv` | output_path:str, rows:list[dict] | int | 先校验全表列与有限值、排序；同目录独占临时文件写UTF-8 BOM标准CSV并fsync，原子且不覆盖发布，返回行数；重复目标抛ConflictError，I/O失败不冒充成功 |

常量 COMPONENTS:tuple[str,...]为九分量行优先顺序；EXTRA_FIELDS:tuple[str,...]为末七列；METADATA_FIELDS:tuple[str,...]为旧39列中不允许物性解析器覆盖的七个身份/nominal列。

Windows发布使用os.rename，Windows目标存在即失败；POSIX使用同目录os.link独占发布，不能使用会覆盖目标的POSIX rename/replace。不支持所需文件系统操作时明确失败；只清理本次UUID临时文件，既有最终CSV始终保留。输出不放在任务或.hpc_runs目录内。

## pipeline.py

| 声明 | 参数类型 | 返回类型 | 功能、副作用与异常 |
|---|---|---|---|
| `_has_link` | path:str | bool | 检查路径祖先的符号链接/Windows目录联接，不打开目标内容 |
| `_control_paths` | root:str | list[str] | 逐层无链接地枚举直接任务manifest、relax/zstar计划、jobs和progress；不扫描DFT大输出 |
| `_snapshot` | paths:list[str] | dict | 只取得dev/ino/size/mtime_ns，不打开内容。缺失、不可读分别记录，不读取链接；检测正在变化抛IntegrityError |
| `_task_paths` | task:dict, config:dict | list[str] | 列举关联记录、封存产物、实际输入及必要物性文件；有限差分和资源文件不在范围内 |
| `_check_output_scope` | destination:str, root:str, tasks:list[dict] | None | 拒绝把最终CSV放进任一任务目录或.hpc_runs；不创建目录 |
| `_collect_batch` | config:dict | tuple[dict,list[dict],list[dict]] | 只读执行一次整批发现、当前协议终态关联及每结构一次物性解析；发现前后及采集末尾比较控制文件范围/stat，物性文件同样前后比较stat，不重复发现或重读整批记录。返回report/tasks/rows；valid有完整行，incomplete/no_tasks行空。有效tasks附本轮内存_observed_files，供清理复用，不写缓存/目录/锁 |
| `run_post` | config:dict, validate_only:bool=False | dict | 自动清理模式首次采集前获取controller锁，采集一次、独占发布CSV、复用本轮tasks/rows清理。validate全程不建锁不写；关闭自动清理仅采集发布。已有CSV抛ConflictError；清理失败保留CSV并返回partial_failed |
| `run_clean` | config:dict | dict | 包装独立run_cleanup，action=clean；不覆盖CSV；显式动作忽略自动清理开关，结果沿用共同屏幕汇总字段，增加cleanup摘要 |
| `format_summary` | report:dict | str | 中文根目录/统计/失败根因和证据汇总；不创建任何日志/派生CSV |

run_post返回status为success/valid/incomplete/no_tasks，自动清理有问题时为partial_failed。共同字段work_root/output_csv/rows_written/total/counts/issues/output_exists；开启自动清理/预览时另有cleanup:dict。counts以单任务主分类统计success、physical_rejected、technical_failed、input_failed、post_failed或unfinished。issues含directory、reason，可带supplementary和evidence。导出包含失败行时status=success表示提取及应做的清理完成，不等于全部数值计算成功；详情看counts与CSV备注。

OUTCOME_LABELS:dict[str,str]用于物理终止/技术失败/输入失败的中文备注前缀。原执行根因优先；带隙拒绝后缺static等后果不堆满CSV，但终端保留补充信息。控制记录或输入在发现/采集期间变化时整批不发布，不把变更后的文件偷偷当作旧验证的新基线。

## cleanup.py：终态任务的指定中间文件清理

清理范围是正式结构任务目录及其递归子目录。SPARSE_NAMES:frozenset[str]固定三个精确CSR名称，另用fnmatchcase匹配`*DENSITY.restart`；PHYSICAL_REASONS:frozenset[str]仅接受“能带绝缘检查不通过”和“Gamma 点存在明显虚频”及经原分类器归一到这两类的原因。其它csr/restart、目录、链接、junction/reparse及所有输入/最终物性/运行证据保留。

| 声明 | 参数类型 | 返回类型 | 功能、副作用与异常 |
|---|---|---|---|
| `_signature` | info:os.stat_result | tuple | 提取dev/ino/size/mtime_ns/文件类型，统一使用lstat避免Windows DirEntry缓存inode不同；不读候选正文 |
| `_is_reparse` | path:str | bool | lstat识别symlink及Windows reparse属性，兼容Python3.10无isjunction；不存在返回False，其它I/O错误保留 |
| `_checked_path` | root:str,path:str,task_dir:str/None=None | str | 核验绝对字面/实际包含关系及全部祖先无链接，返回当前主机绝对路径；不创建目录，越界/链接抛IntegrityError |
| `_canonical_rows` | rows:list[dict] | dict[str,tuple[str,...]] | 标准csv.writer将全部46列转换为字符串，以唯一task_name索引；忽略行序，不忽略任何数据字段；缺列/重复名抛IntegrityError |
| `_read_published_csv` | path:str | dict[str,tuple[str,...]] | 严格UTF-8 BOM兼容读取已有CSV，核对表头顺序/行宽/唯一性及读前后身份；拒绝链接与变更，不写文件 |
| `_guarded_snapshot` | paths:list[str] | dict | 输入、结果、控制记录快照前后逐层核验reparse，不接受变更后的链接作为新基线；所有文件只取普通stat |
| `_guard_unchanged` | paths:list[str],baseline:dict | bool | 逐文件删除前只核祖先reparse和lstat的dev/ino/size/mtime，不反复读CSV/BEC正文；与进入/枚举后/结束时的普通stat检查配合，变化返回False |
| `_eligibility` | task:dict,row:dict | tuple[bool,str] | terminal+revision_linked+可信metadata为前提；成功还须CSV备注空；物理拒绝仅两类；技术/输入/post解析失败保留；无I/O |
| `_task_history_records` | root:str,task:dict | dict[str,dict] | 优先返回本轮_history_records，空字典也不触发回读；只兼容未携带此字段的旧辅助调用时，从record_paths读取有关计划、台账及所属组进度并建同形内存索引，不写缓存。路径/记录错误传播；正常collect、自动清理和独立clean均不走回退 |
| `_history_info` | root:str,task:dict | tuple[list[str],list[str]] | 从本轮成员历史索引取得该任务全部冻结阶段目录及记录，不重新读取JSON/CSV；未finished已提交/回执不明或current_task占用抛ConflictError；不查队列，保留路径核验 |
| `_verify_terminal_record` | root:str,task:dict | None | 从本轮成员历史索引复核真实执行计划和唯一finished终态、根因、冻结参数及五个版本关联字段；不重读计划/进度，不逐任务重扫整批finished；缺失或不符抛IntegrityError。唯一性由首次read_progress核验 |
| `_verify_task` | root:str,task:dict,row:dict,config:dict | None | worker锁内先核对_observed_files普通stat，再使用共享历史复核当前协议终态，复核metadata和revision_matches；不再次解析物性。无本轮观察值或发现变化抛IntegrityError；row/config沿用调用接口，不作为重新采集入口 |
| `_enumerate_candidates` | root:str,task_dir:str | tuple[list[tuple],list[dict]] | 无链接递归枚举目标普通文件，候选为(path,signature)，不读取矩阵正文；不可访问位置附issue，其它文件继续 |
| `_unlink_candidate` | root:str,task_dir:str,path:str,signature:tuple | bool | 再核边界和文件身份，仅unlink单个普通文件；POSIX锚定父目录fd，Windows使用复核的绝对地址；缺失返回False，删除返回True，错误向调用方传播 |
| `_new_summary` | config:dict,preview:bool | dict | 创建精简内存统计，不写清理manifest/日志/CSV |
| `_process_task` | config:dict,task:dict,row:dict,summary:dict,preview:bool,published_snapshot:dict/None=None | None | 更新共享summary；真实模式取得所有历史阶段worker锁，复核原CSV版本/输入/结果后逐文件清理；单文件失败继续，关键数据变更停止本结构 |
| `_controller_locks` | config:dict,inactive:bool=False | contextmanager[None] | 正常按relax/zstar顺序获取现有RuntimeLock，退出释放；inactive不创建文件，供预览或上层已持锁使用；锁/路径错误传播 |
| `run_cleanup` | config:dict,preview:bool=False,expected_rows:list[dict]/None=None,*,collected:tuple/None=None,controllers_locked:bool=False | dict | collected为本轮(report,tasks,rows)；实际复用须controllers_locked=True。独立调用先锁后只采集一次，自动调用复用数据。整张已发布CSV全部46列语义匹配后逐任务清理；expected_rows可额外比较。预览无CSV要求且不建锁。全局错误blocked、任务/文件错误partial_failed、空批no_tasks |

summary固定包含status:preview/success/partial_failed/blocked/no_tasks，work_root/output_csv:str；eligible_tasks/skipped_tasks/candidate_files/candidate_bytes/deleted_files/deleted_bytes/missing_files:int；issues:list[dict]含directory/path/reason；tasks:list[dict]含directory/eligible/reason及候选、删除、缺失计数与字节。大小为逻辑长度估算，不能保证等于真实释放磁盘量。任务只计一次；部分删除后的文件错误不会重新计为另一个保留任务。

真实清理按relax/zstar固定顺序取得现有.controller.lock，然后按稳定目录顺序取得所有相关历史冻结布局的.hpc-worker.lock。当前版本成功/物理拒绝之外的任务不申请清理资格；CSV和关键记录的初始核验版本持续关联到删除阶段。已有CSV不覆盖，重复clean缺失文件幂等。可预期全局/逐任务/逐文件错误进入精简内存汇总，配置loader错误仍由CLI按配置错误处理；不捕获编程错误伪装成功。


## models.py

### 常量与类型别名

- `Matrix3 = numpy.typing.NDArray[numpy.float64]`：3×3 张量；由本模块关联解析函数返回时设为只读。
- `CsvValue = str \| int \| float`：CSV 单个非缺失单元格的值类型。
- `COMPONENTS: tuple[str, ...]`：固定为 `xx,xy,xz,yx,yy,yz,zx,zy,zz`，对应矩阵 C 顺序展平。

### `DataValidationError(ValueError)`

预期的产物格式、物理约定或数值错误。构造参数沿用 `ValueError(*args)`；输出是异常对象。调用方可以逐结构隔离这种异常，不应把任意编程异常全部伪装成这一类型。

### `BornChargeRecord`

冻结 dataclass，初始化参数及字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `atom_index` | `int` | 文件中唯一正整数原子编号，允许不连续，绝不重新编号 |
| `species` | `str` | 原文件中的元素符号 |
| `z_displacement_polarization` | `Matrix3` | 行为位移、列为极化，单位 e |

构造返回 `BornChargeRecord`。dataclass 不替代解析器的格式校验；只读矩阵由解析器建立。没有手写成员方法。

### `DielectricValues`

冻结 dataclass；构造返回 `DielectricValues`。字段 `total`、`electronic`、`ionic` 均为 `Matrix3`；`ionic_trace_mean`、`total_trace_mean` 为 `float`。`ionic` 定义为 `total-electronic`，两个均值定义为相应张量迹除以三。没有手写成员方法。

## readers.py：数值、矩阵与文本

| 声明 | 参数类型 | 返回类型 | 功能及失败条件 |
|---|---|---|---|
| `parse_finite_float(text, context)` | `text: str`, `context: str` | `float` | 十进制/E/e/D/d 指数的有限数值；拒绝 bool、下划线、NaN、Inf 和溢出 |
| `_signature(info)` | `info: os.stat_result` | `tuple[int,int,int,int]` | 提取 dev、ino、长度、mtime_ns；Windows path stat 和 fstat 对 ctime 语义可能不同，因此不跨 API 比较 ctime |
| `_product_path(zstar_dir, relative)` | `zstar_dir: str`, `relative: str` | `str` | 将受限 POSIX 相对引用连接到当前计算根；拒绝链接/junction 祖先目录和实际地址越界 |
| `_check_nonempty(path)` | `path: str` | `None` | 验证非空普通文件及读取稳定性，仅读取一个字节；用于大型 band.dat，避免整文件载入内存 |
| `_read_text(path)` | `path: str` | `str` | 读取 UTF-8 BOM 兼容的非空普通文件；拒绝最终符号链接、纯空白文本和读取期间身份/长度/mtime 变化 |
| `iter_data_lines(path)` | `path: str` | `Iterator[tuple[int,str]]` | 从稳定文本逐行剥离空行和 # 注释，保留原始一基行号 |
| `matrix_from_tokens(tokens, context)` | `tokens: Sequence[str]`, `context: str` | `Matrix3` | 恰好九个有限数值按行转为只读 3×3 矩阵 |
| `validate_matrix3(value, context)` | `value: object`, `context: str` | `Matrix3` | 严格要求 3×3 数值分量；拒绝布尔、数字字符串、非有限值和 float64 转换溢出 |

文件不存在、权限及编码问题保留 `OSError`/`UnicodeError`；可说明的格式和一致性问题抛 `DataValidationError`。纯解析函数允许读取调用方明确提供的现有文件；任务编排使用 `_product_path` 将结果限制在当前 zstar 文件树内。

## readers.py：具体物性

### `parse_band_gap(path: str) -> float`

只读取标签 `Band gap (eV):` 的唯一值。缺失、重复、字段格式错误均失败。不另设绝缘阈值，不根据数值自行把任务判为物理拒绝。

### `parse_electronic_tensor(path: str) -> Matrix3`

读取 BORN 的首个电子介电张量。支持注释头后九分量行、可选单数值单位换算因子后九分量行、以及三行三分量矩阵。单位因子不乘电子介电张量。首张量无效时直接失败，不能跳过它而把后面的原子电荷当成电子介电。

### `_reject_constant(value: str) -> None`

`json.loads(parse_constant=...)` 回调；遇到非标准 JSON 常量 NaN/Infinity 直接抛 `DataValidationError`，正常解析不调用。

### `_unique_object(pairs: list[tuple[str, object]]) -> dict`

JSON 对象构建回调，保留对象键值并拒绝重复键，避免 tensor、单位等字段被静默覆盖。

### `parse_total_tensor(path: str) -> Matrix3`

严格读取顶层 `tensor`，同时要求：

- `frequency_cm-1` 为数值零，不能是布尔或字符串；
- `response_kind == "total relative dielectric tensor"`；
- `response_unit == "1"`；
- `response_convention == "relative permittivity"`；
- `electronic_response_included is True`；
- 如包含 `dimensionality`，必须是整数 3。

当前原生输出没有 dimensionality 字段，三维含义由精确的 response_kind 确定。不接受只有 tensor、缺少物理元数据的旧散装结果，不提供静默兼容开关。不对总张量再加一次电子项。

### `parse_born_charges(path: str) -> tuple[BornChargeRecord, ...]`

读取全部 BEC 记录，支持 shared 的注释头以及 legacy 的 `No. Atom ...` 表头、`*1` 和 `*   1` 星标格式。每条记录包含原子编号、真实元素和恰好九个有限分量。拒绝重复/零编号、Xx 等不存在的元素、不完整行和任意未知非注释行。原始顺序及不连续编号保留。对于 `BEC.rep.dat` 返回全部不等价原子；对于 `BEC.dat` 用于完整性检查。

### `parse_insulation(path: str) -> bool`

严格读取原生 `zstar_insulation.json`，要求对象包含 JSON 布尔类型的 `insulating`，不能从字符串或 0/1 猜测。若原生提供 `gap_eV`、`threshold_eV`，只检查其为有限数值，不重新判断阈值。返回 True 或 False；False 是正常的物理拒绝结论，不抛格式异常。缺失文件、错误 JSON、重复键、非有限常量或字段类型错误会失败。

### `trace_mean(matrix: Matrix3, context: str) -> float`

校验张量后取三个对角分量各除以三，再用 `math.fsum` 求和，避免对有限大数先加后除导致不必要的溢出。保留符号；无法给出有限结果时报 `DataValidationError`。

### `derive_dielectric_values(total: Matrix3, electronic: Matrix3) -> DielectricValues`

再次校验两个输入，计算 `ionic = total-electronic` 和两个迹平均，返回只读矩阵及标量。不改变坐标框架或张量方向；相减真正溢出时失败。

### `serialize_born_charges(records: tuple[BornChargeRecord, ...]) -> str`

输出适用于单个 CSV 单元格的紧凑 JSON 数组，每个对象包含 `atom_index`、`species`、`z_displacement_polarization`。只在此序列化边界将矩阵转换成嵌套列表，不重排、不转置，禁止 NaN 输出。

## readers.py：任务级部分提取

### `_band_info_path(zstar_dir: str, config: dict) -> tuple[str, str]`

在配置 `band_relative_root`（默认 `0.no-move/pyatb-band/Out`）内查找同目录的 `band_info.dat` 与 `band.dat`。禁止链接目录；存在多套非空文件对则报歧义，不任意选择默认目录。只有唯一 info、但缺 dat 时允许返回该地址，供终止任务保留有效带隙，同时由调用方记录完整性错误。返回 `(info_path, data_path)`。

### `_matrix_columns(kind: str, matrix: Matrix3) -> dict[str, float]`

按固定 COMPONENTS 将矩阵转换为 `dielectric_{kind}_{component}` 九个字段。内部调用 kind 取 total、electronic、ionic。

### `read_task_data(zstar_dir: str, config: dict) -> dict`

任务级纯读取入口。`config` 只使用 `band_relative_root: str`，其他配置由编排层负责。返回对象固定包含：

| 键 | 类型 | 含义 |
|---|---|---|
| `values` | `dict[str, str\|int\|float]` | 已确认有效的旧版科学字段子集；缺失字段完全不提供，不用零或 [] 冒充 |
| `errors` | `list[str]` | 按字段来源组织的短错误及文件地址；编排层按实际根因决定备注 |
| `evidence` | `list[str]` | 本轮定位的原始结果文件地址；不复制内容 |
| `insulation` | `bool \| None` | 原生绝缘检查结论；缺失或无效时为 None 并附错误，False 本身不是格式错误 |

完整数据的 values 有 32 个字段：band_gap_ev、27 个张量分量、ionic/total 两个迹平均、BEC 数量和 BEC JSON。错误隔离分别作用于带隙、band.dat 完整性、原生绝缘结论、电子张量、总张量、离子派生量、BEC.dat、BEC.rep.dat。绝缘结论不占 CSV 的科学数据列，编排层结合终态决定是否为成功行。

总迹平均只依赖总张量，BORN 缺失不会阻止排序值输出；离子量必须同时有 total 和 electronic。缺少 BEC.dat 会报告完整性错误，但不丢弃已有效解析的 BEC.rep.dat。单字段错误不判断整个任务的成功、物理终止或技术失败，终态和当前输入版本由 discovery/pipeline 负责。预期文件/格式错误被收集；编程错误不被宽泛捕获。

## metadata.py

| 声明 | 参数与类型 | 返回类型 | 功能与异常 |
| --- | --- | --- | --- |
| `_optional_integer(value, label, positive=False)` | `value: object`，封存编号；`label: str`，错误字段名；`positive: bool`，是否禁止零 | `int \| str` | 缺省返回空字符串；有值仅接受 ASCII 十进制整数。布尔、负数、浮点、指数等抛 `IntegrityError`。 |
| `_nominal_values(source_metadata)` | `source_metadata: dict`，前处理可选source_metadata对象；LOCAL入口不写此键 | `tuple[str, float \| str]` | 返回可用性 `""/"False"/"True"` 和有限数值/空字符串。未知不伪装为 False；False 配非空数值、True 配缺失/非有限数值均拒绝。 |
| `resolve_metadata(task_dir)` | `task_dir: str`，本机语法的正式任务绝对路径 | `dict` | 调用已有只读 `inspect_existing`，验证当前schema2/workflow_version=0.0.2完成记录、UUID、稳定身份及STRU/INPUT/primitive.cif三个普通非空文件；传expected_input_description=None，不比较外部模板。检查目录名与 task_id 一致。返回 `dft_priority/global_sample_id/formula/elements/task_name/nominal_k_available/nominal_k_value/input_source/dataset_namespace/source_record_id`。`elements` 使用旧 CSV 的 `-` 分隔形式；MP 的 global_sample_id 始终为空，MJM 必须有样本号和正 priority。只通过 inspect_existing 读取一次完成记录，绝不访问资源文件。预期文件与记录错误由调用方逐任务归并。 |

## discovery.py

| 声明 | 参数与类型 | 返回类型 | 功能与异常 |
| --- | --- | --- | --- |
| `_stage_inputs_present(directory)` | `directory: str`，阶段目录 | `bool` | 仅lstat检查STRU/INPUT为普通非空、非reparse文件；缺失/类型无效返回False，其它I/O错误由调用者归并。不会读取正文或执行科学回读。 |
| `_checked_record(root, path)` | `root: str`，工作根绝对路径；`path: str`，拟读取记录/目录的绝对路径 | `str` | 校验位置仍在 root 内，拒绝祖先符号链接和 Windows junction，返回原路径。也适用于尚不存在的 progress/jobs 文件，仅检查路径链；不创建文件。 |
| `_plan_order(plan, path)` | `plan: dict`，已加载计划；`path: str`，计划绝对路径 | `tuple[float, int]` | 使用带时区 created_at 的 Unix 时间和计划文件纳秒 mtime 排序，同秒 UUID 字典序不决定运行先后。无 created_at 时仅使用 mtime；非法时间抛 `IntegrityError`。 |
| `_read_history(root, stage)` | `root: str`；`stage: str`，`relax` 或 `zstar` | `tuple[dict, list[str]]` | 每份plan、jobs、各组progress只读取一次；按计划相对路径建立索引，记录含原plan、任务/finished/成员组/job索引及按batch保存的原progress，并返回所有读取过或检查过的控制文件位置。核验完成项只属于自己批次、只对应本计划五个版本关联字段、只包含允许终态；错误记录阻止整批最终导出。 |
| `_latest_event(relative, history, root)` | `relative: str`，任务直接子目录名；`history: dict`，同阶段历史索引；`root: str` | `dict \| None` | 返回该任务最新一轮的合并状态、冻结 execution、证据位置；active 观察记录沿 plan 指针回到真实执行计划。新计划未结束不能复用旧成功；明确提交失败为技术终态。未知、待提交、待核验继续为未结束。循环、缺失指针和完全相同的无法区分时间均拒绝。 |
| `_base_metadata(relative)` | `relative: str`，当前正式任务目录名 | `dict` | 对损坏元数据但已知终态的任务构造可定位空行；不猜测来源 ID、样本号、priority 或 formula。 |
| `_relax_revision_indexes(root, history)` | `root: str`；`history: dict`，本轮已读relax历史 | `dict[str,dict]` | 按真实计划顺序合并当前协议计划及finished，按relax子目录、任务相对路径和pre UUID索引成功阶段UUID或None；纯内存，无全批重读。索引仅在本轮调用有效，不跨轮缓存。 |
| `discover_tasks(config)` | `config: dict`，至少含 `work_root: str`，可含默认 `zstar_subdir: str` | `list[dict]` | 枚举整个批次，每任务一条记录。新版元数据错误作为任务错误保留；同命名空间重复稳定身份、MJM 样本号/priority、计划任务缺失属于全局错误。优先使用 zstar 最新记录；没有 zstar 记录时，保留上游 relax 技术/输入失败。终态仍须按冻结科学设置、pre来源版本和选定relax版本核对当前记录关联，避免用当前 zstar_config 错误否定旧合法结果。不会查询 Slurm、prepare、执行计算或改动计算输入。 |

常量 `REVISION_FIELDS: tuple[str,...]` 为 input_revision_id、source_input_revision_id、source_relax_revision_id、calculation_subdir、scientific_input。前两者分别为阶段UUID和pre UUID；zstar还须绑定成功relax阶段UUID。旧schema记录可读但最新事件保持未完成，不能自动取得当前终态、跳过或清理资格。

`discover_tasks` 返回的每项记录包含：

没有zstar轮次而保留上游relax终态时，核对pre来源UUID、阶段目录和冻结科学设置；不符改记输入失败。relax关联不代表zstar输入已绑定，provenance仍为unavailable，不读取残留zstar物性。准备失败没有阶段UUID时允许保留当前协议失败行。上游输入仍纳入普通stat观察。

| 键 | 类型 | 含义 |
| --- | --- | --- |
| `task_name`、`task_dir` | `str` | 稳定目录名及当前绝对位置。 |
| `metadata` | `dict` | 元数据字段，并附 `zstar_relative_path`。 |
| `metadata_errors` | `list[str]` | 前处理记录或元数据核验失败原因。已知终态任务保留失败行；无终态仍须等待。 |
| `terminal` | `bool` | 是否有可信的整结构结束边界。 |
| `outcome` | `str` | `success/physical_rejected/technical_failed/input_failed`，或未结束状态。 |
| `reason`、`evidence` | `str`、`list[str]` | 主因和证据；证据含当前 plan/progress 绝对位置及原始短证据引用。 |
| `zstar_dir`、`zstar_relative_path` | `str` | 冻结布局下的当前计算目录，以及相对 work_root 的可迁移位置。 |
| `expected_input_revision_id` | `str` | 该 zstar 阶段的显式UUID，未准备输入或只有 relax 失败时为空。 |
| `execution` | `dict` | 对应轮次的冻结执行/科学参数，不取当前模板。 |
| `provenance_status` | `str` | `revision_linked` 记录关联成立、可读取物性；`revision_mismatch` 声明版本或科学设置不符；`invalid_record` 前处理记录不符合当前契约；`unavailable` 无 zstar 输入关联。关联不证明内容未被人工修改，也不代表计算成功。 |
| `record_paths` | `list[str]` | 控制记录、前处理产物和实际输入的位置，供总流水线采集前后核对，不包含赝势/轨道文件。 |
| `_history_records` | `dict[str,dict]` | 以计划绝对路径索引本任务参与的历史；值是本轮_read_history的共享记录对象，不复制整批计划，不重读JSON/CSV，不落盘。清理复用其plan、jobs、progress、finished和成员索引；首次读取后仍用观察stat与锁约束清理。 |

`revision_entry: dict` 保存五个关联字段；`_collect_batch` 完成后为每项任务附 `_observed_files: dict[str, tuple]`，仅在本次调用内共享，不落盘。

UUID声明关联，不验证文件内容。普通stat只能提示常见变化；同大小同mtime的外部替换可能无法发现，锁也只约束合作工作流。已有CSV不自动迁移、重标或覆盖；v0.0.2的第44列按新契约命名；旧schema的已提交历史任务没有当前协议完成证据时继续阻止清理。物性合法性、CSV全列语义比对、物理终止分类和文件删除边界均保留。

本轮每任务内存对象另携带 `_relax_revision_index`，供版本关联及后续清理复用；它来自已读历史，不落盘、不进入 scientific_input，不改变冻结配置。清理前仍检查记录stat、当前pre UUID和锁，索引不构成跨轮缓存。

## LOCAL输入兼容说明

本目录全部Python源码原样复制，任务来自新pre的四件套。LOCAL manifest不写global_sample_id、dft_priority、nominal字段或空source_metadata；resolve_metadata使用get默认值，只有MJM才强制编号，因此LOCAL合法。原46列仍保留这四个空表头，不在导出后删列，避免破坏独立clean的表头/整行比对。pre失败未发布的文件不属于正式任务，不占最终结果行。CONFIG五个键的完整默认值、作用、路径规则与限制见[根API.md](../../API.md)。

