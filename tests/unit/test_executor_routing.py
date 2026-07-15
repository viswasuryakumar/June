"""Unit tests for src/executor/routing.py using lightweight page fakes
(no browser needed - routing only reads page.url and probes selectors)."""

from __future__ import annotations

from src.executor.routing import (
    classify_apply_destination,
    detect_ats,
    has_login_wall,
)


class FakeLocator:
    def __init__(self, present: bool):
        self._present = present

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self._present else 0

    def is_visible(self, timeout=None):
        return self._present


class FakePage:
    def __init__(self, url: str, visible_selectors: set[str] | None = None):
        self.url = url
        self._visible = visible_selectors or set()

    def locator(self, selector: str):
        return FakeLocator(selector in self._visible)


class RaisingPage:
    @property
    def url(self):
        raise RuntimeError("target closed")


class TestDetectAts:
    def test_known_hosts_and_subdomains(self):
        assert detect_ats("boards.greenhouse.io") == "greenhouse"
        assert detect_ats("jobs.lever.co") == "lever"
        assert detect_ats("acme.wd5.myworkdayjobs.com") == "workday"

    def test_suffix_matching_requires_a_dot_boundary(self):
        assert detect_ats("notgreenhouse.io") is None
        assert detect_ats("careers.example.com") is None


class TestHasLoginWall:
    def test_visible_password_field_detected(self):
        page = FakePage("https://x.example", {"input[type=password]"})
        assert has_login_wall(page) is True

    def test_clean_page_has_no_wall(self):
        assert has_login_wall(FakePage("https://x.example")) is False


class TestClassifyApplyDestination:
    def test_jobright_stays_jobright(self):
        dest = classify_apply_destination(FakePage("https://jobright.ai/jobs/info/123"))
        assert dest.kind == "jobright"

    def test_linkedin_login_url_is_login_wall(self):
        for url in (
            "https://www.linkedin.com/login?redirect=x",
            "https://www.linkedin.com/checkpoint/lg/sign-in",
            "https://www.linkedin.com/authwall?trk=x",
        ):
            dest = classify_apply_destination(FakePage(url))
            assert dest.kind == "linkedin_login"
            assert dest.ats == "linkedin"

    def test_linkedin_page_with_password_field_is_login_wall(self):
        page = FakePage("https://www.linkedin.com/jobs/view/4437285122", {"input[type=password]"})
        assert classify_apply_destination(page).kind == "linkedin_login"

    def test_authenticated_linkedin_job_page(self):
        page = FakePage("https://www.linkedin.com/jobs/view/4437285122")
        assert classify_apply_destination(page).kind == "linkedin"

    def test_company_page_is_external_ats_with_label(self):
        dest = classify_apply_destination(FakePage("https://boards.greenhouse.io/acme/jobs/1"))
        assert dest.kind == "external_ats"
        assert dest.ats == "greenhouse"

    def test_unrecognized_company_site_is_external_without_label(self):
        dest = classify_apply_destination(FakePage("https://careers.acme.com/apply/1"))
        assert dest.kind == "external_ats"
        assert dest.ats is None

    def test_unreadable_page_degrades_to_unknown(self):
        assert classify_apply_destination(RaisingPage()).kind == "unknown"
