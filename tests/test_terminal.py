import unittest

from ucloud_api.terminal import TerminalJobs, container_path, parse_ssh_endpoint

APP = {
    "metadata": {"name": "terminal-ubuntu", "version": "Oct2026"},
    "invocation": {
        "ssh": {"mode": "OPTIONAL"},
        "parameters": [{"name": n} for n in
                       ["shell", "enableTmux", "modules", "initScript", "batchScript", "extraArgs", "slurm", "numWeb"]],
    },
}
PRODUCTS = [
    {"product": {"name": "cpu-amd-zen5-4-vcpu", "category": {"name": "cpu-amd-zen5", "provider": "ucloud"}}},
    {"product": {"name": "terminal", "category": {"name": "terminal", "provider": "ucloud"}}},
    {"product": {"name": "terminal", "category": {"name": "terminal", "provider": "sdu-odense"}}},
]
DRIVES = [{"id": "123456", "specification": {"title": "Home"}}]


class FakeClient:
    base_url = "https://cloud.sdu.dk"

    def retrieve_application(self, name, version=None):
        return APP

    def job_products(self):
        return PRODUCTS

    def browse_drives(self):
        return DRIVES


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


if __name__ == "__main__":
    unittest.main()
