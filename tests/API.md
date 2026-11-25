# 测试目录接口与行为清单

本文对应本地结构拆分项目的实际测试源码。测试使用unittest，主入口为 `python -B -m unittest discover -s tests -v`。临时结构、工作根、桩脚本和缓存限制在本项目.test-artifacts/.tmp/.cache内；执行回归时TMP/TEMP/TMPDIR/MPLCONFIGDIR也应指向项目内。科学程序/Slurm使用替身，结构格式和原胞检查使用真实ASE/spglib。

原calculations/submission/postprocess/resources测试核心判据继续保留。MP/MJM名字仅出现在未修改下游的历史兼容夹具中，不代表本项目提供这些来源入口。新LOCAL测试从真实目录开始验证四件套、缺省元数据及原post兼容。没有筛选、筛选历史或handoff专属测试。

以下按实际AST声明列出类、函数、方法和嵌套helper。标准继承方法及桩脚本文本中的定义不计；测试省略self、正常返回None，断言失败为AssertionError，预期异常由assertRaises捕获。表中版本是UUID与显式参数关联，不证明文件正文未变；字节对照仅用于验证未修改/未覆盖。

## test_calculations.py

ABACUS 末步日志判据、对称化原胞与质量上限、zstar 四命令和失败根因；命令执行由测试回调替代。日志回归从 EOF 以 262144 字节块定位两种 STEP 标题中最后一项，覆盖跨块完整行、超过一块的末步、SCF 失败保留、弛豫结论先后次序、缺标题/缺正常结束、500 步边界及有界 I/O；共享 _tail 仍为 128 KiB。结构回归另外覆盖真实 ABACUS 末态的 m 移动标志、标量属性规范化与无映射转换的拒绝边界。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `CalculationTests`（类） | 由 unittest 框架构造；继承标准构造器 | CalculationTests | 全部临时输入和产物限定在新版项目测试目录，真实任务不参与回归。 |
| `CalculationTests.setUp` | 无（省略实例参数） | None | 建立单原子简单立方原胞与独立任务根。 |
| `CalculationTests.make_relaxed` | log: str \| None = None | str | 准备含 v3.10.1 离子步标题、短收敛字段和结束标记的产物，返回阶段目录。 |
| `CalculationTests.make_products` | 无（省略实例参数） | str | 建立用户要求的最小完成产物，不添加 qpoints/irreps。 |
| `CalculationTests.test_relax_prepare_no_overwrite` | 无（省略实例参数） | None | 已有弛豫 STRU 不因根 STRU 改变而覆盖。 |
| `CalculationTests.test_relax_completion_requires_positive_convergence` | 无（省略实例参数） | None | 第 500 步存在明确未收敛结论时失败，不能只凭正常结束误判成功。 |
| `CalculationTests.test_relax_at_limit_but_converged_is_success` | 无（省略实例参数） | None | 最后允许步收敛仍正常成功，保留旧检查意图并修正其粗判。 |
| `CalculationTests.test_ion_convergence_does_not_replace_lattice_convergence` | 无（省略实例参数） | None | 真实 ABACUS 离子已收敛但最后晶格未收敛，不能标记 cell-relax 成功。 |
| `CalculationTests.test_final_lattice_convergence_is_success` | 无（省略实例参数） | None | 中间步晶格未收敛、最后晶格收敛时应成功。 |
| `CalculationTests.test_missing_short_fields_do_not_invent_failure` | 无（省略实例参数） | None | 末步与正常结束完整、无负面结论时，缺少某类短字段不额外制造失败。 |
| `CalculationTests.test_relax_step_labels_supported_across_versions` | 无（省略实例参数） | None | 两种明确支持的 STEP 标题都能定位第 500 步并保留未收敛判断。 |
| `CalculationTests.test_latest_header_wins_for_both_label_orders` | 无（省略实例参数） | None | 两种标题合并按文件先后选最新，不能固定优先一种标题或取最大步号。 |
| `CalculationTests.test_previous_step_scf_failure_does_not_poison_final_step` | 无（省略实例参数） | None | 前一离子步 SCF 失败不影响后续末步成功，日志内旧完成标记也不改变选段。 |
| `CalculationTests.test_final_step_scf_failure_is_sticky_after_success` | 无（省略实例参数） | None | 同一末步中 SCF 失败不可被其后成功覆盖，成功后再失败同样明确失败。 |
| `CalculationTests.test_relax_events_follow_text_order_even_on_one_line` | 无（省略实例参数） | None | 整体弛豫结论取最后一次出现，同行先负后正可恢复，反向则失败。 |
| `CalculationTests.test_whole_relaxation_can_resolve_prior_lattice_failure` | 无（省略实例参数） | None | 明确整体 Relaxation 最终成功可证明完成，单独 Ion 成功仍不能覆盖晶格失败。 |
| `CalculationTests.test_missing_header_or_normal_end_is_explicit_failure` | 无（省略实例参数） | None | 无受支持末步标题或末步无正常结束标记时，即使其他产物齐全也失败。 |
| `CalculationTests.test_last_step_keywords_allow_case_whitespace_and_punctuation` | 无（省略实例参数） | None | 标题及四个短字段大小写、横向空白和外围标点宽容，否定字段仍正确识别。 |
| `CalculationTests.test_final_step_larger_than_one_reverse_block_is_not_truncated` | 无（省略实例参数） | None | 末步正文超过 256 KiB 时仍读取标题起全部内容，不遗漏靠前的 SCF 失败。 |
| `CalculationTests.test_last_header_split_across_default_reverse_blocks` | 无（省略实例参数） | None | 真实 256 KiB 逆读边界落在末步标题内部时，必须拼接得到该完整标题。 |
| `CalculationTests.test_reverse_block_suffix_is_not_a_complete_header` | 无（省略实例参数） | None | 逆读块开头只是某行后缀时，不能把行中 STEP 文本误认成完整标题。 |
| `CalculationTests.test_reverse_reader_uses_bounded_io_and_stops_at_latest_header` | 无（省略实例参数） | None | 记录真实二进制读取，默认单次不超过 256 KiB 且找到末步后不读完整旧历史。 |
| `CalculationTests.test_reverse_reader_uses_bounded_io_and_stops_at_latest_header.observed_open` | filename: str；mode: str = "r"；**kwargs: object | Iterator[Mock]；@contextmanager 对外返回上下文管理器 | 只代理本测试日志的 rb 打开，保留真实文件与 seek/tell 行为并观察 read 大小。 |
| `CalculationTests.test_reverse_reader_uses_bounded_io_and_stops_at_latest_header.observed_open.observed_read` | size: int = -1 | bytes | 拒绝无界 read 或超过块上限的读取，其余返回真实文件字节。 |
| `CalculationTests.test_log_read_error_is_explicit_failure` | 无（省略实例参数） | None | 逆向读取发生 I/O 错误时明确技术失败，不伪装成仍在等待或成功。 |
| `CalculationTests.test_missing_log_fails_only_after_stage_directory_exists` | 无（省略实例参数） | None | 尚未准备的阶段仍 pending；已有阶段缺少弛豫日志必须明确技术失败。 |
| `CalculationTests.test_generic_tail_keeps_128_kib_limit` | 无（省略实例参数） | None | 其他调用者使用的 _tail 仍只返回末尾 128 KiB，不被弛豫逆读需求扩大。 |
| `CalculationTests.test_failure_classifier_can_exclude_scf_without_losing_oom` | 无（省略实例参数） | None | 分类器默认保留 SCF 识别；显式关闭仅屏蔽 SCF，不屏蔽 OOM。 |
| `CalculationTests.test_relax_command_old_scf_tail_and_metadata_do_not_override_final_step` | 无（省略实例参数） | None | 当前末步成功时，命令尾部及 worker 预分类的旧 SCF 失败不能重新污染终态。 |
| `CalculationTests.test_relax_nonzero_exit_and_oom_still_fail_with_complete_products` | 无（省略实例参数） | None | 末步和产物成功不能掩盖非零返回或明确 OOM，屏蔽旧 SCF 不会放宽这些失败。 |
| `CalculationTests.test_relax_input_change_conflicts` | 无（省略实例参数） | None | 声明输入世代变化不能沿用完成状态。 |
| `CalculationTests.test_relax_duplicate_sources_rejected` | 无（省略实例参数） | None | 多个末态来源明确拒绝，不任取第一个。 |
| `CalculationTests.test_symmetry_primitive_and_resources` | 无（省略实例参数） | None | 真实 spglib 理想化产物可读取，保留私人资源绝对路径文本。 |
| `CalculationTests.test_zstar_prepare_accepts_abacus_move_marker` | 无（省略实例参数） | None | 初始 STRU 保持裸标志，末态写真实 m 1 1 1 后 prepare_zstar 成功；保留资源，不调用 ASE writer，也不生成 ase_sort.dat。 |
| `CalculationTests.test_real_symmetry_lowering_flag_continues` | 无（省略实例参数） | None | 立方到四方真实降对称按 beta 理想化，FLAG 不拒绝。 |
| `CalculationTests.test_conventional_cell_output_reduced_to_primitive` | 无（省略实例参数） | None | 金刚石常规胞经标准化后输出两原子原胞。 |
| `CalculationTests.test_hard_geometry_errors` | 无（省略实例参数） | None | 非有限坐标和重叠都在计算前拒绝。 |
| `CalculationTests.test_lattice_qc_independent_of_mapping_affine` | 无（省略实例参数） | None | 真实晶格理想化修正必须触发独立应变上限；删除位点映射后仍保留该物理质量约束。 |
| `CalculationTests.test_resume_does_not_resanitize` | 无（省略实例参数） | None | 已有计算续算不再次处理或覆盖 STRU，继续绑定原 relax 声明版本。 |
| `CalculationTests.test_minimum_products_complete_with_binding` | 无（省略实例参数） | None | 最小产物组合即可成功；无旧绑定时不接受历史产物。 |
| `CalculationTests.test_invalid_static_tensor` | 无（省略实例参数） | None | JSON 非有限数值、布尔值和维数错误均拒绝。 |
| `CalculationTests.test_physics_stop_separate_from_success` | 无（省略实例参数） | None | 明确带隙拒绝归类物理终止，不能混入成功。 |
| `CalculationTests.test_tensor_payload_nonfinite_or_gibberish_rejected` | 无（省略实例参数） | None | BEC/BORN 不能只靠非空，拒绝非有限数值和没有张量载荷的文本。 |
| `CalculationTests.test_legacy_starred_bec_and_born_factor_accepted` | 无（省略实例参数） | None | legacy 星标、非注释表头和 Phonopy 换算因子格式仍可使用。 |
| `CalculationTests.test_scientific_setting_changes_identity` | 无（省略实例参数） | None | 修改 XC/kspacing 改变输入版本，修改 MPI ranks 不改变版本。 |
| `CalculationTests.test_four_commands_continue_and_root_cause_survives` | 无（省略实例参数） | None | SCF 失败后 post/static 都继续，后续零返回码不能掩盖主因。 |
| `CalculationTests.test_four_commands_continue_and_root_cause_survives.callback` | label:str, argv:list[str], cwd:str | dict | 模拟 SCF 失败及两个下游缺输入错误。 |
| `CalculationTests.test_pre_existing_is_delegated_and_all_commands_run` | 无（省略实例参数） | None | 已存在 pre 的非零提示交 native 处理，完整后续结果仍成功。 |
| `CalculationTests.test_pre_existing_is_delegated_and_all_commands_run.callback` | label:str, argv:list[str], cwd:str | dict | 模拟 native pre 已存在提示。 |
| `CalculationTests.test_native_scf_reason_beats_generic_command_errors` | 无（省略实例参数） | None | native 当前失败日志中的 SCF 主因优先于普通退出码或下游缺文件；仅在本次 run_zstar 调用中固定起始时钟，避免 Windows 文件 mtime 微秒精度差异引发假失败，不改变生产时间过滤。 |
| `CalculationTests.test_native_scf_reason_beats_generic_command_errors.callback` | label:str, argv:list[str], cwd:str | dict | run 时写原生状态，模拟 stdout 只有泛化子命令退出码。 |
| `CalculationTests.test_unknown_pre_failure_cannot_be_hidden_by_success` | 无（省略实例参数） | None | 仅广义 FileExistsError 不能自动视为 native 正常跳过。 |
| `CalculationTests.test_unknown_pre_failure_cannot_be_hidden_by_success.callback` | label:str, argv:list[str], cwd:str | dict | 模拟与 ensemble 无关的 pre 锁文件失败。 |
| `CalculationTests.test_native_latest_failure_beats_stale_running` | 无（省略实例参数） | None | 陈旧 running 状态不覆盖最新已失败结构的状态。 |
| `CalculationTests.test_technical_symmetry_error_not_physical` | 无（省略实例参数） | None | zstar 对称性异常始终可技术续算。 |
| `CalculationTests.test_slurm_resources_and_force_formal_tolerance` | 无（省略实例参数） | None | 并行资源来自 Slurm，pre/post 正式容差和 --force 保持统一。 |
| `CalculationTests.test_full_scan_loose_restoration_is_diagnostic_only` | 无（省略实例参数） | None | 全部五档扫描保留；宽松恢复初始群仍采用正式降对称原胞。 |
| `CalculationTests.test_metadata_transfer_requires_uniform_same_element_values` | 无（省略实例参数） | None | 删除映射后，同元素不同磁矩拒绝；统一有限标量按元素保留，NaN/Inf磁矩拒绝。 |
| `CalculationTests.test_metadata_canonical_movement_and_scalar_preservation` | 无（省略实例参数） | None | m 与裸 111/000、缺省 111、mag/magmom 和等价数值按有效属性统一；显式块默认与省略磁矩等价，检查块磁矩及写出行的有效标量。 |
| `CalculationTests.test_metadata_rejects_unmapped_and_vector_properties` | 无（省略实例参数） | None | 混合方向、同元素位点移动标志或磁矩不同、速度别名含全零、角度、三分量磁矩含全零和非有限标量继续拒绝；mag 前后出现速度均报速度类错误。 |
| `CalculationTests.test_no_readback_when_serializing_symmetry_result` | 无（省略实例参数） | None | 输出函数直接序列化，不调用 ASE 解析或重复原胞识别。 |
| `CalculationTests.test_custom_commands_are_not_given_extra_symmetry_arguments` | 无（省略实例参数） | None | 用户四命令原样展开，不补写 native 已默认的容差参数。 |
| `CalculationTests.test_standardized_output_has_no_second_group_identification` | 无（省略实例参数） | None | 只识别初始和 relaxed 五档，输出群字段明确只是正式目标声明。 |
| `CalculationTests.test_invalid_standardized_array_and_composition_rejected` | 无（省略实例参数） | None | 标准化返回坏形状或改变元素计量仍属硬错误，不靠重复空间群识别发现。 |
| `CalculationTests.test_zstar_revision_tracks_selected_relax_revision` | 无（省略实例参数） | None | 选定 relax 目录产生另一 UUID 后，既有 zstar 关联明确失效。 |
| `CalculationTests.test_missing_stage_inputs_cannot_reuse_completed_products` | 无（省略实例参数） | None | 完整旧物性或日志不能掩盖阶段输入缺失，stat 检查不重读科学结构。 |
| `CalculationTests.test_default_zstar_command_prefix_is_a_scientific_setting` | 无（省略实例参数） | None | 未使用四命令覆盖时更改默认前缀使版本不匹配，不能复用旧终态。 |
| `CalculationTests.test_science_and_qc_changes_require_new_directory` | 无（省略实例参数） | None | 命令任意参数及 QC 小设置均冻结，Slurm 动态资源保持可调。 |
| `CalculationTests.test_formal_failure_still_finishes_all_diagnostic_tolerances` | 无（省略实例参数） | None | 正式档失败先完成五档诊断，再作为硬错误拒绝标准化。 |
| `CalculationTests.test_relax_revision_index_is_explicit_scoped_and_not_global` | 无（省略实例参数） | None | 同轮显式 relax 成功版本索引避免重复历史 I/O；不提供索引或索引工作根不符时重新读取，不使用全局持久缓存。 |
| `CalculationTests.test_invalid_slurm_resource_values_rejected` | 无（省略实例参数） | None | Slurm 提供零或负资源值时不构造错误的真实 MPI 命令。 |

本文件声明71项，其中测试方法61项。

## test_cli_config.py

本地 pre 命令、覆盖锚点、必须原生绝对的结构根，以及取消旧入口/配置。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `CliConfigTests`（类） | 由 unittest 框架构造；继承标准构造器 | CliConfigTests | 测试只在新项目 .test-artifacts 中建立临时文件。 |
| `CliConfigTests.setUp` | 无（省略实例参数） | None | 建立本测试独立临时沙箱并注册 cleanup；只写新版 .test-artifacts。 |
| `CliConfigTests.test_help_and_positional_commands` | 无（省略实例参数） | None | 帮助及功能选择采用位置子命令，帮助不需要计算输入。 |
| `CliConfigTests.test_config_overlay_and_anchor` | 无（省略实例参数） | None | 自定义work根按覆盖文件目录定位；默认namespace=local，配置无metadata_csv/input_source。 |
| `CliConfigTests.test_old_screening_and_handoff_commands_are_not_available` | 无（省略实例参数） | None | screening、旧pipeline、validate screening与--from-screening均在参数解析时以2拒绝。 |
| `CliConfigTests.test_structure_root_requires_an_original_absolute_path` | 无（省略实例参数） | None | 相对structures_root不能被配置位置悄悄转成合法绝对输入。 |
| `CliConfigTests.test_old_input_configuration_keys_are_rejected` | 无（省略实例参数） | None | 旧CSV、MP模式、handoff和结构版本键明确报ConfigError。 |
| `CliConfigTests.test_unknown_config_is_rejected` | 无（省略实例参数） | None | 配置键拼写错误必须给 ConfigError。 |
| `CliConfigTests.test_custom_syntax_error_has_configuration_context` | 无（省略实例参数） | None | Python 配置语法错误报告文件和行号。 |
| `CliConfigTests.test_windows_and_posix_remain_independent` | 无（省略实例参数） | None | ntpath/posixpath 处理不随主机污染另一类根路径。 |
| `CliConfigTests.test_unsafe_or_ambiguous_references` | 无（省略实例参数） | None | 拒绝越界、混合分隔符、盘符相对和歧义 UNC。 |

本文件声明11项，其中测试方法9项。

## test_input_adapters.py

沿用历史文件名，现仅验证LOCAL单层文件发现、CIF→ABACUS→自动读取、必要检查和稳定身份。没有CSV输入适配器。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `InputAdapterTests`（类） | 由unittest框架构造；继承标准构造器 | InputAdapterTests | 测试LOCAL文件输入；不导入已移除CSV适配器。 |
| `InputAdapterTests.setUp` | 无（省略实例参数） | None | 建立本项目.test-artifacts中隔离结构目录和MgO周期几何。 |
| `InputAdapterTests.test_discovery_is_sorted_single_level_and_has_no_filename_filter` | 无（省略实例参数） | None | 包含CSV、隐藏名、中文和未知后缀；排序稳定，子目录不递归，来源均为LOCAL。 |
| `InputAdapterTests.test_real_cif_without_extension_uses_explicit_first_reader` | 无（省略实例参数） | None | 真实无扩展名CIF通过首个显式读取器，化学信息来自实际结构。 |
| `InputAdapterTests.test_reader_order_stops_after_first_success` | 无（省略实例参数） | None | 分别模拟第1/2/3个解析器成功，断言cif/abacus/None顺序和及时停止。 |
| `InputAdapterTests.test_reader_reports_every_parser_exception` | 无（省略实例参数） | None | 三次读取错误完整保留各自类型与详情，不串用异常类型。 |
| `InputAdapterTests.test_bad_geometry_does_not_trigger_another_parser` | 无（省略实例参数） | None | 解析成功但体积为零时拒绝，不能再换格式绕过物理检查。 |
| `InputAdapterTests.test_nonfinite_occupancy_and_overlap_are_rejected` | 无（省略实例参数） | None | 半占位、NaN/Inf占位和短程重叠均拒绝。 |
| `InputAdapterTests.test_source_constraints_and_magnetism_are_normalized` | 无（省略实例参数） | None | 源固定约束及非零磁矩按固定政策清除/归零，不建立位点映射。 |
| `InputAdapterTests.test_identity_keeps_extension_and_is_not_based_on_formula_or_index` | 无（省略实例参数） | None | 同stem不同后缀保持不同身份，namespace改变身份，计划平铺且不写目录；手造不安全路径形成单条planning_error，正常计划继续。 |
| `InputAdapterTests.test_casefold_identity_collision_is_rejected_before_writing` | 无（省略实例参数） | None | 内存模拟大小写等价文件，规划拒绝碰撞且未建输出。 |
| `InputAdapterTests.test_stable_directory_encoding_has_no_separator_collision` | 无（省略实例参数） | None | LOCAL安全编码避免双下划线拼接和staging保留命名碰撞。 |
| `InputAdapterTests.test_input_and_output_trees_are_isolated` | 无（省略实例参数） | None | 相同根及双向祖先关系拒绝，Windows大小写变体不能绕过重叠判断。 |

本文件声明13项，其中测试方法11项。

## test_post_cleanup.py

四类中间文件的终态资格、只读预览、锁、逐项删除失败恢复及 POSIX 目录 fd 模型。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `PostCleanupTests`（类） | 由 unittest 框架构造；继承标准构造器 | PostCleanupTests | 组合已有真实记录夹具，验证清理边界、只读预览及失败后的独立重试。 |
| `PostCleanupTests.setUp` | 无（省略实例参数） | None | 借用夹具构造器而不继承其测试类，避免发现器重复执行整套已有测试。 |
| `PostCleanupTests.make_terminal` | identifier: str='mp-clean', state: str='success', reason: str='', products: bool=True, insulating: bool=True, static: bool=True | str | 生成与冻结计划关联的终态任务；失败任务可有意不具备完整介电产物。 |
| `PostCleanupTests.make_intermediates` | task: str | tuple[list[str], list[str]] | 在弛豫、参考及位移目录放小型同名载荷，同时放置必须保留的相近文件名。 |
| `PostCleanupTests.publish_without_cleanup` | 无（省略实例参数） | dict | 先发布真实46列结果，明确禁用自动清理，以测试独立clean重试路径。 |
| `PostCleanupTests.tree_snapshot` | 无（省略实例参数） | dict[str, bytes] | 记录整个隔离根内的文件及字节，检测预览是否偷偷建锁、目录或写出记录。 |
| `PostCleanupTests.assert_present` | paths: list[str] | None | 逐个断言测试目标仍存在，防止终态不可信时误删一部分文件。 |
| `PostCleanupTests.exercise_posix_unlink` | scenario: str | dict | 用受限目录fd模型覆盖POSIX分支；文件和最终删除仍是真实隔离夹具内操作。 |
| `PostCleanupTests.exercise_posix_unlink.open_directory` | path: str, flags: int | int | 仅将真实目标父目录注册为模型fd，不允许用此模型打开别的路径。 |
| `PostCleanupTests.exercise_posix_unlink.fstat_directory` | fd: int | object | 读取模型fd对应真实父目录身份，指定场景仅伪造inode不匹配。 |
| `PostCleanupTests.exercise_posix_unlink.stat_relative` | name: str, *, dir_fd: int, follow_symlinks: bool | object | 只解析已登记父目录fd下的basename，保留真实文件签名或注入身份失配。 |
| `PostCleanupTests.exercise_posix_unlink.unlink_relative` | name: str, *, dir_fd: int | None | 相对模型fd定位并实际删除唯一夹具目标；相邻文件永远不作为删除参数。 |
| `PostCleanupTests.exercise_posix_unlink.close_directory` | fd: int | None | 释放且只释放已打开的模型fd，覆盖成功和身份不匹配异常路径。 |
| `PostCleanupTests.test_posix_dir_fd_deletes_real_target_relative_to_verified_parent` | 无（省略实例参数） | None | POSIX模型分支确实删除目标且保留相邻文件，unlink只得到basename和已核验fd。 |
| `PostCleanupTests.test_posix_dir_fd_parent_identity_mismatch_preserves_real_target` | 无（省略实例参数） | None | 父目录fd身份不匹配时零unlink，关闭句柄并保留真实目标字节。 |
| `PostCleanupTests.test_posix_dir_fd_file_identity_mismatch_preserves_real_target` | 无（省略实例参数） | None | 相对fd复核发现目标身份变化时零unlink，同样释放目录fd并保留目标。 |
| `PostCleanupTests.test_automatic_cleanup_exact_names_all_subdirectories` | 无（省略实例参数） | None | 成功CSV发布后递归清理四类中间文件，保留其他矩阵、结果、输入和原生日志。 |
| `PostCleanupTests.test_physical_gap_and_imaginary_terminal_tasks_are_cleaned` | 无（省略实例参数） | None | 明确带隙拒绝和虚频拒绝无需static结果；其输入关联通过时同样可清理。 |
| `PostCleanupTests.test_technical_symmetry_and_unknown_physical_failures_are_retained` | 无（省略实例参数） | None | SCF、对称性及不明物理终止不能仅凭terminal标志删掉可续算中间结果。 |
| `PostCleanupTests.test_success_with_bad_static_or_insulation_conflict_is_not_cleaned` | 无（省略实例参数） | None | worker虽记成功，产物解析失败或绝缘结论冲突时仍保留全部中间文件。 |
| `PostCleanupTests.test_revision_mismatch_before_publication_is_retained` | 无（省略实例参数） | None | 输入失配会导出失败行；CSV存在并不能把失配任务升级为可删除对象。 |
| `PostCleanupTests.test_preview_is_read_only_and_reports_candidates_without_csv` | 无（省略实例参数） | None | 独立preview无需CSV，不建锁、不写文件，只汇报候选文件和大小。 |
| `PostCleanupTests.test_validate_only_previews_without_creating_locks_or_csv` | 无（省略实例参数） | None | post validate给出预计清理量，但整个操作保持只读。 |
| `PostCleanupTests.test_disabled_automatic_cleanup_still_allows_explicit_clean` | 无（省略实例参数） | None | 配置False只关闭collect自动删除；显式clean仍按完整资格重新核验执行。 |
| `PostCleanupTests.test_cleanup_switch_requires_bool_and_legacy_four_keys_default_on` | 无（省略实例参数） | None | 删除开关只接受真实布尔值；旧四键完整配置继续按默认开启执行。 |
| `PostCleanupTests.test_cli_clean_and_validate_help_are_positional_actions` | 无（省略实例参数） | None | 清理和验证仍采用位置动作，帮助可以直接列出clean而不读取默认工作根。 |
| `PostCleanupTests.test_explicit_clean_is_idempotent_and_never_rewrites_csv` | 无（省略实例参数） | None | CSV已存在时可重试清理，重复调用不重导表格、不把缺文件当作失败。 |
| `PostCleanupTests.test_missing_or_modified_csv_blocks_all_deletion` | 无（省略实例参数） | None | 独立clean不能绕过结果发布，已有人修改的任一物性列也必须拒绝使用。 |
| `PostCleanupTests.test_csv_change_after_initial_comparison_cannot_become_new_baseline` | 无（省略实例参数） | None | CSV刚完成逐列比较后被修改，也不能把新字节当作快照基线继续删除。 |
| `PostCleanupTests.test_csv_change_after_initial_comparison_cannot_become_new_baseline.change_after_comparison` | path: str | dict[str, tuple[str, ...]] | 首次读取返回旧可信内容后立即改物性，模拟真实比对与下一快照的竞争。 |
| `PostCleanupTests.test_csv_publication_failure_never_starts_cleanup` | 无（省略实例参数） | None | CSV写出发生磁盘错误时，中间文件和原始计算现场全部保留。 |
| `PostCleanupTests.test_unfinished_batch_does_not_delete_already_finished_member` | 无（省略实例参数） | None | 仍有批内等待结构时不发布整表，也不提前删除已完成成员的中间结果。 |
| `PostCleanupTests.test_input_or_plan_change_after_publication_blocks_clean` | 无（省略实例参数） | None | CSV发布后出现新输入或新续算计划时，旧表不再授权当前计算树的清理。 |
| `PostCleanupTests.test_revision_changed_after_publication_cannot_use_old_csv` | 无（省略实例参数） | None | 已发布表格对应旧声明版本，不能据此删除新版本的现场。 |
| `PostCleanupTests.test_independent_clean_collects_and_parses_once` | 无（省略实例参数） | None | 独立清理仅discover和物性解析各一次；锁内复核版本/stat，历史计划、台账、进度不得回读。 |
| `PostCleanupTests.test_older_submitted_unfinished_plan_blocks_cleanup` | 无（省略实例参数） | None | 新轮记录成功不能掩盖旧轮已提交未结束作业；未知队列状态时保守保留文件。 |
| `PostCleanupTests.test_historical_frozen_calculation_directory_lock_is_respected` | 无（省略实例参数） | None | 历史计划使用过不同阶段目录时也要取得其锁，不能只锁当前模板目录。 |
| `PostCleanupTests.test_controller_lock_conflict_preserves_every_target` | 无（省略实例参数） | None | 提交控制器持锁时禁止清理，避免检查结束后新计划同时开始改写现场。 |
| `PostCleanupTests.test_worker_lock_conflict_preserves_current_structure` | 无（省略实例参数） | None | worker还持有结构锁时，记录中虽有终态也不删除其正在使用的中间文件。 |
| `PostCleanupTests.test_single_unlink_failure_does_not_stop_other_candidates` | 无（省略实例参数） | None | 一个文件权限不足只影响该文件，后续目录和候选仍遍历，便于重试释放空间。 |
| `PostCleanupTests.test_single_unlink_failure_does_not_stop_other_candidates.deny_one` | root: str, task_dir: str, path: str, signature: tuple | bool | 只对一个本测试中间文件模拟权限错误，其余调用真实受限删除实现。 |
| `PostCleanupTests.test_candidate_missing_after_enumeration_is_idempotent` | 无（省略实例参数） | None | 枚举后文件被另一清理者移除属于幂等缺失，其余目标仍正常释放。 |
| `PostCleanupTests.test_candidate_missing_after_enumeration_is_idempotent.remove_before_unlink` | root: str, task_dir: str, path: str, signature: tuple | bool | 仅删除本隔离夹具的一个目标，再让真实helper识别已不存在。 |
| `PostCleanupTests.test_candidate_replaced_after_enumeration_is_preserved` | 无（省略实例参数） | None | 候选被改写后不能按旧签名删除；保留新文件并继续处理其余中间文件。 |
| `PostCleanupTests.test_candidate_replaced_after_enumeration_is_preserved.replace_before_unlink` | root: str, task_dir: str, path: str, signature: tuple | bool | 用不同长度的新字节替代一个隔离目标，确保跨平台stat签名必然变化。 |
| `PostCleanupTests.test_target_and_directory_links_never_delete_external_file` | 无（省略实例参数） | None | 不跟随目录软链接，也不删除具有匹配名称的链接目标；外部仅指测试树内其他目录。 |
| `PostCleanupTests.test_target_and_directory_links_never_delete_external_file.identify_test_links` | path: str | bool | 只有本测试两个模型入口替代系统reparse标志；其他路径仍走真实检查。 |
| `PostCleanupTests.test_real_cli_clean_after_collect_and_repeated_clean` | 无（省略实例参数） | None | 真实CLI支持collect后显式clean并重复重试，既不覆盖CSV也不重跑zstar。 |

本文件声明48项，其中测试方法30项。

## test_post_config_writer.py

一批一表、46 列契约、数值排序、来源字段保护、无覆盖发布和 CLI 配置。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `PostConfigWriterTests`（类） | 由 unittest 框架构造；继承标准构造器 | PostConfigWriterTests | 只在新版.test-artifacts沙箱创建输出，不使用真实工作根或资源库。 |
| `PostConfigWriterTests.setUp` | 无（省略实例参数） | None | 建立独立输出沙箱，测试结束只清理本次临时目录。 |
| `PostConfigWriterTests.row` | identity: str, value: float \| str, source: str='MP' | dict | 构造一行合法部分结果；排序与源身份不借助被测排序器生成。 |
| `PostConfigWriterTests.read_output` | 无（省略实例参数） | list[dict] | 按普通CSV消费者读取已发布结果，验证BOM和引用规则。 |
| `PostConfigWriterTests.test_exact_46_columns_and_original_order` | 无（省略实例参数） | None | 原39列不变，追加六项追溯和最后备注，不插入排序编号。 |
| `PostConfigWriterTests.test_numeric_sort_zero_negative_and_missing_last` | 无（省略实例参数） | None | 数值100必须排在9前，零和负值有效，缺失始终置底。 |
| `PostConfigWriterTests.test_equal_values_use_identity_and_do_not_renumber_priority` | 无（省略实例参数） | None | 同值排序确定，原priority字段不随新行序改变。 |
| `PostConfigWriterTests.test_mp_global_id_always_empty_and_false_not_unknown` | 无（省略实例参数） | None | 即使调用者传入MP样本号也清空；明确False保留字符串False。 |
| `PostConfigWriterTests.test_scientific_parser_cannot_overwrite_metadata` | 无（省略实例参数） | None | 物性读取器不能覆盖源身份、priority或nominal预测。 |
| `PostConfigWriterTests.test_nonfinite_or_nonnumeric_sort_values_do_not_create_file` | 无（省略实例参数） | None | NaN、Inf、布尔或数值字符串都不能进入排序/最终CSV。 |
| `PostConfigWriterTests.test_bom_and_json_roundtrip` | 无（省略实例参数） | None | 中文备注与包含逗号引号的全部原子JSON应无损通过标准CSV引用。 |
| `PostConfigWriterTests.test_repeated_export_preserves_existing_bytes` | 无（省略实例参数） | None | 重复收集不得覆盖已确认的本批最终结果。 |
| `PostConfigWriterTests.test_write_failure_leaves_no_partial_final_file` | 无（省略实例参数） | None | 落盘失败只清理自己临时文件，最终目标始终不存在。 |
| `PostConfigWriterTests.test_publication_race_never_overwrites_winner` | 无（省略实例参数） | None | 另一个进程抢先发布时，保留胜者结果且清理本次临时文件。 |
| `PostConfigWriterTests.test_publication_race_never_overwrites_winner.competitor` | source: str；destination: str | NoReturn（抛 FileExistsError） | 模拟在真正发布前出现的已存在目标，而非模拟成功路径。 |
| `PostConfigWriterTests.test_config_anchor_default_output_and_unknown_legacy_key` | 无（省略实例参数） | None | 配置相对自身定位，默认一批一表，旧散装导入选项不被静默接受。 |
| `PostConfigWriterTests.test_invalid_subdir_and_relative_traversal_rejected` | 无（省略实例参数） | None | 错误路径在读任务前被拒绝，不把其他批次当成本批结果。 |
| `PostConfigWriterTests.test_cli_positional_post_default_validate_and_help` | 无（省略实例参数） | None | 后处理默认collect，validate为位置动作，帮助不加载工作根。 |

本文件声明18项，其中测试方法13项。

## test_post_discovery.py

只发现正式任务，关联冻结参数及声明版本；失败保留、未结束阻断与相对路径迁移。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `PostDiscoveryTests`（类） | 由 unittest 框架构造；继承标准构造器 | PostDiscoveryTests | 用已封存的小文件替代数值计算，断言只读关联不写状态或接管旧散装产物。 |
| `PostDiscoveryTests.setUp` | 无（省略实例参数） | None | 为每项测试创建项目内独占工作根，不向旧工作流或外部缓存写入。 |
| `PostDiscoveryTests.make_task` | source: str = "MJM"；identity: str = "1"；namespace: str = "batch"；nominal: dict[str, str] \| None = None | str | 建立当前四产物、稳定身份、UUID 和结构化输入描述的前处理夹具，另放 zstar 输入；只写隔离小文件，不访问资源。 |
| `PostDiscoveryTests.plan` | task: str；run: str；outcome: str = "success"；finished: bool = True；status: str = "planned"；execution: dict \| None = None；pointer: str \| None = None | str | 写一轮 schema 2 冻结计划和可选 worker 进度，显式绑定输入 UUID；pointer 可模拟正在运行计划的相对引用，返回计划路径。 |
| `PostDiscoveryTests.collect` | 无（省略实例参数） | list[dict] | 使用只包含后处理定位设置的配置调用只读发现，不提供当前 zstar 科学参数。 |
| `PostDiscoveryTests.test_mp_global_sample_always_empty_and_nominal_unknown` | 无（省略实例参数） | None | MP 即使封存了意外样本号，输出也为空；不把未知 nominal 冒充 False。 |
| `PostDiscoveryTests.test_mjm_source_metadata_keeps_false_or_true_nominal` | 无（省略实例参数） | None | MJM 直接使用已封存源行，不依赖旧优先级表。 |
| `PostDiscoveryTests.test_nominal_inconsistent_false_is_rejected` | 无（省略实例参数） | None | 明确 False 与非空数值不可同时存在，避免错误物性混入 CSV。 |
| `PostDiscoveryTests.test_nominal_rejects_underscores_and_nonfinite_tokens` | 无（省略实例参数） | None | nominal 数值沿用旧表严格词法，不接受 Python float 的下划线扩展或 NaN/Inf。 |
| `PostDiscoveryTests.test_no_calculation_plan_is_unfinished_even_with_result_directory` | 无（省略实例参数） | None | 有目录或旧产物本身不构成新版已结束证据。 |
| `PostDiscoveryTests.test_frozen_commands_and_xc_used_instead_of_current_template` | 无（省略实例参数） | None | 版本关联使用实际轮次冻结的自定义科学命令，而不是外部默认配置。 |
| `PostDiscoveryTests.test_latest_unfinished_attempt_does_not_resurrect_old_success` | 无（省略实例参数） | None | 旧成功后新建续算计划尚未完成时，整批最终导出必须等待新轮次。 |
| `PostDiscoveryTests.test_same_second_random_name_does_not_reverse_attempt_order` | 无（省略实例参数） | None | 同秒运行目录的 UUID 字典序可能倒置，后处理必须选实际较新的计划。 |
| `PostDiscoveryTests.test_latest_active_snapshot_follows_completed_owner_progress` | 无（省略实例参数） | None | 较新计划的 active 只是观察快照，真正执行计划结束后不能永远卡在 active。 |
| `PostDiscoveryTests.test_finished_result_with_changed_source_revision_becomes_failure` | 无（省略实例参数） | None | 新来源版本不能借用旧终态，保留失败行同时禁止读取旧物性。 |
| `PostDiscoveryTests.test_corrupt_pre_metadata_terminal_keeps_failure_row` | 无（省略实例参数） | None | 前处理必需文件缺失保留可定位终态失败，不丢失该任务。 |
| `PostDiscoveryTests.test_plan_missing_task_is_global_batch_integrity_error` | 无（省略实例参数） | None | 已计划任务被移出工作根时，不能输出悄悄少一行的所谓整批最终 CSV。 |
| `PostDiscoveryTests.test_progress_from_other_input_cannot_supply_terminal_result` | 无（省略实例参数） | None | 进度中的完成项必须与其所属计划的版本一一对应。 |
| `PostDiscoveryTests.test_input_preparation_failure_can_end_without_zstar_stru` | 无（省略实例参数） | None | 未能产生可靠输入的任务也有一行终态失败，但没有可读物性或虚构版本。 |
| `PostDiscoveryTests.test_distinct_namespace_allows_same_mjm_number` | 无（省略实例参数） | None | 独立数据集命名空间允许各自包含相同的样本号与 priority。 |
| `PostDiscoveryTests.test_duplicate_priority_in_same_dataset_is_global_error` | 无（省略实例参数） | None | 即使前处理记录自洽，同数据集两条样本复用 priority 仍属于表级关联错误。 |
| `PostDiscoveryTests.test_failed_sbatch_is_terminal_without_queue_query` | 无（省略实例参数） | None | 提交明确失败是当前轮的技术终态；不必也不应该靠队列探测猜测。 |
| `PostDiscoveryTests.test_relax_failure_preserved_when_zstar_never_planned` | 无（省略实例参数） | None | 上游 cell-relax 失败仍应占整批一行，而不是因为没有 zstar 目录就丢弃。 |
| `PostDiscoveryTests.test_relax_failed_revision_change_replaces_old_scf_reason` | 无（省略实例参数） | None | 上游失败后显式换来源版本，不再沿用旧 SCF 根因。 |
| `PostDiscoveryTests.test_relax_failure_uses_frozen_layout_and_remains_zstar_unavailable` | 无（省略实例参数） | None | 冻结的非默认弛豫子目录不受当前模板影响，匹配也不能冒充 zstar 已绑定。 |
| `PostDiscoveryTests.test_migrated_root_resolves_everything_from_relative_records` | 无（省略实例参数） | None | 工作树迁移后旧输入已改变，仍只核验新根下的相对记录与输入。 |
| `PostDiscoveryTests.test_old_scattered_directory_is_not_imported` | 无（省略实例参数） | None | 旧目录只有 STRU 不属于新版批次，不接管、不生成任何记录。 |
| `PostDiscoveryTests.test_discovery_is_read_only` | 无（省略实例参数） | None | 发现前后逐文件字节一致；查询不创建原生计算状态、日志或队列快照。 |
| `PostDiscoveryTests.test_legacy_protocol_does_not_supply_current_terminal_state` | 无（省略实例参数） | None | 旧协议只读可见，但不自动转成当前版本的终态或重标其UUID。 |
| `PostDiscoveryTests.test_declared_revision_does_not_claim_content_integrity` | 无（省略实例参数） | None | 只改字节而不声明新版本不会被UUID识别；此测试固定公开的保证边界。 |
| `PostDiscoveryTests.test_missing_stage_input_does_not_borrow_finished_properties` | 无（省略实例参数） | None | UUID不能掩盖必要阶段输入缺失，普通stat检查仍阻止读取残留物性。 |

本文件声明31项，其中测试方法26项。

## test_post_pipeline.py

真实文件记录链串联发现、部分物性、整批发布与同轮清理，防止重复采集和并发变化。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `file_bytes` | path: str | bytes | 测试专用：直接读取隔离小文件字节以断言只读或未覆盖，不用于生产版本检测。 |
| `make_revision` | task: str, execution: dict, stage: str | dict | 生成显式版本夹具；zstar 关联已有的最小成功 relax 计划，不新增记录种类。 |
| `PostPipelineTests`（类） | 由 unittest 框架构造；继承标准构造器 | PostPipelineTests | 串联当前前处理记录、冻结科学参数、worker 声明终态及物性读取，不使用内容摘要作为版本证明。 |
| `PostPipelineTests.setUp` | 无（省略实例参数） | None | 建立新版项目内部临时工作根和显式输出地址，不触碰真实批次。 |
| `PostPipelineTests.make_task` | source: str, source_id: str, priority: int=1, nominal: dict \| None=None | str | 按来源、命名空间和源编号创建当前前处理 manifest 与三份非空产物，并准备独立 zstar 输入；MJM 保留编号和 priority，不执行科学写出。 |
| `PostPipelineTests.make_products` | task: str, total: float=20.0, gap: float=1.2, include_static: bool=True, insulating: bool=True | None | 写出最小真实格式产物；无需 qpoints、irreps 或任何赝势轨道资源。 |
| `PostPipelineTests.make_plan` | entries: list[tuple[str, str, str]], execution: dict \| None=None | str | entries 每项为任务路径、终态和短原因，空终态表示未结束；写冻结计划与批次进度并关联声明 UUID，返回计划路径。 |
| `PostPipelineTests.read_rows` | 无（省略实例参数） | list[dict[str, str]] | 用标准 CSV 读取器回读最终文件，检查 JSON/中文备注的引用没有破坏列数。 |
| `PostPipelineTests.test_auto_cleanup_reuses_one_collection_and_one_parse` | 无（省略实例参数） | None | 自动清理与发布共享同轮采集，discover和物性解析各一次；历史计划、台账、进度及父版本索引不得回读。 |
| `PostPipelineTests.test_mixed_batch_sort_and_all_mp_global_ids_blank` | 无（省略实例参数） | None | 一根一表同时适配 MP/MJM；按总迹平均数值降序，编号和 priority 不影响排序。 |
| `PostPipelineTests.test_physical_and_scf_failures_remain_one_partial_row_each` | 无（省略实例参数） | None | 虚频保留已完成带隙/BEC，SCF 是根因；缺结果不伪装为零，也不额外生成三行。 |
| `PostPipelineTests.test_gap_rejection_preserves_zero_gap_and_empty_static` | 无（省略实例参数） | None | 零带隙是有效数值；明确绝缘拒绝仍导出，缺失的物性单元格保持空白。 |
| `PostPipelineTests.test_unfinished_task_blocks_final_csv_and_directory_creation` | 无（省略实例参数） | None | 同批只要还有当前结构/批内等待成员，整表不发布且不预先建立输出目录。 |
| `PostPipelineTests.test_bad_static_preserves_task_and_other_valid_values` | 无（省略实例参数） | None | 已结束成功任务中的损坏 JSON 记为后处理失败，保留有效带隙、电子项和 BEC。 |
| `PostPipelineTests.test_static_parent_regular_file_is_isolated_to_one_result_row` | 无（省略实例参数） | None | 静态响应父位置误为普通文件时保留任务行和其他物性，不使整批提取异常退出。 |
| `PostPipelineTests.test_recorded_success_cannot_hide_current_insulation_rejection` | 无（省略实例参数） | None | 历史成功记录与当前明确绝缘拒绝冲突时，不能输出一行无备注的成功结果。 |
| `PostPipelineTests.test_frozen_scientific_commands_are_used_for_provenance` | 无（省略实例参数） | None | 计算计划冻结的科学命令是输入关联依据，不读取当前可修改的 zstar 配置模板。 |
| `PostPipelineTests.test_latest_unfinished_attempt_does_not_reuse_old_success` | 无（省略实例参数） | None | 同一任务前轮成功、后轮尚未结束时，不用旧终态伪造本批最终表。 |
| `PostPipelineTests.test_multiple_finished_attempts_produce_one_row` | 无（省略实例参数） | None | 同一工作根的续算轮次合并为一个结构行，不把一次提交当成独立数据批次。 |
| `PostPipelineTests.test_changed_revision_keeps_identity_without_stale_properties` | 无（省略实例参数） | None | 来源显式换版本后不采用旧带隙、介电或 BEC，但仍保留错误任务行。 |
| `PostPipelineTests.test_validate_only_and_existing_output_leave_inputs_untouched` | 无（省略实例参数） | None | validate 不发布结果；重复导出拒绝覆盖，已生成表格与计算输入字节保持不变。 |
| `PostPipelineTests.test_relocated_work_root_requires_no_record_path_rewrite` | 无（省略实例参数） | None | 整棵工作根迁移后，任务、计划和证据以相对路径重新定位，不依赖旧机器地址。 |
| `PostPipelineTests.test_input_change_during_collection_prevents_publication` | 无（省略实例参数） | None | 读取物性后若输入变化，不能发布由两个不同时间版本拼出的整批表格。 |
| `PostPipelineTests.test_input_change_during_collection_prevents_publication.change_after_read` | directory: str, config: dict | dict | 在模拟的并发写入点更改本测试任务输入，返回原读取结果供一致性检查识别。 |
| `PostPipelineTests.test_input_change_after_discovery_cannot_become_snapshot_baseline` | 无（省略实例参数） | None | 发现阶段验证后、物性快照建立前的输入变化也必须阻止旧结果发布。 |
| `PostPipelineTests.test_input_change_after_discovery_cannot_become_snapshot_baseline.change_after_discovery` | config: dict | list[dict] | 第一次发现返回可信旧版本后立即变更输入，模拟真正的校验/快照边界竞争。 |
| `PostPipelineTests.test_real_cli_validate_then_default_collect_from_custom_config` | 无（省略实例参数） | None | 真实 CLI 从自定义配置相对路径加载一整批，验证只读、收集混合结果并拒绝覆盖。 |

本文件声明27项，其中测试方法17项。

## test_post_readers.py

带隙、BORN、BEC 与静态总介电的严格解析，保留原始张量方向和有效部分结果。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `PostReaderTests`（类） | 由 unittest 框架构造；继承标准构造器 | PostReaderTests | 验证原始方向、部分结果、严格物理元数据及错误隔离。 |
| `PostReaderTests.setUp` | 无（省略实例参数） | None | 在授权项目内建立独立临时目录；测试后删除自身的临时产物。 |
| `PostReaderTests.write` | name: str；content: str | str | 只在本测试临时根中写入指定相对名称的 UTF-8 夹具。 |
| `PostReaderTests.static` | **updates: object | str | 写出带完整原生物理元数据的总静态响应，支持逐项错误注入。 |
| `PostReaderTests.products` | 无（省略实例参数） | None | 生成一套最小完整成功产物，张量刻意不对称以检查是否误转置。 |
| `PostReaderTests.test_inline_examples_preserve_values_and_bec_order` | 无（省略实例参数） | None | 文件内夹具核对方向、全部不等价原子及原子顺序，不访问项目外历史样例。 |
| `PostReaderTests.test_fortran_numbers_and_invalid_number_strings` | 无（省略实例参数） | None | 接受 E/D 科学记数并拒绝非有限值、布尔、下划线和非 ASCII 文本。 |
| `PostReaderTests.test_band_label_missing_duplicate_or_malformed` | 无（省略实例参数） | None | 带隙字段必须唯一且严格匹配，不能静默选择某一个。 |
| `PostReaderTests.test_born_nine_components_and_optional_factor_and_matrix` | 无（省略实例参数） | None | BORN 支持标准九分量行以及单位因子+三行矩阵，因子不乘电子张量。 |
| `PostReaderTests.test_born_malformed_first_tensor_never_falls_back_to_later_atom` | 无（省略实例参数） | None | 错误首张量不能回退到后续原子 BEC，将其他物理量冒充电子介电。 |
| `PostReaderTests.test_static_preserves_asymmetry_negative_values_and_no_double_electronic` | 无（省略实例参数） | None | 直接读取总张量，不对称化、不转置、不取绝对值、不重复增加电子项。 |
| `PostReaderTests.test_static_requires_exact_physical_metadata` | 无（省略实例参数） | None | 有限频率、离子项、二维响应、错误单位及字符串/布尔频率都不进入总介电列。 |
| `PostReaderTests.test_static_rejects_duplicate_keys_nonfinite_and_wrong_component_types` | 无（省略实例参数） | None | JSON 的重复键、非有限数值、字符串、布尔和错误形状均给出受控数据错误。 |
| `PostReaderTests.test_bec_legacy_star_markers_and_uncommented_header` | 无（省略实例参数） | None | 兼容旧版未注释表头、紧贴或分离的星标，并保留原不连续编号。 |
| `PostReaderTests.test_bec_rejects_duplicate_zero_index_unknown_lines_and_bad_tensor` | 无（省略实例参数） | None | BEC 不允许重复编号、零号、错误数据行、任意说明行或不完整张量。 |
| `PostReaderTests.test_full_reader_returns_32_physical_fields_without_errors` | 无（省略实例参数） | None | 完整输入得到带隙、27 分量、2 均值、BEC 数量和 JSON 共 32 个物性字段。 |
| `PostReaderTests.test_native_insulation_false_preserves_band_gap_without_another_error` | 无（省略实例参数） | None | 明确绝缘拒绝是有效原生结论，不丢弃带隙，也不当作 JSON 解析失败。 |
| `PostReaderTests.test_native_insulation_requires_strict_boolean_and_finite_optional_values` | 无（省略实例参数） | None | 绝缘结论不能从字符串、整数或非有限元数据猜测。 |
| `PostReaderTests.test_partial_total_independent_of_electronic_bec_and_band` | 无（省略实例参数） | None | 只存在静态总张量时仍可提取总分量与排序值，其他字段不伪造零。 |
| `PostReaderTests.test_partial_band_gap_retained_when_band_data_missing` | 无（省略实例参数） | None | 物理拒绝后的单独 band_info 仍能保留带隙，同时报告 band.dat 缺失。 |
| `PostReaderTests.test_ambiguous_band_pairs_are_not_selected_arbitrarily` | 无（省略实例参数） | None | 两套完整能带结果即使包含默认 Band_Structure 也必须报歧义。 |
| `PostReaderTests.test_configurable_band_root` | 无（省略实例参数） | None | 自定义相对 Out 根只改变本任务内部寻址，不自动改写为别的平台绝对地址。 |
| `PostReaderTests.test_nonempty_bec_dat_is_required_but_rep_partial_is_retained` | 无（省略实例参数） | None | 缺少 BEC.dat 时仍保留有效不等价 BEC，但完整性错误不会丢失。 |
| `PostReaderTests.test_ionic_overflow_retains_valid_total_and_electronic_fields` | 无（省略实例参数） | None | 相减真实溢出只丢失离子字段，已验证的总/电子原始数据继续保留。 |
| `PostReaderTests.test_source_file_changes_are_rejected` | 无（省略实例参数） | None | 读取前后的 stat 身份/长度/时间信息变化时拒绝本文件，防止并发改写造成混合时刻结果；这不是长期内容一致性检查。 |
| `PostReaderTests.test_source_file_changes_are_rejected.changed` | info: os.stat_result | tuple[int, int, int, int] | 调用真实 stat 签名 helper，最后一次复核伪造不同文件长度，驱动读取器拒绝并发变更。 |
| `PostReaderTests.test_symlink_parent_not_used_as_results` | 无（省略实例参数） | None | 目录链接不能将另一个任务结果借入当前结构；Windows 无权限时用等价检测注入。 |
| `PostReaderTests.test_bec_rep_partial_survives_static_metadata_failure` | 无（省略实例参数） | None | 静态物理约定错误不应丢失已经有效的带隙和不等价原子电荷。 |

本文件声明28项，其中测试方法22项。

## test_primitive_mapping.py

保留历史文件名；只测一次原胞转换及必要几何，不建立映射，不要求拒绝源磁矩/约束。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `PrimitiveMappingTests`（类） | 由 unittest 框架构造；继承标准构造器 | PrimitiveMappingTests | 保留原测试文件和类名，验证一次 spglib 转换、有限几何、化学计量、显式失败策略及必要属性拒绝；不再验证位点映射。 |
| `PrimitiveMappingTests.config` | **changes:Any | dict[str,Any] | 在默认 pre 配置之上覆盖本测试指定设置。 |
| `PrimitiveMappingTests.assert_primitive` | source: ase.Atoms；result: PrimitiveResult | None | 断言源胞计数、非空输出、整倍数缩胞和有限坐标，并确认结果没有 mapping 属性。 |
| `PrimitiveMappingTests.test_find_primitive_supercell` | 无（省略实例参数） | None | 真实 NaCl 常规超胞缩为两原子原胞，同时满足基本原子计数和有限坐标检查。 |
| `PrimitiveMappingTests.test_rotated_nonorthogonal_structure_all_methods` | 无（省略实例参数） | None | 旋转的非正交 MgO 输入分别经过 find_primitive 与两种 standardize_cell 设置，均满足轻量结果检查。 |
| `PrimitiveMappingTests.test_primitive_failure_policy_is_explicit` | 无（省略实例参数） | None | spglib 失败默认拒绝，显式 fallback 才保留输入晶胞。 |
| `PrimitiveMappingTests.test_reidentified_canonical_axes_do_not_break_mapping` | 无（省略实例参数） | None | 保留历史测试名及真实 ZrTl2CrO6 离线样本；新版仅检查一次原胞转换结果，不执行第二次识别或位点映射。 |
| `PrimitiveMappingTests.test_conversion_never_reidentifies_symmetry` | 无（省略实例参数） | None | 真实 find_primitive 只调用一次；将 get_symmetry_dataset 替换为失败，证明前处理不再重复识别。 |
| `PrimitiveMappingTests.test_invalid_spglib_arrays_and_composition_rejected` | 无（省略实例参数） | None | 模拟非法原子序数、改变化学计量及 NaN 分数坐标，断言基本返回值检查仍拒绝异常。 |
| `PrimitiveMappingTests.test_short_range_overlap_and_periodic_self_images` | 无（省略实例参数） | None | 原子重叠及过短自身周期镜像仍拒绝，不再添加与统一归一化冲突的属性拒绝断言。 |
| `PrimitiveMappingTests.test_nonfinite_occupancy_is_not_full_occupancy` | 无（省略实例参数） | None | 模拟 ASE 返回 NaN、Inf 或半占位；三者均在满占位校验中拒绝，不能由浮点比较漏过。 |

本文件声明11项，其中测试方法8项。

## test_resources.py

完整赝势/轨道配对字典、官方根替换与私人路径、元素扩展和无资源文件 I/O。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `ResourceMappingTests`（类） | 由 unittest 框架构造；继承标准构造器 | ResourceMappingTests | 旧完整路径为独立基线，所有测试写入仅发生在新版测试沙箱。 |
| `ResourceMappingTests.setUp` | 无（省略实例参数） | None | 读取独立旧映射基线，创建新版目录内临时沙箱并注册 cleanup。 |
| `ResourceMappingTests.test_all_default_paths_exactly_match_previous_commit` | 无（省略实例参数） | None | 保留历史测试名；当前断言 83 个元素及 PP/轨道文件 basename 与独立基线一致，允许资源主目录有意迁移。 |
| `ResourceMappingTests.test_new_dojo_root_changes_only_67_official_elements` | 无（省略实例参数） | None | 修改 dojo_root 只改变 67 个官方元素的两种路径；La—Lu 与 Hf 共 16 个私人元素路径完全不变。 |
| `ResourceMappingTests.test_complete_absolute_replacement_needs_no_dojo_root` | 无（省略实例参数） | None | 替换整表后 dojo_root 缺省、None、空字符串均可使用；禁止调用官方拼接函数，不补回旧元素，缺元素时明确失败。 |
| `ResourceMappingTests.test_editing_only_literal_dictionary_can_replace_whole_library` | 无（省略实例参数） | None | AST 仅替换 `_resource_map` 的 return 字典，在内存中编译和执行完整模块，验证下游构造器不需改动即可消费新 U 资源。 |
| `ResourceMappingTests.test_new_element_can_be_added_without_83_element_limit` | 无（省略实例参数） | None | 添加原表外合法元素 Og 的配对条目后正常提供两种资源，不存在固定 83 元素上限。 |
| `ResourceMappingTests.test_malformed_top_level_maps_are_configuration_errors` | 无（省略实例参数） | None | None、空表和非 dict 的资源表都必须抛 ConfigError。 |
| `ResourceMappingTests.test_malformed_element_entries_are_configuration_errors` | 无（省略实例参数） | None | 非物理元素、非字符串元素键、非 dict 条目、缺 pp/basis 或多余字段均明确拒绝。 |
| `ResourceMappingTests.test_malformed_paths_are_configuration_errors` | 无（省略实例参数） | None | 两种资源路径均拒绝错误类型、空值、Windows/相对路径、空白、NUL、越界或混合分隔符。 |
| `ResourceMappingTests.test_default_official_entries_require_explicit_linux_root` | 无（省略实例参数） | None | 默认表仍引用官方资源，因此必须提供合法的 POSIX 主目录；不把无效根误当完整路径。 |
| `ResourceMappingTests.test_dojo_join_keeps_posix_root_and_rejects_escape` | 无（省略实例参数） | None | Windows 上也按 POSIX 拼接，拒绝空子路径、绝对子路径、点路径和反斜杠，不能偷偷替换主目录。 |
| `ResourceMappingTests.test_unused_element_changes_do_not_change_selected_mapping` | 无（省略实例参数） | None | 修改未使用 Hf 的资源不会改变 Mg/O 任务实际选取的映射。 |
| `ResourceMappingTests.test_config_type_and_removed_resource_settings_are_rejected` | 无（省略实例参数） | None | 配置顶层错误类型及含四个旧资源配置键的直接 API 调用均拒绝，防止静默忽略。 |
| `ResourceMappingTests.test_removed_settings_are_rejected_by_custom_config_loader` | 无（省略实例参数） | None | CLI 的自定义 CONFIG 也拒绝 lanthanide_root、hfo_root、pp_overrides、basis_overrides。 |
| `ResourceMappingTests.test_resource_construction_and_selection_perform_no_file_io` | 无（省略实例参数） | None | 同时禁止 open/stat/isfile/isdir/exists/access，构建与选取官方、La、Hf 资源仍正常。 |
| `ResourceMappingTests.test_literal_resource_table_has_no_duplicate_elements_or_fields` | 无（省略实例参数） | None | AST 静态查验默认 83 个元素字面键无重复，每个值明确包含一对 pp/basis，避免 Python 静默覆盖重复键。 |

本文件声明17项，其中测试方法15项。

## test_resume_records.py

独立pre快照、通用脱敏、锁、标准JSON及UUID声明，不依赖筛选运行。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `RecordTests`（类） | 由 unittest 框架构造；继承标准构造器 | RecordTests | 验证公共 FileLock、脱敏运行记录、原子 JSON 发布和显式 UUID 声明版本，不证明文件正文一致性。 |
| `RecordTests.setUp` | 无（省略实例参数） | None | 建立本测试独立临时沙箱并注册 cleanup；只写新版 .test-artifacts。 |
| `RecordTests.test_lock_excludes_and_releases_after_exception` | 无（省略实例参数） | None | 已有锁排他，异常退出释放自身锁，不阻止后续运行。 |
| `RecordTests.test_snapshots_are_unique_and_secrets_hidden` | 无（省略实例参数） | None | 重复运行生成不同目录，API Key 不以明文落盘。 |
| `RecordTests.test_legal_configuration_sets_are_serializable` | 无（省略实例参数） | None | 通用audit_labels集合规范化为排序JSON数组，无筛选配置依赖。 |
| `RecordTests.test_revision_is_explicit_uuid_not_file_content` | 无（省略实例参数） | None | 新版本 UUID 唯一且可识别，拒绝空值、旧长字符串、无效值与非规范大写；运行记录声明 schema 2 和 workflow 0.0.2，移除旧配置摘要字段。 |
| `RecordTests.test_json_refuses_non_finite_before_publication` | 无（省略实例参数） | None | JSON 含 NaN 编码失败时保留原正式文件。 |

本文件声明7项，其中测试方法5项。

## test_run_end_to_end.py

真实CLI子进程与本地调度/科学程序桩；新增从目录pre开始，经原relax/zstar worker到原post/clean的贯通验证。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `RunEndToEndTests`（类） | 由 unittest 框架构造；继承标准构造器 | RunEndToEndTests | 子进程走真实入口与科学读取；只在新项目专属临时目录写出测试文件。 |
| `RunEndToEndTests.setUp` | 无（省略实例参数） | None | 建立有效 STRU、配置模板和带空格的 Python 桩路径。 |
| `RunEndToEndTests.write` | name: str, text: str | str | 在本测试 files 目录写 UTF-8 桩脚本并返回绝对位置。 |
| `RunEndToEndTests.configure` | work: str | None | 写入当前机器工作根和测试调度器，其他值由真实默认模板提供。 |
| `RunEndToEndTests.make_task` | name: str | str | 建立单 Si 原胞与 cell-relax INPUT，不建立假完成记录。 |
| `RunEndToEndTests.invoke` | *args: str | subprocess.CompletedProcess | 运行真实 cli.py，捕获两个原始输出流供测试断言。 |
| `RunEndToEndTests.make_plan` | 无（省略实例参数） | str | 使用真实配置加载与 prepare 链生成计划，要求无模板或解析错误。 |
| `RunEndToEndTests.test_real_cli_sbatch_worker_continues_then_status` | 无（省略实例参数） | None | 从配置到提交 argv 到真实 worker：第一结构失败，第二完成，status 根因正确。 |
| `RunEndToEndTests.test_real_cli_worker_uses_relocated_plan_and_frozen_commands` | 无（省略实例参数） | None | 整棵工作树复制后旧根删除，worker 仍只用相对计划定位并保持原输入字节。 |
| `RunEndToEndTests.test_real_cli_worker_keeps_final_step_and_nonzero_exit_independent` | 无（省略实例参数） | None | 真实 worker 的旧 SCF 尾部不污染末步；非零退出预分类也不能伪造末步 SCF 根因。 |
| `RunEndToEndTests.test_invalid_group_count_preserves_revision_but_blocks_submit` | 无（省略实例参数） | None | 分组失败仍留版本计划，修正数量后继续准备而不是使目录失去关联。 |
| `RunEndToEndTests.test_local_directory_pre_relax_zstar_and_original_post` | 无（省略实例参数） | None | 真实CLI目录pre→原两阶段worker→post validate/collect/clean；LOCAL缺省字段、46列/四空列、总/离子介电20/18、白名单删除及非名单保留、源字节不变。 |
| `RunEndToEndTests.test_local_directory_pre_relax_zstar_and_original_post.command` | *args:str | subprocess.CompletedProcess | 本测试内真实CLI子进程调用；科学程序使用项目内Python桩，非零退出包含stdout/stderr诊断。 |

本文件声明13项，其中测试方法5项。

## test_run_status_config.py

运行配置、批内状态、活动引用、原生阶段显示、根因与只读查询。状态展示只关联已读记录，不复验输入或科学产物；每次查询的计划和批次缓存不能跨轮次混用。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `RunStatusConfigTests`（类） | 由 unittest 框架构造；继承标准构造器 | RunStatusConfigTests | 所有临时计划位于新项目测试目录，队列和科学计算使用明确替身。 |
| `RunStatusConfigTests.setUp` | 无（省略实例参数） | None | 建立本测试独享根，并自动清理本次测试产生的内容。 |
| `RunStatusConfigTests.make_plan` | name: str = '20261024-01'；rows: list[dict] \| None = None；stage: str = 'zstar' | str | 生成指定阶段的三任务同批计划和可选台账，返回计划地址。 |
| `RunStatusConfigTests.write_progress` | path: str；batch: int；value: dict | None | 让测试事件与该计划版本关联，保持状态测试聚焦于调度语义。 |
| `RunStatusConfigTests.job` | job_id: str = '123'；status: str = 'SUBMITTED' | dict[str, str] | 返回一条最小合法提交台账行。 |
| `RunStatusConfigTests.test_positional_run_commands_and_default` | 无（省略实例参数） | None | 功能采用位置参数，默认执行扫描计划提交；帮助不依赖科学库。 |
| `RunStatusConfigTests.test_default_config_and_overlay_path_anchor` | 无（省略实例参数） | None | 自定义配置未覆盖的默认脚本仍按默认模板定位。 |
| `RunStatusConfigTests.test_configuration_rejects_unsafe_and_nonfinite_values` | 无（省略实例参数） | None | 尽早拒绝拼错键、越界目录、非整数作业数和非有限数。 |
| `RunStatusConfigTests.test_missing_state_status_does_not_create_directories` | 无（省略实例参数） | None | 无计划时也保持纯只读，不创建 work 或 .hpc_runs。 |
| `RunStatusConfigTests.test_running_job_only_current_structure_is_running` | 无（省略实例参数） | None | 同批一个完成、一个计算、一个等待；父作业状态不能平铺到所有结构，禁止调用 zstar 科学终态复验。 |
| `RunStatusConfigTests.test_submission_failure_is_failure_and_not_waiting` | 无（省略实例参数） | None | sbatch 从未受理时按提交失败列出，不能误显示为批内等待。 |
| `RunStatusConfigTests.test_parent_oom_only_marks_interrupted_current_structure` | 无（省略实例参数） | None | 批次 OOM 不能改变先前完成或尚未执行结构的状态。 |
| `RunStatusConfigTests.test_unknown_scheduler_state_never_claims_running` | 无（省略实例参数） | None | 队列查询失败仍保留待核实状态，既不猜完成也不猜运行。 |
| `RunStatusConfigTests.test_all_nonterminal_statuses_are_protected_from_duplicate_submission` | 无（省略实例参数） | None | 显示层的排队/活动状态必须都被发现层识别，含重排队暂挂状态。 |
| `RunStatusConfigTests.test_status_read_only_snapshot` | 无（省略实例参数） | None | 已有计划查询前后文件名和内容完全一致。 |
| `RunStatusConfigTests.snapshot` | 无（省略实例参数） | dict[str,bytes] | 读取本测试全部文件字节并及时关闭句柄，返回相对名到内容的字典。 |
| `RunStatusConfigTests.test_recorded_imaginary_stop_needs_input_binding_not_native_copy` | 无（省略实例参数） | None | 保留历史测试名；static 虚频只在 Slurm stdout 留痕时，版本字段匹配的已记录拒绝仍保持独立终态，禁止回读当前输入关联。 |
| `RunStatusConfigTests.test_recorded_terminals_do_not_read_scientific_files` | 无（省略实例参数） | None | relax/zstar 完成及 zstar 物理拒绝在任务目录不存在时仍按匹配记录展示；禁止调用两类 inspect、revision_matches 和父弛豫索引，查询前后文件字节不变。 |
| `RunStatusConfigTests.test_finished_revision_and_legacy_rules_remain_record_checks` | 无（省略实例参数） | None | 逐项变更五个版本字段或使用旧协议 progress，成功记录均不能被当前计划采纳；旧协议计划成功继续显示待核实。 |
| `RunStatusConfigTests.test_status_reuses_records_without_crossing_plan_or_batch` | 无（省略实例参数） | None | 默认全部历史和显式新计划两种查询均复用活动引用；各计划/台账、各计划批次进度只读取一次，finished 索引只遍历一次；同名结构、同编号批次不能混用终态。 |
| `RunStatusConfigTests.test_status_reuses_records_without_crossing_plan_or_batch.SinglePassFinished`（局部类） | iterable: list[dict]；继承 list 构造器 | SinglePassFinished | 仅统计状态汇总对已完成列表的遍历，不计底层读取器的格式校验。 |
| `RunStatusConfigTests.test_status_reuses_records_without_crossing_plan_or_batch.SinglePassFinished.__iter__` | 无（省略实例参数） | Iterator[dict] | 记录遍历次数；同一批列表第二次被遍历即抛 AssertionError。 |
| `RunStatusConfigTests.test_status_reuses_records_without_crossing_plan_or_batch.counted_progress` | path: str；batch: int | dict | 调用真实只读进度解析后，以 SinglePassFinished 包装 finished，供状态层遍历次数断言。 |
| `RunStatusConfigTests.test_stale_native_running_file_not_displayed` | 无（省略实例参数） | None | pre 外层运行时旧 disp 不得冒充当前正在执行的有限差分阶段。 |
| `RunStatusConfigTests.test_active_reference_tracks_original_batch_and_native_displacement` | 无（省略实例参数） | None | 新轮占用条目仍沿相对引用展示原批次的真实当前结构和 disp。 |
| `RunStatusConfigTests.test_failure_evidence_and_supplementary_print_current_absolute_paths` | 无（省略实例参数） | None | 根因保持一个，后续原因显示为补充，三种相对证据均展开为当前地址。 |

本文件声明26项，其中测试方法17项。

## test_submission.py

分组、冻结版本、sbatch 回执、防重复提交、worker 隔离、内核锁释放和续算边界。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `SubmissionTests`（类） | 由 unittest 框架构造；继承标准构造器 | SubmissionTests | 全部测试文件建立在新项目 .test-artifacts 内，测试调度接口使用 mock。 |
| `SubmissionTests.setUp` | 无（省略实例参数） | None | 建立单测专属工作根和最小两结构计划。 |
| `SubmissionTests.row` | status: str = 'SUBMITTED'；job_id: str = '123' | dict[str, str] | 返回固定批次的最小提交台账行。 |
| `SubmissionTests.test_parallel_count_balancing_and_bounds` | 无（省略实例参数） | None | 0/1/中间并行数精确分组，超范围和布尔值不静默改写。 |
| `SubmissionTests.test_plan_relocates_without_old_absolute_root` | 无（省略实例参数） | None | 整工作目录复制到另一位置后，原记录仍附着新的任务根。 |
| `SubmissionTests.test_plan_rejects_duplicate_group_members` | 无（省略实例参数） | None | 分组重复结构不能导致同一输入被执行两次。 |
| `SubmissionTests.test_sbatch_argv_preserves_paths_and_parses_cluster_ack` | 无（省略实例参数） | None | argv 不经 shell；sbatch 的 jobid;cluster 回执正确展开日志位置。 |
| `SubmissionTests.test_unknown_ack_never_blindly_resubmits` | 无（省略实例参数） | None | 已提交但回执未知的批次只核对唯一名称，不再次调用 sbatch。 |
| `SubmissionTests.test_ack_unknown_can_reconcile_existing_job` | 无（省略实例参数） | None | 唯一作业名可确认受理时补全 job ID，仍不重复提交。 |
| `SubmissionTests.test_already_submitted_plan_does_not_submit_again` | 无（省略实例参数） | None | 同一计划重复 submit 不产生重复作业。 |
| `SubmissionTests.test_active_job_only_protects_unfinished_members` | 无（省略实例参数） | None | 批内已结束结构可单独续算，尚未执行的成员仍受已有作业保护。 |
| `SubmissionTests.test_scheduler_unknown_is_not_terminal` | 无（省略实例参数） | None | Slurm 暂时不可查询时保持 UNKNOWN，防误重提。 |
| `SubmissionTests.test_scan_does_not_call_prepare_or_write` | 无（省略实例参数） | None | scan 使用只读分支，既不准备输入也不发布计划。 |
| `SubmissionTests.test_worker_continues_next_structure_and_no_reprepare` | 无（省略实例参数） | None | 第一结构技术失败仍执行第二个，且 worker 不再次 prepare。 |
| `SubmissionTests.test_worker_continues_next_structure_and_no_reprepare.execute` | task_dir:str, config:dict, callback:Callable | dict | 模拟两个任务的不同终态，不启动真实计算。 |
| `SubmissionTests.test_worker_rejects_queue_time_input_mutation` | 无（省略实例参数） | None | 排队后输入版本变化时拒绝执行，但仍逐任务写出明确输入失败。 |
| `SubmissionTests.test_worker_duplicate_lock_is_rejected` | 无（省略实例参数） | None | 同批两个 worker 不允许同时遍历结构。 |
| `SubmissionTests.test_runtime_lock_releases_after_process_kill` | 无（省略实例参数） | None | 真实子进程被强制终止后，内核锁自动释放，无须删除残留锁文件。 |
| `SubmissionTests.test_revision_mismatch_cannot_be_rebound_by_prepare` | 无（省略实例参数） | None | 旧声明输入版本变更不能重新准备后把历史成功文件绑定给新输入。 |
| `SubmissionTests.test_command_template_change_requires_new_stage_directory` | 无（省略实例参数） | None | 命令模板可含科学参数，整条模板改变不能悄悄复用同目录旧成功。 |
| `SubmissionTests.test_planned_but_unfinished_history_cannot_attest_old_success` | 无（省略实例参数） | None | 只有计划而没有终态关联时，即使旧产物完整也继续实际执行。 |
| `SubmissionTests.test_physical_rejection_survives_without_native_static_stage` | 无（省略实例参数） | None | 明显虚频拒绝只有简短 progress 记录时，仍按绑定输入保留终止状态。 |
| `SubmissionTests.test_legacy_plan_cannot_execute_or_submit` | 无（省略实例参数） | None | 旧协议允许历史读取，但禁止执行或提交旧冻结输入。 |
| `SubmissionTests.test_forged_progress_revision_cannot_skip_worker` | 无（省略实例参数） | None | 另一声明版本的结束事件不能让当前 worker 跳过结构。 |
| `SubmissionTests.test_staging_with_complete_manifest_is_not_a_formal_task` | 无（省略实例参数） | None | 遗留 staging 即使含 STRU 和成功 manifest 也从正式任务列表和计算计划中排除。 |
| `SubmissionTests.test_zstar_scan_builds_one_index_for_the_whole_batch` | 无（省略实例参数） | None | 多结构 zstar 扫描只构建一次 relax 历史索引，并经本轮内存配置传给所有任务适配器。 |
| `SubmissionTests.test_zstar_scan_builds_one_index_for_the_whole_batch.inspect` | directory: str；config: dict；revision_entry: dict \| None = None | dict[str, str] | 只收集适配器收到的 _relax_revision_index 引用并返回 pending，用于断言整批共享同一索引；不读写任务文件。 |
| `SubmissionTests.test_unrecorded_legacy_structure_is_input_failed_on_scan` | 无（省略实例参数） | None | 只含 STRU 的旧散装目录可在扫描结果中显示，但必须是 input_failed，不能直接准备或复用。 |

本文件声明28项，其中测试方法23项。

## test_task_writing.py

真实ASE/spglib目录前处理；四产物、常驻writer、恢复/迁移/不覆盖，加真实CIF/STRU/POSCAR归一化、坏文件隔离和长名称边界。

| 声明 | 参数类型与默认值 | 返回类型 | 功能与断言目标 |
|---|---|---|---|
| `file_bytes` | path: str | bytes | 测试专用：直接读取隔离小文件字节以断言只读或未覆盖，不用于生产版本检测。 |
| `TaskWritingTests`（类） | 由 unittest 框架构造；继承标准构造器 | TaskWritingTests | 全部可变内容位于新项目，测试不会写原 workflow 或真实集群。 |
| `TaskWritingTests.setUp` | 无（省略实例参数） | None | 建立独立结构目录和真实CIF，配置只使用本地来源，不生成输入CSV。 |
| `TaskWritingTests.execute` | name:str="run", config:dict\|None=None | dict[str,Any] | 调用前处理公共组件，输出限定到本测试沙箱。 |
| `TaskWritingTests.test_builtin_template_is_exact_copy` | 无（省略实例参数） | None | 内置 INPUT 字节与用户 INPUT-template 完全一致。 |
| `TaskWritingTests.test_full_write_reuse_and_unchanged_main_cwd` | 无（省略实例参数） | None | 真实四产物和LOCAL manifest，旧编号/nominal/source_metadata键缺失；第二轮复用不启动writer，CWD稳定。 |
| `TaskWritingTests.test_moved_work_and_source_can_reuse` | 无（省略实例参数） | None | 整体迁移源和工作根后，按稳定身份与明确设置复用，软件审计版本不作为内容证明。 |
| `TaskWritingTests.test_fcc_primitive_has_four_products_without_mapping` | 无（省略实例参数） | None | 真实 NaCl 常规胞缩至两原子原胞，正式目录没有 atom_mapping.json，仅发布新版四产物。 |
| `TaskWritingTests.test_external_cif_edit_does_not_claim_content_verification` | 无（省略实例参数） | None | 同身份、同显式输入描述下修改源 CIF 坐标仍复用旧任务，旧 STRU 字节与成功索引保持不变，明确不具备外部正文变更探测。 |
| `TaskWritingTests.test_complete_interrupted_staging_is_recovered` | 无（省略实例参数） | None | 把本测试成功任务移入完整 staging，重跑应验证并恢复为正式任务，计入 tasks_recovered。 |
| `TaskWritingTests.test_incomplete_staging_is_not_counted_as_published` | 无（省略实例参数） | None | 残缺 staging 留待诊断并列入 unpublished_staging，不进入正式任务数或成功索引。 |
| `TaskWritingTests.test_custom_input_failure_never_falls_back` | 无（省略实例参数） | None | 明确指定 INPUT 缺失时报错，不回退默认参数。 |
| `TaskWritingTests.test_resource_mapping_never_checks_resource_files` | 无（省略实例参数） | None | patch 文件探测函数为失败，资源映射仍可构建和校验。 |
| `TaskWritingTests.test_changed_resource_paths_conflict_without_overwriting_stru` | 无（省略实例参数） | None | 真实发布后修改 O 的赝势/轨道路径，结构化输入描述比较导致冲突；旧目录全部文件字节保持不变。 |
| `TaskWritingTests.test_mixed_official_and_private_resources_reach_real_stru` | 无（省略实例参数） | None | 真实 LaHfO2 写出中，O 使用改后的官方根，La/Hf 保留私人绝对路径，PP 与轨道六条路径均进入 STRU。 |
| `TaskWritingTests.test_modified_manifest_cannot_relabel_old_results` | 无（省略实例参数） | None | 修改 manifest 的源材料身份而保留旧任务目录及输入描述时，inspect_existing 必须报告身份冲突，禁止重新标记旧结果。 |
| `TaskWritingTests.test_batch_reuses_one_writer_and_one_staging_index` | 无（省略实例参数） | None | 两份独立材料在同一轮只启动一个 writer，scan_staging 仅调用一次，两个正式任务都成功发布。 |
| `TaskWritingTests.test_writer_recovers_after_process_exit_and_keeps_ordinary_errors_local` | 无（省略实例参数） | None | 真实常驻 worker 普通写出失败后沿用同一 PID；主动终止进程后当前请求明确失败并释放会话，下一请求重新启动且成功。 |
| `TaskWritingTests.test_explicit_input_settings_change_requires_new_work_root` | 无（省略实例参数） | None | 改变显式 INPUT 字节时既有工作根拒绝复用，换新工作根可正常创建任务。 |
| `TaskWritingTests.test_legacy_manifest_is_not_reused` | 无（省略实例参数） | None | 将 manifest 的 workflow_version 改为旧值后，inspect_existing 拒绝旧记录而不修补或覆盖。 |
| `TaskWritingTests.test_bad_enum_is_configuration_error` | 无（省略实例参数） | None | 错误 primitive 枚举类型给 ConfigError。 |
| `TaskWritingTests.test_real_three_formats_prepare_from_geometry_without_touching_sources` | 无（省略实例参数） | None | 真实CIF、含固定/非零磁矩STRU、Selective Dynamics POSCAR均准备成功；输出实际全自由/零磁矩，INPUT原字节及所有源文件字节不变。 |
| `TaskWritingTests.test_bad_file_is_recorded_while_direct_structures_continue` | 无（省略实例参数） | None | 混入坏CSV计单文件失败但CIF继续；子目录内结构不参与；失败表无旧编号/行号。 |
| `TaskWritingTests.test_empty_directory_is_not_successful_preparation` | 无（省略实例参数） | None | 空目录不报成功、任务数为零且不启动writer。 |
| `TaskWritingTests.test_long_encoded_identity_fails_locally_and_keeps_other_file` | 无（省略实例参数） | None | 安全编码超限的有效中文CIF只使本文件失败，其他结构照常发布。 |

本文件声明25项，其中测试方法21项。

## resource_mapping_baseline.json

只读资源基线包含dojo_root及pp/basis两张元素路径表。默认资源回归核对83个元素及文件basename；67个官方条目的根替换与16个私人条目分别断言，不把basename一致称为所有完整路径逐字一致。读取基线不访问实际资源文件。

## 验证边界

本地桩端到端验证接口和失败传播，不证明真实ABACUS/PYATB/zstar数值结果或远程Slurm环境已通过。POSIX删除分支使用隔离目录fd模型，不构成跨节点NFS锁和删除语义实测。UUID/stat及锁的边界沿原实现。

测试通过数量必须以当次执行结果为准，下列为源码静态声明数量。新增/删除/改名测试应同步维护本表，不继承旧项目的通过记录。

当前源码合计15个Python测试文件、374项声明、283个测试方法。

