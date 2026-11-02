"""University email (phase 4): reading over IMAP, junk cleanup, the weekly cleanup Proposal, undo, emptying, search.

The mailbox is a fake at the `Mailbox` level; the IMAP parsing underneath is tested on Exchange-shaped bytes."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from peace import mail
from peace.api import app
from peace.models import AuditEvent, MailMessage, Notification, Preference
from peace.routes import get_now

API = "/api/v1"
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
JUNK, TRASH = '"Lixo Eletr&APM-nico"', '"Itens Eliminados"'


def msg(sender, subject, days_ago, body="Olá.", extra=""):
    date = format_datetime(NOW - timedelta(days=days_ago))
    return (f"From: {sender}\r\nSubject: {subject}\r\nDate: {date}\r\nMessage-ID: <{abs(hash(subject))}@x>\r\n{extra}"
            f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n").encode()


class FakeBox:
    """An Exchange mailbox in memory. Records every write so tests can prove reading changes nothing."""

    def __init__(self):
        self.folders = {"INBOX": {"validity": 7, "next": 1, "msgs": {}}, JUNK: {"validity": 8, "next": 1, "msgs": {}},
                        TRASH: {"validity": 9, "next": 1, "msgs": {}}, '"Sent Items"': {"validity": 5, "next": 1, "msgs": {}}}
        self.current, self.writes, self.logins = None, [], 0

    def add(self, folder, raw, days_ago=0, flags=()):
        f = self.folders[folder]
        uid, f["next"] = f["next"], f["next"] + 1
        f["msgs"][uid] = {"raw": raw, "flags": {x.lower() for x in flags}, "date": NOW - timedelta(days=days_ago)}  # as parse_fetch gives them
        return uid

    # the Mailbox interface
    def listing(self):
        return [b'(\\HasNoChildren) "/" INBOX', b'(\\HasNoChildren) "/" ' + JUNK.encode(),
                b'(\\HasNoChildren) "/" ' + TRASH.encode(), b'(\\HasNoChildren) "/" "Sent Items"']

    def open(self, name, write=False):
        self.current = name
        return self.folders[name]["validity"]

    def uids(self, criteria="ALL"):
        msgs = self.folders[self.current]["msgs"]
        if criteria.startswith("BEFORE"):
            day = datetime.strptime(criteria.split()[1], "%d-%b-%Y").date()
            return sorted(u for u, m in msgs.items() if m["date"].date() < day)
        return sorted(msgs)

    def headers(self, uids):
        msgs = self.folders[self.current]["msgs"]
        return [{"uid": u, "size": len(msgs[u]["raw"]), "flags": set(msgs[u]["flags"]),
                 "literal": msgs[u]["raw"].split(b"\r\n\r\n")[0]} for u in uids if u in msgs]

    def flags(self):
        return {u: set(m["flags"]) for u, m in self.folders[self.current]["msgs"].items()}

    def sizes(self):
        return sum(len(m["raw"]) for m in self.folders[self.current]["msgs"].values())

    def body(self, uid):
        return self.folders[self.current]["msgs"][uid]["raw"]

    def move(self, uids, dest):
        src, to = self.folders[self.current], self.folders[dest]
        mapping = {}
        for u in uids:
            mapping[u] = new = to["next"]
            to["next"] += 1
            to["msgs"][new] = src["msgs"].pop(u)
        self.writes.append(("move", self.current, list(uids), dest))
        return to["validity"], mapping

    def can_purge(self):
        return True

    def purge(self, uids):
        for u in uids:
            self.folders[self.current]["msgs"].pop(u, None)
        self.writes.append(("purge", self.current, list(uids)))

    def close(self):
        pass


@pytest.fixture
def box(session, monkeypatch):
    fake = FakeBox()

    def opener(login):
        fake.logins += 1
        if login["password"] == "errada":
            raise mail.AuthError("O servidor recusou o utilizador ou a palavra-passe.")
        return fake
    monkeypatch.setattr(mail, "connect", opener)
    session.add(Preference(key="integrations", value={"email": {
        "enabled": True, "url": "https://webmail.escola.example.pt", "username": "a2020@escola.example.pt", "password": "segredo",
        "clean_junk": True, "quota_mb": None}, "moodle": {"enabled": True, "url": "https://moodle.escola.example.pt/moodle",
                                                          "username": "u", "password": "p"}}))
    session.commit()
    app.dependency_overrides[get_now] = lambda: NOW
    yield fake
    app.dependency_overrides.pop(get_now, None)


def course(client):
    return client.post(f"{API}/courses", json={"name": "Bases de Dados", "code": "BD"}).json()["id"]


def mailbox(fake):
    """A teacher, a newsletter, a Moodle copy, an unread list mail, a flagged list mail; spam old and new."""
    return {
        "teacher": fake.add("INBOX", msg("Prof. Rui <rui@escola.example.pt>", "BD: enunciado do projeto", 40,
                                         "O projeto final entrega-se a 20 de novembro."), 40, ["\\Seen"]),
        "news": fake.add("INBOX", msg("Loja <news@loja.pt>", "Promoções", 45, extra="List-Unsubscribe: <mailto:u@loja.pt>\r\n"), 45, ["\\Seen"]),
        "moodle": fake.add("INBOX", msg("Moodle <noreply@moodle.escola.example.pt>", "Novo aviso em BD", 35), 35, ["\\Seen"]),
        "unread": fake.add("INBOX", msg("Lista <lista@x.pt>", "Convite", 50, extra="List-Id: <l.x.pt>\r\n"), 50),
        "flagged": fake.add("INBOX", msg("Lista <lista@x.pt>", "Guardar", 50, extra="List-Id: <l.x.pt>\r\n"), 50, ["\\Seen", "\\Flagged"]),
        "old_spam": fake.add(JUNK, msg("x@spam.biz", "Ganhou!", 20), 20),
        "new_spam": fake.add(JUNK, msg("y@spam.biz", "Ganhou outra vez", 2), 2),
    }


def by_subject(session, subject):
    return session.query(MailMessage).filter_by(subject=subject).one()


def test_imap_parsing_on_exchange_shaped_bytes():
    assert mail.decode_utf7("Lixo Eletr&APM-nico") == "Lixo Eletrónico" and mail.decode_utf7("A&-B") == "A&B"
    found = mail.parse_list([b'(\\HasNoChildren) "/" INBOX', b'(\\HasNoChildren \\Junk) "/" "Junk Email"',
                             b'(\\HasNoChildren) "/" "Itens Eliminados"', b'(\\Noselect) "/" "Public"'])
    assert found == {"inbox": "INBOX", "junk": '"Junk Email"', "trash": '"Itens Eliminados"'}
    data = [(b'1 (UID 42 RFC822.SIZE 1234 FLAGS (\\Seen \\Answered) BODY[HEADER.FIELDS (FROM)] {20}', b"From: a@b.pt\r\n\r\n"),
            b")", b"2 (UID 43 FLAGS ())"]
    rows = mail.parse_fetch(data)
    assert [(r["uid"], r["size"], r["flags"]) for r in rows] == [(42, 1234, {"\\seen", "\\answered"}), (43, 0, set())]
    assert rows[0]["literal"].startswith(b"From:")
    assert mail.imap_date(datetime(2026, 11, 3)) == "3-Nov-2026"

    class Imap:
        """imaplib's real shapes: response(code) answers (code, [data]) or (code, [None]); untagged codes are kept,
        a code on the tagged OK line stays in the command's reply."""

        def __init__(self, untagged=None, tagged=b"MOVE completed."):
            self.untagged, self.tagged = untagged or {}, tagged

        def select(self, name, readonly=True):
            return "OK", [b"12"]

        def uid(self, *args):
            return "OK", [self.tagged]

        def response(self, code):
            return code, self.untagged.pop(code, [None])

    box = mail.Mailbox.__new__(mail.Mailbox)
    box.imap = Imap({"UIDVALIDITY": [b"7"], "COPYUID": [b"9 4:5,7 101:103"]})
    assert box.open("INBOX") == 7
    assert box.move([4, 5, 7], "Trash") == (9, {4: 101, 5: 102, 7: 103})  # COPYUID in an untagged OK
    box.imap = Imap(tagged=b"[COPYUID 9 8 104] MOVE completed.")
    assert box.move([8], "Trash") == (9, {8: 104})  # COPYUID on the tagged OK line
    with pytest.raises(mail.MailError):
        box.open("INBOX")  # no UIDVALIDITY sent: a readable error, not a crash


def test_reading_classifies_without_changing_anything_and_spam_is_cleaned(client, session, box):
    c = course(client)
    ids = mailbox(box)
    mail.run(session, NOW)
    teacher = by_subject(session, "BD: enunciado do projeto")
    assert (teacher.academic, teacher.bulk, teacher.course_id) == (True, False, c)
    assert "20 de novembro" in teacher.body  # academic messages keep their text
    assert by_subject(session, "Promoções").body is None and by_subject(session, "Promoções").bulk  # others: headers only
    assert by_subject(session, "Novo aviso em BD").notice
    assert box.writes == [("move", JUNK, [ids["old_spam"]], TRASH)]  # the only write: spam older than 14 days
    assert by_subject(session, "Ganhou!").removed_by == "junk" and by_subject(session, "Ganhou outra vez").removed_at is None
    st = client.get(f"{API}/mail/status").json()
    assert st["error"] is None and st["inbox"] == 5 and st["used_bytes"] > 0

    mail.run(session, NOW + timedelta(minutes=5))  # within 15 minutes: nothing happens
    assert box.logins == 1
    del box.folders["INBOX"]["msgs"][ids["news"]]  # the student deleted it in the webmail
    box.folders["INBOX"]["msgs"][ids["unread"]]["flags"].add("\\seen")
    mail.run(session, NOW + timedelta(minutes=20))
    assert session.query(MailMessage).filter_by(subject="Promoções").count() == 0
    assert by_subject(session, "Convite").seen and session.query(MailMessage).count() == 6


def test_a_refused_login_is_not_retried_until_the_password_changes(client, session, box):
    row = session.get(Preference, "integrations")
    row.value = {**row.value, "email": {**row.value["email"], "password": "errada"}}
    session.commit()
    mail.run(session, NOW)
    mail.run(session, NOW + timedelta(hours=2))
    assert box.logins == 1 and client.get(f"{API}/mail/status").json()["needs_login"]
    assert session.query(Notification).filter(Notification.key.like("mail:login:%")).count() == 1
    r = client.put(f"{API}/preferences/integrations", json={
        "inforestudante": {"enabled": False, "url": "", "username": "", "password": ""},
        "moodle": {"enabled": True, "url": "https://moodle.escola.example.pt/moodle", "username": "u", "password": ""},
        "email": {"enabled": True, "url": "https://webmail.escola.example.pt/owa/", "username": "a2020@escola.example.pt",
                  "password": "nova", "clean_junk": True, "quota_mb": 2000}})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == {"enabled": True, "url": "https://webmail.escola.example.pt", "username": "a2020@escola.example.pt",
                                 "has_password": True, "clean_junk": True, "quota_mb": 2000}
    session.expire_all()
    assert "nova" not in str([a.detail for a in session.query(AuditEvent)])  # never in the audit trail
    mail.run(session, NOW + timedelta(hours=3))
    assert box.logins == 2 and not client.get(f"{API}/mail/status").json()["needs_login"]


def test_weekly_cleanup_lists_only_safe_mail_and_the_student_can_keep_some(client, session, box):
    course(client)
    ids = mailbox(box)
    box.add("INBOX", msg("Lista <lista@x.pt>", "BD: aviso da lista", 60, extra="List-Id: <l.x.pt>\r\n"), 60, ["\\Seen"])
    box.add("INBOX", msg("Amigo <amigo@gmail.com>", "Jantar", 60, extra="List-Id: <l.x.pt>\r\n"), 60, ["\\Seen"])
    box.add("INBOX", msg("ESCOLA - Gabinete de Comunicação <comunicacao@escola.example.pt>", "Semana aberta", 60), 60)  # unread broadcast
    box.add("INBOX", msg("Ana Reis <areis@escola.example.pt>", "Reunião", 60), 60)  # an unread person: never
    client.put(f"{API}/mail/keep", json={"senders": ["Amigo@gmail.com"]})
    mail.run(session, NOW)
    proposal = mail.offer_cleanup(session, NOW)
    session.commit()
    listed = {e["quote"]: e["rule"] for e in proposal.evidence}
    # Not the teacher, not flagged, not course-matched list mail, not a sender to keep; unread mass mail is listed.
    assert listed == {"Promoções": "lista ou newsletter", "Novo aviso em BD": "aviso do Moodle/inforestudante",
                      "Convite": "lista ou newsletter", "Semana aberta": "divulgação de um serviço"}
    assert mail.offer_cleanup(session, NOW + timedelta(days=1)) is None  # once a week, one at a time

    news = by_subject(session, "Promoções").id
    shown = [p for p in client.get(f"{API}/proposals").json() if p["kind"] == "mail_cleanup"][0]
    assert len(shown["preview"]["messages"]) == 4 and shown["preview"]["error"] is None
    applied = client.post(f"{API}/proposals/{proposal.id}/apply", json={"keep": [news]}).json()
    assert applied["status"] == "applied" and len(applied["result"]["moved"]) == 3
    assert box.writes[-1][:2] == ("move", "INBOX") and ids["moodle"] in box.writes[-1][2] and ids["news"] not in box.writes[-1][2]
    assert ids["news"] in box.folders["INBOX"]["msgs"]  # kept

    undone = client.post(f"{API}/proposals/{proposal.id}/undo").json()
    assert undone["status"] == "undone"
    assert box.writes[-1][1] == TRASH and box.writes[-1][3] == "INBOX"
    assert any(b"Novo aviso em BD" in m["raw"] for m in box.folders["INBOX"]["msgs"].values())


def test_peace_empties_only_what_it_moved_after_30_days(client, session, box):
    mailbox(box)
    mine_elsewhere = box.add(TRASH, msg("a@b.pt", "Apagado pelo estudante", 1), 1)
    mail.run(session, NOW)
    spam = by_subject(session, "Ganhou!")
    assert spam.trash_uid in box.folders[TRASH]["msgs"]
    mail.run(session, NOW + timedelta(days=29))
    assert not [w for w in box.writes if w[0] == "purge"]
    mail.run(session, NOW + timedelta(days=31))
    assert [w for w in box.writes if w[0] == "purge"] == [("purge", TRASH, [spam.trash_uid])]
    assert mine_elsewhere in box.folders[TRASH]["msgs"]  # the student's own deleted mail is never touched
    session.expire_all()
    assert by_subject(session, "Ganhou!").purged_at is not None
    r = client.post(f"{API}/mail/messages/{spam.id}/restore")
    assert r.status_code == 409 and "Recuperar itens eliminados" in r.json()["detail"]


def test_a_spam_put_back_goes_to_the_inbox(client, session, box):
    mailbox(box)
    mail.run(session, NOW)
    spam = by_subject(session, "Ganhou!")
    assert client.post(f"{API}/mail/messages/{spam.id}/restore").status_code == 200
    assert any(b"Ganhou!" in m["raw"] for m in box.folders["INBOX"]["msgs"].values())
    mail.run(session, NOW + timedelta(minutes=20))
    assert by_subject(session, "Ganhou!").folder == "inbox"  # read again, now as an inbox message


def test_quota_warning_and_search(client, session, box):
    course(client)
    mailbox(box)
    row = session.get(Preference, "integrations")
    row.value = {**row.value, "email": {**row.value["email"], "quota_mb": 1}}
    session.commit()
    box.add('"Sent Items"', b"From: a2020@escola.example.pt\r\nSubject: anexo\r\n\r\n" + b"x" * 950_000, 3, ["\\Seen"])
    mail.run(session, NOW)
    note = session.query(Notification).filter(Notification.key.like("mail:quota:%")).one()
    assert note.url == "/correio" and "de 1 MB" in note.body
    found = client.get(f"{API}/mail/messages", params={"q": "projeto final"}).json()
    assert [m["subject"] for m in found] == ["BD: enunciado do projeto"]
    assert [m["subject"] for m in client.get(f"{API}/mail/messages").json()] == ["BD: enunciado do projeto"]
    assert client.get(f"{API}/mail/messages", params={"view": "removed"}).json()[0]["restorable"]


def test_services_are_mass_mail_and_people_are_not():
    def kind(sender):
        head = mail.email.message_from_bytes(f"From: {sender}\r\nSubject: x\r\n".encode(), policy=mail.email.policy.default)
        c = mail.classify(head, [], set())
        return "bulk" if c["bulk"] else "academic" if c["academic"] else "other"
    # Senders in the shapes of a real mailbox.
    for service in ('"ESCOLA\\\\COM - Gabinete de Comunicação e Relações Publicas" <comunicacao@escola.example.pt>',
                    "ESCOLA\\COM - Gabinete de Comunicação <comunicacao@escola.example.pt>",  # malformed (unquoted): still read
                    "<divulgacao@politecnico.example.pt>",
                    "Secretariado da Presidência <sec-presidencia@escola.example.pt>", "SAS <sas@politecnico.example.pt>", "<eventos@politecnico.example.pt>",
                    "Serviço de Informática <si@escola.example.pt>", "<comunicacao.geral@politecnico.example.pt>"):
        assert kind(service) == "bulk", service
    for person in ("Beatriz Ferreira Lopes <bferreira@escola.example.pt>", "Carla Nunes <carla.nunes@escola.example.pt>",
                   '"jsilva@escola.example.pt" <jsilva@escola.example.pt>', "<pcc-cursos@escola.example.pt>"):
        assert kind(person) == "academic", person
    assert not mail.is_service("jsilva@escola.example.pt", "jsilva") and mail.is_service("", "divulgacao")


def test_course_acronyms_match_whole_words_only():
    assert mail.acronym("Programação Orientada a Objetos") == "POO" and mail.acronym("Sistemas Operativos") == "SO"


def test_assistant_searches_mail_and_diagnostics_show_it(client, session, box):
    from peace.assistant import cmd_search_mail, match_rules
    from peace.diagnostics import report

    assert match_rules("procura nos emails por projeto final", NOW.date()) == ("search_mail", {"query": "projeto final"})
    assert match_rules("pesquisa enunciado nos meus emails", NOW.date()) == ("search_mail", {"query": "enunciado"})
    mailbox(box)
    mail.run(session, NOW)
    reply = cmd_search_mail(session, NOW, "projeto final")
    assert reply.to == "/correio" and "«BD: enunciado do projeto», de Prof. Rui" in reply.text
    assert cmd_search_mail(session, NOW, "xpto").text == "Não encontrei emails sobre «xpto»."
    row = next(c for c in report(session, NOW)["components"] if c["id"] == "mail")
    assert row["state"] == "ok" and "Lido" in row["detail"]
