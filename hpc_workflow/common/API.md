# common 公共基础模块接口

本目录只提供路径语法、配置加载、原子文件写出与可预期错误。它不导入 ASE/pymatgen/mp-api，不执行结构转换或计算。`__init__.py` 不声明类和函数；其他声明全部列在下文。

## paths.py：两种语法、三类路径

本机文件路径按根字符串识别 Windows/POSIX 语法，再由 `ntpath` 或 `posixpath` 进行字符串操作。`ensure_host_path` 只在真正 I/O 前确认路径语法与运行主机匹配。写入 STRU 的 Linux 资源路径由 resources 模块单独处理，不交给本机路径转换。

运行记录中的内部引用使用 `/` 分隔、无盘符的相对字符串；重新加载时以当前根目录附着。下列函数不进行文件存在性探测，不解析符号链接，不调用远程文件系统。`normpath` 只折叠字符串中的点段和冗余分隔符，不改变已识别的语法。

| 声明 | 参数及类型 | 返回类型 | 行为、异常与调用者 |
|---|---|---|---|
| path_style | path:str | str | 返回 windows/posix；要求明确绝对根；拒绝空值、NUL、盘符相对路径、缺 share 的 UNC、单反斜杠根、混合 POSIX 反斜杠和有歧义双正斜杠；抛 ConfigError |
| _module | path:str | ntpath/posixpath 模块对象 | 依据 path_style 选择纯字符串工具，不依据当前操作系统转换 |
| join_local | base:str, *parts:str | str | 按 base 语法拼接相对部分；后续绝对路径不能替换根；POSIX 部分不能含反斜杠；错误抛 ConfigError |
| resolve_local | path:str, base:str | str | 明确绝对 path 保留其语法；相对 path 以 base 拼接；不读盘；配置加载器使用 |
| path_dirname | path:str | str | 按路径本身语法取父目录 |
| path_basename | path:str | str | 按路径本身语法取最后名称 |
| to_relative | path:str, root:str | str | 输出 root 内 POSIX 相对引用；拒绝跨语法、跨盘与 `..` 越界；错误抛 ConfigError |
| from_relative | root:str, relative:str | str | 还原本机语法路径；拒绝绝对、盘符、反斜杠、空段、点段和向上路径；`.` 表示根自身 |
| ensure_host_path | path:str | str | Windows 主机只接受 Windows 本机路径，POSIX 主机只接受 POSIX 本机路径；原字符串返回；外来语法报 ConfigError，不替用户改写 |
| project_root | 无 | str | 根据本模块位置定位新版项目根，独立于命令启动目录；仅程序自身相对 __file__ 必要时用本机 abspath |

调用例：Windows 中 `join_local(r'D:\work', 'local__x', 'STRU')` 仍为反斜杠路径；`join_local('/public/work','local__x','STRU')` 仍为 POSIX 路径。纯拼接允许双平台字符串测试，实际打开文件前再 ensure_host_path。

## config_loader.py：默认模板与用户覆盖

默认根模板为 pre_config.py，声明 CONFIG 字典。默认模块使用正常 import；自定义模块使用明确指定的文件源码编译，不创建配置旁的 pycache。自定义配置是用户可信 Python 源码，允许正常表达式与导入；加载器不会篡改它或默认配置。

| 声明 | 参数及类型 | 返回类型 | 行为、异常、副作用 |
|---|---|---|---|
| _read_custom | path:str | dict[str,Any] | 确认本机路径，读取字节并执行独立模块；要求 CONFIG 为 dict。SyntaxError 包装为带行号 ConfigError，其他加载错误和 SystemExit 同样包装；不创建输出目录，不写 pycache |
| load_config | kind:str, custom_path:str\|None=None | dict[str,Any] | kind仅pre。深复制默认、拒绝未知键、叠加自定义。配置位置按CWD；work_root按声明它的默认/覆盖文件目录定位，structures_root和非空input_file必须原本为绝对本机路径。加入_config_dir/_config_path，资源文本不按本机解析 |
| public_config | config:dict[str,Any] | dict[str,Any] | 生成持久化快照，去掉 `_` 内部键并遮蔽 api_key；不会修改原字典；由运行记录模块使用 |
| _json_value | value:Any | JSON 标准值 | 递归规范化快照：tuple→list，set/frozenset→稳定排序 list；字典键必须为字符串，未知对象及 NaN/Infinity 抛 ConfigError；无 I/O |
| business_config | config:dict[str,Any] | dict[str,Any] | 深复制公开业务字段，去掉加载器内部键；保留执行所需 api_key，只供内存调用，不直接当脱敏日志 |

只有work_root允许自动相对定位；默认值按默认模板、自定义覆盖值按覆盖文件目录解释。structures_root必须原本为绝对本机路径，不通过resolve掩盖相对输入错误。`input_file` 非空时必须已经是本机可读语法的绝对路径，禁止把用户错误路径改解释为默认 INPUT。具体数值、枚举和字段组合由相应业务验证器进一步检查。public_config 使用 _json_value 把合法 Python 配置容器变为可持久化标准值，业务配置仍保留执行所需类型。

## atomic.py：先完成编码，再发布

| 声明 | 参数及类型 | 返回类型 | 行为、异常、副作用 |
|---|---|---|---|
| atomic_write_bytes | path:str, data:bytes | None | 检查本机路径、创建目标父目录；同目录 UUID 临时文件以 xb 独占写入、flush/fsync 后 os.replace 发布；finally 仅删除自己命名的残留临时文件。底层 OSError 原样抛出 |
| atomic_write_text | path:str, text:str | None | UTF-8 编码后委托 bytes 写出；换行由调用方给定 |
| atomic_write_json | path:str, payload:JSON 可序列化对象 | None | 稳定键排序、保留中文、缩进、末尾换行；allow_nan=False。编码失败先报错，不修改现有正式文件 |
| atomic_write_csv | path:str, fieldnames:Iterable[str], rows:Iterable[Mapping[str,Any]] | None | 内存 StringIO 完成 CSV 引号、表头和行编码，extrasaction=raise；UTF-8 BOM 原子发布；不一致列先 ValueError |
| read_json | path:str | Any | 本机只读 UTF-8/BOM JSON；不存在、权限、编码和 JSONDecodeError 向上传递 |
| FileLock | path:str | FileLock 实例 | 构造只验证路径，创建 token，owned=False；不立即建立锁 |
| FileLock.__enter__ | 无 | FileLock | 创建父目录，O_CREAT\|O_EXCL 独占锁文件；写 token/pid/host/时间；已存在抛 ConflictError，不按年龄抢锁 |
| FileLock.__exit__ | exc_type:type\|None, exc_value:BaseException\|None, traceback:TracebackType\|None | bool=False | 只有锁文件 token 仍属于本实例时才释放；缺文件容忍；不吞掉 with 块异常 |

文件原子发布保证读者看到完整旧版或完整新版单文件。跨文件发布边界由任务 staging、必要文件存在性、运行终态共同声明；不再比较文件正文或计算摘要；不能只凭某一个 CSV 已出现就认定整轮完成。

## errors.py：异常分类

| 类 | 基类 | 构造参数 | 含义与命令行处理 |
|---|---|---|---|
| WorkflowError | RuntimeError | *args:Any（继承异常构造器） | 可向用户报告的工作流错误，CLI 返回 1 |
| ConfigError | WorkflowError, ValueError | *args:Any | 类型、范围、路径语法或配置组合错误；CLI 优先捕获，返回 2 |
| ConflictError | WorkflowError | *args:Any | 锁、既有任务或输出冲突；保留旧产物，CLI 返回 1 |
| IntegrityError | WorkflowError | *args:Any | 输入不可解析、几何不可用、记录协议或版本关联不成立，CLI 返回 1 |

这些类没有另外声明方法；继承标准异常的消息与 traceback 行为。预期错误不会伪装成成功；CLI 未捕获的程序错误保留 traceback 供开发定位。

四份CONFIG的全部默认值、用途、单位、路径语义和限制见[根API.md](../../API.md)。common只加载pre；submission/postprocess保留各自独立loader，并继续复用_read_custom等辅助接口。

