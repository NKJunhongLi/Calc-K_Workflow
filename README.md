# 本地结构 K 值计算工作流

本项目从 HPC 工作流 v0.0.2 拆分计算部分：结构父目录 → pre → cell-relax → relaxed 结构标准化原胞 → zstar → 整批 CSV → 指定中间文件清理。内部包名仍为 hpc_workflow，记录协议仍为 schema_version=2、workflow_version=0.0.2。新项目使用独立工作根，不接管旧项目计算现场。

calculations、submission、postprocess、resources 的 Python 源码原样保留。变动集中在本地目录输入、pre、入口与默认工作根；不提供 screening、MP/MJM CSV 输入、handoff 或原 screening→pre pipeline。

## 环境与部署

Python 需要满足 requirements.txt 中锁定依赖的要求。必须使用提供 ase.io.abacus 的 ASE-ABACUS 分支，不能用普通 ASE 替换。ABACUS、PYATB、zstar、MPI、Slurm 由用户现有环境提供；zstar 核心独立，通过命令行调用。

保留原 Slurm 脚本、launcher、赝势/轨道映射和外部 INPUT-scf。部署路径仍对应既有集群环境；不复制或检查资源文件本体。用户已确认 INPUT 为 nspin 1，模板原样使用。本机输入与工作路径须符合执行主机；写入 STRU 的资源路径始终是 Linux 文本，不按 Windows 语法改写。

## 最小使用方法

把 CIF、ABACUS STRU、POSCAR 单结构文件放在同一个干净父目录，填写 pre_config.py 的 structures_root 绝对路径。四份配置的 work_root 默认均为本项目/work；更换工作根时，后续配置也须指向同一批次。

也可用独立覆盖文件：

```python
CONFIG = {"structures_root": "/cluster/structures"}
```

Windows 可填 r"D:\data\structures"。structures_root 与非空 input_file 必须原本就是绝对路径。其他键、类型、默认值、路径语义和限制完整见 [API.md](API.md)。Python 配置是可信可执行代码，未知键报错。

```bash
python3 cli.py help
python3 cli.py help pre
python3 cli.py validate pre
python3 cli.py pre
python3 cli.py pre --config /cluster/configs/pre_local.py
```

validate pre 只查配置、输入目录、可选 INPUT、资源映射文本，不逐文件转换，不建业务目录。帮助不导入科学库。

## 扫描与前处理

只枚举父目录直接包含的普通文件，不递归、不按文件名过滤；子目录和链接不作为结构输入。确定排序后依次尝试 format="cif"、format="abacus"、ASE 自动识别。前一解析器失败才尝试下一项；三次都失败时记录三次异常，继续后续文件。成功解析后的占位、几何、资源错误不通过更换解析器绕过。

本版只承诺 CIF、STRU、POSCAR；自动识别并非三格式白名单，可能读取其他格式。用户应整理目录；混入 log、CSV 等普通文件也会被尝试读取并可能计入失败。

处理链为读取 → 必要物理检查 → 一次 spglib 原胞转换 → 直接写出 → 发布完成记录。保留非空、真实元素、有限晶格/坐标、非退化体积、完整占位、短程原子/周期镜像重叠、资源配对，以及转换前后计量和整倍数缩胞检查。不读输入 CSV，不检查交付编号/原子数，不执行氢黑名单、带隙、金属或三维网络筛选。

源结构只作为元素、晶格和坐标来源。固定政策是不继承源约束/移动标志/磁矩等附加属性；输出有效移动标志全部自由、初始磁矩全零，采用现有 nspin 1 INPUT。保留 Atoms 重建与 ASE 写出，无属性映射。裸 1 1 1 与 m 1 1 1、块默认零磁矩与逐位点零覆盖按等价含义处理，不强制字面排版。

spglib 返回 None 时按 primitive_failure_policy 拒绝或显式沿用输入胞。不重复识别输出群，不建立原子映射，不回读刚写出的 STRU/CIF。

## 目录、身份与恢复

```text
work/
  local__<namespace>__<encoded_filename>/
    STRU
    INPUT
    primitive.cif
    task_manifest.json
    cell-relax/                 # 后续独立准备
    zstar/                      # 后续独立准备
  runs/<run_id>/...
  preprocess_manifest.csv
  preprocess_failed.csv
  preprocess_summary.json
  .hpc_runs/<stage>/<run_id>/...
```

LOCAL、命名空间和含扩展名源文件名决定稳定身份，不按化学式或遍历序号编号。manifest 保存实际化学信息、原子数、UUID4、明确设置和资源映射；不加样本号、priority、nominal k、Ehull、CSV 行号或空 source_metadata。pre CSV 是程序输出台账，用户无需提供 CSV。

一轮复用一个常驻隔离 writer，请求/结果走内存管道；只切换子进程 staging CWD，父进程 CWD 不变。最后写完成记录，再 rename 发布正式任务。ase_sort.dat 不读取、不校验、不发布，只删除自己 staging 中的副产物。

保留一次 staging 索引、完整 staging 恢复和正式任务不覆盖。普通单文件失败可继续；全局配置、索引或写盘错误明确失败。没有发布正式任务的源文件只进入 pre 失败表，不在原 post 的最终 CSV 占一行；1000 个源文件不必然产生 1000 行结果。

复用直接比较 INPUT、转换设置和实际资源映射，要求当前协议完成记录及三份普通非空产物。UUID 不是内容校验；同名源文件手改不保证自动产生新版本，更换输入批次应使用新工作根或命名空间。没有内容摘要、atom_mapping.json、逐结构 Python 启动或文件形式 writer 请求。

## 计算提交、续算与状态

```bash
python3 cli.py run relax scan
python3 cli.py run relax plan
python3 cli.py run relax
python3 cli.py run relax status
python3 cli.py run relax resume
# 确认弛豫完成后独立启动 zstar
python3 cli.py run zstar scan
python3 cli.py run zstar plan
python3 cli.py run zstar
python3 cli.py run zstar status
python3 cli.py run zstar resume
```

plan 会准备阶段输入；run 规划后提交，返回不代表计算完成。parallel_jobs=0 每结构一作业，1 全串行，其余正整数精确均分非空批次，超过有效待算数报错。submit 必须提供 --plan；worker 为 Slurm 内部入口，只消费冻结计划，不重载配置。

阶段目录、UUID、冻结命令/QC、Slurm 防重提、逐结构失败隔离及内核锁保持旧实现。命令模板或科学设置改变不能沿用原阶段目录，可用新工作根或不同单层阶段子目录，不自动生成多层版本目录。

status 只汇总 plan/jobs/progress、实时 Slurm 和运行中 zstar 原生步骤。终态来自已有版本关联，不重读结构/日志/物性重新判断，不改 progress。记录成功不证明当前产物仍存在；scan、worker 和下一阶段准备保留各自检查。

cell-relax 仍面向 ABACUS LTS 3.10.1，从日志末尾按 256 KiB 块寻找两类完整 STEP 标题，取最后离子步。末步 SCF 失败不能被后续成功覆盖；离子成功不能覆盖晶格失败，整体最终成功可解决此前组件失败。正常结束、JSON、唯一可读末态等门槛保留；最后允许步恰好收敛仍可成功。

新 zstar 按 relaxed 自身群以正式 1e-3 Å 标准化原胞，五档容差扫描只诊断，独立晶格应变 QC 保留，真实降对称可继续。已有 zstar 续算不重新标准化。pre→run→post→static 四命令非零不短路，明确 SCF 等根因不被后续缺文件掩盖。

## 整批结果与清理

```bash
python3 cli.py post validate
python3 cli.py post
python3 cli.py post clean
```

一个 work_root 是一批；全部正式任务有可信终态后才发布一张 CSV，已有目标拒绝覆盖。原 post 源码与 46 列顺序不变；LOCAL 的 dft_priority、global_sample_id、nominal_k_available、nominal_k_value 四列留空，输入侧不为它们增加占位键。

总介电来自静态响应，电子项来自 BORN，离子项为两者之差，平均为 trace/3。保留符号、非对角项、不等价原子与原生方向，不转置或对称化；总介电迹平均降序、空值置底。失败保留一行与有效部分物性。revision_linked 只表示流程关联，不代表内容校验或 DFT 成功。

默认发布 CSV 后仅清理符合资格任务的三个精确 CSR 名称和 *DENSITY.restart。资格为物性读取通过的成功，或明确带隙/明显虚频物理终止；技术失败、未知、输入关联错误保留。独立 clean 复核原 46 列。控制器/worker 锁、历史未结束保护、链接拒绝、路径边界、删除前身份检查保持。清理失败不回滚 CSV，可重试；post validate 不写 CSV、不建锁、不删除。

## 开发与验证

每模块 API.md 列出声明、参数、返回、异常、副作用和调用关系；根 API.md 完整说明四份 CONFIG。新增代码块用中文注释。保留模块文档仍可描述 MP/MJM 历史兼容分支，但本项目不提供这些输入入口。

```bash
python3 -B -m unittest discover -s tests -v
```

测试临时目录、缓存和生成文件应限制在本项目内。科学程序与 Slurm 使用本地替身，真实结构读取与转换使用现有 ASE/spglib；测试声明见 tests/API.md。本地回归不构成真实远程集群或跨节点共享文件系统验证。

退出码：0 成功/只读校验通过；1 运行或部分失败；2 配置/命令错误；130 中断。post 成功表示整批提取与应执行清理完成，不表示所有 DFT 成功。
