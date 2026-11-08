"""orion_partsel (os/dualboot/orion_partsel.py): the installer's partition choices, fed fake `sfdisk -J` output.
No disk, no QEMU: a Windows-like drive with a gap in its partition numbers, BitLocker data partitions and a tiny
EFI partition, and the ways sfdisk could surprise us."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import orion_partsel as P  # noqa: E402

GB = 1024 * 1024 * 1024 // 512          # sectors per GiB
MS_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
MS_RESERVED = "E3C9E316-0B5C-4DB8-817D-F92DF00215AE"
WIN_RECOVERY = "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"


def table(parts, last=500 * GB, label="gpt"):
    return {"partitiontable": {"label": label, "id": "1F2E3D4C-0000-4000-8000-000000000001", "device": "/dev/nvme0n1",
                               "unit": "sectors", "firstlba": 34, "lastlba": last, "sectorsize": 512,
                               "partitions": parts}}


def part(n, start, size, typ, name="", uuid=None):
    return {"node": f"/dev/nvme0n1p{n}", "start": start, "size": size, "type": typ, "name": name,
            "uuid": uuid or f"0000000{n}-AAAA-4BBB-8CCC-00000000000{n}"}


def windows_disk(esp_mb=100):
    """ESP 1, MSR 2, C: (BitLocker) 3, recovery 5 at the very end, and NOTHING numbered 4: the old installer's trap.
    C: was shrunk, so ~200 GB of free space sits between 3 and 5."""
    esp_s = esp_mb * 2048
    c_start = 2048 + esp_s + 16 * 2048
    c_size = 250 * GB
    rec_size = 1 * GB
    return table([
        part(1, 2048, esp_s, ESP, "EFI system partition"),
        part(2, 2048 + esp_s, 16 * 2048, MS_RESERVED, "Microsoft reserved partition"),
        part(3, c_start, c_size, MS_DATA, "Basic data partition"),
        part(5, 500 * GB - rec_size - 2048, rec_size, WIN_RECOVERY, "Windows recovery"),
    ])


def after_sfdisk(before, plan, nodes=("/dev/nvme0n1p4", "/dev/nvme0n1p6")):
    """What `sfdisk --append` does on that disk: the gap number 4 is taken first, then 6."""
    t = copy.deepcopy(before)
    for (s, z), node in zip(plan, nodes):
        n = node.rsplit("p", 1)[1]
        t["partitiontable"]["partitions"].append({"node": node, "start": s, "size": z,
                                                  "type": "0FC63DAF-8483-4772-8E79-3D69D8477DE4",
                                                  "uuid": f"NEW0000{n}-1111-4222-8333-444444444444"})
    return t


def load(t):
    return P.load_table(json.dumps(t))


# =====================================================================================  plan
def test_free_space_is_found_between_partitions_with_gapped_numbers():
    t = load(windows_disk())
    start, size = P.largest_free(t)
    c = t["parts"][2]
    assert start >= c["start"] + c["size"] and start % 2048 == 0
    rec = t["parts"][3]
    assert start + size <= rec["start"]
    assert size > 200 * GB


def test_plan_never_overlaps_windows():
    t = load(windows_disk())
    plan = P.plan_alongside(t)
    (bs, bz), (rs, rz) = plan
    assert bz == 2 * 1024 * 1024 and rs == bs + bz and rz % 2048 == 0
    for s, z in plan:
        for p in t["parts"]:
            assert not P.overlaps(s, z, p["start"], p["size"])


def test_plan_refuses_too_little_space():
    t = windows_disk()
    t["partitiontable"]["partitions"][2]["size"] = 480 * GB          # C: fills nearly everything
    with pytest.raises(P.Refused, match="shrink the C: drive"):
        P.plan_alongside(load(t))


def test_plan_refuses_mbr_disks():
    t = windows_disk()
    t["partitiontable"]["label"] = "dos"
    with pytest.raises(P.Refused, match="GPT"):
        P.plan_alongside(load(t))


# =====================================================================================  select
def test_select_picks_the_new_partitions_even_when_they_fill_a_numbering_gap():
    before = windows_disk()
    plan = P.plan_alongside(load(before))
    after = after_sfdisk(before, plan)
    assert P.select_new(load(before), load(after), plan) == ["/dev/nvme0n1p4", "/dev/nvme0n1p6"]
    # the old rule ("the two highest numbers") would have picked the recovery partition:
    nodes = sorted((p["node"] for p in after["partitiontable"]["partitions"]), key=lambda n: int(n.rsplit("p", 1)[1]))
    assert nodes[-2:] == ["/dev/nvme0n1p5", "/dev/nvme0n1p6"]


def test_select_refuses_when_nothing_starts_where_planned():
    before = windows_disk()
    plan = P.plan_alongside(load(before))
    shifted = [(plan[0][0] + 2048, plan[0][1]), plan[1]]
    after = after_sfdisk(before, shifted)
    with pytest.raises(P.Refused, match="Couldn't find the new partition"):
        P.select_new(load(before), load(after), plan)


def test_select_refuses_when_an_existing_partition_changed():
    before = windows_disk()
    plan = P.plan_alongside(load(before))
    after = after_sfdisk(before, plan)
    after["partitiontable"]["partitions"][2]["size"] += 2048          # BitLocker C: "moved"
    with pytest.raises(P.Refused, match="moved or changed size"):
        P.select_new(load(before), load(after), plan)


def test_select_refuses_a_reused_name_or_id():
    before = windows_disk()
    plan = P.plan_alongside(load(before))
    after = after_sfdisk(before, plan, nodes=("/dev/nvme0n1p3", "/dev/nvme0n1p6"))
    with pytest.raises(P.Refused, match="already"):
        P.select_new(load(before), load(after), plan)


def test_select_refuses_wrong_sizes_and_surprises():
    before = windows_disk()
    plan = P.plan_alongside(load(before))
    after = after_sfdisk(before, [(plan[0][0], plan[0][1] - 10 * 2048), plan[1]])
    with pytest.raises(P.Refused, match="isn't the size"):
        P.select_new(load(before), load(after), plan)
    after = after_sfdisk(before, plan)
    after["partitiontable"]["partitions"].append(part(7, 499 * GB - 4096, 2048, MS_DATA))
    with pytest.raises(P.Refused, match="unexpected partition"):
        P.select_new(load(before), load(after), plan)


def test_select_refuses_a_plan_on_top_of_bitlocker():
    before = windows_disk()
    c = before["partitiontable"]["partitions"][2]
    plan = [(c["start"], 2 * 1024 * 1024), (c["start"] + 2 * 1024 * 1024, 30 * GB)]
    after = after_sfdisk(before, plan)
    with pytest.raises(P.Refused, match="overlap /dev/nvme0n1p3"):
        P.select_new(load(before), load(after), plan)


# =====================================================================================  ESP and whole disk
def test_tiny_esp_is_explained_and_refused():
    t = load(windows_disk(esp_mb=100))
    assert P.find_esp(t) == "/dev/nvme0n1p1"
    ok, msg = P.esp_check(free_bytes=22 * 1024 * 1024, total_bytes=100 * 1024 * 1024)
    assert not ok and "only 22 MB free of its 100 MB" in msg and "needs 50 MB" in msg and "Nothing was changed" in msg
    assert P.esp_check(free_bytes=200 * 1024 * 1024)[0]


def test_disk_size_check_for_erase():
    assert not P.disk_check(16 * 1024 ** 3)[0]
    assert "only 16 GB" in P.disk_check(16 * 1024 ** 3)[1]
    assert P.disk_check(256 * 1024 ** 3)[0]


def test_command_line(tmp_path):
    before = windows_disk()
    (tmp_path / "b.json").write_text(json.dumps(before))
    script = str(HERE.parent / "orion_partsel.py")
    r = subprocess.run([sys.executable, script, "plan", "--table", str(tmp_path / "b.json")], capture_output=True,
                       text=True)
    assert r.returncode == 0
    bs, bz, rs, rz = map(int, r.stdout.split())
    (tmp_path / "a.json").write_text(json.dumps(after_sfdisk(before, [(bs, bz), (rs, rz)])))
    r = subprocess.run([sys.executable, script, "select", "--before", str(tmp_path / "b.json"), "--after",
                        str(tmp_path / "a.json"), "--plan", f"{bs}:{bz},{rs}:{rz}"], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.split() == ["/dev/nvme0n1p4", "/dev/nvme0n1p6"]
    r = subprocess.run([sys.executable, script, "esp", "--free-bytes", "1000"], capture_output=True, text=True)
    assert r.returncode == 1 and "EFI system partition" in r.stderr
    (tmp_path / "bad.json").write_text("not json")
    r = subprocess.run([sys.executable, script, "plan", "--table", str(tmp_path / "bad.json")], capture_output=True,
                       text=True)
    assert r.returncode == 1 and "couldn't be read" in r.stderr


def test_erase_plan_on_an_empty_gpt_drive():
    t = load(table([], last=256 * GB))
    plan = P.plan_alongside(t, esp_mib=512)
    assert [z for _, z in plan[:2]] == [512 * 2048, 1024 * 2048] and plan[0][0] == 2048
    after = table([{"node": f"/dev/sda{i + 1}", "start": s, "size": z, "type": "x", "uuid": f"u{i}"}
                   for i, (s, z) in enumerate(plan)], last=256 * GB)
    assert P.select_new(t, load(after), plan) == ["/dev/sda1", "/dev/sda2", "/dev/sda3"]
