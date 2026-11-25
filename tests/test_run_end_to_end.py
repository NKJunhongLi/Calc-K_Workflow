"""真实 CLI/子进程/流式日志回归；科学可执行程序与 Slurm 用本地桩替代。"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hpc_workflow.common.atomic import atomic_write_json, read_json
from hpc_workflow.records.manifest import new_revision_id
from hpc_workflow.common.paths import join_local, project_root
from hpc_workflow.submission.state import read_progress


STRU = """ATOMIC_SPECIES
Si 28.085 /remote/resources/Si.upf

NUMERICAL_ORBITAL
/remote/resources/Si.orb

LATTICE_CONSTANT
1.8897261258369282

LATTICE_VECTORS
5 0 0
0 5 0
0 0 5

ATOMIC_POSITIONS
Direct

Si
0.0
1
0 0 0 1 1 1
"""

ABACUS_STUB = r"""import json,os,sys
name=os.path.basename(os.path.dirname(os.getcwd()))
print('UNIQUE_ABACUS_OUTPUT_'+name,flush=True)
os.makedirs('OUT.ABACUS',exist_ok=True)
# 初始 STRU 保持裸移动标志；末态模拟 ABACUS 的 m 前缀，避免直接复制掩盖格式差异。
with open('STRU',encoding='utf-8') as h:
 relaxed_stru=h.read().replace('0 0 0 1 1 1','0 0 0 m 1 1 1')
with open('OUT.ABACUS/STRU_ION_D','w',encoding='utf-8') as h:
 h.write(relaxed_stru)
failed=name=='a-fails'
recovered=name in ('b-recovers','c-old-scf-nonzero')
log='STEP OF ION RELAXATION : 2\n'
if recovered:
 print('convergence has not been achieved @_@',flush=True)
 log='STEP OF RELAXATION : 1\nconvergence has not been achieved @_@\n'+log
log+=('convergence has not been achieved @_@\n' if failed else 'charge density convergence is achieved\n')
log+='Relaxation is converged!\nTotal Time: 1 s\n'
with open('OUT.ABACUS/running_cell-relax.log','w',encoding='utf-8') as h:
 h.write(log)
for filename in ('abacus.json','time.json'):
 with open(filename,'w',encoding='utf-8') as h: json.dump({'complete':True},h)
if failed:
 print('SCF not converged',file=sys.stderr,flush=True)
 sys.exit(7)
if name=='c-old-scf-nonzero':
 sys.exit(7)
"""


class RunEndToEndTests(unittest.TestCase):
    """子进程走真实入口与科学读取；只在新项目专属临时目录写出测试文件。"""

    def setUp(self):
        """建立有效 STRU、配置模板和带空格的 Python 桩路径。"""
        artifacts = join_local(project_root(), ".test-artifacts")
        os.makedirs(artifacts, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=artifacts)
        self.addCleanup(self.temporary.cleanup)
        self.root = self.temporary.name
        self.work = join_local(self.root, "work")
        self.files = join_local(self.root, "smoke files")
        os.makedirs(self.files)
        self.abacus = self.write("fake abacus.py", ABACUS_STUB)
        self.capture = join_local(self.files, "sbatch-argv.json")
        self.sbatch = self.write("fake sbatch.py", "import json,sys\nwith open(" + repr(self.capture)
                                 + ",'w',encoding='utf-8') as h: json.dump(sys.argv[1:],h)\nprint('9001;local-test')\n")
        self.queue = self.write("fake queue.py", "import sys\nif sys.argv[1]=='account': print('9001|COMPLETED|None')\n")
        self.config = join_local(self.files, "relax config.py")
        self.cli = join_local(project_root(), "cli.py")
        # 子进程同样禁写 bytecode，避免科学库导入产生越界缓存。
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
                        SLURM_JOB_ID="9001", SLURM_NTASKS="2", SLURM_CPUS_PER_TASK="1")

    def write(self, name: str, text: str) -> str:
        """在本测试 files 目录写 UTF-8 桩脚本并返回绝对位置。"""
        path = join_local(self.files, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path

    def configure(self, work: str) -> None:
        """写入当前机器工作根和测试调度器，其他值由真实默认模板提供。"""
        value = {"work_root": work, "parallel_jobs": 1, "python_command": [sys.executable, "-B"],
                 "abacus_command": [sys.executable, "-B", self.abacus],
                 "sbatch_command": [sys.executable, "-B", self.sbatch],
                 "squeue_command": [sys.executable, "-B", self.queue, "queue"],
                 "sacct_command": [sys.executable, "-B", self.queue, "account"]}
        with open(self.config, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("CONFIG = " + repr(value) + "\n")

    def make_task(self, name: str) -> str:
        """建立单 Si 原胞与 cell-relax INPUT，不建立假完成记录。"""
        task = join_local(self.work, name)
        os.makedirs(task)
        for filename, text in (("STRU", STRU), ("INPUT", "INPUT_PARAMETERS\ncalculation cell-relax\nrelax_nmax 5\n")):
            with open(join_local(task, filename), "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        atomic_write_json(join_local(task, "task_manifest.json"), {"schema_version": 2,
                          "workflow_version": "0.0.2", "status": "success", "input_revision_id": new_revision_id()})
        return task

    def invoke(self, *args: str) -> subprocess.CompletedProcess:
        """运行真实 cli.py，捕获两个原始输出流供测试断言。"""
        return subprocess.run([sys.executable, "-B", self.cli, "run", "relax", *args], cwd=project_root(),
                              env=self.env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=False, timeout=45)

    def make_plan(self) -> str:
        """使用真实配置加载与 prepare 链生成计划，要求无模板或解析错误。"""
        result = self.invoke("plan", "--config", self.config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["plan"]

    def test_real_cli_sbatch_worker_continues_then_status(self):
        """从配置到提交 argv 到真实 worker：第一结构失败，第二完成，status 根因正确。"""
        failed = self.make_task("a-fails")
        succeeded = self.make_task("b-completes")
        self.configure(self.work)
        original = {join_local(task, name): Path(join_local(task, name)).read_bytes()
                    for task in (failed, succeeded) for name in ("STRU", "INPUT")}
        plan = self.make_plan()
        submitted = self.invoke("submit", "--config", self.config, "--plan", plan)
        self.assertEqual(submitted.returncode, 0, submitted.stdout + submitted.stderr)
        argv = read_json(self.capture)
        # 模拟用户 wrapper 的 exec "$@"；通过被 sbatch 接收的实际 argv 启动 worker。
        python_index = argv.index(sys.executable)
        worker = subprocess.run(argv[python_index:], cwd=project_root(), env=self.env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=45)
        self.assertEqual(worker.returncode, 1, worker.stdout + worker.stderr)
        finished = read_progress(plan, 1)["finished"]
        self.assertEqual([item["status"] for item in finished], ["technical_failed", "success"])
        self.assertEqual(finished[0]["reason"], "SCF 不收敛")
        self.assertEqual(worker.stdout.count("UNIQUE_ABACUS_OUTPUT_a-fails"), 1)
        self.assertEqual(worker.stdout.count("UNIQUE_ABACUS_OUTPUT_b-completes"), 1)
        self.assertEqual(worker.stderr.count("SCF not converged"), 1)
        self.assertIn("COMMAND relax BEGIN", worker.stdout)
        status = self.invoke("status", "--config", self.config)
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertIn("完成=1", status.stdout)
        self.assertIn("失败=1", status.stdout)
        self.assertIn("SCF 不收敛", status.stdout)
        for path, expected in original.items():
            self.assertEqual(Path(path).read_bytes(), expected)

    def test_real_cli_worker_uses_relocated_plan_and_frozen_commands(self):
        """整棵工作树复制后旧根删除，worker 仍只用相对计划定位并保持原输入字节。"""
        self.make_task("b-completes")
        self.configure(self.work)
        plan = self.make_plan()
        copied = join_local(self.root, "cluster work")
        shutil.copytree(self.work, copied)
        relative = os.path.relpath(plan, self.work)
        new_plan = os.path.join(copied, relative)
        # 删除前显式检查只位于本测试的临时根，且绝不触碰项目其他工作树。
        self.assertEqual(os.path.commonpath([self.root, os.path.realpath(self.work)]), self.root)
        shutil.rmtree(self.work)
        self.configure(copied)
        worker = self.invoke("worker", "--plan", new_plan, "--batch", "1")
        self.assertEqual(worker.returncode, 0, worker.stdout + worker.stderr)
        self.assertEqual(read_progress(new_plan, 1)["finished"][0]["status"], "success")
        output = join_local(copied, "b-completes", "cell-relax", "OUT.ABACUS", "STRU_ION_D")
        self.assertTrue(os.path.isfile(output))
        self.assertIn(" m 1 1 1", Path(output).read_text(encoding="utf-8"))
        status = self.invoke("status", "--config", self.config)
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertIn(copied, status.stdout)
        self.assertIn("完成=1", status.stdout)

    def test_real_cli_worker_keeps_final_step_and_nonzero_exit_independent(self):
        """真实 worker 的旧 SCF 尾部不污染末步；非零退出预分类也不能伪造末步 SCF 根因。"""
        self.make_task("b-recovers")
        self.make_task("c-old-scf-nonzero")
        self.configure(self.work)
        plan = self.make_plan()
        worker = self.invoke("worker", "--plan", plan, "--batch", "1")
        self.assertEqual(worker.returncode, 1, worker.stdout + worker.stderr)
        self.assertEqual(worker.stdout.count("convergence has not been achieved"), 2)
        finished = read_progress(plan, 1)["finished"]
        self.assertEqual(len(finished), 2)
        self.assertEqual(finished[0]["status"], "success")
        self.assertEqual(finished[0]["reason"], "")
        self.assertEqual(finished[1]["status"], "technical_failed")
        self.assertNotEqual(finished[1]["reason"], "SCF 不收敛")
        self.assertIn("7", finished[1]["reason"])
        status = self.invoke("status", "--config", self.config)
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertIn("完成=1", status.stdout)
        self.assertIn("失败=1", status.stdout)

    def test_invalid_group_count_preserves_revision_but_blocks_submit(self):
        """分组失败仍留版本计划，修正数量后继续准备而不是使目录失去关联。"""
        self.make_task("b-completes")
        self.configure(self.work)
        text = Path(self.config).read_text(encoding="utf-8").replace("'parallel_jobs': 1", "'parallel_jobs': 2")
        Path(self.config).write_text(text, encoding="utf-8")
        result = self.invoke("plan", "--config", self.config)
        self.assertNotEqual(result.returncode, 0)
        saved = next(Path(self.work).glob(".hpc_runs/relax/*/plan.json"))
        first = read_json(str(saved))
        self.assertEqual(first["status"], "prepared_not_submitted")
        self.assertEqual(first["groups"], [])
        blocked = self.invoke("submit", "--config", self.config, "--plan", str(saved))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertFalse(os.path.exists(self.capture))
        self.configure(self.work)
        retry = read_json(self.make_plan())
        self.assertEqual(first["tasks"][0]["input_revision_id"], retry["tasks"][0]["input_revision_id"])
        self.assertEqual(retry["groups"], [{"batch": 1, "tasks": ["b-completes"]}])


    def test_local_directory_pre_relax_zstar_and_original_post(self):
        """真实目录 pre 接入两阶段原 worker 与原 post，验证 LOCAL 缺省字段和最终清理。"""
        import csv
        from ase import Atoms
        from ase.io import write

        # 用户仅提供结构目录；配置和桩程序放在其外，避免“不过滤文件名”把它们当输入。
        structures = join_local(self.root, "initial structures")
        os.mkdir(structures)
        source = join_local(structures, "sample.cif")
        write(source, Atoms("Si", scaled_positions=[[0, 0, 0]], cell=[5, 5, 5], pbc=True), format="cif")
        original = Path(source).read_bytes()
        pre_config = self.write("pre config.py", "CONFIG = " + repr({"structures_root": structures,
                                      "work_root": self.work}) + "\n")

        def command(*args):
            """所有阶段使用真实 CLI 子进程，科学程序仅替换为本测试内的 Python 桩。"""
            result = subprocess.run([sys.executable, "-B", self.cli, *args], cwd=project_root(),
                                    env=self.env, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", check=False, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result

        # pre 的 validate 全程只读；随后 pre 应真实调用 ASE/spglib 和常驻 writer。
        command("validate", "pre", "--config", pre_config)
        self.assertFalse(os.path.exists(self.work))
        command("pre", "--config", pre_config)
        tasks = [path for path in Path(self.work).iterdir() if path.name.startswith("local__")]
        self.assertEqual(len(tasks), 1)
        task = str(tasks[0])
        manifest = read_json(join_local(task, "task_manifest.json"))
        self.assertEqual(manifest["input_source"], "LOCAL")
        self.assertEqual(manifest["source_record_id"], "sample.cif")
        for key in ("global_sample_id", "dft_priority", "source_metadata", "nominal_k_available", "nominal_k_value"):
            self.assertNotIn(key, manifest)

        # 保持原阶段准备和 worker 实现，先完成真正的 relax 终态，再准备 zstar 原胞。
        self.configure(self.work)
        relax_plan = json.loads(command("run", "relax", "plan", "--config", self.config).stdout)["plan"]
        command("run", "relax", "worker", "--plan", relax_plan, "--batch", "1")
        self.assertEqual(read_progress(relax_plan, 1)["finished"][0]["status"], "success")
        zstar_stub = self.write("fake zstar.py", r'''import json,os,sys
label=sys.argv[1]
print('LOCAL_ZSTAR_'+label,flush=True)
if label=='static':
 os.makedirs('0.no-move/pyatb-band/Out/Band_Structure',exist_ok=True)
 os.makedirs('dielectric_response',exist_ok=True)
 for name,text in [('band.dat','0 1\n'),('band_info.dat','Band gap (eV): 1.2\n')]:
  with open('0.no-move/pyatb-band/Out/Band_Structure/'+name,'w',encoding='utf-8') as h:h.write(text)
 with open('0.no-move/zstar_insulation.json','w',encoding='utf-8') as h:json.dump({'insulating':True,'gap_eV':1.2,'threshold_eV':0.01},h)
 with open('BORN','w',encoding='utf-8') as h:h.write('# epsilon and Born\n2 0 0 0 2 0 0 0 2\n1 0 0 0 1 0 0 0 1\n')
 for name in ('BEC.dat','BEC.rep.dat'):
  with open(name,'w',encoding='utf-8') as h:h.write('# atom species tensor\n1 Si 1 0 0 0 1 0 0 0 1\n')
 response={'frequency_cm-1':0,'response_kind':'total relative dielectric tensor','response_unit':'1','response_convention':'relative permittivity','electronic_response_included':True,'tensor':[[20,0,0],[0,20,0],[0,0,20]]}
 with open('dielectric_response/static_response.json','w',encoding='utf-8') as h:json.dump(response,h)
 with open('data-HR-sparse_SPIN0.csr','w',encoding='utf-8') as h:h.write('cleanup fixture')
 with open('keep.log','w',encoding='utf-8') as h:h.write('keep fixture')
''')
        zstar_config = self.write("zstar config.py", "CONFIG = " + repr({"work_root": self.work,
            "parallel_jobs": 1, "abacus_command": [sys.executable, "-B", self.abacus],
            "squeue_command": [sys.executable, "-B", self.queue, "queue"],
            "sacct_command": [sys.executable, "-B", self.queue, "account"],
            "zstar_commands": {label: [sys.executable, "-B", zstar_stub, label]
                               for label in ("pre", "run", "post", "static")}}) + "\n")
        zstar_plan = json.loads(command("run", "zstar", "plan", "--config", zstar_config).stdout)["plan"]
        worker = command("run", "zstar", "worker", "--plan", zstar_plan, "--batch", "1")
        for label in ("pre", "run", "post", "static"):
            self.assertEqual(worker.stdout.count("LOCAL_ZSTAR_" + label), 1)
        self.assertEqual(read_progress(zstar_plan, 1)["finished"][0]["status"], "success")

        # 原 post 只读预览不能删文件；原 collect 发布 46 列并执行原有白名单清理。
        output = join_local(self.root, "results.csv")
        post_config = self.write("post config.py", "CONFIG = " + repr({"work_root": self.work,
                                                   "output_csv": output}) + "\n")
        candidate = join_local(task, "zstar", "data-HR-sparse_SPIN0.csr")
        command("post", "validate", "--config", post_config)
        self.assertFalse(os.path.exists(output))
        self.assertTrue(os.path.exists(candidate))
        command("post", "--config", post_config)
        with open(output, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(len(reader.fieldnames), 46)
            rows = list(reader)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["input_source"], "LOCAL")
        self.assertEqual(row["source_record_id"], "sample.cif")
        self.assertEqual(row["remarks"], "")
        self.assertEqual(row["provenance_status"], "revision_linked")
        self.assertEqual(float(row["dielectric_total_trace_mean"]), 20)
        self.assertEqual(float(row["dielectric_ionic_trace_mean"]), 18)
        for key in ("global_sample_id", "dft_priority", "nominal_k_available", "nominal_k_value"):
            self.assertEqual(row[key], "")
        self.assertFalse(os.path.exists(candidate))
        self.assertTrue(os.path.exists(join_local(task, "zstar", "keep.log")))
        command("post", "clean", "--config", post_config)
        self.assertEqual(Path(source).read_bytes(), original)
