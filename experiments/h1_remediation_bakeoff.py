"""H1 REMEDIATION BAKE-OFF — thiết kế và thí nghiệm, KHÔNG vá sản phẩm.

Ba hợp đồng khắc phục được dựng thành *wrapper* quanh `ProjectionOutbox`.
Không một dòng nào trong `bio_agent_os/` bị sửa. Mục đích là lấy bằng chứng
thực thi trước khi chọn, chứ không phải chọn rồi đi tìm bằng chứng.

Hợp đồng mục tiêu (đã chốt): KHÔNG cần strict FIFO. Cần:
  1. healthy job cuối cùng phải tiến triển
  2. một job độc không được độc chiếm cơ hội acquisition
  3. hạ tầng sập không được âm thầm vứt việc
  4. abandonment lặp lại bất thường phải quan sát và cách ly được
  5. drain phải có kết cục hữu hạn

Ba ngữ nghĩa được đếm TÁCH BIỆT, vì gộp chúng chính là gốc của bản vá sai:
  delivery_attempts   — số lượt được GIAO (claim). Đây là thứ `attempts` đang đếm.
  processing_failures — handler ĐÃ chạy và hỏng, `fail()` được gọi.
  abandoned_leases    — đã claim, worker chết trước khi kịp `fail()`.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.reconciliation_worker import worker_for

CLOCK0, STEP, LEASE = 2_000.0, 1.0, 0.5
MAX_ROUNDS = int(__import__('os').getenv('H1_ROUNDS', '40'))
OPERATOR_ROUND = int(__import__('os').getenv('H1_OPERATOR_ROUND', '20'))
QUARANTINE_AFTER = 3          # abandoned_leases, KHÔNG phải delivery_attempts

OK, ABANDON, FAIL = "ok", "abandon", "fail"


# --------------------------------------------------------------------------
# ngữ nghĩa tách biệt
# --------------------------------------------------------------------------
@dataclass
class Ledger:
    delivery_attempts: dict = field(default_factory=dict)
    processing_failures: dict = field(default_factory=dict)
    abandoned_leases: dict = field(default_factory=dict)
    quarantined: set = field(default_factory=set)
    # Cach ly RUT LAI duoc khong xoa cai gia cua no: moi lan cach ly
    # mot viec lanh la mot lan operator phai ngo den. Dem 'da tung',
    # khong dem 'con dang'.
    ever_quarantined: set = field(default_factory=set)
    completed_round: dict = field(default_factory=dict)

    def bump(self, book: str, job_id: str) -> int:
        d = getattr(self, book)
        d[job_id] = d.get(job_id, 0) + 1
        return d[job_id]


# --------------------------------------------------------------------------
# ba hợp đồng, dựng bằng wrapper
# --------------------------------------------------------------------------
class Contract:
    """BASELINE — sản phẩm như đang chạy. Không can thiệp gì."""
    name = "BASELINE"
    yields = False
    quarantines = False

    def __init__(self, outbox, ledger: Ledger):
        self.outbox, self.ledger = outbox, ledger

    def _expired(self, now: float) -> list:
        return [(r[0], r[1]) for r in self.outbox.conn.execute(
            "SELECT job_id, attempts FROM projection_outbox "
            "WHERE status='in_progress' AND locked_at IS NOT NULL "
            "AND locked_at <= ?", (now - LEASE,))]

    def before_claim(self, now: float) -> None:
        # BASELINE cũng chạy vòng này: đếm là QUAN SÁT, không phải chính sách.
        # Chỉ hai nhánh dưới mới là can thiệp.
        for job_id, _ in self._expired(now):
            n = self.ledger.bump("abandoned_leases", job_id)

            if self.quarantines and n >= QUARANTINE_AFTER:
                # Cách ly = rút khỏi acquisition + ghi sổ. KHÔNG phải dead_letter:
                # việc vẫn còn nguyên, operator khôi phục được.
                self.ledger.quarantined.add(job_id)
                self.ledger.ever_quarantined.add(job_id)
                self.outbox.conn.execute(
                    "UPDATE projection_outbox SET status='pending', "
                    "locked_by=NULL, locked_at=NULL, available_at=? "
                    "WHERE job_id=?", (float("inf"), job_id))
                continue

            if self.yields:
                # Nhường lượt: quay lại hàng đợi với backoff, để đoàn phía sau
                # bước vào. Việc KHÔNG bị vứt đi.
                backoff = min(2.0 ** n, 300.0)
                self.outbox.conn.execute(
                    "UPDATE projection_outbox SET status='pending', "
                    "locked_by=NULL, locked_at=NULL, available_at=? "
                    "WHERE job_id=?", (now + backoff, job_id))
        self.outbox.conn.commit()

    def operator_restore(self, job_id: str, now: float) -> None:
        self.ledger.quarantined.discard(job_id)
        self.outbox.conn.execute(
            "UPDATE projection_outbox SET status='pending', available_at=? "
            "WHERE job_id=?", (now, job_id))
        self.outbox.conn.commit()


class FairScheduling(Contract):
    """A — nhường lượt + backoff khi lease mất. Không cách ly."""
    name, yields, quarantines = "A fair-scheduling", True, False


class Quarantine(Contract):
    """B — cách ly job bị bỏ rơi lặp lại. Không nhường lượt."""
    name, yields, quarantines = "B quarantine", False, True


class FairAndQuarantine(Contract):
    """C — nhường lượt bảo vệ throughput, cách ly bảo vệ hội tụ."""
    name, yields, quarantines = "C fairness+quarantine", True, True


CONTRACTS = [Contract, FairScheduling, Quarantine, FairAndQuarantine]


# --------------------------------------------------------------------------
# fixtures — mỗi job: (needle, nội dung, tenant, hành vi)
# --------------------------------------------------------------------------
def flaky(k):
    return ("flaky", k)


FIXTURES = {
    "healthy only": [
        ("An", "Nguyễn An sống ở Huế.", "t1", OK),
        ("Bảo", "Trần Bảo sống ở Vinh.", "t1", OK),
    ],
    "1 poison + healthy sau": [
        ("Yến", "Số của Hoàng Yến là 0977123456.", "t1", ABANDON),
        ("Cường", "Bùi Cường sống ở Hà Nội.", "t1", OK),
        ("Dung", "Lê Dung sống ở Đà Nẵng.", "t1", OK),
    ],
    "nhieu poison + healthy": [
        ("Yến", "Số của Hoàng Yến là 0977123456.", "t1", ABANDON),
        ("Kha", "Số của Đỗ Kha là 0988777666.", "t1", ABANDON),
        ("Cường", "Bùi Cường sống ở Hà Nội.", "t1", OK),
        ("Dung", "Lê Dung sống ở Đà Nẵng.", "t1", OK),
    ],
    "worker crash chap chon": [
        ("Cường", "Bùi Cường sống ở Hà Nội.", "t1", flaky(1)),
        ("Dung", "Lê Dung sống ở Đà Nẵng.", "t1", flaky(2)),
        ("Ế", "Vũ Ế sống ở Cần Thơ.", "t1", flaky(3)),
        ("Phong", "Ngô Phong sống ở Nha Trang.", "t1", flaky(4)),
    ],
    "handler hong tuong minh": [
        ("Yến", "Số của Hoàng Yến là 0977123456.", "t1", FAIL),
        ("Cường", "Bùi Cường sống ở Hà Nội.", "t1", OK),
    ],
    "hai tenant": [
        ("Yến", "Số của Hoàng Yến là 0977123456.", "t1", ABANDON),
        ("Cường", "Bùi Cường sống ở Hà Nội.", "t2", OK),
        ("Dung", "Lê Dung sống ở Đà Nẵng.", "t2", OK),
    ],
    "restart storm": [
        ("Cường", "Bùi Cường sống ở Hà Nội.", "t1", flaky(1)),
        ("Dung", "Lê Dung sống ở Đà Nẵng.", "t1", flaky(1)),
        ("Ế", "Vũ Ế sống ở Cần Thơ.", "t1", flaky(1)),
    ],
    "operator retry": [
        ("Yến", "Số của Hoàng Yến là 0977123456.", "t1", flaky(6)),
        ("Cường", "Bùi Cường sống ở Hà Nội.", "t1", OK),
    ],
}


@dataclass
class Run:
    fixture: str
    contract: str
    ledger: Ledger
    plan: dict
    healthy_done: int
    healthy_total: int
    rounds_to_all_healthy: int | None
    claimable_left: int
    poison_isolated: bool
    lost: list
    operator_recovered: bool | None


def _build(tmp: Path, fixture: list):
    memory_os = MemoryOS(tmp, projection_mode="outbox")
    for _, content, tenant, _b in fixture:
        memory_os.observe(tenant_id=tenant, actor="a", source="u",
                          content=content, workspace_id="w1")
    # Thứ tự nạp phải hiển ngôn: time.time() có resolution 15.6 ms nên hai
    # observe() liên tiếp có thể trùng created_at, và ORDER BY created_at
    # không tiebreaker thì thứ tự trả về không xác định.
    ids = {}
    for offset, (needle, _c, _t, _b) in enumerate(fixture):
        evs = {str(e) for e, p in memory_os.events.conn.execute(
            "SELECT event_id, payload_json FROM cognitive_events")
            if needle in str(p or "")}
        row = next(dict(r) for r in memory_os.memories.conn.execute(
            "SELECT job_id, event_id FROM projection_outbox")
            if str(dict(r)["event_id"]) in evs)
        memory_os.memories.conn.execute(
            "UPDATE projection_outbox SET created_at=?, available_at=? "
            "WHERE job_id=?", (1000.0 + offset, 1000.0 + offset,
                               row["job_id"]))
        ids[needle] = row["job_id"]
    memory_os.memories.conn.commit()
    return memory_os, ids


def _materialised(memory_os, needle: str) -> bool:
    return any(needle in str(r[0]) for r in memory_os.memories.conn.execute(
        "SELECT content FROM cognitive_memories"))


def _status(outbox, job_id: str) -> str:
    row = outbox.conn.execute(
        "SELECT status FROM projection_outbox WHERE job_id=?", (job_id,)).fetchone()
    return row[0] if row else "?"


def run_one(tmp: Path, fixture_name: str, fixture: list, contract_cls) -> Run:
    memory_os, ids = _build(tmp, fixture)
    try:
        plan = {ids[n]: b for n, _c, _t, b in fixture}
        needle_of = {ids[n]: n for n, _c, _t, b in fixture}
        # "healthy" = việc RỒI SẼ xong nếu hàng đợi công bằng.
        healthy = [ids[n] for n, _c, _t, b in fixture
                   if b == OK or (isinstance(b, tuple) and b[0] == "flaky")]
        poison = [ids[n] for n, _c, _t, b in fixture if b == ABANDON]
        flakies = [ids[n] for n, _c, _t, b in fixture
                   if isinstance(b, tuple) and b[0] == "flaky"]

        ledger = Ledger()
        worker = worker_for(memory_os, lease_seconds=LEASE)
        contract = contract_cls(worker.outbox, ledger)
        deaths: dict = {}
        now, rounds_to_all = CLOCK0, None
        restored, restored_ids = False, set()

        for rnd in range(MAX_ROUNDS):
            contract.before_claim(now)

            # Operator can thiệp: ở vòng 20, khôi phục việc đang bị cách ly.
            if rnd == OPERATOR_ROUND and ledger.quarantined and not restored:
                for job_id in list(ledger.quarantined):
                    contract.operator_restore(job_id, now)
                    ledger.abandoned_leases[job_id] = 0
                    # Operator retry thật = ĐÃ SỬA NGUYÊN NHÂN rồi mới thử lại.
                    # Bản đầu chỉ reset bộ đếm mà để hạ tầng vẫn hỏng, nên trục
                    # này đo nhầm câu "một lần restore có đủ không" thay vì câu
                    # cần hỏi: "cách ly có đảo ngược được, việc có còn nguyên?"
                    deaths[job_id] = 10 ** 6
                    restored_ids.add(job_id)
                restored = True

            jobs = worker.outbox.claim(worker_id="w", limit=1, now=now,
                                       lease_seconds=LEASE, tenant_id=None)
            for job in jobs:
                jid = job.job_id
                ledger.bump("delivery_attempts", jid)
                behaviour = plan[jid]
                if isinstance(behaviour, tuple):
                    behaviour = (ABANDON if deaths.get(jid, 0) < behaviour[1]
                                 else OK)

                if behaviour == ABANDON:
                    deaths[jid] = deaths.get(jid, 0) + 1
                    continue                      # lease treo: worker chết
                if behaviour == FAIL:
                    ledger.bump("processing_failures", jid)
                    worker.outbox.fail(jid, "handler hong that", now=now)
                    continue
                worker.process(job)
                if _status(worker.outbox, jid) == "in_progress":
                    worker.outbox.complete(jid, now=now)
                ledger.completed_round.setdefault(jid, rnd)

            if rounds_to_all is None and all(
                    _materialised(memory_os, needle_of[j]) for j in healthy):
                rounds_to_all = rnd + 1
            now += STEP

        claimable = worker.outbox.conn.execute(
            "SELECT COUNT(*) FROM projection_outbox WHERE available_at <= ? "
            "AND status IN ('pending','in_progress')", (now + 1e9,)).fetchone()[0]
        claimable_left = worker.outbox.conn.execute(
            "SELECT COUNT(*) FROM projection_outbox "
            "WHERE status IN ('pending','in_progress') "
            "AND available_at < ?", (float("inf"),)).fetchone()[0]
        done = sum(1 for j in healthy if _materialised(memory_os, needle_of[j]))
        lost = [needle_of[j] for j in healthy
                if _status(worker.outbox, j) in ("dead_letter", "skipped")]
        isolated = bool(poison) and all(
            j in ledger.quarantined or _status(worker.outbox, j) == "dead_letter"
            for j in poison)
        # Trục này PHẢI hỏi: việc được operator khôi phục có xong không.
        # Bản đầu viết bằng all() trên một tập rỗng, nên trả True vô căn cứ
        # — metric sai, không phải hệ thống sai.
        op = None
        if restored_ids:
            op = all(_materialised(memory_os, needle_of[j])
                     for j in restored_ids)
        return Run(fixture_name, contract_cls.name, ledger, plan, done,
                   len(healthy), rounds_to_all, claimable_left, isolated, lost, op)
    finally:
        memory_os.close()


def main() -> None:
    import tempfile
    root = Path(tempfile.mkdtemp())
    results: dict = {}
    for ci, contract_cls in enumerate(CONTRACTS):
        for fi, (name, fixture) in enumerate(FIXTURES.items()):
            tmp = root / f"c{ci}f{fi}.db"
            results[(contract_cls.name, name)] = run_one(
                tmp, name, fixture, contract_cls)

    width = max(len(n) for n in FIXTURES) + 2
    for contract_cls in CONTRACTS:
        cn = contract_cls.name
        print(f"\n{'='*78}\n{cn}\n{'='*78}")
        print(f"{'fixture':<{width}} {'healthy':<9} {'vong':<6} {'con lai':<8} "
              f"{'poison':<9} {'mat':<6} deliveries/failures/abandons")
        for name in FIXTURES:
            r = results[(cn, name)]
            dv = sum(r.ledger.delivery_attempts.values())
            pf = sum(r.ledger.processing_failures.values())
            ab = sum(r.ledger.abandoned_leases.values())
            has_poison = any(b == ABANDON for _n, _c, _t, b in FIXTURES[name])
            print(f"{name:<{width}} {r.healthy_done}/{r.healthy_total:<7} "
                  f"{str(r.rounds_to_all_healthy or '-'):<6} "
                  f"{r.claimable_left:<8} "
                  f"{('CACH LY' if r.poison_isolated else 'con chay') if has_poison else '-':<9} "
                  f"{(','.join(r.lost) or '-'):<6} {dv}/{pf}/{ab}")

    print(f"\n{'='*78}\nTONG HOP 8 TRUC\n{'='*78}")
    hdr = f"{'truc':<26}" + "".join(f"{c.name.split()[0]:<12}" for c in CONTRACTS)
    print(hdr)
    rows = {}
    for contract_cls in CONTRACTS:
        cn = contract_cls.name
        rs = [results[(cn, n)] for n in FIXTURES]
        flaky_ids = {}
        quarantined_flaky = 0
        total_flaky = 0
        for n, fx in FIXTURES.items():
            r = results[(cn, n)]
            ids_flaky = [i for i, b in r.plan.items()
                         if isinstance(b, tuple) and b[0] == "flaky"]
            total_flaky += len(ids_flaky)
            quarantined_flaky += sum(1 for i in ids_flaky
                                     if i in r.ledger.ever_quarantined)
        rows.setdefault("healthy-job progress", []).append(
            f"{sum(r.healthy_done for r in rs)}/{sum(r.healthy_total for r in rs)}")
        rows.setdefault("data-loss (viec mat)", []).append(
            str(sum(len(r.lost) for r in rs)))
        pois = [r for r, n in zip(rs, FIXTURES)
                if any(b == ABANDON for _a, _b, _c, b in FIXTURES[n])]
        rows.setdefault("poison isolation", []).append(
            f"{sum(1 for r in pois if r.poison_isolated)}/{len(pois)}")
        rows.setdefault("drain convergence", []).append(
            f"{sum(1 for r in rs if r.claimable_left == 0)}/{len(rs)}")
        tf = results[(cn, "hai tenant")]
        rows.setdefault("tenant fairness", []).append(
            f"{tf.healthy_done}/{tf.healthy_total}")
        cr = results[(cn, "restart storm")]
        rows.setdefault("crash recovery", []).append(
            f"{cr.healthy_done}/{cr.healthy_total}")
        opr = results[(cn, "operator retry")]
        rows.setdefault("operator recovery", []).append(
            "n/a" if opr.operator_recovered is None
            else ("co" if opr.operator_recovered else "KHONG"))
        rows.setdefault("false quarantine", []).append(
            f"{quarantined_flaky}/{total_flaky}")

    for axis, vals in rows.items():
        print(f"{axis:<26}" + "".join(f"{v:<12}" for v in vals))


if __name__ == "__main__":
    main()
