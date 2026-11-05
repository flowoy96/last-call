"""Strings used in the e-mails (the web UI is English only)."""

from __future__ import annotations

from datetime import date

STRINGS = {
    "de": {
        "subject": "Last Call auf {server}: {leaving} Titel verschwinden bald",
        "subject_new_only": "Neu auf {server}: {count} Titel",
        "preheader": "Schau rein, bevor sie weg sind.",
        "greeting": "Hallo {name},",
        "intro": "hier ist dein Update von {server}.",
        "leaving_title": "Bald nicht mehr verfügbar",
        "leaving_text": "Diese Titel werden demnächst entfernt. Schau sie dir an, solange du noch kannst.",
        "new_title": "Neu hinzugefügt",
        "new_text": "Frisch in der Bibliothek seit deinem letzten Update.",
        "leaves_on": "Bis {date}",
        "leaves_today": "Nur noch heute",
        "new_badge": "Neu",
        "new_episodes": "{count} neue Folgen",
        "new_episode": "1 neue Folge",
        "reco_title": "Empfehlungen für dich",
        "reco_text": "Noch nicht gesehen, passt aber zu dem, was du gerne schaust.",
        "reco_badge": "Für dich",
        "rewatch_title": "Mal wieder schauen?",
        "rewatch_text": "Filme, die du schon kennst, aber länger nicht gesehen hast.",
        "rewatch_badge": "Schon gesehen",
        "rewatch_last": "Zuletzt {date}",
        "subject_suggestions": "Tipps für dich auf {server}",
        "season": "Staffel {n}",
        "watch_now": "Jetzt ansehen",
        "open_server": "{server} öffnen",
        "footer": "Du erhältst diese Mail, weil du ein Konto auf {server} hast. "
                  "Wenn du keine Mails mehr möchtest, sag einfach dem Betreiber Bescheid.",
        "test_subject": "Last Call: Testmail",
        "test_body": "Wenn du das liest, funktioniert der SMTP-Versand.",
        "months_long": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
                        "September", "Oktober", "November", "Dezember"],
        "months": ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."],
        "weekdays": ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."],
    },
    "en": {
        "subject": "Last call on {server}: {leaving} titles are leaving soon",
        "subject_new_only": "New on {server}: {count} titles",
        "preheader": "Catch them before they're gone.",
        "greeting": "Hi {name},",
        "intro": "here's your update from {server}.",
        "leaving_title": "Leaving soon",
        "leaving_text": "These titles will be removed soon. Watch them while you still can.",
        "new_title": "Recently added",
        "new_text": "Fresh in the library since your last update.",
        "leaves_on": "Until {date}",
        "leaves_today": "Last day today",
        "new_badge": "New",
        "new_episodes": "{count} new episodes",
        "new_episode": "1 new episode",
        "reco_title": "Recommended for you",
        "reco_text": "Not seen yet, but it matches what you like to watch.",
        "reco_badge": "For you",
        "rewatch_title": "Watch it again?",
        "rewatch_text": "Movies you already know but haven't seen in a while.",
        "rewatch_badge": "Seen before",
        "rewatch_last": "Last seen {date}",
        "subject_suggestions": "Picks for you on {server}",
        "season": "Season {n}",
        "watch_now": "Watch now",
        "open_server": "Open {server}",
        "footer": "You receive this e-mail because you have an account on {server}. "
                  "If you no longer want these e-mails, just tell the server admin.",
        "test_subject": "Last Call: test e-mail",
        "test_body": "If you can read this, SMTP delivery works.",
        "months_long": ["January", "February", "March", "April", "May", "June", "July", "August",
                        "September", "October", "November", "December"],
        "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    },
}


def t(lang: str) -> dict:
    return STRINGS.get(lang, STRINGS["en"])


def format_date(d: date, lang: str) -> str:
    s = t(lang)
    wd = s["weekdays"][d.weekday()]
    mon = s["months"][d.month - 1]
    if lang == "de":
        return f"{wd} {d.day}. {mon}"
    return f"{wd}, {mon} {d.day}"


def format_month(d: date, lang: str) -> str:
    return f"{t(lang)['months'][d.month - 1]} {d.year}"
