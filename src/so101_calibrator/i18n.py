"""English / Turkish texts of the interactive front ends.

Text marked *like this* is the important word of a sentence: the TUI draws it in the accent colour,
`plain()` drops the marks. Check messages from checks.py are English only for now.
"""

from __future__ import annotations

import os

LANGS = ("en", "tr")

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "app.title": "SO-101 CALIBRATION",
        "menu.follower": "Follower", "menu.follower.sub": "12 V arm",
        "menu.leader": "Leader", "menu.leader.sub": "5 V arm",
        "menu.teleop": "Test both arms", "menu.teleop.sub": "leader → follower",
        "status.none": "not calibrated", "status.inactive": "no active version", "status.active": "v{v} · {ago}",
        "ago.now": "just now", "ago.min": "{n} min ago", "ago.h": "{n} h ago", "ago.d": "{n} d ago",
        "flow.calibrate": "Calibrate {role}", "flow.verify": "Verify {role}", "flow.teleop": "Teleop test",
        "step.port": "USB port", "step.motors": "Motors", "step.power": "Power", "step.homing": "Middle pose",
        "step.ranges": "Ranges", "step.save": "Save", "step.match": "Compare",
        "step.pose": "Middle pose check", "step.teleop": "Teleop",
        "hint.port.ready": "Press ⏎, then *unplug* the {role}'s USB cable",
        "hint.port.unplug": "*Unplug* the {role}'s USB cable now",
        "hint.port.replug": "Now *plug it back in*",
        "hint.port.ambiguous": "Several ports vanished: plug in *only this arm*",
        "hint.port.known": "*{role}*: {port}",
        "sub.port.known": "Its USB cable and power plugged in.",
        "hint.motors": "Power on, then *scan* the motors",
        "hint.power": "The supply should be *{lo}–{hi} V*",
        "hint.homing": "Put every joint in the *middle*",
        "hint.ranges": "Move each joint to *both ends*",
        "hint.save": "*Save* the calibration",
        "hint.match": "Compare the motors with the *saved* calibration",
        "hint.pose": "Put the arm in the *middle* pose again",
        "hint.teleop.ready": "Fold both arms into the *rest pose*",
        "hint.teleop": "Move the *leader* slowly",
        "sub.port": "Only this arm's board plugged in.",
        "sub.motors": "Motors 1–6 should answer.",
        "sub.power": "Follower 12 V, leader 5 V.",
        "sub.homing": "Upper arm and forearm at a right angle; moving jaw (leader: trigger) on top; gripper half open.",
        "check.teleop.wrist_wrap": "Stopped: the leader's wrist turned more than half a turn from its middle pose, so "
        "the follower would have spun its wrist the other way round.",
        "check.teleop.wrist_wrap.fix": "Turn the leader's wrist back. If this happens in normal use, recalibrate both "
        "arms with the wrists turned the same way in the middle pose (moving jaw / trigger on top).",
        "check.homing.wrist": "wrist_roll: turned ~{deg} degrees differently from the {other} in its middle pose.",
        "check.homing.wrist.fix": "In the middle pose the moving jaw (on the leader: the trigger) is on top, on both "
        "arms. Turn the wrist that way and retry, or teleoperation turns the follower's wrist that far.",
        "sub.ranges": "Not wrist_roll, it turns all the way. Then back to the middle pose and ⏎.",
        "check.teleop.mirrored": "{joint}: leader reads {leader}, follower {follower}: mirror images.",
        "check.teleop.mirrored.fix": "If both arms really are folded the same way, the {joint} motor (or its horn) is "
        "mounted the other way round on one arm: re-assemble it like on the other arm. Teleoperation would drive "
        "this joint backwards.",
        "check.teleop.aligned": "Leader and follower poses differ: {joints}.",
        "check.teleop.aligned.fix": "Put the leader in the follower's pose (both folded, wrists and grippers turned "
        "the same way) and retry: once torque is on the follower moves to the leader's pose.",
        "check.save.forced": "Saved as {arm} v{version} (active), with accepted failures: {names}.",
        "check.save.forced.fix": "Fine if you meant it; the teleop test shows whether the arm works.",
        "sub.save": "Written to the motors and exported for LeRobot.",
        "sub.match": "",
        "sub.pose": "Each joint should read about 2047.",
        "sub.teleop": "Folded, wrists turned the same way. The follower starts slowly; keep a hand near it.",
        "key.start": "start", "key.stop": "done", "key.accept": "continue", "key.retry": "retry",
        "key.force": "accept anyway", "key.abort": "cancel", "key.abort_again": "press again to cancel",
        "key.joints": "joints", "key.details": "details", "key.ask": "assistant", "key.done": "done",
        "key.history": "history", "key.select": "select", "key.verify": "verify", "key.quit": "quit",
        "key.lang": "Türkçe", "key.activate": "make active", "key.diff": "compare", "key.export": "export",
        "key.back": "back",
        "result.pass": "Done", "result.warn": "Done, with warnings", "result.fail": "Failed",
        "result.aborted": "Cancelled", "result.saved": "Saved as {arm} v{v}",
        "working": "working…", "checks.passed": "{n} checks passed", "coverage": "coverage", "time": "time",
        "history.title": "History", "history.empty": "No calibrations yet.",
        "history.confirm": "Press ⏎ again to make {arm} v{v} active.",
        "history.activated": "{arm} v{v} is active. The motors keep their values until `so101-cal write {arm}`.",
        "history.exported": "Exported to {path}", "history.same": "This is the active version.",
        "ai.off": "Assistant is off. Type a model: ollama, claude, openai:<model>",
        "ai.ask": "Ask {model}  (⏎ on empty: explain the current problem)",
        "ai.on": "Assistant on: {model}", "ai.thinking": "thinking…",
    },
    "tr": {
        "app.title": "SO-101 KALİBRASYON",
        "menu.follower": "Follower", "menu.follower.sub": "12 V kol",
        "menu.leader": "Leader", "menu.leader.sub": "5 V kol",
        "menu.teleop": "İki kolu test et", "menu.teleop.sub": "leader → follower",
        "status.none": "kalibre edilmedi", "status.inactive": "aktif sürüm yok", "status.active": "v{v} · {ago}",
        "ago.now": "az önce", "ago.min": "{n} dk önce", "ago.h": "{n} sa önce", "ago.d": "{n} gün önce",
        "flow.calibrate": "{role} kalibrasyonu", "flow.verify": "{role} doğrulama", "flow.teleop": "Teleop testi",
        "step.port": "USB portu", "step.motors": "Motorlar", "step.power": "Besleme", "step.homing": "Orta poz",
        "step.ranges": "Aralıklar", "step.save": "Kaydet", "step.match": "Karşılaştır",
        "step.pose": "Orta poz kontrolü", "step.teleop": "Teleop",
        "hint.port.ready": "⏎'a bas, sonra {role} USB kablosunu *çıkar*",
        "hint.port.unplug": "{role} USB kablosunu şimdi *çıkar*",
        "hint.port.replug": "Şimdi *geri tak*",
        "hint.port.ambiguous": "Birden çok port kayboldu: *sadece bu kolu* tak",
        "hint.port.known": "*{role}*: {port}",
        "sub.port.known": "USB kablosu ve gücü takılı olsun.",
        "hint.motors": "Gücü aç, motorları *tara*",
        "hint.power": "Besleme *{lo}–{hi} V* olmalı",
        "hint.homing": "Her eklemi *ortaya* getir",
        "hint.ranges": "Her eklemi *iki ucuna* götür",
        "hint.save": "Kalibrasyonu *kaydet*",
        "hint.match": "Motorları *kayıtlı* kalibrasyonla karşılaştır",
        "hint.pose": "Kolu yine *orta* poza getir",
        "hint.teleop.ready": "İki kolu da *dinlenme duruşuna* katla",
        "hint.teleop": "*Leader*'ı yavaşça oynat",
        "sub.port": "Sadece bu kolun kartı takılı olsun.",
        "sub.motors": "1–6 numaralı motorlar cevap vermeli.",
        "sub.power": "Follower 12 V, leader 5 V.",
        "sub.homing": "Üst kol ile ön kol dik açı; hareketli çene (leader'da tetik) üstte; gripper yarı açık.",
        "check.teleop.wrist_wrap": "Durduruldu: leader'ın bileği orta duruştan yarım turdan fazla döndü; follower "
        "bileğini ters yönden tam tur döndürecekti.",
        "check.teleop.wrist_wrap.fix": "Leader'ın bileğini geri çevir. Normal kullanımda da oluyorsa iki kolu orta "
        "duruşta bilekleri aynı yöne dönük (hareketli çene / tetik üstte) olacak şekilde yeniden kalibre et.",
        "check.homing.wrist": "wrist_roll: orta duruşta {other} koluna göre ~{deg} derece farklı çevrilmiş.",
        "check.homing.wrist.fix": "Orta duruşta iki kolda da hareketli çene (leader'da tetik) üstte olmalı. Bileği "
        "öyle çevirip tekrar dene; yoksa teleop follower'ın bileğini bu kadar çevirir.",
        "sub.ranges": "wrist_roll hariç, o tam tur döner. Bitince kolu dik açılı orta duruşa getir ve ⏎.",
        "check.teleop.mirrored": "{joint}: leader {leader}, follower {follower} okuyor: birbirinin tersi.",
        "check.teleop.mirrored.fix": "İki kol gerçekten aynı şekilde katlıysa {joint} motoru (ya da horn'u) bir kolda "
        "ters takılı: diğer koldaki gibi yeniden monte et. Teleop bu eklemi ters yöne sürer.",
        "check.teleop.aligned": "Leader ve follower farklı duruşta: {joints}.",
        "check.teleop.aligned.fix": "Leader'ı follower'ın duruşuna getir (ikisi de katlı, bilekleri ve gripper'ları "
        "aynı yöne dönük) ve tekrar dene: tork açılınca follower leader'ın duruşuna gider.",
        "check.save.forced": "{arm} v{version} olarak kaydedildi ve aktif; kabul edilen hatalar: {names}.",
        "check.save.forced.fix": "Bilerek kabul ettiysen sorun yok; kolun çalışıp çalışmadığını teleop testi gösterir.",
        "sub.save": "Motorlara yazılır ve LeRobot için dışa aktarılır.",
        "sub.match": "",
        "sub.pose": "Her eklem yaklaşık 2047 okumalı.",
        "sub.teleop": "Katlı, bilekleri aynı yöne dönük. Follower yavaş başlar; elin yakında olsun.",
        "key.start": "başla", "key.stop": "bitti", "key.accept": "devam", "key.retry": "tekrar",
        "key.force": "yine de kabul", "key.abort": "iptal", "key.abort_again": "iptal için tekrar bas",
        "key.joints": "eklemler", "key.details": "detay", "key.ask": "asistan", "key.done": "bitti",
        "key.history": "geçmiş", "key.select": "seç", "key.verify": "doğrula", "key.quit": "çık",
        "key.lang": "English", "key.activate": "aktif yap", "key.diff": "karşılaştır", "key.export": "dışa aktar",
        "key.back": "geri",
        "result.pass": "Tamamlandı", "result.warn": "Uyarılarla tamamlandı", "result.fail": "Başarısız",
        "result.aborted": "İptal edildi", "result.saved": "{arm} v{v} olarak kaydedildi",
        "working": "çalışıyor…", "checks.passed": "{n} kontrol geçti", "coverage": "kapsama", "time": "süre",
        "history.title": "Geçmiş", "history.empty": "Henüz kalibrasyon yok.",
        "history.confirm": "{arm} v{v} sürümünü aktif yapmak için ⏎'a tekrar bas.",
        "history.activated": "{arm} v{v} aktif. Motorlar `so101-cal write {arm}` çalışana kadar eski değerde kalır.",
        "history.exported": "Dışa aktarıldı: {path}", "history.same": "Bu zaten aktif sürüm.",
        "ai.off": "Asistan kapalı. Bir model yaz: ollama, claude, openai:<model>",
        "ai.ask": "{model}'e sor  (boş ⏎: şu anki sorunu açıkla)",
        "ai.on": "Asistan açık: {model}", "ai.thinking": "düşünüyor…",
    },
}

_lang = "en"


def detect() -> str:
    """The language of the user's locale, if we have it."""
    for var in ("SO101CAL_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value:
            code = value.split("_")[0].split(".")[0].lower()
            return code if code in LANGS else "en"
    return "en"


def set_lang(lang: str) -> None:
    global _lang
    if lang not in LANGS:
        raise ValueError(f"unknown language '{lang}' (use {', '.join(LANGS)})")
    _lang = lang


def get_lang() -> str:
    return _lang


def t(key: str, lang: str | None = None, **kw) -> str:
    """The text for `key` in `lang` (default: the current language), English if it is missing there."""
    table = STRINGS[lang or _lang]
    text = table.get(key, STRINGS["en"].get(key, key))
    return text.format(**kw) if kw else text


def plain(text: str) -> str:
    return text.replace("*", "")


def check_text(check: dict, lang: str | None = None) -> tuple[str, str]:
    """(message, fix) of a check (checks.Check.to_dict) in `lang`; checks without a translation stay English."""
    key, args = f"check.{check['id']}", check.get("args")
    if args is None or key not in STRINGS["en"]:
        return check["message"], check.get("fix", "")
    return t(key, lang, **args), t(f"{key}.fix", lang, **args)


def hint(key: str, args: dict, lang: str | None = None) -> str:
    """A step hint (see wizard.Step.hint_spec)."""
    return t(key, lang, **args)
