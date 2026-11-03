"""Machine types (UCloud "products") for compute jobs.

Two sources:
  * CATALOG: a static reference built from the UCloud documentation
    (https://docs.cloud.sdu.dk/guide/resources-products.html) and the live product list on 2026-11-03.
    It is for help text and for reading offline. It is not authoritative.
  * live_machines(): the products your workspace can see right now, merged with your allocations
    (wallets) and the current queue status. Use this (`ucloud products`) to choose a machine.

Naming: <category>-<size>. The size is vCPUs for CPU machines and GPUs for GPU machines.
You pay for a machine from the allocation (wallet) of its *category*. A machine whose category
has no allocation in the active workspace cannot be launched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

# Categories, from the docs plus the live catalog. Sizes are the machine-name suffixes.
CATALOG: List[Dict] = [
    {
        "provider": "ucloud", "provider_title": "DeiC Interactive HPC (SDU/K8s)",
        "category": "cpu-amd-zen5", "kind": "CPU", "unit": "core-hours",
        "names": "cpu-amd-zen5-{1,2,4,8,16,32,64,128}-vcpu",
        "per_size": "3 GB RAM per vCPU (e.g. -4-vcpu = 4 vCPU / 12 GB)",
        "hardware": "2x AMD EPYC 9635 @2.25 GHz, 128 vCPU, 768 GB DDR5-6000 per node",
        "notes": "Default general-purpose choice. Supports multi-node (--nodes).",
    },
    {
        "provider": "ucloud", "provider_title": "DeiC Interactive HPC (SDU/K8s)",
        "category": "gpu-nvidia-b200", "kind": "GPU", "unit": "GPU-hours",
        "names": "gpu-nvidia-b200-{1..8}-gpu",
        "per_size": "48 vCPU / 288 GB RAM / 1x B200 192 GB per GPU",
        "hardware": "2x AMD EPYC 9655 @2.6 GHz, 384 vCPU, 2304 GB DDR5-6400, 8x NVIDIA B200 192 GB per node",
        "notes": "",
    },
    {
        "provider": "ucloud", "provider_title": "DeiC Interactive HPC (SDU/K8s)",
        "category": "gpu-nvidia-b200 (MIG)", "kind": "GPU", "unit": "GPU-hours (1 MIG = 1/7 GPU)",
        "names": "gpu-nvidia-b200-{1..4}-mig.1g",
        "per_size": "6 vCPU / 36 GB RAM / one 1/7-B200 slice (23 GB) per MIG",
        "hardware": "Multi-Instance GPU slices of the B200 nodes",
        "notes": "Cheap GPU for development. Billed to the gpu-nvidia-b200 allocation.",
    },
    {
        "provider": "sdu-odense", "provider_title": "SDU-Odense (SDU-affiliated users only)",
        "category": "cpu-amd-zen4", "kind": "CPU", "unit": "core-hours",
        "names": "cpu-amd-zen4-{1,2,4,8,16,32,64,128}",
        "per_size": "6 GB RAM per vCPU",
        "hardware": "2x AMD EPYC 9534 @2.45 GHz, 128 vCPU, 768 GB DDR5-4800 per node",
        "notes": "",
    },
    {
        "provider": "sdu-odense", "provider_title": "SDU-Odense (SDU-affiliated users only)",
        "category": "gpu-nvidia-h100", "kind": "GPU", "unit": "GPU-hours",
        "names": "gpu-nvidia-h100-{1..4}",
        "per_size": "48 vCPU / 192 GB RAM / 1x H100 SXM5 80 GB per GPU",
        "hardware": "2x AMD EPYC 9454 @2.75 GHz, 96 vCPU, 768 GB DDR5-4800, 4x NVIDIA H100 80 GB per node",
        "notes": "",
    },
    {
        "provider": "sdu-odense", "provider_title": "SDU-Odense (SDU-affiliated users only)",
        "category": "gpu-nvidia-rtx6000", "kind": "GPU", "unit": "GPU-hours",
        "names": "gpu-nvidia-rtx6000-{1..4}",
        "per_size": "24 vCPU / 128 GB RAM / 1x RTX PRO 6000 per GPU",
        "hardware": "Intel Xeon 6740E nodes with NVIDIA RTX PRO 6000",
        "notes": "Not in the docs. Taken from the live catalog.",
    },
    {
        "provider": "sdu-odense", "provider_title": "SDU-Odense (group-restricted)",
        "category": "u1-dfm / u1-madlab / u1-medrob / u2-madlab", "kind": "mixed", "unit": "core- or GPU-hours",
        "names": "u1-dfm-{1..8} (B200), u1-madlab-{1..128} (CPU), u1-medrob-{1..4} (L40S), u2-madlab-{1..4} (L40S)",
        "per_size": "see `ucloud products`",
        "hardware": "Dedicated research-group hardware",
        "notes": "Only usable if your project has an allocation for that group's category.",
    },
]

CATALOG_HELP = """\
Machine types (run `ucloud products` for the live list with your allocations and queue status):

  provider ucloud = DeiC Interactive HPC (SDU/K8s)
    cpu-amd-zen5-{1,2,4,8,16,32,64,128}-vcpu   CPU, 3 GB RAM/vCPU            billed in core-hours
    gpu-nvidia-b200-{1..8}-gpu                 per GPU: 48 vCPU, 288 GB, B200 billed in GPU-hours
    gpu-nvidia-b200-{1..4}-mig.1g              per MIG: 6 vCPU, 36 GB, 1/7 B200 (1/7 GPU-hour)
  provider sdu-odense (SDU-affiliated users only)
    cpu-amd-zen4-{1,2,4,8,16,32,64,128}        CPU, 6 GB RAM/vCPU             billed in core-hours
    gpu-nvidia-h100-{1..4}                     per GPU: 48 vCPU, 192 GB, H100 80 GB
    gpu-nvidia-rtx6000-{1..4}                  per GPU: 24 vCPU, 128 GB, RTX PRO 6000
    u1-dfm-*, u1-madlab-*, u1-medrob-*, u2-madlab-*   research-group hardware (restricted)

  The cost per hour equals the size: cpu-amd-zen5-8-vcpu costs 8 core-hours per hour and
  gpu-nvidia-b200-2-gpu costs 2 GPU-hours per hour. A MIG slice costs 1/7 GPU-hour per hour.
  You can only launch machines whose category has an allocation (`ucloud balance`).
"""


@dataclass
class Machine:
    name: str
    provider: str
    category: str
    cpu: int
    memory_gb: int
    gpu: int
    gpu_model: str
    cpu_model: str
    unit: str                 # "Core" / "GPU" / "DKK" ...
    price_per_hour: float     # unit-hours charged per hour of runtime (core-hours/h or GPU-hours/h)
    queue: Optional[str]      # AVAILABLE / BUSY / FULL / None
    usable: bool              # category has a non-empty allocation in this workspace
    remaining_units: Optional[float]  # remaining allocation of the category, in unit-hours

    def to_dict(self) -> Dict:
        return dict(self.__dict__)


_PER_HOUR = {"PERIODIC_MINUTE": 60, "PERIODIC_HOUR": 1, "PERIODIC_DAY": 1 / 24}


def wallet_index(wallets: List[Dict]) -> Dict[tuple, float]:
    """(category, provider) -> remaining usable allocation, in unit-hours."""
    out = {}
    for w in wallets:
        pc = w["paysFor"]
        per_hour = _PER_HOUR.get(pc.get("accountingFrequency", "PERIODIC_MINUTE"), 60)
        out[(pc["name"], pc["provider"])] = (w.get("maxUsable") or 0) / per_hour
    return out


def live_machines(job_products: List[Dict], wallets: List[Dict]) -> List[Machine]:
    """Merge /api/jobs/retrieveProducts with /api/accounting/v2/browseWallets."""
    wi = wallet_index(wallets)
    out = []
    for item in job_products:
        p, cat = item["product"], item["product"]["category"]
        if p["name"] in ("terminal", "syncthing"):   # internal products, not machine types
            continue
        # A product priced at `price` units per period uses `price` unit-periods each period, and wallets
        # are kept in unit-periods. So, in unit-hours, the cost per hour is just price x fraction
        # (MIG slices have fraction 1/7).
        frac = p.get("fraction") or {}
        num, den = frac.get("numerator") or 1, frac.get("denominator") or 1
        remaining = wi.get((cat["name"], cat["provider"]))
        out.append(Machine(
            name=p["name"], provider=cat["provider"], category=cat["name"],
            cpu=p.get("cpu") or 0, memory_gb=p.get("memoryInGigs") or 0,
            gpu=p.get("gpu") or 0, gpu_model=p.get("gpuModel") or "", cpu_model=p.get("cpuModel") or "",
            unit=(cat.get("accountingUnit") or {}).get("name", ""),
            price_per_hour=(p.get("price") or 0) * num / den,
            queue=(item.get("support") or {}).get("queueStatus"),
            usable=bool(cat.get("freeToUse")) or (remaining or 0) > 0,
            remaining_units=remaining,
        ))
    out.sort(key=lambda m: (not m.usable, m.provider, m.category, m.gpu, m.cpu))
    return out
