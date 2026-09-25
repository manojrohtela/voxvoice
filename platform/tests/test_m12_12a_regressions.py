"""M12.12A — the four defects the M12.12 audit found, pinned so they cannot return.

Two of them are testable from Python and are tested here: a **documentation** claim and a
**source** claim, neither of which any browser or component test can see.

  * **Defect 1** — the console labelled a WebRTC transport "WebRTC · real providers". The
    console API carries no provider information at all (`E11-3`: `api/**` never names a vendor),
    so the console cannot know this, and it said so while the E2E harness ran scripted TTS. The
    component test `console/src/app/App.test.tsx` pins the rendered page; this pins the source,
    so the phrase cannot come back through any other component.
  * **Defect 4** — `m12-frontend-architecture.md` claimed "Contrast meets WCAG AA in both themes"
    while `m12-certification.md` recorded contrast as NOT EXECUTED. Contrast has since been
    measured (it passes for the text tokens), but no WCAG *conformance* is claimed and none may
    be, because screen readers, keyboard traversal and other browsers remain untested.

Defects 2 (conversation-detail overflow) and 3 (viewer Playground gating) are pinned where they
can actually be observed: `Conversations.test.tsx`, `Playground.test.tsx`, and the widths and
pages added to `e2e/browser.mjs`.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONSOLE_SRC = REPO / "console" / "src"
FRONTEND_ARCHITECTURE = REPO / "docs" / "architecture" / "epic12" / "m12-frontend-architecture.md"
CERTIFICATION = REPO / "docs" / "testing" / "m12-certification.md"

#: What the console may never assert, because `/v1/console/meta` cannot support it. Matched
#: against the strings the console ships, not against comments explaining why they are absent.
FORBIDDEN_CLAIM = re.compile(r"real\s+providers", re.IGNORECASE)

#: A claim of conformance, as opposed to a statement of what was measured.
CONFORMANCE_CLAIM = re.compile(
    r"(meets|complies with|compliant with|conforms to|conformant with)\s+WCAG"
    r"|WCAG[^.\n]{0,20}compliant",
    re.IGNORECASE,
)


def _shipped_strings(source: str) -> str:
    """The source with its comments removed: what a user could actually be shown."""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.MULTILINE)


def test_console_never_claims_real_providers() -> None:
    """Defect 1: no console file may render a claim about which providers are configured."""
    offenders = [
        path.relative_to(REPO)
        for path in CONSOLE_SRC.rglob("*.ts*")
        if not path.name.endswith(".test.tsx")
        and not path.name.endswith(".test.ts")
        and FORBIDDEN_CLAIM.search(_shipped_strings(path.read_text()))
    ]
    assert offenders == [], (
        "the console cannot know whether the providers are real — /v1/console/meta carries no "
        f"provider information (E11-3). Claimed in: {offenders}"
    )


def test_meta_still_carries_no_provider_information() -> None:
    """The premise of defect 1: the fix is only correct while this stays true."""
    from api.console.routers.meta import RuntimeDescription

    assert set(RuntimeDescription.model_fields) == {
        "transport",
        "webrtc_signalling",
        "ice_policy",
    }


def test_frontend_architecture_claims_no_wcag_conformance() -> None:
    """Defect 4: the document may state what was measured, never that the console conforms."""
    text = FRONTEND_ARCHITECTURE.read_text()
    claim = CONFORMANCE_CLAIM.search(text)
    assert claim is None, f"unsupported WCAG conformance claim: {claim.group(0)!r}"


def test_frontend_architecture_records_what_contrast_testing_covered() -> None:
    """The replacement must be specific, and must name what is still untested."""
    text = FRONTEND_ARCHITECTURE.read_text()
    assert "No WCAG conformance is claimed" in text
    assert re.search(r"colour contrast.{0,80}WCAG AA", text, re.IGNORECASE | re.DOTALL)
    for untested in ("Screen-reader output", "keyboard-only journey", "other than Chrome"):
        assert untested in text, untested


def test_certification_record_and_architecture_doc_agree_on_accessibility() -> None:
    """The two documents contradicted each other for a fortnight. They may not again."""
    certification = CERTIFICATION.read_text()
    assert "no accessibility conformance is claimed" in certification.lower()
    assert CONFORMANCE_CLAIM.search(certification) is None
