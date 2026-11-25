# calculations：计算适配与结构质量检查

本目录处理单个 pre 任务根目录下的 `cell-relax` 和 `zstar`，不负责队列资源分配。原任务根目录的 STRU、INPUT、task_manifest.json 均不修改；zstar 源码不导入、不改动。命令由 submission.worker 提供的回调执行，适配层不直接启动进程、不捕获或复制 Slurm 日志。

## 通用约定

- `task_dir: str`：当前主机可读取的 pre 任务根绝对路径；通过 common.paths 拼接。
- `config: dict`：独立计算配置。目录名使用 `relax_subdir` / `zstar_subdir`，默认 `cell-relax` / `zstar`。不能写绝对目录、`..` 或路径分隔符。
- 结果为 `dict`，基本字段 `status: str`、`step: str`、`reason: str`、`evidence: list[str]`。状态仅为 `pending`、`success`、`input_failed`、`technical_failed`、`physical_rejected`。证据通常是相对任务根的原生文件位置；回调提供的 Slurm 证据沿用其相对 work 根约定，由上层统一显示。
- 准备成功返回 `pending`，并带 `input_revision_id: str` 及来源版本、科学设置原值；只表示输入就绪，不表示已经提交。
- `on_command(label: str, args: list[str], cwd: str) -> int \| dict`：同步执行当前命令。字典必须含 `returncode: int`，可带 `output_tail: str`、`reason: str`、`status: str`、`evidence: list[str]`。其中 `status` 表示已经识别的明确原因，普通错误尾部不能冒充物理拒绝。
- 硬错误不猜测、不修补，保留目录供追溯。赝势与轨道只有路径文本被保留，不访问、复制或检查其真实文件。

## relax.py

| 函数 | 参数与类型 | 返回类型及功能 |
| --- | --- | --- |
| `calculation_directory` | `task_dir: str, config: dict, stage: str`（relax/zstar） | `str`：受控计算子目录；不创建。拒绝目录穿越、外部绝对目录及符号链接。 |
| `read_task_revision_id` | `task_dir: str` | `str`：只读 pre manifest；要求 schema_version=2、workflow_version=0.0.2、status=success、合法 UUID4，失败抛 IntegrityError/读取异常。 |
| `scientific_input` | `config: dict, stage: str` | `dict`：relax 保存 abacus_command、minimum_distance、relax_input_file、relax_max_steps；zstar 另保存 pyatb_command、zstar_command、四命令模板、XC、kspacing、正式容差、relax_subdir、angle_tolerance、max_symmetry_strain 和扫描列表原值。不解析自定义 argv 的科学/资源语义。 |
| `build_relax_revision_index` | `work_root: str, relax_subdir: str = "cell-relax"` | `dict`：一次读取当前协议 relax 历史，返回 `work_root`（规范绝对根）、`relax_subdir`、`entries[task_relative][source_pre_uuid] = success_relax_uuid \| None`。调用内临时索引，不写文件。 |
| `selected_relax_revision_id` | `task_dir: str, config: dict` | `str \| None`：按任务相对路径、选定 relax 目录、当前 pre 版本读取最新可信 relax 成功 UUID；仅当前协议的 plan/progress 可证明终态。 |
| `revision_matches` | `task_dir: str, config: dict, stage: str, entry: dict \| None` | `bool`：比较声明版本、来源 pre UUID、阶段目录与科学设置；zstar 还比较选定 relax UUID。读取错误可向上抛出。 |
| `new_stage_revision` | `task_dir: str, config: dict, stage: str` | `dict`：产生阶段 UUID 并冻结来源；zstar 无关联 relax 成功版本则拒绝。不写 sidecar。 |
| `validate_stage_inputs` | `directory: str` | `None`：只用 lstat 检查 STRU/INPUT 为非空普通非 reparse 文件；缺失/不合格抛 IntegrityError，不读取正文。 |
| `command_context` | `config: dict` | `dict`：`ntasks`、`omp_threads`、固定 `symprec=0.001`、`xc`、`kspacing`。优先采用 Slurm 环境；不自行分配资源。 |
| `command_argv` | `template: str \| list[str] \| tuple[str, ...], context: dict` | `list[str]`：先拆词再展开占位符，字符串采用 POSIX shlex 规则。回调按 argv 执行，禁止外层 shell 再解释。 |
| `classify_failure` | `text: str, *, include_scf: bool = True` | `dict \| None`：识别明确带隙/虚频拒绝、zstar 对称性异常、OOM、SCF 不收敛及缺完成标记；未知则 None。默认行为保持 zstar/worker 原调用兼容；relax 两个调用传 False，跳过无离子步边界的 SCF 不收敛匹配，其他硬错误仍识别。137 不单独判 OOM。 |
| `_tail` | `path: str, maximum: int = 131072` | `str`：UTF-8 宽容解码的有界文件尾部；不可读取返回空串。仅用于引用原日志，不产生日志副本。 |
| `_read_last_relax_step` | `path: str, block_size: int = 262144` | `tuple[str, int \| None]`：只读最后一个完整离子步标题至打开时 EOF 的 UTF-8 宽容解码文本及步号；找不到标题返回 `("", None)`。每次读取不超过 block_size，从后向前按完整行定位，两种标题共用位置顺序，跨块标题补齐后才判断。命中后再按块读取该末步；不累积无标题日志的全文。block_size 必须为正整数且不能为 bool，否则 ValueError；读取错误/检测到读取期间截断抛 OSError。块大小不是返回文本的内存上限，完整末步和补齐中的单行仍可能很长；不新建或修改文件。 |
| `inspect_relax` | `task_dir: str, config: dict, revision_entry: dict \| None = None` | `dict`：只读状态。要求明确最后离子步边界、正常结束、该步无最终 SCF/离子失败，以及唯一 OUT 末态来源、可解析非空 abacus.json/time.json、可读有限末态。完整末步正常结束时不强制出现正面收敛短句。无阶段目录为 pending；已有目录缺日志、无完整步标题、不可读取或无正常结束标记为带具体 reason 的 technical_failed。成功额外返回 `relaxed_structure: str`（相对任务根）。可选版本条目不匹配返回 input_failed。 |
| `prepare_relax` | `task_dir: str, config: dict` | `dict`：首次复制根 STRU 及 `relax_input_file: str \| None` 指定 INPUT（默认根 INPUT）；已有目录绝不覆盖，也不重读科学结构；缺损目录明确输入失败。 |
| `run_relax` | `task_dir: str, config: dict, on_command: callable` | `dict`：执行一个 ABACUS 命令，核对声明输入版本和完成判据，并合并本轮明确错误。忽略跨离子步输出尾部/回调元数据中的旧 SCF 归因；完整末步确认的 SCF 不收敛不被后续输出或回调覆盖。非零退出仍为失败，不因旧完成产物而成功。`commands: list[dict]` 仅为内存返回，不要求额外落盘。 |

弛豫读取限定当前 ABACUS LTS 3.10.1 使用的两种完整行标题：`STEP OF RELAXATION : 数字`、`STEP OF ION RELAXATION : 数字`。标题大小写和水平空白不敏感；两种标题视为同一集合，取文件位置最靠后的一个。`RELAX CELL` 不再作为本检查的离子步边界。生产调用使用 262144 字节读取块，从 EOF 向前找标题，不能把块首截断片段当作新行。先定位再读取末步，避免每加一块就复制和重扫全部累计文本；没有标题时必须读到文件开头才能确认。`_tail` 的 128 KiB 行为保留，zstar 不随此变更改变读取范围。

仅在最后离子步内部检查四个短字段，先统一大小写并把空白/标点归一为词间空格：SCF 失败 `convergence has not been achieved`、SCF 成功 `convergence is achieved`、离子/晶格失败 `relaxation is not converged`、成功 `relaxation is converged`。最终步内出现一次 SCF 失败就明确返回 `technical_failed / SCF 不收敛`，后续 SCF 成功、再次 INIT SCF、整体 Relaxation 成功、回调成功或其他输出均不清除这个根因；前面离子步的同类提示不参与判定。其余 OOM 等硬错误仍保留。该规则不等于只检查最后一次 SCF 过程。

离子/晶格事件按出现顺序更新两个失败状态：`Ion relaxation` 只更新离子状态，`Lattice relaxation` 只更新晶格状态；无组件前缀的整体 `Relaxation`（兼容 `Structural relaxation`）同时覆盖此前两者。因而较后的 Ion 成功不能清除 Lattice 失败，较后的整体成功可以覆盖此前组件失败，整体成功后新出现的晶格失败仍会拒绝。整体失败会同时置两个失败状态，单个组件后来的成功只清除自身状态。完整末步正常结束且没有这些负面事件时，允许缺少 SCF 或离子正面短字段；此规则绝不用于缺失离子步边界或未正常结束的日志。

正常结束继续要求最后一步内出现 `Total Time`/`Total Times` 或 `Finish Time`/`Finished Time`，不把单独的 `Time Statistics` 当结束证明。已有阶段目录缺少日志的 reason 为 `弛豫日志不存在: running_cell-relax.log`；没有完整标题为 `弛豫日志缺少完整的离子步标题`；读取异常前缀为 `弛豫日志无法可靠读取:`；缺结束标记为 `ABACUS 日志缺少正常结束标记`。这些均为 technical_failed；仅尚未创建阶段目录仍为 pending。检查不写回 plan/progress，也不自动更正过去的失败台账。

保留原 Bash 的两份结束 JSON 和末态来源门槛：计算目录根部的 `abacus.json`、`time.json` 均非空且可解析，唯一 `OUT.*/STRU_ION_D` 能读取并通过几何检查，才返回成功。`relax_max_steps: int \| None` 未提供时从计算 INPUT 的 `relax_nmax` 读取；已在最后允许步完成仍可成功，只有最终离子/晶格失败且达到上限才报告 `relax 达到步数上限仍未收敛`，未达上限的明确失败报告 `relax 最后离子步未收敛`。末步 SCF 失败优先于这两个离子原因。UUID、科学设置冻结、阶段目录及输入复用协议均保持原样。

## symmetry.py

`SYMPREC: float = 1e-3` 是正式结构编辑和 QC 使用的唯一位置容差（Å）。扫描值只用于说明识别稳定性，不改变目标群或实际编辑容差。

| 函数 | 参数与类型 | 返回类型及功能 |
| --- | --- | --- |
| `read_structure` | `path: str` | `ase.Atoms`：固定一份 UTF-8/BOM STRU 文本快照后解析；不调用 ASE 写出器。 |
| `validate_geometry` | `atoms: ase.Atoms, minimum_distance: float = 0.1` | `None`：直接复用 preprocess.validation.validate_geometry 的短程邻居几何检查，无全原子对矩阵。异常 `IntegrityError`。 |
| `symmetry_group` | `atoms: ase.Atoms, symprec: float = 0.001` | `int`：空间群编号。spglib 无法识别时抛 IntegrityError。 |
| `sanitize_structure` | `initial: ase.Atoms, relaxed: ase.Atoms, config: dict` | `tuple[ase.Atoms, dict]`：按 relaxed 在 1e-3 下识别的群执行 `standardize_cell(to_primitive=True, no_idealize=False)`。初始群仅作比较，真实降对称返回 FLAG 并继续。摘要含初始群、正式群、声明的输出目标群、正式容差、FLAG、QC、完整诊断扫描；不建立原子映射。 |
| `_normalized_site_fields` | `fields: list[str], default_magnetism: float` | `list[str]`：读取单个位点坐标后的字段，返回规范化的 `m` 三元标志及必要的标量 `mag` 覆盖值。默认磁矩由调用者提供有限值；支持带/不带 `m` 的移动标志及 `mag/magmom` 别名，省略移动字段沿用自由移动默认值。保留111和000，混合方向、速度、角度、三分量磁矩明确拒绝；不检查坐标或结构。字段不支持/数量不正确抛 IntegrityError，非法标量文本可能抛 ValueError。 |
| `_site_metadata` | `text: str` | `dict[str, tuple[str, list[list[str]]]]`：保持元素插入顺序，读取并规范化有限块默认磁矩与逐位点附加列。返回默认磁矩字符串及规范化行列表，供同元素一致性判断；不复验晶格、坐标或 spglib 转换。非有限磁矩或不支持字段抛 IntegrityError。 |
| `sanitized_stru_text` | `output: ase.Atoms, resource_text: str, relaxed_text: str, summary: dict` | `str`：资源头逐字保留，按原资源元素顺序写 Direct 坐标；按元素传递规范化后统一的移动标志及标量磁矩。显式000不改为111；同元素位点属性仍不一致则拒绝，不用首行覆盖不同属性。直接返回文本，不回读。 |

诊断必遍历 `1e-5, 1e-4, 1e-3, 1e-2, 1e-1`；配置额外容差加入扫描，不能减少上述五档。正式识别/标准化始终使用 `1e-3`，宽松恢复初始群也继续使用正式群并记录 FLAG。摘要 `flags` 可包含 `symmetry_changed`、`loose_tolerance_restores_initial_group`、`initial_group_unavailable`、`diagnostic_scan_failed`；单档诊断失败保留在 `diagnostic_errors`，正式群失败则拒绝。

保留独立晶格 QC：第二次 `standardize_cell(no_idealize=True)` 仅提供同一标准基底的晶格，与正式理想化晶格比较形变奇异值；`max_symmetry_strain` 默认 0.01。它不执行原胞复验。`output_group` 为正式目标群声明，`output_group_basis="declared_formal_target"`，不代表独立输出复验。标准化数组仅检查形状、有限值、整数元素与计量倍率。稠密原子映射、位移 QC、输出重复识别群、重复求原胞及写后回读已经删除；`mapping_tolerance_factor`、`max_symmetry_displacement` 配置也删除。硬几何错误、正式识别/标准化失败或过大应变属于输入处理失败。

当前支持 ABACUS Direct/Cartesian 输入以及标量磁矩、所有方向同开关的移动标志。坐标后的 `m 1 1 1` 与裸 `1 1 1` 等价，`m 0 0 0` 与裸 `0 0 0` 同样保留；方向混合的101等组合沿用拒绝规则，不强制转换成111，移动标志仍只接受字面0/1。`mag/magmom` 按标量读取，字段边界由关键字识别，不把后续字段误计为磁矩分量。磁矩别名、等值磁矩数值写法、显式块默认磁矩与省略写法规范化后再比较，避免等价表示造成误拒；不建立原子映射。

新坐标行统一写出 `m` 前缀，元素块保留规范化默认磁矩，只有与块默认值不同的逐位点标量覆盖才额外写出 `mag`。同元素属性不同仍抛 `site_dependent_metadata_requires_mapping`。`v/vel/velocity`（包括零速度）、`angle1/angle2` 仍抛 `unsupported_vector_site_metadata`；三分量 `mag/magmom`（包括全零）仍抛 `unsupported_vector_magnetism`，不会截取第一分量。移动字段缺项、重复或非0/1抛 `invalid_atomic_movement_flags`，重复/不完整标量字段抛 `invalid_scalar_magnetism`，非有限磁矩抛 `nonfinite_scalar_magnetism`，未知附加字段抛 `unsupported_site_metadata:<字段>`。这些限制只用于读取字段与无损传递属性，不重复检查第三方的几何输出正确性。新输出不依赖 ASE writer，也不产生 `ase_sort.dat` 副作用。

## zstar.py

| 函数 | 参数与类型 | 返回类型及功能 |
| --- | --- | --- |
| `_strict_json` | `path: str` | `object`：纯读取 JSON，拒绝 NaN/Infinity。 |
| `_strict_json.reject_constant` | `value: str` | `None`：json 解码回调，始终抛 ValueError，报告非标准常量。 |
| `_nonempty` | `path: str` | `bool`：普通文件存在、非空且不是符号链接。 |
| `_finite_tensor_file` | `path: str, born: bool = False` | `None`：读取 BEC/BORN 数值载荷并检查有限性与基本张量分量数。BEC 支持普通及 legacy 星标索引行、No. Atom 表头；BORN 支持注释头、可选单位因子、9列或3列张量行，至少包含介电与一张 BEC。非法载荷抛 ValueError。 |
| `native_status` | `task_dir: str, config: dict, since: float \| None = None, failed_only: bool = False` | `dict`：直接读原生 .zstar/stages JSON，展示最新阶段及 SCF/BAND/PYATB 等步骤。只使用 name 与当前根拼接；不依赖历史绝对 path，不实例化会写目录的 native store。可限制本次命令后的失败记录；当前失败阶段日志用于精确归因。 |
| `inspect_zstar` | `task_dir: str, config: dict, revision_entry: dict \| None = None` | `dict`：纯只读轻量完成检查。没有当前协议版本关联时，历史产物不能直接视为本轮完成；已有无关联目录要求使用新的计算子目录。 |
| `prepare_zstar` | `task_dir: str, config: dict` | `dict`：新任务验证 relaxed 完成后理想化，生成独立 zstar/STRU 和 INPUT；返回 `symmetry: dict` 摘要。已有 zstar 任务核对版本后仅检查阶段输入为非空普通文件，绝不重新理想化或覆盖 STRU，`symmetry` 返回 None；planner 继续保留原计划诊断。 |
| `zstar_command_set` | `config: dict` | `list[tuple[str, list[str]]]`：固定顺序 pre/run/post/static。默认 pre 带 --force，pre/post 为 --symmprec 0.001，run 的 --omp-threads 跟随 Slurm。可以用 zstar_commands 四键覆盖完整 argv，不限定 native 模式，也不会补入容差参数；native 默认容差为 1e-3。 |
| `run_zstar` | `task_dir: str, config: dict, on_command: callable` | `dict`：四命令逐个执行，非零不截断。最早明确根因作为任务失败原因，下游缺输入只作为 supplementary；成功返回码不能覆盖前面错误。 |

完成判据为：`0.no-move/pyatb-band/Out` 下面同目录中的 band.dat 与 band_info.dat 非空；原生 `0.no-move/zstar_insulation.json` 的 insulating 为 true；根 BEC.dat、BEC.rep.dat、BORN 非空且数值张量载荷有限；dielectric_response/static_response.json 可严格解析且 tensor 为有限实数 3×3。明确的 insulating=false 或明确明显虚频属于 physical_rejected，zstar 对称性重建异常属于 technical_failed。qpoints.yaml / irreps.yaml 不作为外层必需产物。

run 会立即读取本次失败 run 更新的 native 状态及原日志，优先呈现 SCF 根因，避免 post 缺 BEC、static 缺 BORN 分裂成三个不相干错误。pre 的已存在判断仍完全交给 zstar；外层只把 native 明确的 `Existing ensemble files` 提示作为已存在续算提示，仍执行后面三条命令。其他未知 pre 非零不能被后续成功掩盖。

## 测试与异常边界

tests/test_calculations.py 使用真实 spglib/ASE 小晶胞和模拟命令，覆盖原胞保持、常规胞缩原胞、真实降对称、独立晶格 QC、硬几何错误、正负收敛标志、末步收敛、唯一来源、续算不覆盖、最小产物、输入版本与四命令根因。不会调用真实 ABACUS、zstar、MPI 或 Slurm。第三方依赖错误、不可读结构及正式结构处理错误由 prepare 汇总为 input_failed，保留原目录和短原因。

## v0.0.2 版本关联边界

`REVISION_FIELDS` 为 `input_revision_id/source_input_revision_id/calculation_subdir/scientific_input/source_relax_revision_id`（最后一项仅 zstar）。阶段 UUID 只记录在现有 plan.tasks 与 progress.finished；没有全局登记文件或额外 revision 文件。首次准备分配新 UUID，续算必须由 planner 在私有 `config["_revision_entry"]` 传入原条目，run 也要求该条目。

版本 UUID 是声明身份，不证明输入字节未被人工修改。程序不重新读取全文生成摘要，也不使用文件大小或时间戳冒充内容一致性保证。科学参数或来源版本改变后，同一阶段目录报冲突；用户改用新的 `relax_subdir` / `zstar_subdir`，不会自动创建版本目录树。旧协议产物只可历史查看，不自动转换成当前成功。缺失、坏记录与不明状态保留现场。

命令模板允许任意 wrapper 和科学参数，因此整条 `abacus_command/pyatb_command/zstar_command/zstar_commands` 改变均要求新阶段目录，即使只换 launcher 路径。`ntasks/omp_threads` 等 Slurm 动态资源不作为科学设置；模板占位符在运行时正常展开。几何/QC 门槛和诊断扫描列表也冻结为小型值，修改后不会用旧成功跳过新政策。

大批次使用本轮 `config["_relax_revision_index"]`，格式同 `build_relax_revision_index`，使用前核对规范根和选定 relax 子目录。scan/worker 每次顶层调用或批次构建一次；post 可从已读历史构造同形索引。直接调用没有有效索引时才回退读取，不存在跨轮全局缓存。status 不建立该索引、不调用 inspect_relax/inspect_zstar/revision_matches，只展示 plan/progress 关联终态；本模块的计算结束检查与阶段准备检查保持原样。

## 本地目录拆分兼容说明

本目录全部Python源码原样复制。新pre按固定政策生成无约束、零初始磁矩的LOCAL结构，后续relax判据、zstar结构标准化/元数据处理和四命令顺序均不修改。INPUT为用户已确认的nspin 1模板，外部INPUT-scf及自定义命令保持。relax/zstar CONFIG每个用户参数的实际默认值、用途与限制见[根API.md](../../API.md)。

