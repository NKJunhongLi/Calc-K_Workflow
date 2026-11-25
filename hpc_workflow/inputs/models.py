"""本地结构的轻量来源记录；元素常量保持原导出位置供资源和后处理复用。"""

from dataclasses import dataclass


# 元素表只描述真实化学元素，不附加筛选规则；原路径由资源和后处理共同导入。
ELEMENTS = frozenset((
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
    "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce "
    "Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn "
    "Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og"
).split())


@dataclass(frozen=True)
class InputRecord:
    """文件身份先于解析建立；化学信息只在实际读取成功后补入。"""

    # 单层源文件名包含后缀，是可随整个输入目录迁移的相对身份。
    input_source: str
    source_record_id: str
    source_file: str
    # 失败尝试可以没有化学信息；成功 manifest 只记录从真实结构得到的值。
    formula: str = ""
    elements: tuple[str, ...] = ()
