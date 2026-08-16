"""Labelled cases for `classify_relation`, from two sources on purpose.

**Generated** from the lifetime world, which already knows what it did. Its
`EventKind` is the answer key and the classifier never sees it — it sees only
the sentence a person would have said. That gives four of the six relations at
realistic volume, with the value collisions and the timing a hand-written table
would smooth over.

**Curated** for the two the world does not contain, and for the edges that a
generator has no reason to produce:

    COEXIST     the world has no multi-valued predicate. Anh's own example —
                liking Python does not stop you liking Rust — is not in it
    UNKNOWN     the world always knows what it meant. Real input does not
    corrected birth date          the case that breaks `old != new -> supersede`
    correction backdated          later in belief time, earlier in world time
    two values, no order          disagreement without a change

Each curated case carries `why`, and the reason is not decoration: a case whose
label cannot be argued for in one sentence is a case that will later be
"fixed" by moving the label. Fifteen of them are here because a specific rule
would pass without them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from bio_agent_os.cognitive.aspect_resolver import Predicate
from bio_agent_os.cognitive.relations import Claim, Event, Relation


@dataclass(frozen=True)
class Case:
    name: str
    old: Claim
    new: Event
    expected: Relation
    why: str
    source: str = "curated"

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "expected": self.expected.value,
                "why": self.why, "source": self.source,
                "old": self.old.value, "new": self.new.value,
                "predicate": str(self.old.predicate)}


def _claim(value: str, predicate: Predicate, *, at: str,
           subject: str = "Trần Hà") -> Claim:
    return Claim(value=value, predicate=predicate, subject=subject,
                 observed_at=at, valid_from=at, claim_id=f"c-{value}-{at}")


def _event(value: str, text: str, predicate: Predicate, *, at: str,
           subject: str = "Trần Hà") -> Event:
    return Event(value=value, text=text, predicate=predicate, subject=subject,
                 observed_at=at, event_id=f"e-{value}-{at}")


CURATED: tuple[Case, ...] = (
    # -- REPEAT ------------------------------------------------------------
    Case("repeat_marked",
         _claim("trưởng nhóm", Predicate.ROLE, at="0100"),
         _event("trưởng nhóm", "Nhắc lại, Trần Hà đang giữ chức trưởng nhóm.",
                Predicate.ROLE, at="0200"),
         Relation.REPEAT,
         "cùng giá trị; nhắc lại không đổi thứ đang đúng, chỉ đổi độ tươi"),
    Case("repeat_unmarked_after_long_gap",
         _claim("Hà Nội", Predicate.LOCATION, at="0100"),
         _event("Hà Nội", "Trần Hà sống ở Hà Nội.", Predicate.LOCATION,
                at="0900"),
         Relation.REPEAT,
         "khoảng cách thời gian không biến một lời nhắc lại thành thay đổi"),
    Case("repeat_on_immutable",
         _claim("12/03/1990", Predicate.BIRTHDAY, at="0100"),
         _event("12/03/1990", "Trần Hà sinh ngày 12/03/1990.",
                Predicate.BIRTHDAY, at="0500"),
         Relation.REPEAT,
         "bất biến mà trùng giá trị thì vẫn là nhắc lại, không phải xung đột"),

    # -- CORRECT -----------------------------------------------------------
    Case("correct_birthday",
         _claim("12/03/1990", Predicate.BIRTHDAY, at="0100"),
         _event("12/03/1991",
                "Xin lỗi, thông tin trước là sai, Trần Hà sinh ngày 12/03/1991.",
                Predicate.BIRTHDAY, at="0300"),
         Relation.CORRECT,
         "không ai sinh hai lần; 1990 phải không trả lời câu hỏi nào, ở mọi "
         "as_of. Đây là ca làm gãy luật 'khác thì thay thế'"),
    Case("correct_role",
         _claim("trưởng nhóm", Predicate.ROLE, at="0100"),
         _event("trưởng phòng",
                "Đính chính: thông tin trước là sai, Trần Hà đang giữ chức "
                "trưởng phòng.", Predicate.ROLE, at="0400"),
         Relation.CORRECT,
         "vị từ đổi được, nhưng người nói đã bảo cái cũ SAI chứ không phải "
         "đã đổi — hai thứ đó cho hai lịch sử khác nhau"),
    Case("correct_backdated",
         _claim("Locaith", Predicate.EMPLOYER, at="0500"),
         Event(value="An Phát",
               text="Thực ra hồi tháng 1 Trần Hà đã làm ở An Phát rồi.",
               predicate=Predicate.EMPLOYER, subject="Trần Hà",
               observed_at="0800", valid_from="0100", event_id="e-backdated"),
         Relation.CORRECT,
         "muộn hơn về thời điểm BIẾT, sớm hơn về thời điểm ĐÚNG. Xếp theo "
         "valid_from sẽ đọc thành xung đột; đây đúng là đính chính"),

    # -- SUPERSEDE ---------------------------------------------------------
    Case("supersede_marked",
         _claim("nhân viên kinh doanh", Predicate.ROLE, at="0100"),
         _event("trưởng nhóm",
                "Từ hôm nay, Trần Hà đang giữ chức trưởng nhóm.",
                Predicate.ROLE, at="0300"),
         Relation.SUPERSEDE,
         "cái cũ TỪNG đúng và vẫn phải trả lời được câu hỏi lịch sử"),
    Case("unmarked_value_change_volatile",
         _claim("32 triệu", Predicate.SALARY, at="0100"),
         _event("40 triệu", "Lương của Trần Hà là 40 triệu.",
                Predicate.SALARY, at="0700"),
         Relation.UNKNOWN,
         "ĐÍNH CHÍNH NHÃN, 15/08/2026. Ca này ban đầu ghi SUPERSEDE với lý "
         "do 'thiếu dấu hiệu thì hạ độ tin, không đổi kết luận'. Đo trên 312 "
         "ca dùng cách nói giữ lại: luật đó cho silent_conflict_rate 1.000 "
         "và forced_transition_rate 1.000 — 'Có tin đồn là 40 triệu', 'Lúc "
         "nãy tôi ghi lộn: 40 triệu' và 'Từ hôm nay 40 triệu' đến với hình "
         "dạng y hệt nhau. Nhãn đổi vì bằng chứng, không vì code; nhãn cũ "
         "giữ trong dòng này để so được"),
    Case("supersede_slow_attribute",
         _claim("Hà Nội", Predicate.LOCATION, at="0100"),
         _event("Huế", "Trần Hà chuyển đến Huế.", Predicate.LOCATION,
                at="0600"),
         Relation.SUPERSEDE,
         "chậm không có nghĩa là bất biến"),

    # -- COEXIST -----------------------------------------------------------
    Case("coexist_preferences",
         _claim("Python", Predicate.PREFERENCE, at="0100"),
         _event("Rust", "Trần Hà thích Rust.", Predicate.PREFERENCE,
                at="0400"),
         Relation.COEXIST,
         "thích Rust không làm hết thích Python. Giết cái cũ chỉ vì cùng vị "
         "từ là mất một sự thật không ai rút lại"),
    Case("coexist_not_overridden_by_change_marker",
         _claim("Python", Predicate.PREFERENCE, at="0100"),
         _event("Go", "Bây giờ Trần Hà thích Go.", Predicate.PREFERENCE,
                at="0400"),
         Relation.COEXIST,
         "'bây giờ' nói về thứ mới, không rút lại thứ cũ, khi vị từ nhận "
         "nhiều giá trị"),

    # -- CONFLICT ----------------------------------------------------------
    Case("conflict_alternative_source",
         _claim("trưởng nhóm", Predicate.ROLE, at="0100"),
         _event("giám đốc kỹ thuật",
                "Theo một nguồn khác, Trần Hà đang giữ chức giám đốc kỹ thuật.",
                Predicate.ROLE, at="0500"),
         Relation.CONFLICT,
         "nguồn thứ hai bất đồng, chưa ai phân xử — chọn một bên trong im "
         "lặng là thứ silent_conflict_rate đo"),
    Case("conflict_immutable_unmarked",
         _claim("12/03/1990", Predicate.BIRTHDAY, at="0100"),
         _event("05/07/1988", "Trần Hà sinh ngày 05/07/1988.",
                Predicate.BIRTHDAY, at="0600"),
         Relation.CONFLICT,
         "ngày sinh không đổi theo thời gian, nên một trong hai sai — và "
         "không có gì ở đây nói cái nào"),
    Case("conflict_rumour_about_a_change",
         _claim("Locaith", Predicate.EMPLOYER, at="0100"),
         _event("Đại Việt",
                "Nghe nói bây giờ Trần Hà làm việc tại công ty Đại Việt.",
                Predicate.EMPLOYER, at="0500"),
         Relation.CONFLICT,
         "tin đồn VỀ một thay đổi không phải một thay đổi; dấu hiệu nguồn "
         "khác phải thắng dấu hiệu thay đổi"),
    Case("conflict_same_time",
         Claim(value="Hà Nội", predicate=Predicate.LOCATION, subject="Trần Hà",
               observed_at="0300", valid_from="0300", claim_id="c-same"),
         Event(value="Đà Nẵng", text="Trần Hà sống ở Đà Nẵng.",
               predicate=Predicate.LOCATION, subject="Trần Hà",
               observed_at="0300", event_id="e-same"),
         Relation.CONFLICT,
         "cùng thời điểm quan sát thì không có cái nào thay thế cái nào"),
    Case("conflict_older_statement",
         _claim("Huế", Predicate.LOCATION, at="0700"),
         _event("Hà Nội", "Trần Hà sống ở Hà Nội.", Predicate.LOCATION,
                at="0200"),
         Relation.CONFLICT,
         "một câu cũ hơn không thay thế được cái đang giữ; nó bất đồng"),

    # -- UNKNOWN -----------------------------------------------------------
    Case("unknown_no_times",
         Claim(value="Hà Nội", predicate=Predicate.LOCATION, subject="Trần Hà",
               claim_id="c-untimed"),
         Event(value="Huế", text="Trần Hà sống ở Huế.",
               predicate=Predicate.LOCATION, subject="Trần Hà",
               event_id="e-untimed"),
         Relation.UNKNOWN,
         "không xếp được thứ tự thì chưa CHỨNG MINH được hai câu bất đồng — "
         "chúng có thể là một sự thật nói ở hai lúc. Gọi là CONFLICT là "
         "khẳng định một điều không ai chứng minh"),
    Case("unknown_different_predicate",
         _claim("Hà Nội", Predicate.LOCATION, at="0100"),
         _event("trưởng nhóm", "Trần Hà đang giữ chức trưởng nhóm.",
                Predicate.ROLE, at="0300"),
         Relation.UNKNOWN,
         "hai câu về hai chuyện khác nhau không nói gì về nhau"),
    Case("unknown_different_subject",
         _claim("Hà Nội", Predicate.LOCATION, at="0100", subject="Trần Hà"),
         _event("Huế", "Trần Thảo sống ở Huế.", Predicate.LOCATION,
                at="0300", subject="Trần Thảo"),
         Relation.UNKNOWN,
         "cùng vị từ, khác người — thế giới này chia năm số điện thoại cho "
         "hai mươi người, nên đây không phải ca hiếm"),
    Case("unknown_undeclared_predicate",
         Claim(value="A", predicate=Predicate.UNKNOWN, subject="Trần Hà",
               observed_at="0100", claim_id="c-undeclared"),
         Event(value="B", text="Trần Hà thế nào đó.",
               predicate=Predicate.UNKNOWN, subject="Trần Hà",
               observed_at="0300", event_id="e-undeclared"),
         Relation.UNKNOWN,
         "chưa ai khai báo vị từ này nhận một hay nhiều giá trị, nên chưa "
         "biết hai câu có bất đồng hay không — im lặng mượn dòng mặc định "
         "rồi gọi là CONFLICT là báo một mâu thuẫn chưa được chứng minh"),
)


#: Rewrites of the world's fixed prefixes using wording the classifier has
#: **never been shown**. Held out on purpose.
#:
#: The world emits one fixed opening per event kind, so "Đính chính:" *is*
#: CORRECT and five memorised strings score 1.000 — the first run of
#: `relation_benchmark` did exactly that on 645 cases.
#:
#: The obvious repair is worthless. Rewriting the prefixes using phrases from
#: the classifier's own marker lists is typing the same strings twice; it was
#: written, it scored 1.000 again, and it proved nothing. So every phrase below
#: is checked to be absent from `relations.CORRECTION_MARKERS`,
#: `CHANGE_MARKERS` and `ALTERNATIVE_SOURCE_MARKERS` — see
#: `test_relations.py::test_held_out_wording_is_actually_held_out`, which fails
#: if one of these ever leaks into the classifier.
#:
#: The expected result is **not** that the classifier still gets these right.
#: It is that it fails *safely*: an unrecognised correction must not become a
#: confident supersession, because that keeps a retracted value as historical
#: truth. Both numbers are reported.
HELDOUT_PARAPHRASES: dict[str, tuple[str, ...]] = {
    "Nhắc lại, ": ("Y như cũ: ", "Không có gì mới — ", "Tôi khẳng định: ",
                   "Vẫn thế thôi: "),
    "Từ hôm nay, ": ("Có thay đổi nhân sự: ", "Vừa có quyết định mới: ",
                     "Sang giai đoạn mới: ", "Cập nhật tình hình: "),
    "Đính chính: thông tin trước là sai, ": (
        # "Tôi đưa nhầm số liệu" was here and had to go: it contains "nhầm",
        # which is in `CORRECTION_MARKERS`. One of four held-out corrections
        # was therefore recognised, and the held-out score was that much
        # kinder than it looked. Caught by the guard test, not by reading.
        "Lúc nãy tôi ghi lộn: ", "Bỏ cái trước đi nhé — ",
        "Số liệu vừa rồi chưa chuẩn, ", "Cái vừa rồi không đúng: "),
    "Theo một nguồn khác, ": ("Có tin đồn là ", "Chưa kiểm chứng: ",
                              "Ai đó vừa kể rằng ", "Hình như "),
}


def _paraphrase(text: str, index: int) -> str:
    """Swap the opening for held-out wording that means the same thing.

    Deterministic in `index` rather than random: a corpus that differs between
    two runs cannot support a before/after comparison, and comparing two runs
    is the whole point of keeping the number.
    """
    for prefix, alternatives in HELDOUT_PARAPHRASES.items():
        if text.startswith(prefix):
            return alternatives[index % len(alternatives)] + text[len(prefix):]
    return text


def from_world(events: Iterable, ledger, people) -> list[Case]:
    """Cases the world already knows the answer to.

    Only `EventKind` decides the label, and only the sentence reaches the
    classifier. The mapping is one line each and none of it is a judgement
    call:

        REPEAT     -> REPEAT       an existing value mentioned again
        SUPERSEDE  -> SUPERSEDE    a new value replaces the old from a moment
        CORRECT    -> CORRECT      the old value was never true
        CONTRADICT -> CONFLICT     a second source disagrees, unresolved

    `ASSERT` produces no case: the world only asserts into an empty slot, so
    there is no prior claim for a relation to hold between. `FORGET` and
    `NOISE` are not relations either.
    """
    from bio_agent_os.evals.lifetime_world import EventKind

    label = {EventKind.REPEAT: Relation.REPEAT,
             EventKind.SUPERSEDE: Relation.SUPERSEDE,
             EventKind.CORRECT: Relation.CORRECT,
             EventKind.CONTRADICT: Relation.CONFLICT}

    names = {p.subject_id: p.name for p in people}
    predicate_of = {p.attribute: p for p in Predicate if p.attribute}

    #: The value standing in each slot as the events are walked, which is what
    #: the classifier would be comparing against in production.
    held: dict[tuple[str, str], tuple[str, int]] = {}
    cases: list[Case] = []

    for event in events:
        if event.subject_id is None or event.attribute is None:
            continue
        slot = (event.subject_id, event.attribute)
        predicate = predicate_of.get(event.attribute, Predicate.UNKNOWN)
        current = held.get(slot)
        value = getattr(event, "value", None)

        if event.kind is EventKind.FORGET:
            held.pop(slot, None)
            continue
        if value is None:
            continue

        expected = label.get(event.kind)
        if expected is not None and current is not None:
            old_value, old_tick = current
            # Every other case is reworded. Half the corpus keeping the
            # generator's exact opening would let a five-string lookup pass;
            # all of it reworded would stop measuring the wording the world
            # actually produces.
            reworded = len(cases) % 2 == 1
            text = _paraphrase(event.text, len(cases)) if reworded else event.text
            cases.append(Case(
                name=f"world_{event.kind.value}_{slot[0]}_{slot[1]}_{event.tick}"
                     + ("_heldout" if reworded else ""),
                old=Claim(value=old_value, predicate=predicate,
                          subject=names.get(event.subject_id, ""),
                          observed_at=f"{old_tick:05d}",
                          valid_from=f"{old_tick:05d}",
                          claim_id=f"{slot[0]}/{slot[1]}@{old_tick}"),
                new=Event(value=value, text=text, predicate=predicate,
                          subject=names.get(event.subject_id, ""),
                          observed_at=f"{event.tick:05d}",
                          event_id=f"e{event.tick}"),
                expected=expected,
                why=f"thế giới ghi kind={event.kind.value}"
                    + (" (cách nói bị giữ lại khỏi classifier)" if reworded
                       else ""),
                source="world_heldout" if reworded else "world"))

        # A contradiction leaves the held value alone — that is what makes it a
        # contradiction rather than a change.
        if event.kind is not EventKind.CONTRADICT:
            held[slot] = (value, event.tick)

    return cases


def all_cases(events=None, ledger=None, people=None) -> list[Case]:
    cases = list(CURATED)
    if events is not None:
        cases.extend(from_world(events, ledger, people))
    return cases


__all__ = ["CURATED", "Case", "all_cases", "from_world"]
