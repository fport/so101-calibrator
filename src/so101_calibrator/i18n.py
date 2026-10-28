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
        "step.ranges": "Ranges", "step.direction": "Directions", "step.save": "Save", "step.match": "Compare",
        "step.pose": "Middle pose check", "step.teleop": "Teleop",
        "hint.port.ready": "Press ⏎, then *unplug* the {role}'s USB cable",
        "hint.port.unplug": "*Unplug* the {role}'s USB cable now",
        "hint.port.replug": "Now *plug it back in*",
        "hint.port.ambiguous": "Several ports vanished: plug in *only this arm*",
        "hint.port.known": "Using *{port}*",
        "hint.motors": "Power on, then *scan* the motors",
        "hint.power": "The supply should be *{lo}–{hi} V*",
        "hint.homing": "Put every joint in the *middle*",
        "hint.ranges": "Move each joint to *both ends*",
        "hint.direction.ready": "Move each joint *a little*, as shown",
        "hint.direction": "{joint}: {prompt}",
        "hint.save": "*Save* the calibration",
        "hint.match": "Compare the motors with the *saved* calibration",
        "hint.pose": "Put the arm in the *middle* pose again",
        "hint.teleop.ready": "Put both arms in the *same pose*",
        "hint.teleop": "Move the *leader* slowly",
        "sub.port": "Only this arm's board plugged in.",
        "sub.motors": "Motors 1–6 should answer.",
        "sub.power": "Follower 12 V, leader 5 V.",
        "sub.homing": "Upper arm and forearm at a right angle, gripper half open.",
        "sub.ranges": "Leave wrist_roll alone. Back to the middle, then ⏎.",
        "sub.direction": "Each joint moves on by itself once it is seen.",
        "sub.save": "Written to the motors and exported for LeRobot.",
        "sub.match": "",
        "sub.pose": "Each joint should read about 2047.",
        "sub.teleop": "The follower mirrors it. Keep a hand near it.",
        "joint.shoulder_pan": "turn the whole arm to *its left*",
        "joint.shoulder_lift": "lift the upper arm *up*",
        "joint.elbow_flex": "*unfold* the elbow",
        "joint.wrist_flex": "tilt the wrist *up*",
        "joint.wrist_roll": "roll the wrist *clockwise*",
        "joint.gripper": "*open* the gripper",
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
        "step.ranges": "Aralıklar", "step.direction": "Yönler", "step.save": "Kaydet", "step.match": "Karşılaştır",
        "step.pose": "Orta poz kontrolü", "step.teleop": "Teleop",
        "hint.port.ready": "⏎'a bas, sonra {role} USB kablosunu *çıkar*",
        "hint.port.unplug": "{role} USB kablosunu şimdi *çıkar*",
        "hint.port.replug": "Şimdi *geri tak*",
        "hint.port.ambiguous": "Birden çok port kayboldu: *sadece bu kolu* tak",
        "hint.port.known": "*{port}* kullanılıyor",
        "hint.motors": "Gücü aç, motorları *tara*",
        "hint.power": "Besleme *{lo}–{hi} V* olmalı",
        "hint.homing": "Her eklemi *ortaya* getir",
        "hint.ranges": "Her eklemi *iki ucuna* götür",
        "hint.direction.ready": "Her eklemi gösterildiği gibi *biraz* oynat",
        "hint.direction": "{joint}: {prompt}",
        "hint.save": "Kalibrasyonu *kaydet*",
        "hint.match": "Motorları *kayıtlı* kalibrasyonla karşılaştır",
        "hint.pose": "Kolu yine *orta* poza getir",
        "hint.teleop.ready": "İki kolu *aynı poza* getir",
        "hint.teleop": "*Leader*'ı yavaşça oynat",
        "sub.port": "Sadece bu kolun kartı takılı olsun.",
        "sub.motors": "1–6 numaralı motorlar cevap vermeli.",
        "sub.power": "Follower 12 V, leader 5 V.",
        "sub.homing": "Üst kol ile ön kol dik açı, gripper yarı açık.",
        "sub.ranges": "wrist_roll'a dokunma. Bitince ortaya dön ve ⏎.",
        "sub.direction": "Her eklem algılanınca kendiliğinden geçer.",
        "sub.save": "Motorlara yazılır ve LeRobot için dışa aktarılır.",
        "sub.match": "",
        "sub.pose": "Her eklem yaklaşık 2047 okumalı.",
        "sub.teleop": "Follower onu taklit eder. Elin yakında olsun.",
        "joint.shoulder_pan": "tüm kolu *kendi soluna* çevir",
        "joint.shoulder_lift": "üst kolu *yukarı* kaldır",
        "joint.elbow_flex": "dirseği *aç*",
        "joint.wrist_flex": "bileği *yukarı* eğ",
        "joint.wrist_roll": "bileği *saat yönünde* çevir",
        "joint.gripper": "gripper'ı *aç*",
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


def hint(key: str, args: dict, lang: str | None = None) -> str:
    """A step hint (see wizard.Step.hint_spec); a "joint" argument brings its localized prompt."""
    if "joint" in args:
        args = {**args, "prompt": t(f"joint.{args['joint']}", lang)}
    return t(key, lang, **args)
