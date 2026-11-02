"""University email (phase 4, ADR-021 as amended): read the mailbox over IMAP and keep it under its limit.

Deterministic, no LLM, standard library only. Reading never changes a message (EXAMINE, BODY.PEEK). The only
writes are moves to Deleted Items: automatically for Junk Email older than 14 days, otherwise only through a
cleanup Proposal the student applied; and, 30 days later, emptying from Deleted Items only what PEACE moved there
(by UID, with UID EXPUNGE, so nothing else in that folder is touched). Never sends, never flags.
"""

import base64
import email
import email.policy
import hashlib
import html
import imaplib
import logging
import os
import re
import ssl
import unicodedata
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from peace import sync
from peace.auth import require_token
from peace.config import get_settings
from peace.db import get_session
from peace.models import AuditEvent, Course, MailMessage, Preference, Proposal
from peace.notifications import _add, _end_of_day
from peace.routes import get_now

log = logging.getLogger(__name__)

EVERY = timedelta(minutes=15)
JUNK_AFTER = timedelta(days=14)
CLEAN_AFTER = timedelta(days=30)  # inbox messages older than this may be offered for cleanup
PURGE_AFTER = timedelta(days=30)  # what PEACE moved to Deleted Items is emptied this long after
SIZES_EVERY = timedelta(hours=6)
BATCH = 200
BODIES_PER_RUN = 50
MAX_BODY = 100_000
MAX_CLEANUP = 300  # shortcut: one proposal lists at most 300 messages; the next week offers the rest
QUOTA_WARN = 0.85
# shortcut: set in peace.env (comma-separated); move to Settings if other students need it without editing a file.
ACADEMIC_DOMAINS = tuple(d.strip() for d in os.environ.get("PEACE_ACADEMIC_DOMAINS", "").split(",") if d.strip())
NO_REPLY = re.compile(r"^(no-?reply|do-?not-?reply|nao-?responder|mailer-daemon|notifica\w*|bounce\w*)\b", re.I)
FOLDER_NAMES = {  # Exchange names its folders in the mailbox's language
    "junk": ("junk email", "junk e-mail", "e-mail de lixo", "lixo eletrónico", "lixo eletronico", "junk", "spam"),
    "trash": ("deleted items", "itens eliminados", "itens excluídos", "trash"),
}
# Offices and services of the university write to everyone (no list headers): seen in a real mailbox, 2026-10-29.
# PEACE_MAIL_SERVICES adds the names of your own school's offices, as "name|name".
_OWN_SERVICES = os.environ.get("PEACE_MAIL_SERVICES", "").strip("|")
SERVICE = re.compile(r"\b(" + (_OWN_SERVICES + "|" if _OWN_SERVICES else "") + r"gabinete|servicos?|secretariado|secretaria|presidencia|presidente|reitoria\w*|comunicacao|"
                     r"divulgacao|cultura|qualidade|relacoes|eventos|conselho|alumni|informatica|universidade|instituto|"
                     r"politecnic\w*|jornadas\w*|biblioteca|associacao|direcao|sas|si|ri|geral)\b")
RULES = 3  # bump when classification changes: unremoved messages are read again with the new rules
HEADERS = "FROM SUBJECT DATE MESSAGE-ID LIST-UNSUBSCRIBE LIST-ID PRECEDENCE AUTO-SUBMITTED REFERENCES IN-REPLY-TO"


class AuthError(Exception):
    """The server refused the login: not retried until the student changes it."""


class MailError(Exception):
    """The mailbox could not be read or changed; the message is safe to show (no secrets)."""


# --- IMAP ----------------------------------------------------------------------------------------

def decode_utf7(name: str) -> str:
    """IMAP's modified UTF-7 folder names: "Lixo Eletr&APM-nico" -> "Lixo Eletrónico"."""
    def part(m: re.Match) -> str:
        chunk = m.group(1)
        if not chunk:
            return "&"
        chunk = chunk.replace(",", "/")
        return base64.b64decode(chunk + "=" * (-len(chunk) % 4)).decode("utf-16-be")
    return re.sub(r"&([^-]*)-", part, name)


LIST_LINE = re.compile(r'^\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


def _listed(lines: list[Any]) -> list[tuple[str, str]]:
    """LIST lines -> [(flags lowercased, raw name)] for selectable folders."""
    out = []
    for line in lines:
        m = LIST_LINE.match(line.decode("utf-8", "replace") if isinstance(line, bytes) else "")
        if m and "\\noselect" not in m["flags"].lower():
            out.append((m["flags"].lower(), m["name"].strip()))
    return out


def parse_list(lines: list[Any]) -> dict[str, str]:
    """LIST lines -> {"inbox" | "junk" | "trash": the folder's raw name}. Special-use flags first, then names."""
    found: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for flags, raw in _listed(lines):
        plain = decode_utf7(raw[1:-1] if raw.startswith('"') else raw)
        for role, flag in (("junk", "\\junk"), ("trash", "\\trash")):
            if flag in flags:
                found.setdefault(role, raw)
        by_name[plain.lower()] = raw
        if plain.upper() == "INBOX":
            found["inbox"] = raw
    for role, names in FOLDER_NAMES.items():
        for n in names:
            if role not in found and n in by_name:
                found[role] = by_name[n]
    return found


FETCH_META = re.compile(rb"UID (\d+)|RFC822\.SIZE (\d+)|FLAGS \(([^)]*)\)")


def parse_fetch(data: list[Any]) -> list[dict[str, Any]]:
    """imaplib FETCH data (tuples of meta + literal, or plain meta lines) -> [{uid, size, flags, literal}]."""
    out = []
    for item in data:
        meta, literal = (item[0], item[1]) if isinstance(item, tuple) else (item, None)
        if not isinstance(meta, bytes) or b"UID " not in meta:
            continue
        row: dict[str, Any] = {"uid": None, "size": 0, "flags": set(), "literal": literal}
        for uid, size, flags in FETCH_META.findall(meta):
            if uid:
                row["uid"] = int(uid)
            if size:
                row["size"] = int(size)
            if flags:
                row["flags"] = {f.lower() for f in flags.decode().split()}
        if row["uid"] is not None:
            out.append(row)
    return out


def imap_date(when: datetime) -> str:
    """IMAP dates are "3-Nov-2026" in English, whatever the system locale."""
    return f"{when.day}-{'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()[when.month - 1]}-{when.year}"


def _uidset(uids: list[int]) -> str:
    return ",".join(str(u) for u in uids)


def _expand(uidset: str) -> list[int]:
    out = []
    for part in uidset.split(","):
        a, _, b = part.partition(":")
        out.extend(range(int(a), int(b or a) + 1))
    return out


class Mailbox:
    """The few IMAP operations PEACE needs, over imaplib (TLS, certificate checked)."""

    def __init__(self, host: str, username: str, password: str):
        try:
            self.imap = imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context(), timeout=30)
        except (OSError, imaplib.IMAP4.error) as e:
            raise MailError(f"Sem ligação ao servidor de email ({host}).") from e
        try:
            self.imap.login(username, password)
        except imaplib.IMAP4.error as e:
            self.close()
            raise AuthError("O servidor recusou o utilizador ou a palavra-passe.") from e

    @staticmethod
    def _ok(typ: str, data: Any, what: str) -> Any:
        if typ != "OK":
            raise MailError(f"O servidor de email recusou: {what}.")
        return data

    def close(self) -> None:
        try:
            self.imap.logout()
        except (OSError, imaplib.IMAP4.error):
            pass

    def listing(self) -> list[Any]:
        return self._ok(*self.imap.list(), "listar pastas")

    def open(self, name: str, write: bool = False) -> int:
        self._ok(*self.imap.select(name, readonly=not write), "abrir uma pasta")
        # response() answers (code, [data]), never "OK"; [None] when the server did not send the code.
        data = self.imap.response("UIDVALIDITY")[1]
        if not data or data[0] is None:
            raise MailError("O servidor de email não disse o UIDVALIDITY da pasta.")
        return int(data[0])

    def uids(self, criteria: str = "ALL") -> list[int]:
        data = self._ok(*self.imap.uid("SEARCH", None, criteria), "procurar")
        return [int(u) for u in (data[0] or b"").split()]

    def headers(self, uids: list[int]) -> list[dict[str, Any]]:
        what = f"(UID RFC822.SIZE FLAGS BODY.PEEK[HEADER.FIELDS ({HEADERS})])"
        return parse_fetch(self._ok(*self.imap.uid("FETCH", _uidset(uids), what), "ler cabeçalhos"))

    def flags(self) -> dict[int, set[str]]:
        return {r["uid"]: r["flags"] for r in parse_fetch(self._ok(*self.imap.uid("FETCH", "1:*", "(UID FLAGS)"), "ler estados"))}

    def sizes(self) -> int:
        typ, data = self.imap.uid("FETCH", "1:*", "(UID RFC822.SIZE)")
        return sum(r["size"] for r in parse_fetch(data)) if typ == "OK" else 0

    def body(self, uid: int) -> bytes | None:
        rows = parse_fetch(self._ok(*self.imap.uid("FETCH", str(uid), "(UID BODY.PEEK[])"), "ler mensagem"))
        return rows[0]["literal"] if rows else None

    def move(self, uids: list[int], dest: str) -> tuple[int | None, dict[int, int]]:
        """Move messages (MOVE + UIDPLUS); returns the destination's UIDVALIDITY and old UID -> new UID."""
        tagged = self._ok(*self.imap.uid("MOVE", _uidset(uids), dest), "mover mensagens")
        # COPYUID comes in an untagged OK (imaplib keeps it) or on the tagged OK line (imaplib leaves it in the reply).
        codes = [c for c in self.imap.response("COPYUID")[1] if c is not None]
        codes += [m[1] for d in tagged if isinstance(d, bytes) for m in re.finditer(rb"\[COPYUID ([^\]]+)\]", d)]
        validity, mapping = None, {}
        for code in codes:
            parts = (code.decode() if isinstance(code, bytes) else str(code)).split()
            if len(parts) == 3:
                validity = int(parts[0])
                mapping |= dict(zip(_expand(parts[1]), _expand(parts[2])))
        return validity, mapping

    def can_purge(self) -> bool:
        return "UIDPLUS" in self.imap.capabilities

    def purge(self, uids: list[int]) -> None:
        """Permanently delete exactly these UIDs from the open folder (UID EXPUNGE leaves everything else)."""
        self._ok(*self.imap.uid("STORE", _uidset(uids), "+FLAGS.SILENT", r"(\Deleted)"), "marcar para apagar")
        self._ok(*self.imap.uid("EXPUNGE", _uidset(uids)), "esvaziar")


def connect(login: dict[str, Any]) -> Mailbox:
    return Mailbox(login["host"], login["username"], login["password"])


# --- What a message is ---------------------------------------------------------------------------

def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _domain_in(domain: str, domains) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in domains if d)


def acronym(name: str) -> str:
    """"Programação Orientada a Objetos" -> "POO"; "Bases de Dados" -> "BD" (short joining words are skipped)."""
    return "".join(w[0] for w in re.findall(r"\w+", name) if len(w) > 2 and w[0].isupper()).upper()


def is_service(name: str, local: str) -> bool:
    """An office or service of the university, by its name or address. A name that is just an address says nothing
    (a person shown as "jsilva@comunicacao.pt" must not match "comunicacao")."""
    name = re.sub(r"\S+@\S+", " ", name)
    return bool(SERVICE.search(_fold(f"{name} {re.sub(r'[._-]', ' ', local)}")))


def classify(head: email.message.Message, courses: list[Course], notice_hosts: set[str]) -> dict[str, Any]:
    """Bulk, site notification, academic, course: from headers only (rules, never the model)."""
    raw_from = str(head.get("From", ""))
    name, address = parseaddr(raw_from)
    if "@" not in address:
        # A malformed From (unquoted backslash in the name) is parsed to "<>"; never lose the sender: use the raw text.
        raw_from = next((str(v) for k, v in head.raw_items() if k.lower() == "from"), "")
        if found := re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", raw_from):
            address, name = found[0], raw_from[:found.start()].strip(' "<')
    address = address.lower()
    local, _, domain = address.partition("@")
    precedence = str(head.get("Precedence", "")).strip().lower()
    auto = str(head.get("Auto-Submitted", "no")).strip().lower()
    notice = _domain_in(domain, notice_hosts) or "moodle" in local or "moodle" in domain
    # A university office writing to everyone counts as mass mail; a person (or an unknown sender) never does.
    service = not notice and is_service(name, local)
    bulk = bool(head.get("List-Unsubscribe") or head.get("List-Id") or precedence in ("bulk", "list", "junk")
                or auto not in ("", "no") or NO_REPLY.match(local) or service)
    raw_subject = str(head.get("Subject", ""))
    subject = _fold(raw_subject)
    course_id = None
    for c in courses:
        # Codes and acronyms ("BD", "POO") match case-sensitively as whole words, so "bd" inside a word does not.
        marks = {m for m in (c.code, acronym(c.name)) if m and len(m) >= 2}
        if any(re.search(rf"\b{re.escape(m)}\b", raw_subject) for m in marks) or (len(c.name) >= 4 and _fold(c.name) in subject):
            course_id = c.id
            break
    return {"sender": address[:320], "sender_name": name[:200], "bulk": bulk, "notice": notice, "course_id": course_id,
            "academic": course_id is not None or notice or (_domain_in(domain, ACADEMIC_DOMAINS) and not bulk)}


def _when(value: Any) -> datetime | None:
    try:
        d = parsedate_to_datetime(str(value))
    except (TypeError, ValueError, IndexError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def text_of(raw: bytes) -> str:
    """The readable text of a message (plain preferred, else HTML without tags). Attachments are not opened."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        return ""
    if part.get_content_type() == "text/html":
        content = html.unescape(re.sub(r"(?is)<(script|style).*?</\1>|<[^>]+>", " ", content))
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n\n", content)).strip()[:MAX_BODY]


# --- Reading ---------------------------------------------------------------------------------------

def state(session: Session) -> dict[str, Any]:
    row = session.get(Preference, "mail")
    return dict(row.value) if row else {}


def _save(session: Session, value: dict[str, Any]) -> None:
    row = session.get(Preference, "mail")
    if row:
        row.value = value
    else:
        session.add(Preference(key="mail", value=value))


def keep_senders(session: Session) -> list[str]:
    row = session.get(Preference, "mail_keep")
    return list(row.value.get("senders", [])) if row else []


def _fingerprint(login: dict[str, Any]) -> str:
    """Which login was refused, without storing the password itself."""
    return hashlib.sha256(f"{login['host']}\0{login['username']}\0{login['password']}".encode()).hexdigest()


def _notice_hosts(session: Session) -> set[str]:
    """The Moodle/inforestudante hosts: mail sent from there is a site notification."""
    hosts = set()
    for v in sync.integrations(session).values():
        host = urlsplit(v["url"]).hostname or ""
        hosts.add(host)
    return hosts - {""}


def _read(session: Session, box: Mailbox, role: str, name: str, courses: list[Course], hosts: set[str]) -> int:
    """New messages' headers, every message's flags, academic bodies. Returns how many were new."""
    validity = box.open(name)
    present = set(box.uids())
    known = {}
    for row in session.scalars(select(MailMessage).where(MailMessage.folder == role, MailMessage.removed_at.is_(None))):
        if row.uidvalidity != validity or row.uid not in present:
            session.delete(row)  # deleted or moved by the student, or the server renumbered the folder
        else:
            known[row.uid] = row
    new = sorted(present - known.keys())
    for i in range(0, len(new), BATCH):
        for h in box.headers(new[i:i + BATCH]):
            head = email.message_from_bytes(h["literal"] or b"", policy=email.policy.default)
            refs = str(head.get("References", "") or head.get("In-Reply-To", "")).split()
            row = MailMessage(folder=role, uidvalidity=validity, uid=h["uid"], size=h["size"],
                              message_id=str(head.get("Message-ID", "")).strip()[:998] or None,
                              thread=refs[0][:998] if refs else None, subject=str(head.get("Subject", ""))[:2000],
                              sent_at=_when(head.get("Date")), seen="\\seen" in h["flags"],
                              flagged="\\flagged" in h["flags"], answered="\\answered" in h["flags"],
                              **classify(head, courses, hosts))
            session.add(row)
            known[row.uid] = row
    if known:
        for uid, flags in box.flags().items():
            if row := known.get(uid):
                row.seen, row.flagged, row.answered = "\\seen" in flags, "\\flagged" in flags, "\\answered" in flags
    session.flush()
    if role == "inbox":
        for row in sorted((r for r in known.values() if r.academic and r.body is None), key=lambda r: -r.uid)[:BODIES_PER_RUN]:
            raw = box.body(row.uid)
            row.body = text_of(raw) if raw else ""
    return len(new)


def _clean_junk(session: Session, box: Mailbox, folders: dict[str, str], now: datetime) -> int:
    """Move Junk Email older than 14 days to Deleted Items. Exchange already judged it spam."""
    if "junk" not in folders or "trash" not in folders:
        return 0
    box.open(folders["junk"], write=True)
    old = box.uids(f"BEFORE {imap_date(now - JUNK_AFTER)}")
    if not old:
        return 0
    validity, mapping = box.move(old, folders["trash"])
    for row in session.scalars(select(MailMessage).where(MailMessage.folder == "junk", MailMessage.uid.in_(old),
                                                         MailMessage.removed_at.is_(None))):
        row.removed_at, row.removed_by = now, "junk"
        row.trash_uidvalidity, row.trash_uid = validity, mapping.get(row.uid)
    session.add(AuditEvent(actor="peace", action="mail.clean_junk", detail={"count": len(old)}))
    return len(old)


def _purge(session: Session, box: Mailbox, folders: dict[str, str], now: datetime) -> int:
    """Empty from Deleted Items only what PEACE moved there 30+ days ago."""
    rows = session.scalars(select(MailMessage).where(MailMessage.removed_at <= now - PURGE_AFTER,
                                                     MailMessage.purged_at.is_(None))).all()
    if not rows or "trash" not in folders or not box.can_purge():
        return 0
    validity = box.open(folders["trash"], write=True)
    ours = [r for r in rows if r.trash_uid and r.trash_uidvalidity == validity]
    if ours:
        box.purge([r.trash_uid for r in ours])
    for r in rows:
        r.purged_at = now  # emptied, or no longer findable in Deleted Items: nothing more PEACE can do
    session.add(AuditEvent(actor="peace", action="mail.purge", detail={"count": len(ours)}))
    return len(ours)


def run(session: Session, now: datetime, force: bool = False) -> dict[str, Any]:
    """One pass over the mailbox (worker, every 15 minutes; or on demand). Commits; returns the new state."""
    login = sync.mail_login(session)
    st = state(session)
    if not (login["enabled"] and login["host"] and login["username"] and login["password"]):
        return st
    if st.get("refused") == _fingerprint(login):
        return st  # a refused login is not retried (it could lock the account) until the student changes it
    last = datetime.fromisoformat(st["last_run"]) if st.get("last_run") else None
    if not force and last and now - last < EVERY:
        return st
    st["last_run"] = now.isoformat()
    if st.get("rules") != RULES:  # read every message again with the current rules; keep what PEACE moved
        session.execute(delete(MailMessage).where(MailMessage.removed_at.is_(None)))
        st["rules"] = RULES
    try:
        box = connect(login)
    except AuthError as e:
        st |= {"refused": _fingerprint(login), "error": str(e), "ok": False}
        _save(session, st)
        _add(session, key=f"mail:login:{now.date()}", kind="integration", title="Email: login recusado",
             body="Atualiza a palavra-passe do email em Definições → Integrações.", url="/definicoes",
             actions=["open"], expires_at=now + timedelta(days=1))
        session.commit()
        return st
    except MailError as e:
        st |= {"error": str(e), "ok": False}
        _save(session, st)
        session.commit()
        return st
    try:
        folders = parse_list(box.listing())
        courses = list(session.scalars(select(Course)))
        hosts = _notice_hosts(session)
        new = sum(_read(session, box, role, folders[role], courses, hosts) for role in ("inbox", "junk") if role in folders)
        cleaned = _clean_junk(session, box, folders, now) if login["clean_junk"] else 0
        purged = _purge(session, box, folders, now)
        sized = datetime.fromisoformat(st["sizes_at"]) if st.get("sizes_at") else None
        if force or not sized or now - sized >= SIZES_EVERY:
            used = 0
            for _, raw in _listed(box.listing()):  # every folder counts toward the limit (Sent Items too)
                box.open(raw)
                used += box.sizes()
            st |= {"used_bytes": used, "sizes_at": now.isoformat()}
        st |= {"ok": True, "error": None, "refused": None, "last_success": now.isoformat()}
        log.info("mail: %d new, %d junk cleaned, %d purged", new, cleaned, purged)
    except (MailError, imaplib.IMAP4.error, OSError) as e:
        session.rollback()
        st = state(session) | {"last_run": now.isoformat(), "ok": False,
                               "error": str(e) if isinstance(e, MailError) else "O servidor de email falhou a meio; tenta outra vez."}
    finally:
        box.close()
    _save(session, st)
    _warn_quota(session, st, login, now)
    session.commit()
    return st


def _warn_quota(session: Session, st: dict[str, Any], login: dict[str, Any], now: datetime) -> None:
    quota, used = login.get("quota_mb"), st.get("used_bytes")
    if not quota or used is None or used < QUOTA_WARN * quota * 2**20:
        return
    tz = ZoneInfo(get_settings().timezone)
    today = now.astimezone(tz).date()
    _add(session, key=f"mail:quota:{today}", kind="integration", title="Caixa de email quase cheia",
         body=f"Usas {used / 2**20:.0f} de {quota} MB. Vê em Correio os emails maiores e o que há para limpar.",
         url="/correio", actions=["open"], expires_at=_end_of_day(today, tz))


# --- Cleanup -------------------------------------------------------------------------------------

def _rule(row: MailMessage) -> str:
    if row.notice:
        return "aviso do Moodle/inforestudante"
    if NO_REPLY.match(row.sender):
        return "«no-reply»"
    return "divulgação de um serviço" if is_service(row.sender_name, row.sender.split("@")[0]) else "lista ou newsletter"


def candidates(session: Session, now: datetime) -> list[MailMessage]:
    """Inbox messages safe to offer for cleanup: older than 30 days, mass mail (not about a course) or a site
    notification PEACE already has; never flagged, replied, from a person, or from a sender to keep. Unread mass mail
    counts too: a student who never opens the broadcasts would otherwise never get space back (seen 2026-10-29)."""
    keep = set(keep_senders(session))
    rows = session.scalars(select(MailMessage).where(
        MailMessage.folder == "inbox", MailMessage.removed_at.is_(None), ~MailMessage.flagged,
        ~MailMessage.answered, MailMessage.sent_at < now - CLEAN_AFTER,
        MailMessage.notice | (MailMessage.bulk & MailMessage.course_id.is_(None))).order_by(MailMessage.size.desc(), MailMessage.id))
    return [r for r in rows if r.sender not in keep][:MAX_CLEANUP]


def offer_cleanup(session: Session, now: datetime) -> Proposal | None:
    """One cleanup Proposal a week, listing every message and why; nothing moves until the student applies it."""
    year, week, _ = now.astimezone(ZoneInfo(get_settings().timezone)).isocalendar()
    key = f"mail_cleanup:{year}-W{week:02d}"
    if session.scalar(select(Proposal.id).where((Proposal.key == key) | ((Proposal.kind == "mail_cleanup") & (Proposal.status == "pending")))):
        return None
    rows = candidates(session, now)
    if not rows:
        return None
    size = sum(r.size for r in rows)
    proposal = Proposal(key=key, kind="mail_cleanup", title=f"Limpar {len(rows)} emails · {max(1, round(size / 2**20))} MB",
                        reason="Emails com mais de 30 dias: divulgações de gabinetes e serviços, listas, newsletters, «no-reply» "
                               "e avisos do Moodle que a PEACE já tem. Nunca emails de pessoas. Vão para Itens Eliminados; podes repô-los durante 30 dias.",
                        evidence=[{"source": "email", "ref": str(r.id), "quote": r.subject[:200], "sender": r.sender,
                                   "size": r.size, "rule": _rule(r)} for r in rows])
    session.add(proposal)
    session.flush()
    return proposal


def move_to_trash(session: Session, rows: list[MailMessage], by: str, now: datetime) -> int:
    box = connect(sync.mail_login(session))
    try:
        folders = parse_list(box.listing())
        if "trash" not in folders:
            raise MailError("Não encontrei a pasta Itens Eliminados.")
        moved = 0
        for role in ("inbox", "junk"):
            mine = [r for r in rows if r.folder == role]
            if not mine or role not in folders:
                continue
            validity = box.open(folders[role], write=True)
            mine = [r for r in mine if r.uidvalidity == validity]
            if not mine:
                continue
            trash_validity, mapping = box.move([r.uid for r in mine], folders["trash"])
            for r in mine:
                r.removed_at, r.removed_by = now, by
                r.trash_uidvalidity, r.trash_uid = trash_validity, mapping.get(r.uid)
            moved += len(mine)
        return moved
    finally:
        box.close()


def apply_cleanup(session: Session, proposal: Proposal, keep: set[int], now: datetime) -> dict[str, Any]:
    """Move the listed messages the student did not keep, re-checking each one (it may have changed since)."""
    still = {r.id for r in candidates(session, now)}
    rows = [session.get(MailMessage, int(e["ref"])) for e in proposal.evidence
            if int(e["ref"]) in still and int(e["ref"]) not in keep]
    if rows:
        move_to_trash(session, rows, f"proposal:{proposal.id}", now)
    return {"removed": [], "created": [], "moved": [r.id for r in rows if r.removed_at == now], "kept": sorted(keep)}


def restore(session: Session, rows: list[MailMessage]) -> int:
    """Move messages back from Deleted Items to the inbox (put back from Junk too: the student says it is not spam).
    Refused once emptied."""
    if any(r.purged_at or r.trash_uid is None for r in rows):
        raise MailError("Alguns já foram esvaziados de Itens Eliminados; procura-os em «Recuperar itens eliminados» no webmail.")
    if not rows:
        return 0
    box = connect(sync.mail_login(session))
    try:
        folders = parse_list(box.listing())
        if "trash" not in folders:
            raise MailError("Não encontrei a pasta Itens Eliminados.")
        validity = box.open(folders["trash"], write=True)
        if any(r.trash_uidvalidity != validity for r in rows):
            raise MailError("A pasta Itens Eliminados mudou; repõe-nos no webmail.")
        box.move([r.trash_uid for r in rows], folders["inbox"])
        for r in rows:
            session.delete(r)  # the next read finds it again, with its new UID
        return len(rows)
    finally:
        box.close()


# --- API -----------------------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/mail", dependencies=[Depends(require_token)])
DB = Depends(get_session)
NOW = Depends(get_now)


def status(session: Session) -> dict[str, Any]:
    login, st = sync.mail_login(session), state(session)
    counts = dict(session.execute(select(MailMessage.folder, func.count()).where(MailMessage.removed_at.is_(None))
                                  .group_by(MailMessage.folder)).all())
    return {"configured": bool(login["host"] and login["username"] and login["password"]), "enabled": login["enabled"],
            "host": login["host"], "last_run": st.get("last_run"), "last_success": st.get("last_success"),
            "error": st.get("error"), "needs_login": bool(st.get("refused")), "used_bytes": st.get("used_bytes"),
            "quota_mb": login["quota_mb"], "clean_junk": login["clean_junk"], "keep_senders": keep_senders(session),
            "inbox": counts.get("inbox", 0), "junk": counts.get("junk", 0)}


def message_out(r: MailMessage) -> dict[str, Any]:
    return {"id": r.id, "folder": r.folder, "sender": r.sender, "sender_name": r.sender_name, "subject": r.subject,
            "sent_at": r.sent_at, "size": r.size, "seen": r.seen, "academic": r.academic, "bulk": r.bulk,
            "notice": r.notice, "course": r.course and {"id": r.course.id, "name": r.course.name},
            "removed_at": r.removed_at, "removed_by": r.removed_by,
            "restorable": bool(r.removed_at and r.trash_uid and not r.purged_at), "snippet": (r.body or "")[:240],
            "facts": r.facts}


@router.get("/status")
def get_status(session: Session = DB):
    return status(session)


@router.post("/sync")
def sync_now(session: Session = DB, now: datetime = NOW):
    run(session, now, force=True)
    return status(session)


def search(session: Session, q: str, limit: int = 50) -> list[MailMessage]:
    """Postgres full-text search (Portuguese) over sender, subject and academic bodies."""
    doc = func.to_tsvector("portuguese", func.concat_ws(" ", MailMessage.sender_name, MailMessage.sender,
                                                        MailMessage.subject, MailMessage.body))
    query = func.websearch_to_tsquery("portuguese", q)
    # shortcut: no index; a student's mailbox is a few thousand rows. Add a GIN index if searches get slow.
    return list(session.scalars(select(MailMessage).where(MailMessage.removed_at.is_(None), doc.op("@@")(query))
                                .order_by(func.ts_rank(doc, query).desc(), MailMessage.sent_at.desc()).limit(limit)))


@router.get("/messages")
def list_messages(view: str = "academic", q: str = "", session: Session = DB):
    if q.strip():
        return [message_out(r) for r in search(session, q.strip()[:200])]
    query = select(MailMessage)
    if view == "removed":
        query = query.where(MailMessage.removed_at.is_not(None)).order_by(MailMessage.removed_at.desc(), MailMessage.id)
    elif view == "largest":
        query = query.where(MailMessage.removed_at.is_(None)).order_by(MailMessage.size.desc())
    else:
        query = query.where(MailMessage.removed_at.is_(None), MailMessage.folder == "inbox")
        if view == "academic":
            query = query.where(MailMessage.academic, ~MailMessage.notice)
        query = query.order_by(MailMessage.sent_at.desc().nulls_last())
    return [message_out(r) for r in session.scalars(query.limit(100))]


@router.get("/messages/{message_id}")
def get_message(message_id: int, session: Session = DB):
    row = session.get(MailMessage, message_id)
    if row is None:
        raise HTTPException(404, "Email não encontrado.")
    return message_out(row) | {"body": row.body}


@router.post("/messages/{message_id}/restore")
def restore_message(message_id: int, session: Session = DB):
    row = session.get(MailMessage, message_id)
    if row is None or row.removed_at is None:
        raise HTTPException(404, "Este email não foi removido pela PEACE.")
    subject = row.subject[:200]
    try:
        restore(session, [row])
    except (MailError, AuthError) as e:
        session.rollback()
        raise HTTPException(409, str(e)) from None
    except (imaplib.IMAP4.error, OSError):
        session.rollback()
        raise HTTPException(409, "O servidor de email falhou a meio; tenta outra vez.") from None
    session.add(AuditEvent(actor="user", action="mail.restore", detail={"subject": subject}))
    session.commit()
    return {"ok": True}


class KeepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    senders: list[str] = Field(max_length=500)


@router.put("/keep")
def put_keep(body: KeepIn, session: Session = DB):
    senders = sorted({s.strip().lower()[:320] for s in body.senders if "@" in s})
    row = session.get(Preference, "mail_keep")
    if row:
        row.value = {"senders": senders}
    else:
        session.add(Preference(key="mail_keep", value={"senders": senders}))
    session.add(AuditEvent(actor="user", action="mail.keep", detail={"count": len(senders)}))
    session.commit()
    return {"senders": senders}
