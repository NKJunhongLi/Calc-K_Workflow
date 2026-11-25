# ABACUS 资源映射 API

本模块只处理“元素 → Linux 路径文本”。默认保留原有 83 个元素的赝势/轨道文件名与实际路径；不检查文件存在、不读取资源、不计算资源文件哈希、不执行 SSH。任务输入描述记录实际使用的资源映射文本。

## abacus.py 的全部声明

### AbacusResources（frozen dataclass）

构造参数为 `pp:dict[str,str]`、`basis:dict[str,str]`，两张字典分别保存元素符号到 Linux 绝对文件路径。此接口与下游保持不变；字段完整只表示映射存在，不代表远端文件已验证。冻结 dataclass 限制字段重新绑定，不把内部 dict 声称为深度不可变对象。

| 声明 | 参数类型 | 返回类型 | 功能、错误与副作用 |
|---|---|---|---|
| `AbacusResources.validate_elements(elements)` | elements:tuple[str,...]，实际结构元素 | None | 核对实际元素同时具有 pp/basis 以及合法 POSIX 路径；缺项或无效文本抛 ConfigError；无 I/O |
| `AbacusResources.mapping_for(elements)` | elements:tuple[str,...] | dict[str,dict[str,str]]，顶层键为 pp/basis | 先校验实际元素，再提取排序后的最小资源子集；无 I/O |
| `validate_resource_path(path,label)` | path:str；label:str 诊断标签 | str，合法的原路径文本 | 拒绝非 Linux 绝对路径、反斜杠、NUL、空白和 `.`/`..` 分段；ConfigError；无 I/O |
| `_dojo_path(dojo_root,*parts)` | dojo_root:str\|None；parts:tuple[str,...]，源码中固定的相对组成 | str，官方资源 Linux 路径 | 仅官方条目实际调用时验证 dojo_root，再使用 posixpath 拼接并验证结果；无 parts、空/非字符串/绝对/反斜杠组成或无效根抛 ConfigError；不探测文件 |
| `_resource_map(dojo_root)` | dojo_root:str\|None | dict[str,dict[str,str]]，元素到 pp/basis 配对 | 返回唯一的显式完整资源字典；默认官方条目调用 _dojo_path，私人条目为绝对字符串；无 I/O |
| `build_abacus_resources(config)` | config:dict；可含 dojo_root | AbacusResources | 获取完整配对字典，核对非空顶层/条目类型、合法元素、严格 pp/basis 配对及每条 POSIX 路径，转换为稳定的两张下游映射；旧资源键或无效设置抛 ConfigError；无 I/O |

## 唯一资源表与编辑方式

`_resource_map` 返回一个连续、完整的配对字典。默认包含 83 个元素：67 个官方条目的两种路径引用 `_dojo_path(dojo_root, ...)`，La–Lu 的 15 个元素及 Hf 共 16 个私人条目直接写 Linux 绝对路径。私人条目沿用当前用户已有资源，不把这些个人目录假定为通用安装位置。不存在文件名表或元素族分支来在字典之外重新推断资源。

每个元素条目必须恰好包含两个字段：

```python
"O": {
    "pp": "/cluster/custom/O.upf",
    "basis": "/cluster/custom/O.orb",
},
```

增加元素时增加同样结构的条目；替换单元素时修改该条目；替换整套库时覆盖整个返回字典。新表是唯一来源，不自动合并或补回旧资源，不根据文件是否存在选择另一套资源。只有表中实际使用 `_dojo_path` 的官方条目需要 dojo_root；整表改为绝对字符串后，config 可以省略 dojo_root 或设为 None/空字符串。资源模块不主动 import pre_config，仍由配置加载器处理默认配置与 `--config` 覆盖。

`pre_config.py` 的资源设置仅保留 `dojo_root`。旧 `lanthanide_root`、`hfo_root`、`pp_overrides`、`basis_overrides` 不再支持；自定义配置应删除旧键，并把对应资源迁入该字典。CLI 配置加载把旧键报告为未知配置，直接调用 build_abacus_resources 的 config 如果含有旧键也明确抛 ConfigError，不静默忽略。路径拼接始终使用 posixpath，与 Windows/Linux 主机无关。

## 校验与调用关系

`preprocess.validation.validate_config` 构造并校验整张资源表。表中的未知元素、遗漏/多余字段、无效路径不应在任务写出阶段才偶然暴露；没有提供某个合法元素本身不构成错误，只有实际结构需要它时才要求存在。`preprocess.pipeline` 在读取实际结构后调用 `mapping_for`，将该材料所需子集交给隔离 writer 并写入任务输入描述。

v0.0.2 删除所有内容指纹。直接比较当前材料使用的资源路径字典，改变会导致已占用任务冲突；未使用元素的合法映射变化不影响该材料。资源本体的存在性、格式与数值适用性由后续计算工具负责，当前模块没有文件存在性检查开关。

## 本地结构入口

本目录全部Python源码及默认资源映射原样复制。新pre读取结构后按实际元素调用mapping_for，不再通过MP/MJM元数据指定元素。inputs.models.ELEMENTS导出位置保持，资源本体仍不探测。唯一用户资源键dojo_root的实际默认值与路径约束见[根API.md](../../API.md)。

