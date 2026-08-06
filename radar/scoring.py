"""Local relevance scoring.

Every process that survives the coarse geography filter is scored here, against
the whole record rather than a single column, on normalised text. A score is a
0-100 number plus the reasons behind it, so an alert can explain itself instead
of just asserting a percentage.
"""

from dataclasses import dataclass, field

from .fields import get
from .normalize import flatten_record, normalize, pad, phrase_in_padded

# Direct points on a 0-100 scale — no rescaling, so a score is readable on its
# own terms: 60+ means "worth an email", 80+ means "drop what you are doing".
# Calibrated so the maximum reachable score is 98: nothing saturates at 100,
# which keeps the ranking meaningful when several strong matches land at once.
# Two keywords in the objeto plus a plausible value clears the alert threshold
# on their own, so an alert never depends on the UNSPSC code being right.
W_CRITICA_1 = 40        # one critical keyword, in the objeto
W_CRITICA_2 = 52        # two or more, in the objeto
W_CRITICA_CONTEXTO = 12 # only outside the objeto — likely the entity name
W_UNSPSC = 18           # official category hit, independent of wording
W_DESEABLE = 4          # each, capped
W_DESEABLE_MAX = 12
W_VALOR_OK = 8
W_VALOR_BAJO = -12
W_URGENTE = 8           # closes within 3 days
W_PRONTO = 4            # closes within 10
W_CERRADO = -25


@dataclass
class Match:
    record: dict
    score: int
    reasons: list = field(default_factory=list)
    excluded_by: str | None = None

    @property
    def is_match(self) -> bool:
        return self.excluded_by is None and self.score > 0


def score_record(record: dict, schema: dict, criteria: dict) -> Match:
    # flatten_record already returns normalised text, so the haystack is padded
    # once here and every keyword below is a plain substring check. Normalising
    # inside the lookup would repeat that work once per keyword, which on a full
    # sweep of tens of thousands of records dominates the runtime.
    blob = flatten_record(record)
    objeto = normalize(get(record, schema, "objeto", ""))
    haystack = pad(f"{blob} {objeto}")
    # What the contract is FOR carries the signal. The rest of the record —
    # entity name above all — carries a lot of false positives: every contract
    # from a 'Secretaría de Educación' contains 'educacion', toner included.
    objeto_hay = pad(objeto)

    keywords = criteria.get("keywords", {})
    reasons = []

    # --- Hard exclusions -----------------------------------------------
    for term in keywords.get("excluyentes", []) or []:
        if phrase_in_padded(haystack, term):
            return Match(record=record, score=0, excluded_by=term)

    raw = 0

    # --- Critical keywords ---------------------------------------------
    criticas = keywords.get("criticas") or []
    en_objeto = [t for t in criticas if phrase_in_padded(objeto_hay, t)]
    solo_en_resto = [
        t for t in criticas
        if t not in en_objeto and phrase_in_padded(haystack, t)
    ]

    if en_objeto:
        raw += W_CRITICA_2 if len(en_objeto) >= 2 else W_CRITICA_1
        reasons.append("En el objeto: " + ", ".join(en_objeto[:4]))
    elif solo_en_resto:
        # Probably the entity's name rather than the subject of the contract.
        raw += W_CRITICA_CONTEXTO
        reasons.append("Sólo en el contexto: " + ", ".join(solo_en_resto[:3]))

    # --- Desirable keywords --------------------------------------------
    deseables_hit = [t for t in (keywords.get("deseables") or []) if phrase_in_padded(haystack, t)]
    if deseables_hit:
        raw += min(W_DESEABLE * len(deseables_hit), W_DESEABLE_MAX)
        reasons.append("Refuerzo: " + ", ".join(deseables_hit[:4]))

    # --- UNSPSC category ------------------------------------------------
    unspsc_value = str(get(record, schema, "unspsc", "") or "")
    families = [str(f) for f in (criteria.get("unspsc", {}).get("familias") or [])]
    digits = unspsc_digits(unspsc_value)
    for family in families:
        if digits.startswith(family):
            raw += W_UNSPSC
            reasons.append(f"Categoria UNSPSC {unspsc_value} (familia {family})")
            break

    # A process with neither a keyword nor a category hit is not ours.
    if raw == 0:
        return Match(record=record, score=0)

    # --- Contract value --------------------------------------------------
    valor = _to_float(get(record, schema, "valor"))
    limits = criteria.get("valor", {}) or {}
    if valor is not None:
        minimo, maximo = limits.get("minimo"), limits.get("maximo")
        if minimo and valor < minimo:
            reasons.append(f"Valor bajo el minimo (${valor:,.0f})")
            raw += W_VALOR_BAJO
        elif maximo and valor > maximo:
            reasons.append(f"Valor sobre el maximo (${valor:,.0f})")
        else:
            raw += W_VALOR_OK
            reasons.append(f"Valor en rango (${valor:,.0f})")

    # --- Closing-date urgency -------------------------------------------
    # Order matters: a past date is negative and would otherwise satisfy the
    # "closes within 10 days" branch and be reported as still open.
    days = days_until_close(record, schema)
    if days is not None:
        if days < 0:
            raw += W_CERRADO
            reasons.append("Ya cerrado")
        elif days <= 3:
            raw += W_URGENTE
            reasons.append(f"CIERRA EN {days} DIA(S)")
        elif days <= 10:
            raw += W_PRONTO
            reasons.append(f"Cierra en {days} dias")

    score = max(0, min(100, round(raw)))
    return Match(record=record, score=score, reasons=reasons)


def unspsc_digits(value: str) -> str:
    """Digits of a UNSPSC code, dropping Socrata's 'V1.' version prefix.

    SECOP publishes categories as 'V1.86101700'. Extracting digits from the
    whole string yields '186101700', which never matches family '86'.
    """
    code = str(value or "").strip()
    if "." in code:
        code = code.rsplit(".", 1)[-1]
    return "".join(c for c in code if c.isdigit())


def days_until_close(record: dict, schema: dict):
    """Days from today to the closing date, or None if unparseable."""
    from datetime import datetime, timezone

    raw = get(record, schema, "fecha_cierre")
    if not raw:
        return None
    text = str(raw)[:19]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return (parsed - datetime.now(timezone.utc)).days
        except ValueError:
            continue
    return None


def _to_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def matches_geography(record: dict, schema: dict, criteria: dict) -> bool:
    """Local geography check, per profile.

    The download is shared across profiles and filtered by the union of their
    departments, so each profile has to narrow to its own here. A record with
    no department published is kept: missing an opportunity costs more than
    reviewing an extra one.
    """
    departamentos = criteria.get("geografia", {}).get("departamentos") or []
    if not departamentos:
        return True

    actual = normalize(get(record, schema, "departamento", ""))
    if not actual:
        return True

    return any(normalize(dep) in actual for dep in departamentos)


def filter_and_score(records: list, schema: dict, criteria: dict) -> list:
    """Score every record and return the matches above threshold, best first."""
    threshold = criteria.get("umbral_score", 35)
    matches = []
    for record in records:
        if not matches_geography(record, schema, criteria):
            continue
        match = score_record(record, schema, criteria)
        if match.is_match and match.score >= threshold:
            matches.append(match)
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches
