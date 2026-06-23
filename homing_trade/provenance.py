"""Phase 9 #4: structured provenance for self-modification PRs.

A self-proposed CODE change must trace back to the model-authored row that motivated it — the
`reflections` lesson, or the `proposals` suggestion. This module is the pure half: it turns a
`(table, row_id)` pointer plus the row's own text into a verified, human-readable provenance
reference that the proposer stamps into the PR body. The reverse link (which PR a given
reflection/proposal produced) is recorded in the `self_mod_prs` audit ledger by db.py — together
they make the trail bidirectional and auditable.

Fail-closed by construction: a provenance can only point at one of the two model-authored source
tables, and only at a positive row id. db.resolve_provenance refuses to build one for a row that
doesn't exist — we never attach a dangling or fabricated reference to a PR.
"""
from dataclasses import dataclass

# Only the MODEL_AUTHORED tables that carry a motivating lesson/rationale can be a provenance
# source. Kept in sync with db.MODEL_AUTHORED_TABLES via test_provenance.
SOURCE_TABLES = ("reflections", "proposals")
# The free-text field on each source row that best summarises *why* the change was motivated.
SUMMARY_FIELD = {"reflections": "lesson", "proposals": "rationale"}
_SUMMARY_MAX = 240


def _clip(text, limit=_SUMMARY_MAX):
    """Collapse whitespace and bound the summary so the PR body stays readable (no newlines, no
    runaway lesson text)."""
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class Provenance:
    """An immutable, verified link from a self-mod PR to the row that motivated it."""
    table: str
    row_id: int
    summary: str = ""

    @property
    def ref(self):
        """The stable short reference, e.g. `reflections#42`."""
        return f"{self.table}#{self.row_id}"

    def __str__(self):
        s = (self.summary or "").strip()
        return f'{self.ref} — "{s}"' if s else self.ref


def make_provenance(table, row_id, summary=""):
    """Build a Provenance, validating the pointer. Raises ValueError on an unknown table or a
    non-positive / non-int row id — a provenance can never point at an unprotected, made-up, or
    audit-truth table."""
    if table not in SOURCE_TABLES:
        raise ValueError(f"provenance source must be one of {SOURCE_TABLES}, got {table!r}")
    # bool is an int subclass — exclude it explicitly so True/False can't masquerade as a row id.
    if isinstance(row_id, bool) or not isinstance(row_id, int) or row_id <= 0:
        raise ValueError(f"provenance row_id must be a positive int, got {row_id!r}")
    return Provenance(table=table, row_id=row_id, summary=_clip(summary))
