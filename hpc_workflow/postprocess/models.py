"""后处理的科学数据类型；只表达数据，不读取目录或改变计算状态。"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


# 沿用旧版表格的展平约定：先行后列；非对角项和负号均按原数据保留。
Matrix3 = NDArray[np.float64]
CsvValue = str | int | float
COMPONENTS = ("xx", "xy", "xz", "yx", "yy", "yz", "zx", "zy", "zz")


class DataValidationError(ValueError):
    """可预期的产物格式、物理约定或数值错误，供单任务隔离处理。"""


@dataclass(frozen=True)
class BornChargeRecord:
    """一个不等价原子的 BEC；行是位移方向，列是极化方向，单位 e。"""

    atom_index: int
    species: str
    z_displacement_polarization: Matrix3


@dataclass(frozen=True)
class DielectricValues:
    """总、电子和离子贡献及两个迹平均值；矩阵禁止原地修改。"""

    total: Matrix3
    electronic: Matrix3
    ionic: Matrix3
    ionic_trace_mean: float
    total_trace_mean: float
