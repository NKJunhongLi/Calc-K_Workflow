"""默认 ABACUS INPUT 的内置副本；保持用户原模板字节，不在运行时读外部模板。"""

# 该常量从项目 INPUT-template 原样迁入，包含原换行和全部参数；测试直接核对两者字节。
DEFAULT_INPUT_BYTES = (
    # ABACUS总控制与求解器设置，以下参数逐字节保留原模板。
    b'INPUT_PARAMETERS\r\n'
    b'suffix         ABACUS\r\n'
    b'nspin          1\r\n'
    b'symmetry       1\r\n'
    b'esolver_type   ksdft\r\n'
    b'ks_solver      genelpa\r\n'
    b'\r\n'
    # 截断能、自洽与弛豫收敛参数；整合阶段不改变这些数值。
    b'ecutwfc        100\r\n'
    b'scf_thr        1e-7\r\n'
    b'scf_nmax       100\r\n'
    b'relax_nmax     500\r\n'
    b'relax_method   cg\r\n'
    b'force_thr_ev   0.001\r\n'
    b'stress_thr     0.5\r\n'
    b'basis_type     lcao\r\n'
    b'calculation    cell-relax\r\n'
    b'chg_extrap     second-order\r\n'
    # 泛函、k点与占据设置同样保持原始计算约定。
    b'dft_functional PBESOL\r\n'
    b'\r\n'
    b'kspacing       0.1\r\n'
    b'\r\n'
    b'smearing_method gaussian\r\n'
    b'smearing_sigma  0.010\r\n'
    b'\r\n'
    b'mixing_beta     0.5\r\n'
)

def load_input_bytes(config: dict) -> tuple[bytes, str]:
    """返回 INPUT 字节及 builtin/user 来源；用户路径失败不允许隐式回退。"""
    from ..common.paths import ensure_host_path

    # 配置留空时直接采用内置字节；显式配置的用户文件必须真实读取成功。
    path = config.get("input_file")
    if path in (None, ""):
        return DEFAULT_INPUT_BYTES, "builtin"
    # 是否为绝对路径由配置校验负责，此处再次检查主机格式后只读输入。
    ensure_host_path(path)
    with open(path, "rb") as handle:
        data = handle.read()
    if not data.strip():
        raise ValueError("用户 INPUT 内容为空")
    return data, "user"
