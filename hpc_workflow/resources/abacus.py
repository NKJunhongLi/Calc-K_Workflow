"""复用已验证的元素资源文件名；全部路径保持 POSIX 文本，绝不检查资源存在性。"""

from dataclasses import dataclass
import posixpath

from ..common.errors import ConfigError
from ..inputs.models import ELEMENTS


@dataclass(frozen=True)
class AbacusResources:
    """按元素保存赝势与轨道路径；完整仅表示映射完整，不表示文件已验证。"""

    pp: dict[str, str]
    basis: dict[str, str]

    def validate_elements(self, elements: tuple[str, ...]) -> None:
        """检查实际元素有两种非空 POSIX 映射，不访问映射指向的文件。"""
        # 只核对本结构需要的两张元素映射；这里始终不探测赝势或轨道文件本体。
        for symbol in elements:
            for label, mapping in (("PP", self.pp), ("BASIS", self.basis)):
                if symbol not in mapping:
                    raise ConfigError(f"缺少 {symbol} 的 {label} 元素映射")
                validate_resource_path(mapping[symbol], f"{symbol} {label}")

    def mapping_for(self, elements: tuple[str, ...]) -> dict:
        """提取实际元素的映射用于显式输入描述，未使用元素不会引发无关任务冲突。"""
        # 先完成实际元素校验，再提取最小映射子集，避免无关元素配置影响任务复用。
        self.validate_elements(elements)
        return {"pp": {s: self.pp[s] for s in sorted(elements)},
                "basis": {s: self.basis[s] for s in sorted(elements)}}


def validate_resource_path(path: str, label: str) -> str:
    """校验 Linux 绝对路径语法；不执行 exists/isfile/stat/读取/资源哈希。"""
    # 把资源地址作为 Linux 文本校验，禁止分词歧义和路径穿越，不进行本机路径解析。
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "\\" in path or "\x00" in path or any(character.isspace() for character in path):
        raise ConfigError(f"{label} 必须是明确的 Linux 绝对路径文本：{path!r}")
    if any(piece in (".", "..") for piece in path.split("/")):
        raise ConfigError(f"{label} 不接受含 . 或 .. 的资源路径：{path!r}")
    return path


def _dojo_path(dojo_root: str | None, *parts: str) -> str:
    """仅在官方条目实际使用时校验主目录，并按 POSIX 文本拼接资源地址。"""
    # 整表若已换成用户绝对路径，此函数不会被调用，不强制要求无关的 dojo_root。
    root = validate_resource_path(dojo_root, "dojo_root")
    # 官方子路径只能追加到给定根目录；不允许绝对子路径暗中替换根。
    if not parts or any(not isinstance(part, str) or not part or part.startswith("/")
                        or "\\" in part for part in parts):
        raise ConfigError("Dojo 资源子路径必须是非空 POSIX 相对路径")
    return validate_resource_path(posixpath.join(root, *parts), "Dojo 资源路径")


def _resource_map(dojo_root: str | None) -> dict[str, dict[str, str]]:
    """完整资源表：用户在此逐元素修改、增加条目，或整体替换这个字典。"""
    # 每个元素把 pp/basis 放在一起。官方条目仅引用 dojo_root；私人条目直接写绝对路径。
    # 用户换用其他资源库时，可把整个返回字典替换为相同结构的绝对路径字典，
    # 此时 dojo_root 可留空；不要在表后补回旧资源或添加隐含的元素族分支。
    return {
        # 官方 Dojo 资源：H—Ba；安装位置由 pre_config.py 中的 dojo_root 控制。
        "H": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/H.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/H_DZP/H_gga_7au_100Ry_2s1p.orb"),
        },
        "He": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/He.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/He_DZP/He_gga_10au_100Ry_2s1p.orb"),
        },
        "Li": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Li.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Li_DZP/Li_gga_10au_100Ry_4s1p.orb"),
        },
        "Be": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Be.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Be_DZP/Be_gga_9au_100Ry_4s1p.orb"),
        },
        "B": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/B.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/B_DZP/B_gga_9au_100Ry_2s2p1d.orb"),
        },
        "C": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/C.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/C_DZP/C_gga_9au_100Ry_2s2p1d.orb"),
        },
        "N": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/N.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/N_DZP/N_gga_9au_100Ry_2s2p1d.orb"),
        },
        "O": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/O.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/O_DZP/O_gga_7au_100Ry_2s2p1d.orb"),
        },
        "F": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/F.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/F_DZP/F_gga_10au_100Ry_2s2p1d.orb"),
        },
        "Ne": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Ne.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Ne_DZP/Ne_gga_10au_100Ry_2s2p1d.orb"),
        },
        "Na": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Na.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Na_DZP/Na_gga_10au_100Ry_4s2p1d.orb"),
        },
        "Mg": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Mg.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Mg_DZP/Mg_gga_10au_100Ry_4s2p1d.orb"),
        },
        "Al": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Al.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Al_DZP/Al_gga_9au_100Ry_2s2p1d.orb"),
        },
        "Si": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Si.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Si_DZP/Si_gga_8au_100Ry_2s2p1d.orb"),
        },
        "P": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/P.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/P_DZP/P_gga_10au_100Ry_2s2p1d.orb"),
        },
        "S": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/S.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/S_DZP/S_gga_7au_100Ry_2s2p1d.orb"),
        },
        "Cl": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Cl.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Cl_DZP/Cl_gga_7au_100Ry_2s2p1d.orb"),
        },
        "Ar": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Ar.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Ar_DZP/Ar_gga_10au_100Ry_2s2p1d.orb"),
        },
        "K": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/K.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/K_DZP/K_gga_10au_100Ry_4s2p1d.orb"),
        },
        "Ca": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Ca.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Ca_DZP/Ca_gga_11au_100Ry_4s2p1d.orb"),
        },
        "Sc": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Sc.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Sc_DZP/Sc_gga_10au_100Ry_4s2p2d1f.orb"),
        },
        "Ti": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Ti.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Ti_DZP/Ti_gga_10au_100Ry_4s2p2d1f.orb"),
        },
        "V": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/V.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/V_DZP/V_gga_8au_100Ry_4s2p2d1f.orb"),
        },
        "Cr": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Cr.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Cr_DZP/Cr_gga_7au_100Ry_4s2p2d1f.orb"),
        },
        "Mn": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Mn.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Mn_DZP/Mn_gga_7au_100Ry_4s2p2d1f.orb"),
        },
        "Fe": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Fe.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Fe_DZP/Fe_gga_8au_100Ry_4s2p2d1f.orb"),
        },
        "Co": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Co.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Co_DZP/Co_gga_7au_100Ry_4s2p2d1f.orb"),
        },
        "Ni": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Ni.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Ni_DZP/Ni_gga_8au_100Ry_4s2p2d1f.orb"),
        },
        "Cu": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Cu.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Cu_DZP/Cu_gga_10au_100Ry_4s2p2d1f.orb"),
        },
        "Zn": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Zn.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Zn_DZP/Zn_gga_10au_100Ry_4s2p2d1f.orb"),
        },
        "Ga": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Ga.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Ga_DZP/Ga_gga_8au_100Ry_2s2p2d1f.orb"),
        },
        "Ge": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Ge.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Ge_DZP/Ge_gga_8au_100Ry_2s2p2d1f.orb"),
        },
        "As": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/As.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/As_DZP/As_gga_9au_100Ry_2s2p2d1f.orb"),
        },
        "Se": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Se.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Se_DZP/Se_gga_9au_100Ry_2s2p2d1f.orb"),
        },
        "Br": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Br.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Br_DZP/Br_gga_10au_100Ry_2s2p1d.orb"),
        },
        "Kr": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Kr.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Kr_DZP/Kr_gga_9au_100Ry_2s2p1d.orb"),
        },
        "Rb": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Rb.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Rb_DZP/Rb_gga_10au_100Ry_4s2p1d.orb"),
        },
        "Sr": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Sr.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Sr_DZP/Sr_gga_11au_100Ry_4s2p1d.orb"),
        },
        "Y": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Y.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Y_DZP/Y_gga_10au_100Ry_4s2p2d1f.orb"),
        },
        "Zr": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Zr.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Zr_DZP/Zr_gga_10au_100Ry_4s2p2d1f.orb"),
        },
        "Nb": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Nb.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Nb_DZP/Nb_gga_9au_100Ry_4s2p2d1f.orb"),
        },
        "Mo": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Mo.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Mo_DZP/Mo_gga_8au_100Ry_4s2p2d1f.orb"),
        },
        "Tc": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Tc.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Tc_DZP/Tc_gga_8au_100Ry_4s2p2d1f.orb"),
        },
        "Ru": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Ru.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Ru_DZP/Ru_gga_8au_100Ry_4s2p2d1f.orb"),
        },
        "Rh": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Rh.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Rh_DZP/Rh_gga_10au_100Ry_4s2p2d1f.orb"),
        },
        "Pd": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Pd.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Pd_DZP/Pd_gga_9au_100Ry_2s2p2d1f.orb"),
        },
        "Ag": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Ag.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Ag_DZP/Ag_gga_8au_100Ry_4s2p2d1f.orb"),
        },
        "Cd": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Cd.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Cd_DZP/Cd_gga_10au_100Ry_4s2p2d1f.orb"),
        },
        "In": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/In.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/In_DZP/In_gga_9au_100Ry_2s2p2d1f.orb"),
        },
        "Sn": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Sn.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Sn_DZP/Sn_gga_10au_100Ry_2s2p2d1f.orb"),
        },
        "Sb": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Sb.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Sb_DZP/Sb_gga_10au_100Ry_2s2p2d1f.orb"),
        },
        "Te": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Te.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Te_DZP/Te_gga_8au_100Ry_2s2p2d1f.orb"),
        },
        "I": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/I.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/I_DZP/I_gga_10au_100Ry_2s2p1d.orb"),
        },
        "Xe": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Xe.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Xe_DZP/Xe_gga_10au_100Ry_2s2p1d.orb"),
        },
        "Cs": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Cs.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Cs_DZP/Cs_gga_11au_100Ry_4s2p1d.orb"),
        },
        "Ba": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Ba.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Ba_DZP/Ba_gga_10au_100Ry_4s2p1d.orb"),
        },
        # 私人优化资源：La—Lu，保留原计算使用的绝对地址；其他用户需自行替换。
        "La": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/La.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/La_DZP/La_gga_10au_100Ry_4s2p2d1f.orb",
        },
        "Ce": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/Ce.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/Ce_DZP/Ce_gga_10au_100Ry_4s2p2d1f.orb",
        },
        "Pr": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/Pr.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/Pr_DZP/Pr_gga_10au_100Ry_4s2p2d1f.orb",
        },
        "Nd": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/Nd.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/Nd_DZP/Nd_gga_10au_100Ry_4s2p2d1f.orb",
        },
        "Pm": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/Pm.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/Pm_DZP/Pm_gga_10au_100Ry_4s2p2d1f.orb",
        },
        "Sm": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/Sm.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/Sm_DZP/Sm_gga_10au_100Ry_4s2p2d1f.orb",
        },
        "Eu": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/Eu.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/Eu_DZP/Eu_gga_10au_100Ry_4s2p2d1f.orb",
        },
        "Gd": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/Gd.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/Gd_DZP/Gd_gga_10au_100Ry_4s2p2d1f.orb",
        },
        "Tb": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/Tb.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/Tb_DZP/Tb_gga_10au_100Ry_4s2p2d1f.orb",
        },
        "Dy": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/Dy.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/Dy_DZP/Dy_gga_10au_100Ry_4s2p2d1f.orb",
        },
        "Ho": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/Ho.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/Ho_DZP/Ho_gga_10au_100Ry_4s2p2d1f.orb",
        },
        "Er": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/Er.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/Er_DZP/Er_gga_10au_100Ry_4s2p2d1f.orb",
        },
        "Tm": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/Tm.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/Tm_DZP/Tm_gga_10au_100Ry_4s2p2d1f.orb",
        },
        "Yb": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/Yb.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/Yb_DZP/Yb_gga_10au_100Ry_4s2p2d1f.orb",
        },
        "Lu": {
            "pp": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Pseudopotential/Lu.upf",
            "basis": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-SR_La-Series/Orbitals_v2.0/Lu_DZP/Lu_gga_10au_100Ry_4s2p2d1f.orb",
        },
        # 私人 Hf 资源：直接定位专门目录，不与官方根目录或 La 系目录关联。
        "Hf": {
            "pp": "/public/home/ustc_ljh/abacus/HfZrO_pp_basis/Hf.upf",
            "basis": "/public/home/ustc_ljh/abacus/HfZrO_pp_basis/Hf_gga_9au_100Ry_6s3p3d2f1g.orb",
        },
        # 继续使用官方 Dojo 资源：Ta—Bi。
        "Ta": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Ta.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Ta_DZP/Ta_gga_10au_100Ry_4s2p2d1f.orb"),
        },
        "W": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/W.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/W_DZP/W_gga_9au_100Ry_4s2p2d1f.orb"),
        },
        "Re": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Re.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Re_DZP/Re_gga_8au_100Ry_4s2p2d1f.orb"),
        },
        "Os": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Os.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Os_DZP/Os_gga_8au_100Ry_4s2p2d1f.orb"),
        },
        "Ir": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Ir.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Ir_DZP/Ir_gga_8au_100Ry_4s2p2d1f.orb"),
        },
        "Pt": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Pt.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Pt_DZP/Pt_gga_11au_100Ry_4s2p2d1f.orb"),
        },
        "Au": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Au.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Au_DZP/Au_gga_10au_100Ry_4s2p2d1f.orb"),
        },
        "Hg": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Hg.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Hg_DZP/Hg_gga_8au_100Ry_4s2p2d1f.orb"),
        },
        "Tl": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Tl.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Tl_DZP/Tl_gga_9au_100Ry_2s2p2d1f.orb"),
        },
        "Pb": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Pb.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Pb_DZP/Pb_gga_10au_100Ry_2s2p2d1f.orb"),
        },
        "Bi": {
            "pp": _dojo_path(dojo_root, "Pseudopotential/Bi.upf"),
            "basis": _dojo_path(dojo_root, "Orbitals_v2.0/Bi_DZP/Bi_gga_10au_100Ry_2s2p2d1f.orb"),
        },
    }


def build_abacus_resources(config: dict) -> AbacusResources:
    """读取唯一资源表，校验成对映射，返回下游保持不变的 pp/basis 接口。"""
    # 配置不再维护逐元素覆盖或私人目录，避免两个入口产生不明确的优先级。
    if not isinstance(config, dict):
        raise ConfigError("资源配置必须为 dict")
    removed = {"lanthanide_root", "hfo_root", "pp_overrides", "basis_overrides"}.intersection(config)
    if removed:
        raise ConfigError("资源配置项已移除：" + ", ".join(sorted(removed))
                          + "；请直接修改 resources/abacus.py 的 _resource_map 字典")

    # 根目录只交给资源表；是否使用官方库由表中条目决定，不预先探测或强制校验。
    resource_map = _resource_map(config.get("dojo_root"))
    if not isinstance(resource_map, dict) or not resource_map:
        raise ConfigError("资源表必须是非空的元素到 pp/basis 配对字典")
    pp, basis = {}, {}
    # 全表做纯文本契约校验，缺失实际元素则仍由 validate_elements 给出明确诊断。
    for symbol, pair in resource_map.items():
        if not isinstance(symbol, str) or symbol not in ELEMENTS:
            raise ConfigError(f"资源表含未知元素 {symbol!r}")
        if not isinstance(pair, dict) or set(pair) != {"pp", "basis"}:
            raise ConfigError(f"{symbol} 资源条目必须恰好包含 pp 和 basis 两个字段")
        pp[symbol] = validate_resource_path(pair["pp"], f"{symbol} PP")
        basis[symbol] = validate_resource_path(pair["basis"], f"{symbol} BASIS")

    # 这里只拆分已验证的字典，不读取资源文件、不查找替代文件、不自动补回旧元素。
    return AbacusResources(pp, basis)
