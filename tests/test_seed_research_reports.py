# -*- coding: utf-8 -*-
import unittest

from scripts.seed_research_reports import DEFAULT_AUTHOR_EMAIL, SAMPLE_REPORTS, seed_research_reports
from src.storage import AppResearchReport, AppUser, DatabaseManager


class TestSeedResearchReports(unittest.TestCase):
    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self.db_manager = DatabaseManager(db_url="sqlite:///:memory:")
        self.session = self.db_manager.get_session()

    def tearDown(self) -> None:
        self.session.close()
        DatabaseManager.reset_instance()

    def test_seed_creates_author_and_published_reports(self) -> None:
        result = seed_research_reports(self.session, count=3)

        self.assertEqual(result["created"], 3)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["skipped"], 0)

        author = self.session.query(AppUser).filter(AppUser.email == DEFAULT_AUTHOR_EMAIL).first()
        self.assertIsNotNone(author)
        self.assertTrue(author.is_research_operator)

        reports = self.session.query(AppResearchReport).order_by(AppResearchReport.id).all()
        self.assertEqual(len(reports), 3)
        self.assertTrue(all(report.author_id == author.id for report in reports))
        self.assertTrue(all(report.is_published for report in reports))
        self.assertTrue(all(report.published_at is not None for report in reports))
        self.assertEqual(reports[0].title, SAMPLE_REPORTS[0].title)

    def test_seed_is_idempotent_by_title_unless_replace_enabled(self) -> None:
        seed_research_reports(self.session, count=1)

        second = seed_research_reports(self.session, count=1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["skipped"], 1)

        report = self.session.query(AppResearchReport).first()
        report.summary = "changed locally"
        self.session.commit()

        replaced = seed_research_reports(self.session, count=1, replace=True)
        self.assertEqual(replaced["created"], 0)
        self.assertEqual(replaced["updated"], 1)
        self.assertEqual(replaced["skipped"], 0)

        refreshed = self.session.query(AppResearchReport).first()
        self.assertEqual(refreshed.summary, SAMPLE_REPORTS[0].summary)

    def test_dry_run_rolls_back_created_rows(self) -> None:
        result = seed_research_reports(self.session, count=2, dry_run=True)

        self.assertEqual(result["created"], 2)
        self.assertEqual(self.session.query(AppResearchReport).count(), 0)
        self.assertEqual(self.session.query(AppUser).filter(AppUser.email == DEFAULT_AUTHOR_EMAIL).count(), 0)


if __name__ == "__main__":
    unittest.main()
