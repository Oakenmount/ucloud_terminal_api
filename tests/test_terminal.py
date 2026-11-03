import unittest

from ucloud_api.client import UCloudError
from ucloud_api.machines import live_machines
from ucloud_api.terminal import TerminalJobs, container_path, parse_ssh_endpoint

APP = {
    "metadata": {"name": "terminal-ubuntu", "version": "Oct2026"},
    "invocation": {
        "ssh": {"mode": "OPTIONAL"},
        "parameters": [{"name": n} for n in
                       ["shell", "enableTmux", "modules", "initScript", "batchScript", "extraArgs", "slurm", "numWeb"]],
    },
}
CPU_CAT = {"name": "cpu-amd-zen5", "provider": "ucloud", "accountingUnit": {"name": "Core"},
           "accountingFrequency": "PERIODIC_MINUTE"}
GPU_CAT = {"name": "gpu-nvidia-b200", "provider": "ucloud", "accountingUnit": {"name": "GPU"},
           "accountingFrequency": "PERIODIC_MINUTE"}
PRODUCTS = [
    {"product": {"name": "cpu-amd-zen5-4-vcpu", "price": 4, "cpu": 4, "category": CPU_CAT},
     "support": {"queueStatus": "AVAILABLE"}},
    {"product": {"name": "gpu-nvidia-b200-1-mig.1g", "price": 1, "gpu": 1, "category": GPU_CAT,
                 "fraction": {"numerator": 1, "denominator": 7}}, "support": {"queueStatus": "BUSY"}},
    {"product": {"name": "terminal", "category": {"name": "terminal", "provider": "ucloud"}}},
    {"product": {"name": "terminal", "category": {"name": "terminal", "provider": "sdu-odense"}}},
]
DRIVES = [{"id": "123456", "specification": {"title": "Home"}}]
# 600 core-minutes = 10 core-hours left on cpu-amd-zen5; nothing on the GPU category.
WALLETS = [{"paysFor": CPU_CAT, "maxUsable": 600, "quota": 6000, "totalUsage": 5400}]


class FakeClient:
    base_url = "https://cloud.sdu.dk"

    def retrieve_application(self, name, version=None):
        return APP

    def job_products(self):
        return PRODUCTS

    def browse_drives(self):
        return DRIVES

    def wallets(self, product_type="COMPUTE"):
        return WALLETS


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.tj = TerminalJobs(FakeClient())

    def test_parse_ssh(self):
        ups = [{"status": "Job is starting soon"},
               {"status": "SSH: Connected! Available at: ssh ucloud@ssh.cloud.sdu.dk -p 2413"}]
        ep = parse_ssh_endpoint(ups)
        self.assertEqual((ep.user, ep.host, ep.port), ("ucloud", "ssh.cloud.sdu.dk", 2413))
        self.assertIsNone(parse_ssh_endpoint([{"status": None}]))

    def test_paths(self):
        self.assertEqual(self.tj.resolve_path("Home/proj/x"), "/123456/proj/x")
        self.assertEqual(self.tj.resolve_path("/123456/proj"), "/123456/proj")
        self.assertEqual(container_path("/123456/proj/"), "/work/proj")
        with self.assertRaises(ValueError):
            self.tj.resolve_path("Nope/x")

    def test_product(self):
        self.assertEqual(self.tj.resolve_product("cpu-amd-zen5-4-vcpu"),
                         {"id": "cpu-amd-zen5-4-vcpu", "category": "cpu-amd-zen5", "provider": "ucloud"})
        with self.assertRaises(ValueError):
            self.tj.resolve_product("terminal")  # ambiguous
        with self.assertRaises(ValueError):
            self.tj.resolve_product("nope")

    def test_spec(self):
        spec = self.tj.build_spec("cpu-amd-zen5-4-vcpu", hours=3, workdir="Home/proj",
                                  init_script="Home/proj/start.sh", env_dir="Home/envs/py",
                                  modules_dir="Home/mods", extra_args="a b", name="t")
        self.assertTrue(spec["sshEnabled"])
        self.assertEqual(spec["timeAllocation"], {"hours": 3, "minutes": 0, "seconds": 0})
        self.assertEqual([r["path"] for r in spec["resources"]], ["/123456/proj", "/123456/envs/py"])
        self.assertEqual(spec["parameters"]["initScript"]["path"], "/123456/proj/start.sh")
        self.assertEqual(spec["parameters"]["modules"]["path"], "/123456/mods")
        self.assertEqual(spec["parameters"]["extraArgs"], {"type": "text", "value": "a b"})
        with self.assertRaises(ValueError):
            self.tj.build_spec("cpu-amd-zen5-4-vcpu", extra_parameters={"bogus": {}})

    def test_live_machines(self):
        ms = {m.name: m for m in live_machines(PRODUCTS, WALLETS)}
        self.assertNotIn("terminal", ms)
        cpu, mig = ms["cpu-amd-zen5-4-vcpu"], ms["gpu-nvidia-b200-1-mig.1g"]
        self.assertEqual(cpu.price_per_hour, 4)
        self.assertAlmostEqual(mig.price_per_hour, 1 / 7)
        self.assertTrue(cpu.usable)
        self.assertEqual(cpu.remaining_units, 10)
        self.assertFalse(mig.usable)
        self.assertEqual(mig.queue, "BUSY")

    def test_preflight(self):
        warnings = self.tj.preflight(self.tj.build_spec("cpu-amd-zen5-4-vcpu", hours=2))
        self.assertIn("8 core-hours", warnings[0])
        with self.assertRaises(UCloudError):   # 4/h x 3 h = 12 > 10 remaining
            self.tj.preflight(self.tj.build_spec("cpu-amd-zen5-4-vcpu", hours=3))
        with self.assertRaises(UCloudError):   # no GPU allocation
            self.tj.preflight(self.tj.build_spec("gpu-nvidia-b200-1-mig.1g", hours=1))
        self.assertEqual(len(self.tj.preflight(self.tj.build_spec("cpu-amd-zen5-4-vcpu", hours=3),
                                               check_allocation=False)), 1)


if __name__ == "__main__":
    unittest.main()
