# hpc_workflow 分层与调用关系

内部包保留 __version__="0.0.2"，无启动副作用。版本号是未修改计算/提交/post模块识别当前记录的协议前提；产品目录名为codex-project_calc-k_workflow，不复用原项目工作根。唯一用户入口cli.py，可导入入口main.py。

| 层 | 输入 | 输出与责任 |
|---|---|---|
| common | 配置、字符串路径、小型记录 | pre配置加载、当前主机路径、原子发布、FileLock和异常；无内容摘要 |
| records | 明确配置与运行终态 | schema2快照、UUID4、分发版本审计；无handoff |
| inputs | 本地直接文件身份 | InputRecord及ELEMENTS；无CSV适配器、编号和筛选客户端 |
| preprocess | 单层结构目录、INPUT、资源文本 | CIF→ABACUS→自动识别、必要检查、一次spglib、常驻writer、四产物及LOCAL记录 |
| resources | 官方根/显式元素配对 | 原AbacusResources.pp/basis，不访问资源本体 |
| calculations | 正式pre任务与冻结科学设置 | 原relax判据、正式1e-3标准化、容差诊断和zstar四命令 |
| submission | 阶段任务、UUID、Slurm配置 | 原plan/jobs/progress、队列防重提、worker、续算及只读status |
| postprocess | 正式任务、可信终态与物性 | 原46列整批CSV和有限清理，LOCAL旧来源四列为空 |

调用链：cli→main→preprocess.pipeline，或独立submission/postprocess入口。无screening和原pipeline；不自动等待或串联两阶段提交。用户确认relax完成后单独启动zstar。

pre来源固定LOCAL，身份由namespace和完整文件名决定；源结构化学信息直接生成。manifest不写样本号、priority、nominal、Ehull或空source_metadata。普通源文件失败保留pre失败行；未正式发布的文件不是下游任务。

跨模块必须保留inputs.models.ELEMENTS、preprocess.validation.validate_geometry、preprocess.writer.inspect_existing、common.config_loader共享辅助接口。任务仍平铺work直接子目录，并保留STRU/INPUT/primitive.cif/task_manifest.json；post明确依赖四件套契约。

阶段plan/progress冻结来源pre UUID、阶段UUID、相对计算目录和科学设置，resume延用输入UUID。UUID/文件存在性不证明正文未变；无内容摘要、原子映射、写后回读或自动多层revision树。资源路径始终POSIX文本，实际I/O按执行主机。

各模块API列实际声明、参数、返回、异常与副作用。四份CONFIG全部用户参数的默认值、用途和限制详见[根API.md](../API.md)。
